"""Executed complete journeys, plus immutable paired hardware proof consumers."""
import json
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest
from qualify_helpers import COMMON, ROOT, REPORTS, SKILLS, child, sha


@pytest.mark.as1_case('E2E-01')
def test_observer_source_evidence_cross_project_journey():
    record = child(COMMON + r'''
roots={}; services={}
for name,files in {
 'api':{'README.md':'Purpose: numerical contract.\n','base.py':'VALUE=1\n','generated.hpp':'int generated(int);\n'},
 'consumer':{'README.md':'Python numerical consumer.\n','consumer.py':'import base\n','later.py':'import consumer\n'},
 'declaration':{'README.md':'[contract](contract.md)\n','contract.md':'Generated numerical interface declaration.\n'}}.items():
    roots[name],services[name]=repository(name,files)
config=configuration(roots)
mutator=create_mcp(config,profile='mutator',state_directory=home/'mutator')
for name,svc in services.items():
    payload={'id':'purpose','fields':{'purpose':(roots[name]/'README.md').read_text().strip()},'anchors':[{'project':name,'repository':'source','path':'README.md','content_sha256':digest(roots[name]/'README.md')}]}
    assert amend(mutator,name,svc,'update_orientation',payload,'orientation-'+name)['status']=='applied'
uuid={name:svc.project['project_uuid'] for name,svc in services.items()}
def node(name,path): return {'project_uuid':uuid[name],'repository':'source','kind':'file','id':path,'path':path}
for name,id,source,target in [('consumer','python-generated',node('consumer','consumer.py'),node('api','generated.hpp')),('declaration','markdown-python',node('declaration','contract.md'),node('consumer','consumer.py'))]:
    assert amend(mutator,name,services[name],'register_relation',{'id':id,'relation':'depends_on','source':source,'target':target},'relation-'+id)['status']=='applied'
assert amend(mutator,'api',services['api'],'register_generation',{'id':'generator','roots':['generated.hpp'],'inputs':[{'project':'api','repository':'source','path':'base.py','content_sha256':digest(roots['api']/'base.py')}],'generator':{'tool':'qualification-generator','version':'1'}},'generation-1')['status']=='applied'
plan=base_plan([safe_task('T1','work')]); plan['project']['name']='api'; path=roots['api']/'plan.json';path.write_text(json.dumps(plan));services['api'].plan_apply(str(path))
server=create_mcp(config,profile='observer',state_directory=home/'observer')
def public(tool,project='api',**args): return call(server,tool,{'project':project,'detail':'extended',**args})
try:
    catalog=call(server,'overview',{}); assert {p['project'] for p in catalog['data']['projects']}==set(roots)
    overview=public('overview'); assert overview['data']['orientation'] and overview['sources']
    read=public('read',paths=['README.md','base.py','generated.hpp','missing.py'],repository='source')
    assert read['status']=='partial' and read['data']['files'][-1]['status']=='error'
    for source in read['sources']: assert source['content_sha256']==digest(roots['api']/source['path'])
    exact=public('search',query={'kind':'task','target':'T1'}); assert exact['data']['resolution']['status']=='resolved'
    evidence=public('evidence',subject='T1'); assert evidence['packet'] and evidence['coverage']
    trace=public('impact',targets=[{'repository':'source','path':'base.py'}],view='snippets')
    rows={(r['node']['project_uuid'],r['node']['id']):r for r in trace['data']['dependencies']}
    for key in [(uuid['api'],'generated.hpp'),(uuid['consumer'],'consumer.py'),(uuid['consumer'],'later.py'),(uuid['declaration'],'contract.md')]: assert key in rows,(key,trace)
    assert any(row.get('snippet',{}).get('status')=='ok' for row in rows.values()),trace
    for row in rows.values():
        if row.get('snippet',{}).get('status')=='ok':
            n=row['node'];name=next(k for k,v in uuid.items() if v==n['project_uuid']); assert row['snippet']['content_sha256']==digest(roots[name]/n['path'])
            assert row['snippet']['text']=='\n'.join((roots[name]/n['path']).read_text().splitlines()[:20])
    assert not trace['data']['coverage']['complete_graph_cut']
    generation=trace['data']['generation'];(roots['consumer']/'new.py').write_text('import consumer\n')
    changed=public('impact',targets=[{'repository':'source','path':'base.py'}])
    assert any(r['node']['id']=='new.py' for r in changed['data']['dependencies']) and changed['data']['generation']!=generation
    (roots['consumer']/'consumer.py').write_text('import later\n')
    cyclic=public('impact',project='consumer',targets=[{'repository':'source','path':'consumer.py'}]); assert cyclic['data']['traversal']['cycle_or_shared_path_count']>=1
    for packet in (catalog,overview,read,exact,evidence,trace):
        assert server._project_control_surface.store.lookup(packet['packet'],access_scope=server._project_control_surface.host.scope('api' if packet is not catalog else None)).status=='ok'
    emit({'journey':'observer','catalog':catalog,'read':read,'exact':exact,'evidence':evidence,'trace':trace,'new_consumer':changed,'cycle':cyclic})
finally:
    server._project_control_surface.close();mutator._project_control_surface.close();tmp.cleanup()
''', 'observer-journey')
    assert record['trace']['data']['dependencies']


