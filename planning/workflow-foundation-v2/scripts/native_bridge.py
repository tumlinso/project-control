#!/usr/bin/env python3
"""Explicit selected-runtime bridge. Only operation=apply performs workflow mutation."""
from __future__ import annotations
import argparse,dataclasses,hashlib,importlib,inspect,json,os,sys
from pathlib import Path
sys.dont_write_bytecode=True
from common import load,canonical,demand,object_digest,PackageError
MODULES=('project_control.mutation','project_control.models','project_control.proposals','project_control.config','project_control.registry','todo_orchestrator.plan','todo_orchestrator.service','todo_orchestrator.workflow.protocol')

def api():
    mutation=importlib.import_module('project_control.mutation')
    models=importlib.import_module('project_control.models')
    config=importlib.import_module('project_control.config').load_config()
    proposals=importlib.import_module('project_control.proposals')
    from project_control.registry import WorkspaceRegistry
    for name,params in [('validate_native_plan',{'config','project','native_plan'}),('build_mutation_snapshot',{'config','project'}),('apply_proposal',{'config','project','proposal'})]:
        demand(hasattr(mutation,name) and params<=set(inspect.signature(getattr(mutation,name)).parameters),'installed API changed: '+name)
    return mutation,models,config,proposals,WorkspaceRegistry

