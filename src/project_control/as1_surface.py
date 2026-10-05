"""Startup composition and MCP adapters for the qualified AS1 producer ports."""
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Literal, get_args

from mcp.types import ToolAnnotations
from pydantic import ConfigDict, Field, ValidationError, create_model
from .as1_context import ContextHost, InformationService
from .as1_contracts import ExactEntityQuery, ImpactTarget
from .as1_control import ControlService, ProjectAmendment, MaintenanceRequest
from .as1_jobs import JobService, TrustedObserverFactory, InvalidToolArguments, ObserverLogArguments
from .as1_packets import SQLitePacketStore
from .as1_skill import SkillService, SkillObserverFactory, _verified_reads, _entry_precedes_resource
from .as1_trace import TraceService
from .config import configured_observer_skills_root
from .observer_analysis import SkillsObserverAnalysisProvider, observer_analysis_state_root
from .profiles import MCPProfile
from .models import (DeltaSince, EvidenceInput, HistoryTraceInput, InspectInput,
                     ArchitectureContextInput, SourceContextInput, CoordinationViewInput)
from .services.machine_inspection import MachineDiagnostic

# Qualified inquiry-cache producer receipt; supplied by root after CPU acceptance.
QUALIFIED_OBSERVER_RUNTIME_SHA256 = '6f170e8373a37f0e0c1977ae591319d27cc785e60e63453f0963ea4f5d8a2768'
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
ANALYSIS_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
_NATIVE_INPUT_ERROR_TITLES = frozenset(model.__name__ for model in (
    DeltaSince, EvidenceInput, HistoryTraceInput, InspectInput,
    ArchitectureContextInput, SourceContextInput, CoordinationViewInput))
_WORKER_EXACT_QUERY = create_model('ObserverExactEntityQuery', __base__=ExactEntityQuery,
    kind=(Literal[(*get_args(InspectInput.model_fields['kind'].annotation),
                   'packet', 'investigation', 'registration')], ...))


def observer_tool_argument_models(profile):
    """Describe the internal information adapter, using native field contracts.

    Project is optional because the durable admission scope supplies its default.
    Detail follows the original inquiry profile, never the dispatcher's profile.
    """
    detail = Literal['compact', 'standard', 'extended'] if profile == 'observer' else Literal['compact', 'standard']
    common = {'project': (str | None, None), 'detail': (detail, 'compact')}
    def native_fields(model, names):
        return {name: (model.model_fields[name].annotation, model.model_fields[name]) for name in names}
    fields = {
        'overview': {},
        'delta': {'since': (str | DeltaSince, ...)},
        'frontier': {'scope': (dict[str, Any] | None, None)},
        'search': {'query': (str | _WORKER_EXACT_QUERY, ...), 'scope': (dict[str, Any] | None, None)},
        'evidence': native_fields(EvidenceInput, ('subject', 'kinds', 'max_items')),
        'history': native_fields(HistoryTraceInput, tuple(name for name in HistoryTraceInput.model_fields if name not in {'project', 'detail'})),
        'impact': {'targets': (list[ImpactTarget], Field(min_length=1, max_length=32)),
                   'change_class': (Literal['body', 'interface', 'configuration', 'generator', 'removal', 'unknown'], 'unknown'),
                   'mode': (Literal['paths', 'snippets'], 'paths')},
        'machine': {'query_or_view': (MachineDiagnostic, 'host_memory')},
    }
    return {name: create_model('Observer' + name.title() + 'Arguments',
        __config__=ConfigDict(extra='forbid', strict=True), **common, **arguments)
        for name, arguments in fields.items()}