@pytest.mark.as1_case('E2E-02')
def test_native_coder_and_independent_mutator_journey():
    child(COMMON + r'''
root,service=repository('demo',{'README.md':'Native task fixture.\n','src/a/contract.txt':'Exact native source authority.\n'})
config=configuration({'demo':root});mutator=create_mcp(config,profile='mutator',state_directory=home/'mutator')
plan=base_plan([safe_task('A','src/a')]);plan['project']['name']='demo'
before=service.db.revision()
validated=call(mutator,'plan',{'project':'demo','action':'validate','native_plan':plan});assert validated['valid']
diff=call(mutator,'plan',{'project':'demo','action':'diff','native_plan':plan});assert diff['would_add']==['A'] and service.db.revision()==before
applied=call(mutator,'plan',{'project':'demo','action':'apply','native_plan':plan});assert applied['status']=='applied'
coder=create_mcp(config,profile='coder',state_directory=home/'coder')
try:
    schemas=asyncio.run(enumerate_tool_schemas(coder));assert not {'read','skill','plan','amend_project'}&schemas.keys()
    assert 'No overview is automatic.' in coder.instructions
    claimed=call(coder,'next_task',{'repo_root':str(root),'task_id':'A'});handle=claimed['workflow_handle'];assert handle.startswith('wfc_')
    text=(root/'src/a/contract.txt').read_text();assert text=='Exact native source authority.\n'
    payload={'kind':'finding','payload':{'id':'qualification-source','anchors':[{'project':service.project['project_uuid'],'repository':str(root),'path':'src/a/contract.txt','content_sha256':digest(root/'src/a/contract.txt')}]}}
    published=call(coder,'coordinate_task',{'workflow_handle':handle,'action':'publish_context','payload':payload});assert published['status']=='published',published
    assert call(coder,'coordinate_task',{'workflow_handle':handle,'action':'publish_context','payload':payload})['status']=='noop'
    finished=call(coder,'finish_task',{'workflow_handle':handle,'action':'complete','disposition':'implemented','note':'Exact fixture source inspected'});assert finished['status']=='idle',finished
    from mcp.server.fastmcp.exceptions import ToolError
    for forbidden in ('read','skill','plan','amend_project'):
        try:call(coder,forbidden,{})
        except ToolError:pass
        else:raise AssertionError('forbidden adapter/mutation exposed')
    identity=amend(mutator,'demo',service,'register_identity',{'id':'api','repository':'source','version':'1'},'identity-1');assert identity['invalidations']
    uuid=service.project['project_uuid']
    relation={'id':'consumer','relation':'depends_on','source':{'project_uuid':uuid,'repository':'source','kind':'file','id':'README.md','path':'README.md'},'target':{'project_uuid':uuid,'repository':'source','kind':'identity','id':'api'}}
    first=amend(mutator,'demo',service,'register_relation',relation,'relation-1');assert first['invalidations']
    relation['relation']='consumes';second=amend(mutator,'demo',service,'register_relation',relation,'relation-2');assert second['invalidations'] and second['status']=='applied'
    emit({'journey':'native-coder-mutator','validated':validated,'diff':diff,'claim':claimed,'publication':published,'finish':finished,'relation_amendment':second})
finally:coder._project_control_surface.close();mutator._project_control_surface.close();tmp.cleanup()
''', 'native-journey')