def runtime_identity():
    mutation,models,config,_,_=api();records={}
    for name in MODULES:
        module=importlib.import_module(name);path=Path(module.__file__).resolve()
        records[name]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    # Hash all loaded product implementation source, not only the bridge entry point.
    package=Path(importlib.import_module('project_control').__file__).resolve().parent
    product={str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.rglob('*.py'))}
    donor=Path(importlib.import_module('todo_orchestrator').__file__).resolve().parent
    donor_tree={str(p.relative_to(donor)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(donor.rglob('*.py'))}
    if hasattr(config,'model_dump'):raw=config.model_dump(mode='json')
    elif dataclasses.is_dataclass(config):raw=dataclasses.asdict(config)
    else:raise PackageError('cannot fingerprint installed configuration type; inspect the runtime contract')
    config_hash=hashlib.sha256(json.dumps(raw,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
    envnames=('PROJECT_CONTROL_SKILLS_ROOT','PROJECT_CONTROL_RELEASE_MANIFEST','PROJECT_CONTROL_RELEASE_DIGEST','TODO_ORCHESTRATOR_STATE_DIR','XDG_CONFIG_HOME','XDG_STATE_HOME','HOME')
    envhash=hashlib.sha256(json.dumps({k:os.environ.get(k) for k in envnames},sort_keys=True).encode()).hexdigest()
    release=os.environ.get('PROJECT_CONTROL_RELEASE_MANIFEST')
    return {'format':'wf2-runtime-identity-v1','python':sys.executable,'python_version':sys.version,'modules':records,'product_tree_digest':object_digest(product),'legacy_kernel_tree_digest':object_digest(donor_tree),'configuration_digest':config_hash,'environment_digest':envhash,'release_manifest_digest':hashlib.sha256(Path(release).read_bytes()).hexdigest() if release else None,'signatures':{n:str(inspect.signature(getattr(mutation,n))) for n in ('validate_native_plan','build_mutation_snapshot','apply_proposal')}}

def _table(snapshot,*keys):
    for k in keys:
        if k in snapshot.todo_tables:return snapshot.todo_tables[k]
    return None

def observe(project,repo):
    mutation,models,config,proposals,Registry=api()
    registry=Registry(config);workspace=registry.workspace(project)
    demand(workspace.authority_repository is not None,'workspace has no authority; never initialize one here')
    actual=registry.repository(project,workspace.authority_repository).root.resolve()
    demand(actual==Path(repo).resolve(),'explicit root does not match registered authority root')
    snapshot=mutation.build_mutation_snapshot(config,project)
    pre=proposals.observation_preconditions(snapshot).model_dump(mode='json')
    demand(pre.get('project_uuid') and isinstance(pre.get('todo_revision'),int),'authority identity unavailable')
    demand(pre.get('workflow_revision')==pre['todo_revision'] and pre.get('workflow_authority_fingerprint'),'workflow authority unavailable or skewed')
    wf=snapshot.todo_workflow
    runs=_table(snapshot,'workflow_runs','runs')
    lanes=_table(snapshot,'workflow_lanes','lanes')
    queues=_table(snapshot,'workflow_lane_tasks','lane_tasks')
    # Supported normalized semantic workflow is an alternative authoritative read,
    # never an inferred reconstruction from filesystem claims.
    if runs is None and isinstance(wf,dict) and isinstance(wf.get('runs'),list):runs=wf['runs']
    if lanes is None and runs is not None and all('lanes' in r for r in runs):
        lanes=[dict(l,run_id=r['id']) for r in runs for l in r['lanes']]
    if queues is None and lanes is not None and all('queue' in l or 'serial_queue' in l for l in lanes):
        queues=[dict(q,lane_id=l['id']) for l in lanes for q in l.get('queue',l.get('serial_queue',[]))]
    selected={}
    names={'tasks':('tasks',),'checkpoints':('checkpoints',),'gates':('gates',),'interfaces':('interfaces',),'barriers':('barriers',),'locks':('named_locks','locks'),'invariants':('invariants',)}
    # Expose no opaque auth handles, sessions, environment secrets or raw DB.
    fields={'id','name','parent_id','kind','title','objective','status','result','state','task_id','owner_task_id','checkpoint_id','version','content_hash','contract_paths_json','required','priority','parallel_policy','next_action','notes','capacity','rule','scope_json','enforcement','severity','revision','completion_revision','completion_commit','completion_git_head','root_task_id','mode'}
    for name,aliases in names.items():
        rows=_table(snapshot,*aliases)
        demand(rows is not None,'authoritative table unavailable: '+name)
        selected[name]=[{k:v for k,v in r.items() if k in fields} for r in rows]
    demand(runs is not None and lanes is not None and queues is not None,'complete authoritative run/lane/queue read unavailable')
    selected['runs']=[{k:v for k,v in r.items() if k in {'id','root_task_id','status','revision'}} for r in runs]
    selected['lanes']=[{k:v for k,v in r.items() if k in {'id','run_id','parent_lane_id','role','workspace_mode','state'}} for r in lanes]
    selected['lane_tasks']=[{k:v for k,v in q.items() if k in {'lane_id','task_id','position','state'}} for q in queues]
    return {'workspace':project,'preconditions':pre,'tables':selected}

def operation(request):
    mode=request['operation']
    if mode=='runtime':return runtime_identity()
    project=request['project'];repo=request['repo']
    current=observe(project,repo)
    if mode=='observe':return current
    mutation,models,config,_,_=api();plan=request['plan']
    if mode=='validate':return {'native':mutation.validate_native_plan(config,project,plan),'observation':current}
    demand(mode=='apply','unknown bridge operation')
    demand(request.get('confirm')=='APPLY-'+request['run_id'],'apply confirmation mismatch')
    expected=models.ObservationPreconditions.model_validate(request['approved_preconditions'])
    proposal=models.ProposalEnvelope.create(intent='Manual WF2 bootstrap import of '+request['run_id'],proposed_change=plan,observation_preconditions=expected)
    # One invocation. Never refresh expected preconditions, retry or repair.
    return mutation.apply_proposal(config,project,proposal)

if __name__=='__main__':
    try:result=operation(json.load(sys.stdin));print(json.dumps(result,sort_keys=True,default=str))
    except Exception as exc:
        print(json.dumps({'status':'error','code':getattr(exc,'code',type(exc).__name__),'message':str(exc),'details':getattr(exc,'details',{})},default=str));raise SystemExit(2)
