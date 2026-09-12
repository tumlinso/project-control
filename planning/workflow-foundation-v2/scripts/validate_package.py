#!/usr/bin/env python3
"""Offline structure/integrity validation, not the installed native validator."""
from __future__ import annotations
from pathlib import Path
import argparse,csv,json,sys,re
sys.dont_write_bytecode=True
from common import *
from compile_plan import assemble
PACKAGE=Path(__file__).resolve().parents[1]
TASK_KEYS={'id','parent_id','kind','title','objective','status','priority','tags','parallel_policy','next_action','notes','scope','claim_locks','depends_on','checkpoints','gates','consumes_interfaces','invariants','produced_artifacts'}
PROFILE_ENUMS={
 'difficulty':{'trivial','routine','complex','hard','exceptional'},
 'risk':{'low','medium','high','critical'},
 'work_type':{'inspection','implementation','testing','review','architecture','integration','performance','research','documentation'},
 'context_depth':{'local','focused','deep','cross_project'}}

def check_profile(profile):
    demand(set(profile)==set(PROFILE_ENUMS),'work profile must have exactly four semantic fields')
    for k,v in profile.items():demand(isinstance(v,str) and v in PROFILE_ENUMS[k],f'invalid work profile {k}: {v}')

def validate_structure(master):
    p=assemble(master);tasks=p['tasks'];by={t['id']:t for t in tasks}
    demand(p['schema_version']==3,'bootstrap must use current native schema 3')
    demand(len(by)==len(tasks),'duplicate task identity')
    hierarchy=[];dep=[];cps={};gates={}
    for t in tasks:
        demand(not (set(t)-TASK_KEYS),'unsupported bootstrap-native task keys')
        demand(t.get('status')=='planned','bootstrap must not claim completed/started work')
        demand(t['kind'] in {'epic','task','integration_task','validation_task'},'unsupported task kind')
        if t.get('parent_id'):hierarchy.append((t['parent_id'],t['id']))
        for scope in t.get('scope',{}).values():
            for path in scope:relpath(path)
        for c in t.get('checkpoints',[]):
            demand(c['id'] not in cps,'duplicate checkpoint');cps[c['id']]=t['id']
        for g in t.get('gates',[]):
            demand(g['id'] not in gates,'duplicate gate');gates[g['id']]=t['id']
            demand(g['type']=='command' and g.get('argv') and g.get('required') is True,'required executable gate absent')
            demand(g['argv'][:2]==['python3','-B'],'gate runner must be explicit Python without bytecode')
            demand('run_gate.py' in g['argv'][2] and g['argv'][-1]==t['id'],'gate task identity drift')
    barriers={b['id']:b for b in p.get('barriers',[])}
    demand(len(barriers)==len(p.get('barriers',[])),'duplicate barrier')
    for t in tasks:
        for d in t.get('depends_on',[]):
            if d['type']=='task':dep.append((d['task_id'],t['id']))
            elif d['type']=='checkpoint':dep.append((cps[d['checkpoint_id']],t['id']))
            elif d['type']=='barrier':
                for r in barriers[d['barrier_id']]['requirements']:
                    dep.append((cps[r['id']] if r['type']=='checkpoint' else r['id'],t['id']))
            else:raise PackageError('unreviewed dependency type in bootstrap')
    # The installed v3 validator combines parentage and task prerequisites.
    dag(by,hierarchy+dep)
    run=p['runs'][0];lanes=run['lanes'];lane_ids={l['id'] for l in lanes};assigned=[]
    demand(len(p['runs'])==1 and len(lane_ids)==len(lanes),'ambiguous run/lane IDs')
    root_lanes=[l for l in lanes if not l.get('parent_lane_id')]
    demand(len(root_lanes)==1,'exactly one root lane required')
    lane_graph=[];serial=[];roles={}
    for l in lanes:
        if l.get('parent_lane_id'):lane_graph.append((l['parent_lane_id'],l['id']))
        demand(l['workspace']['mode'] in {'read_shared','isolated_merge','exclusive'},'unreviewed workspace mode')
        if l['role']=='integrator':demand(l['workspace']['mode']=='exclusive','integration destination must be exclusive')
        for tid in l['tasks']:roles[tid]=l['role']
        assigned+=l['tasks'];serial+=list(zip(l['tasks'],l['tasks'][1:]))
    dag(lane_ids,lane_graph)
    demand(len(assigned)==len(set(assigned)) and set(assigned)==set(by),'every task must appear exactly once in a lane')
    root=run['root_task_id'];coord=master['coordinator_task']
    demand(by[root]['kind']=='epic' and not by[root].get('depends_on'),'epic depends on child would cycle in native validator')
    demand(root_lanes[0]['tasks']==[coord,root],'ordinary coordinator must precede epic')
    demand(by[coord]['kind']=='task' and not by[coord].get('depends_on') and by[coord].get('claim_locks'),'coordinator must be claimable before final completion')
    levels,reach=dag(by,dep+serial)
    # Scope overlaps are legal only when actual prerequisite/serial edges order them.
    leaves=[t for t in tasks if t['id'] not in {coord,root}]
    for i,a in enumerate(leaves):
        for b in leaves[i+1:]:
            if b['id'] in reach[a['id']] or a['id'] in reach[b['id']]:continue
            for x in a.get('scope',{}).get('exclusive_paths',[]):
                for y in b.get('scope',{}).get('exclusive_paths',[]):
                    demand(not path_overlap(x,y),f'unordered write overlap {a["id"]} / {b["id"]}: {x} / {y}')
    demand(len({i['id'] for i in p.get('interfaces',[])})==len(p.get('interfaces',[])),'duplicate interface')
    locks={x['name'] for x in p.get('locks',[])}
    invs={x['id'] for x in p.get('invariants',[])}
    demand(len(locks)==len(p.get('locks',[])) and len(invs)==len(p.get('invariants',[])),'duplicate lock/invariant')
    for t in tasks:
        demand(set(t.get('claim_locks',[]))<=locks and set(t.get('invariants',[]))<=invs,'unknown task lock/invariant')
    for barrier in barriers.values():
        demand(bool(barrier['requirements']),'empty barrier')
        for r in barrier['requirements']:
            demand(r['type'] in {'task','checkpoint'},'unsupported barrier requirement')
            demand(r['id'] in (by if r['type']=='task' else cps),'unknown barrier requirement')
    for interface in p.get('interfaces',[]):
        demand(interface['owner_task_id'] in by,'unknown interface owner')
        demand(roles[interface['owner_task_id']] in {'implementer','specialist','integrator'},'interface owner lacks publish role')
        demand(interface['state']=='draft','future interface must not be pre-frozen')
        for path in interface['contract_paths']:relpath(path)
    rich={t['id']:t for t in master['tasks']}
    demand(len(rich)==len(master['tasks']) and set(rich)==set(by),'rich task coverage/identity drift')
    for tid,t in rich.items():
        check_profile(t['work_profile'])
        demand(len(t['acceptance'])>=3,'task lacks substantive acceptance conditions')
        demand(len(t['mechanism'])>=90,'task lacks implementation mechanism')
        demand(t['native']['id']==tid,'native/rich identity drift')
        demand(t['depends']==[d['task_id'] for d in t['native'].get('depends_on',[]) if d['type']=='task'],'rich/native prerequisite drift')
        demand(t['source_scope']=={'write':t['native'].get('scope',{}).get('exclusive_paths',[]),'read':t['native'].get('scope',{}).get('read_paths',[])},'rich/native ownership drift')
    return {'tasks':len(tasks),'lanes':len(lanes),'checkpoints':len(cps),'gates':len(gates),'interfaces':len(p.get('interfaces',[])),'barriers':len(barriers),'dependency_edges':len(set(dep))}, dep+serial