def observer_tool_argument_schemas(profile):
    schemas = {name: model.model_json_schema() for name, model in observer_tool_argument_models(profile).items()}
    schemas['log'] = ObserverLogArguments.model_json_schema()
    command = create_model('ObserverCommandArguments', __config__=ConfigDict(extra='forbid', strict=True),
        argv=(list[str], Field(min_length=1, max_length=128)), cwd=(str, ...),
        timeout_seconds=(int | float, Field(default=10, gt=0, le=60)),
        max_output_bytes=(int, Field(default=8192, ge=1, le=65536)))
    schemas['command'] = command.model_json_schema()
    def compact(value):
        if isinstance(value, dict):
            return {key: compact(item) for key, item in value.items() if key not in {'title', 'description'}}
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value
    return compact(schemas)


# Broker mechanics remain private even when a producer nests a result/continuation.
_PRIVATE_INQUIRY_FIELDS = {'job', 'observations', 'job_id', 'prior_job', 'attempt',
    'lease', 'lease_until', 'lease_owner', 'queue_position', 'queue', 'poll',
    'accepted', 'retry', 'immediate', 'request_id', 'execution_question',
    'refresh_context', 'deadline_epoch', 'attempt_generation', 'lease_epoch', 'poll_after_seconds',
    'attempts', 'leases', 'lease_expires_at', 'lease_expires_epoch', 'lease_seconds',
    'attempt_count', 'scheduler_generation', 'queue_depth'}


def public_inquiry(value):
    if isinstance(value, dict):
        if value.get('status') == 'thinking':
            return {'status': 'thinking', 'message': 'Read-only analysis is in progress. Continue useful work and repeat the identical question later; avoid submitting variants.'}
        if value.get('status') == 'busy':
            return {'status': 'busy', 'message': 'Read-only analysis is busy. This question was not accepted. Use search, read or evidence to contextualize or refine a later question.'}
        return {key: public_inquiry(item) for key, item in value.items()
                if key not in _PRIVATE_INQUIRY_FIELDS}
    if isinstance(value, list):
        return [public_inquiry(item) for item in value]
    return value


