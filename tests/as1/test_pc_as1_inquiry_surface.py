"""Public inquiry delivery and material dependency validation, without inference."""
import asyncio
import hashlib
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from project_control.as1_contracts import SourceLocator
from project_control.as1_surface import InquiryFreshness, public_inquiry
from project_control.as1_packets import SQLitePacketStore
from project_control.as1_skill import SkillService
from test_pc_as1_surface import servers, run


def test_registered_inquiry_targets_are_labeled_separately_from_installed_skills(servers, tmp_path):
    from project_control.config import ProjectControlConfig, WorkspaceConfig, RepositoryConfig
    project_root = tmp_path / 'project'; project_root.mkdir()
    other_root = tmp_path / 'other'; other_root.mkdir()
    config = ProjectControlConfig(workspaces={
        'p': WorkspaceConfig(repositories={'main': RepositoryConfig(root=project_root)}),
        'other': WorkspaceConfig(repositories={'main': RepositoryConfig(root=other_root)})})
    c = servers(config=config)._project_control_surface
    provider = c.jobs.inquiry_context_provider
    expected = [{'project': 'p', 'repository': 'main', 'root': str(project_root)}]
    original = provider(SimpleNamespace(scope=c.scope('p')))
    assert original['inquiry_repositories'] == expected
    assert original['installed_skill_roots'] == [str(c.skills.root)]
    assert str(project_root) not in original['installed_skill_roots']
    # Caller/profile provenance and caller hints do not change trusted targets.
    changed = {**c.scope('p'), 'principal': 'another-caller', 'profile': 'mutator',
               'root': '/untrusted', 'inquiry_repositories': [{'root': '/untrusted'}]}
    changed_context = provider(SimpleNamespace(scope=changed))
    assert changed_context['inquiry_repositories'] == original['inquiry_repositories']
    assert changed_context['installed_skill_roots'] == original['installed_skill_roots']
    assert changed_context['tool_argument_schemas']['overview']['properties']['detail']['enum'] == ['compact', 'standard']
    # Neither mutating returned labels nor later config mutation changes the
    # immutable admission configuration snapshot.
    original['inquiry_repositories'][0]['root'] = '/untrusted'
    config.workspaces['p'].repositories['main'].root = other_root
    assert provider(SimpleNamespace(scope=c.scope('p')))['inquiry_repositories'] == expected


def test_composed_broker_moves_to_v2_without_moving_packet_store(servers):
    c = servers()._project_control_surface
    assert c.jobs.path == c.store.directory / 'jobs-v2' / 'jobs.sqlite3'
    assert c.store.path == c.store.directory / 'packets.sqlite3'


def test_dispatch_passes_trusted_target_context_without_hint_override(tmp_path):
    from project_control.as1_jobs import JobService
    from test_pc_as1_jobs import wait
    store = SQLitePacketStore(tmp_path / 'packets')
    scope = {'principal': 'alice', 'profile': 'observer', 'project': 'p'}
    hint = store.create(tool='read', access_scope=scope, payload={
        'inquiry_repositories': [{'project': 'wrong', 'root': '/wrong'}]})
    captured = []
    context = {'inquiry_repositories': [{'project': 'p', 'repository': 'main',
                                        'root': str(tmp_path / 'project')}],
               'installed_skill_roots': [str(tmp_path / 'skills')]}
    def factory(service, job):
        class Worker:
            def run(self, request):
                captured.append(request)
                return {'status': 'completed', 'answer': 'registered project'}
        return Worker()
    service = JobService(tmp_path / 'jobs', packets=store, worker_factory=factory,
                         inquiry_context_provider=lambda job: context).start()
    try:
        admitted = service.submit(question='Explain this project', access_scope=scope, hints=[hint.alias])
        wait(lambda: bool(captured))
        assert captured[0]['inquiry_repositories'] == context['inquiry_repositories']
        assert captured[0]['installed_skill_roots'] == context['installed_skill_roots']
        assert captured[0]['hints']
    finally:
        service.shutdown()


