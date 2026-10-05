"""TRC journeys execute the managed source with real Git and typed providers."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest
import project_control.as1_trace as trace_module
from project_control.as1_trace import TraceService, ProviderFragment, digest, node_key
from project_control.as1_context import ContextHost, InformationService
from project_control.as1_packets import SQLitePacketStore
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import ProjectSnapshot, RepositoryIdentity


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def world(tmp_path, monkeypatch):
    assert Path(trace_module.__file__).resolve() == Path(__file__).resolve().parents[2] / 'src/project_control/as1_trace.py'
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    roots, snapshots, contexts = {}, {}, {}
    for name in ('demo', 'foreign'):
        root = tmp_path / name; root.mkdir()
        git(root, 'init', '-b', 'main'); git(root, 'config', 'user.name', 'Fixture'); git(root, 'config', 'user.email', 'test@example.invalid')
        (root / 'base.py').write_text('VALUE = 1\n')
        (root / 'mid.py').write_text('import base\n')
        (root / 'top.py').write_text('import mid\n')
        (root / 'README.md').write_text('[base](base.py)\n')
        git(root, 'add', '.'); git(root, 'commit', '-m', 'fixture')
        head = git(root, 'rev-parse', 'HEAD')
        roots[name] = root
        snapshots[name] = ProjectSnapshot(workspace_id=name, project_uuid='fixture-' + name,
            observed_at='2026-10-04T00:00:00Z', todo_revision=12,
            repositories={'source': RepositoryIdentity(commit=head, dirty=False)},
            todo_tables={'tasks': [{'id':'T1', 'title':'Provider', 'status':'ready'}, {'id':'T2', 'title':'Consumer', 'status':'ready'}, {'id':'T3', 'title':'Transitive', 'status':'ready'}],
                         'task_dependencies': [{'task_id':'T2', 'prerequisite_task_id':'T1'}, {'task_id':'T3', 'prerequisite_task_id':'T2'}]})
        contexts[name] = {'project_uuid':'fixture-' + name, 'project_revision':12, 'declarations':[], 'invalidations':[]}
    config = ProjectControlConfig(workspaces={n: WorkspaceConfig(authority_repository='source', repositories={'source':RepositoryConfig(root=r)}) for n, r in roots.items()})
    host = ContextHost('observer', 'alice', frozenset(roots))
    service = TraceService(config, snapshots.__getitem__, host, semantic_provider=contexts.__getitem__)
    return roots, snapshots, contexts, service, config, host, tmp_path


def names(result):
    return [(r['node']['project_uuid'], r['node']['id']) for r in result['dependencies']]


@pytest.mark.as1_case('TRC-01')
def test_multi_hop_direction_cycle_fanout_and_witness(world):
    roots, snaps, _, service, *_ = world
    result = service(project='demo', targets=['base.py'])
    assert names(result) == [('fixture-demo', 'mid.py'), ('fixture-demo', 'top.py')]
    assert len(result['dependencies'][1]['witness_chain']) == 2
    assert all(e['relation'] == 'imports' for r in result['dependencies'] for e in r['witness_chain'])
    assert not service(project='demo', targets=['top.py'])['dependencies']
    declared = service(project='demo', targets=[{'kind':'task', 'id':'T1'}])
    assert [r['node']['id'] for r in declared['dependencies']] == ['T2', 'T3']
    (roots['demo'] / 'base.py').write_text('import top\n')
    cycle = service(project='demo', targets=['base.py'])
    assert cycle['traversal']['cycle_or_shared_path_count'] == 1
    for i in range(12): (roots['demo'] / f'fan{i}.py').write_text('import base\n')
    bounded = service(project='demo', targets=['base.py'], max_nodes=2)
    assert len(bounded['dependencies']) == 2 and bounded['traversal']['omitted_groups']
    assert bounded['continuation'] and not bounded['coverage']['complete_graph_cut']
    continued = service(project='demo', targets=['base.py'], max_nodes=40, cursor=bounded['continuation'])
    assert len(continued['dependencies']) == 14
    with pytest.raises(ValueError): service(project='demo', targets=['base.py'], change_class='silent')


class ExportProvider:
    """Installed compiler export port; source determinants are independently checked."""
    name = 'compiler-export/1'
    def __init__(self, suffix='.ts'):
        self.suffix = suffix; self.refreshes = 0; self.version = '1'; self.edges = []
    def detect(self, obs): return any(p.endswith(self.suffix) for p in obs['membership'])
    def observe(self, obs):
        return [{'kind':'provider', 'key':self.name, 'digest':digest(self.version)},
                {'kind':'configuration', 'key':'flags', 'digest':digest('-O0')},
                {'kind':'directory_membership', 'key':'source', 'digest':digest(obs['membership'])},
                *[{'kind':'file', 'key':p, 'digest':digest((obs['root']/p).read_bytes())} for p in obs['membership'] if p.endswith(self.suffix)]]
    def refresh(self, obs, inputs):
        self.refreshes += 1
        generation = digest(inputs)
        source = TraceService._node(obs, 'file', 'consumer' + self.suffix, 'consumer' + self.suffix)
        target = TraceService._node(obs, 'file', 'provider' + self.suffix, 'provider' + self.suffix)
        edge = TraceService._edge(source, target, 'calls', self.name, generation, inputs,
                                  witness={'path':'consumer' + self.suffix, 'start_line':1, 'end_line':1, 'producer':'fixture compiler', 'content_sha256':digest((obs['root']/('consumer'+self.suffix)).read_bytes())})
        self.edges = [edge]
        return ProviderFragment(self.name, generation, inputs, [edge], [source, target], ('calls',), True, 'current', ['export_scope_only;dynamic_dispatch_unknown'])


@pytest.mark.as1_case('TRC-02')
def test_heterogeneous_relations_cpp_reuse_and_installed_export(world):
    roots, _, contexts, service, config, host, _ = world
    root = roots['demo']
    (root / 'provider.cpp').write_text('int provider() { return 1; }\n')
    (root / 'consumer.cpp').write_text('int provider(); int consumer() { return provider(); }\n')
    index = root / '.ctxpp'; index.mkdir()
    (index / 'index.jsonl').write_text(json.dumps({'id':'provider', 'name':'provider', 'kind':'function', 'path':'provider.cpp', 'line':1}) + '\n')
    (root / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1"\ndependencies=["foreign==1"]\n')
    (root / 'generated.py').write_text('VALUE=2\n')
    contexts['demo']['declarations'].append({'kind':'generation', 'id':'gen', 'version':1, 'origin':'project_declared',
        'payload':{'id':'gen', 'generator':'fixture-generator/1', 'roots':['generated.py'],
                   'inputs':[{'project':'demo','repository':'source','path':'base.py','content_sha256':digest((root/'base.py').read_bytes())}]}})
    compiler = ExportProvider('.cpp'); service.providers = (compiler,)
    result = service(project='demo', targets=['provider.cpp'], query='provider')
    assert names(result) == [('fixture-demo', 'consumer.cpp')]
    assert any(c['source'] == 'existing_ctxpp_index' for c in result['possible_related_candidates'])
    assert all(not c['propagates'] for c in result['possible_related_candidates'])
    generated = service(project='demo', targets=['base.py'])
    assert ('fixture-demo', 'generated.py') in names(generated)
    assert any(e['relation'] == 'mentions' for e in generated['nonpropagating_relations'])
    assert ('fixture-demo', 'README.md') not in names(generated)
    assert any(u.get('edge', {}).get('relation') == 'package_dependency' for u in generated['unknown_scope'])
    assert not result['coverage']['complete_graph_cut']
    service.providers = ()
    absent = service(project='demo', targets=['provider.cpp'])
    assert not absent['dependencies'] and absent['status'] == 'partial'


@pytest.mark.as1_case('TRC-03')
def test_cross_project_registration_versions_permissions_and_no_name_resolution(world):
    roots, snapshots, contexts, service, config, host, _ = world
    contexts['foreign']['declarations'] = [{'kind':'identity','id':'api','version':1,'origin':'project_declared',
         'payload':{'id':'api','repository':'source','version':'2'}}]
    ref = {'project_uuid':'fixture-foreign','repository':'source','kind':'identity','id':'api'}
    relation = {'kind':'relation','id':'use','version':1,'origin':'project_declared', 'payload':{
        'id':'use','source':{'project_uuid':'fixture-demo','repository':'source','kind':'file','id':'top.py','path':'top.py'},
        'target':ref,'target_version':'2','relation':'depends_on'}}
    contexts['demo']['declarations'] = [relation]
    result = service(project='foreign', targets=[{'kind':'identity','id':'api'}])
    assert ('fixture-demo','top.py') in names(result)
    assert result['dependencies'][0]['witness_chain'][0]['witness']['target_version'] == '2'
    assert any(i['kind']=='semantic_revision' and i['key']=='foreign' for i in result['dependencies'][0]['witness_chain'][0]['input_manifest'])
    # A version change invalidates unchanged consumer declarations; names alone
    # cannot select the replacement environment.
    contexts['foreign']['declarations'][0]['payload']['version'] = '3'
    changed = service(project='foreign', targets=[{'kind':'identity','id':'api'}])
    assert ('fixture-demo','top.py') not in names(changed)
    assert changed['generation'] != result['generation']
    assert any('identity_version_unresolved' in g for p in changed['coverage']['providers'] for g in p['gaps'])
    restricted = TraceService(config, snapshots.__getitem__, ContextHost('observer','bob',frozenset({'demo'})), semantic_provider=contexts.__getitem__)
    inaccessible = restricted(project='demo', targets=['top.py'])
    assert any(u['reason'] == 'inaccessible_or_unregistered_relationship' for u in inaccessible['unknown_scope'])
    with pytest.raises(PermissionError): restricted(project='foreign', targets=['base.py'])
    contexts['demo']['project_uuid'] = 'spoof'
    assert any('semantic_registration_unavailable' in u['reason'] for u in service(project='demo',targets=['base.py'])['unknown_scope'])


@pytest.mark.as1_case('TRC-04')
def test_incremental_new_consumers_exports_renames_config_and_watcher(world):
    roots, _, _, service, *_ = world
    root = roots['demo']
    initial = service(project='demo', targets=['base.py']); reads = service.read_count
    warm = service(project='demo', targets=['base.py'])
    assert service.read_count == reads and warm['generation'] == initial['generation']
    (root / 'new.py').write_text('import base\n')
    new = service(project='demo', targets=['base.py'], since=initial['generation'])
    assert ('fixture-demo','new.py') in names(new) and new['delta']['added_edges']
    (root / 'base.py').write_text('NEW_EXPORT=2\n')
    exported = service(project='demo', targets=['base.py'], since=new['generation'])
    assert exported['delta']['changed_edges'] and exported['generation'] != new['generation']
    (root / 'new.py').rename(root / 'renamed.py')
    renamed = service(project='demo', targets=['base.py'], since=exported['generation'])
    assert ('fixture-demo','renamed.py') in names(renamed) and renamed['delta']['removed_edges']
    (root / 'renamed.py').unlink()
    deleted = service(project='demo', targets=['base.py'])
    assert ('fixture-demo','renamed.py') not in names(deleted)
    (root / 'pyproject.toml').write_text('[project]\nname="new-config"\nversion="2"\n')
    configured = service(project='demo',targets=['base.py'])
    assert configured['generation'] != deleted['generation']
    before = service.read_count
    loss = service(project='demo',targets=['base.py'],watcher_lost=True)
    assert service.read_count > before and any('watcher_loss' in u['reason'] for u in loss['unknown_scope'])
    assert service(project='demo',targets=['base.py'],since='expired')['delta']['status'] == 'refresh_required'


@pytest.mark.as1_case('TRC-05')
def test_paths_snippets_exact_identities_stale_and_lexical_halo(world, monkeypatch):
    roots, _, _, service, *_ = world
    initial = service(project='demo',targets=['base.py']); reads = service.read_count
    paths = service(project='demo',targets=['base.py'])
    assert service.read_count == reads and all('snippet' not in d for d in paths['dependencies'])
    snippets = service(project='demo',targets=['base.py'],mode='snippets')
    assert snippets['dependencies'][0]['snippet']['text'] == 'import base'
    assert snippets['dependencies'][0]['snippet']['content_sha256'] == digest((roots['demo']/'mid.py').read_bytes())
    old_read = service._read
    def changed(root, path):
        if path == 'mid.py': return b'import elsewhere\n'
        return old_read(root, path)
    monkeypatch.setattr(service, '_read', changed)
    stale = service(project='demo',targets=['base.py'],mode='snippets')
    assert stale['dependencies'][0]['snippet']['status'] == 'stale'
    assert 'source_snippets_partial' in stale['warnings']
    halo = service(project='demo',targets=['base.py'],query='top')
    assert names(halo) == names(paths)
    assert all(not c['propagates'] for c in halo['possible_related_candidates'])
    with pytest.raises(ValueError): service(project='demo',targets=['../private'])
    (roots['demo'] / 'link.py').symlink_to(roots['foreign']/'base.py')
    denied = service(project='demo',targets=['base.py'])
    assert any('unavailable_source:link.py' in g for p in denied['coverage']['providers'] for g in p['gaps'])


@pytest.mark.as1_case('TRC-06')
def test_provider_extension_warm_reuse_replacement_and_information_port(world):
    roots, _, _, service, config, host, tmp = world
    root = roots['demo']
    (root / 'provider.ts').write_text('export function provided() { return 1; }\n')
    (root / 'consumer.ts').write_text('import {provided} from "./provider"; provided();\n')
    export = ExportProvider(); service.providers = (export,)
    first = service(project='demo',targets=['provider.ts'])
    count = export.refreshes; reads = service.read_count
    warm = service(project='demo',targets=['provider.ts'])
    assert export.refreshes == count and service.read_count == reads
    assert warm['generation'] == first['generation']
    assert names(warm) == [('fixture-demo','consumer.ts')]
    export.version = '2'
    second = service(project='demo',targets=['provider.ts'],since=first['generation'])
    assert export.refreshes == count + 1 and second['delta']['provider_state_changed']
    assert len(second['dependencies']) == 1
    information = InformationService(config, SQLitePacketStore(tmp/'packets'), service.snapshots, host, impact_provider=service)
    packet = information.call('impact',project='demo',detail='extended',targets=['provider.ts'])
    assert packet['status'] == 'partial' and packet['data']['generation'] == second['generation']
    assert packet['packet'] and packet['coverage']['omissions']
    lost = service(project='demo',targets=['provider.ts'],watcher_lost=True)
    assert export.refreshes == count + 2
    assert lost['status'] == 'partial'


@pytest.mark.parametrize('targets', [[{'path': 'README.md'}], [{'kind': 'task', 'id': 'T1'}]])
def test_impact_payload_cap_has_bounded_preview_and_schema_is_discoverable(world, targets):
    roots, _, contexts, service, config, host, tmp = world

    class BulkyImpactProvider:
        name = 'bulky-fixture/1'
        def detect(self, obs): return obs['project'] == 'demo'
        def observe(self, obs):
            return [{'kind': 'environment', 'key': f'input-{i:05d}-' + 'x' * 160,
                     'digest': digest(f'value-{i}')} for i in range(18000)]
        def refresh(self, obs, inputs):
            source = TraceService._node(obs, 'file', 'base.py', 'base.py')
            target = TraceService._node(obs, 'file', 'README.md', 'README.md')
            edge = TraceService._edge(source, target, 'imports', self.name, digest(inputs), inputs,
                                      witness={'path': 'base.py', 'start_line': 1, 'end_line': 1,
                                               'content_sha256': digest(b'VALUE = 1\n')})
            return ProviderFragment(self.name, digest(inputs), inputs, [edge], [source, target],
                                    ('imports',), True, 'current', [])

    service.providers = (BulkyImpactProvider(),)
    store = SQLitePacketStore(tmp / 'impact-cap-packets', max_payload_bytes=4 * 1024 * 1024)
    information = InformationService(config, store, service.snapshots, host, impact_provider=service)
    result = information.call('impact', project='demo', targets=targets,
                              change_class='interface', mode='paths', detail='compact')

    assert result['packet']
    assert result['status'] == 'partial'
    assert result['data']['generation']
    assert result['data']['continuation']['query']['target'] == result['packet']
    assert result['data']['dependencies'] or result['data']['unknown_scope'], result['data']
    if result['data']['dependencies']:
        preview_node = result['data']['dependencies'][0]['node']
        assert preview_node['project_uuid'].startswith('fixture-') and preview_node['repository'].startswith('source@')
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 2048
    stored = store.lookup(result['packet'], access_scope=host.scope('demo')).packet.payload['data']
    assert stored['dependencies'], stored.get('payload_budget')
    assert stored.get('payload_budget'), len(json.dumps(stored, separators=(',', ':')).encode())
    provider = next(p for p in stored['coverage']['providers'] if p['provider'] == 'bulky-fixture/1')
    expected_inputs = service.providers[0].observe({'project': 'demo'}) + [
        {'kind': 'registry', 'key': 'demo', 'digest': digest(contexts['demo']['declarations'])},
        {'kind': 'semantic_revision', 'key': 'demo', 'digest': digest(contexts['demo']['project_revision'])}]
    assert provider.get('input_manifest_count') == 18002, (provider.keys(), len(provider.get('input_manifest', [])))
    assert provider['input_manifest_sha256'] == digest(expected_inputs)
    assert stored['continuation'] is None
    assert any(o['section'] == 'input_manifests' for o in stored['payload_budget']['omissions'])
    assert len(json.dumps(store.lookup(result['packet'], access_scope=host.scope('demo')).packet.payload,
                          separators=(',', ':')).encode()) <= store.max_payload_bytes
    assert result['coverage']['omissions']

    from project_control.as1_surface import observer_tool_argument_models
    schema = observer_tool_argument_models('observer')['impact'].model_json_schema()
    target_ref = schema['properties']['targets']['items']['$ref'].split('/')[-1]
    assert schema['$defs'][target_ref]['properties'].keys() >= {'project', 'repository', 'kind', 'id', 'path'}
    assert schema['properties']['change_class']['enum'] == ['body', 'interface', 'configuration', 'generator', 'removal', 'unknown']

    import asyncio
    from project_control.as1_surface import register_surface
    from project_control.profiles import ProfiledFastMCP, enumerate_tool_schemas
    public = ProfiledFastMCP(name='impact-schema', profile='observer')
    register_surface(public, object())
    public_schema = asyncio.run(enumerate_tool_schemas(public))['impact']
    public_target_ref = public_schema['properties']['targets']['items']['$ref'].split('/')[-1]
    public_target = public_schema['$defs'][public_target_ref]['properties']
    assert public_target.keys() >= {'project', 'repository', 'kind', 'id', 'path'}
    assert public_schema['properties']['change']['enum'] == ['body', 'interface', 'configuration', 'generator', 'removal', 'unknown']
    assert public_schema['properties']['view']['enum'] == ['paths', 'snippets']


def test_absolute_relative_child_imports_and_scope_identity(world):
    roots, _, _, service, *_ = world
    root = roots['demo']; (root/'pkg').mkdir()
    (root/'pkg/__init__.py').write_text('VALUE=1\n')
    (root/'pkg/child.py').write_text('VALUE=2\n')
    (root/'absolute.py').write_text('from pkg import child, VALUE\n')
    (root/'pkg/relative.py').write_text('from . import child\n')
    result = service(project='demo',targets=['pkg/child.py'])
    assert ('fixture-demo','absolute.py') in names(result)
    assert ('fixture-demo','pkg/relative.py') in names(result)
    assert len({node_key(r['node']) for r in result['dependencies']}) == len(result['dependencies'])


@pytest.mark.as1_case('TRC-02')
def test_native_ctxpp_format_directed_calls_includes_and_configuration_gap(world):
    from project_control.as1_trace import CtxppExportProvider
    roots, _, _, service, *_ = world
    root = roots['demo']; index = root/'.ctxpp'; index.mkdir()
    (root/'api.h').write_text('int provider();\n')
    (root/'provider.cpp').write_text('int provider(){return 1;}\n')
    (root/'consumer.cpp').write_text('#include "api.h"\nint consumer(){return provider();}\n')
    config = {'flags':['-std=c++17'], 'compiler':'fixture-clang', 'generator_inputs':[], 'environment':{'target':'fixture'}}
    records = [{'record':'meta','format':'CTXPP-INDEX/1','semantic':True,'backend':'libclang-runtime','core_version':'fixture/1',
                'incomplete':False,'failures':[],'config_hash':digest(config)},
               *[{'record':'file','path':p,'hash':digest((root/p).read_bytes()),'includes':['api.h'] if p=='consumer.cpp' else []} for p in ('provider.cpp','consumer.cpp','api.h')],
               {'record':'symbol','id':'c:@F@provider#','file':'provider.cpp','name':'provider','line':1},
               {'record':'symbol','id':'c:@F@consumer#','file':'consumer.cpp','name':'consumer','line':2},
               {'record':'edge','type':'call','from':'c:@F@consumer#','to':'c:@F@provider#','file':'consumer.cpp','start':36,'end':46,
                'translation_unit':'consumer.cpp','configuration_hash':digest(config)}]
    raw = '\n'.join(json.dumps(r) for r in records).encode() + b'\n'
    (index/'index.jsonl').write_bytes(raw)
    (index/'manifest.json').write_text(json.dumps({'format':'CTXPP-MANIFEST/1','index_hash':digest(raw),
        'files':{r['path']:r['hash'] for r in records if r['record']=='file'}}))
    # This explicit disposable producer identity verifies only fixture config.
    # No installed runtime binding or deployment metadata is replaced.
    def config_observer(obs, meta):
        return {'verified':meta['config_hash'] == digest(config),
                'inputs':[{'kind':'configuration','key':'fixture-compiler-config','digest':digest(config)}]}
    provider = CtxppExportProvider(config_observer); service.providers=(provider,)
    result = service(project='demo',targets=[{'kind':'symbol','id':'c:@F@provider#'}])
    assert names(result) == [('fixture-demo','c:@F@consumer#')]
    assert result['dependencies'][0]['witness_chain'][0]['witness']['byte_start'] == 36
    includes = service(project='demo',targets=['api.h'])
    assert ('fixture-demo','consumer.cpp') in names(includes)
    provider.config_observer = lambda obs, meta: {**config_observer(obs,meta), 'verified':False}
    revoked = service(project='demo',targets=[{'kind':'symbol','id':'c:@F@provider#'}])
    assert not revoked['dependencies'] and any(p['freshness']=='unknown' for p in revoked['coverage']['providers'] if p['provider']==provider.name)
    provider.config_observer = None
    unknown = service(project='demo',targets=[{'kind':'symbol','id':'c:@F@provider#'}])
    assert not unknown['dependencies']
    assert any('compiler_configuration_or_source_unverified' in g for p in unknown['coverage']['providers'] for g in p['gaps'])
    provider.config_observer = config_observer
    (root/'provider.cpp').write_text('int provider(){return 2;}\n')
    stale = service(project='demo',targets=[{'kind':'symbol','id':'c:@F@provider#'}])
    assert not stale['dependencies'] and any(p['freshness']=='stale' for p in stale['coverage']['providers'] if p['provider']==provider.name)


def test_provider_races_and_symlink_parent_do_not_reuse_authority(world):
    roots, _, _, service, *_ = world
    root=roots['demo']; (root/'provider.ts').write_text('export const value=1;\n'); (root/'consumer.ts').write_text('value();\n')
    class RacingProvider(ExportProvider):
        def refresh(self, obs, inputs):
            fragment=super().refresh(obs,inputs)
            (obs['root']/'consumer.ts').write_text('changed-during-provider-read\n')
            return fragment
    service.providers=(RacingProvider(),)
    raced=service(project='demo',targets=['provider.ts'])
    assert not raced['dependencies']
    assert any(u['reason']=='source_or_semantics_changed_during_producer_reads' for u in raced['unknown_scope'])
    service.providers=()
    (root/'nested').mkdir(); (root/'nested/input.ts').write_text('fixture input\n')
    (root/'nested/base.py').write_text('VALUE=1\n')
    (root/'nested/consumer.py').write_text('from . import base\n')
    git(root,'add','nested'); git(root,'commit','-m','tracked nested fixture')
    before=service(project='demo',targets=['nested/base.py'])
    assert ('fixture-demo','nested/consumer.py') in names(before)
    signature=service._file_signature(root,'nested/input.ts')
    (root/'nested').rename(root/'original'); (root/'nested').symlink_to(root/'original',target_is_directory=True)
    with pytest.raises(OSError): service._file_signature(root,'nested/input.ts')
    refused=service(project='demo',targets=['nested/base.py'])
    assert not refused['dependencies']
    assert any('unavailable_source:nested/base.py' in g for p in refused['coverage']['providers'] for g in p['gaps'])


def test_source_budget_and_bad_provider_inputs_are_partial(world):
    roots, _, _, service, config, host, _=world
    bounded=TraceService(config,service.snapshots,host,semantic_provider=service.semantic,max_files=1)
    result=bounded(project='demo',targets=['base.py'])
    assert any('file_budget' in g for p in result['coverage']['providers'] for g in p['gaps'])
    root=roots['demo']; (root/'provider.ts').write_text('provider\n'); (root/'consumer.ts').write_text('consumer\n')
    class BadProvider(ExportProvider):
        def observe(self,obs):
            return [{'kind':'file','key':'../forbidden','digest':digest(b'private')}]
    service.providers=(BadProvider(),)
    bad=service(project='demo',targets=['base.py'])
    assert any(u.get('provider')==BadProvider.name and 'provider_unavailable' in u['reason'] for u in bad['unknown_scope'])


@pytest.mark.as1_case('TRC-02')
def test_genuine_installed_ctxpp_producer_and_trace_consumer(tmp_path, monkeypatch):
    """The installed producer writes its real export; no handcrafted call rows."""
    import hashlib
    import shutil
    from project_control.as1_trace import CtxppExportProvider
    skill = Path('/home/tumlinson/.agents/skills/cpp-context-compiler')
    executable = skill/'scripts/ctxpp'
    assert executable.is_file(), 'required installed ctxpp producer unavailable'
    monkeypatch.syspath_prepend(str(skill/'scripts'))
    from ctxpp_lib import load_config, stable_json, find_core, translate_recipe
    core = find_core(skill)
    assert core and core.is_file(), 'required installed ctxpp core unavailable'
    compiler = shutil.which('clang++-18') or shutil.which('clang++')
    assert compiler, 'required installed compiler identity unavailable'
    root = tmp_path/'native'; root.mkdir(); (root/'src').mkdir(); (root/'include').mkdir()
    (root/'include/helper.hpp').write_text('int helper(int);\n')
    (root/'src/entry.cpp').write_text('#include "helper.hpp"\nint helper(int x){return x+1;}\nint entry(){return helper(2);}\n')
    (root/'.ctxpp.toml').write_text('version=1\nsources=["src/**/*.cpp","include/**/*.hpp"]\ncompilation_database="compile_commands.json"\ntokenizer="estimate"\n')
    command = {'directory':str(root),'file':str(root/'src/entry.cpp'),
               'arguments':[compiler,'-std=c++17','-I'+str(root/'include'),'-c',str(root/'src/entry.cpp')]}
    (root/'compile_commands.json').write_text(json.dumps([command]))
    git(root,'init','-b','main'); git(root,'config','user.name','Fixture'); git(root,'config','user.email','test@example.invalid')
    git(root,'add','.'); git(root,'commit','-m','genuine compiler fixture')
    identities = {str(p):digest(p.read_bytes()) for p in (core,executable,skill/'scripts/ctxpp_lib.py',skill/'scripts/ctxpp_recipe.py',Path(compiler).resolve())}
    argv=[str(executable),'--root',str(root),'--json','scan','.']
    produced=subprocess.run(argv,capture_output=True,text=True,timeout=60)
    assert produced.returncode==0, produced.stdout+produced.stderr
    index=root/'.ctxpp/index.jsonl'; manifest=root/'.ctxpp/manifest.json'
    records=[json.loads(line) for line in index.read_text().splitlines()]
    meta=next(r for r in records if r['record']=='meta')
    native_calls=[r for r in records if r['record']=='edge' and r.get('type')=='call']
    assert meta['semantic'] and native_calls
    assert json.loads(manifest.read_text())['index_hash']==digest(index.read_bytes())
    config = ProjectControlConfig(workspaces={'native':WorkspaceConfig(authority_repository='source',repositories={'source':RepositoryConfig(root=root)})})
    snapshot=ProjectSnapshot(workspace_id='native',project_uuid='fixture-native-compiler',todo_revision=1,
        observed_at='2026-10-04T00:00:00Z',repositories={'source':RepositoryIdentity(commit=git(root,'rev-parse','HEAD'),dirty=True)})
    observations=[]
    def compiler_inputs(obs,current_meta):
        cfg,_=load_config(root)
        command_now=json.loads((root/'compile_commands.json').read_text())[0]
        args=translate_recipe(command_now,root/'src/entry.cpp')['clang_argv']
        command_hash=hashlib.sha256(stable_json({'directory':command_now['directory'],'args':args}).encode()).hexdigest()
        facts=[r for r in records if r['record']=='file' and r['path']=='src/entry.cpp'][0]
        known=(current_meta['config_hash']==hashlib.sha256(stable_json(cfg).encode()).hexdigest()
               and command_hash in facts['command_hashes']
               and all(digest(Path(p).read_bytes())==h for p,h in identities.items()))
        observations.append({'normalized_config_agrees':known,'normalized_command_hash':command_hash,
                             'runtime_library_and_system_headers':'unverified'})
        # A newly generated native artifact is not proof of every toolchain
        # environment input. Keep those dimensions unknown instead of asserting
        # config-file equality establishes full compiler freshness.
        return {'verified':False,'inputs':[
            {'kind':'configuration','key':'normalized-ctxpp-config','digest':digest(cfg)},
            {'kind':'configuration','key':'normalized-compile-command','digest':command_hash},
            {'kind':'environment','key':'fixture-unverified-runtime-dimensions','digest':digest(observations[-1])},
            *[{'kind':'provider','key':p,'digest':h} for p,h in identities.items()]]}
    provider=CtxppExportProvider(compiler_inputs)
    service=TraceService(config,lambda p:snapshot,ContextHost('observer','fixture-native',frozenset({'native'})),
                         semantic_provider=lambda p:{'project_uuid':snapshot.project_uuid,'project_revision':1,'declarations':[]},providers=(provider,))
    helper=next(r for r in records if r['record']=='symbol' and r.get('name')=='helper')
    result=service(project='native',targets=[{'kind':'symbol','id':helper['id']}])
    imported_calls=[u['edge'] for u in result['unknown_scope'] if u.get('edge',{}).get('provider')==provider.name and u['edge']['relation']=='calls']
    assert imported_calls and all(e['resolution']=='stale' for e in imported_calls)
    assert any(e['witness']['byte_start']==native_calls[0]['start'] for e in imported_calls)
    assert observations[0]['normalized_config_agrees']
    assert not result['coverage']['complete_graph_cut']
    assert next(p for p in result['coverage']['providers'] if p['provider']==provider.name)['freshness']=='unknown'
    evidence={'command':argv,'returncode':produced.returncode,'stdout':produced.stdout,'stderr':produced.stderr,
              'index_sha256':digest(index.read_bytes()),'manifest_sha256':digest(manifest.read_bytes()),
              'producer_identities':identities,'native_call_count':len(native_calls),'consumer_call_count':len(imported_calls),
              'input_observations':observations,'coverage':result['coverage'],'generation':result['generation']}
    (tmp_path/'genuine-ctxpp-evidence.json').write_text(json.dumps(evidence,indent=2))
    print('genuine-ctxpp-evidence='+str(tmp_path/'genuine-ctxpp-evidence.json'))