class InquiryFreshness:
    """Validate the retained material dependency manifest, never repository HEAD."""
    def __init__(self, composition):
        self.c = composition

    def __call__(self, job):
        c, scope = self.c, job['scope']
        changed, checked, seen = [], [], set()
        roots = [c.skills.root]
        project = scope.get('project')
        permitted_projects = c.host.projects if project == 'catalog' else ({project} & c.host.projects)
        for permitted in permitted_projects:
            roots += [r.root for r in c.control.registry.workspace(permitted).repositories.values()]

        def file_hash(path):
            path = Path(path)
            for root in roots:
                root = Path(root).absolute()
                if path.is_absolute() and path.is_relative_to(root):
                    try:
                        return hashlib.sha256(InformationService._working_bytes(root, path.relative_to(root).as_posix())).hexdigest()
                    except (OSError, ValueError):
                        return None
            return None

        def dependency(key):
            if key.startswith('file:'):
                target = key[5:]
                if target.startswith('/'):
                    return file_hash(target)
                alias, _, path = target.partition('/')
                if project in c.host.projects:
                    try:
                        return file_hash(c.control.registry.repository(project, alias).root / path)
                    except (KeyError, ValueError):
                        return None
            for prefix, provider in (('semantic_revision:', lambda p: c.information.snapshot_provider(p).todo_revision),
                                     ('semantic_context_revision:', lambda p: c.control.project_context(p).get('project_revision'))):
                if key.startswith(prefix):
                    target = key[len(prefix):]
                    if target not in permitted_projects:
                        return None
                    try:
                        value = provider(target)
                        return hashlib.sha256(str(value).encode()).hexdigest() if value is not None else None
                    except (OSError, ValueError, KeyError):
                        return None
            return None

        def visit(ref, result=False):
            if ref in seen:
                return
            seen.add(ref)
            lookup = c.store.lookup(ref, access_scope=scope)
            if lookup.status != 'ok':
                changed.append({'reference': ref, 'reason': lookup.status}); return
            packet = lookup.packet
            for parent in packet.parents:
                visit(parent)
            nested = packet.payload.get('packet')
            if isinstance(nested, str):
                visit(nested)
                return
            reads = packet.payload.get('source_reads', []) if packet.tool == 'command' else []
            if reads:
                for read in reads:
                    valid = (packet.payload.get('status') == 'completed' and packet.payload.get('exit_code') == 0
                             and not packet.payload.get('truncated') and not packet.payload.get('timed_out'))
                    actual = file_hash(read.get('path', '')) if valid and read.get('method') == 'direct_cat' else None
                    checked.append({'path': read.get('path'), 'content_sha256': read.get('content_sha256')})
                    if not actual or actual != read.get('content_sha256'):
                        changed.append({'path': read.get('path'), 'reason': 'unverified' if actual is None else 'changed'})
            elif not result or packet.sources or (packet.freshness or {}).get('dependencies'):
                def packet_dependency(key):
                    matches = [source for source in packet.sources
                               if key == f'file:{source.repository}/{source.path}']
                    if matches and not matches[0].repository.startswith('/'):
                        values = set()
                        for source in matches:
                            if source.project not in permitted_projects:
                                return None
                            try:
                                values.add(file_hash(c.control.registry.repository(source.project, source.repository).root / source.path))
                            except (OSError, KeyError, ValueError):
                                return None
                        return next(iter(values)) if len(values) == 1 else None
                    return dependency(key)
                assembled = c.store.assemble_hints([ref], access_scope=scope, current_dependencies=packet_dependency,
                                                   budget_bytes=8*1024*1024)
                checked.extend(s.model_dump() for s in packet.sources)
                checked.extend({'dependency': k} for k in (packet.freshness or {}).get('dependencies', {}))
                changed.extend(o for o in assembled['omissions'] if o['reason'] != 'duplicate_content')
        # Accepted finding citations define answer dependencies. Observations and
        # the derived result's aggregate provenance also contain incidental reads.
        citations = [ref for finding in job.get('findings', [])
                     for ref in finding.get('evidence_packets', [])]
        refs = list(dict.fromkeys(citations if citations else
                                 job.get('hints', []) + job.get('evidence_packets', [])))
        for ref in refs:
            visit(ref)
        if job.get('result_packet'):
            if citations:
                result = c.store.lookup(job['result_packet'], access_scope=scope)
                if result.status != 'ok':
                    changed.append({'reference': job['result_packet'], 'reason': result.status})
            else:
                visit(job['result_packet'], result=True)
        else:
            changed.append({'reason': 'result_missing'})
        if job.get('mode') == 'skill' and job.get('result_packet'):
            result = c.store.lookup(job['result_packet'], access_scope=scope)
            stored = c.jobs.lookup(job['job_id'], access_scope=scope)
            reads = _verified_reads(c.store, job, stored.get('observations', []), scope)
            selections = (result.packet.payload.get('skill_selection') or {}).get('selections', []) if result.status == 'ok' else []
            if not selections:
                changed.append({'reason': 'selection_manifest_missing'})
            for item in selections:
                reader = c.skills.readers.get(item.get('skill'))
                entry = reads.get(str(reader.root / 'SKILL.md')) if reader else None
                resource = reads.get(str(reader.root / item.get('resource', ''))) if reader else None
                if (not entry or not resource or not _entry_precedes_resource(entry, resource)
                        or resource.get('content_sha256') != item.get('content_sha256')):
                    changed.append({'skill': item.get('skill'), 'resource': item.get('resource'),
                                    'reason': 'selection_proof_missing'})
                # Selection authority remains material even when findings cite
                # only the selected resource and omit its prerequisite entry.
                for read in (entry, resource):
                    if read:
                        actual = file_hash(read.get('path', ''))
                        checked.append({'path': read.get('path'), 'content_sha256': read.get('content_sha256')})
                        if not actual or actual != read.get('content_sha256'):
                            changed.append({'path': read.get('path'),
                                            'reason': 'unverified' if actual is None else 'changed'})
        if not checked:
            changed.append({'reason': 'dependency_manifest_missing'})
        return {'fresh': not changed, 'changed_sources': changed, 'dependencies': checked}