@pytest.mark.parametrize('cite_feedback', [False, True])
@pytest.mark.parametrize('bad_tool', ['log', 'search'])
def test_invalid_internal_arguments_retain_feedback_then_answer_without_retry(servers, cite_feedback, bad_tool):
    import json
    class Backend:
        def __init__(self): self.turns = 0; self.feedback = []
        def open_sessions(self, count, **policy): return {'status': 'available', 'session_ids': ['cpu']}
        def close_session(self, session): return {'released': True}
        def run_observer_turn(self, request):
            self.turns += 1
            if self.turns == 1:
                system = request['messages'][0]['content']
                assert 'Tool argument schemas' in system and '"subject"' in system
                value = ({'tool': 'log', 'arguments': {'offset': 0}} if bad_tool == 'log' else
                         {'tool': 'search', 'arguments': {'query': {'kind': 'source', 'target': 'README.md'}}})
            elif self.turns == 2:
                value = {'tool': 'evidence', 'arguments': {}}
            elif self.turns == 3:
                value = {'tool': 'overview', 'arguments': {}}
            else:
                observations = [json.loads(m['content']) for m in request['messages'] if m['role'] == 'user']
                self.feedback = [p for p in observations if p.get('reason') == 'invalid_arguments']
                assert len(self.feedback) == 2
                assert all(p['validation_data']['is_source_evidence'] is False for p in self.feedback)
                evidence = next(p for p in observations if p.get('data', {}).get('projects') == [])
                if cite_feedback:
                    evidence = self.feedback[0]
                value = {'answer': 'Catalog observed.', 'findings': [
                    {'text': 'Registered catalog.', 'evidence_packets': [evidence['packet_id']]}]}
            return {'status': 'available', 'text': json.dumps(value)}
    backend = Backend()
    c = servers(observer_backend=backend)._project_control_surface
    c.start()
    value = c.jobs.inquire('Read the catalog', c.scope(None))
    if cite_feedback:
        assert value['status'] == 'unavailable', value
        # The worker uses the remaining semantic rounds to repair a finding
        # that cites non-evidence feedback, then exhausts the bounded budget.
        assert backend.turns == 6
        return
    assert value['status'] == 'completed', value
    assert value['job']['attempt'] == 1 and backend.turns == 4
    retained = c.jobs.lookup(value['job']['job_id'], access_scope=c.scope(None))['observations']
    assert len([p for p in retained if p.get('reason') == 'invalid_arguments']) == 2


def test_native_request_errors_become_feedback_but_output_contract_errors_escape(servers, monkeypatch):
    from pydantic import ValidationError
    from project_control.as1_jobs import InvalidToolArguments
    from project_control.as1_contracts import InformationPacket
    from project_control.models import InspectInput
    c = servers()._project_control_surface
    tools = c.jobs.worker_factory.trusted.tools
    def invalid_input(*args, **kwargs):
        return InspectInput.model_validate({'project': 'p', 'kind': 'source', 'target': 'README.md'})
    monkeypatch.setattr('project_control.as1_surface.InformationService.call', invalid_input)
    with pytest.raises(InvalidToolArguments, match='InspectInput'):
        tools('overview', {}, c.scope(None))
    def invalid_output(*args, **kwargs):
        return InformationPacket.model_validate({})
    monkeypatch.setattr('project_control.as1_surface.InformationService.call', invalid_output)
    with pytest.raises(ValidationError) as error:
        tools('overview', {}, c.scope(None))
    assert error.value.title == 'InformationPacket'


def test_argument_validation_preserves_authority_and_backend_failures(servers, monkeypatch):
    import pytest
    from project_control.as1_jobs import InvalidToolArguments
    c = servers()._project_control_surface
    tools = c.jobs.worker_factory.trusted.tools
    scope = c.scope(None)
    with pytest.raises(InvalidToolArguments): tools('evidence', {}, scope)
    with pytest.raises(PermissionError): tools('overview', {'project': 'outside'}, scope)
    def failed(*args, **kwargs): raise TypeError('true backend implementation failure')
    monkeypatch.setattr('project_control.as1_surface.InformationService.call', failed)
    with pytest.raises(TypeError, match='true backend'): tools('overview', {}, scope)


@pytest.mark.parametrize('project', [{'id': 'outside'}, ['outside']])
def test_malformed_project_argument_is_noncitable_feedback_without_dispatch(servers, monkeypatch, project):
    c = servers()._project_control_surface
    # Claiming this fixture must not depend on a live local supervisor.
    c.jobs.backend.central_status = lambda: {'status': 'available'}
    scope = c.scope(None)
    class Live:
        def is_alive(self): return True
    c.jobs._thread = Live()
    admitted = c.jobs.submit(question='Argument feedback fixture', access_scope=scope)
    job = c.jobs.claim()
    assert job.job_id == admitted['job_id']
    # Build the actual trusted tool bridge without executing its model loop.
    worker = c.jobs.worker_factory.trusted(c.jobs, job)
    dispatch = Mock(side_effect=AssertionError('invalid/unauthorized arguments dispatched'))
    monkeypatch.setattr('project_control.as1_surface.InformationService.call', dispatch)
    result = worker.tools('overview', {'project': project})
    assert result['status'] == 'denied' and result['reason'] == 'invalid_arguments'
    assert result['dispatched'] is False
    assert result['validation_data']['is_source_evidence'] is False
    assert result['packet_id']
    retained = c.jobs.lookup(job.job_id, access_scope=scope)['observations']
    assert any(p.get('packet_id') == result['packet_id'] for p in retained)
    with pytest.raises(PermissionError): worker.tools('overview', {'project': 'outside'})
    dispatch.assert_not_called()


