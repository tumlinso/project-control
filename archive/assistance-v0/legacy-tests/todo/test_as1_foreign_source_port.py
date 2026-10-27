"""Disposable native authorities in a source-bound child (no guard mocks)."""
import os
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
TODO_PACKAGE = ROOT / 'src' / 'todo_orchestrator'
SKILLS = Path(os.environ.get('PROJECT_CONTROL_SKILLS_ROOT', Path.home() / '.agents/skills'))
SCRIPT = r'''
import copy, hashlib, json, os, tempfile
from pathlib import Path
from tests.todo.v2_helpers import V2Repo, base_plan, safe_task
from todo_orchestrator.service import Service
from todo_orchestrator.models import TodoError
from todo_orchestrator.runtime_identity import bind_canonical_runtime
identity = bind_canonical_runtime()
import todo_orchestrator.service as service_module
import todo_orchestrator.plan as plan_module
import todo_orchestrator.project_amendments as amendments_module
actual_source = {}
for module in (service_module, plan_module, amendments_module):
    source = Path(module.__file__).resolve()
    assert source.parent == identity.package_root
    actual_source[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
assert actual_source == json.loads(os.environ['AS1_SOURCE_HASHES'])
print(json.dumps({'fixture_module_sha256':actual_source}))
print(json.dumps({'fixture_source': identity.public()}))
repo = V2Repo()
try:
    scenario = os.environ['AS1_SCENARIO']
    if scenario.startswith('plan'):
        plan = base_plan([safe_task('A', 'src/a', notes='retain me', gates=[{'id':'G','type':'manual','accepted':True}]), safe_task('B', 'src/b', depends_on=[{'type':'task','task_id':'A'}])])
        plan['schema_version'] = 3
        plan['runs'] = [{'id':'RUN','root_task_id':'A','charter':{'objective':'bounded'},'lanes':[{'id':'LANE','role':'implementer','tasks':['A','B']}]}]
        if scenario == 'plan_gate_order':
            plan['tasks'][0]['gates'] = [{'id':'G2','type':'manual','accepted':True},{'id':'G1','type':'manual','accepted':True}]
        first = repo.apply(plan)
        if scenario == 'plan_history':
            claim = repo.service.continue_work(task_id='A')['claim']
            repo.service.gate_run('G', claim['claim_token'])
            repo.service.handoff(claim['claim_token'], note='preserved handoff')
        before = repo.service.db.revision()
        with repo.service.db.read() as conn:
            snapshot = list(conn.iterdump())
        if scenario in ('plan_noop', 'plan_history'):
            result = repo.apply(copy.deepcopy(plan))
            assert result['status'] == 'noop', result
            assert result['project_revision'] == before and result['projection'] is None
            with repo.service.db.read() as conn:
                assert list(conn.iterdump()) == snapshot
            assert repo.service.plan_diff(str(repo.root/'plan.json'))['update'] == []
            readonly = Service(repo.root, read_only=True)
            assert readonly.plan_diff(str(repo.root/'plan.json'))['status'] == 'noop'
        elif scenario == 'plan_change':
            plan['tasks'][0]['scope'] = {'exclusive_paths':['src/new']}
            result = repo.apply(plan)
            assert result['project_revision'] == before + 1
            with repo.service.db.read() as conn:
                assert conn.execute("SELECT path FROM ownership_scopes WHERE task_id='A'").fetchone()[0] == 'src/new'
        elif scenario == 'plan_omitted':
            partial = base_plan([{'id':'A','title':'A'}]); partial['schema_version'] = 3
            result = repo.apply(partial)
            assert result['status'] == 'noop', result
        elif scenario in ('plan_partial_run', 'plan_gate_order'):
            plan['tasks'][0] = {'id':'A','title':'A'}
            result = repo.apply(plan)
            assert result['status'] == 'noop', result
        elif scenario == 'plan_lane':
            plan['runs'][0]['lanes'][0]['tasks'] = ['B','A']
            result = repo.apply(plan)
            assert result['project_revision'] == before + 1
        print('PASS', scenario)
    else:
        old_state = os.environ.pop('TODO_ORCHESTRATOR_STATE_DIR', None)
        foreign_dir = tempfile.TemporaryDirectory()
        foreign_root = Path(foreign_dir.name)
        import subprocess
        subprocess.run(['git','-C',str(foreign_root),'init','-q'],check=True)
        foreign, _ = Service.bootstrap(foreign_root, 'foreign-native')
        os.environ['TODO_ORCHESTRATOR_STATE_DIR'] = old_state
        (foreign_root/'facts.md').write_text('exact source')
        sha = hashlib.sha256((foreign_root/'facts.md').read_bytes()).hexdigest()
        locator = {'project': foreign.project['project_uuid'], 'repository':'foreign', 'path':'facts.md', 'content_sha256':sha}
        calls = []
        def verifier(anchor):
            calls.append(anchor)
            with foreign.db.read() as conn:
                revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
            receipt = {'project':'foreign-id', 'project_uuid':foreign.project['project_uuid'], 'repository':'foreign', 'path':'facts.md', 'content_sha256':hashlib.sha256((foreign_root/'facts.md').read_bytes()).hexdigest(), 'revision':revision}
            if scenario == 'foreign_substituted': receipt['repository'] = 'substitute'
            if scenario == 'foreign_bad_uuid': receipt['project_uuid'] = 'not-a-uuid'
            if scenario == 'foreign_bad_revision': receipt['revision'] = True
            if scenario == 'foreign_error': raise RuntimeError('broker unavailable')
            return receipt
        if scenario != 'foreign_absent':
            repo.service = Service(repo.root, project_source_verifier=verifier)
        request = {'format':'pc-project-amendment/1','project':repo.service.project['project_uuid'],'action':'update_orientation','intent':'verified foreign context','expected_revision':repo.service.db.revision(),'operation_id':'foreign-1','mode':'preview','payload':{'id':'orientation','fields':{'objective':'bounded foreign facts'},'anchors':[locator],'field_anchors':{'objective':[locator]}}}
        if scenario in ('foreign_absent','foreign_substituted','foreign_bad_uuid','foreign_bad_revision','foreign_error'):
            try: repo.service.amend_project(request,role='mutator')
            except TodoError as exc:
                assert exc.code == ('source_prerequisite_stale' if scenario == 'foreign_substituted' else 'source_prerequisite_unavailable'), exc.code
            else: raise AssertionError('foreign authority accepted')
        else:
            preview = repo.service.amend_project(request,role='mutator')
            assert preview['sources'][0]['project_uuid'] == foreign.project['project_uuid']
            request['mode'] = 'apply'
            if scenario == 'foreign_revision':
                foreign.db.mutate(actor_session_id=None, entity_type='project', entity_id=foreign.project['project_uuid'], event_type='fixture.changed', payload={}, operation=lambda c,r:{})
                try: repo.service.amend_project(request,role='mutator')
                except TodoError as exc: assert exc.code == 'proposal_stale', exc.code
                else: raise AssertionError('changed revision accepted')
            else:
                result = repo.service.amend_project(request,role='mutator')
                assert result['status'] == 'applied'
                context = repo.service.project_context()['orientation'][0]
                stored = context['payload']['anchors'][0]
                assert stored['project'] == 'foreign-id' and stored['project_uuid'] == foreign.project['project_uuid'] and 'revision' in stored
                assert context['field_freshness']['objective']['status'] == 'fresh'
                if scenario == 'foreign_freshness_revision':
                    foreign.db.mutate(actor_session_id=None, entity_type='project', entity_id=foreign.project['project_uuid'], event_type='fixture.changed', payload={}, operation=lambda c,r:{})
                else:
                    (foreign_root/'facts.md').write_text('changed')
                assert repo.service.project_context()['orientation'][0]['field_freshness']['objective']['status'] == 'stale'
                replay = repo.service.amend_project(request,role='mutator')
                assert replay['status'] == 'replayed' and replay['receipt'] == result['receipt']
                assert 'current_readiness' in replay
        foreign_dir.cleanup()
        print('PASS', scenario)
finally:
    repo.close()
'''