@pytest.mark.as1_case('E2E-01')
def test_observer_genuine_compiler_export_retains_unknown_closure(tmp_path, monkeypatch):
    import project_control.as1_trace as loaded
    assert sha(loaded.__file__)==sha(ROOT/'src/project_control/as1_trace.py')
    from test_pc_as1_trace import test_genuine_installed_ctxpp_producer_and_trace_consumer
    test_genuine_installed_ctxpp_producer_and_trace_consumer(tmp_path, monkeypatch)
    record=json.loads((tmp_path/'genuine-ctxpp-evidence.json').read_text())
    assert record['native_call_count'] and record['consumer_call_count']
    REPORTS.mkdir(parents=True,exist_ok=True)
    (REPORTS/'compiler-journey.json').write_text(json.dumps(record,indent=2)+'\n')


@pytest.mark.as1_case('E2E-02')
def test_native_recovery_preserves_dirty_source_and_receipt_replay():
    from test_pc_as1_control import test_exact_supersession_receipt_dirty_handoff_and_current_continuation, test_stopped_execution_diagnose_prepare_execute_and_live_refusal
    test_exact_supersession_receipt_dirty_handoff_and_current_continuation()
    test_stopped_execution_diagnose_prepare_execute_and_live_refusal()


@pytest.mark.as1_case('E2E-04')
def test_measured_matched_frozen_old_and_current_new_journey(tmp_path):
    old=Path('/home/tumlinson/.local/share/project-control/candidates/cuda-reconciliation-3a55e7c-20261003')
    manifest=old/'release-manifest.json'
    assert sha(manifest)=='e01182edf95a08378d745736922d81b2db7b834699dbe625c05249cefd0498b8'
    root=tmp_path/'matched';root.mkdir()
    (root/'README.md').write_text('Fixture purpose: bounded calculations.\n')
    (root/'module.py').write_text('def calculate_total(value):\n    return value + 1\n')
    subprocess.run(['git','init','-q','-b','main'],cwd=root,check=True)
    subprocess.run(['git','add','.'],cwd=root,check=True)
    subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','matched fixture'],cwd=root,check=True)
    script=(ROOT/'tests/as1/qualify_benchmark.py').read_text()
    baseline=child(script,'economics-old',interpreter=str(old/'bin/python'),environment={
        'PYTHONPATH':'','AS1_BENCH_ROOT':str(root),'AS1_BENCH_MODE':'old',
        'PROJECT_CONTROL_SKILLS_ROOT':str(old/'runtime-skills'),
        'PROJECT_CONTROL_RELEASE_MANIFEST':str(manifest),
        'PROJECT_CONTROL_RELEASE_DIGEST':sha(manifest),
        'PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT':json.loads(manifest.read_text())['todo_runtime_fingerprint']})
    current=child(script,'economics-new',environment={'AS1_BENCH_ROOT':str(root),'AS1_BENCH_MODE':'new'})
    assert baseline['source_sha256']==current['source_sha256']
    assert baseline['snapshot']==current['snapshot']
    assert baseline['correct_facts']==current['correct_facts']
    assert str(old) in baseline['identity']['module']
    assert str(ROOT) in current['identity']['module']
    comparison={'question':'Orient, read both defining files, find exact task T1, inspect pending gate evidence.',
        'old':baseline['metrics'],'new':current['metrics'],
        'correctness_and_provenance_regression':False,'source_sha256':current['source_sha256'],
        'limits':current['limits'],
        'tradeoff':'New response packets include durable evidence/source identity; total exposure is compared including all discovered schemas and wire content.'}
    assert current['metrics']['schema_bytes']<baseline['metrics']['schema_bytes']
    assert current['metrics']['calls']<=baseline['metrics']['calls']
    comparison['total_bytes_delta']=current['metrics']['total_client_visible_bytes']-baseline['metrics']['total_client_visible_bytes']
    (REPORTS/'economics-comparison.json').write_text(json.dumps(comparison,indent=2)+'\n')


