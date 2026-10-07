"""Packet-backed information API. Host identity is bound at construction, not in requests."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any, Callable, Mapping

from .as1_contracts import RESPONSE_BUDGETS_BYTES, SHARED_INFORMATION_TOOLS, SourceLocator, canonical_digest, route_search, relative_path
from .as1_packets import SQLitePacketStore, mask_payload
from .adapters.git import GitReadAdapter
from .config import DEFAULT_DENY_PATTERNS, ProjectControlConfig
from .models import DeltaSince, HistoryTraceInput, InspectInput, EvidenceInput, ArchitectureContextInput, SourceContextInput, SourceTarget, ProjectSnapshot, utc_now
from .registry import WorkspaceRegistry
from .security import is_denied, redact_text
from .services.inspect import inspect_subject
from .reconcile import ProjectReconciler
from .services.overview import project_overview
from .services.delta import project_delta
from .services.frontier import project_frontier
from .services.coordination import coordination_view
from .models import CoordinationViewInput
from .services.architecture import architecture_context
from .services.source_context import source_context
from .services.evidence import evidence_for
from .services.history import history_trace
from .services.machine_inspection import machine_inspection


@dataclass(frozen=True)
class ContextHost:
    """Trusted startup policy. Projects are allowlisted even for catalog requests."""
    profile: str
    principal: str
    projects: frozenset[str]

    def __post_init__(self):
        if self.profile not in {'observer', 'coder', 'codex', 'mutator', 'investigator', 'skill_assembler'} or not self.principal:
            raise ValueError('invalid trusted context host')

    def scope(self, project: str | None) -> dict:
        return {'principal': self.principal, 'project': project or 'catalog', 'profile': 'coder' if self.profile == 'codex' else self.profile}


class InformationService:
    """Canonical synthesis ports with one immutable packet store.

    semantic_provider is a read-only bound Service.project_context port; absent
    migration/extension is reported, never replaced with an empty success.
    impact_provider and job_lookup are host-installed canonical ports.
    """
    def __init__(self, config: ProjectControlConfig, store: SQLitePacketStore,
                 snapshot_provider: Callable[[str], ProjectSnapshot], host: ContextHost, *,
                 semantic_provider: Callable[[str], dict] | None = None,
                 job_lookup: Callable[[str, Mapping[str, Any]], dict] | None = None,
                 impact_provider: Callable[..., dict] | None = None,
                 todo_adapter: Callable[[str], Any] | None = None,
                 notebook_provider=None):
        self.config, self.store, self.snapshot_provider, self.host = config, store, snapshot_provider, host
        self.registry = WorkspaceRegistry(config)
        self.semantic_provider, self.job_lookup, self.impact_provider = semantic_provider, job_lookup, impact_provider
        self.todo_adapter = todo_adapter
        # Imported and constructed on first discovery/evidence request. This
        # keeps the packet producer's import graph independent of its consumer.
        self.notebook_provider = notebook_provider

    def call(self, tool: str, *, project: str | None = None, detail: str = 'compact', **params) -> dict:
        if tool not in (*SHARED_INFORMATION_TOOLS, 'read'):
            raise PermissionError('unsupported_information_tool')
        if detail not in RESPONSE_BUDGETS_BYTES or (detail == 'extended' and self.host.profile != 'observer'):
            raise PermissionError('detail_not_permitted')
        if tool == 'read' and self.host.profile != 'observer':
            raise PermissionError('observer_read_required; use native find/rg/Git')
        if project is not None:
            if project not in self.host.projects:
                raise PermissionError('project_not_permitted')
            self.registry.workspace(project)
        if project is None and tool not in {'overview', 'search', 'machine'}:
            raise ValueError('project_required')
        sources, omissions, parents = [], [], []
        query_kind = params.get('query', {}).get('kind') if isinstance(params.get('query'), dict) else None
        snapshot = self.snapshot_provider(project) if project and not (tool == 'search' and query_kind in {'packet', 'investigation'}) else None
        context, context_error = self._context(project) if project and (tool in {'overview', 'delta'} or (tool == 'search' and isinstance(params.get('query'), str))) else ({}, None)
        status = 'ok'
        if context and snapshot and context.get('project_revision') not in {None, snapshot.todo_revision}:
            omissions.append({'reason': 'semantic_context_revision_skew', 'snapshot_revision': snapshot.todo_revision, 'context_revision': context['project_revision']})
        if tool == 'overview':
            if not project:
                data = {'projects': [{'project': p, 'repositories': sorted(self.config.workspaces[p].repositories)}
                                     for p in sorted(self.host.projects & self.config.workspaces.keys())]}
            else:
                overview_detail = {'compact': 'compact', 'standard': 'standard',
                                   'extended': 'expanded'}[detail]
                data = {'orientation': context.get('orientation', []), 'work': project_overview(snapshot, detail=overview_detail).data,
                        'entry_points': [{'repository': a, 'path': p} for a in self.config.workspaces[project].repositories
                                         for p in GitReadAdapter(self.registry.repository(project, a).root).tracked_files()
                                         if p in {'README.md', 'AGENTS.md', 'pyproject.toml', 'CMakeLists.txt'}],
                        'skill_uses': context.get('skill_uses', [])}
                if not data['orientation']:
                    omissions.append({'reason': context_error or 'authored_orientation_missing'})
                sources.extend(self._anchors(project, context))
        elif tool == 'read':
            data, sources = self._read(project, detail=detail, **params)
            if any(i['status'] != 'ok' for i in data['files']): status = 'partial'
        elif tool == 'search':
            query = params.pop('query')
            data = route_search(query, exact_lookup=lambda kind, target: self._exact(project, snapshot, kind, target, params),
                                discovery=lambda text: self._discover(project, snapshot, text, params, context))
            status = data.pop('_status', 'ok')
            if data.get('unavailable'): omissions.append({'reason':str(data['unavailable'])})
            if isinstance(query, dict) and query.get('kind') == 'registration':
                context = {'project_revision': data.get('authority_revision'), 'project_uuid': data.get('project_uuid')}
                if snapshot and data.get('authority_revision') not in {None, snapshot.todo_revision}:
                    omissions.append({'reason':'semantic_context_revision_skew', 'snapshot_revision':snapshot.todo_revision, 'context_revision':data['authority_revision']})
            sources.extend(self._anchors(project, data))
            if isinstance(query, str) and project:
                for group in data.get('source', []):
                    for match in group.get('live_fallback', []):
                        try:
                            path = relative_path(str(match['path']))
                            raw = self._working_bytes(self.registry.repository(project, group['repository']).root, path)
                            sources.append(SourceLocator(project=project, repository=group['repository'], path=path, content_sha256=hashlib.sha256(raw).hexdigest()))
                        except (ValueError, OSError, KeyError):
                            omissions.append({'reason': 'search_source_identity_unavailable'})
        elif tool == 'delta':
            since = params.pop('since')
            if isinstance(since, str):
                found = self.store.lookup(since, access_scope=self.host.scope(project))
                if found.status != 'ok':
                    data = {'baseline': found.status, 'current': snapshot.cursor().model_dump(mode='json')}
                    omissions.append({'reason': 'baseline_' + found.status}); status = 'partial'
                    since = None
                else:
                    parents.append(found.packet.packet_id)
                    since = found.packet.payload.get('cursor')
                    if not since:
                        data = {'baseline': 'unavailable', 'current': snapshot.cursor().model_dump(mode='json')}
                        omissions.append({'reason': 'baseline_cursor_unavailable'}); status = 'partial'
            if since:
                adapters = {a: GitReadAdapter(self.registry.repository(project, a).root) for a in snapshot.repositories}
                result = project_delta(snapshot, DeltaSince.model_validate(since), adapters,
                                       todo_adapter=self.todo_adapter(project) if self.todo_adapter else None)
                data = result.data; omissions.extend({'reason': w} for w in result.warnings)
                status = result.status.value if hasattr(result.status, 'value') else result.status
                data['semantic_context'] = context
        elif tool == 'frontier':
            data = project_frontier(snapshot).data
            scope = params.pop('scope', None)
            if scope:
                request = CoordinationViewInput(project=project, detail='standard', **scope)
                data['coordination'] = coordination_view(snapshot, request).data
        elif tool == 'evidence':
            result = evidence_for(self.config, snapshot, EvidenceInput(project=project, **params))
            data = result.data; omissions.extend({'reason': w} for w in result.warnings)
            if project:
                data['prepared_context'] = self._prepared_context(params['subject'], project)
                sources.extend(self._anchors(project, data['prepared_context']))
            gates = []
            for gate in ProjectReconciler(snapshot).reconcile().gates:
                if params['subject'] not in {str(gate.get('id')), str(gate.get('task_id')), str(gate.get('owner_task_id'))}: continue
                state = str(gate.get('effective_state') or gate.get('status') or 'pending')
                task = next((r for r in snapshot.todo_semantic.get('tasks', []) if r.get('id') == gate.get('task_id')), {})
                terminal = task.get('terminal') and task.get('raw_result') in {'success', 'validated', 'passed'}
                classification = ('terminal_frozen_success' if terminal and gate.get('valid') else
                    'stale_historical_pass' if gate.get('valid') and gate.get('relevance') == 'historical' else
                    'current_pass' if gate.get('valid') else 'running' if 'running' in state else
                    'registered_unrun' if state in {'pending', 'current_pending', 'not_run', 'queued', 'ready'} else 'failed' if 'fail' in state or 'invalid' in state else 'absent_evidence')
                gates.append({**gate, 'evidence_classification': classification})
            data['registered_gates'] = gates
            data['source_mentions_are_proof'] = False
        elif tool == 'history':
            data, omissions = self._history(project, snapshot, params)
        elif tool == 'impact':
            if self.impact_provider is None:
                data = {'targets': params.get('targets', []), 'provider': 'unavailable'}
                omissions.append({'reason': 'impact_provider_unavailable'}); status = 'partial'
            else:
                data = self.impact_provider(project=project, detail=detail,
                                            max_payload_bytes=max(1024, self.store.max_payload_bytes - 8192),
                                            **params)
                if hasattr(data, 'model_dump'): data = data.model_dump(mode='json')
        elif tool == 'machine':
            view = params.pop('query_or_view', 'host_memory')
            from typing import get_args
            from .services.machine_inspection import MachineDiagnostic
            if view not in get_args(MachineDiagnostic): raise ValueError('unsupported_machine_view')
            if params: raise ValueError('unsupported_machine_parameters')
            if not project:
                snapshot = ProjectSnapshot(workspace_id='catalog', repositories={}, observed_at=utc_now())
            result = machine_inspection(self.config, snapshot, project=project or 'catalog', diagnostic=view)
            data = result.data; data['observed_at'] = utc_now()
            omissions.extend({'reason': w} for w in result.warnings)
        if isinstance(data, dict):
            prepared = data.get('prepared_context')
            if isinstance(prepared, dict) and prepared.get('omissions'):
                omissions.extend({'reason': 'prepared_context_omission', **item}
                                 for item in prepared['omissions'][:32] if isinstance(item, dict))
                if status == 'ok': status = 'partial'
        if isinstance(data, dict) and data.get('warnings'):
            omissions.extend({'reason': str(w)} for w in data['warnings'])
        if 'source_registration_unavailable' in json.dumps(context):
            omissions.append({'reason': 'semantic_source_registration_unavailable'})
        if context_error:
            omissions.append({'reason': context_error})
        if snapshot:
            cursor = snapshot.cursor().model_dump(mode='json')
        else: cursor = None
        if omissions and status == 'ok': status = 'partial'
        data, masked = mask_payload(data); omissions.extend(masked)
        coverage = {'omissions': omissions, 'semantic_revision': snapshot.todo_revision if snapshot else None,
                    'context_revision': context.get('project_revision'), 'context_project_uuid': context.get('project_uuid'),
                    'observed_at': snapshot.observed_at if snapshot else utc_now(), 'complete': not omissions}
        payload = {'status': status, 'data': data, 'coverage': coverage, 'cursor': cursor}
        freshness = {'dependencies': {f'file:{s.repository}/{s.path}': s.content_sha256 for s in sources}}
        if snapshot and project: freshness['dependencies']['semantic_revision:' + project] = hashlib.sha256(str(snapshot.todo_revision).encode()).hexdigest()
        if context.get('project_revision') is not None:
            freshness['dependencies']['semantic_context_revision:' + project] = hashlib.sha256(str(context['project_revision']).encode()).hexdigest()
        if tool == 'machine': freshness.update(volatile=True, max_age_seconds=5)
        packet = self.store.create(tool=tool, payload=payload, sources=sources, parents=parents,
                                   access_scope=self.host.scope(project), normalized_request={'tool': tool, 'project': project, 'detail': detail},
                                   freshness=freshness, omissions=omissions)
        response = {'status': status, 'packet': packet.packet_id, 'data': packet.payload['data'],
                    'sources': [s.model_dump(exclude_none=True) for s in sources], 'coverage': coverage}
        response_size = len(json.dumps(response, ensure_ascii=False).encode())
        if response_size > RESPONSE_BUDGETS_BYTES[detail]:
            # Exact excerpts are indivisible units. Keep them in the immutable
            # packet; return a targeted continuation instead of text slicing.
            continuation = {'tool': 'search', 'query': {'kind': 'packet',
                            'target': query.get('target') if tool == 'search' and isinstance(query, dict) and query.get('kind') == 'packet' else packet.packet_id},
                            'detail': 'extended' if self.host.profile == 'observer' else 'standard'}
            if tool == 'impact':
                source_count = len(response['sources'])
                if len(response['sources']) > 2:
                    response['sources'] = response['sources'][:2]
                response['coverage'] = {'complete': False,
                    'omissions': [*omissions[:2],
                                  *([{'reason': 'source_locators', 'omitted_count': source_count - 2}]
                                    if source_count > 2 else []),
                                  {'reason': 'response_budget', 'unit': 'utf8_bytes',
                                   'omitted_count': max(0, len(omissions) - 2)}]}
                base_size = len(json.dumps({**response, 'data': {}}, ensure_ascii=False).encode())
                preview_budget = max(256, RESPONSE_BUDGETS_BYTES[detail] - base_size - 64)
                response['data'] = self._impact_preview(packet.payload['data'], continuation, preview_budget)
                # The wrapper budget includes sources and coverage too. If they
                # are unusually large, retry with the exact remaining space.
                actual_size = len(json.dumps(response, ensure_ascii=False).encode())
                if actual_size > RESPONSE_BUDGETS_BYTES[detail]:
                    response['data'] = self._impact_preview(packet.payload['data'], continuation,
                        max(256, preview_budget - (actual_size - RESPONSE_BUDGETS_BYTES[detail]) - 64))
            else:
                response['data'] = {'continuation': continuation, 'needed_bytes': response_size}
                response['coverage'] = {**coverage, 'complete': False, 'omissions': [*omissions, {'reason': 'response_budget', 'unit': 'utf8_bytes'}]}
            response['status'] = 'partial'
            if tool == 'impact' and len(json.dumps(response, ensure_ascii=False).encode()) > RESPONSE_BUDGETS_BYTES[detail]:
                response['sources'] = []
                response['coverage'] = {'complete': False,
                                        'omissions': [{'reason': 'response_budget', 'unit': 'utf8_bytes'}]}
                base_size = len(json.dumps({**response, 'data': {}}, ensure_ascii=False).encode())
                response['data'] = self._impact_preview(packet.payload['data'], continuation,
                    max(128, RESPONSE_BUDGETS_BYTES[detail] - base_size - 32))
        return response

    @staticmethod
    def _impact_preview(data, continuation, budget):
        """Show a compact affected-path sample while keeping the packet addressable."""
        dependencies = data.get('dependencies', [])
        compact_node = lambda node: {key: node[key] for key in
                                     ('project_uuid', 'repository', 'kind', 'id', 'path') if key in node}
        sample = []
        for item in dependencies[:2]:
            chain = item.get('witness_chain', [])
            sample.append({'node': compact_node(item.get('node', {})), 'witness_chain': {
                'edge_count': len(chain), 'sha256': hashlib.sha256(json.dumps(chain, sort_keys=True,
                    separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()}})
        full_dependency_digest = hashlib.sha256(json.dumps(dependencies, sort_keys=True,
            separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        groups = data.get('traversal', {}).get('omitted_groups', [])
        traversal = {key: data.get('traversal', {}).get(key, 0) for key in
                     ('visited_nodes', 'examined_edges', 'cycle_or_shared_path_count')}
        traversal['omitted_group_count'] = len(groups)
        unknown = []
        unknown_values = data.get('unknown_scope', [])
        selected_unknown = [*([item for item in unknown_values if item.get('target')][:1]),
                            *([item for item in unknown_values if not item.get('target')][:1])]
        for item in selected_unknown:
            compact = {key: item[key] for key in ('target', 'reason', 'count', 'project', 'repository') if key in item}
            if isinstance(compact.get('target'), dict): compact['target'] = compact_node(compact['target'])
            if isinstance(item.get('edge'), dict):
                compact['edge'] = {key: item['edge'][key] for key in
                                   ('relation', 'provider', 'generation', 'resolution') if key in item['edge']}
            unknown.append(compact)
        if len(unknown_values) > len(selected_unknown):
            unknown.append({'omitted_count': len(data['unknown_scope']) - len(unknown),
                            'full_count': len(unknown_values)})
        preview = {'generation': data.get('generation'),
                   'seeds': [compact_node(n) for n in data.get('seeds', [])[:1]],
                   'dependencies': sample,
                   'unknown_scope': unknown,
                   'coverage': {'complete_graph_cut': data.get('coverage', {}).get('complete_graph_cut')},
                   'traversal': traversal, 'warnings': data.get('warnings', [])[:1],
                   'continuation': continuation}
        if len(data.get('warnings', [])) > len(preview['warnings']):
            preview['warning_omissions'] = {'full_count': len(data['warnings']),
                                            'omitted_count': len(data['warnings']) - len(preview['warnings'])}
        if len(groups):
            preview['traversal']['omitted_groups_sha256'] = hashlib.sha256(json.dumps(groups, sort_keys=True,
                separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        if len(dependencies) > len(sample) or any(len(d.get('witness_chain', [])) > 1 for d in dependencies[:len(sample)]):
            preview['omissions'] = {'section': 'dependencies_or_witness_chains', 'preview_count': len(sample),
                                    'full_count': len(dependencies), 'full_sha256': full_dependency_digest}
        if data.get('payload_budget'):
            payload_summary = data['payload_budget']
            preview['payload_budget'] = {'full_result_sha256': payload_summary.get('full_result_sha256'),
                                         'omitted_sections': [entry.get('section') for entry in
                                                              payload_summary.get('omissions', [])[:3]]}

        encoded = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
        while sample and len(encoded(preview)) > budget:
            sample.pop()
            preview['dependencies'] = sample
            preview.setdefault('omissions', {'section': 'dependencies', 'full_count': len(dependencies),
                'full_sha256': full_dependency_digest})['preview_count'] = len(sample)
        if len(encoded(preview)) > budget:
            # Keep the request identity and one useful locator when the wrapper
            # leaves almost no room. The packet continuation retains full proof.
            seed = (data.get('seeds') or [{}])[0]
            target = compact_node(seed) if isinstance(seed, dict) else {}
            affected = dependencies[0].get('node', {}) if dependencies else {}
            preview = {'generation': data.get('generation'), 'target': target,
                       'affected_node': compact_node(affected), 'unknown_scope': unknown[:1], 'continuation': continuation,
                       'omissions': {'section': 'trace_preview', 'full_sha256': full_dependency_digest}}
            while len(encoded(preview)) > budget and preview.get('affected_node'):
                preview.pop('affected_node')
                preview['omissions']['affected_node_omitted'] = True
        return preview

    def _context(self, project):
        if not self.semantic_provider: return {}, 'semantic_context_extension_unavailable'
        try:
            result = self.semantic_provider(project)
            if result.get('ok') is False: return {}, str(result.get('error', 'semantic_context_unavailable'))
            value = result.get('data', result)
            if value.get('status') == 'unavailable': return {}, 'semantic_context_schema_migration_required'
            return self._public_context(project, value), None
        except Exception as exc:
            return {}, 'semantic_context_unavailable:' + type(exc).__name__

    def _public_context(self, project, value):
        """Keep semantic UUID identity; publish only registered locator aliases."""
        def clean(item):
            if isinstance(item, dict):
                if item.get('path') and item.get('content_sha256') and item.get('repository'):
                    repository = item['repository']
                    owner = item.get('project')
                    # Native canonical declarations may use their UUID/root
                    # as identity. Translate only this verified owner/root.
                    if owner == value.get('project_uuid'): owner = project
                    aliases = self.config.workspaces.get(owner).repositories if owner in self.config.workspaces and owner in self.host.projects else {}
                    matches = [a for a, cfg in aliases.items() if a == repository or str(cfg.root) == repository]
                    if len(matches) != 1:
                        return {'status': 'source_registration_unavailable', 'content_sha256': item['content_sha256']}
                    return {**{k: clean(v) for k,v in item.items()}, 'project': owner, 'repository': matches[0]}
                return {k: clean(v) for k,v in item.items()}
            if isinstance(item, list): return [clean(v) for v in item]
            return item
        return clean(value)

    def _anchors(self, project, value):
        """Only real source anchors become file locators; entities never do."""
        found = {}
        def walk(item):
            if isinstance(item, dict):
                if item.get('path') and item.get('content_sha256') and item.get('repository'):
                    try:
                        owner = item.get('project', project)
                        repository = item['repository']
                        if owner not in self.host.projects or owner not in self.config.workspaces: return
                        repositories = self.config.workspaces[owner].repositories
                        if repository not in repositories:
                            aliases = [alias for alias, config in repositories.items() if str(config.root) == repository]
                            if len(aliases) != 1: return
                            repository = aliases[0]
                        anchor = {**item, 'repository': repository}
                        locator = SourceLocator(project=owner, **{k: anchor[k] for k in
                            ('repository', 'path', 'content_sha256', 'revision', 'line_start', 'line_end', 'worktree') if k in item})
                        found[(locator.repository, locator.path, locator.content_sha256, locator.line_start, locator.line_end)] = locator
                    except (ValueError, TypeError): pass
                for child in item.values(): walk(child)
            elif isinstance(item, list):
                for child in item: walk(child)
        walk(value)
        return list(found.values())

    def _exact(self, project, snapshot, kind, target, params):
        if kind == 'packet':
            result = self.store.lookup(target, access_scope=self.host.scope(project))
            return {'kind': kind, 'target': target, 'resolution': result.status,
                    'result': result.packet.payload if result.packet else None,
                    'sources': [s.model_dump() for s in result.packet.sources] if result.packet else [],
                    '_status': 'ok' if result.status == 'ok' else 'partial'}
        if kind == 'investigation':
            if not self.job_lookup: return {'kind': kind, 'resolution': 'unavailable', '_status': 'partial'}
            result = dict(self.job_lookup(target, self.host.scope(project)))
            if result.get('resolution') in {'forbidden', 'not_found', 'expired', 'unavailable'} or result.get('status') in {'forbidden', 'not_found', 'expired', 'unavailable'}:
                result['_status'] = 'partial'
            return result
        if not project: raise ValueError('project_required_for_exact_entity')
        if kind == 'registration':
            context, error = self._context(project)
            matches = [r for r in context.get('declarations', []) if str(r.get('id')) == target]
            return {'kind': kind, 'matches': matches, 'resolution': 'ambiguous' if len(matches)>1 else 'resolved' if matches else 'not_found',
                    'authority_revision': context.get('project_revision'), 'project_uuid': context.get('project_uuid'), 'unavailable': error,
                    '_status': 'partial' if error or len(matches) != 1 else 'ok'}
        # No source corpus, lexical index or filesystem discovery for exact IDs.
        request = InspectInput(project=project, kind=kind, target=target, **params)
        result = inspect_subject(self.config, snapshot, request, exact_only=True)
        data = result.data
        if data.get('resolution', {}).get('status') != 'resolved': data['_status'] = 'partial'
        data['authority_revision'] = snapshot.todo_revision
        return data

    def _discover(self, project, snapshot, query, params, context):
        if not query or len(query) > 12000: raise ValueError('invalid_discovery_query')
        if not project:
            return {'matches': [{'kind': 'project', 'id': p, 'why': 'registered_catalog'} for p in
                                sorted(self.host.projects & self.config.workspaces.keys()) if query.casefold() in p.casefold()],
                    'coverage': 'registered_catalog_only'}
        scope = params.pop('scope', {}) or {}
        repository = scope.get('repository', params.pop('repository', None))
        semantic = architecture_context(snapshot, ArchitectureContextInput(project=project, question=query, detail='compact')).data
        source_results = []
        for alias in ([repository] if repository else sorted(self.config.workspaces[project].repositories)):
            # Phrase-aware fixed-string live fallback runs beside existing
            # semantic/symbol/lexical providers, never in exact typed routing.
            root = self.registry.repository(project, alias).root
            deny = [*DEFAULT_DENY_PATTERNS, *self.config.workspaces[project].deny_patterns]
            phrase = query.strip('"\'')
            paths = [p for p in GitReadAdapter(root).tracked_files() if phrase.casefold() in p.casefold()
                     and not is_denied(Path(p), deny) and (not scope.get('path') or p.startswith(scope['path']))]
            source = source_context(self.config, snapshot, SourceContextInput(project=project, repository=alias,
                targets=[SourceTarget(kind='text', value=query)], detail='standard', budget_bytes=32768)).data
            fixed = GitReadAdapter(root).grep(phrase, deny_patterns=deny)
            if scope.get('path'):
                fixed = [m for m in fixed if str(m.get('path', '')).startswith(scope['path'])]
            if scope.get('path'):
                for target in source.get('targets', []):
                    target['matches'] = [m for m in target.get('matches', []) if str(m.get('path', '')).startswith(scope['path'])]
            if ' ' in phrase:
                for target in source.get('targets', []):
                    target['matches'] = [m for m in target.get('matches', []) if phrase.casefold() in str(m.get('excerpt', '')).casefold()]
            source_results.append({'repository': alias, 'paths': paths[:50], 'source': source,
                                   'live_fallback': fixed, 'origin': 'lexical_candidate'})
        return {'semantic': semantic, 'source': source_results, 'skill_uses': context.get('skill_uses', []),
                'notes': context.get('declarations', []),
                'prepared_context': self._prepared_context(query, project), 'scope': scope,
                'coverage': 'registered_graph_source_symbol_lexical_live; not impact closure'}

    def _prepared_context(self, query: str, project: str) -> dict:
        """Return bounded notebook context as advisory enrichment of this query."""
        try:
            provider = self.notebook_provider
            if provider is None:
                from .assistance.knowledge import NotebookProvider
                provider = NotebookProvider(
                    self.store,
                    resolve_dependency=self._resolve_notebook_dependency,
                    trusted_projects=self.host.projects,
                    clock=self.store.clock,
                )
                self.notebook_provider = provider
            result = provider.retrieve(query, project=project,
                access_scope=self.host.scope(project))
            # Make the non-authoritative contract explicit at the consumer seam,
            # even if a provider implementation is replaced during migration.
            if not isinstance(result, dict):
                raise TypeError('notebook_provider_result_invalid')
            return {**result, 'authoritative': False, 'mutation_authority': False,
                    'advisory_instruction': True}
        except Exception as exc:
            return {'notes': [], 'goals': [],
                    'omissions': [{'project': project,
                                   'reason': 'notebook_provider_unavailable:' + type(exc).__name__}],
                    'coverage': {'complete': False, 'projects': [project]},
                    'authoritative': False, 'mutation_authority': False,
                    'advisory_instruction': True}

    def _resolve_notebook_dependency(self, project: str, key: str):
        """Resolve only explicit material dependencies inside registered roots."""
        if project not in self.host.projects or project not in self.config.workspaces:
            return {'status': 'unavailable', 'reason': 'project_not_permitted'}
        if not isinstance(key, str) or not key:
            return {'status': 'unavailable', 'reason': 'invalid_dependency_key'}
        try:
            if key.startswith('semantic_revision:'):
                dependency_project = key.partition(':')[2]
                if dependency_project != project or dependency_project not in self.host.projects:
                    return {'status': 'unavailable', 'reason': 'semantic_project_not_permitted'}
                snapshot = self.snapshot_provider(dependency_project)
                digest = canonical_digest(snapshot.todo_revision)
                return {'status': 'current', 'digest': digest}
            if key.startswith('configuration:'):
                alias = key.partition(':')[2]
                if alias not in self.config.workspaces[project].repositories:
                    return {'status': 'unavailable', 'reason': 'configuration_repository_unregistered'}
                # Use the same trusted TraceService observation and digest that
                # powers impact-provider configuration determinants. This is a
                # material config digest; unrelated HEAD movement is irrelevant.
                trace = self.impact_provider
                observe = getattr(trace, '_repository', None)
                if not callable(observe):
                    return {'status': 'unavailable', 'reason': 'configuration_provider_unavailable'}
                observed = observe(project, alias, self.snapshot_provider(project))
                item = next((entry for entry in observed.get('inputs', [])
                             if entry.get('kind') == 'configuration' and entry.get('key') == alias), None)
                if not item or observed.get('freshness') != 'current':
                    return {'status': 'unavailable', 'reason': 'configuration_observation_unavailable'}
                return {'status': 'current', 'digest': item['digest']}
            if not key.startswith('file:'):
                return {'status': 'unavailable', 'reason': 'unsupported_dependency_kind'}
            target = key[len('file:'):]
            selected_project, alias, relative = project, None, None
            if target.startswith('/'):
                absolute = Path(target)
                matches = []
                for candidate in sorted(self.host.projects):
                    workspace = self.config.workspaces.get(candidate)
                    if workspace is None:
                        continue
                    for candidate_alias, repository in workspace.repositories.items():
                        try:
                            rel = absolute.relative_to(repository.root).as_posix()
                        except ValueError:
                            continue
                        if rel not in {'', '.'}:
                            matches.append((candidate, candidate_alias, rel))
                if len(matches) != 1:
                    return {'status': 'unavailable', 'reason': 'absolute_source_root_ambiguous_or_unregistered'}
                selected_project, alias, relative = matches[0]
            else:
                alias, separator, relative = target.partition('/')
                if not separator or not alias or not relative:
                    return {'status': 'unavailable', 'reason': 'invalid_file_dependency'}
            if selected_project not in self.host.projects:
                return {'status': 'unavailable', 'reason': 'source_project_not_permitted'}
            workspace = self.config.workspaces[selected_project]
            if alias not in workspace.repositories:
                return {'status': 'unavailable', 'reason': 'source_repository_unregistered'}
            relative = relative_path(relative)
            deny = [*DEFAULT_DENY_PATTERNS, *workspace.deny_patterns]
            if is_denied(Path(relative), deny):
                return {'status': 'unavailable', 'reason': 'source_path_denied'}
            repository = self.registry.repository(selected_project, alias)
            raw = self._working_bytes(repository.root, relative)
            return {'status': 'current', 'digest': hashlib.sha256(raw).hexdigest()}
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            return {'status': 'unavailable', 'reason': 'dependency_unavailable:' + reason}

    def _read(self, project, *, paths, repository=None, revision=None, detail='compact'):
        if not isinstance(paths, list) or not 1 <= len(paths) <= 32: raise ValueError('read_requires_1_to_32_paths')
        repo = self.registry.repository(project, repository)
        deny = [*DEFAULT_DENY_PATTERNS, *self.config.workspaces[project].deny_patterns]
        revision_error = None
        try: commit = GitReadAdapter(repo.root).verify_revision(revision) if revision else None
        except (ValueError, RuntimeError): commit, revision_error = None, 'invalid_revision'
        files, sources = [], []
        for requested in paths:
            item = {'path': requested if isinstance(requested, str) else requested.get('path')}
            try:
                if revision_error: raise ValueError(revision_error)
                spec = {'path': requested} if isinstance(requested, str) else requested
                path = relative_path(spec['path'])
                if path.startswith('-') or is_denied(Path(path), deny): raise ValueError('path_denied')
                first, last = spec.get('line_start', 1), spec.get('line_end')
                if not isinstance(first, int) or first < 1 or (last is not None and (not isinstance(last, int) or last < first)):
                    raise ValueError('invalid_range')
                if commit:
                    mode = self._git(repo.root, 'ls-tree', commit, '--', path).decode().split(' ', 1)[0]
                    if mode == '120000': raise ValueError('symlink_denied')
                    if mode == '040000': raise ValueError('not_a_file')
                    raw = self._git(repo.root, 'show', commit + ':' + path)
                else:
                    raw = self._working_bytes(repo.root, path)
                digest = hashlib.sha256(raw).hexdigest()
                if b'\0' in raw: raise ValueError('unsupported_binary')
                try: text = raw.decode('utf-8')
                except UnicodeDecodeError: raise ValueError('unsupported_binary') from None
                lines = text.splitlines(keepends=True)
                end = min(last if last is not None else len(lines), len(lines))
                selected = ''.join(lines[first-1:end])
                clean = redact_text(selected)
                if clean != selected:
                    item.update(status='redacted', redaction=True)
                else: item['status'] = 'ok'
                item.update(content=clean, line_start=first, line_end=end, content_sha256=digest, revision=commit,
                            exact=clean == selected)
                sources.append(SourceLocator(project=project, repository=repo.alias, path=path, content_sha256=digest,
                                             revision=commit, line_start=first, line_end=max(first, end)))
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                item.update(status='error', error=str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
            files.append(item)
        return {'files': files, 'repository': repo.alias, 'revision': commit}, sources

    @staticmethod
    def _git(root, *args):
        result = subprocess.run(['git', *args], cwd=root, capture_output=True, timeout=5)
        if result.returncode: raise ValueError('git_object_unavailable')
        if len(result.stdout) > 8*1024*1024: raise ValueError('source_unit_too_large')
        return result.stdout

    @staticmethod
    def _working_bytes(root, relative):
        # Descriptor traversal prevents TOCTOU symlink swaps in intermediate
        # components. No requested symlink is followed, including in-root links.
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            parts = relative.split('/')
            for component in parts[:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor); descriptor = child
            leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            try:
                before = os.fstat(leaf)
                if not stat.S_ISREG(before.st_mode): raise ValueError('not_a_file')
                if before.st_size > 8*1024*1024: raise ValueError('source_unit_too_large')
                with os.fdopen(os.dup(leaf), 'rb') as stream: raw = stream.read(8*1024*1024+1)
                after = os.fstat(leaf)
                current = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
                identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                if identity(before) != identity(after) or identity(after) != identity(current): raise ValueError('racy_source_read')
                # Verify bytes independently before calling the read exact.
                os.lseek(leaf, 0, os.SEEK_SET)
                with os.fdopen(os.dup(leaf), 'rb') as stream: check = stream.read(8*1024*1024+1)
                if raw != check or identity(after) != identity(os.fstat(leaf)): raise ValueError('racy_source_read')
                validation = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    for component in parts[:-1]:
                        child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=validation)
                        os.close(validation); validation = child
                    if identity(after) != identity(os.stat(parts[-1], dir_fd=validation, follow_symlinks=False)):
                        raise ValueError('racy_source_read')
                finally: os.close(validation)
                return raw
            finally: os.close(leaf)
        finally: os.close(descriptor)

    def _history(self, project, snapshot, params):
        request = HistoryTraceInput(project=project, **params)
        omissions = []
        for kind in ('task', 'checkpoint', 'interface'):
            anchor = getattr(request, 'from_' + kind)
            if anchor:
                exact = inspect_subject(self.config, snapshot, InspectInput(project=project, kind=kind, target=anchor), exact_only=True).data
                rows = exact.get('matches', [])
                if len(rows) != 1 or rows[0].get('revision') is None:
                    omissions.append({'reason': 'history_anchor_unavailable', 'kind': kind, 'target': anchor})
                else: request = request.model_copy(update={'from_revision': rows[0]['revision'], 'from_' + kind: None})
        # Legacy synthesis may not compare commit strings: Git handles them.
        result = history_trace(snapshot, request.model_copy(update={'from_commit': None, 'to_commit': None}))
        data = result.data; omissions.extend({'reason': w} for w in result.warnings)
        git_events = []
        for alias in sorted(snapshot.repositories):
            root = self.registry.repository(project, alias).root
            try:
                adapter = GitReadAdapter(root)
                end = adapter.verify_revision(request.to_commit or snapshot.repositories[alias].commit or 'HEAD')
                begin = adapter.verify_revision(request.from_commit) if request.from_commit else None
                if begin and subprocess.run(['git', 'merge-base', '--is-ancestor', begin, end], cwd=root, capture_output=True).returncode:
                    raise ValueError('history_non_ancestor_range')
                arguments = ['log', '--topo-order', '-100', '--format=%H%x09%cI%x09%s', f'{begin}..{end}' if begin else end]
                raw = self._git(root, *arguments).decode()
                for line in raw.splitlines():
                    sha, stamp, subject = line.split('\t', 2)
                    if request.from_time and stamp < request.from_time: continue
                    git_events.append({'repository': alias, 'commit': sha, 'timestamp': stamp, 'summary': subject,
                                       'causal_basis': None, 'source': 'git_ancestry'})
                if len(raw.splitlines()) == 100: omissions.append({'reason': 'git_history_limit', 'repository': alias})
            except (ValueError, subprocess.SubprocessError) as exc:
                omissions.append({'reason': str(exc), 'repository': alias})
        # Exported git records are replaced by real ancestry, not SHA ordering.
        data['events'] = [e for e in data.get('events', []) if e.get('entity_type') != 'git_commit']
        data['git_events'] = git_events
        data['selectors'] = request.model_dump(exclude_none=True)
        return data, omissions
