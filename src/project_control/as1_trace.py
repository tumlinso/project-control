"""Source-bound dependency overlay; no observer builds or inferred grep calls.

Providers publish typed fragments through trusted startup ports. Syntax fragments
are disposable; declarations remain in Todo. See docs/as1-trace.md for coverage.
"""
from __future__ import annotations

import ast
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import os
import stat
import threading
import time
import tomllib
from typing import Any, Callable, Protocol

from .adapters.ctxpp import CtxppReadAdapter
from .adapters.git import GitReadAdapter
from .as1_context import ContextHost, InformationService
from .as1_contracts import EntityReference, ImpactEdge, ProviderInput, relative_path
from .config import DEFAULT_DENY_PATTERNS, ProjectControlConfig
from .graph import ProjectGraph
from .reconcile import ProjectReconciler
from .registry import WorkspaceRegistry
from .security import is_denied, redact_text


def digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def node_key(node: dict) -> str:
    """Internal index key includes repository identity, never a public selector."""
    return digest([node[k] for k in ('project_uuid', 'repository', 'kind', 'id')])


def edge_key(edge: dict) -> str:
    return digest({k: edge[k] for k in ('source', 'target', 'relation', 'origin', 'provider')})


@dataclass
class ProviderFragment:
    provider: str
    generation: str
    inputs: list[dict]
    edges: list[dict] = field(default_factory=list)
    nodes: list[dict] = field(default_factory=list)
    relations: tuple[str, ...] = ()
    complete: bool = False
    freshness: str = 'unknown'
    gaps: list[str] = field(default_factory=list)


class TraceProvider(Protocol):
    """Installed read-only port: detect, full input observation, atomic refresh.

    Inputs include every resolution determinant, not just edge source files.
    observe must reconcile additions/deletions and report watcher loss. File
    inputs use repository-relative keys; all other digests are producer-owned.
    """
    name: str
    def detect(self, observation: dict) -> bool: ...
    def observe(self, observation: dict) -> list[dict]: ...
    def refresh(self, observation: dict, inputs: list[dict]) -> ProviderFragment: ...


class CtxppExportProvider:
    """Read existing CTXPP-INDEX/1 exports, never invoke a compiler.

    config_observer is an installed pure input port returning verified and
    typed inputs. It uses the installed producer's normalization for config,
    command recipes, core/environment and generated inputs. Without this port
    the available export remains unknown/stale, never semantic completeness.
    """
    name = 'ctxpp-export/1'

    def __init__(self, config_observer: Callable | None = None):
        self.config_observer = config_observer
        self._cache = {}
        self._observations = {}

    def detect(self, obs):
        return (obs['root'] / '.ctxpp/index.jsonl').is_file()

    def _bytes(self, obs, path):
        relative_path(path)
        if is_denied(Path(path), obs['deny']): raise PermissionError('denied_ctxpp_input')
        signature = TraceService._file_signature(obs['root'], path)
        key = (str(obs['root']), path)
        old = self._cache.get(key)
        if old and old[0] == signature: return old[1]
        raw = InformationService._working_bytes(obs['root'], path)
        self._cache[key] = (signature, raw)
        return raw

    def observe(self, obs):
        raw = self._bytes(obs, '.ctxpp/index.jsonl')
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]
        meta = next((r for r in records if r.get('record') == 'meta'), {})
        if meta.get('format') != 'CTXPP-INDEX/1': raise ValueError('unsupported_ctxpp_export')
        manifest = json.loads(self._bytes(obs, '.ctxpp/manifest.json'))
        if manifest.get('format') != 'CTXPP-MANIFEST/1' or manifest.get('index_hash') != digest(raw):
            raise ValueError('ctxpp_manifest_mismatch')
        inputs = [{'kind':'provider', 'key':self.name, 'digest':digest([meta.get('tool_version'),meta.get('core_version'),meta.get('backend')])},
                  {'kind':'file', 'key':'.ctxpp/index.jsonl', 'digest':digest(raw)},
                  {'kind':'file', 'key':'.ctxpp/manifest.json', 'digest':digest(self._bytes(obs, '.ctxpp/manifest.json'))},
                  {'kind':'directory_membership', 'key':obs['alias'], 'digest':digest(obs['membership'])}]
        freshness = 'current'
        for record in records:
            if record.get('record') == 'file':
                path = relative_path(record['path'])
                actual = digest(self._bytes(obs, path))
                if manifest.get('files', {}).get(path) != record.get('hash') or actual != record.get('hash'):
                    freshness = 'stale'
                inputs.append({'kind':'file', 'key':path, 'digest':actual})
        verified = False
        if self.config_observer:
            config = self.config_observer(obs, deepcopy(meta))
            inputs.extend(ProviderInput.model_validate(i).model_dump() for i in config['inputs'])
            verified = config.get('verified') is True
        if not verified and freshness != 'stale': freshness = 'unknown'
        inputs.append({'kind':'configuration', 'key':'ctxpp-verification-state', 'digest':digest([verified,freshness])})
        self._observations[(obs['project'],obs['repository'])] = (records, meta, freshness)
        return inputs

    def refresh(self, obs, inputs):
        records, meta, freshness = self._observations[(obs['project'],obs['repository'])]
        generation = digest(inputs)
        symbols, nodes, edges = {}, [], []
        gaps = ['compiler_export_scope_only;dynamic_dispatch_unknown']
        if freshness != 'current': gaps.append('compiler_configuration_or_source_unverified')
        if not meta.get('semantic') or meta.get('incomplete') or meta.get('failures'):
            freshness = 'unknown'; gaps.append('compiler_export_incomplete_or_degraded')
        for record in records:
            if record.get('record') == 'file': nodes.append(TraceService._node(obs, 'file', record['path'], record['path']))
            elif record.get('record') == 'symbol' and not record.get('degraded'):
                node = TraceService._node(obs, 'symbol', record['id'], record['file'])
                nodes.append(node); symbols[record['id']] = node
        for record in records:
            if record.get('record') == 'edge' and record.get('type') == 'call':
                source, target = symbols.get(record.get('from')), symbols.get(record.get('to'))
                if not source or not target:
                    gaps.append('unresolved_compiler_symbol'); continue
                edges.append(TraceService._edge(source,target,'calls',self.name,generation,inputs,
                    witness={'path':record['file'],'byte_start':record.get('start'),'byte_end':record.get('end'),
                             'translation_unit':record.get('translation_unit'),'configuration_hash':record.get('configuration_hash'),
                             'producer':meta.get('backend')}))
            elif record.get('record') == 'file':
                source = TraceService._node(obs,'file',record['path'],record['path'])
                for included in record.get('includes', []):
                    try: target = TraceService._node(obs,'file',included,included)
                    except ValueError:
                        gaps.append('external_include_unresolved'); continue
                    edges.append(TraceService._edge(source,target,'includes',self.name,generation,inputs,
                        resolution='resolved' if included in obs['membership'] else 'unresolved',
                        witness={'path':record['path'],'producer':meta.get('backend'),'source_range':'not_retained_by_native_include_export'}))
        return ProviderFragment(self.name,generation,inputs,edges,nodes,('calls','includes'),False,freshness,gaps)


