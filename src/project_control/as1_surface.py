"""Startup composition and MCP adapters for the qualified AS1 producer ports."""
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Literal

from mcp.types import ToolAnnotations
from .as1_context import ContextHost, InformationService
from .as1_contracts import ExactEntityQuery
from .as1_control import ControlService, ProjectAmendment, MaintenanceRequest
from .as1_jobs import JobService, TrustedObserverFactory
from .as1_packets import SQLitePacketStore
from .as1_skill import SkillService, SkillObserverFactory, _verified_reads, _entry_precedes_resource
from .as1_trace import TraceService
from .config import configured_observer_skills_root
from .observer_analysis import SkillsObserverAnalysisProvider, observer_analysis_state_root
from .profiles import MCPProfile

# Qualified inquiry-cache producer receipt; supplied by root after CPU acceptance.
QUALIFIED_OBSERVER_RUNTIME_SHA256 = '5e632aa35ec592a77eef5386ddcc629317befe774e9e54959ab20a90bfd6caea'
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
ANALYSIS_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


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
        refs = list(dict.fromkeys(job.get('hints', []) + job.get('evidence_packets', [])))
        for ref in refs:
            visit(ref)
        if job.get('result_packet'):
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
            'installed_skill_roots': list(installed_skill_roots)}
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
            if scope['project'] != 'catalog' and project not in {None, scope['project']}:
                raise PermissionError('job_project_not_permitted')
            project = project or (None if scope['project'] == 'catalog' else scope['project'])
            class AdmissionHost(ContextHost):
                def scope(self, project):
                    return {k: v for k, v in scope.items() if not k.startswith('_')}
            worker_host = AdmissionHost(scope['profile'], scope['principal'],
                                        frozenset(scope.get('catalog_projects', c.host.projects)))
            info = InformationService(config, c.store, runtime.snapshot, worker_host,
                semantic_provider=c.control.project_context, impact_provider=c.trace,
                todo_adapter=runtime.todo_adapter, job_lookup=lambda ident, access: c.jobs.lookup(ident, access_scope=access))
            return info.call(name, project=project, **args)
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
    def impact(project: str, targets: list[dict[str, Any]], change: str = 'unknown', direction: Literal['dependents'] = 'dependents', view: Literal['paths', 'snippets'] = 'paths', detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('impact', project=project, targets=targets, change_class=change, mode=view, detail=detail)
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
        'overview': 'Orient on demand; without project return the registered catalog.',
        'search': 'Discover context or directly resolve an exact typed {kind,target}; no fuzzy fallback for exact IDs.',
        'read': 'Read exact relative files or ranges with immutable source identities; observer only.',
        'investigate': 'Read-only investigation of project context and evidence. Use read or evidence for authoritative selected source. While thinking, continue useful work and repeat the identical question later; avoid submitting variants. If busy, use search, read or evidence to contextualize or refine a later question.',
        'skill': 'Read-only discovery and use of installed native skills and their authoritative selected source. While thinking, continue useful work and repeat the identical question later; avoid submitting variants. If busy, use search, read or evidence to contextualize or refine a later question.',
        'command': 'Internal read-only sandbox command; host clamps limits. No delegation or mutation.',
        'log': 'Internal scoped job findings; prior findings remain attributed evidence.',
    }
    for fn in (overview, delta, frontier, search, evidence, impact, history, machine, read, investigate, skill, command, log, plan, amend_project, maintain_execution):
        register(fn, descriptions.get(fn.__name__, 'Canonical scoped ' + fn.__name__ + ' service.'), mutation=fn.__name__ in {'plan', 'amend_project', 'maintain_execution'}, analysis=fn.__name__ in {'investigate', 'skill'})