def manifest_check(root):
    path=root/'MANIFEST.sha256';demand(path.is_file() and not path.is_symlink(),'manifest missing')
    entries={}
    for line in path.read_text().splitlines():
        if not line.strip():continue
        h,rel=line.split('  ',1);demand(rel not in entries,'duplicate manifest entry');demand(re.fullmatch(r'[0-9a-f]{64}',h),'invalid manifest digest')
        entries[rel]=h;demand(digest(contained(root,rel))==h,'manifest mismatch: '+rel)
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p!=root/'MANIFEST.sha256' and '__pycache__' not in p.parts}
    demand(all(not p.is_symlink() for p in root.rglob('*')),'symlink in sealed package')
    demand(set(entries)==actual,'manifest coverage mismatch')
    return len(entries)

def validate(root=PACKAGE,peer=None,*,integrity=True):
    root=Path(root).resolve();m=load(root/'machine/proposed_todos.json');stats,edges=validate_structure(m)
    demand((root/'machine'/m['native_plan_file']).read_bytes()==canonical(assemble(m)),'native plan projection drift')
    profiles=load(root/'machine/work_profiles.json')
    demand(profiles=={t['id']:t['work_profile'] for t in m['tasks']},'profile projection drift')
    matrix=load(root/'machine/acceptance_matrix.json')
    demand(set(matrix)=={t['id'] for t in m['tasks']},'acceptance coverage drift')
    for t in m['tasks']:
        entry=matrix[t['id']]
        demand(entry['assertions']==t['acceptance'],'assertion projection drift')
        demand(len(entry['assertion_ids'])==len(t['acceptance']) and len(set(entry['assertion_ids']))==len(t['acceptance']),'assertion ID coverage drift')
        for test in entry['tests']:
            relpath(test['path'])
            demand(bool(test['test_names']) and len(test['test_names'])==len(set(test['test_names'])),'empty/duplicate future test inventory')
        demand(entry['kind'] in {'tests','governance','cross_receipt','closure'},'unknown evidence kind')
        if entry['kind']=='tests':demand(bool(entry['tests']),'implementation task lacks executable acceptance inventory')
        demand((root/'proposed-todos'/(t['id'].lower()+'.md')).is_file(),'human task sheet missing')
    for filename,key in [('lanes.json','lanes'),('barriers.json','barriers'),('interface_catalog.json','interfaces')]:
        expected=m['native_header']['runs'][0]['lanes'] if key=='lanes' else m['native_header'].get(key,[])
        demand(load(root/'machine'/filename)==expected,filename+' drift')
    with (root/'machine/proposed_todos.csv').open(newline='') as f:rows=list(csv.DictReader(f))
    demand(rows==[{k:t[k] for k in ('id','stream','title')}|t['work_profile'] for t in m['tasks']],'task CSV drift')
    with (root/'machine/scope_ownership.csv').open(newline='') as f:ownership=list(csv.DictReader(f))
    demand(ownership==[{'task_id':t['id'],'mode':mode,'path':p} for t in m['tasks'] for mode,paths in t['source_scope'].items() for p in paths],'ownership CSV drift')
    with (root/'machine/checkpoints.csv').open(newline='') as f:checkpoints=list(csv.DictReader(f))
    demand(checkpoints==[{'checkpoint_id':cp['id'],'owner_task_id':t['id']} for t in m['tasks'] for cp in t['native'].get('checkpoints',[])],'checkpoint CSV drift')
    trace=load(root/'machine/requirements_traceability.json')['requirements']
    expected_trace=[{'id':t['id']+f'-A{i+1:02d}','requirement':a,'task_id':t['id'],'gate_id':t['id']+'-G','evidence_kind':matrix[t['id']]['kind']} for t in m['tasks'] for i,a in enumerate(t['acceptance'])]
    demand(trace==expected_trace,'requirements traceability drift')
    streams=load(root/'machine/workstreams.json');lane_by={l['id']:l for l in m['native_header']['runs'][0]['lanes']}
    demand({s['id'] for s in streams}=={t['stream'] for t in m['tasks'] if t['stream']!='COORD'},'workstream coverage drift')
    for stream in streams:
        lane=lane_by[stream['lane_id']]
        demand(stream['task_ids']==lane['tasks']==[t['id'] for t in m['tasks'] if t['stream']==stream['id']],'workstream queue drift')
        demand(stream['role']==lane['role'] and stream['workspace_mode']==lane['workspace']['mode'],'workstream role/mode drift')
    with (root/'machine/dependency_edges.csv').open(newline='') as f:rows=list(csv.DictReader(f))
    expected=sorted((d['task_id'],t['id']) for t in assemble(m)['tasks'] for d in t.get('depends_on',[]) if d['type']=='task')
    demand(sorted((r['prerequisite_task_id'],r['task_id']) for r in rows)==expected,'dependency CSV drift')
    summary=load(root/'machine/plan_summary.json')
    demand(summary['tasks']==len(m['tasks']) and summary['native_plan_sha256']==object_digest(assemble(m)),'summary count/digest drift')
    launch=load(root/'machine/controller_launch.json')
    demand(launch['execute_now'] is False and launch.get('dispatch_command') is None,'package must not authorize automatic dispatch')
    joint_local=load(root/'machine/joint_program.json')
    demand(joint_local['authority_to_apply'] is False and joint_local['execute_now'] is False,'joint metadata unexpectedly authorizes execution')
    own=next((x for x in joint_local['projects'] if x['workspace']==m['workspace']),None)
    demand(own is not None and own['root_task_id']==m['root_task'] and own['run_id']==m['native_header']['runs'][0]['id'],'joint local run identity drift')
    demand(load(root/'machine/cross_repository_contracts.json')['edges']==joint_local['cross_edges'],'cross edge projection drift')
    with (root/'machine/external_dependency_receipts.csv').open(newline='') as f:external=list(csv.DictReader(f))
    expected_external=[{k:edge[k] for k in ('producer_project','producer_task','producer_checkpoint','consumer_task','receipt_kind')}|{'edge_id':edge['id']} for edge in joint_local['cross_edges'] if edge['consumer_project']==m['workspace']]
    demand(external==expected_external,'external receipt CSV drift')
    if integrity:stats['manifest_files']=manifest_check(root)
    if peer:
        pm=load(Path(peer)/'machine/proposed_todos.json');pstats,pedges=validate_structure(pm)
        joint=load(root/'machine/joint_program.json');demand(joint==load(Path(peer)/'machine/joint_program.json'),'joint program drift')
        nodes=[t['id'] for t in m['tasks']+pm['tasks']]
        demand(len(nodes)==len(set(nodes)),'cross-authority task namespace collision')
        all_by={t['id']:t for t in m['tasks']+pm['tasks']}
        projects={x['workspace']:x for x in joint['projects']}
        for e in joint['cross_edges']:
            demand(e['producer_project']!=e['consumer_project'],'cross edge must name distinct authorities')
            demand(e['producer_task'] in all_by and e['consumer_task'] in all_by,'unknown cross-edge endpoint')
            prod=all_by[e['producer_task']]
            demand(e['producer_checkpoint'] in {c['id'] for c in prod['native'].get('checkpoints',[])},'cross-edge checkpoint not owned by producer')
            demand(projects[e['producer_project']]['root_task_id']==prod['native']['parent_id'],'wrong producer project identity')
        levels,_=dag(nodes,edges+pedges+[(e['producer_task'],e['consumer_task']) for e in joint['cross_edges']])
        stats['joint_tasks']=len(nodes);stats['joint_execution_dag_levels']=len(levels);stats['largest_topological_layer']=max(map(len,levels))
        # Add terminal-only obligations, not acquisition prerequisites.
        extra=[]
        for mm in (m,pm):
            extra.append((mm['final_task'],mm['coordinator_task']))
            extra += [(t['id'],mm['root_task']) for t in mm['tasks'] if t['id']!=mm['root_task']]
        dag(nodes,edges+pedges+[(e['producer_task'],e['consumer_task']) for e in joint['cross_edges']]+extra)
    return {'status':'passed','scope':'offline package checks only','native_runtime_validated':False,**stats}

if __name__=='__main__':
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('--peer-package',type=Path);o=a.parse_args()
    try:print(json.dumps(validate(peer=o.peer_package),indent=2))
    except (PackageError,KeyError,OSError) as e:raise SystemExit('validate_package: '+str(e))