def test_internal_argument_schemas_preserve_native_constraints_and_original_profile():
    import json
    from project_control.as1_surface import observer_tool_argument_schemas
    schema = observer_tool_argument_schemas('observer')
    assert set(schema) == {'overview', 'delta', 'frontier', 'search', 'evidence', 'impact', 'history', 'machine', 'log', 'command'}
    assert schema['evidence']['required'] == ['subject']
    assert schema['evidence']['properties']['subject']['minLength'] == 1
    assert schema['log']['additionalProperties'] is False and 'offset' not in schema['log']['properties']
    assert schema['overview']['properties']['detail']['enum'] == ['compact', 'standard', 'extended']
    assert observer_tool_argument_schemas('mutator')['overview']['properties']['detail']['enum'] == ['compact', 'standard']
    assert '$defs' in schema['search'] and '$ref' in json.dumps(schema['search'])
    kinds = schema['search']['$defs']['ObserverExactEntityQuery']['properties']['kind']['enum']
    assert 'path' in kinds and 'source' not in kinds
    assert {'packet', 'investigation', 'registration'} <= set(kinds)
    assert len(json.dumps(schema).encode()) < 12000


def test_actual_tools_schemas_and_recursive_projection(servers):
    tools = {tool.name: tool for tool in run(servers().list_tools())}
    assert len(tools) == 11
    for name, tool in tools.items():
        assert tool.annotations.readOnlyHint is True
        if name in {'investigate', 'skill'}:
            assert 'job_id' not in tool.inputSchema['properties']
            assert 'poll' not in tool.description and 'job_id' not in tool.description
    value = public_inquiry({'status': 'completed', 'answer': 'Supported answer',
        'result': {'job_id': 'private', 'attempt': 3, 'lease_until': 1,
                   'continuation': {'job_id': 'private', 'detail': 'extended'}},
        'findings': [{'text': 'Fact', 'evidence_packets': ['pkt_support'], 'queue_position': 1}]})
    assert value['answer'] == 'Supported answer'
    assert value['result'] == {'continuation': {'detail': 'extended'}}
    assert value['findings'] == [{'text': 'Fact', 'evidence_packets': ['pkt_support']}]


def test_terminal_answer_projection_keeps_supported_evidence(servers):
    server = servers(); c = server._project_control_surface; scope = c.scope(None)
    packet = c.store.create(tool='investigate', access_scope=scope,
        payload={'status': 'completed', 'answer': 'Verified source says X.', 'job_id': 'private',
                 'observations': [], 'findings': [{'text': 'X', 'evidence_packets': ['pkt_evidence']}]})
    c.jobs.inquire = Mock(return_value={'status': 'completed', 'job': {
        'job_id': 'private', 'result_packet': packet.packet_id, 'evidence_packets': ['pkt_evidence']},
        'observations': [{'attempt': 1}]})
    result = run(server.call_tool('investigate', {'question': 'literal  question\n'}))[1]
    assert result['answer'] == 'Verified source says X.'
    assert result['evidence_packets'] == ['pkt_evidence'] and result['packet_id'] == packet.packet_id
    assert not {'job', 'job_id', 'observations', 'attempt'} & result.keys()
    assert c.jobs.inquire.call_args.kwargs['question'] == 'literal  question\n'


def test_investigate_and_skill_leave_event_loop_responsive(servers):
    server = servers()
    c = server._project_control_surface
    active = []
    completed = []
    lock = threading.Lock()

    def blocked_inquiry(**kwargs):
        with lock:
            active.append(kwargs)
        time.sleep(.25)
        with lock:
            completed.append(kwargs)
        return {'status': 'thinking'}

    c.jobs.inquire = blocked_inquiry
    c.skills.inquire = blocked_inquiry

    async def exercise():
        started = time.monotonic()
        pending = asyncio.gather(
            server.call_tool('investigate', {'question': 'read only question'}),
            server.call_tool('skill', {'query': 'read only skill question'}),
        )
        await asyncio.sleep(.03)
        heartbeat = (time.monotonic() - started, len(active), len(completed))
        results = await pending
        return heartbeat, results

    heartbeat, results = asyncio.run(exercise())
    assert heartbeat[0] < .15
    assert heartbeat[1] >= 1 and heartbeat[2] == 0
    assert len(active) == len(completed) == 2
    assert all((result[1] if isinstance(result, tuple) else result)['status'] == 'thinking'
               for result in results)


def fixture_cache(tmp_path):
    root = tmp_path/'skills'; root.mkdir(); (root/'fixture').mkdir()
    store = SQLitePacketStore(tmp_path/'packets')
    jobs = SimpleNamespace(packets=store, worker_factory=SimpleNamespace(skills={
        'fixture': {'name': 'fixture', 'root': str(root/'fixture')}}))
    skills = SkillService(jobs, skills_root=root)
    composition = SimpleNamespace(store=store, jobs=jobs, skills=skills,
        host=SimpleNamespace(projects=frozenset()), control=SimpleNamespace())
    return root, store, jobs, InquiryFreshness(composition)