def paired_consumer():
    path=SKILLS/'tests/as1/test_sk_as1_qualify.py'
    assert path.is_file(), 'Paired qualification consumer unavailable'
    spec=importlib.util.spec_from_file_location('as1_paired_qualification_consumer',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.as1_case('E2E-03')
def test_real_inference_busy_skill_restart_eviction_reuse(tmp_path):
    paired=paired_consumer()
    # This consumer verifies raw admissions, completed jobs, direct excerpts,
    # actual distinct model PIDs, durable retained packets, source-read counts,
    # real usage records, GPU cleanup and unchanged protected services.
    paired.test_real_scout_skill_eviction_proof_is_bound_to_exact_candidate(tmp_path)
    proof=json.loads(paired.PROOF.read_text())
    installed=paired.CANDIDATE/'lib/python3.13/site-packages/project_control'
    hashes={}
    product_paths=list((ROOT/'src/project_control').glob('as1_*.py'))
    product_paths += [ROOT/'src/project_control'/name for name in ('observer_analysis.py','app.py','profiles.py','runtime_identity.py')]
    for path in product_paths:
        assert sha(installed/path.name)==sha(path), path.name
        hashes[path.name]=sha(path)
    turns=json.loads((paired.PROOF.parent/proof['inference_artifact']).read_text())
    usage={'completion_tokens':sum(t['response']['usage']['completion_tokens'] for t in turns),
           'prompt_tokens':sum(t['response']['usage']['prompt_tokens'] for t in turns) if all('prompt_tokens' in t['response']['usage'] for t in turns) else None,
           'actual_turns':len(turns),'model_ids':sorted({t['model_id'] for t in turns}),
           'model_pids':sorted({t['model_pid'] for t in turns}),
           'comparison_limit':'Actual paired model usage only; no matched historical model journey or pricing equivalence.'}
    REPORTS.mkdir(parents=True,exist_ok=True)
    (REPORTS/'paired-real-consumption.json').write_text(json.dumps({'proof':str(paired.PROOF),
        'proof_sha256':sha(paired.PROOF),'consumer_sha256':sha(SKILLS/'tests/as1/test_sk_as1_qualify.py'),
        'source_identity':proof['source_identity'],'installed_product_hashes':hashes,'usage':usage,
        'actual_broker_metrics':proof['metrics'],
        'status':'passed'},indent=2)+'\n')


@pytest.mark.as1_case('E2E-03')
def test_skill_selected_resource_change_fails_direct_authority(tmp_path):
    # Non-inference TOCTOU fixture supplements, and cannot replace, the
    # mandatory real paired inference test above.
    import project_control.as1_skill as loaded
    assert sha(loaded.__file__)==sha(ROOT/'src/project_control/as1_skill.py')
    from test_pc_as1_skill import test_assembly_revalidates_stale_escape_and_redaction_without_fake_verbatim
    test_assembly_revalidates_stale_escape_and_redaction_without_fake_verbatim(tmp_path,'changed')


@pytest.mark.as1_case('E2E-05')
def test_current_conformance_and_independent_review_are_executed():
    index_path=REPORTS/'conformance-index.json'
    assert index_path.is_file(), 'Executed source-bound conformance index required'
    index=json.loads(index_path.read_text())
    expected={row['id'] for row in json.loads((ROOT/'planning/adaptive-surface-v1/contracts/acceptance-cases.json').read_text())['cases']
              if row['project']=='project-control' and row['outcome'] not in {'PC-AS1-QUALIFY','PC-AS1-RELEASE'} and row['required']}
    covered=set()
    for reference in index['reports']:
        path=REPORTS/reference['path'];assert path.resolve().is_relative_to(REPORTS.resolve())
        assert sha(path)==reference['sha256']
        report=json.loads(path.read_text())
        selected=set(reference['cases'])
        if reference['kind']=='pytest':
            excluded=reference.get('superseded_failed_tests',{})
            if excluded:
                actual_failed={row['test'] for rows in report['cases'].values() for row in rows if row['outcome']!='passed'}
                assert set(excluded)==actual_failed
                for test,replacement in excluded.items():
                    replacement_ref=next(row for row in index['reports'] if row['path']==replacement)
                    replacement_path=REPORTS/replacement
                    assert sha(replacement_path)==replacement_ref['sha256']
                    replacement_report=json.loads(replacement_path.read_text())
                    assert replacement_report['pytest_exitstatus']==0
                    replaced=[row for rows in replacement_report['cases'].values() for row in rows if row['test']==test]
                    assert replaced and all(row['outcome']=='passed' for row in replaced)
            for case in selected:
                rows=[row for row in report['cases'][case] if row['test'] not in excluded]
                assert rows and all(row['outcome']=='passed' for row in rows),case
            if report['pytest_exitstatus']!=0 and not excluded:
                # Historical combined run contributes only its passing MUT rows.
                assert selected=={f'MUT-{i:02}' for i in range(1,6)}
                assert reference['limit']=='MUT cases passed; earlier API assertion failures superseded by final public report.'
        elif reference['kind']=='native_gate':
            assert report['status']=='passed' and report['kind']=='executed_product_acceptance'
            assert report['pytest_returncode']==0 and not report['missing_or_failed_cases']
            assert selected<=set(report['passed_cases'])
        else:raise AssertionError('Unknown conformance evidence kind')
        covered.update(selected)
    assert expected<=covered,sorted(expected-covered)
    for reference in index.get('historical_reports',[]):
        path=REPORTS/reference['path']
        assert path.resolve().is_relative_to(REPORTS.resolve()) and sha(path)==reference['sha256']
    history=index.get('source_history')
    if history:
        previous=REPORTS/history['previous_index']
        assert previous.resolve().is_relative_to(REPORTS.resolve())
        assert sha(previous)==history['previous_index_sha256']
    latest_checks={}
    for check in index['superseding_source_checks']:
        previous=latest_checks.get(check['path'])
        if previous:assert check['historical_sha256']==previous['current_sha256']
        latest_checks[check['path']]=check
        assert check['execution'] in {row['path'] for row in index['reports']}
    for path,check in latest_checks.items():assert check['current_sha256']==index['source_hashes'][path]
    for relative,expected_hash in index['source_hashes'].items():
        assert sha(ROOT/relative)==expected_hash,relative
    for relative,expected_hash in index['receipt_hashes'].items():
        assert sha(Path('/home/tumlinson/project-control')/relative)==expected_hash,relative
    entry=index.get('paired_entry_producer')
    if entry:
        for relative,expected_hash in entry['source_hashes'].items():
            assert sha(Path('/home/tumlinson/.agents/skills')/relative)==expected_hash,relative
        native=entry['native_execution']
        assert sha(REPORTS/native['path'])==native['sha256']
        assert '10 passed' in (REPORTS/native['path']).read_text()
        assert entry['source_hashes']['local-coding-worker/local_worker/observer_runtime.py']==entry['qualified_observer_runtime_sha256']
    paired=paired_consumer()
    report_path=Path(os.environ.get('AS1_SQA_REPORT','/home/tumlinson/.local/state/project-control/as1-bootstrap/sqa/final-acceptance-report.json'))
    assert report_path.is_file(), 'Current executed SQA report required'
    sqa=json.loads(report_path.read_text());assert sqa['pytest_exitstatus']==0
    for case in ('SQA-01','SQA-02','SQA-03'):
        assert sqa['cases'][case] and all(row['outcome']=='passed' for row in sqa['cases'][case]),case
    # Source-bound raw proof remains mandatory even if a typed SQA report says
    # passed. Its hash must match the E2E03 consumption just performed.
    consumption=json.loads((REPORTS/'paired-real-consumption.json').read_text())
    assert consumption['proof_sha256']==sha(paired.PROOF)
    assert consumption['consumer_sha256']==sha(SKILLS/'tests/as1/test_sk_as1_qualify.py')
    review_path=SKILLS/'planning/adaptive-surface-v1/validation/skills-paired-qualification-review.json'
    review=json.loads(review_path.read_text())
    assert review['independent_review_completed'] is True
    assert review['reviewer'] and review['configured_role']=='wf2-reviewer'
    for finding in review['findings']:
        assert finding['status']=='executed_verified',finding
    for path,expected_hash in review['source_review_followup_sha256'].items():assert sha(path)==expected_hash,path
    REPORTS.mkdir(parents=True,exist_ok=True)
    (REPORTS/'conformance-consumption.json').write_text(json.dumps({'status':'passed','index_sha256':sha(index_path),
        'required_pc_cases':sorted(expected),'sqa_report':str(report_path),'sqa_report_sha256':sha(report_path),
        'independent_review':str(review_path),'independent_review_sha256':sha(review_path),
        'source_identity':consumption['source_identity'],
        'limits':['Historical native gates are retained; current source hashes and later changed CONTROL/public executions are verified separately.']},indent=2)+'\n')
