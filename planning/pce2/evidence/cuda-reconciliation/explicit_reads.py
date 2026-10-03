"""Prove exclusion is ordinary indexing policy, while explicit text reads work."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'src'))
from project_control.skills import SkillRegistry
registry=SkillRegistry(Path('/home/tumlinson/.agents/skills'))
skill=next(s['id'] for s in registry.list(query='cuda',max_items=128)['skills'] if s['name']=='cuda')
resources=[]
for row in registry.resources(skill):
    if Path(row['path']).name == '.project-control-corpus.json':
        resources.extend(json.loads(registry.read_text(skill,row['path'])['content'])['resources'])
rows=[]
for resource in resources:
    if resource.get('index_excluded') and (resource.get('role') in {'archive','aggregate_view','generated','derived_summary'}):
        result=registry.read(skill,resource['path'],line_start=1,budget_bytes=2048)
        assert result['status']=='ok' and result['content']
        assert result['identity']==resource.get('sha256',result['identity'])
        rows.append({'resource_id':resource['id'],'role':resource['role'],'index_excluded':True,'read':result})
assert rows and any('FULL' in r['read']['resource'] for r in rows)
path=Path(__file__).resolve().parent/'explicit-reads.json'
assert not path.exists()
path.write_text(json.dumps({'scope':'Explicit bounded registry read; no model calls','gpu_calls':0,'reads':rows},indent=2)+'\n')
print('PASS:',len(rows),'excluded derived/archive resources remain explicitly readable')
