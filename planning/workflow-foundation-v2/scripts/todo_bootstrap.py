#!/usr/bin/env python3
"""Validate, review, explicitly import once, then verify. Never dispatches workers."""
from __future__ import annotations
import argparse,json,os,subprocess,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
from common import *
from validate_package import validate
PACKAGE=Path(__file__).resolve().parents[1]
META=load(PACKAGE/'machine/package.json') if (PACKAGE/'machine/package.json').is_file() else {}

def bridge(python,request):
    p=Path(python).absolute();demand(p.is_file() and os.access(p,os.X_OK),'explicit installed runtime interpreter unavailable')
    result=subprocess.run([str(p),'-B',str(PACKAGE/'scripts/native_bridge.py')],input=json.dumps(request),capture_output=True,text=True,timeout=240)
    try:value=json.loads(result.stdout)
    except ValueError:raise PackageError('runtime did not return JSON: '+result.stderr[-4000:])
    demand(result.returncode==0 and value.get('status')!='error','runtime refused: '+json.dumps(value,default=str))
    return value

def namespace_ids(plan):
    return {'tasks':{t['id'] for t in plan['tasks']},'runs':{r['id'] for r in plan['runs']},'lanes':{l['id'] for r in plan['runs'] for l in r['lanes']},'checkpoints':{c['id'] for t in plan['tasks'] for c in t.get('checkpoints',[])},'gates':{g['id'] for t in plan['tasks'] for g in t.get('gates',[])},'interfaces':{i['id'] for i in plan.get('interfaces',[])},'barriers':{b['id'] for b in plan.get('barriers',[])},'locks':{l['name'] for l in plan.get('locks',[])},'invariants':{i['id'] for i in plan.get('invariants',[])}}

def no_collisions(plan,obs):
    for table,expected in namespace_ids(plan).items():
        rows=obs['tables'].get(table);demand(rows is not None,'collision inventory unavailable: '+table)
        present={r.get('id',r.get('name')) for r in rows}
        demand(not (present&expected),'existing '+table+' IDs: '+str(sorted(present&expected)))

def accepted_preview(result,plan,meta):
    value=result['native'];pre=value.get('current_observation_preconditions')
    demand(value.get('valid') is True and value.get('status')=='validated','full native validation did not succeed')
    demand(value.get('project_uuid')==meta['project_uuid'],'wrong authority UUID')
    demand(value.get('plan_digest')==object_digest(plan),'native canonical plan digest mismatch')
    demand(set(value.get('would_add',[]))=={t['id'] for t in plan['tasks']},'native additions do not match all planned tasks')
    demand(not value.get('would_modify') and not value.get('warnings'),'native existing-record modification/warning requires reconciliation')
    demand(isinstance(pre,dict) and pre.get('workflow_revision')==pre.get('todo_revision') and pre.get('workflow_authority_fingerprint'),'incoherent native preconditions')
    no_collisions(plan,result['observation'])
    return pre

def accepted_verification(result,plan):
    value=result['native'];expected={t['id'] for t in plan['tasks']}
    demand(value.get('valid') is True and value.get('plan_digest')==object_digest(plan),'native verification or canonical plan digest failed')
    demand(not value.get('would_add') and not value.get('warnings'),'native verification reported additions or warnings')
    demand(set(value.get('would_modify',[]))==expected,'native existing-ID verification set is partial or foreign')
    return 'existing_task_ids_reported_as_updates'

