"""Public inquiry delivery and material dependency validation, without inference."""
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

from project_control.as1_contracts import SourceLocator
from project_control.as1_surface import InquiryFreshness, public_inquiry
from project_control.as1_packets import SQLitePacketStore
from project_control.as1_skill import SkillService
from test_pc_as1_surface import servers, run


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
    entrypacket = store.create(tool='command', access_scope=scope, payload={
        'status': 'completed', 'exit_code': 0, 'source_reads': [{'method': 'direct_cat',
            'path': str(entry), 'content_sha256': hashlib.sha256(entry.read_bytes()).hexdigest()}]})
    job['evidence_packets'].insert(0, entrypacket.packet_id)
    jobs.lookup.return_value['observations'].insert(0, {'packet_id': entrypacket.packet_id})
    assert fresh(job)['fresh']
    entry.write_text('changed entry')
    assert not fresh(job)['fresh']


def test_public_skill_terminal_projection_and_unavailable(servers):
    server = servers(); c = server._project_control_surface
    c.skills.inquire = Mock(return_value={'status': 'partial', 'job_id': 'private', 'attempt': 1,
        'excerpts': [{'content': 'exact source', 'verbatim': True}],
        'continuation': {'job_id': 'private', 'detail': 'extended'},
        'result': {'lease': 1, 'queue_position': 2}, 'unresolved': ['Missing dependency']})
    result = run(server.call_tool('skill', {'query': 'Use instructions', 'detail': 'extended'}))[1]
    assert result['status'] == 'partial' and result['excerpts'][0]['verbatim']
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
        assert b.jobs.lookup(job_id, access_scope=b.scope(None))['status'] == 'forbidden'
    finally:
        b.close(); a.close()


def test_shared_worker_cannot_launder_private_packet_or_job_without_hint(servers, monkeypatch):
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
        denied = tools('search', {'query': {'kind': kind, 'target': target}}, scope)
        assert denied['status'] == 'unavailable'
        assert 'PRIVATE' not in str(denied)
    # The same valid private packet remains an accepted, explicit input.
    c.jobs.inquire('with explicit material', scope, hints=[private.packet_id], foreground_timeout=0)
    with c.jobs._db() as db:
        job = c.jobs.lookup(db.execute("SELECT id FROM jobs WHERE json_extract(record,'$.question')='with explicit material'").fetchone()[0], access_scope=scope)['job']
    tools = c.jobs.worker_factory.trusted(c.jobs, DurableJob.model_validate(job))
    allowed = tools('search', {'query': {'kind': 'packet', 'target': private.packet_id}}, scope)
    assert allowed['data']['result']['text'] == 'PRIVATE CALLER TEXT'
    assert c.jobs.inquire('with explicit material', {**scope, 'principal': 'bob', 'profile': 'coder'})['reason'] == 'access_unavailable'