def test_dependency_sources_changed_deleted_unrelated_file_and_volatile_result(tmp_path):
    root, store, jobs, fresh = fixture_cache(tmp_path)
    source = root/'fixture/resource.md'; source.write_text('authoritative')
    scope = {'principal': 'alice', 'profile': 'observer'}
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    evidence = store.create(tool='read', payload={'data': 'authoritative'}, access_scope=scope,
        sources=[SourceLocator(project='skills', repository=str(root), path='fixture/resource.md', content_sha256=digest)])
    answer = store.create(tool='investigate', payload={'answer': 'authoritative'}, access_scope=scope,
        freshness={'volatile': True, 'max_age_seconds': 0})
    job = {'scope': scope, 'mode': 'investigate', 'hints': [], 'evidence_packets': [evidence.packet_id], 'result_packet': answer.packet_id}
    assert fresh(job)['fresh']
    (root/'unrelated.md').write_text('new file')
    assert fresh(job)['fresh']
    source.write_text('changed')
    assert not fresh(job)['fresh'] and fresh(job)['changed_sources']
    source.unlink()
    assert not fresh(job)['fresh']
    job['evidence_packets'] = []
    assert not fresh(job)['fresh']


def test_nested_authoritative_packet_and_missing_material(tmp_path):
    root, store, jobs, fresh = fixture_cache(tmp_path)
    source = root/'fixture/resource.md'; source.write_text('exact')
    scope = {'principal': 'alice', 'profile': 'observer'}
    actual = store.create(tool='read', payload={'data': 'exact'}, access_scope=scope,
        sources=[SourceLocator(project='skills', repository=str(root), path='fixture/resource.md',
                               content_sha256=hashlib.sha256(source.read_bytes()).hexdigest())])
    wrapper = store.create(tool='search', payload={'packet': actual.packet_id}, access_scope=scope,
                           freshness={'volatile': True, 'max_age_seconds': 0})
    answer = store.create(tool='investigate', payload={'answer': 'exact'}, access_scope=scope)
    job = {'scope': scope, 'mode': 'investigate', 'hints': [], 'evidence_packets': [wrapper.packet_id], 'result_packet': answer.packet_id}
    assert fresh(job)['fresh']
    job['evidence_packets'].append('pkt_missing')
    assert not fresh(job)['fresh']


