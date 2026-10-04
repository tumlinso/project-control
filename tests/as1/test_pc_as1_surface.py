"""AS1 public adapters exercise real composed services with CPU scripted ports."""
import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from project_control.app import create_mcp
from project_control.as1_context import ContextHost
from project_control.as1_surface import QUALIFIED_OBSERVER_RUNTIME_SHA256
from project_control.config import ProjectControlConfig
from project_control.profiles import enumerate_tool_schemas, validate_profile_registration
from project_control.workflow_tools import register_workflow_tools

BASE = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((BASE / 'planning/adaptive-surface-v1/contracts/surface.json').read_text())


def run(value):
    return asyncio.run(value)


@pytest.fixture
def servers(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    import project_control.as1_surface as module
    assert Path(module.__file__).resolve() == BASE / 'src/project_control/as1_surface.py'
    assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == hashlib.sha256((BASE / 'src/project_control/as1_surface.py').read_bytes()).hexdigest()
    made = []
    def make(profile='observer', **kwargs):
        with patch('project_control.app.todo_read_port_factory', return_value=None):
            server = create_mcp(kwargs.pop("config", ProjectControlConfig()), profile=profile,
                state_directory=tmp_path / str(len(made)), **kwargs)
        made.append(server)
        return server
    yield make
    for server in made:
        server._project_control_surface.close()


@pytest.mark.as1_case('API-01')
def test_exact_profile_matrix_and_dispatch_guard(servers):
    for profile, contract in CONTRACT['profiles'].items():
        server = servers(profile)
        schemas = run(enumerate_tool_schemas(server))
        assert set(schemas) == set(contract['tools'])
        run(validate_profile_registration(server))
        for forbidden in set(CONTRACT['removed_default_names']) | (set(CONTRACT['profiles']['mutator']['tools']) - set(schemas)):
            with pytest.raises(ToolError):
                run(server.call_tool(forbidden, {}))
    assert run(enumerate_tool_schemas(servers('codex'))) == run(enumerate_tool_schemas(servers('coder')))
    with pytest.raises(ValueError, match='startup_host'):
        servers('observer', host=ContextHost('mutator', 'alice', frozenset()))


@pytest.mark.as1_case('API-02')
def test_observer_only_detail_schema_and_dispatch(servers):
    for profile in CONTRACT['profiles']:
        server = servers(profile)
        schemas = run(enumerate_tool_schemas(server))
        for name, schema in schemas.items():
            detail = schema.get('properties', {}).get('detail')
            if detail:
                assert detail['default'] == 'compact'
                assert ('extended' in detail['enum']) == (profile == 'observer')
        if profile != 'observer':
            assert not {'read', 'skill'} & schemas.keys()
            with pytest.raises(ToolError, match='detail_not_permitted'):
                run(server.call_tool('overview', {'detail': 'extended'}))
        assert 'No overview is automatic.' in server.instructions
        result = server._project_control_surface.information.call('overview')
        assert result['status'] == 'ok' and result['packet']


@pytest.mark.as1_case('API-03')
def test_inactive_delegation_preserved_and_fenced(servers):
    class Protocol:
        def delegate_task(self, **args):
            raise AssertionError('inactive dispatch reached authority')
        def collect_delegation(self, **args):
            raise AssertionError('inactive dispatch reached authority')
    protocol = Protocol()
    for profile in ('coder', 'mutator'):
        server = servers(profile)
        register_workflow_tools(server, protocol=protocol)
        names = run(enumerate_tool_schemas(server))
        for name in ('delegate_task', 'collect_delegation'):
            assert name not in names
            with pytest.raises(ToolError, match='temporarily_inactive'):
                run(server.call_tool(name, {'profile': 'mutator'}))
            assert server.feature_metadata['temporarily_inactive'][name]['preserve_implementation']
            assert 'explicit operator decision' in server.feature_metadata['temporarily_inactive'][name]['reenable']
    assert callable(protocol.delegate_task) and callable(protocol.collect_delegation)


@pytest.mark.as1_case('API-04')
def test_composed_producers_lazy_startup_packets_and_native_worker(servers):
    from project_control.as1_context import InformationService
    from project_control.as1_control import ControlService
    from project_control.as1_jobs import JobService, TrustedObserverFactory
    from project_control.as1_skill import SkillObserverFactory, SkillService
    from project_control.as1_trace import TraceService
    from project_control.cli import _doctor
    class Backend:
        def run_observer_turn(self, request):
            raise AssertionError('startup invoked inference')
    with patch('project_control.as1_surface.TraceService._build', side_effect=AssertionError('startup scanned corpus')):
        server = servers(observer_backend=Backend())
    c = server._project_control_surface
    assert isinstance(c.information, InformationService) and isinstance(c.control, ControlService)
    assert isinstance(c.jobs, JobService) and isinstance(c.skills, SkillService) and isinstance(c.trace, TraceService)
    assert c.control.information_service is c.information and c.skills.jobs is c.jobs
    assert isinstance(c.jobs.worker_factory, SkillObserverFactory)
    assert isinstance(c.jobs.worker_factory.trusted, TrustedObserverFactory)
    assert c.jobs.worker_factory.trusted.digest == QUALIFIED_OBSERVER_RUNTIME_SHA256
    assert c.jobs.health()['dispatcher'] == 'stopped'
    worker = c.jobs.worker_factory.trusted
    assert worker.path.is_file()
    assert c.trace._repos == {} and c.trace._generations == {}
    result = run(server.call_tool('overview', {}))
    # FastMCP returns content plus structured result when structured_output is on.
    value = result[1] if isinstance(result, tuple) else result
    assert value['status'] == 'ok' and value['sources'] == []
    assert c.store.lookup(value['packet'], access_scope=c.host.scope(None)).status == 'ok'
    catalog = c.skills.catalog(access_scope=c.host.scope(None))
    assert catalog
    c.start()
    assert c.jobs.health()['dispatcher'] == 'running'
    assert c.close()
    bad = servers(observer_runtime_sha256='0' * 64)._project_control_surface
    assert bad.worker_unavailable == 'ValueError' and bad.jobs.worker_factory is None
    assert bad.jobs.submit(question='bounded question', access_scope=bad.host.scope(None))['reason'] == 'durable_processing_unavailable'
    with patch('project_control.cli.load_config', return_value=ProjectControlConfig()):
        _, diagnostic = _doctor(tunnel=False)
    assert diagnostic['surface']['profiles']['observer'] == list(CONTRACT['profiles']['observer']['tools'])
    assert 'internal_command_sandbox' in diagnostic


from test_pc_as1_context import world


@pytest.mark.as1_case('API-04')
def test_real_public_reads_exact_search_discovery_scope_and_trace(servers, world):
    root, snapshot, store, existing = world
    with patch('project_control.app.Runtime.snapshot', return_value=snapshot):
        server = servers(config=existing.config)
        c = server._project_control_surface
        c.information.semantic_provider = existing.semantic_provider
        c.trace.semantic = existing.semantic_provider
        def call(name, args):
            value = run(server.call_tool(name, {'project': 'demo', 'detail': 'extended', **args}))
            return value[1] if isinstance(value, tuple) else value
        exact = call('search', {'query': {'kind': 'task', 'target': 'T1'}})
        assert exact['data']['resolution']['status'] == 'resolved'
        with patch.object(c.information, '_discover', side_effect=AssertionError('exact invoked discovery')):
            missing = call('search', {'query': {'kind': 'task', 'target': 'MISSING'}})
            assert missing['status'] == 'partial'
        scoped = call('search', {'query': 'def', 'scope': {'repository': 'source', 'path': 'module.py'}})
        assert scoped['data']['scope']['path'] == 'module.py'
        assert all(row['path'] == 'module.py' for group in scoped['data']['source'] for row in group['live_fallback'])
        files = call('read', {'paths': ['module.py'], 'repository': 'source'})
        assert files['sources'][0]['content_sha256'] == hashlib.sha256((root / 'module.py').read_bytes()).hexdigest()
        assert call('frontier', {})['packet']
        assert call('evidence', {'subject': 'T1'})['packet']
        assert call('history', {'subject': 'T1', 'from_revision': 9})['packet']
        assert call('impact', {'targets': [{'repository': 'source', 'path': 'module.py'}]})['packet']
        assert c.trace._repos


@pytest.mark.as1_case('API-04')
def test_worker_command_roots_follow_durable_admission(servers, tmp_path):
    from types import SimpleNamespace
    from project_control.config import WorkspaceConfig, RepositoryConfig
    a = tmp_path / 'project_a'; b = tmp_path / 'project_b'; a.mkdir(); b.mkdir()
    config = ProjectControlConfig(workspaces={name: WorkspaceConfig(repositories={'source': RepositoryConfig(root=root)}) for name, root in [('a', a), ('b', b)]})
    c = servers(config=config)._project_control_surface
    trusted = c.jobs.worker_factory.trusted
    job = SimpleNamespace(scope=c.host.scope('a'), mode='investigate', job_id='job_fixture', attempt=1)
    worker = trusted(c.jobs, job)
    assert worker.command.roots[0] == a.resolve()
    assert worker.command.allows(a / 'source.py')
    assert not worker.command.allows(b / 'source.py')
    job.mode = 'skill'
    worker = trusted(c.jobs, job)
    assert worker.command.roots == (trusted.root,)
    assert not worker.command.allows(a / 'source.py')
    assert worker.command.allows(trusted.root / 'local-coding-worker/SKILL.md')
    job.scope['principal'] = 'forged'
    with pytest.raises(PermissionError):
        trusted(c.jobs, job)


@pytest.mark.as1_case('API-04')
def test_public_jobs_use_actual_installed_worker_and_shared_service(servers):
    import time
    class ScriptedBackend:
        def __init__(self):
            self.turns = 0; self.closed = []; self.replayed_packet = None
        def open_sessions(self, count, **policy):
            assert count == 1 and policy['compute_profile'] == 'narrow'
            return {'status': 'available', 'session_ids': ['scripted-session']}
        def close_session(self, session):
            self.closed.append(session)
        def run_observer_turn(self, request):
            self.turns += 1
            messages = request['messages']
            user_payloads = [json.loads(message['content']) for message in messages if message['role'] == 'user']
            assert sum(payload.get('question') == 'Inspect registered catalog.' for payload in user_payloads) == 1
            if self.turns == 1:
                assert len(messages) == 2
                turn = {'tool': 'overview', 'arguments': {}}
            else:
                replayed = [json.loads(messages[index + 1]['content'])
                    for index, message in enumerate(messages[:-1])
                    if message['role'] == 'assistant'
                    and json.loads(message['content']) == {'tool': 'overview', 'arguments': {}}
                    and messages[index + 1]['role'] == 'user']
                assert len(replayed) == 1
                self.replayed_packet = replayed[0]
                assert self.replayed_packet['data']['projects'] == []
                assert self.replayed_packet['packet_id']
                turn = {'answer': 'Registered catalog observed.', 'findings': [
                    {'text': 'The shared catalog was observed.', 'evidence_packets': [self.replayed_packet['packet_id']]}]}
            return {'status': 'available', 'text': json.dumps(turn)}
    backend = ScriptedBackend()
    server = servers(observer_backend=backend)
    c = server._project_control_surface
    c.start()
    value = run(server.call_tool('investigate', {'question': 'Inspect registered catalog.', 'request_id': 'surface-scripted-job'}))
    admitted = value[1] if isinstance(value, tuple) else value
    assert admitted['accepted'] and admitted['job_id']
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = run(server.call_tool('investigate', {'job_id': admitted['job_id']}))
        polled = value[1] if isinstance(value, tuple) else value
        if polled['job']['status'] in {'completed', 'partial', 'failed'}:
            break
        time.sleep(.01)
    assert polled['job']['status'] == 'completed', polled
    assert polled['observations'][0]['data']['projects'] == []
    assert polled['observations'][0]['packet_id'] == backend.replayed_packet['packet_id']
    assert polled['job']['findings'][0]['evidence_packets'] == [backend.replayed_packet['packet_id']]
    assert backend.turns == 2
    deadline = time.monotonic() + 2
    while not backend.closed and time.monotonic() < deadline:
        time.sleep(.01)
    assert backend.closed == ['scripted-session']
    assert c.jobs.lookup(admitted['job_id'], access_scope={**c.host.scope(None), 'principal': 'forged'})['status'] == 'forbidden'


@pytest.mark.as1_case('API-04')
@pytest.mark.parametrize('scenario', [
    'skill_use', 'finding', 'candidate_relation', 'wrong_role', 'wrong_repository',
    'wrong_task', 'stale_source', 'symlink_source', 'out_of_scope', 'forged_handle',
    'generic_context',
])
def test_public_native_capability_publication_consumer(scenario):
    """A child selects paired source without changing deployment pins in the gate."""
    import os
    import subprocess
    import sys
    from project_control.runtime_identity import package_fingerprint
    skills = Path('/home/tumlinson/.agents/skills')
    native = skills / 'todo-orchestrator'
    files = [BASE / 'src/project_control' / name for name in ('app.py', 'as1_surface.py', 'as1_control.py', 'workflow_tools.py')]
    files += [native / 'todo_orchestrator' / name for name in ('project_amendments.py', 'workflow/protocol.py', 'workflow/service.py', 'workflow/capabilities.py', 'workflow/roles.py')]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    env = os.environ.copy()
    for key in ('PROJECT_CONTROL_RELEASE_MANIFEST', 'PROJECT_CONTROL_RELEASE_DIGEST',
                'PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT', 'CODING_WORKFLOW_RUNTIME_FINGERPRINT',
                'CODING_WORKFLOW_SKILLS_ROOT', 'TODO_ORCHESTRATOR_STATE_DIR', 'TODO_ORCHESTRATOR_READ_ONLY'):
        env.pop(key, None)
    env.update(PROJECT_CONTROL_SKILLS_ROOT=str(skills),
        PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT=package_fingerprint(native / 'todo_orchestrator'),
        PYTHONPATH=os.pathsep.join([str(BASE / 'src'), str(native), str(native / 'tests')]),
        AS1_SURFACE_SOURCE_HASHES=json.dumps(hashes), AS1_SURFACE_SCENARIO=scenario,
        PYTHONDONTWRITEBYTECODE='1')
    script = r'''
import asyncio, hashlib, json, os
from pathlib import Path
from v2_helpers import V2Repo, base_plan, safe_task
from project_control.app import create_mcp
from project_control.config import ProjectControlConfig, WorkspaceConfig, RepositoryConfig
from project_control.workflow_binding import workflow_protocol
from todo_orchestrator.workflow.capabilities import default_first_class_operations
import project_control.app, project_control.as1_surface, project_control.as1_control, project_control.workflow_tools
import todo_orchestrator.project_amendments, todo_orchestrator.workflow.protocol, todo_orchestrator.workflow.service, todo_orchestrator.workflow.capabilities, todo_orchestrator.workflow.roles
modules = (project_control.app, project_control.as1_surface, project_control.as1_control, project_control.workflow_tools,
    todo_orchestrator.project_amendments, todo_orchestrator.workflow.protocol, todo_orchestrator.workflow.service, todo_orchestrator.workflow.capabilities, todo_orchestrator.workflow.roles)
assert {str(Path(m.__file__).resolve()):hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules} == json.loads(os.environ['AS1_SURFACE_SOURCE_HASHES'])
scenario = os.environ['AS1_SURFACE_SCENARIO']
repo = V2Repo()
server = None
try:
    os.environ['XDG_STATE_HOME'] = str(repo.root / 'private-state')
    repo.apply(base_plan([safe_task('A', 'src/a')]))
    source = repo.root / 'src/a/contract.txt'; source.parent.mkdir(parents=True); source.write_text('exact fixture source\n')
    config = ProjectControlConfig(skills_root=Path(os.environ['PROJECT_CONTROL_SKILLS_ROOT']),
        workspaces={'demo':WorkspaceConfig(authority_repository='source',repositories={'source':RepositoryConfig(root=repo.root)})})
    server = create_mcp(config, profile='coder', state_directory=repo.root / 'private-state/as1')
    def call(name, arguments):
        result = asyncio.run(server.call_tool(name, arguments))
        return result[1] if isinstance(result, tuple) else result
    claimed = call('next_task', {'repo_root':str(repo.root),'task_id':'A'})
    handle = claimed['workflow_handle']
    anchor = {'project':repo.service.project['project_uuid'], 'repository':str(repo.root),
        'path':'src/a/contract.txt', 'content_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    kind = scenario if scenario in ('skill_use','candidate_relation') else 'finding'
    payload = {'id':'frontend-fact', 'anchors':[anchor]}
    if kind == 'skill_use': payload.update(skill='todo-orchestrator',reason='native source contract',status='applied')
    body = {'kind':kind,'payload':payload}
    if scenario == 'wrong_task': body['task_id'] = 'OTHER'
    if scenario == 'stale_source': anchor['content_sha256'] = '0' * 64
    if scenario == 'symlink_source':
        link = source.with_name('link.txt'); link.symlink_to(source); anchor['path'] = 'src/a/link.txt'
    if scenario == 'out_of_scope':
        outside = repo.root / 'elsewhere.txt'; outside.write_bytes(source.read_bytes()); anchor['path']='elsewhere.txt'
    if scenario == 'forged_handle': handle = 'wfc_forged'
    if scenario == 'wrong_repository':
        server._project_control_surface.control.config.workspaces['demo'].repositories['source'].root = repo.root / 'unregistered'
    if scenario in ('wrong_role', 'generic_context'):
        role = 'validator' if scenario == 'wrong_role' else 'coordinator'
        def alter(conn, revision):
            conn.execute('UPDATE workflow_lanes SET role=?',(role,))
            conn.execute('UPDATE workflow_capabilities SET role=?,allowed_operations_json=?',
                (role,json.dumps(sorted(default_first_class_operations(role)))))
        repo.service.db.mutate(actor_session_id=None, entity_type='fixture', entity_id=None, event_type='fixture', payload={}, operation=alter)
    before = repo.service.db.revision()
    with repo.service.db.read() as conn: before_dump = list(conn.iterdump())
    if scenario == 'generic_context':
        result = call('coordinate_task', {'workflow_handle':handle,'action':'publish_context',
            'payload':{'content':{'summary':'Native generic context remains separate'},'anchors':[{'kind':'path','value':'src/a/contract.txt'}],'series_key':'generic-contract'}})
        assert 'context_note' in result, result
        with repo.service.db.read() as conn:
            assert conn.execute('SELECT COUNT(*) FROM project_declarations').fetchone()[0] == 0
        assert repo.service.db.revision() == before + 1
    else:
        result = call('coordinate_task', {'workflow_handle':handle,'action':'publish_context','payload':body})
        if scenario in ('skill_use','finding','candidate_relation'):
            assert result['status'] == 'published' and result['id'] == 'A:frontend-fact', result
            assert repo.service.db.revision() == before + 1
            assert call('coordinate_task', {'workflow_handle':handle,'action':'publish_context','payload':body})['status'] == 'noop'
            context = server._project_control_surface.control.project_context('demo')
            records = [row for row in context['skill_uses' if kind == 'skill_use' else 'declarations'] if row['id'] == 'A:frontend-fact']
            assert len(records) == 1 and records[0]['kind'] == kind, context
            assert records[0]['payload'] == payload
            if kind == 'candidate_relation': assert records[0]['origin'] == 'candidate'
        else:
            assert result['status'] in ('attention_required','unavailable'), result
            assert repo.service.db.revision() == before, result
            with repo.service.db.read() as conn:
                assert list(conn.iterdump()) == before_dump
                assert conn.execute('SELECT COUNT(*) FROM project_declarations').fetchone()[0] == 0
    assert 'claim_token' not in json.dumps(result) and 'session_token' not in json.dumps(result)
    print(json.dumps({'scenario':scenario,'status':result['status'],'source_hashes':json.loads(os.environ['AS1_SURFACE_SOURCE_HASHES'])}))
finally:
    if server: server._project_control_surface.close()
    repo.close()
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=BASE, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