def verify_records(plan,obs,*,strict_import=False):
    for table,expected in namespace_ids(plan).items():
        rows=obs['tables'].get(table);demand(rows is not None,'verification inventory unavailable: '+table)
        present={r.get('id',r.get('name')) for r in rows};demand(expected<=present,'missing imported '+table+': '+str(sorted(expected-present)))
    tasks={t['id']:t for t in obs['tables']['tasks']}
    for t in plan['tasks']:
        live=tasks[t['id']]
        for k in ('parent_id','kind','title','objective','priority','parallel_policy','next_action','notes'):
            demand(live.get(k)==t.get(k),f'imported task {t["id"]} field mismatch: {k}')
        if strict_import:demand(live.get('status')=='planned','import unexpectedly started/completed a task')
    runs={r['id']:r for r in obs['tables']['runs']};lanes={l['id']:l for l in obs['tables']['lanes']}
    for r in plan['runs']:
        demand(runs[r['id']]['root_task_id']==r['root_task_id'],'run root mismatch')
        for lane in r['lanes']:
            live=lanes[lane['id']]
            demand(live.get('role')==lane['role'] and live.get('parent_lane_id')==lane.get('parent_lane_id'),'lane role/parent mismatch')
            demand(live.get('workspace_mode')==lane['workspace']['mode'],'lane mode mismatch')
            q=sorted([x for x in obs['tables']['lane_tasks'] if x['lane_id']==lane['id']],key=lambda x:x['position'])
            demand([x['task_id'] for x in q]==lane['tasks'],'lane serial queue mismatch')
    return {'verified_tasks':len(plan['tasks']),'verified_runs':len(plan['runs']),'verified_lanes':sum(len(r['lanes']) for r in plan['runs']),'scope':'identities, task fields, roles, modes, queues and expected entity presence; not implementation qualification'}

def sources(o):
    local=Path(o.repo).resolve(strict=True);peer=Path(o.peer_repo).resolve(strict=True)
    demand(PACKAGE==local/META['package_path'],'invoke installed package inside the explicitly selected authority root')
    demand(local!=peer,'two distinct repository authorities required')
    pm=load(peer/META['package_path']/'machine/package.json')
    demand(pm['workspace']!=META['workspace'] and pm['program_id']==META['program_id'],'wrong peer package')
    validate(PACKAGE,peer/META['package_path'])
    for r,base in ((local,META['baseline_commit']),(peer,pm['baseline_commit'])):
        call(['git','-C',r,'merge-base','--is-ancestor',base,'HEAD'])
    a=source_snapshot(local,o.review_head,allow_dirty=o.acknowledge_dirty)
    b=source_snapshot(peer,o.peer_review_head,allow_dirty=o.acknowledge_dirty)
    return a,b,digest(PACKAGE/'MANIFEST.sha256'),digest(peer/META['package_path']/'MANIFEST.sha256')