class SurfaceComposition:
    def start(self):
        if self.jobs.worker_factory is not None:
            self.jobs.start()

    def close(self):
        stopped = self.jobs.shutdown()
        close = getattr(self.backend, 'close', None)
        if stopped and close:
            close()
        return stopped

    def publish_context(self, protocol, workflow_handle, request):
        if set(request) - {'kind', 'task_id', 'payload'}:
            raise ValueError('invalid_publication_fields')
        capability = protocol.capabilities.resolve(workflow_handle,
            required_operation='coordinate:publish_project_context', expected_class='first_class')
        root = capability.repository_root
        matches = [project for project in self.host.projects
                   if self.control.registry.workspace(project).authority_repository
                   and self.control.registry.repository(project,
                       self.control.registry.workspace(project).authority_repository).root.resolve() == root]
        if len(matches) != 1:
            raise PermissionError('publication_registered_authority_required')
        return self.control.publish_workflow_context(matches[0], request,
            workflow_handle=workflow_handle, protocol=protocol)

    def scope(self, project):
        if project is not None and project not in self.host.projects:
            raise PermissionError('project_not_permitted')
        scope = self.host.scope(project)
        if project is None:
            scope['catalog_projects'] = sorted(self.host.projects)
        return scope

    def can_execute_inquiry(self, job, shared):
        project = job.scope.get('project')
        if project == 'catalog':
            manifest = job.scope.get('catalog_projects')
            return manifest is None or set(manifest) <= self.host.projects
        return project in self.host.projects

    def inquiry_access(self, required, supplied, *, log=False):
        context = JobService.inquiry_context(supplied)
        if not log and required != context:
            return False
        permitted = self.host.projects & set(supplied.get('catalog_projects', self.host.projects))
        if required.get('project') == 'catalog':
            return ('catalog_projects' in required
                    and set(required['catalog_projects']) <= permitted)
        return (required.get('project') in permitted
                and {k: v for k, v in required.items() if k != 'project'}
                    == {k: v for k, v in context.items() if k not in {'project', 'catalog_projects'}})

    def packet_access(self, required, supplied, sources):
        """Trusted host source authority, independent of reader identity/role."""
        project = required.get('project')
        permitted = self.host.projects & set(supplied.get('catalog_projects', self.host.projects))
        if project is not None and project != 'catalog' and project not in permitted:
            return False
        if any(source.get('project') not in {*permitted, 'skills', 'catalog'} for source in sources):
            return False
        manifest = required.get('catalog_projects')
        if manifest is not None and not set(manifest) <= permitted:
            return False
        # These values come from registered host authority, never tool arguments.
        trusted = {**supplied, 'project': project}
        if manifest is not None:
            trusted['catalog_projects'] = manifest
        return SQLitePacketStore._authorized(required, trusted)