@pytest.mark.parametrize('scenario', ['foreign_absent', 'foreign_substituted', 'foreign_verified', 'foreign_revision', 'foreign_freshness_revision', 'foreign_bad_uuid', 'foreign_bad_revision', 'foreign_error', 'plan_noop', 'plan_history', 'plan_change', 'plan_omitted', 'plan_partial_run', 'plan_gate_order', 'plan_lane'])
def test_native_source_port(scenario):
    env = os.environ.copy()
    # A child fixture is independently bound to actual source. The deployed
    # parent binding is neither altered nor represented as qualifying this code.
    for key in ('CODING_WORKFLOW_RUNTIME_FINGERPRINT', 'TODO_ORCHESTRATOR_STATE_DIR',
                'PROJECT_CONTROL_RELEASE_MANIFEST', 'PROJECT_CONTROL_RELEASE_DIGEST'):
        env.pop(key, None)
    source_hashes = {name: hashlib.sha256((TODO_PACKAGE/name).read_bytes()).hexdigest()
                     for name in ('service.py', 'plan.py', 'project_amendments.py')}
    env['AS1_SOURCE_HASHES'] = json.dumps(source_hashes, sort_keys=True)
    env.update(PROJECT_CONTROL_SKILLS_ROOT=str(SKILLS),
               PYTHONPATH=os.pathsep.join([str(ROOT/'src'), str(ROOT/'tests/todo')]), AS1_SCENARIO=scenario,
               PYTHONDONTWRITEBYTECODE='1')
    process = subprocess.run([sys.executable, '-c', SCRIPT], env=env, text=True, capture_output=True)
    assert process.returncode == 0, process.stdout + process.stderr