def main():
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('operation',choices=['validate','inspect-runtime','preview','apply','verify'])
    a.add_argument('--repo',type=Path);a.add_argument('--peer-repo',type=Path);a.add_argument('--review-head');a.add_argument('--peer-review-head');a.add_argument('--acknowledge-dirty',action='store_true')
    a.add_argument('--runtime-python',type=Path);a.add_argument('--runtime-receipt',type=Path);a.add_argument('--runtime-review-sha256')
    a.add_argument('--receipt',type=Path);a.add_argument('--reviewed-preview',type=Path);a.add_argument('--review-sha256');a.add_argument('--confirm')
    o=a.parse_args()
    if o.operation=='validate':print(json.dumps(validate(PACKAGE,o.peer_repo/META['package_path'] if o.peer_repo else None),indent=2));return
    demand(o.runtime_python is not None,'select --runtime-python explicitly')
    identity=bridge(o.runtime_python,{'operation':'runtime'})
    if o.operation=='inspect-runtime':
        demand(o.receipt,'provide --receipt outside all repositories');dest=new_output(o.receipt,[PACKAGE,*[x for x in (o.repo,o.peer_repo) if x]])
        write_new(dest,identity);print(json.dumps({'runtime_identity':identity,'receipt_sha256':digest(dest)},indent=2));return
    demand(o.repo and o.peer_repo and o.review_head and o.peer_review_head,'both reviewed roots and literal reviewed HEADs are required')
    demand(o.runtime_receipt and o.runtime_review_sha256,'review the inspect-runtime receipt before native operations')
    demand(digest(o.runtime_receipt)==o.runtime_review_sha256,'runtime review digest mismatch')
    demand(load(o.runtime_receipt)==identity,'runtime/configuration changed after review; stop and reinspect')
    local,peer,lmanifest,pmanifest=sources(o)
    plan=load(PACKAGE/'machine'/META['native_plan_file'])
    common={'project':META['workspace'],'repo':str(o.repo.resolve()),'plan':plan}
    if o.operation=='verify':
        checked=bridge(o.runtime_python,{'operation':'validate',**common})
        nv=checked['native'];obs=checked['observation']
        diff_semantics=accepted_verification(checked,plan)
        demand(obs['preconditions']['project_uuid']==META['project_uuid'],'wrong verified project UUID')
        result=verify_records(plan,obs,strict_import=True)
        result['native_diff_semantics']=diff_semantics
        demand(o.receipt,'provide external --receipt');dest=new_output(o.receipt,[o.repo,o.peer_repo]);write_new(dest,{'format':'wf2-bootstrap-verification-v1','program_id':META['program_id'],'workspace':META['workspace'],'project_uuid':META['project_uuid'],'plan_digest':object_digest(plan),'observation':obs,'result':result,'source':local,'peer_source':peer,'authority_to_dispatch':False})
        print(json.dumps(result,indent=2));return
    demand(o.receipt,'provide a new external --receipt output');dest=new_output(o.receipt,[o.repo,o.peer_repo])
    if o.operation=='preview':
        result=bridge(o.runtime_python,{'operation':'validate',**common});pre=accepted_preview(result,plan,META)
        # Bind exactly the observed bytes, not merely Git HEAD.
        demand((local,peer,lmanifest,pmanifest)==sources(o),'source changed while validating')
        demand(identity==bridge(o.runtime_python,{'operation':'runtime'}),'runtime changed while validating')
        write_new(dest,{'format':'wf2-reviewed-preview-v1','created_unix':time.time(),'workspace':META['workspace'],'run_id':META['run_id'],'source':local,'peer_source':peer,'manifest_sha256':lmanifest,'peer_manifest_sha256':pmanifest,'runtime_identity':identity,'plan_digest':object_digest(plan),'approved_preconditions':pre,'native_validation':result['native'],'authority_to_apply':False})
        print(json.dumps({'status':'preview_ready_for_review','receipt':str(dest),'review_sha256':digest(dest),'native':result['native']},indent=2));return
    demand(o.reviewed_preview and o.review_sha256 and o.confirm=='APPLY-'+META['run_id'],'explicit preview digest and exact APPLY-run confirmation are required')
    demand(digest(o.reviewed_preview)==o.review_sha256,'reviewed preview digest mismatch')
    reviewed=load(o.reviewed_preview);check_age(reviewed['created_unix'])
    demand(reviewed['format']=='wf2-reviewed-preview-v1' and reviewed['workspace']==META['workspace'] and reviewed['run_id']==META['run_id'],'wrong reviewed preview')
    for field,value in [('source',local),('peer_source',peer),('manifest_sha256',lmanifest),('peer_manifest_sha256',pmanifest),('runtime_identity',identity),('plan_digest',object_digest(plan))]:
        demand(reviewed[field]==value,'changed after review: '+field)
    marker=o.reviewed_preview.with_name(o.reviewed_preview.name+'.attempt.json')
    new_output(marker,[o.repo,o.peer_repo]);write_new(marker,{'attempted_at':time.time(),'review_sha256':o.review_sha256,'run_id':META['run_id'],'status':'one_attempt_reserved; inspect live outcome before any recovery'})
    try:
        result=bridge(o.runtime_python,{'operation':'apply',**common,'approved_preconditions':reviewed['approved_preconditions'],'confirm':o.confirm,'run_id':META['run_id']})
        write_new(dest,{'format':'wf2-native-import-receipt-v1','result':result,'review_sha256':o.review_sha256,'authority_to_dispatch':False})
        print(json.dumps({'status':'import_returned','receipt':str(dest),'result':result,'next':'separate verify; do not dispatch'},indent=2))
    except Exception as exc:
        if not dest.exists():write_new(dest,{'status':'failed_or_outcome_unknown','error':str(exc),'review_sha256':o.review_sha256,'retry_authorized':False})
        raise
if __name__=='__main__':
    try:main()
    except (PackageError,OSError,KeyError,subprocess.SubprocessError) as exc:raise SystemExit('todo_bootstrap: '+str(exc))