def compose_surface(runtime, profile, *, host=None, state_directory=None, backend=None,
                    observer_runtime_sha256=None, coder_claim_provider=None, command_port=None):
    c = SurfaceComposition()
    config = runtime.config
    canonical = 'coder' if profile == MCPProfile.CODEX else profile.value
    c.host = host or ContextHost(canonical, 'project-control-' + canonical, frozenset(config.workspaces))
    if c.host.profile != canonical or not c.host.projects <= config.workspaces.keys():
        raise ValueError('startup_host_profile_or_scope_mismatch')
    c.store = SQLitePacketStore(state_directory or observer_analysis_state_root() / 'as1')
    c.control = ControlService(config, c.host, coder_claim_provider=coder_claim_provider)
    c.trace = TraceService(config, lambda project: runtime.snapshot(project), c.host, semantic_provider=c.control.project_context)
    c.information = InformationService(config, c.store, lambda project: runtime.snapshot(project), c.host,
        semantic_provider=c.control.project_context, impact_provider=c.trace, todo_adapter=runtime.todo_adapter)
    c.control.information_service = c.information
    root = configured_observer_skills_root(config)
    c.backend = backend or SkillsObserverAnalysisProvider()
    roots = [config.workspaces[p].repositories[a].root for p in sorted(c.host.projects)
             for a in config.workspaces[p].repositories]
    # Freeze labels independently of the global model/service state. Installed
    # Skills roots are navigation resources, never implicit inquiry targets.
    inquiry_repositories = tuple((project, alias, str(repository.root))
        for project in sorted(c.host.projects)
        for alias, repository in sorted(config.workspaces[project].repositories.items()))
    installed_skill_roots = (str(root),)
    def inquiry_context(job):
        scope = job.scope
        project = scope.get('project')
        projects = (set(scope.get('catalog_projects', c.host.projects))
                    if project in {None, 'catalog'} else {project})
        if not projects <= c.host.projects:
            raise PermissionError('job_scope_not_permitted')
        return {'inquiry_repositories': [
            {'project': project, 'repository': alias, 'root': repository_root}
            for project, alias, repository_root in inquiry_repositories if project in projects],
            'installed_skill_roots': list(installed_skill_roots),
            'tool_argument_schemas': observer_tool_argument_schemas(scope['profile'])}
    c.worker_unavailable = None
    c.command = command_port
    factory = None
    try:
        catalog = json.loads((root / 'integrations/native-skill-catalog.json').read_text())
        skills = {entry['name']: {'name': entry['name'], 'root': str(root / entry['name'])}
                  for entry in catalog['entries'] if entry.get('status') == 'accessible'}
        def execution_permitted(scope, shared):
            from types import SimpleNamespace
            return c.can_execute_inquiry(SimpleNamespace(scope=scope), shared)

        def information_tool(name, arguments, scope):
            # Jobs carry a trusted admission scope; requests cannot override it.
            if not execution_permitted(scope, scope.get('_shared_inquiry', False)):
                raise PermissionError('job_scope_not_permitted')
            args = dict(arguments)
            project = args.pop('project', None)
            if project is not None and not isinstance(project, str):
                raise InvalidToolArguments('project must be a string or null')
            if scope['project'] != 'catalog' and project not in {None, scope['project']}:
                raise PermissionError('job_project_not_permitted')
            project = project or (None if scope['project'] == 'catalog' else scope['project'])
            if project is not None and project not in c.host.projects:
                raise PermissionError('job_project_not_permitted')
            if args.get('detail') == 'extended' and scope['profile'] != 'observer':
                raise PermissionError('detail_not_permitted')
            try:
                observer_tool_argument_models(scope['profile'])[name].model_validate({'project': project, **args})
                if project is None and name not in {'overview', 'search', 'machine'}:
                    raise InvalidToolArguments('project_required')
                if name in {'evidence', 'history'}:
                    model = EvidenceInput if name == 'evidence' else HistoryTraceInput
                    # The outer packet detail is distinct from the underlying
                    # native evidence/history detail vocabulary.
                    model.model_validate({'project': project, **{key: value for key, value in args.items() if key != 'detail'}})
            except ValidationError as error:
                raise InvalidToolArguments(str(error)) from error
            class AdmissionHost(ContextHost):
                def scope(self, project):
                    return {k: v for k, v in scope.items() if not k.startswith('_')}
            worker_host = AdmissionHost(scope['profile'], scope['principal'],
                                        frozenset(scope.get('catalog_projects', c.host.projects)))
            info = InformationService(config, c.store, runtime.snapshot, worker_host,
                semantic_provider=c.control.project_context, impact_provider=c.trace,
                todo_adapter=runtime.todo_adapter, job_lookup=lambda ident, access: c.jobs.lookup(ident, access_scope=access))
            try:
                return info.call(name, project=project, **args)
            except ValidationError as error:
                # Native request DTOs may validate deeper semantic parameters.
                # Output packet/result DTO failures remain real backend errors.
                if error.title not in _NATIVE_INPUT_ERROR_TITLES:
                    raise
                raise InvalidToolArguments(str(error)) from error
        class ScopedTrustedObserverFactory(TrustedObserverFactory):
            def __call__(self, service, job):
                scope = job.scope
                shared = service.is_inquiry(job.job_id)
                if not execution_permitted(scope, shared):
                    raise PermissionError('job_scope_not_permitted')
                # Command roots follow the durable admission domain. Catalog and
                # skill jobs never acquire implicit project filesystem access.
                permitted = [root]
                if job.mode != 'skill' and scope['project'] != 'catalog':
                    permitted = [r.root for r in config.workspaces[scope['project']].repositories.values()] + [root]
                bound = TrustedObserverFactory(root, self.digest, backend=self.backend,
                    roots=permitted, tools=lambda name, args, original: self.tools(name, args, {**original, '_shared_inquiry': shared}), skills=self.skills)
                return bound(service, job)
        trusted = ScopedTrustedObserverFactory(root, observer_runtime_sha256 or QUALIFIED_OBSERVER_RUNTIME_SHA256,
            backend=c.backend, roots=[root], tools=information_tool, skills=skills)
        factory = SkillObserverFactory(trusted, skills_root=root)
        if c.command is None and canonical in {'investigator', 'skill_assembler'}:
            def command(argv, cwd=None, limits=None):
                if hashlib.sha256(trusted.path.read_bytes()).hexdigest() != trusted.digest:
                    raise RuntimeError('observer runtime changed after validation')
                spec = importlib.util.spec_from_file_location('pc_as1_command_runtime', trusted.path)
                module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                def packetize(payload):
                    return c.store.create(tool='command', payload=payload, sources=[], access_scope=c.host.scope(None)).packet_id
                permitted = [root] if canonical == 'skill_assembler' else [*roots, root]
                runner = module.ReadOnlyCommandRunner(permitted, packetize=packetize)
                limits = limits or {}
                return runner.run(argv, cwd or str(permitted[0]), timeout_seconds=min(30, max(1, limits.get('timeout_seconds', 10))),
                                  max_output_bytes=min(65536, max(256, limits.get('max_output_bytes', 8192))))
            c.command = command
    except (OSError, ValueError, KeyError) as exc:
        c.worker_unavailable = type(exc).__name__
    c.jobs = JobService((state_directory or observer_analysis_state_root() / 'as1') / 'jobs',
                        packets=c.store, worker_factory=factory, backend=c.backend, inquiry_access=c.inquiry_access, can_execute=c.can_execute_inquiry,
                        inquiry_context_provider=inquiry_context)
    c.store.authority_access = c.packet_access
    c.information.job_lookup = lambda ident, scope: c.jobs.lookup(ident, access_scope=scope)
    c.skills = SkillService(c.jobs, skills_root=root)
    c.jobs.freshness_provider = InquiryFreshness(c)
    return c


