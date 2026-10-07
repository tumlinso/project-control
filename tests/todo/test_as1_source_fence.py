"""Actual source-bound publication transactions in disposable authorities."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
TODO_PACKAGE = ROOT / 'src' / 'todo_orchestrator'
SKILLS = Path(os.environ.get('PROJECT_CONTROL_SKILLS_ROOT', Path.home() / '.agents/skills'))
SCRIPT = r'''
import hashlib, json, os
from pathlib import Path
from tests.todo.v2_helpers import V2Repo, base_plan, safe_task
from todo_orchestrator.service import Service
from todo_orchestrator.models import TodoError
from todo_orchestrator.runtime_identity import bind_canonical_runtime
from todo_orchestrator import project_amendments, service, plan
identity = bind_canonical_runtime()
actual = {}
for module in (service, plan, project_amendments):
    path = Path(module.__file__).resolve()
    assert path.parent == identity.package_root
    actual[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
assert actual == json.loads(os.environ['AS1_SOURCE_HASHES'])
print(json.dumps({'fixture_source': identity.public(), 'fixture_module_sha256': actual}))
repo = V2Repo()
try:
    scenario = os.environ['AS1_SCENARIO']
    repo.apply(base_plan([safe_task('A', 'src/a')]))
    source = repo.root / 'src/a/contract.txt'
    source.parent.mkdir(parents=True)
    source.write_text('exact local source\n')
    token = repo.service.continue_work(task_id='A')['claim']['claim_token']
    project = repo.service.project
    project_uuid = project['project_uuid']
    project['configuration']['registered_project_id'] = 'registered-local'
    locator = {'project': project_uuid, 'repository': str(repo.root),
               'path': 'src/a/contract.txt',
               'content_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    if scenario == 'name': locator['project'] = project['project_name']
    if scenario == 'registered': locator['project'] = 'registered-local'
    if scenario == 'repository_alias': locator['repository'] = 'unregistered-alias'
    if scenario == 'fallback_stale': locator['content_sha256'] = '0' * 64
    calls = []
    before = repo.service.db.revision()
    if scenario == 'orientation_uuid': locator['project_uuid'] = project_uuid
    if scenario == 'orientation_pinned':
        locator.update(project_uuid=project_uuid, revision=before)
    if scenario == 'orientation_bad_uuid': locator['project_uuid'] = '00000000-0000-0000-0000-000000000000'
    if scenario == 'orientation_bad_revision': locator['revision'] = before + 1
    def verifier(anchor):
        calls.append(dict(anchor))
        assert anchor == {**locator, 'project': project_uuid}
        if scenario == 'denied':
            raise TodoError('source_prerequisite_stale', 'host denies local source')
        if scenario in ('raised', 'repository_alias'):
            raise RuntimeError('host source unavailable')
        if scenario == 'incomplete': return None
        receipt = {**anchor, 'project': 'registered-local', 'project_uuid': project_uuid,
                   'revision': repo.service.db.revision()}
        if scenario == 'substituted_hash': receipt['content_sha256'] = '0' * 64
        if scenario == 'substituted_path': receipt['path'] = 'src/a/substitute.txt'
        if scenario == 'substituted_repository': receipt['repository'] = 'substitute'
        return receipt
    if not scenario.startswith('fallback'):
        repo.service.project_source_verifier = verifier
    request = {'kind': 'finding', 'task_id': 'A',
               'payload': {'id': 'local-fence', 'anchors': [locator]}}
    with repo.service.db.read() as conn:
        snapshot = list(conn.iterdump())
    if scenario.startswith('orientation'):
        amendment = {'format': 'pc-project-amendment/1', 'project': 'registered-local',
                     'action': 'update_orientation', 'intent': 'local content freshness',
                     'expected_revision': before, 'operation_id': 'local-orientation-1',
                     'payload': {'id': 'local-orientation', 'fields': {'objective': 'content'},
                                 'anchors': [locator]}}
        if scenario in ('orientation_bad_uuid', 'orientation_bad_revision'):
            try:
                repo.service.amend_project(amendment, role='mutator')
            except TodoError as exc:
                assert exc.code == 'source_prerequisite_stale', exc.code
            else:
                raise AssertionError('invalid caller pin accepted')
            assert repo.service.db.revision() == before
            with repo.service.db.read() as conn:
                assert list(conn.iterdump()) == snapshot
            assert len(calls) == 1
        else:
            assert repo.service.amend_project(amendment, role='mutator')['status'] == 'applied'
            context = repo.service.project_context()['orientation'][0]
            assert context['payload']['anchors'] == [locator]
            assert context['field_freshness']['objective']['status'] == 'fresh'
            with repo.service.db.read() as conn:
                applied_snapshot = list(conn.iterdump())
            amendment.update(operation_id='local-orientation-2', expected_revision=before + 1)
            if scenario == 'orientation_pinned':
                try:
                    repo.service.amend_project(amendment, role='mutator')
                except TodoError as exc:
                    assert exc.code == 'source_prerequisite_stale', exc.code
                else:
                    raise AssertionError('explicit stale revision pin accepted')
                with repo.service.db.read() as conn:
                    assert list(conn.iterdump()) == applied_snapshot
            else:
                result = repo.service.amend_project(amendment, role='mutator')
                assert result['status'] == 'noop', result
                assert repo.service.db.revision() == before + 1
                assert repo.service.project_context()['orientation'][0]['payload']['anchors'] == [locator]
                source.write_text('changed local content\n')
                assert repo.service.project_context()['orientation'][0]['field_freshness']['objective']['status'] == 'stale'
            assert len(calls) == 2
        print('PASS', scenario)
    else:
        errors = {'denied': 'source_prerequisite_stale',
                  'raised': 'source_prerequisite_unavailable',
                  'incomplete': 'source_prerequisite_unavailable',
                  'repository_alias': 'source_prerequisite_unavailable',
                  'substituted_hash': 'source_prerequisite_stale',
                  'substituted_path': 'source_prerequisite_stale',
                  'substituted_repository': 'source_prerequisite_stale',
                  'fallback_stale': 'source_prerequisite_stale'}
        if scenario in errors:
            try:
                repo.service.publish_project_context(request, claim_token=token)
            except TodoError as exc:
                assert exc.code == errors[scenario], exc.code
            else:
                raise AssertionError('denied source published')
            assert repo.service.db.revision() == before
            with repo.service.db.read() as conn:
                assert list(conn.iterdump()) == snapshot
        else:
            result = repo.service.publish_project_context(request, claim_token=token)
            assert result['status'] == 'published'
            assert repo.service.db.revision() == before + 1
            with repo.service.db.read() as conn:
                row = conn.execute("SELECT * FROM project_declarations WHERE id='A:local-fence'").fetchone()
                assert row['task_id'] == 'A' and row['origin'] == 'task_scoped'
                assert json.loads(row['payload_json']) == request['payload']
        assert len(calls) == (0 if scenario.startswith('fallback') else 1)
        print('PASS', scenario)
finally:
    repo.close()
'''


@pytest.mark.parametrize('scenario', [
    'denied', 'raised', 'incomplete', 'uuid', 'name', 'registered',
    'repository_alias', 'substituted_hash', 'substituted_path',
    'substituted_repository', 'fallback', 'fallback_stale',
    'orientation', 'orientation_uuid', 'orientation_pinned',
    'orientation_bad_uuid', 'orientation_bad_revision',
])
def test_local_publication_source_fence(scenario):
    env = os.environ.copy()
    # Isolate only the disposable source fixture; preserve live deployment pins.
    for key in ('CODING_WORKFLOW_RUNTIME_FINGERPRINT', 'TODO_ORCHESTRATOR_STATE_DIR',
                'PROJECT_CONTROL_RELEASE_MANIFEST', 'PROJECT_CONTROL_RELEASE_DIGEST'):
        env.pop(key, None)
    env.update(
        PROJECT_CONTROL_SKILLS_ROOT=str(SKILLS),
        PYTHONPATH=os.pathsep.join([str(ROOT / 'src'), str(ROOT / 'tests/todo')]),
        AS1_SCENARIO=scenario, PYTHONDONTWRITEBYTECODE='1',
        AS1_SOURCE_HASHES=json.dumps({
            name: hashlib.sha256((TODO_PACKAGE / name).read_bytes()).hexdigest()
            for name in ('service.py', 'plan.py', 'project_amendments.py')
        }, sort_keys=True),
    )
    process = subprocess.run([sys.executable, '-c', SCRIPT], env=env,
                             text=True, capture_output=True)
    assert process.returncode == 0, process.stdout + process.stderr