@pytest.mark.parametrize('dependency', ['unchanged_cat', 'changed_cat', 'cited_volatile'])
def test_finding_dependencies_exclude_incidental_and_aggregate_observations(tmp_path, dependency):
    root, store, jobs, fresh = fixture_cache(tmp_path)
    now = [1000.0]; store.clock = lambda: now[0]
    source = root/'fixture/resource.md'; source.write_text('exact')
    scope = {'principal': 'alice', 'profile': 'observer'}
    cited = store.create(tool='command', access_scope=scope, payload={
        'status': 'completed', 'exit_code': 0, 'source_reads': [{'method': 'direct_cat',
            'path': str(source), 'content_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]})
    expired = store.create(tool='command', access_scope=scope, payload={'stdout': '/cwd'}, ttl_seconds=1)
    denied = store.create(tool='search', access_scope=scope, payload={'status': 'denied'})
    unavailable = store.create(tool='evidence', access_scope=scope, payload={'status': 'unavailable'})
    volatile = store.create(tool='command', access_scope=scope, payload={'stdout': 'coarse grep'},
                            freshness={'volatile': True, 'max_age_seconds': 0})
    if dependency == 'changed_cat':
        source.write_text('changed')
    if dependency == 'cited_volatile':
        cited = volatile
    findings = [{'text': 'Supported fact', 'evidence_packets': [cited.packet_id]}]
    # Derived answers aggregate all observations, including unrelated source
    # manifests and parents. Only their retained existence/access is material.
    answer = store.create(tool='investigate', access_scope=scope, payload={'answer': 'fact', 'findings': findings},
        parents=[expired.packet_id, denied.packet_id, unavailable.packet_id],
        sources=[SourceLocator(project='skills', repository=str(root), path='missing.md', content_sha256='a'*64)],
        freshness={'dependencies': {'semantic_revision:missing': 'b'*64}})
    now[0] += 2
    job = {'scope': scope, 'mode': 'investigate', 'findings': findings, 'hints': [expired.packet_id],
           'evidence_packets': [cited.packet_id, expired.packet_id, denied.packet_id, unavailable.packet_id,
                                volatile.packet_id], 'result_packet': answer.packet_id}
    result = fresh(job)
    assert result['fresh'] is (dependency == 'unchanged_cat')
    if dependency == 'changed_cat':
        assert {'path': str(source), 'reason': 'changed'} in result['changed_sources']
    if dependency == 'cited_volatile':
        assert any(item.get('reference') == volatile.packet_id for item in result['changed_sources'])
    # No explicit finding citations retain conservative legacy dependencies.
    job['findings'] = []
    assert not fresh(job)['fresh']


@pytest.mark.parametrize('result_status', ['missing', 'forbidden', 'expired'])
def test_finding_dependencies_still_require_accessible_result(tmp_path, result_status):
    root, store, jobs, fresh = fixture_cache(tmp_path)
    source = root/'fixture/resource.md'; source.write_text('exact')
    scope = {'principal': 'alice', 'profile': 'observer'}
    cited = store.create(tool='command', access_scope=scope, payload={
        'status': 'completed', 'exit_code': 0, 'source_reads': [{'method': 'direct_cat',
            'path': str(source), 'content_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]})
    answer = store.create(tool='investigate', payload={'answer': 'fact'},
        access_scope={**scope, 'project': 'other'} if result_status == 'forbidden' else scope,
        ttl_seconds=0 if result_status == 'expired' else None)
    job = {'scope': scope, 'mode': 'investigate', 'hints': [], 'evidence_packets': [cited.packet_id],
           'findings': [{'text': 'fact', 'evidence_packets': [cited.packet_id]}],
           'result_packet': 'pkt_missing' if result_status == 'missing' else answer.packet_id}
    result = fresh(job)
    assert not result['fresh']
    assert any(item.get('reason') == ('not_found' if result_status == 'missing' else result_status)
               for item in result['changed_sources'])


def test_verified_sed_line_window_revalidates_full_source_hash(tmp_path):
    root, store, jobs, fresh = fixture_cache(tmp_path)
    source = root/'fixture/resource.md'; source.write_text('first\nsecond\nthird\n')
    scope = {'principal': 'alice', 'profile': 'observer'}
    read = {'method': 'direct_sed_lines', 'path': str(source),
            'content_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'line_count': 3, 'line_ranges': [{'start': 2, 'end': 2}]}
    cited = store.create(tool='command', access_scope=scope, payload={
        'status': 'completed', 'exit_code': 0, 'truncated': False, 'timed_out': False,
        'source_reads': [read]})
    answer = store.create(tool='investigate', access_scope=scope, payload={'answer': 'second'})
    job = {'scope': scope, 'mode': 'investigate', 'hints': [],
           'evidence_packets': [cited.packet_id],
           'findings': [{'text': 'Second line', 'evidence_packets': [cited.packet_id]}],
           'result_packet': answer.packet_id}
    assert fresh(job)['fresh']
    source.write_text('first\nchanged\nthird\n')
    result = fresh(job)
    assert not result['fresh']
    assert {'path': str(source), 'reason': 'changed'} in result['changed_sources']


@pytest.mark.parametrize('metadata', [
    {'method': 'direct_sed_lines', 'line_count': 3, 'line_ranges': []},
    {'method': 'direct_sed_lines', 'line_count': 3, 'line_ranges': [{'start': 0, 'end': 2}]},
    {'method': 'unknown_filtered_read', 'line_count': 3, 'line_ranges': [{'start': 1, 'end': 2}]},
])
def test_invalid_or_unknown_sed_line_window_proofs_remain_unverified(tmp_path, metadata):
    root, store, jobs, fresh = fixture_cache(tmp_path)
    source = root/'fixture/resource.md'; source.write_text('first\nsecond\nthird\n')
    scope = {'principal': 'alice', 'profile': 'observer'}
    read = {**metadata, 'path': str(source),
            'content_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    cited = store.create(tool='command', access_scope=scope, payload={
        'status': 'completed', 'exit_code': 0, 'truncated': False, 'timed_out': False,
        'source_reads': [read]})
    answer = store.create(tool='investigate', access_scope=scope, payload={'answer': 'second'})
    job = {'scope': scope, 'mode': 'investigate', 'hints': [],
           'evidence_packets': [cited.packet_id],
           'findings': [{'text': 'Second line', 'evidence_packets': [cited.packet_id]}],
           'result_packet': answer.packet_id}
    result = fresh(job)
    assert not result['fresh']
    assert {'path': str(source), 'reason': 'unverified'} in result['changed_sources']


def test_skill_literal_identity_separate_execution_and_required_entry_proof(tmp_path):
    root, store, jobs, fresh = fixture_cache(tmp_path)
    jobs.inquire = Mock(return_value={'status': 'thinking'})
    scope = {'principal': 'alice', 'profile': 'observer'}
    service = SkillService(jobs, skills_root=root, discovery_skill='fixture')
    assert service.inquire(query='raw  query\n', access_scope=scope)['status'] == 'thinking'
    args = jobs.inquire.call_args.kwargs
    assert args['question'] == 'raw  query\n' and args['execution_question'].startswith('raw  query\n')
    assert args['mode'] == 'skill' and args['skill'] == 'fixture'
    (root/'fixture/SKILL.md').write_text('entry')
    path = root/'fixture/resource.md'; path.write_text('resource')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    packet = store.create(tool='command', access_scope=scope, payload={
        'status': 'completed', 'exit_code': 0, 'source_reads': [
            {'method': 'direct_cat', 'path': str(path), 'content_sha256': sha}]})
    result = store.create(tool='skill', access_scope=scope, payload={'skill_selection': {'selections': [
        {'skill': 'fixture', 'resource': 'resource.md', 'content_sha256': sha}]}})
    job = {'job_id': 'private', 'mode': 'skill', 'scope': scope, 'hints': [],
           'evidence_packets': [packet.packet_id], 'result_packet': result.packet_id}
    jobs.lookup = Mock(return_value={'observations': [{'packet_id': packet.packet_id}]})
    assert not fresh(job)['fresh']
    entry = root/'fixture/SKILL.md'
    entry.write_text('entry')
    window_entry = store.create(tool='command', access_scope=scope, payload={
        'status': 'completed', 'exit_code': 0, 'source_reads': [{'method': 'direct_sed_lines',
            'path': str(entry), 'content_sha256': hashlib.sha256(entry.read_bytes()).hexdigest(),
            'line_count': 1, 'line_ranges': [{'start': 1, 'end': 1}]}]})
    job['evidence_packets'].insert(0, window_entry.packet_id)
    jobs.lookup.return_value['observations'].insert(0, {'packet_id': window_entry.packet_id})
    assert not fresh(job)['fresh']
    entrypacket = store.create(tool='command', access_scope=scope, payload={
        'status': 'completed', 'exit_code': 0, 'source_reads': [{'method': 'direct_cat',
            'path': str(entry), 'content_sha256': hashlib.sha256(entry.read_bytes()).hexdigest()}]})
    job['evidence_packets'].insert(0, entrypacket.packet_id)
    jobs.lookup.return_value['observations'].insert(0, {'packet_id': entrypacket.packet_id})
    assert fresh(job)['fresh']
    job['findings'] = [{'text': 'Selected resource', 'evidence_packets': [packet.packet_id]}]
    assert fresh(job)['fresh']
    entry.write_text('changed entry')
    assert not fresh(job)['fresh']
    entry.write_text('entry')
    job['findings'][0]['evidence_packets'] = [entrypacket.packet_id]
    assert fresh(job)['fresh']
    path.write_text('changed resource')
    assert not fresh(job)['fresh']


def test_public_skill_terminal_projection_and_unavailable(servers):
    server = servers(); c = server._project_control_surface
    c.skills.inquire = Mock(return_value={'status': 'partial', 'job_id': 'private', 'attempt': 1,
        'excerpts': [{'content': 'exact source', 'verbatim': True}],
        'continuation': {'job_id': 'private', 'detail': 'extended'},
        'result': {'lease': 1, 'queue_position': 2}, 'unresolved': ['Missing dependency']})
    result = run(server.call_tool('skill', {'query': 'Use instructions'}))[1]
    assert result['status'] == 'partial' and result['excerpts'][0]['verbatim']
    assert c.skills.inquire.call_args.kwargs['detail'] == 'extended'
    assert result['continuation'] == {'detail': 'extended'}
    assert result['result'] == {} and 'job_id' not in result and 'attempt' not in result
    c.skills.inquire.return_value = {'status': 'stale_attempt', 'job_id': 'private'}
    result = run(server.call_tool('skill', {'query': 'Use instructions'}))[1]
    assert result == {'status': 'unavailable', 'reason': 'answer_unavailable'}


def test_catalog_scoped_project_source_identity_and_semantic_revision(servers, tmp_path):
    from project_control.config import ProjectControlConfig, WorkspaceConfig, RepositoryConfig
    source = tmp_path/'source'; source.mkdir(); (source/'facts.md').write_text('fact')
    config = ProjectControlConfig(workspaces={'project': WorkspaceConfig(repositories={
        'source': RepositoryConfig(root=source)})})
    c = servers(config=config)._project_control_surface
    scope = c.scope(None)
    c.information.snapshot_provider = Mock(return_value=SimpleNamespace(todo_revision=12))
    packet = c.store.create(tool='read', access_scope=scope, payload={'data': 'fact'},
        sources=[SourceLocator(project='project', repository='source', path='facts.md',
                               content_sha256=hashlib.sha256((source/'facts.md').read_bytes()).hexdigest())],
        freshness={'dependencies': {'semantic_revision:project': hashlib.sha256(b'12').hexdigest()}})
    result = c.store.create(tool='investigate', access_scope=scope, payload={'answer': 'fact'})
    job = {'mode': 'investigate', 'scope': scope, 'hints': [], 'evidence_packets': [packet.packet_id],
           'result_packet': result.packet_id}
    assert c.jobs.freshness_provider(job)['fresh']
    c.information.snapshot_provider.return_value.todo_revision = 13
    assert not c.jobs.freshness_provider(job)['fresh']


def test_two_host_global_dispatch_public_cache_and_packet_resolution(tmp_path):
    import json
    import time
    from unittest.mock import patch
    from project_control.app import create_mcp
    from project_control.as1_context import ContextHost
    from project_control.config import ProjectControlConfig
    class Backend:
        def __init__(self): self.turns = 0
        def open_sessions(self, count, **policy):
            return {'status': 'available', 'session_ids': ['cpu-fixture']}
        def close_session(self, session): return {'released': True}
        def run_observer_turn(self, request):
            self.turns += 1
            if self.turns == 1:
                value = {'tool': 'overview', 'arguments': {}}
            else:
                observations = [json.loads(m['content']) for m in request['messages'] if m['role'] == 'user']
                observation = next(p.get('retained_observation', p) for p in reversed(observations)
                                   if p.get('packet_id') or p.get('retained_observation', {}).get('packet_id'))
                value = {'answer': 'Shared registered catalog.', 'findings': [
                    {'text': 'Registered catalog observed.', 'evidence_packets': [observation['packet_id']]}]}
            return {'status': 'available', 'text': json.dumps(value)}
    backend = Backend()
    with patch('project_control.app.todo_read_port_factory', return_value=None):
        observer = create_mcp(ProjectControlConfig(), profile='observer', state_directory=tmp_path/'shared',
                              host=ContextHost('observer', 'alice', frozenset()), observer_backend=backend)
        coder = create_mcp(ProjectControlConfig(), profile='coder', state_directory=tmp_path/'shared',
                           host=ContextHost('coder', 'bob', frozenset()), observer_backend=backend)
    a, b = observer._project_control_surface, coder._project_control_surface
    class Live:
        def is_alive(self): return True
    a.jobs._thread = Live()  # fixture admission only; coder owns real CPU dispatcher
    a.jobs.freshness_provider = b.jobs.freshness_provider = lambda job: {'fresh': True}
    try:
        assert a.jobs.inquire('shared exact', a.scope(None), foreground_timeout=0)['status'] == 'thinking'
        b.start()
        end = time.monotonic() + 5
        while time.monotonic() < end:
            cached = b.jobs.inquire('shared exact', b.scope(None), foreground_timeout=0)
            answer = ({'status': cached['status'], 'answer': cached['job']['answer'],
                       'packet_id': cached['job']['result_packet'], 'evidence_packets': cached['job']['evidence_packets']}
                      if cached.get('job') else cached)
            if answer['status'] == 'completed': break
            time.sleep(.01)
        with b.jobs._db() as db:
            diagnostics = [json.loads(r[0]) for r in db.execute('SELECT record FROM jobs')]
        assert answer['status'] == 'completed' and answer['answer'] == 'Shared registered catalog.', json.dumps(diagnostics)
        assert backend.turns == 2
        repeated = run(observer.call_tool('investigate', {'question': 'shared exact'}))[1]
        assert repeated['packet_id'] == answer['packet_id'] and backend.turns == 2
        evidence = run(coder.call_tool('search', {'query': {'kind': 'packet', 'target': answer['evidence_packets'][0]}}))[1]
        assert evidence['data']['resolution'] == 'ok'
        assert evidence['data']['result']['data']['projects'] == []
        with b.jobs._db() as db:
            job_id = db.execute('SELECT id FROM jobs').fetchone()[0]
            assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
        assert b.jobs.lookup(job_id, access_scope=b.scope(None))['status'] == 'ok'
    finally:
        b.close(); a.close()


def test_shared_worker_can_reuse_cross_role_packet_and_job_without_hint(servers, monkeypatch):
    from project_control.as1_jobs import TrustedObserverFactory
    server = servers(); c = server._project_control_surface
    class Live:
        def is_alive(self): return True
    c.jobs._thread = Live()
    scope = c.scope(None)
    private = c.store.create(tool='read', payload={'text': 'PRIVATE CALLER TEXT'}, access_scope=scope)
    legacy = c.jobs.submit(question='PRIVATE LEGACY', access_scope=scope)['job_id']
    c.jobs.inquire('summarize private objects', scope, foreground_timeout=0)
    with c.jobs._db() as db:
        inquiry_id = db.execute('SELECT job FROM inquiry_index').fetchone()[0]
    job = c.jobs.lookup(inquiry_id, access_scope=scope)['job']
    from project_control.as1_contracts import DurableJob
    monkeypatch.setattr(TrustedObserverFactory, '__call__', lambda self, service, job: self.tools)
    tools = c.jobs.worker_factory.trusted(c.jobs, DurableJob.model_validate(job))
    for kind, target in [('packet', private.packet_id), ('investigation', legacy)]:
        shared = tools('search', {'query': {'kind': kind, 'target': target}}, scope)
        assert shared['status'] == 'ok'
        assert 'PRIVATE' in str(shared)
    # The same valid private packet remains an accepted, explicit input.
    c.jobs.inquire('with explicit material', scope, hints=[private.packet_id], foreground_timeout=0)
    with c.jobs._db() as db:
        job = c.jobs.lookup(db.execute("SELECT id FROM jobs WHERE json_extract(record,'$.question')='with explicit material'").fetchone()[0], access_scope=scope)['job']
    tools = c.jobs.worker_factory.trusted(c.jobs, DurableJob.model_validate(job))
    allowed = tools('search', {'query': {'kind': 'packet', 'target': private.packet_id}}, scope)
    assert allowed['data']['result']['text'] == 'PRIVATE CALLER TEXT'
    assert c.jobs.inquire('with explicit material', {**scope, 'principal': 'bob', 'profile': 'coder'})['status'] == 'thinking'


def test_shared_packet_access_preserves_source_and_explicit_authority(servers, tmp_path):
    from project_control.as1_context import ContextHost
    from project_control.config import ProjectControlConfig, WorkspaceConfig, RepositoryConfig
    root = tmp_path/'repo'; root.mkdir()
    config = ProjectControlConfig(workspaces={p: WorkspaceConfig(repositories={'source': RepositoryConfig(root=root)})
                                              for p in ['allowed', 'outside']})
    server = servers(config=config, host=ContextHost('observer', 'reader', frozenset({'allowed'})))
    c = server._project_control_surface
    scope = c.scope('allowed')
    cross_role = {**scope, 'principal': 'writer', 'profile': 'coder'}
    packet = c.store.create(tool='read', access_scope={**scope, 'authority': 'registered'},
        payload={'text': 'cross-role fact'}, sources=[SourceLocator(project='allowed', repository='source',
                            path='facts.md', content_sha256='a'*64)])
    assert c.store.lookup(packet.packet_id, access_scope={**cross_role, 'authority': 'registered'}).status == 'ok'
    assert c.store.lookup(packet.packet_id, access_scope={**cross_role, 'authority': 'different'}).status == 'forbidden'
    outside = c.store.create(tool='read', access_scope=scope, payload={'text': 'outside source'},
        sources=[SourceLocator(project='outside', repository='source', path='facts.md', content_sha256='b'*64)])
    assert c.store.lookup(outside.packet_id, access_scope=cross_role).status == 'forbidden'
    with __import__('pytest').raises(PermissionError, match='project_not_permitted'):
        c.scope('outside')


def test_derived_answer_preserves_registered_source_authority(tmp_path):
    from project_control.as1_context import ContextHost
    from project_control.as1_jobs import JobService
    from project_control.as1_surface import SurfaceComposition
    c = SurfaceComposition(); c.host = ContextHost('observer', 'reader', frozenset({'allowed'}))
    store = SQLitePacketStore(tmp_path/'packets', authority_access=c.packet_access)
    jobs = JobService(tmp_path/'jobs', packets=store, inquiry_access=c.inquiry_access)
    class Live:
        def is_alive(self): return True
    jobs._thread = Live()
    # Scripted fixture presents an observation from an unregistered source.
    jobs.inquire('derived source', c.scope('allowed'), foreground_timeout=0)
    job = jobs.claim()
    source = SourceLocator(project='outside', repository='source', path='facts.md', content_sha256='a'*64)
    jobs.observe(job.job_id, job.attempt, {'sources': [source.model_dump()], 'text': 'outside source fact'})
    jobs.finish(job.job_id, job.attempt, {'status': 'completed', 'answer': 'Derived from the observation.'})
    with jobs._db() as db:
        record = __import__('json').loads(db.execute('SELECT record FROM jobs WHERE id=?', (job.job_id,)).fetchone()[0])
    assert store.lookup(record['result_packet'], access_scope=c.scope('allowed')).status == 'forbidden'
    assert jobs.inquire('derived source', c.scope('allowed'))['reason'] == 'access_unavailable'
    assert not jobs.log(access_scope=c.scope('allowed'))


def test_derived_source_manifest_retains_exact_freshness(servers, tmp_path):
    from project_control.config import ProjectControlConfig, WorkspaceConfig, RepositoryConfig
    root = tmp_path/'source'; root.mkdir(); (root/'facts.md').write_text('fact')
    config = ProjectControlConfig(workspaces={'project': WorkspaceConfig(repositories={'source': RepositoryConfig(root=root)})})
    c = servers(config=config)._project_control_surface
    # Keep the claim path deterministic and independent of a live supervisor;
    # composition supplies the real, configured analysis runtime identity.
    c.jobs.backend.central_status = lambda: {'status': 'available'}
    class Live:
        def is_alive(self): return True
    c.jobs._thread = Live()
    scope = c.scope('project')
    source = SourceLocator(project='project', repository='source', path='facts.md',
                           content_sha256=hashlib.sha256((root/'facts.md').read_bytes()).hexdigest())
    original = c.store.create(tool='read', access_scope=scope, sources=[source], payload={'text': 'fact'})
    c.jobs.inquire('supported manifest', scope, foreground_timeout=0)
    job = c.jobs.claim()
    c.jobs.observe(job.job_id, job.attempt, {'packet': original.packet_id, 'sources': [source.model_dump()]}, tool='read')
    c.jobs.finish(job.job_id, job.attempt, {'status': 'completed', 'answer': 'fact'})
    with c.jobs._db() as db:
        record = __import__('json').loads(db.execute('SELECT record FROM jobs WHERE id=?', (job.job_id,)).fetchone()[0])
    assert c.jobs.freshness_provider(record)['fresh']
    (root/'facts.md').write_text('changed')
    assert not c.jobs.freshness_provider(record)['fresh']