def register_surface(mcp, c):
    detail_type = Literal['compact', 'standard', 'extended'] if mcp.profile == MCPProfile.OBSERVER else Literal['compact', 'standard']
    def register(fn, description, *, mutation=False, analysis=False):
        if 'detail' in fn.__annotations__:
            fn.__annotations__['detail'] = detail_type
        mcp.add_tool(fn, description=description, annotations=WRITE if mutation else ANALYSIS_READ if analysis else READ, structured_output=True)

    def overview(project: str | None = None, detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('overview', project=project, detail=detail)
    def delta(project: str, since: str | dict[str, Any], detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('delta', project=project, since=since, detail=detail)
    def frontier(project: str, scope: dict[str, Any] | None = None, detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('frontier', project=project, scope=scope, detail=detail)
    def search(query: str | ExactEntityQuery, project: str | None = None, scope: dict[str, Any] | None = None, detail: str = 'compact') -> dict[str, Any]:
        params = dict(scope or {}) if isinstance(query, ExactEntityQuery) else {"scope": scope}
        return c.information.call('search', project=project, query=query.model_dump() if isinstance(query, ExactEntityQuery) else query, detail=detail, **params)
    def evidence(project: str, subject: str, kinds: list[str] | None = None, detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('evidence', project=project, subject=subject, kinds=kinds or [], detail=detail)
    def impact(project: str, targets: list[ImpactTarget], change: Literal['body', 'interface', 'configuration', 'generator', 'removal', 'unknown'] = 'unknown', direction: Literal['dependents'] = 'dependents', view: Literal['paths', 'snippets'] = 'paths', detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('impact', project=project,
                                  targets=[target.model_dump(exclude_none=True) for target in targets],
                                  change_class=change, mode=view, detail=detail)
    def history(project: str, subject: str, from_revision: int | None = None, to_revision: int | None = None, detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('history', project=project, subject=subject, from_revision=from_revision, to_revision=to_revision, detail=detail)
    def machine(query_or_view: str = 'host_memory', detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('machine', query_or_view=query_or_view, detail=detail)
    def read(project: str, paths: list[str | dict[str, Any]], repository: str | None = None, revision: str | None = None, detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('read', project=project, paths=paths, repository=repository, revision=revision, detail=detail)
    def investigate(question: str, project: str | None = None, hints: list[str] | None = None, request_id: str | None = None, detail: str = 'compact') -> dict[str, Any]:
        scope = c.scope(project)
        value = c.jobs.inquire(question=question, access_scope=scope, hints=hints or (), request_id=request_id)
        if value.get('job') and value['status'] in {'completed', 'partial'}:
            c.jobs.reconcile()
            ref = value['job'].get('result_packet')
            result = c.store.lookup(ref, access_scope=scope) if ref else None
            if not result or result.status != 'ok':
                return {'status': 'unavailable', 'reason': 'answer_unavailable'}
            return public_inquiry({**result.packet.payload, 'status': value['status'],
                'packet_id': result.packet.packet_id, 'alias': result.packet.alias,
                'evidence_packets': value['job'].get('evidence_packets', []),
                'sources': [source.model_dump(exclude_none=True) for source in result.packet.sources]})
        return public_inquiry(value)
    def skill(query: str | None = None, skill: str | None = None, project: str | None = None, hints: list[str] | None = None, request_id: str | None = None, detail: str = 'compact') -> dict[str, Any]:
        value = c.skills.inquire(access_scope=c.scope(project), query=query, skill=skill,
            hints=hints or (), request_id=request_id, detail=detail)
        if value.get('status') not in {'ok', 'thinking', 'busy', 'unavailable', 'completed', 'partial'}:
            return {'status': 'unavailable', 'reason': 'answer_unavailable'}
        return public_inquiry(value)
    def command(argv: list[str], cwd: str | None = None, limits: dict[str, Any] | None = None) -> dict[str, Any]:
        if c.command is None:
            return {'status': 'unavailable', 'reason': 'qualified_command_port_unavailable'}
        return c.command(argv, cwd=cwd, limits=limits)
    def log(query: str = '', job_id: str | None = None, path_or_entity: str | None = None, limit: int = 50, project: str | None = None) -> dict[str, Any]:
        scope = c.scope(project)
        if job_id:
            return c.jobs.lookup(job_id, access_scope=scope)
        return {'records': c.jobs.log(access_scope=scope, query=query or path_or_entity or '', limit=min(50, max(1, limit)))}
    def plan(project: str, action: Literal['validate', 'diff', 'apply', 'amend', 'supersede', 'retire'], native_plan: dict[str, Any] | None = None, proposal: dict[str, Any] | None = None, replan: dict[str, Any] | None = None, intent: dict[str, Any] | None = None, prepared_request: dict[str, Any] | None = None, authorization_id: str | None = None) -> dict[str, Any]:
        return c.control.plan(project, action, native_plan=native_plan, proposal=proposal, replan=replan, intent=intent, prepared_request=prepared_request, authorization_id=authorization_id)
    def amend_project(request: ProjectAmendment) -> dict[str, Any]:
        return c.control.amend_project(request)
    def maintain_execution(request: MaintenanceRequest) -> dict[str, Any]:
        return c.control.maintain_execution(request)
    descriptions = {
        'overview': 'Read registered projects and repositories, or give orientation for the named project. Omit project to list the catalog; nothing is loaded automatically.',
        'delta': 'Summarize project changes since a prior commit or a DeltaSince cursor with a supported revision, task, checkpoint, interface, or time anchor. Returns scoped immutable packets with source locators.',
        'frontier': 'Show current open work and relevant project context; use scope to narrow the view. Returns scoped immutable packets and source locators.',
        'search': 'Discover context with a text query, or resolve an exact typed {kind,target}. Exact lookups never fall back to fuzzy search; scope can narrow discovery.',
        'evidence': 'Retrieve supported evidence for a task, source, run, or other subject in a project. Use kinds to filter evidence; results include source locators.',
        'impact': 'Trace declared dependents of target entities for a change. Choose paths for affected locations or snippets for bounded source excerpts; results are read-only context.',
        'history': 'Trace supported history for a project subject, optionally bounded by from_revision and to_revision. Returns scoped history with source locators.',
        'machine': 'Read host facts or diagnostics selected by query_or_view, such as memory or runtime status. This reports machine context, not repository changes.',
        'read': 'Read exact project repository paths or requested ranges, optionally from a named repository and revision. Returns content with immutable source identities; observer profile only.',
        'investigate': 'Ask a read-only question about project context using optional evidence packet hints. A cached answer for the same question and context is reused only while its sources remain current; while thinking, continue useful work and repeat the identical question later; avoid submitting variants. If busy, use search, read or evidence to contextualize or refine a later question. Completed findings retain evidence and source references; use read or evidence to inspect authoritative source.',
        'skill': 'Read-only discovery and use of an installed native skill for a question, using optional project context and evidence hints. A cached answer for the same question and context is reused only while its selected sources remain current; while thinking, continue useful work and repeat the identical question later; avoid submitting variants. If busy, use search, read or evidence to contextualize or refine a later question. Results identify the selected authoritative skill source.',
        'command': 'Run a bounded read-only command within the configured repository roots using argv, optional cwd, and limits. Host policy clamps execution limits; output is recorded as evidence. No delegation or mutation.',
        'log': 'Retrieve up to five question-and-answer briefs from the global latest-50 answered-inquiry cache, using query/path_or_entity for lexical matches. Optional project narrows results; job_id remains a compatibility exact-record read.',
        'plan': 'Validate or compare a native Todo plan, or apply, amend, supersede, or retire project work through the scoped transaction authority. Supply the action and its matching plan or proposal; authorized mutations require valid prepared authority.',
        'amend_project': 'Submit a typed semantic project amendment, such as a supported registration or evidence update. The request is checked against current project authority before it is previewed or committed.',
        'maintain_execution': 'Resume one clean stopped execution under a host-issued maintenance mandate. Supply a typed request containing the authorized action and mandate; the mandate limits which execution can change.',
    }
    for fn in (overview, delta, frontier, search, evidence, impact, history, machine, read, investigate, skill, command, log, plan, amend_project, maintain_execution):
        register(fn, descriptions[fn.__name__], mutation=fn.__name__ in {'plan', 'amend_project', 'maintain_execution'}, analysis=fn.__name__ in {'investigate', 'skill'})
