"""Native opaque-handle semantic publication in source-bound disposable authorities."""
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
from todo_orchestrator.models import TodoError
from todo_orchestrator.runtime_identity import bind_canonical_runtime
from todo_orchestrator.workflow.protocol import WorkflowProtocol
from todo_orchestrator.workflow.service import WorkflowKernel
from todo_orchestrator.workflow.capabilities import WorkflowCapabilityLocator, WorkflowCapabilityStore
from todo_orchestrator import project_amendments
from todo_orchestrator.workflow import protocol, service, capabilities, roles
identity = bind_canonical_runtime()
modules = (project_amendments, protocol, service, capabilities, roles)
actual = {str(Path(m.__file__).resolve().relative_to(identity.package_root)): hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules}
assert actual == json.loads(os.environ['AS1_SOURCE_HASHES'])
repo = V2Repo()
try:
    repo.apply(base_plan([safe_task('A', 'src/a')]))
    source = repo.root / 'src/a/contract.txt'
    source.parent.mkdir(parents=True)
    source.write_text('exact source\n')
    locator = WorkflowCapabilityLocator(repo.root / 'locators')
    workflow = WorkflowProtocol(WorkflowKernel(locator=locator), locator)
    handle = workflow.next_task(repo_root=str(repo.root), task_id='A')['workflow_handle']
    cap = locator.resolve(handle, required_operation='coordinate:publish_project_context', expected_class='first_class')
    anchor = {'project': repo.service.project['project_uuid'], 'repository': str(repo.root), 'path': 'src/a/contract.txt', 'content_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    scenario = os.environ['AS1_SCENARIO']
    assert 'semantic_variant' not in workflow.inspect_task(workflow_handle=handle, kind='task')['action_policy']['coordinate']['publish_context']
    assert workflow.inspect_task(workflow_handle=handle, kind='task')['action_policy']['coordinate']['publish_context']['required'] == ['kind', 'payload']
    kind = 'skill_use' if scenario == 'skill_use' else 'candidate_relation' if scenario == 'candidate_relation' else 'finding'
    payload = {'id': 'fact', 'anchors': [anchor]}
    if kind == 'skill_use': payload.update(skill='todo-orchestrator', reason='native contract', status='applied')
    kwargs = {'kind': kind, 'payload': payload, 'expected_repository_root': repo.root}
    calls = []
    def verifier(a):
        calls.append(dict(a))
        if scenario == 'source_denied': raise TodoError('source_prerequisite_stale', 'denied')
        if scenario == 'source_incomplete': return None
        if scenario == 'source_changed' and len(calls) == 1: source.write_text('changed\n')
        if scenario == 'source_changed_inside' and len(calls) == 2: source.write_text('changed\n')
        if scenario == 'symlink_inside' and len(calls) == 2:
            replacement = repo.root / 'src/a/replacement'; replacement.write_bytes(source.read_bytes())
            source.unlink(); source.symlink_to(replacement)
        if scenario == 'parent_symlink_inside' and len(calls) == 2:
            original = source.parent; moved = original.with_name('moved'); original.rename(moved); original.symlink_to(moved, target_is_directory=True)
        if scenario == 'revoke_between' and len(calls) == 1:
            WorkflowCapabilityStore(repo.service.db).revoke(handle, actor_session_id=None)
        return {**a, 'project_uuid': repo.service.project['project_uuid'], 'revision': repo.service.db.revision()}
    kwargs['source_verifier'] = verifier
    if scenario == 'forged': handle = 'wfc_forged'
    if scenario == 'unsupported': kwargs['kind'] = 'identity'
    if scenario == 'task': kwargs['task_id'] = 'B'
    if scenario == 'repository': kwargs['expected_repository_root'] = repo.root / 'other'
    if scenario == 'foreign_project': anchor['project'] = 'foreign'
    if scenario == 'foreign_repository': anchor['repository'] = '/foreign'
    if scenario == 'out_of_scope':
        outside = repo.root / 'elsewhere.txt'; outside.write_text('exact source\n'); anchor['path'] = 'elsewhere.txt'
    if scenario == 'stale_hash': anchor['content_sha256'] = '0' * 64
    if scenario == 'symlink':
        link = repo.root / 'src/a/link'; link.symlink_to(source); anchor['path'] = 'src/a/link'
    if scenario in ('revoked', 'expired_claim', 'dispatch', 'session', 'wrong_role', 'read_scope', 'wrong_owner', 'specialist', 'lane_closed'):
        def alter(conn, revision):
            if scenario == 'revoked': conn.execute("UPDATE workflow_capabilities SET state='revoked'")
            if scenario == 'expired_claim': conn.execute("UPDATE claims SET expires_at='2000-01-01T00:00:00Z'")
            if scenario == 'dispatch': conn.execute("UPDATE workflow_dispatches SET state='finished'")
            if scenario == 'session': conn.execute("UPDATE sessions SET state='closed'")
            if scenario == 'wrong_role':
                conn.execute("UPDATE workflow_lanes SET role='validator'")
                conn.execute("UPDATE workflow_capabilities SET role='validator'")
            if scenario == 'specialist':
                conn.execute("UPDATE workflow_lanes SET role='specialist'")
                conn.execute("UPDATE workflow_capabilities SET role='specialist'")
            if scenario == 'lane_closed': conn.execute("UPDATE workflow_lanes SET state='closed'")
            if scenario == 'wrong_owner': conn.execute("UPDATE claims SET owner_system='foreign'")
            if scenario == 'read_scope': conn.execute("UPDATE ownership_scopes SET mode='read'")
        repo.service.db.mutate(actor_session_id=None, entity_type='fixture', entity_id=None, event_type='fixture', payload={}, operation=alter)
    before = repo.service.db.revision()
    with repo.service.db.read() as conn: snapshot = list(conn.iterdump())
    errors = {'forged_capability':'capability_operation_forbidden', 'lane_closed':'workflow_lane_closed', 'revoke_between':'invalid_workflow_capability', 'wrong_owner':'publication_scope_denied', 'forged':'invalid_workflow_capability', 'revoked':'invalid_workflow_capability', 'expired_claim':'capability_claim_inactive', 'dispatch':'capability_dispatch_inactive', 'session':'capability_session_inactive', 'wrong_role':'workflow_role_forbidden', 'task':'publication_scope_denied', 'repository':'repository_identity_mismatch', 'foreign_project':'publication_scope_denied', 'foreign_repository':'publication_scope_denied', 'out_of_scope':'publication_scope_denied', 'read_scope':'publication_scope_denied', 'unsupported':'publication_scope_denied', 'source_denied':'source_prerequisite_stale', 'source_incomplete':'source_prerequisite_unavailable', 'parent_symlink_inside':'source_prerequisite_stale', 'source_changed_inside':'source_prerequisite_stale', 'symlink_inside':'source_prerequisite_stale', 'source_changed':'source_prerequisite_stale', 'symlink':'source_prerequisite_stale', 'stale_hash':'source_prerequisite_stale'}
    if scenario in errors:
        try:
            if scenario == 'forged_capability':
                from dataclasses import replace
                forged = replace(cap, lineage=replace(cap.lineage, role='coordinator'))
                workflow.port._publish_project_context(forged, workflow_handle=handle, **kwargs)
            else: workflow.publish_project_context(handle, **kwargs)
        except TodoError as exc: assert exc.code == errors[scenario], (exc.code, errors[scenario])
        else: raise AssertionError('denied publication succeeded')
        assert repo.service.db.revision() == before + (1 if scenario == 'revoke_between' else 0)
        with repo.service.db.read() as conn:
            if scenario != 'revoke_between': assert list(conn.iterdump()) == snapshot
            assert conn.execute('SELECT COUNT(*) FROM project_declarations').fetchone()[0] == 0
    else:
        if scenario == 'native_coordinate':
            result = workflow.coordinate_task(workflow_handle=handle, action='publish_context', payload={'kind':kind, 'payload':payload})
            assert result['status'] == 'claimed' and result['id'] == 'A:fact'
        else:
            result = workflow.publish_project_context(handle, **kwargs)
            assert result['status'] == 'published'
            assert len(calls) == 2
        assert workflow.publish_project_context(handle, **kwargs)['status'] == 'noop'
        assert repo.service.db.revision() == before + 1
        context = repo.service.project_context()
        with repo.service.db.read() as conn:
            rows = conn.execute('SELECT * FROM project_declarations').fetchall()
            assert len(rows) == 1 and rows[0]['task_id'] == 'A'
            assert json.loads(rows[0]['payload_json']) == payload
        assert 'fact' in json.dumps(context)
    print(json.dumps({'scenario':scenario,'source':identity.public(),'hashes':actual}))
finally: repo.close()
'''


@pytest.mark.parametrize('scenario', [
    'success', 'skill_use', 'candidate_relation', 'forged', 'revoked',
    'expired_claim', 'dispatch', 'session', 'wrong_role', 'task', 'repository',
    'foreign_project', 'foreign_repository', 'out_of_scope', 'read_scope',
    'unsupported', 'source_denied', 'source_incomplete', 'source_changed',
    'symlink', 'stale_hash', 'wrong_owner', 'revoke_between', 'specialist', 'native_coordinate', 'source_changed_inside', 'symlink_inside', 'parent_symlink_inside', 'lane_closed', 'forged_capability',
])
def test_native_capability_publication(scenario):
    env = os.environ.copy()
    for key in ('CODING_WORKFLOW_RUNTIME_FINGERPRINT', 'TODO_ORCHESTRATOR_STATE_DIR',
                'PROJECT_CONTROL_RELEASE_MANIFEST', 'PROJECT_CONTROL_RELEASE_DIGEST'):
        env.pop(key, None)
    files = ['project_amendments.py', 'workflow/protocol.py', 'workflow/service.py',
             'workflow/capabilities.py', 'workflow/roles.py']
    env.update(PROJECT_CONTROL_SKILLS_ROOT=str(SKILLS),
               PYTHONPATH=os.pathsep.join([str(ROOT / 'src'), str(ROOT / 'tests/todo')]),
               AS1_SCENARIO=scenario, PYTHONDONTWRITEBYTECODE='1',
               AS1_SOURCE_HASHES=json.dumps({name:hashlib.sha256((TODO_PACKAGE / name).read_bytes()).hexdigest() for name in files}))
    result = subprocess.run([sys.executable, '-c', SCRIPT], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
