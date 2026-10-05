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
