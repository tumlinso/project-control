#!/usr/bin/env python3
"""Check receipt identity/hash/coverage and reobserve producer through native reads.

Hashes and assertions are not a substitute for independent review of evidence.
This helper never creates completion state or grants acceptance by itself.
"""
from __future__ import annotations
import json,os,subprocess,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
from common import *

def native_observe(python,bridge_path,workspace,repo):
    result=subprocess.run([str(python),'-B',str(bridge_path)],input=json.dumps({'operation':'observe','project':workspace,'repo':str(repo)}),text=True,capture_output=True,timeout=240)
    try:value=json.loads(result.stdout)
    except ValueError:raise PackageError('producer observation not JSON')
    demand(result.returncode==0 and value.get('status')!='error','fresh producer authority unavailable: '+str(value))
    return value

def file_refs(items,*,source_root,evidence_root):
    demand(isinstance(items,list) and bool(items),'nonempty evidence references required')
    checked=[]
    for item in items:
        demand(set(item)=={'domain','path','sha256'},'invalid evidence reference shape')
        demand(item['domain'] in {'source','evidence'},'reference must name source/evidence domain')
        base=source_root if item['domain']=='source' else evidence_root
        path=contained(base,item['path'])
        demand(digest(path)==item['sha256'],'evidence hash mismatch: '+item['path'])
        checked.append(item)
    return checked

def verify_cross(edge,receipt,bindings,joint,bridge_path):
    required={'format','program_id','edge_id','producer_project_uuid','producer_task','producer_checkpoint','qualification','status','source_commit','author','reviewer','source_dependencies','evidence'}
    demand(isinstance(receipt,dict) and required<=set(receipt) and not (set(receipt)-required-{'test_reports'}),'invalid capability receipt fields')
    demand(isinstance(receipt.get('source_commit'),str) and __import__('re').fullmatch(r'[0-9a-f]{40,64}',receipt['source_commit']),'invalid capability source commit')
    producer=next(p for p in joint['projects'] if p['workspace']==edge['producer_project'])
    root=Path(bindings['repositories'][producer['workspace']]).resolve(strict=True)
    evidence=Path(bindings['evidence_root']).resolve(strict=True)
    demand(receipt.get('format')=='wf2-capability-receipt-v1','unrecognized capability receipt')
    for k,want in [('program_id',joint['program_id']),('edge_id',edge['id']),('producer_project_uuid',producer['project_uuid']),('producer_task',edge['producer_task']),('producer_checkpoint',edge['producer_checkpoint'])]:
        demand(receipt.get(k)==want,'cross receipt identity mismatch: '+k)
    demand(receipt.get('qualification')==edge['receipt_kind'],'wrong strength of receipt')
    demand(receipt.get('status')=='qualified','receipt is not qualified')
    demand(isinstance(receipt.get('author'),str) and receipt['author'] and isinstance(receipt.get('reviewer'),str) and receipt['reviewer'] and receipt.get('reviewer')!=receipt.get('author'),'independent producer review required')
    head=git(root,'rev-parse','HEAD');source=receipt['source_commit']
    # Conservative exact pin for bootstrap safety. Later dependency-sensitive
    # descendants require the reviewed renewal protocol, not a force option.
    demand(head==source,'producer source changed; requalify receipt at exact consumed source')
    file_refs(receipt.get('source_dependencies'),source_root=root,evidence_root=evidence)
    file_refs(receipt.get('evidence'),source_root=root,evidence_root=evidence)
    if edge['receipt_kind']=='qualified_capability':
        demand(receipt.get('test_reports'),'qualified capability lacks test execution reports')
        for ref in receipt['test_reports']:
            demand(ref.get('domain') == 'evidence','test report must be in the external evidence domain')
            file_refs([ref],source_root=root,evidence_root=evidence)
            report=load(contained(evidence,ref['path']))
            demand(report.get('status')=='passed' and report.get('source_commit')==source,'test report source/status mismatch')
            demand(isinstance(report.get('tests_run'),int) and report['tests_run']>0,'no executed tests in capability receipt')
            demand(not any(report.get(k,0) for k in ('skips','errors','failures','expected_failures','unexpected_successes')),'nonpassing test inventory')
    obs=native_observe(bindings['runtime_python'],bridge_path,producer['workspace'],root)
    demand(obs['preconditions']['project_uuid']==producer['project_uuid'],'wrong producer authority')
    tasks={x['id']:x for x in obs['tables']['tasks']};cps={x['id']:x for x in obs['tables']['checkpoints']}
    task=tasks.get(edge['producer_task']);cp=cps.get(edge['producer_checkpoint'])
    demand(task and task.get('status')=='done' and task.get('result') not in {'failed','superseded'},'producer task not successfully complete')
    demand(cp and cp.get('state')=='reached','producer checkpoint not reached')
    return {'edge_id':edge['id'],'source_commit':source,'authority_revision':obs['preconditions']['todo_revision'],'project_uuid':producer['project_uuid'],'status':'structurally_verified_with_fresh_authority','reviewer':receipt['reviewer']}
