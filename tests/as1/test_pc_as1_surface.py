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
    assert worker.command.allows(a / 'source.py')
    assert not worker.command.allows(b / 'source.py')
    job.mode = 'skill'
    worker = trusted(c.jobs, job)
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
            self.turns = 0; self.closed = []
        def open_sessions(self, count, **policy):
            assert count == 1 and policy['compute_profile'] == 'narrow'
            return {'status': 'available', 'session_ids': ['scripted-session']}
        def close_session(self, session):
            self.closed.append(session)
        def run_observer_turn(self, request):
            self.turns += 1
            if self.turns == 1:
                turn = {'tool': 'overview', 'arguments': {}}
            else:
                observations = json.loads(request['messages'][1]['content'])['observations']
                turn = {'answer': 'Registered catalog observed.', 'findings': [
                    {'text': 'The shared catalog was observed.', 'evidence_packets': [observations[-1]['packet_id']]}]}
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
    assert backend.turns == 2
    deadline = time.monotonic() + 2
    while not backend.closed and time.monotonic() < deadline:
        time.sleep(.01)
    assert backend.closed == ['scripted-session']
    assert c.jobs.lookup(admitted['job_id'], access_scope={**c.host.scope(None), 'principal': 'forged'})['status'] == 'forbidden'
