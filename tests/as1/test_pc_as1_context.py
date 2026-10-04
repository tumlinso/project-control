"""CTX journeys use canonical synthesis, real Git and durable SQLite packets."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
import project_control.as1_context as context_module
from project_control.as1_context import ContextHost, InformationService
from project_control.as1_packets import SQLitePacketStore
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import ProjectSnapshot, RepositoryIdentity


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def world(tmp_path, monkeypatch):
    assert Path(context_module.__file__).resolve() == Path(__file__).resolve().parents[2] / 'src/project_control/as1_context.py'
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    root = tmp_path / 'repo'; root.mkdir()
    git(root, 'init', '-b', 'main'); git(root, 'config', 'user.name', 'Fixture'); git(root, 'config', 'user.email', 'test@example.invalid')
    (root / 'README.md').write_text('Fixture purpose: bounded calculations.\n')
    (root / 'module.py').write_bytes(b'def calculate_total(value):\r\n    return value + 1\r\n\n')
    (root / 'other.py').write_text('def unrelated():\n    pass\n')
    git(root, 'add', '.'); git(root, 'commit', '-m', 'fixture')
    head = git(root, 'rev-parse', 'HEAD')
    config = ProjectControlConfig(workspaces={'demo': WorkspaceConfig(authority_repository='source', repositories={'source': RepositoryConfig(root=root)})})
    snap = ProjectSnapshot(workspace_id='demo', project_uuid='fixture-project', observed_at='2026-10-04T00:00:00Z', todo_revision=10,
        repositories={'source': RepositoryIdentity(commit=head, dirty=False)},
        todo_tables={'tasks': [{'id': 'T1', 'title': 'Calculation', 'status': 'ready', 'revision': 2}],
                     'interfaces': [{'id': 'I1', 'title': 'Interface', 'revision': 3}],
                     'decisions': [{'id': 'D1', 'title': 'Decision', 'revision': 4}],
                     'gates': [{'id': 'G1', 'task_id': 'T1', 'status': 'pending', 'valid': False}],
                     'workflow_runs': [{'id': 'R1', 'state': 'active'}],
                     'events': [{'revision': 9, 'event_type': 'task_updated', 'entity_id': 'T1', 'timestamp': '2026-10-03T00:00:00Z'},
                                {'revision': 10, 'event_type': 'agent_heartbeat', 'entity_id': 'T1', 'timestamp': '2026-10-04T00:00:00Z'}]})
    snap.todo_workflow = {'available': True, 'runs': [{'id':'R1', 'state':'active', 'lanes':[] }]}
    store = SQLitePacketStore(tmp_path / 'state')
    anchor = {'project': 'demo', 'repository': 'source', 'path': 'README.md', 'content_sha256': hashlib.sha256((root/'README.md').read_bytes()).hexdigest()}
    semantic = {'orientation': [{'id': 'purpose', 'payload': {'fields': {'purpose': 'Bounded calculations'}, 'anchors': [anchor]},
                                'field_freshness': {'purpose': {'status': 'fresh', 'sources': [anchor]}}}],
                'skill_uses': [{'id': 'used', 'payload': {'skill': 'python', 'status': 'applied', 'reason': 'Calculation convention', 'anchors': [anchor]}}],
                'declarations': [{'id': 'note', 'payload': {'summary': 'Attributed note', 'anchors': [anchor]}}]}
    service = InformationService(config, store, lambda p: snap, ContextHost('observer', 'alice', frozenset({'demo'})), semantic_provider=lambda p: semantic)
    return root, snap, store, service


def full(world, tool, **args):
    service = world[3]
    result = service.call(tool, project='demo', detail='extended', **args)
    assert world[2].lookup(result['packet'], access_scope=service.host.scope('demo')).status == 'ok'
    return result


@pytest.mark.as1_case('CTX-01')
def test_catalog_orientation_packet_sources(world):
    result = world[3].call('overview')
    assert result['data']['projects'][0]['project'] == 'demo'
    result = full(world, 'overview')
    assert result['data']['orientation'][0]['payload']['fields']['purpose'] == 'Bounded calculations'
    assert result['sources'][0]['path'] == 'README.md'
    world[3].semantic_provider = None
    result = full(world, 'overview')
    assert any('unavailable' in x['reason'] for x in result['coverage']['omissions'])


@pytest.mark.as1_case('CTX-02')
def test_delta_material_and_missing_baselines(world):
    result = full(world, 'delta', since={'todo_revision': 8, 'commits': {'source': world[1].repositories['source'].commit}})
    assert 'agent_heartbeat' not in json.dumps(result['data'])
    result = full(world, 'delta', since='unknown-baseline')
    assert result['status'] == 'partial' and result['data']['baseline'] == 'not_found'
    baseline = full(world, 'overview')['packet']
    result = full(world, 'delta', since=baseline)
    assert world[2].resolve(result['packet'], access_scope=world[3].host.scope('demo')).parents == [baseline]


@pytest.mark.as1_case('CTX-03')
def test_frontier_scope_uses_canonical_coordination(world):
    result = full(world, 'frontier', scope={'task_id': 'T1'})
    assert 'coordination' in result['data']
    assert 'repositories' not in result['data']


@pytest.mark.as1_case('CTX-04')
def test_read_batch_ranges_actual_hashes_immutable_git(world):
    root = world[0]; old = world[1].repositories['source'].commit
    raw = (root/'module.py').read_bytes()
    (root/'module.py').write_text('dirty bytes\n')
    result = full(world, 'read', paths=[{'path': 'module.py', 'line_start': 1, 'line_end': 2}, 'missing.py'], revision=old)
    assert result['data']['files'][0]['content'] == raw.decode().split('\n\n')[0] + '\n'
    assert result['sources'][0]['content_sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['data']['files'][1]['status'] == 'error'
    assert full(world, 'read', paths=['module.py'])['data']['files'][0]['content'] == 'dirty bytes\n'


@pytest.mark.as1_case('CTX-05')
def test_paths_symlinks_binary_races(world):
    root = world[0]
    (root/'link.py').symlink_to(root/'module.py'); (root/'folder').symlink_to(root)
    (root/'binary.py').write_bytes(b'x\0y')
    paths = ['/etc/passwd', '../module.py', 'C:/module.py', '\\\\host\\x', 'link.py', 'folder/module.py', 'binary.py', '.', 'x\0y']
    result = full(world, 'read', paths=paths)
    assert len(result['data']['files']) == len(paths)
    assert all(item['status'] == 'error' for item in result['data']['files'])
    git(root, 'add', 'link.py'); git(root, 'commit', '-m', 'link')
    assert full(world, 'read', paths=['link.py'], revision=git(root, 'rev-parse', 'HEAD'))['data']['files'][0]['error'] == 'symlink_denied'
    real = os.fstat; calls = [0]
    def raced(fd):
        calls[0] += 1
        value = real(fd)
        if calls[0] == 2: (root/'module.py').write_text('changed during read\n')
        return value
    with patch('project_control.as1_context.os.fstat', raced):
        assert full(world, 'read', paths=['module.py'])['data']['files'][0]['error'] == 'racy_source_read'


@pytest.mark.as1_case('CTX-06')
@pytest.mark.parametrize('kind,target', [('task','T1'), ('gate','G1'), ('interface','I1'), ('decision','D1'), ('run','R1')])
def test_exact_native_entities_never_discover(world, kind, target):
    with patch('project_control.as1_context.source_context', side_effect=AssertionError('lexical read forbidden')), \
         patch('project_control.graph.ProjectGraph.resolve', side_effect=AssertionError('fuzzy resolver forbidden')):
        result = full(world, 'search', query={'kind':kind, 'target':target})
    assert result['data']['resolution']['status'] == 'resolved'
    assert result['data']['authority_revision'] == 10
    assert not result['sources']


@pytest.mark.as1_case('CTX-06')
def test_exact_miss_packet_and_job_scoped_access(world):
    with patch('project_control.as1_context.source_context', side_effect=AssertionError()):
        missing = full(world, 'search', query={'kind':'symbol', 'target':'missing_symbol'})
    assert missing['status'] == 'partial'
    reference = full(world, 'overview')['packet']
    assert full(world, 'search', query={'kind':'packet','target':reference})['data']['resolution'] == 'ok'
    world[3].host = ContextHost('observer', 'bob', frozenset({'demo'}))
    assert full(world, 'search', query={'kind':'packet','target':reference})['data']['resolution'] == 'forbidden'
    seen = []
    world[3].job_lookup = lambda ref, scope: seen.append((ref, scope)) or {'resolution':'forbidden'}
    full(world, 'search', query={'kind':'investigation', 'target':'job_12345'})
    assert seen == [('job_12345', {'principal':'bob', 'project':'demo', 'profile':'observer'})]


@pytest.mark.as1_case('CTX-07')
def test_discovery_graph_paths_phrases_live_fallback(world):
    result = full(world, 'search', query='def calculate_total', scope={'repository':'source'})
    source = result['data']['source'][0]
    assert source['live_fallback']
    assert all('calculate_total' in str(m) for m in source['live_fallback'])
    assert 'semantic' in result['data']
    assert full(world, 'search', query='module.py')['data']['source'][0]['paths'] == ['module.py']
    assert next(s for s in result['sources'] if s['path'] == 'module.py')['content_sha256'] == hashlib.sha256((world[0]/'module.py').read_bytes()).hexdigest()


@pytest.mark.as1_case('CTX-08')
def test_evidence_registered_execution_distinctions(world):
    world[1].todo_semantic = {'revision':10, 'tasks': [{'id':'T1', 'effective_state':'active'}], 'gates': [
        {'id':'pending','task_id':'T1','effective_state':'current_pending','valid':False},
        {'id':'failed','task_id':'T1','effective_state':'current_failed','valid':False},
        {'id':'pass','task_id':'T1','effective_state':'current_valid','valid':True},
        {'id':'stale','task_id':'T1','effective_state':'historical_stale','valid':True,'relevance':'historical'}]}
    result = full(world, 'evidence', subject='T1', kinds=['gates'])
    assert {g['evidence_classification'] for g in result['data']['registered_gates']} == {'registered_unrun','failed','current_pass','stale_historical_pass'}
    world[1].todo_semantic['tasks'][0].update(terminal=True, raw_result='validated')
    assert 'terminal_frozen_success' in json.dumps(full(world, 'evidence', subject='T1', kinds=['gates'])['data'])
    assert result['data']['source_mentions_are_proof'] is False


@pytest.mark.as1_case('CTX-09')
def test_history_real_ancestry_anchor_and_unknown_boundary(world):
    root = world[0]; first = world[1].repositories['source'].commit
    (root/'README.md').write_text('second\n'); git(root,'add','.'); git(root,'commit','-m','second')
    second = git(root,'rev-parse','HEAD'); world[1].repositories['source'].commit = second
    result = full(world, 'history', subject='T1', from_commit=first, to_commit=second)
    assert [r['commit'] for r in result['data']['git_events']] == [second]
    assert result['data']['git_events'][0]['causal_basis'] is None
    assert full(world, 'history', subject='T1', from_task='T1')['data']['selectors']['from_revision'] == 2
    assert full(world, 'history', subject='T1', from_interface='I1')['data']['selectors']['from_revision'] == 3
    assert full(world, 'history', subject='T1', from_checkpoint='missing')['status'] == 'partial'
    assert full(world, 'history', subject='T1', from_commit=second, to_commit=first)['status'] == 'partial'


@pytest.mark.as1_case('CTX-10')
@pytest.mark.parametrize('role', ['observer','coder','codex','mutator','investigator','skill_assembler'])
def test_machine_shared_volatile_profile_gates(world, role):
    world[3].host = ContextHost(role, 'alice', frozenset({'demo'}))
    result = world[3].call('machine', query_or_view='host_memory')
    assert result['data']['observed_at']
    assert world[2].resolve(result['packet'], access_scope=world[3].host.scope(None)).freshness['volatile']
    with pytest.raises(ValueError): world[3].call('machine', query_or_view='launch_benchmark')
    if role != 'observer':
        with pytest.raises(PermissionError): world[3].call('read', project='demo', paths=['module.py'])
        with pytest.raises(PermissionError): world[3].call('overview', project='demo', detail='extended')


@pytest.mark.as1_case('CTX-11')
def test_applied_skill_notes_sources_and_budget_exactness(world):
    result = full(world, 'search', query='calculate_total')
    assert result['data']['skill_uses'][0]['payload']['skill'] == 'python'
    assert result['data']['notes'][0]['payload']['summary'] == 'Attributed note'
    (world[0]/'big.py').write_text('x'*6000+'\n')
    compact = world[3].call('read', project='demo', paths=['big.py'])
    assert compact['status'] == 'partial' and compact['data']['continuation']['query']['kind'] == 'packet'
    assert world[2].resolve(compact['packet'], access_scope=world[3].host.scope('demo')).payload['data']['files'][0]['content'] == 'x'*6000+'\n'


@pytest.mark.as1_case('CTX-01', 'CTX-11')
def test_actual_skills_semantic_port_fixture(world, tmp_path):
    # Isolate source module identity from the currently bound migration-11 runtime.
    source = Path('/home/tumlinson/.agents/skills/todo-orchestrator')
    script = '''
import hashlib, json, pathlib, sys
from todo_orchestrator.service import Service
import todo_orchestrator.service as module
from v2_helpers import V2Repo
assert pathlib.Path(module.__file__).resolve() == pathlib.Path(sys.argv[1]) / 'todo_orchestrator/service.py'
repo = V2Repo()
try:
 p=repo.root/'source.txt'; p.write_text('bounded semantic fixture\\n')
 svc=repo.service
 anchor={'project':svc.project['project_uuid'],'repository':str(repo.root),'path':'source.txt','content_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
 for action,payload in [('update_orientation', {'id':'orient','fields':{'purpose':'Canonical fixture purpose'},'anchors':[anchor]}), ('record_skill_use', {'id':'skill','skill':'python','status':'applied','reason':'fixture guidance','anchors':[anchor]})]:
  svc.amend_project({'format':'pc-project-amendment/1','project':svc.project['project_uuid'],'action':action,'intent':'fixture acceptance','expected_revision':svc.db.revision(),'operation_id':'fixture-operation-'+action,'payload':payload,'mode':'apply'}, role='mutator')
 reader=Service(repo.root,read_only=True); before=reader.db.revision(); result=reader.project_context(); assert reader.db.revision()==before
 print(json.dumps(result))
finally: repo.close()
'''
    env = {**os.environ, 'PYTHONPATH': str(source) + ':' + str(source/'tests'), 'PYTHONDONTWRITEBYTECODE':'1'}
    result = subprocess.run(['/home/tumlinson/project-control/.venv/bin/python', '-c', script, str(source)], env=env, capture_output=True, text=True, check=True)
    actual = json.loads(result.stdout)
    world[3].semantic_provider = lambda project: actual
    response = full(world, 'overview')
    assert response['data']['orientation'][0]['payload']['fields']['purpose'] == 'Canonical fixture purpose'
    assert response['data']['orientation'][0]['field_freshness']['purpose']['status'] == 'fresh'
    assert response['data']['skill_uses'][0]['payload']['reason'] == 'fixture guidance'
    # A migration-11 port must stay honest and must not trigger a migration.
    world[3].semantic_provider = lambda project: {'status':'unavailable', 'required_migration_version':12}
    response = full(world, 'overview')
    assert response['status'] == 'partial'
    assert response['data']['orientation'] == []
    assert any('migration_required' in item['reason'] for item in response['coverage']['omissions'])


@pytest.mark.as1_case('CTX-06')
def test_packet_and_job_exact_reads_skip_snapshot_and_ambiguity(world):
    reference = full(world, 'overview')['packet']
    world[3].snapshot_provider = lambda project: (_ for _ in ()).throw(AssertionError('snapshot unnecessary'))
    assert full(world, 'search', query={'kind':'packet', 'target':reference})['data']['resolution'] == 'ok'
    world[3].job_lookup = lambda ref, scope: {'status':'not_found'}
    assert full(world, 'search', query={'kind':'investigation', 'target':'job_missing'})['status'] == 'partial'
    world[3].snapshot_provider = lambda project: world[1]
    world[1].todo_tables['interfaces'].extend([{'id':'I2', 'title':'Ambiguous'}, {'id':'I3', 'title':'Ambiguous'}])
    result = full(world, 'search', query={'kind':'interface', 'target':'Ambiguous'})
    assert result['data']['resolution']['status'] == 'ambiguous'
    assert {c['id'] for c in result['data']['resolution']['candidates']} == {'I2','I3'}


@pytest.mark.as1_case('CTX-04')
def test_invalid_revision_is_independent_batch_error(world):
    result = full(world, 'read', paths=['module.py', 'README.md'], revision='no-such-revision')
    assert len(result['data']['files']) == 2
    assert all(row['error'] == 'invalid_revision' for row in result['data']['files'])


@pytest.mark.as1_case('CTX-06')
def test_same_principal_local_role_cannot_replay_observer_packets(world):
    read = full(world, 'read', paths=['module.py'])['packet']
    overview = full(world, 'overview')['packet']
    world[3].host = ContextHost('coder','alice',frozenset({'demo'}))
    for reference in (read, overview):
        result = world[3].call('search', project='demo', query={'kind':'packet','target':reference})
        assert result['data']['resolution'] == 'forbidden'
        assert result['data']['result'] is None


@pytest.mark.as1_case('CTX-06')
def test_resolved_exact_path_does_not_read_filesystem(world):
    world[1].todo_tables['ownership_scopes'] = [{'task_id':'T1','path':'module.py','mode':'read'}]
    with patch('project_control.services.inspect.read_bounded_text', side_effect=AssertionError('no native read')), \
         patch('project_control.services.inspect.CtxppReadAdapter.inspect', side_effect=AssertionError('no source fallback')):
        result = full(world, 'search', query={'kind':'path','target':'module.py'})
    assert result['data']['resolution']['status'] == 'resolved'
    assert result['data']['source'] == 'reconciled_project_graph'
    assert 'excerpt' not in result['data']


@pytest.mark.as1_case('CTX-01','CTX-11')
def test_context_revision_disagreement_is_attributed_partial(world):
    value = world[3].semantic_provider('demo')
    value.update(project_revision=11,project_uuid='fixture-project')
    result = full(world, 'overview')
    assert result['status'] == 'partial'
    assert result['coverage']['context_revision'] == 11
    assert result['coverage']['semantic_revision'] == 10
    assert any(i['reason'] == 'semantic_context_revision_skew' for i in result['coverage']['omissions'])


@pytest.mark.as1_case('CTX-09')
def test_history_merge_range_and_retained_selectors(world):
    root = world[0]; base = world[1].repositories['source'].commit
    git(root,'checkout','-b','side')
    (root/'side.txt').write_text('side\n'); git(root,'add','.'); git(root,'commit','-m','side')
    side = git(root,'rev-parse','HEAD')
    git(root,'checkout','main')
    (root/'main.txt').write_text('main\n'); git(root,'add','.'); git(root,'commit','-m','main')
    main = git(root,'rev-parse','HEAD')
    git(root,'merge','--no-ff','side','-m','merge side'); merge = git(root,'rev-parse','HEAD')
    world[1].repositories['source'].commit = merge
    result = full(world,'history',subject='T1',from_commit=base,to_commit=merge)
    assert {r['commit'] for r in result['data']['git_events']} == {side,main,merge}
    world[1].todo_tables['checkpoints'] = [{'id':'C1','revision':5,'state':'reached'}]
    assert full(world,'history',subject='T1',from_checkpoint='C1')['data']['selectors']['from_revision'] == 5
    assert not full(world,'history',subject='T1',from_time='2040-01-01T00:00:00Z')['data']['git_events']
    result = full(world,'history',subject='T1',from_revision=0,to_revision=8)
    assert all(e['revision'] <= 8 for e in result['data']['events'] if e.get('revision') is not None)