@dataclass
class _RepositoryCache:
    identity: tuple = ()
    membership: tuple[str, ...] = ()
    fragments: dict[str, dict] = field(default_factory=dict)
    signatures: dict[str, tuple] = field(default_factory=dict)


class TraceService:
    """Callable InformationService impact port with trusted project scope.

    One lock publishes whole generations. Retained generations support exact
    deltas and refresh-required cursors, never a mutable partial graph view.
    """
    def __init__(self, config: ProjectControlConfig, snapshot_provider: Callable,
                 host: ContextHost, *, semantic_provider: Callable | None = None,
                 providers: tuple[TraceProvider, ...] = (), max_files: int = 20000):
        self.config, self.snapshots, self.host = config, snapshot_provider, host
        self.registry = WorkspaceRegistry(config)
        self.semantic = semantic_provider
        self.providers = providers
        if len({p.name for p in providers}) != len(providers):
            raise ValueError('duplicate_trace_provider')
        self.max_files = max_files
        self._repos: dict[tuple, _RepositoryCache] = {}
        self._providers: dict[tuple, tuple[str, ProviderFragment]] = {}
        self._generations: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._input_cache = {}
        self.read_count = 0

    def _read(self, root: Path, path: str) -> bytes:
        relative_path(path)
        self.read_count += 1
        return InformationService._working_bytes(root, path)

    def _repository(self, project: str, alias: str, snapshot, *, force=False) -> dict:
        repo = self.registry.repository(project, alias)
        git = GitReadAdapter(repo.root)
        deny = [*DEFAULT_DENY_PATTERNS, *self.registry.workspace(project).deny_patterns]
        identity = git.identity()
        # Git membership handles tracked renames, deletions and untracked files;
        # stat identities are invalidation hints only, exact bytes bind refreshes.
        paths = sorted(set(git.tracked_files()) | set(git._git('ls-files', '-z', '--others', '--exclude-standard').split('\x00')) - {''})
        paths = [p for p in paths if not is_denied(Path(p), deny)]
        gaps = []
        if len(paths) > self.max_files:
            gaps.append('file_budget_unindexed_membership')
            paths = paths[:self.max_files]
        cache = self._repos.setdefault((project, alias), _RepositoryCache())
        token = (identity.commit, identity.status_fingerprint)
        membership = tuple(paths)
        retained = {}
        signatures = {}
        for path in paths:
            try:
                relative_path(path)
                signature = self._file_signature(repo.root, path)
            except (OSError, ValueError) as exc:
                if path.endswith(('.py', '.md', '.markdown', '.toml', '.json')):
                    gaps.append('unavailable_source:' + path + ':' + type(exc).__name__)
                continue
            signatures[path] = signature
            supported = path.endswith(('.py', '.md', '.markdown')) or path in {'pyproject.toml', 'package.json', 'Cargo.toml'}
            if not supported:
                if path.endswith(('.cpp', '.cc', '.h', '.hpp', '.cu', '.ts', '.rs', '.go', '.java', '.cs')):
                    gaps.append('installed_semantic_export_required:' + path)
                continue
            previous = cache.fragments.get(path)
            if not force and previous and cache.signatures.get(path) == signature:
                retained[path] = previous
                continue
            try:
                raw = self._read(repo.root, path)
                text = raw.decode('utf-8')
                parsed = self._parse(path, text)
                retained[path] = {'digest': digest(raw), **parsed}
            except (OSError, ValueError, UnicodeError, SyntaxError) as exc:
                gaps.append('unavailable_source:' + path + ':' + type(exc).__name__)
        # Detect Git mutations during reconciliation; never advertise an atomic
        # observation when a repository changed while fragments were read.
        after = git.identity()
        stable = token == (after.commit, after.status_fingerprint)
        if not stable:
            gaps.append('repository_changed_during_refresh')
        cache.identity, cache.membership = token, membership
        cache.fragments, cache.signatures = retained, signatures
        repository = alias + '@' + digest(str(git.common_dir()))[:24]
        uuid = snapshot.project_uuid
        if not uuid:
            raise ValueError('project_uuid_required')
        inputs = [{'kind': 'provider', 'key': 'syntax/1', 'digest': digest('syntax/1')},
                  {'kind': 'directory_membership', 'key': alias, 'digest': digest(membership)},
                  {'kind': 'configuration', 'key': alias, 'digest': digest([(p, f['digest']) for p, f in sorted(retained.items()) if p.endswith(('.toml', '.json'))])},
                  {'kind': 'exports', 'key': alias, 'digest': digest([(p, f.get('definitions', [])) for p, f in sorted(retained.items()) if p.endswith('.py')])}]
        return {'project': project, 'project_uuid': uuid, 'repository': repository,
                'alias': alias, 'root': repo.root, 'deny': deny, 'git': git,
                'commit': identity.commit, 'working_tree_fingerprint': identity.status_fingerprint,
                'fragments': retained, 'membership': membership, 'inputs': inputs,
                'freshness': 'current' if stable else 'stale', 'gaps': gaps,
                'observed_at': snapshot.observed_at, 'semantic_revision': snapshot.todo_revision}

    @staticmethod
    def _parse(path: str, text: str) -> dict:
        refs, definitions, gaps = [], [], []
        if path.endswith('.py'):
            tree = ast.parse(text)
            for item in ast.walk(tree):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    definitions.append(item.name)
                if isinstance(item, ast.Import):
                    refs.extend({'module': a.name, 'level': 0, 'relation': 'imports', 'line': item.lineno} for a in item.names)
                elif isinstance(item, ast.ImportFrom):
                    refs.append({'module': item.module or '', 'level': item.level, 'names': [a.name for a in item.names], 'relation': 'imports', 'line': item.lineno})
                elif isinstance(item, ast.Call) and (isinstance(item.func, ast.Name) and item.func.id in {'__import__', 'eval', 'exec', 'getattr'} or isinstance(item.func, ast.Attribute) and item.func.attr == 'import_module'):
                    gaps.append('dynamic_python_reference')
            gaps.append('syntax_imports_not_dynamic_dispatch_or_call_graph')
        elif path.endswith(('.md', '.markdown')):
            for line, value in enumerate(text.splitlines(), 1):
                for match in re.finditer(r'\[[^\]]*\]\(([^\s)]+)\)', value):
                    target = match.group(1).split('#', 1)[0]
                    if target and '://' not in target:
                        refs.append({'path': target, 'relation': 'mentions', 'line': line})
        else:
            data = json.loads(text) if path.endswith('.json') else tomllib.loads(text)
            deps = data.get('dependencies', {}) if path == 'package.json' else data.get('dependencies', {}) if path == 'Cargo.toml' else data.get('project', {}).get('dependencies', [])
            for dep in deps:
                refs.append({'package': str(dep), 'relation': 'package_dependency', 'line': 1})
            gaps.append('manifest_requires_registered_package_versions;unsupported_build_constructs')
        return {'refs': refs, 'definitions': sorted(definitions), 'gaps': gaps}

    @staticmethod
    def _node(obs: dict, kind: str, entity: str, path: str | None = None) -> dict:
        value = {'project_uuid': obs['project_uuid'], 'repository': obs['repository'], 'kind': kind, 'id': entity}
        if path is not None:
            value['path'] = relative_path(path)
        return EntityReference.model_validate(value).model_dump(exclude_none=True)

    @staticmethod
    def _edge(source, target, relation, provider, generation, inputs, *, origin='observed', resolution='resolved', witness=None) -> dict:
        return ImpactEdge(source=source, target=target, relation=relation, origin=origin,
                          provider=provider, generation=generation, input_manifest=inputs,
                          resolution=resolution, witness=witness).model_dump(exclude_none=True)

    def _syntax(self, obs) -> ProviderFragment:
        generation = digest([obs['inputs'], [(p, f['digest']) for p, f in sorted(obs['fragments'].items())]])
        nodes, edges, gaps = [], [], list(obs['gaps'])
        fragments = obs['fragments']
        modules = {}
        for path in fragments:
            if path.endswith('.py'):
                name = path[:-3].replace('/', '.')
                if name.endswith('.__init__'): name = name[:-9]
                modules[name] = path
                if name.startswith('src.'): modules[name[4:]] = path
        for path, fragment in sorted(fragments.items()):
            source = self._node(obs, 'file', path, path)
            nodes.append(source)
            inputs = [*obs['inputs'], {'kind': 'file', 'key': path, 'digest': fragment['digest']}]
            gaps.extend(path + ':' + g for g in fragment['gaps'])
            for ref in fragment['refs']:
                resolution = 'resolved'
                relation = ref['relation']
                target_path = None
                if relation == 'imports':
                    module = ref['module']
                    if ref['level']:
                        package = path[:-3].split('/') if path.endswith('/__init__.py') else path.split('/')[:-1]
                        if path.endswith('/__init__.py'): package = package[:-1]
                        trim = ref['level'] - 1
                        if trim >= len(package):
                            module = '<invalid-relative>.' + module
                        else:
                            module = '.'.join(package[:len(package)-trim] + ([module] if module else []))
                    candidates = [module]
                    if ref.get('names'):
                        candidates.extend(module + '.' + n for n in ref['names'] if module + '.' + n in modules)
                    if not ref['module'] and ref.get('names'):
                        candidates = [module + '.' + n for n in ref['names']]
                    for candidate in candidates:
                        target_path = modules.get(candidate)
                        target = self._node(obs, 'file', target_path, target_path) if target_path else self._node(obs, 'module', candidate)
                        local_inputs = [*inputs]
                        if target_path:
                            local_inputs.append({'kind': 'file', 'key': target_path, 'digest': fragments[target_path]['digest']})
                        edges.append(self._edge(source, target, relation, 'python-syntax/1', generation, local_inputs,
                                                resolution='resolved' if target_path else 'unresolved',
                                                witness={'path': path, 'start_line': ref['line'], 'end_line': ref['line'], 'content_sha256': fragment['digest'], 'commit': obs['commit']}))
                    continue
                elif relation == 'mentions':
                    # Normalize relative links without ever following symlinks.
                    parts = list(Path(path).parent.parts)
                    for part in ref['path'].split('/'):
                        if part == '..':
                            if not parts: resolution = 'unresolved'; break
                            parts.pop()
                        elif part not in {'', '.'}: parts.append(part)
                    candidate = '/'.join(parts)
                    try: relative_path(candidate)
                    except ValueError: resolution = 'unresolved'
                    if candidate not in obs['membership']: resolution = 'unresolved'
                    target = self._node(obs, 'file', candidate) if resolution != 'resolved' else self._node(obs, 'file', candidate, candidate)
                else:
                    target = self._node(obs, 'package', ref['package'])
                    resolution = 'unresolved'
                edges.append(self._edge(source, target, relation, 'markdown-references/1' if relation == 'mentions' else 'manifest/1', generation, inputs,
                                        resolution=resolution, witness={'path': path, 'start_line': ref['line'], 'end_line': ref['line'], 'content_sha256': fragment['digest']}))
        return ProviderFragment('syntax/1', generation, obs['inputs'], edges, nodes,
                                ('imports', 'mentions', 'package_dependency'), False, obs['freshness'], sorted(set(gaps)))

    @staticmethod
    def _file_signature(root, path):
        """Parent and leaf identities; cached bytes never authorize symlinks."""
        relative_path(path)
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        values = []
        try:
            parts = path.split('/')
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor); descriptor = child
                st = os.fstat(descriptor)
                values.append((st.st_dev, st.st_ino, st.st_ctime_ns))
            st = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode): raise ValueError('not_a_regular_provider_input')
            values.append((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns))
            return tuple(values)
        finally: os.close(descriptor)

    def _installed(self, provider: TraceProvider, obs: dict) -> ProviderFragment:
        context = obs.get('semantic_context', {})
        options = {}
        for record in context.get('declarations', []):
            if record.get('kind') == 'provider' and not record.get('retired') and record.get('origin') == 'project_declared' and record['payload'].get('provider_id') == provider.name:
                options.update(record['payload'].get('options', {}))
        if set(options) - set(getattr(provider, 'supported_options', ())):
            raise ValueError('unsupported_registered_provider_options')
        obs = {**obs, 'provider_options': options}
        inputs = [ProviderInput.model_validate(i).model_dump() for i in provider.observe(obs)]
        if context:
            inputs += [{'kind':'registry', 'key':obs['project'], 'digest':digest(context['declarations'])},
                       {'kind':'semantic_revision', 'key':obs['project'], 'digest':digest(context['project_revision'])}]
        # File determinants are independently checked against registered bytes.
        # Non-file determinants belong to this trusted installed producer port.
        for item in inputs:
            if item['kind'] == 'file':
                if is_denied(Path(relative_path(item['key'])), obs['deny']): raise PermissionError('denied_provider_input')
                signature = self._file_signature(obs['root'], item['key'])
                cache_key = (str(obs['root']), item['key'])
                previous = self._input_cache.get(cache_key)
                actual = previous[1] if previous and previous[0] == signature else digest(self._read(obs['root'], item['key']))
                self._input_cache[cache_key] = (signature, actual)
                if actual != item['digest']:
                    raise ValueError('stale_provider_input')
        stamp = digest([provider.name, inputs, obs['commit'], obs['working_tree_fingerprint'], obs['inputs']])
        key = (obs['project'], obs['repository'], provider.name)
        old = self._providers.get(key)
        if old and old[0] == stamp:
            return deepcopy(old[1])
        value = deepcopy(provider.refresh(obs, deepcopy(inputs)))
        if value.provider != provider.name or value.inputs != inputs or not value.generation:
            raise ValueError('provider_fragment_identity_mismatch')
        value.nodes = [EntityReference.model_validate(n).model_dump(exclude_none=True) for n in value.nodes]
        value.edges = [ImpactEdge.model_validate(e).model_dump(exclude_none=True) for e in value.edges]
        for edge in value.edges:
            if edge['source']['project_uuid'] != obs['project_uuid'] or edge['source']['repository'] != obs['repository']:
                raise ValueError('provider_source_scope_mismatch')
            if edge['provider'] != provider.name or edge['generation'] != value.generation or edge['input_manifest'] != inputs:
                raise ValueError('provider_edge_identity_mismatch')
            if edge['relation'] not in value.relations:
                raise ValueError('unsupported_provider_relation')
        # Replace a fully validated fragment, never append stale predecessor rows.
        self._providers[key] = (stamp, deepcopy(value))
        return value

    def _todo(self, obs, snapshot) -> ProviderFragment:
        graph = ProjectGraph(snapshot, ProjectReconciler(snapshot).reconcile())
        inputs = [{'kind': 'semantic_revision', 'key': obs['project'], 'digest': digest(snapshot.todo_revision)}]
        generation = digest([inputs, graph.edges])
        nodes = {k: self._node(obs, n['type'], n['id'], n['record'].get('path') if n['type'] == 'path' else None) for k, n in graph.entities.items()}
        edges = [self._edge(nodes[e['source']], nodes[e['target']], e['relation'], 'todo/1', generation, inputs,
                            origin='project_declared', witness={'basis': e['basis'], 'revision': snapshot.todo_revision}) for e in graph.edges]
        return ProviderFragment('todo/1', generation, inputs, edges, list(nodes.values()), tuple(sorted({e['relation'] for e in edges})), True, 'current', ['workflow_declarations_do_not_prove_source_calls'])

    def _declarations(self, observations, unknown):
        """Consume canonical Service.project_context declarations, never SQLite."""
        if self.semantic is None:
            unknown.append({'reason': 'semantic_registration_provider_unavailable'})
            return []
        by_identity = {}
        for obs in observations:
            for identity in (obs['repository'], obs['alias'], str(obs['root'])):
                by_identity[(obs['project_uuid'], identity)] = obs
        result = []
        contexts = {}
        for project in sorted({o['project'] for o in observations}):
            obs = next(o for o in observations if o['project'] == project)
            try:
                context = deepcopy(self.semantic(project))
                if context.get('project_uuid') != obs['project_uuid'] or context.get('project_revision') != obs['semantic_revision']:
                    raise ValueError('semantic_owner_or_revision_mismatch')
                contexts[project] = context
                for repository_obs in observations:
                    if repository_obs['project'] == project: repository_obs['semantic_context'] = context
            except Exception as exc:
                unknown.append({'project': project, 'reason': 'semantic_registration_unavailable:' + type(exc).__name__})
        for project in sorted(contexts):
            owned = [o for o in observations if o['project'] == project]
            obs = next((o for o in owned if o['alias'] == self.registry.workspace(project).authority_repository), owned[0])
            try:
                context = contexts[project]
                declarations = context.get('declarations', [])
                inputs = [{'kind': 'semantic_revision', 'key': project, 'digest': digest(context['project_revision'])},
                          {'kind': 'registry', 'key': project, 'digest': digest(declarations)}]
                generation = digest(inputs)
                nodes, edges, gaps = [], [], []
                for record in declarations:
                    if record.get('retired') or record.get('origin') != 'project_declared': continue
                    payload = record['payload']
                    if record['kind'] == 'identity':
                        repo = by_identity.get((obs['project_uuid'], payload.get('repository')))
                        if repo and payload.get('version'):
                            node = self._node(repo, 'identity', payload['id'])
                            nodes.append(node)
                def resolve(ref):
                    value = EntityReference.model_validate(ref).model_dump(exclude_none=True)
                    registered = by_identity.get((value['project_uuid'], value['repository']))
                    if registered: value['repository'] = registered['repository']
                    return value, registered
                for record in declarations:
                    if record.get('retired'): continue
                    payload = record['payload']
                    kind = record['kind']
                    origin = record.get('origin')
                    if origin not in {'project_declared', 'candidate'}: continue
                    if kind in {'relation', 'candidate_relation'}:
                        source, source_repo = resolve(payload['source'])
                        target, target_repo = resolve(payload['target'])
                        resolution = 'resolved'
                        if source['project_uuid'] != obs['project_uuid']:
                            gaps.append('declaration_source_owner_mismatch:' + record['id']); continue
                        if not source_repo or not target_repo: resolution = 'unresolved'
                        # Exact identities carry source environment/version facts;
                        # version selectors are never matched by a name alone.
                        edge_inputs = list(inputs)
                        if target['kind'] == 'identity':
                            target_context = contexts.get(target_repo['project'], {}) if target_repo else {}
                            if target_context.get('project_uuid') != target['project_uuid']:
                                resolution = 'unresolved'
                            if target_repo and target_context:
                                edge_inputs += [{'kind': 'semantic_revision', 'key': target_repo['project'], 'digest': digest(target_context['project_revision'])},
                                                {'kind': 'registry', 'key': target_repo['project'], 'digest': digest(target_context['declarations'])}]
                            target_versions = [str(r['payload'].get('version')) for r in target_context.get('declarations', [])
                                               if r.get('kind') == 'identity' and not r.get('retired') and r.get('origin') == 'project_declared'
                                               and r['payload'].get('id') == target['id']
                                               and by_identity.get((target['project_uuid'], r['payload'].get('repository'))) == target_repo]
                            if str(payload.get('target_version')) not in target_versions:
                                resolution = 'unresolved'; gaps.append('identity_version_unresolved:' + record['id'])
                        relation = 'depends_on' if payload['relation'] == 'uses' else payload['relation']
                        edges.append(self._edge(source, target, relation, 'semantic-registration/1', generation, edge_inputs,
                                                origin='candidate' if origin == 'candidate' or kind == 'candidate_relation' else 'project_declared',
                                                resolution=resolution, witness={'declaration': record['id'], 'registration_version': record.get('version'), 'target_version': payload.get('target_version')}))
                    elif kind == 'generation':
                        for anchor in payload.get('inputs', []):
                            anchor_obs = next((o for o in observations if anchor['project'] in {o['project'], o['project_uuid']} and anchor['repository'] in {o['repository'], o['alias'], str(o['root'])}), None)
                            if not anchor_obs:
                                gaps.append('generation_input_inaccessible:' + record['id']); continue
                            path = relative_path(anchor['path'])
                            if is_denied(Path(path), anchor_obs['deny']):
                                gaps.append('generation_input_denied:' + record['id']); continue
                            try: current = digest(self._read(anchor_obs['root'], path))
                            except (OSError, ValueError): current = None
                            status = 'resolved' if current == anchor['content_sha256'] else 'stale'
                            target = self._node(anchor_obs, 'file', path, path)
                            for root in payload.get('roots', []):
                                relative_path(root)
                                for output in obs['membership']:
                                    if output == root or output.startswith(root.rstrip('/') + '/'):
                                        source = self._node(obs, 'file', output, output)
                                        edges.append(self._edge(source, target, 'generated_from', 'semantic-registration/1', generation,
                                                               [*inputs, {'kind': 'file', 'key': path, 'digest': anchor['content_sha256']}],
                                                               origin='project_declared', resolution=status,
                                                               witness={'declaration': record['id'], 'generator': payload.get('generator')}))
                    elif kind not in {'identity', 'provider'}:
                        for anchor in payload.get('anchors', []):
                            source = self._node(obs, 'semantic_note', record['id'])
                            nodes.append(source)
                            path = relative_path(anchor['path'])
                            f = obs['fragments'].get(path)
                            if not f or f['digest'] != anchor['content_sha256']:
                                gaps.append('needs_review:' + record['id'])
                result.append((obs, ProviderFragment('semantic-registration/1', generation, inputs, edges, nodes,
                                                     tuple(sorted({e['relation'] for e in edges})), not gaps, 'current', gaps)))
            except Exception as exc:
                unknown.append({'project': project, 'reason': 'semantic_registration_unavailable:' + type(exc).__name__})
        return result

    def _build(self, project: str, watcher_lost: bool) -> dict:
        observations, fragments, unknown = [], [], []
        if watcher_lost:
            self._providers.clear()
            self._input_cache.clear()
        # Only explicitly host-permitted registered projects participate. Names
        # and program membership never create cross-project dependency edges.
        for current in sorted(self.host.projects & self.config.workspaces.keys()):
            try:
                snapshot = self.snapshots(current)
                for alias in sorted(self.registry.workspace(current).repositories):
                    obs = self._repository(current, alias, snapshot, force=watcher_lost)
                    observations.append(obs)
                    fragments.extend([(obs, self._syntax(obs)), (obs, self._todo(obs, snapshot))])
            except Exception as exc:
                unknown.append({'project': current, 'reason': 'repository_unavailable:' + type(exc).__name__})
        fragments.extend(self._declarations(observations, unknown))
        for obs in observations:
            for provider in self.providers:
                try:
                    if provider.detect(obs): fragments.append((obs, self._installed(provider, obs)))
                except Exception as exc:
                    unknown.append({'project': obs['project'], 'repository': obs['repository'], 'provider': provider.name, 'reason': 'provider_unavailable:' + type(exc).__name__})
        # Producer reads can race with source and Todo updates. Bind all graph
        # segments to the recorded observations, report stale rather than mix.
        for obs in observations:
            try:
                final = obs['git'].identity()
                stale = (final.commit, final.status_fingerprint) != (obs['commit'], obs['working_tree_fingerprint'])
                stale |= self.snapshots(obs['project']).todo_revision != obs['semantic_revision']
                active_files = {i['key'] for o, f in fragments if o['root'] == obs['root'] for i in f.inputs if i['kind'] == 'file'}
                for (root, path), (signature, _) in self._input_cache.items():
                    if root == str(obs['root']) and path in active_files and self._file_signature(obs['root'], path) != signature: stale = True
                if stale:
                    obs['freshness'] = 'stale'
                    unknown.append({'project': obs['project'], 'repository': obs['repository'], 'reason': 'source_or_semantics_changed_during_producer_reads'})
            except Exception as exc:
                obs['freshness'] = 'stale'
                unknown.append({'project': obs['project'], 'repository': obs['repository'], 'reason': 'source_revalidation_unavailable:' + type(exc).__name__})
        nodes, edges, coverage, lookup = {}, {}, [], {}
        for obs in observations:
            lookup[(obs['project_uuid'], obs['repository'])] = obs
        for obs, fragment in fragments:
            coverage.append({'project': obs['project'], 'project_uuid': obs['project_uuid'], 'repository': obs['repository'],
                             'provider': fragment.provider, 'generation': fragment.generation, 'relations': list(fragment.relations),
                             'complete': fragment.complete, 'freshness': 'stale' if obs['freshness'] != 'current' else fragment.freshness,
                             'input_manifest': fragment.inputs, 'gaps': fragment.gaps})
            for node in fragment.nodes: nodes[node_key(node)] = node
            for edge in fragment.edges:
                edge = deepcopy(edge)
                # Foreign identities must be exact registered project+repository
                # identities. Inaccessible targets remain visible but untraversed.
                if any((n['project_uuid'], n['repository']) not in lookup for n in (edge['source'], edge['target'])):
                    edge['resolution'] = 'unresolved'
                    unknown.append({'provider': fragment.provider, 'reason': 'inaccessible_or_unregistered_relationship', 'edge': edge})
                if fragment.freshness != 'current' or any(lookup.get((n['project_uuid'], n['repository']), {}).get('freshness') != 'current' for n in (edge['source'], edge['target'])): edge['resolution'] = 'stale'
                for node in (edge['source'], edge['target']): nodes[node_key(node)] = node
                edges[edge_key(edge)] = edge
        if watcher_lost: unknown.append({'reason': 'watcher_loss_reconciled;installed_providers_must_reobserve_inputs'})
        generation = digest({'nodes': nodes, 'edges': edges, 'coverage': coverage, 'unknown': unknown,
                             'source': [(o['project_uuid'], o['repository'], o['commit'], o['working_tree_fingerprint']) for o in observations]})
        state = {'generation': generation, 'nodes': nodes, 'edges': edges, 'providers': coverage, 'unknown': unknown,
                 'repositories': [{'project': o['project'], 'project_uuid': o['project_uuid'], 'repository': o['repository'],
                                   'commit': o['commit'], 'working_tree_fingerprint': o['working_tree_fingerprint'], 'observed_at': o['observed_at']} for o in observations],
                 '_lookup': lookup}
        self._generations[generation] = state
        while len(self._generations) > 8: self._generations.pop(next(iter(self._generations)))
        return state

    def __call__(self, *, project: str, detail='compact', targets=None, mode='paths',
                 change_class='unknown', max_nodes=200, max_edges=2000, time_budget_ms=1000,
                 since=None, cursor=None, watcher_lost=False, query=None, max_payload_bytes=None) -> dict:
        if project not in self.host.projects: raise PermissionError('project_not_permitted')
        self.registry.workspace(project)
        if mode not in {'paths', 'snippets'}: raise ValueError('unsupported_trace_mode')
        if change_class not in {'body', 'interface', 'configuration', 'generator', 'removal', 'unknown'}: raise ValueError('unsupported_change_class')
        if not isinstance(targets, list) or not targets or len(targets) > 32: raise ValueError('trace_requires_1_to_32_targets')
        if not (1 <= max_nodes <= 10000 and 1 <= max_edges <= 100000 and 1 <= time_budget_ms <= 30000): raise ValueError('invalid_trace_budget')
        if max_payload_bytes is not None and (not isinstance(max_payload_bytes, int) or max_payload_bytes < 1024):
            raise ValueError('invalid_payload_budget')
        with self._lock:
            state = self._build(project, watcher_lost)
            result = self._trace(state, project, targets, mode, change_class, max_nodes, max_edges, time_budget_ms, since, cursor, query)
            return self._bound_result(result, max_payload_bytes) if max_payload_bytes is not None else result

    @staticmethod
    def _bound_result(result, budget):
        """Return a useful impact preview when its immutable packet would overflow."""
        wire = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        original_size = len(wire(result))
        if original_size <= budget:
            return result
        full_digest = digest(result)
        value = deepcopy(result)
        omitted = []
        full_manifest_count = 0
        omitted_manifest_count = 0
        manifest_digests = []

        def summarize_manifest(item):
            nonlocal full_manifest_count, omitted_manifest_count
            manifest = item.get('input_manifest') if isinstance(item, dict) else None
            if not isinstance(manifest, list): return
            full_manifest_count += len(manifest)
            manifest_digests.append(digest(manifest))
            if len(manifest) <= 8: return
            omitted_manifest_count += len(manifest) - 8
            kept = manifest[:8]
            item.update(input_manifest_count=len(manifest), input_manifest_sha256=digest(manifest),
                        input_manifest_omitted=len(manifest) - len(kept), input_manifest_complete=False,
                        input_manifest=kept)

        def summarize_edge(item):
            if not isinstance(item, dict): return
            summarize_manifest(item)
            nested = item.get('witness')
            if isinstance(nested, dict): summarize_edge(nested)

        for provider in value.get('coverage', {}).get('providers', []):
            manifest = provider.get('input_manifest', [])
            summarize_manifest(provider)
            gaps = provider.get('gaps', [])
            if isinstance(gaps, list) and len(gaps) > 8:
                provider.update(gaps_count=len(gaps), gaps_sha256=digest(gaps), gaps_omitted=len(gaps) - 8,
                                gaps=gaps[:8])
        for dependency in value.get('dependencies', []):
            for edge in dependency.get('witness_chain', []): summarize_edge(edge)
        for edge in value.get('nonpropagating_relations', []): summarize_edge(edge)

        def summarize_nested(item):
            if isinstance(item, dict):
                if isinstance(item.get('input_manifest'), list): summarize_manifest(item)
                for child in item.values(): summarize_nested(child)
            elif isinstance(item, list):
                for child in item: summarize_nested(child)

        # An unresolved provider edge can be repeated under unknown_scope, and
        # deltas can carry the same producer evidence. Compact those copies too.
        for section in ('unknown_scope', 'delta'):
            summarize_nested(value.get(section))
        for section in ('unknown_scope',):
            entries = value.get(section, [])
            if isinstance(entries, list) and len(entries) > 8:
                omitted.append({'section': section, 'full_count': len(entries), 'full_sha256': digest(result.get(section, [])),
                                'preview_count': 8})
                value[section] = entries[:8]
        for section in ('possible_related_candidates', 'nonpropagating_relations'):
            entries = value.get(section, [])
            if isinstance(entries, list) and len(entries) > 8:
                omitted.append({'section': section, 'full_count': len(entries), 'full_sha256': digest(result.get(section, [])),
                                'preview_count': 8})
                value[section] = entries[:8]

        if omitted_manifest_count:
            omitted.append({'section': 'input_manifests', 'full_count': full_manifest_count,
                            'full_sha256': digest(manifest_digests),
                            'omitted_count': omitted_manifest_count, 'preview_count': 'summarized'})

        # Keep complete witness chains for every retained dependency. If that
        # still exceeds the store limit, retain a stable prefix and identify
        # exactly how much of the original dependency list was left out.
        dependencies = value.get('dependencies', [])
        dependency_count, dependency_digest = len(dependencies), digest(result.get('dependencies', []))
        while dependencies and len(wire(value)) > budget:
            keep = max(0, len(dependencies) // 2)
            dependencies = dependencies[:keep]
            value['dependencies'] = dependencies
        if len(dependencies) < dependency_count:
            omitted.append({'section': 'dependencies', 'full_count': dependency_count,
                            'full_sha256': dependency_digest, 'preview_count': len(dependencies)})
            traversal = value.get('traversal', {})
            traversal['omitted_groups'] = [*traversal.get('omitted_groups', []),
                {'reason': 'packet_payload_budget', 'full_count': dependency_count,
                 'omitted_count': dependency_count - len(dependencies), 'full_sha256': dependency_digest}]
            value['coverage']['complete_graph_cut'] = False

        # If one retained witness alone cannot fit, preserve seed and coverage
        # orientation while reporting the witness omission instead of failing
        # packet creation.
        if len(wire(value)) > budget:
            old_dependencies = value.get('dependencies', [])
            if old_dependencies:
                omitted.append({'section': 'dependencies', 'full_count': dependency_count,
                                'full_sha256': dependency_digest, 'preview_count': 0})
                value['dependencies'] = []
                value['coverage']['complete_graph_cut'] = False
                value.setdefault('traversal', {})['omitted_groups'] = [
                    {'reason': 'packet_payload_budget', 'full_count': dependency_count,
                     'omitted_count': dependency_count, 'full_sha256': dependency_digest}]

        value['status'] = 'partial'
        value.setdefault('warnings', []).append('impact_payload_budget_preview')
        value['payload_budget'] = {'limit_bytes': budget, 'full_result_bytes': original_size,
                                   'full_result_sha256': full_digest, 'omissions': omitted}
        # Account for the summary itself. The store wrapper reserves 8 KiB for
        # packet status, freshness, and cursor metadata.
        if len(wire(value)) > budget:
            value['possible_related_candidates'] = []
            value['unknown_scope'] = []
            value['nonpropagating_relations'] = []
            for provider in value.get('coverage', {}).get('providers', []):
                provider.pop('gaps', None)
                provider.pop('input_manifest', None)
        if len(wire(value)) > budget:
            # A low-cap store can still persist an honest pointer to the trace.
            return {'status': 'partial', 'generation': result.get('generation'), 'seeds': [],
                    'dependencies': [], 'coverage': {'complete_graph_cut': False, 'providers': []},
                    'traversal': {'visited_nodes': 0, 'examined_edges': 0, 'omitted_groups': []},
                    'continuation': None, 'warnings': ['impact_payload_budget_preview'],
                    'payload_budget': {'limit_bytes': budget, 'full_result_bytes': original_size,
                                       'full_result_sha256': full_digest,
                                       'omissions': [{'section': 'trace', 'full_sha256': full_digest}]}}
        return value

    def _trace(self, state, project, targets, mode, change_class, max_nodes, max_edges, time_budget_ms, since, cursor, query):
        unknown = deepcopy(state['unknown'])
        nodes, edges = state['nodes'], state['edges']
        request = {'project': project, 'targets': targets, 'mode': mode, 'change_class': change_class, 'query': query}
        if cursor and (cursor.get('generation') != state['generation'] or cursor.get('request') != digest(request)):
            return {'status': 'partial', 'generation': state['generation'], 'warnings': ['trace_refresh_required'], 'refresh_required': True}
        seeds = []
        for target in targets:
            if isinstance(target, str): target = {'path': relative_path(target)}
            if not isinstance(target, dict) or set(target) - {'project', 'repository', 'kind', 'id', 'path'}: raise ValueError('invalid_trace_target')
            matches = []
            for key, node in nodes.items():
                obs = state['_lookup'].get((node['project_uuid'], node['repository']))
                if not obs or obs['project'] != target.get('project', project): continue
                if target.get('repository') and target['repository'] not in {obs['alias'], obs['repository']}: continue
                if all(node.get(k) == target[k] for k in ('kind', 'id', 'path') if k in target): matches.append(key)
            if len(matches) == 1: seeds.append(matches[0])
            elif matches and 'path' in target and not any(k in target for k in ('kind', 'id')) and len({nodes[k]['repository'] for k in matches}) == 1:
                seeds.extend(matches)
            else: unknown.append({'target': target, 'reason': 'ambiguous_target' if matches else 'target_not_indexed', 'count': len(matches)})
        # Reverse consumer->provider relations; generation has explicit forward
        # producer->output direction. Mentions/ownership/containment never expand.
        reverse = {'depends_on', 'imports', 'includes', 'calls', 'consumes', 'build_input', 'package_dependency', 'generated_from'}
        forward = {'generates', 'produces_output'}
        adjacency = defaultdict(list)
        halo = []
        for key, edge in sorted(edges.items()):
            a, b = node_key(edge['source']), node_key(edge['target'])
            if edge['origin'] == 'candidate' or edge['relation'] not in reverse | forward:
                halo.append(edge); continue
            if edge['resolution'] != 'resolved':
                unknown.append({'reason': 'edge_' + edge['resolution'], 'edge': edge}); continue
            start, end = (b, a) if edge['relation'] in reverse else (a, b)
            adjacency[start].append((end, key))
        queue = deque((s, []) for s in sorted(set(seeds)))
        visited, results, traversed = set(seeds), [], 0
        deadline = time.monotonic() + time_budget_ms / 1000
        cycles, omitted = 0, []
        while queue:
            current, chain = queue.popleft()
            neighbors = adjacency[current]
            for index, (end, edge_id) in enumerate(neighbors):
                if traversed >= max_edges or len(results) >= max_nodes or time.monotonic() >= deadline:
                    omitted = [{'node': nodes[current], 'remaining_edges': len(neighbors)-index}, *[{'node': nodes[k], 'remaining_edges': len(adjacency[k])} for k, _ in queue]]
                    queue.clear(); break
                traversed += 1
                if end in visited: cycles += 1; continue
                visited.add(end)
                witness = chain + [edges[edge_id]]
                result = {'node': nodes[end], 'witness_chain': witness, 'attention_basis': 'observed_or_declared_dependency', 'fanout': len(adjacency[end])}
                if mode == 'snippets': result['snippet'] = self._snippet(state, nodes[end], witness)
                results.append(result); queue.append((end, witness))
        delta = None
        if since:
            older = self._generations.get(since)
            if not older: delta = {'status': 'refresh_required', 'reason': 'generation_not_retained'}
            else:
                delta = {'status': 'ok', 'from': since, 'to': state['generation'],
                         'added_edges': [edges[k] for k in sorted(edges.keys()-older['edges'].keys())],
                         'removed_edges': [older['edges'][k] for k in sorted(older['edges'].keys()-edges.keys())],
                         'changed_edges': [edges[k] for k in sorted(edges.keys() & older['edges'].keys()) if edges[k] != older['edges'][k]],
                         'provider_state_changed': state['providers'] != older['providers'], 'providers': state['providers']}
        complete = not unknown and not omitted and all(p['complete'] and p['freshness'] == 'current' for p in state['providers'])
        warnings = [] if complete else ['dependency_coverage_partial']
        if omitted: warnings.append('trace_budget;continue_with_larger_budget_same_generation')
        if any(r.get('snippet', {}).get('status') != 'ok' for r in results if 'snippet' in r): warnings.append('source_snippets_partial')
        # Optional lexical halo reuses canonical ctxpp/Git inspection, confined
        # to permitted roots. It cannot add adjacency or affect witness chains.
        candidates = []
        if query:
            for obs in state['_lookup'].values():
                found = CtxppReadAdapter(obs['root'], obs['git'], obs['deny']).inspect(query, max_items=10)
                candidates.append({'project': obs['project'], 'repository': obs['repository'], 'origin': 'candidate', 'propagates': False, **found})
        return {'status': 'ok' if not warnings else 'partial', 'generation': state['generation'],
                'seeds': [nodes[s] for s in seeds], 'dependencies': results, 'possible_related_candidates': candidates,
                'nonpropagating_relations': [e for e in halo if node_key(e['source']) in visited or node_key(e['target']) in visited],
                'unknown_scope': unknown, 'coverage': {'complete_graph_cut': complete, 'providers': state['providers'], 'repositories': state['repositories']},
                'traversal': {'visited_nodes': len(visited), 'examined_edges': traversed, 'cycle_or_shared_path_count': cycles, 'omitted_groups': omitted},
                'continuation': {'generation': state['generation'], 'request': digest(request)} if omitted else None,
                'delta': delta, 'warnings': warnings, 'safe_to_change': None}

    def _snippet(self, state, node, chain):
        obs = state['_lookup'].get((node['project_uuid'], node['repository']))
        if not obs or not node.get('path'): return {'status': 'omitted', 'reason': 'no_source_path'}
        path = node['path']
        expected = obs['fragments'].get(path, {}).get('digest')
        if not expected:
            expected = next((i['digest'] for e in reversed(chain) for i in e['input_manifest'] if i['kind'] == 'file' and i['key'] == path and e['source']['repository'] == node['repository']), None)
        if not expected: return {'status': 'omitted', 'reason': 'source_identity_unavailable'}
        try:
            raw = self._read(obs['root'], path)
            if digest(raw) != expected: return {'status': 'stale', 'reason': 'graph_source_changed'}
            lines = raw.decode('utf-8').splitlines()
            return {'status': 'ok', 'path': path, 'content_sha256': expected, 'commit': obs['commit'],
                    'start_line': 1, 'end_line': min(20, len(lines)), 'text': redact_text('\n'.join(lines[:20])), 'omitted_lines': max(0, len(lines)-20)}
        except (OSError, ValueError, UnicodeError): return {'status': 'stale', 'reason': 'graph_source_unavailable'}
