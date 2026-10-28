"""Packet-backed information API. Host identity is bound at construction, not in requests."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
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
                 native_reader: Callable[[str, str, str], dict] | None = None,
                 todo_adapter: Callable[[str], Any] | None = None,
                 notebook_provider=None):
        self.config, self.store, self.snapshot_provider, self.host = config, store, snapshot_provider, host
        self.registry = WorkspaceRegistry(config)
        self.semantic_provider, self.job_lookup, self.impact_provider = semantic_provider, job_lookup, impact_provider
        self.native_reader = native_reader
        self.todo_adapter = todo_adapter
        self._source_indices = {}
        # Imported and constructed on first discovery/evidence request. This
        # keeps the packet producer's import graph independent of its consumer.
        self.notebook_provider = notebook_provider

    def call(self, tool: str, *, project: str | None = None, **params) -> dict:
        if tool not in (*SHARED_INFORMATION_TOOLS, 'read', 'inspect'):
            raise PermissionError('unsupported_information_tool')
        profile_budget = 'observer' if self.host.profile == 'observer' else 'local'
        response_budget = RESPONSE_BUDGETS_BYTES[profile_budget]
        internal_detail = 'extended' if profile_budget == 'observer' else 'standard'
        if tool == 'read' and self.host.profile != 'observer':
            raise PermissionError('observer_read_required; use native find/rg/Git')
        if project is not None:
            try:
                project, ambiguous = self.registry.resolve_workspace(
                    project, allowed_workspaces=self.host.projects)
            except ValueError as exc:
                raise PermissionError('project_not_permitted') from exc
            if ambiguous:
                return self._ambiguous_project_response(tool, ambiguous)
        if project is None and tool not in {'overview', 'search', 'machine'}:
            raise ValueError('project_required')
        sources, omissions, parents = [], [], []
        query_kind = params.get('query', {}).get('kind') if isinstance(params.get('query'), dict) else None
        lexical_search = tool == 'search' and isinstance(params.get('query'), str)
        snapshot_error = None
        snapshot = None
        if project and tool not in {'read', 'inspect', 'context', 'trace', 'machine', 'evidence', 'search'}:
            try:
                snapshot = self.snapshot_provider(project)
            except Exception as exc:
                if tool != 'overview':
                    raise
                snapshot_error = getattr(exc, 'code', 'todo_project_snapshot_unavailable')
                snapshot = ProjectSnapshot(workspace_id=project, observed_at=utc_now(), repositories={})
        context, context_error = self._context(project) if project and tool in {'overview', 'delta'} else ({}, None)
        status = 'ok'
        if context and snapshot and context.get('project_revision') not in {None, snapshot.todo_revision}:
            omissions.append({'reason': 'semantic_context_revision_skew', 'snapshot_revision': snapshot.todo_revision, 'context_revision': context['project_revision']})
        if tool == 'overview':
            if not project:
                data = {'projects': [{'project': p, 'repositories': sorted(self.config.workspaces[p].repositories)}
                                     for p in sorted(self.host.projects & self.config.workspaces.keys())]}
            else:
                overview_detail = 'expanded' if profile_budget == 'observer' else 'standard'
                data = {'orientation': context.get('orientation', []), 'work': project_overview(snapshot, detail=overview_detail).data,
                        'entry_points': [{'repository': a, 'path': p} for a in self.config.workspaces[project].repositories
                                         for p in GitReadAdapter(self.registry.repository(project, a).root).tracked_files()
                                         if p in {'README.md', 'AGENTS.md', 'pyproject.toml', 'CMakeLists.txt'}],
                        'skill_uses': context.get('skill_uses', []),
                        'registered_relations': context.get('metadata_relations', []),
                        'native_authority': context.get('native_authority', {})}
                if not data['orientation']:
                    omissions.append({'reason': context_error or 'authored_orientation_missing'})
                sources.extend(self._anchors(project, context))
        elif tool == 'read':
            budget_bytes = max(1, min(int(params.pop('budget_bytes', response_budget)), response_budget))
            data, sources = self._read(project, detail=internal_detail,
                budget_bytes=budget_bytes, **params)
            remaining = budget_bytes
            for item in data['files']:
                content = item.get('content')
                if not isinstance(content, str):
                    continue
                encoded = content.encode('utf-8')
                if len(encoded) > remaining:
                    item['content'] = encoded[:remaining].decode('utf-8', errors='ignore')
                    item['complete'] = False
                    item['omission'] = 'context_budget'
                    status = 'partial'
                    remaining = 0
                else:
                    item.setdefault('complete', True)
                    remaining -= len(encoded)
            if any(i['status'] != 'ok' for i in data['files']): status = 'partial'
        elif tool == 'inspect':
            data = self._inspect(project, **params)
            if data.get('complete') is False: status = 'partial'
        elif tool == 'context':
            data, sources = self._context_sources(project, **params)
            if not data.get('complete', False): status = 'partial'
        elif tool == 'trace':
            data = self._trace_sources(project, **params)
            if not data.get('complete', False): status = 'partial'
        elif tool == 'search':
            query = params.pop('query')
            if isinstance(query, str):
                data = {'repositories': {alias: index.search(query,
                    limit=min(500, max(1, int(params.get('limit', 30)))),
                    cursor=params.get('cursor')) for alias, index in
                    self._all_source_indexes(project, params.get('repository'))},
                    'query_kind': 'lexical', 'intent': 'literal substring search'}
                data['_status'] = 'partial' if any(not value.get('complete', True)
                    for value in data['repositories'].values()) else 'ok'
            elif query.get('kind') in {'symbol', 'test', 'path'}:
                data = self._source_entity(project, query, params)
            else:
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
            data = {}
            mode = params.pop('mode', 'changes')
            if mode == 'history':
                subject = params.pop('subject', None)
                if not isinstance(subject, str) or not subject.strip():
                    raise ValueError('history_mode_requires_subject')
                history_params = {'subject': subject, 'max_events': params.pop('max_events', 100),
                    'continuation_cursor': params.pop('cursor', None),
                    'detail': 'expanded' if profile_budget == 'observer' else 'standard'}
                for name in ('from_revision', 'to_revision', 'from_commit', 'to_commit', 'from_time'):
                    value = params.pop(name, None)
                    if value is not None:
                        history_params[name] = value
                # History is scoped to one subject and explicit temporal/revision
                # selectors. Unrelated change-cursor values are invalid here.
                if params.pop('since', None) is not None:
                    raise ValueError('since_cursor_not_valid_in_history_mode')
                data, history_omissions = self._history(project, snapshot, history_params)
                omissions.extend(history_omissions)
                status = 'partial' if history_omissions or data.get('complete') is False else 'ok'
                sources.extend(self._anchors(project, data))
                since = None
            elif mode != 'changes':
                raise ValueError('unsupported_delta_mode')
            else:
                since = params.pop('since', None)
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
            if since and mode == 'changes':
                adapters = {a: GitReadAdapter(self.registry.repository(project, a).root) for a in snapshot.repositories}
                result = project_delta(snapshot, DeltaSince.model_validate(since), adapters,
                                   todo_adapter=self.todo_adapter(project) if self.todo_adapter else None)
                data = result.data; omissions.extend({'reason': w} for w in result.warnings)
                status = result.status.value if hasattr(result.status, 'value') else result.status
                data['semantic_context'] = context
            elif mode == 'changes' and not data:
                data = {'baseline': 'required', 'current': snapshot.cursor().model_dump(mode='json')}
                omissions.append({'reason': 'delta_baseline_required'})
                status = 'partial'
        elif tool == 'frontier':
            data = project_frontier(snapshot).data
            scope = params.pop('scope', None)
            if scope:
                request = CoordinationViewInput(project=project, detail='standard', **scope)
                data['coordination'] = coordination_view(snapshot, request).data
        elif tool == 'evidence':
            from project_evidence import EvidenceStore
            evidence_store = EvidenceStore(self.store.directory.parent / 'evidence.sqlite3',
                self.store.directory.parent / 'evidence-artifacts')
            limit = max(1, min(1000, int(params.get('max_items', params.get('limit', 50)))))
            observations = evidence_store.list_observations(owner=project,
                subject=params['subject'], limit=limit)
            requested_kinds = set(params.get('kinds') or [])
            if requested_kinds:
                observations = [row for row in observations if row.get('kind') in requested_kinds]
            for record in observations:
                record['assessments'] = evidence_store.list_assessments(record['observation_id'], limit=limit)
            relations = evidence_store.list_relations(subject=params['subject'], limit=limit)
            external, external_relations, external_omissions, external_stores = self._registered_evidence(
                project, params['subject'], limit=limit,
                artifact_budget=int(response_budget * 0.65))
            observations.extend(external)
            relations.extend(external_relations)
            omissions.extend(external_omissions)
            data = {'observations': observations, 'relations': relations,
                'stores': external_stores, 'provenance': 'project-evidence',
                'source_mentions_are_proof': False,
                'complete': (len(observations) < limit and len(relations) < limit
                    and not external_omissions)}
            if len(observations) >= limit or len(relations) >= limit:
                omissions.append({'reason': 'result_limit', 'limit': limit})
            todo_kinds = requested_kinds & {'gates', 'tests', 'worker', 'cuda', 'git',
                'architecture', 'decision', 'message', 'context', 'workspace', 'integration'}
            if not requested_kinds:
                todo_kinds = {'gates', 'tests', 'worker', 'cuda', 'git', 'architecture',
                    'decision', 'message', 'context', 'workspace', 'integration'}
            if todo_kinds:
                adapter = self.todo_adapter(project) if self.todo_adapter else None
                if adapter is None:
                    data['todo_authority'] = {'status': 'unavailable',
                        'reason': 'todo_evidence_provider_not_configured',
                        'next_call': {'tool': 'machine', 'view': 'diagnostics',
                            'purpose': 'inspect registered Todo provider readiness'}}
                    omissions.append({'reason': 'todo_evidence_provider_not_configured',
                        'owner': 'todo-orchestrator', 'next_call': data['todo_authority']['next_call']})
                else:
                    try:
                        todo_snapshot = self.snapshot_provider(project)
                        todo_result = evidence_for(self.config, todo_snapshot,
                            EvidenceInput(project=project, subject=params['subject'],
                                kinds=sorted(todo_kinds), max_items=limit))
                        data['todo_authority'] = todo_result.data
                        omissions.extend({'reason': warning} for warning in todo_result.warnings)
                        if todo_result.warnings:
                            data['todo_authority']['complete'] = False
                    except Exception as exc:
                        data['todo_authority'] = {'status': 'unavailable',
                            'reason': getattr(exc, 'code', 'todo_evidence_unavailable'),
                            'next_call': {'tool': 'machine', 'view': 'diagnostics',
                                'purpose': 'inspect registered Todo provider readiness'}}
                        omissions.append({'reason': 'todo_evidence_unavailable',
                            'owner': 'todo-orchestrator', 'detail': str(getattr(exc, 'code', type(exc).__name__)),
                            'next_call': data['todo_authority']['next_call']})
            sources.extend(self._anchors(project, data))
        elif tool == 'history':
            data, omissions = self._history(project, snapshot, params)
        elif tool == 'impact':
            if self.impact_provider is None:
                data = {'targets': params.get('targets', []), 'provider': 'unavailable'}
                omissions.append({'reason': 'impact_provider_unavailable'}); status = 'partial'
            else:
                data = self.impact_provider(project=project, detail=internal_detail,
                                            max_payload_bytes=max(1024, self.store.max_payload_bytes - 8192),
                                            **params)
                if hasattr(data, 'model_dump'): data = data.model_dump(mode='json')
        elif tool == 'machine':
            selected_view = params.pop('view', 'host')
            view = {'host': 'system', 'toolchain': 'versions', 'diagnostics': 'services'}.get(selected_view)
            from typing import get_args
            from .services.machine_inspection import MachineDiagnostic
            if view not in get_args(MachineDiagnostic): raise ValueError('unsupported_machine_view')
            if params: raise ValueError('unsupported_machine_parameters')
            if not project or snapshot is None:
                snapshot = ProjectSnapshot(workspace_id=project or 'catalog', repositories={}, observed_at=utc_now())
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
        if snapshot_error:
            omissions.append({'reason': snapshot_error, 'owner': 'todo-orchestrator'})
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
                                   access_scope=self.host.scope(project), normalized_request={'tool': tool, 'project': project},
                                   freshness=freshness, omissions=omissions)
        response = {'status': status, 'packet': packet.packet_id, 'data': packet.payload['data'],
                    'sources': [s.model_dump(exclude_none=True) for s in sources], 'coverage': coverage}
        if tool == 'context' and isinstance(data, dict) and data.get('continuation'):
            response['continuation'] = data['continuation']
        response_size = len(json.dumps(response, ensure_ascii=False).encode())
        if response_size > response_budget:
            # Exact excerpts are indivisible units. Keep them in the immutable
            # packet; offer a continuation only when its complete public packet
            # search response fits within this caller's maximum permitted detail.
            maximum_detail = None
            target = (query.get('target') if tool == 'search' and isinstance(query, dict)
                      and query.get('kind') == 'packet' else packet.packet_id)
            maximum_bytes = response_budget
            required_bytes = self._packet_lookup_response_size(target, project)
            if required_bytes is None:
                required_bytes = response_size
            required_bytes = max(required_bytes, response_size)
            can_continue = False
            continuation = ({'tool': 'search', 'query': {'kind': 'packet', 'target': target}}
                            if required_bytes <= maximum_bytes else None)
            size_limit = {'reason': 'response_exceeds_profile_maximum',
                          'maximum_bytes': maximum_bytes,
                          'required_bytes': required_bytes}
            source_count = len(response['sources'])
            omitted_source_count = 0
            if tool == 'impact':
                if len(response['sources']) > 2:
                    response['sources'] = response['sources'][:2]
                    omitted_source_count = source_count - 2
                response['coverage'] = self._size_limited_coverage(
                    coverage, size_limit=size_limit if not can_continue else None,
                    source_count=omitted_source_count,
                    extra_omissions=[{'reason': 'response_budget', 'unit': 'utf8_bytes'}])
                base_size = len(json.dumps({**response, 'data': {}}, ensure_ascii=False).encode())
                limit_size = len(json.dumps(size_limit, ensure_ascii=False).encode()) + 16 if not can_continue else 0
                preview_budget = max(256, response_budget - base_size - 64 - limit_size)
                response['data'] = self._impact_preview(packet.payload['data'], continuation, preview_budget,
                                                        size_limit=size_limit if not can_continue else None)
                # The wrapper budget includes sources and coverage too. If they
                # are unusually large, retry with the exact remaining space.
                actual_size = len(json.dumps(response, ensure_ascii=False).encode())
                if actual_size > response_budget:
                    response['data'] = self._impact_preview(packet.payload['data'], continuation,
                        max(256, preview_budget - (actual_size - response_budget) - 64 - limit_size),
                        size_limit=size_limit if not can_continue else None)
            else:
                if len(response['sources']) > 2:
                    response['sources'] = response['sources'][:2]
                    omitted_source_count = source_count - 2
                response['data'] = {'continuation': continuation, 'needed_bytes': response_size}
                response['coverage'] = self._size_limited_coverage(
                    coverage, source_count=omitted_source_count,
                    extra_omissions=[{'reason': 'response_budget', 'unit': 'utf8_bytes'}])
            response['status'] = 'partial'
            if tool == 'impact' and len(json.dumps(response, ensure_ascii=False).encode()) > response_budget:
                response['sources'] = []
                response['coverage'] = self._size_limited_coverage(
                    coverage, size_limit=size_limit if not can_continue else None,
                    source_count=len(sources),
                    extra_omissions=[{'reason': 'response_budget', 'unit': 'utf8_bytes'}])
                base_size = len(json.dumps({**response, 'data': {}}, ensure_ascii=False).encode())
                limit_size = len(json.dumps(size_limit, ensure_ascii=False).encode()) + 16 if not can_continue else 0
                response['data'] = self._impact_preview(packet.payload['data'], continuation,
                    max(128, response_budget - base_size - 32 - limit_size),
                    size_limit=size_limit if not can_continue else None)
                if len(json.dumps(response, ensure_ascii=False).encode()) > response_budget:
                    response['data'] = ({'continuation': continuation, 'needed_bytes': response_size}
                                         if can_continue else {'size_limit': size_limit})
                    response['coverage'] = self._size_limited_coverage(
                        coverage, size_limit=size_limit if not can_continue else None,
                        source_count=source_count,
                        extra_omissions=[{'reason': 'response_budget', 'unit': 'utf8_bytes'}])
            if not can_continue and tool != 'impact':
                response['data'] = {'size_limit': size_limit}
                response['sources'] = []
                response['coverage'] = self._size_limited_coverage(
                    coverage, size_limit=size_limit, source_count=source_count)
        return response

    def _ambiguous_project_response(self, tool: str, candidates: tuple[str, ...]) -> dict:
        """Return bounded, allowlist-filtered choices without opening a project."""
        rows = [{"project": project,
                 "name": self.config.workspaces[project].display_name or project}
                for project in candidates]
        next_calls = [{"tool": tool, "arguments": {"project": project}}
                      for project in candidates]
        data = {"resolution": "ambiguous", "candidates": rows,
                "next_calls": next_calls}
        coverage = {"omissions": [{"reason": "project_selector_ambiguous"}],
                    "semantic_revision": None, "context_revision": None,
                    "observed_at": utc_now(), "complete": False}
        payload = {"status": "partial", "data": data, "coverage": coverage,
                   "cursor": None}
        packet = self.store.create(tool=tool, payload=payload, sources=[], parents=[],
            access_scope=self.host.scope(None), normalized_request={
                "tool": tool, "project_selector": "ambiguous"}, freshness={}, omissions=coverage["omissions"])
        return {"status": "partial", "packet": packet.packet_id, "data": data,
                "sources": [], "coverage": coverage, "next_calls": next_calls}

    @staticmethod
    def _size_limited_coverage(coverage, *, size_limit=None, source_count=0, extra_omissions=()):
        """Retain coverage provenance and important omissions in a small limit response."""
        original = list(coverage.get('omissions', [])) if isinstance(coverage, dict) else []
        important_reasons = {'local_worker_state_unavailable', 'cuda_evidence_unavailable',
                             'evidence_unavailable'}
        important = {}
        ordinary = []
        for item in original:
            reason = item.get('reason') if isinstance(item, dict) else None
            if isinstance(reason, str) and reason in important_reasons:
                important.setdefault(reason, item)
            else:
                ordinary.append(item)
        chosen = [*important.values(), *ordinary[:max(0, 8 - len(important))]]
        omissions = []
        for item in chosen:
            if isinstance(item, dict):
                compact = {key: item[key] for key in ('reason', 'unit', 'omitted_count', 'count') if key in item}
                if len(compact) != len(item):
                    compact['details_truncated'] = True
                omissions.append(compact)
            else:
                omissions.append({'reason': 'provider_omission'})
        omitted_count = len(original) - len(chosen)
        if omitted_count:
            omissions.append({'reason': 'coverage_omissions', 'omitted_count': omitted_count})
        if source_count:
            omissions.append({'reason': 'source_locators', 'omitted_count': source_count})
        omissions.extend(extra_omissions)
        if size_limit is not None:
            omissions.append(size_limit)
        return {**(coverage if isinstance(coverage, dict) else {}),
                'complete': False, 'omissions': omissions}

    def _packet_lookup_response_size(self, target, project):
        """Estimate the next public packet-search response, including its wrapper."""
        found = self.store.lookup(target, access_scope=self.host.scope(project))
        if found.status != 'ok' or found.packet is None:
            return None
        data = {'kind': 'packet', 'target': target, 'resolution': 'ok',
                'result': found.packet.payload,
                'sources': [source.model_dump() for source in found.packet.sources]}
        source_locators = self._anchors(project, data)
        response = {
            'status': 'ok',
            # Packet identifiers use this fixed-width representation in storage.
            'packet': 'pkt_' + ('0' * 32),
            'data': data,
            'sources': [source.model_dump(exclude_none=True) for source in source_locators],
            'coverage': {'omissions': [], 'semantic_revision': None,
                         'context_revision': None, 'context_project_uuid': None,
                         'observed_at': utc_now(), 'complete': True},
        }
        return len(json.dumps(response, ensure_ascii=False).encode()) + 64

    @staticmethod
    def _impact_preview(data, continuation, budget, *, size_limit=None):
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
                   'traversal': traversal, 'warnings': data.get('warnings', [])[:1]}
        if continuation is not None:
            preview['continuation'] = continuation
        if size_limit is not None:
            preview['size_limit'] = size_limit
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
                       'affected_node': compact_node(affected), 'unknown_scope': unknown[:1],
                       'omissions': {'section': 'trace_preview', 'full_sha256': full_dependency_digest}}
            if continuation is not None:
                preview['continuation'] = continuation
            if size_limit is not None:
                preview['size_limit'] = size_limit
            while len(encoded(preview)) > budget and preview.get('affected_node'):
                preview.pop('affected_node')
                preview['omissions']['affected_node_omitted'] = True
        return preview

    def _context(self, project):
        metadata = self._project_metadata(project)
        if not self.semantic_provider: return metadata, 'semantic_context_extension_unavailable'
        try:
            result = self.semantic_provider(project)
            if result.get('ok') is False: return metadata, str(result.get('error', 'semantic_context_unavailable'))
            value = result.get('data', result)
            if value.get('status') == 'unavailable': return metadata, 'semantic_context_schema_migration_required'
            current = self._public_context(project, value)
            for key in ('orientation', 'skill_uses', 'declarations', 'metadata_revision',
                        'metadata_scope_matches', 'metadata_relations'):
                if metadata.get(key): current[key] = metadata[key]
            return current, None
        except Exception as exc:
            return metadata, 'semantic_context_unavailable:' + type(exc).__name__

    def _project_metadata(self, project):
        """Read Project Control declarations without opening Todo or creating state."""
        from .project_metadata import ProjectMetadataStore
        workspace = self.registry.workspace(project)
        roots = {alias: str(repository.root.resolve())
                 for alias, repository in sorted(workspace.repositories.items())}
        scope = hashlib.sha256(json.dumps({"workspace": project, "roots": roots}, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        store = ProjectMetadataStore()
        if not store.path.is_file():
            value = {}
        else:
            value = store.read_context(project, scope)
        rows = [*value.get("declarations", []), *value.get("orientation", []),
                *value.get("skill_uses", [])]
        declarations = []
        for row in rows:
            payload = row.get("payload", {})
            anchors = payload.get("anchors", []) if isinstance(payload, Mapping) else []
            repository = payload.get("repository") if isinstance(payload, Mapping) else None
            if not repository and anchors and isinstance(anchors[0], Mapping):
                repository = anchors[0].get("repository")
            declarations.append({**row, "repository_alias": repository,
                "provenance": {"source_system": "project-control", "source_project_id": project,
                    "source_authority_revision": row.get("revision"), "scope_digest": scope}})
        return {"orientation": [r for r in declarations if r.get("kind") == "orientation"],
            "skill_uses": [r for r in declarations if r.get("kind") == "skill_use"],
            "declarations": [r for r in declarations if r.get("kind") not in {"orientation", "skill_use"}],
            "metadata_relations": [r for r in declarations if r.get("kind") == "relation"],
            "metadata_revision": value.get("revision", 0),
            "metadata_scope_matches": value.get("scope_matches", True)}

    def _registered_evidence(self, project, subject, *, limit, artifact_budget):
        """Read explicitly registered peer evidence stores without initializing them."""
        from project_evidence import EvidenceReader
        raw = os.environ.get('PROJECT_CONTROL_EVIDENCE_STORES')
        omissions, records, relations, stores = [], [], [], []
        configured = {}
        if raw:
            try:
                configured = json.loads(raw)
            except json.JSONDecodeError:
                omissions.append({'reason': 'evidence_store_configuration_invalid_json',
                    'owner': 'project-lab', 'next_call': {
                        'operation': 'configure', 'environment': 'PROJECT_CONTROL_EVIDENCE_STORES'}})
                return records, relations, omissions, stores
            if not isinstance(configured, dict):
                omissions.append({'reason': 'evidence_store_configuration_must_be_object',
                    'owner': 'project-lab', 'next_call': {
                        'operation': 'configure', 'environment': 'PROJECT_CONTROL_EVIDENCE_STORES'}})
                return records, relations, omissions, stores
        specs = configured.get(project, [])
        if isinstance(specs, (str, dict)):
            specs = [specs]
        if not isinstance(specs, list):
            omissions.append({'reason': 'evidence_store_registration_invalid', 'project': project})
            return records, relations, omissions, stores
        remaining_artifact_bytes = artifact_budget
        for spec in specs:
            if isinstance(spec, str):
                database, artifact_dir = spec, None
            elif isinstance(spec, dict):
                database = spec.get('database')
                artifact_dir = spec.get('artifact_dir')
            else:
                database = artifact_dir = None
            if not isinstance(database, str) or not database:
                omissions.append({'reason': 'evidence_store_registration_invalid', 'project': project})
                continue
            try:
                reader = EvidenceReader(database, artifact_dir)
                if not reader.available:
                    raise FileNotFoundError('evidence_store_missing')
                selected = reader.list_observations(subject=subject, limit=limit)
                for item in selected:
                    item['application_owner'] = item.get('owner')
                    item['registered_project'] = project
                    item['store'] = 'registered_project-evidence'
                    item['assessments'] = reader.list_assessments(item['observation_id'], limit=limit)
                    locators = []
                    for ref in item.get('source_refs', []):
                        try:
                            ref = ref if isinstance(ref, dict) else dict(ref)
                            owner_project, repository_alias = ref['repository_id'].split('/', 1)
                            if owner_project not in self.host.projects:
                                raise PermissionError('evidence_source_project_not_permitted')
                            repository_alias = self._repository_alias(owner_project, repository_alias)
                            locator = SourceLocator(project=owner_project, repository=repository_alias,
                                path=relative_path(ref['path']), content_sha256=ref['sha256'],
                                revision=ref.get('revision'), line_start=ref.get('start_line'),
                                line_end=ref.get('end_line'))
                            locators.append(locator.model_dump(exclude_none=True))
                        except (KeyError, TypeError, ValueError, PermissionError) as exc:
                            item.setdefault('source_ref_omissions', []).append(str(exc))
                    item['sources'] = locators
                    artifact_contents = []
                    for artifact_ref in item.get('artifact_refs', []):
                        if not isinstance(artifact_ref, dict) or artifact_ref.get('media_type') not in {
                                'application/json', 'text/plain', 'text/markdown'}:
                            continue
                        try:
                            artifact_bytes = reader.get_artifact(artifact_ref.get('sha256', ''))
                            if len(artifact_bytes) > remaining_artifact_bytes:
                                item.setdefault('artifact_omissions', []).append({
                                    'artifact_id': artifact_ref.get('artifact_id'),
                                    'reason': 'profile_artifact_budget', 'required_bytes': len(artifact_bytes),
                                    'remaining_bytes': remaining_artifact_bytes})
                                continue
                            try:
                                content = json.loads(artifact_bytes)
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                content = artifact_bytes.decode('utf-8', errors='replace')
                            artifact_contents.append({**artifact_ref, 'content': content})
                            remaining_artifact_bytes -= len(artifact_bytes)
                        except (OSError, ValueError, IOError) as exc:
                            item.setdefault('artifact_omissions', []).append({
                                'artifact_id': artifact_ref.get('artifact_id'),
                                'reason': 'registered_artifact_unavailable', 'detail': str(exc)})
                    if artifact_contents:
                        item['artifact_contents'] = artifact_contents
                    if item.get('artifact_omissions'):
                        omissions.append({'reason': 'registered_artifact_omission',
                            'observation_id': item.get('observation_id'),
                            'omissions': item['artifact_omissions']})
                records.extend(selected)
                relations.extend(reader.list_relations(subject=subject, limit=limit))
                stores.append({'project': project, 'status': 'available',
                    'observation_count': len(selected), 'complete': len(selected) < limit})
                if len(selected) >= limit:
                    omissions.append({'reason': 'registered_evidence_result_limit', 'project': project,
                        'limit': limit})
            except (OSError, ValueError, sqlite3.Error) as exc:
                omissions.append({'reason': 'registered_evidence_store_unavailable',
                    'project': project, 'detail': str(exc),
                    'next_call': {'operation': 'configure',
                        'environment': 'PROJECT_CONTROL_EVIDENCE_STORES', 'project': project}})
        if not specs:
            omissions.append({'reason': 'registered_evidence_store_not_configured',
                'owner': 'project-lab', 'project': project,
                'next_call': {'operation': 'configure',
                    'environment': 'PROJECT_CONTROL_EVIDENCE_STORES',
                    'example': {project: [{'database': '/path/to/lab-state/evidence.sqlite3',
                        'artifact_dir': '/path/to/lab-state/evidence-artifacts'}]}}})
            stores.append({'project': project, 'status': 'not_registered', 'owner': 'project-lab'})
        return records, relations, omissions, stores

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

    def _source_index(self, project, alias, revision=None):
        from project_source import CppContextCompilerProvider, Repository, SourceIndex
        key = (project, alias, revision)
        if key not in self._source_indices:
            repo = self.registry.repository(project, alias)
            state_root = Path(os.environ.get("PROJECT_CONTROL_SOURCE_STATE_DIR",
                Path.home() / ".cache/project-control/source"))
            state = state_root / project / alias
            deny_patterns = (*DEFAULT_DENY_PATTERNS, *self.config.workspaces[project].deny_patterns)
            providers = []
            raw_exports = os.environ.get("PROJECT_CONTROL_CPP_EXPORTS")
            if raw_exports:
                try:
                    exports = json.loads(raw_exports)
                except json.JSONDecodeError as exc:
                    raise ValueError("cpp_export_configuration_invalid_json") from exc
                if not isinstance(exports, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                        for k, v in exports.items()):
                    raise ValueError("cpp_export_configuration_must_map_repository_keys_to_paths")
                export_path = exports.get(f"{project}/{alias}")
                if export_path:
                    providers.append(CppContextCompilerProvider(export_path))
            self._source_indices[key] = SourceIndex(Repository(repo.root,
                f"{project}/{alias}", state, revision=revision,
                deny_patterns=tuple(deny_patterns)), providers=providers)
        return self._source_indices[key]

    def _repository_alias(self, project, repository):
        workspace = self.registry.workspace(project)
        if repository in workspace.repositories:
            return repository
        try:
            resolved = Path(repository).expanduser().resolve()
        except (TypeError, OSError, RuntimeError):
            resolved = None
        matches = [alias for alias, config in workspace.repositories.items()
            if resolved is not None and config.root.resolve() == resolved]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError('repository_path_is_ambiguous')
        raise ValueError('repository_not_registered')

    def _project_for_uuid(self, project_uuid, repository=None):
        if not isinstance(project_uuid, str) or not project_uuid:
            raise ValueError('registered_relation_project_identity_missing')
        candidates = []
        for candidate in sorted(self.host.projects):
            workspace = self.config.workspaces.get(candidate)
            if workspace is not None and (repository is None or repository in workspace.repositories):
                candidates.append(candidate)
        matches = []
        for candidate in candidates:
            try:
                observed = self.snapshot_provider(candidate)
            except Exception:
                continue
            if observed.project_uuid == project_uuid:
                matches.append(candidate)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError('registered_relation_project_identity_unavailable')
        raise ValueError('registered_relation_project_identity_ambiguous')

    def _source_entity(self, project, query, params):
        kind, target = query['kind'], query['target']
        matches = []
        for alias, index in self._all_source_indexes(project, params.get('repository')):
            if kind == 'path':
                try:
                    paths = index.tree(path=target, limit=2)['paths']
                    if paths:
                        matches.append({'repository': alias, 'path': paths[0], 'resolution': 'resolved'})
                except (OSError, ValueError):
                    pass
                continue
            tree = index.tree(limit=2000)
            if not tree.get('complete'):
                matches.append({'repository': alias, 'resolution': 'coverage_limited', 'cursor': tree.get('cursor')})
            for path in tree['paths']:
                if kind == 'test' and not (Path(path).name.startswith('test_') or path.endswith('_test.py')):
                    continue
                try:
                    structure = index.structure(path)
                except (OSError, ValueError):
                    continue
                for unit in structure.units:
                    if unit.kind not in {'definition', 'class'} or target not in {unit.name, unit.qualified_name}:
                        continue
                    matches.append({'repository': alias, 'path': unit.path, 'name': unit.name,
                        'qualified_name': unit.qualified_name, 'kind': unit.kind,
                        'line_start': unit.line, 'line_end': unit.end_line,
                        'content_sha256': structure.sha256, 'provider': unit.provider,
                        'resolution': 'resolved'})
        complete = not any(item.get('resolution') == 'coverage_limited' for item in matches)
        definitions = [item for item in matches if item.get('line_start')]
        if len(definitions) > 1:
            resolution = 'ambiguous'
        elif definitions:
            resolution = 'resolved'
        elif kind == 'path' and matches:
            resolution = 'resolved'
        else:
            resolution = 'not_found'
        return {'kind': kind, 'target': target, 'resolution': resolution,
            'matches': matches, 'complete': complete,
            'basis': 'structural observation' if kind != 'path' else 'registered source path'}

    def _all_source_indexes(self, project, repository=None, revision=None):
        aliases = [self._repository_alias(project, repository)] if repository else sorted(self.registry.workspace(project).repositories)
        return [(alias, self._source_index(project, alias, revision)) for alias in aliases]

    def _context_sources(self, project, *, targets, include=None, budget_bytes=8192, cursor=None):
        grouped = {}
        aliases = self.registry.workspace(project).repositories
        for value in targets:
            if isinstance(value, dict):
                target = dict(value)
                alias = target.get('repository')
                kind = target.get('kind', 'path')
                target_value = target.get('target')
                if alias is None and len(aliases) == 1:
                    alias = next(iter(aliases))
                if not isinstance(target_value, str) or not target_value:
                    raise ValueError('typed_retrieval_target_invalid')
                if alias is None:
                    raise ValueError('typed_retrieval_target_requires_repository')
                alias = self._repository_alias(project, alias)
                revision = target.get('revision')
                key = (alias, revision)
                source_target = {'kind': 'path' if kind == 'file' else kind,
                    'target': target_value, 'repository_id': f'{project}/{alias}'}
                for field in ('line_start', 'line_end'):
                    if target.get(field) is not None:
                        source_target[field] = target[field]
                grouped.setdefault(key, []).append(source_target)
            elif isinstance(value, str) and ':' in value:
                alias, target = value.split(':', 1)
                alias = self._repository_alias(project, alias)
                grouped.setdefault((alias, None), []).append(target)
            elif isinstance(value, str) and len(aliases) == 1:
                alias = next(iter(aliases))
                grouped.setdefault((alias, None), []).append(value)
            else:
                raise ValueError('target_requires_registered_repository')
        if not grouped:
            return {'sections': {}, 'complete': False,
                    'omissions': ['select an explicit source path or typed symbol target']}, []
        profile_budget = RESPONSE_BUDGETS_BYTES['observer' if self.host.profile == 'observer' else 'local']
        budget = max(1, min(int(budget_bytes), profile_budget - (4096 if profile_budget > 8192 else 2400)))
        if self.host.profile != 'observer':
            budget = min(budget, 5600)
        packets = []
        each_budget = max(1, budget // len(grouped))
        for (alias, revision), selected_targets in grouped.items():
            packets.append((alias, revision, self._source_index(project, alias, revision).context(selected_targets,
                include=include or ['implementation'], budget_bytes=each_budget, cursor=cursor)))
        sections, sources, omissions, continuations = {}, [], [], []
        used = 0
        complete = True
        cursors = []
        for alias, revision, packet in packets:
            used += packet.used_bytes
            complete = complete and packet.complete
            omissions.extend(packet.omissions)
            if packet.cursor:
                cursors.append({'repository': alias, 'cursor': packet.cursor})
            continuations.extend({'repository': alias, **item} for item in packet.continuations)
            for section, results in packet.sections.items():
                sections.setdefault(section, [])
                for result in results:
                    if hasattr(result, 'path'):
                        source = result.source
                        locator = SourceLocator(project=project, repository=alias, path=result.path,
                            content_sha256=result.sha256, revision=source.revision,
                            line_start=source.start_line, line_end=source.end_line)
                        sources.append(locator)
                        sections[section].append({'path': result.path, 'content': result.content,
                            'source': locator.model_dump(exclude_none=True), 'complete': result.complete,
                            **({'cursor': result.cursor, 'omissions': list(result.omissions)} if result.cursor else {})})
                    else:
                        record = dict(result) if isinstance(result, dict) else {'value': str(result)}
                        refs = record.get('source_refs', record.get('sources', []))
                        locators = []
                        for ref in refs if isinstance(refs, (list, tuple)) else []:
                            ref = ref if isinstance(ref, dict) else {name: getattr(ref, name, None) for name in
                                ('repository_id', 'path', 'sha256', 'content_sha256', 'revision', 'start_line', 'end_line')}
                            source_project = ref.get('project', project)
                            if source_project not in self.host.projects:
                                omissions.append('relationship_source_project_not_permitted')
                                continue
                            repository_id = ref.get('repository_id', ref.get('repository', alias))
                            source_alias = self._repository_alias(source_project, repository_id)
                            try:
                                locator = SourceLocator(project=source_project, repository=source_alias,
                                    path=ref['path'], content_sha256=ref.get('sha256', ref.get('content_sha256')),
                                    revision=ref.get('revision'), line_start=ref.get('start_line'),
                                    line_end=ref.get('end_line'))
                                locators.append(locator.model_dump(exclude_none=True)); sources.append(locator)
                            except (KeyError, TypeError, ValueError):
                                omissions.append('relationship_source_locator_unavailable')
                        if locators:
                            record['sources'] = locators
                        sections[section].append(record)
        metadata = self._project_metadata(project)
        if include and 'guidance' in include:
            for declaration in metadata.get('metadata_relations', []):
                payload = declaration.get('payload', {})
                source_ref, target_ref = payload.get('source'), payload.get('target')
                if not isinstance(source_ref, Mapping) or not isinstance(target_ref, Mapping):
                    continue
                sections.setdefault('guidance', []).append({
                    'subject': source_ref, 'predicate': payload.get('relation'),
                    'object': target_ref, 'basis': 'registered_relation',
                    'relation_id': declaration.get('id'),
                    'provenance': declaration.get('provenance'),
                    'source_refs': payload.get('anchors', []),
                })
        # Explicit guidance declarations can point into a separately
        # registered repository. Expand only declared rows; lexical mentions
        # remain discovery results and never trigger cross-repository reads.
        guidance_remaining = max(0, budget - used)
        for relation in sections.get('guidance', []):
            target = relation.get('object') if isinstance(relation, dict) else None
            target_project = project
            target_alias = target_path = None
            if relation.get('basis') == 'guidance' and isinstance(target, str) and ':' in target:
                qualifier, target_path = target.split(':', 1)
                if '/' in qualifier:
                    target_project, target_alias = qualifier.split('/', 1)
                else:
                    target_alias = qualifier
            elif relation.get('basis') == 'registered_relation' and isinstance(target, Mapping):
                try:
                    target_project = self._project_for_uuid(target.get('project_uuid'), target.get('repository'))
                    target_alias = target.get('repository')
                    target_path = target.get('id') if target.get('kind') == 'path' else None
                except (ValueError, PermissionError) as exc:
                    relation['target_status'] = 'unavailable'
                    relation['target_reason'] = str(exc)
                    omissions.append({'reason': 'registered_relation_target_unavailable',
                        'relation_id': relation.get('relation_id'), 'detail': str(exc)})
                    continue
            else:
                continue
            if not target_path:
                continue
            try:
                if target_project not in self.host.projects:
                    raise PermissionError('cross_project_not_permitted')
                target_alias = self._repository_alias(target_project, target_alias)
                target_path = relative_path(target_path)
                if guidance_remaining <= 0:
                    raise ValueError('context_budget')
                index = self._source_index(target_project, target_alias)
                # Attach the declaration file as provenance for the edge.
                declaration_subject = relation.get('subject', '')
                if relation.get('basis') == 'guidance' and isinstance(declaration_subject, str):
                    declaration_project = project
                    declaration_alias = (declaration_subject.split('/', 1)[1]
                        if declaration_subject.startswith(project + '/')
                        else None)
                    declaration_index = self._source_index(declaration_project,
                        self._repository_alias(declaration_project, declaration_alias)) if declaration_alias else None
                else:
                    declaration_index = None
                if declaration_index is not None:
                    declaration = declaration_index.read('.project-context.json', max_bytes=4096)
                    if declaration.source:
                        locator = SourceLocator(project=declaration_project, repository=declaration_alias,
                            path='.project-context.json', content_sha256=declaration.sha256,
                            revision=declaration.source.revision)
                        relation.setdefault('sources', []).append(locator.model_dump(exclude_none=True))
                        sources.append(locator)
                packet = index.context([{'kind': 'path', 'target': target_path,
                    'repository_id': f'{target_project}/{target_alias}'}], include=['implementation'],
                    budget_bytes=guidance_remaining)
                excerpts = []
                for item in packet.sections.get('implementation', ()):
                    if not hasattr(item, 'path'):
                        continue
                    locator = SourceLocator(project=target_project, repository=target_alias, path=item.path,
                        content_sha256=item.sha256, revision=item.source.revision,
                        line_start=item.source.start_line, line_end=item.source.end_line)
                    sources.append(locator)
                    excerpts.append({'path': item.path, 'content': item.content,
                        'source': locator.model_dump(exclude_none=True), 'complete': item.complete,
                        **({'cursor': item.cursor} if item.cursor else {})})
                relation['target_context'] = excerpts
                used += packet.used_bytes
                guidance_remaining = max(0, budget - used)
                if not packet.complete:
                    omissions.extend(packet.omissions or ['declared_guidance_incomplete'])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                relation['target_context'] = []
                relation['target_status'] = 'unavailable'
                relation['target_reason'] = str(exc) or 'declared_guidance_unavailable'
                omissions.append({'reason': 'declared_guidance_unavailable', 'target': target,
                    'detail': str(exc)})
        continuation = ({'tool': 'context', 'project': project, 'targets': targets,
            'include': include or ['implementation'], 'budget_bytes': budget_bytes,
            **({'cursor': cursors[0]['cursor']} if len(cursors) == 1 else {})}
            if cursors else None)
        if continuations:
            continuation = {'calls': [{'tool': 'read', 'project': project,
                'repository': item['repository'], 'paths': [{'path': item['path'],
                    'cursor': item['cursor']}], 'budget_bytes': max(1, budget // len(continuations))}
                for item in continuations]}
        return {'sections': sections, 'budget_bytes': budget, 'used_bytes': used,
            'complete': complete and not omissions, 'omissions': omissions,
            'cursor': cursors[0]['cursor'] if len(cursors) == 1 else None,
            'associations': [association for _alias, _revision, packet in packets
                for association in packet.associations],
            'continuation': continuation, 'repositories': sorted({alias for alias, _revision in grouped})}, sources

    def _trace_sources(self, project, *, targets, predicates=None, direction='both', max_hops=2, limit=100):
        direction_value = {'incoming': 'in', 'outgoing': 'out', 'both': 'both'}[direction]
        results = {}
        selected = {}
        aliases = self.registry.workspace(project).repositories
        for value in targets:
            if isinstance(value, dict):
                alias = value.get('repository')
                target = value.get('target')
                if alias is None and len(aliases) == 1: alias = next(iter(aliases))
                if not isinstance(target, str) or not alias: continue
                selected.setdefault(self._repository_alias(project, alias), []).append(target)
            elif isinstance(value, str) and ':' in value:
                alias, target = value.split(':', 1)
                selected.setdefault(self._repository_alias(project, alias), []).append(target)
        for alias, index in self._all_source_indexes(project):
            local = selected.get(alias, [])
            results[alias] = index.trace(local, predicates=predicates or [], direction=direction_value,
                max_hops=max_hops, limit=max(1, min(1000, limit))) if local else {
                'edges': [], 'complete': True, 'coverage': 'no targets selected in repository'}
        return {'repositories': results,
            'complete': all(value.get('complete', False) for value in results.values()),
            'direction': direction}

    def _inspect(self, project, *, operation='tree', repository=None, path=None, limit=100, cursor=None):
        if repository is not None:
            repository = self._repository_alias(project, repository)
        indexes = self._all_source_indexes(project, repository)
        if operation in {'tree', 'files'}:
            values = {alias: index.tree(path=path, limit=max(1, min(2000, limit)), cursor=cursor)
                      for alias, index in indexes}
            return {'repositories': values, 'complete': all(value.get('complete', False) for value in values.values())}
        if operation == 'git':
            identities = {}
            for alias, _index in indexes:
                identity = GitReadAdapter(self.registry.repository(project, alias).root).identity()
                identities[alias] = {'commit': identity.commit, 'dirty': identity.dirty,
                    'status_fingerprint': identity.status_fingerprint}
            return {'repositories': identities, 'complete': True}
        if operation == 'build':
            names = {'pyproject.toml', 'CMakeLists.txt', 'Makefile', 'meson.build', 'Cargo.toml',
                'package.json', 'go.mod', 'build.gradle', 'pom.xml'}
            values = {}
            for alias, _index in indexes:
                root = self.registry.repository(project, alias).root
                found = []
                for name in sorted(names):
                    if (root / name).is_file():
                        read, _ = self._read(project, paths=[name], repository=alias, detail='standard')
                        found.extend(read['files'])
                values[alias] = found
            return {'repositories': values, 'complete': True}
        raise ValueError('unsupported_inspection_operation')

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
        native_kind = 'context_fragment' if kind == 'native_context' else kind
        if native_kind in {'candidate', 'effect', 'resource', 'child', 'recovery',
                           'context_fragment', 'legacy_history', 'legacy_task'}:
            if self.native_reader is None:
                return {'kind': kind, 'target': target, 'resolution': 'unavailable',
                        'reason': 'native_todo_read_unavailable', '_status': 'partial'}
            result = self.native_reader(project, native_kind, target)
            result['kind'] = kind
            if result.get('resolution') != 'resolved':
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

    def _read(self, project, *, paths, repository=None, revision=None, detail='compact', budget_bytes=None):
        if not isinstance(paths, list) or not 1 <= len(paths) <= 32: raise ValueError('read_requires_1_to_32_paths')
        repository = self._repository_alias(project, repository) if repository is not None else None
        repo = self.registry.repository(project, repository)
        deny = [*DEFAULT_DENY_PATTERNS, *self.config.workspaces[project].deny_patterns]
        revision_error = None
        try: commit = GitReadAdapter(repo.root).verify_revision(revision) if revision else None
        except (ValueError, RuntimeError): commit, revision_error = None, 'invalid_revision'
        files, sources = [], []
        remaining = budget_bytes
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
                if spec.get('cursor') is not None:
                    from project_source import SourceIndex
                    max_bytes = max(1, min(int(spec.get('max_bytes', remaining or 64000)), remaining or 64000))
                    source_result = self._source_index(project, repo.alias, commit).read(path,
                        start_line=first if first != 1 or last is not None else None,
                        end_line=last, max_bytes=max_bytes, cursor=spec['cursor'])
                    selected = source_result.content or ''
                    clean = redact_text(selected)
                    item.update(status='redacted' if clean != selected else 'ok', content=clean,
                        line_start=source_result.source.start_line or first,
                        line_end=source_result.source.end_line or last,
                        content_sha256=source_result.sha256, revision=commit,
                        exact=clean == selected, complete=source_result.complete,
                        cursor=source_result.cursor)
                    sources.append(SourceLocator(project=project, repository=repo.alias, path=path,
                        content_sha256=source_result.sha256, revision=commit,
                        line_start=source_result.source.start_line,
                        line_end=source_result.source.end_line))
                    remaining = max(0, remaining - len(clean.encode())) if remaining is not None else None
                    files.append(item)
                    continue
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
                if remaining is not None:
                    encoded = clean.encode()
                    if len(encoded) > remaining:
                        clean = encoded[:remaining].decode('utf-8', errors='ignore')
                        item['content'] = clean
                        item.update(complete=False, omission='context_budget')
                        remaining = 0
                    else:
                        remaining -= len(encoded)
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
        if self.native_reader is not None:
            native = self.native_reader(project, 'legacy_history', request.subject)
            if native.get('resolution') == 'resolved':
                data['legacy_history'] = native.get('result', {})
            else:
                data['legacy_history'] = {'status': 'unavailable'}
                omissions.append({'reason': 'legacy_history_unavailable'})
        data['selectors'] = request.model_dump(exclude_none=True)
        return data, omissions
