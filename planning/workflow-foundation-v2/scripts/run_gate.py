#!/usr/bin/env python3
"""Future implementation gate. Missing artifacts/tests fail; no shipped pass flags."""
from __future__ import annotations
import argparse,json,os,subprocess,sys,tempfile,time,uuid
from pathlib import Path
sys.dont_write_bytecode=True
from common import *
from verify_receipt import file_refs,native_observe,verify_cross
PACKAGE=Path(__file__).resolve().parents[1]

def material_head(repo):
    head=git(repo,'rev-parse','HEAD')
    paths=git(repo,'diff','--name-only','HEAD').splitlines()+git(repo,'ls-files','--others','--exclude-standard').splitlines()
    generated=lambda s:s in {'.todo-orchestrator/state.snapshot.json','todo-status.md','todos.md'} or s=='todos' or s.startswith('todos/')
    demand(not [p for p in paths if p and not generated(p)],'commit source-under-test before gate; only generated Todo projections may be dirty')
    return head

def review_record(task,entry,root,evidence,head):
    record=load(contained(evidence,entry['review_record']))
    demand(isinstance(record,dict) and set(record)=={'format','task_id','source_commit','author','reviewer','status','unresolved_required','assertions'},'unknown or missing task review fields')
    demand(isinstance(record['unresolved_required'],list) and isinstance(record['assertions'],list),'review lists have invalid type')
    demand(record.get('format')=='wf2-task-review-v1' and record.get('task_id')==task,'review identity mismatch')
    demand(record.get('source_commit')==head,'review source changed; obtain current review')
    demand(record.get('reviewer') and record.get('author') and record['reviewer']!=record['author'],'independent reviewer must differ from implementer')
    demand(record.get('status')=='accepted' and not record.get('unresolved_required'),'unresolved or unaccepted task review')
    assertions=record.get('assertions',[])
    demand(len(assertions)==len(entry['assertion_ids']) and {a['id'] for a in assertions}==set(entry['assertion_ids']),'review must cover exactly all declared assertion IDs')
    for assertion in assertions:
        demand(isinstance(assertion,dict) and set(assertion)=={'id','status','explanation','evidence'},'unknown or missing assertion fields')
        demand(assertion.get('status')=='pass' and assertion.get('explanation'),'assertion requires reason and current evidence')
        file_refs(assertion.get('evidence'),source_root=root,evidence_root=evidence)
    return record

def execute(task,bindings):
    matrix=load(PACKAGE/'machine/acceptance_matrix.json');demand(task in matrix,'unknown gate task')
    entry=matrix[task];meta=load(PACKAGE/'machine/package.json');joint=load(PACKAGE/'machine/joint_program.json')
    root=PACKAGE.parents[1].resolve();demand(root==Path.cwd().resolve(),'gate must run in the dispatch repository root')
    demand(Path(bindings['repositories'][meta['workspace']]).resolve().is_dir(),'canonical root binding missing')
    evidence=Path(bindings['evidence_root']).resolve(strict=True)
    for r in bindings['repositories'].values():demand(not evidence.is_relative_to(Path(r).resolve()),'evidence must be external to all repositories')
    demand(not evidence.is_relative_to(root),'evidence cannot live in dispatch worktree')
    bridge=PACKAGE/'scripts/native_bridge.py'
    head=material_head(root)
    if entry['kind']=='closure':
        obs=native_observe(bindings['runtime_python'],bridge,meta['workspace'],Path(bindings['repositories'][meta['workspace']]))
        tasks={x['id']:x for x in obs['tables']['tasks']}
        for tid in entry['required_terminal_tasks']:
            r=tasks.get(tid);demand(r and r.get('status')=='done' and r.get('result') not in {'failed','superseded'},'terminal obligation incomplete: '+tid)
        cps={x['id']:x for x in obs['tables']['checkpoints']};demand(cps.get(meta['prefix']+'-I90-CP',{}).get('state')=='reached','final checkpoint missing')
        return {'format':'wf2-gate-result-v1','status':'passed','task_id':task,'source_commit':head,'authority_revision':obs['preconditions']['todo_revision'],'tests_run':0,'evidence_kind':'authoritative_closure_check'}
    review=review_record(task,entry,root,evidence,head)
    result={'format':'wf2-gate-result-v1','status':'passed','task_id':task,'source_commit':head,'review_record_sha256':digest(evidence/entry['review_record']),'evidence_kind':entry['kind'],'tests_run':0,'skips':0,'errors':0,'failures':0,'expected_failures':0,'test_reports':[]}
    if entry['kind']=='cross_receipt':
        edge=next(x for x in joint['cross_edges'] if x['id']==entry['external_edge'])
        ref=bindings['receipts'][edge['id']]
        receipt=load(contained(evidence,ref))
        result['cross_receipt']=verify_cross(edge,receipt,bindings,joint,bridge)
    for spec in entry['tests']:
        contained(root,spec['path'])
        with tempfile.TemporaryDirectory(prefix='wf2-test-spec-') as temp:
            path=Path(temp)/'spec.json';write_new(path,spec)
            proc=subprocess.run([str(bindings['test_python']),'-B',str(PACKAGE/'scripts/strict_unittest.py'),'--repo',str(root),'--spec',str(path)],cwd=root,capture_output=True,text=True,timeout=1500)
        try:report=json.loads(proc.stdout)
        except ValueError:raise PackageError('test runner did not return a valid report: '+proc.stderr[-4000:])
        log=evidence/'runs';log.mkdir(exist_ok=True)
        out=log/(task+'-'+uuid.uuid4().hex+'.json');report['source_commit']=head;report['task_id']=task
        write_new(out,report)
        demand(proc.returncode==0 and report.get('status')=='passed','required tests failed; report preserved at '+str(out))
        result['tests_run']+=report['tests_run'];result['test_reports'].append({'domain':'evidence','path':out.relative_to(evidence).as_posix(),'sha256':digest(out)})
    demand(material_head(root)==head,'source changed during qualification')
    out=evidence/'runs';out.mkdir(exist_ok=True)
    result_path=out/(task+'-gate-'+uuid.uuid4().hex+'.json');write_new(result_path,result)
    return {**result,'result_file':str(result_path),'result_sha256':digest(result_path)}

if __name__=='__main__':
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('--task',required=True);o=a.parse_args()
    try:
        raw=os.environ.get('WF2_BINDINGS');demand(raw,'WF2_BINDINGS must name the explicitly configured external execution binding file')
        print(json.dumps(execute(o.task,load(raw)),indent=2))
    except (PackageError,OSError,KeyError,subprocess.SubprocessError) as exc:
        print(json.dumps({'status':'failed_or_blocked','task_id':o.task,'error':str(exc),'qualification':False}));raise SystemExit(2)
