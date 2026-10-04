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
from .as1_skill import SkillService, SkillObserverFactory
from .as1_trace import TraceService
from .config import configured_observer_skills_root
from .observer_analysis import SkillsObserverAnalysisProvider, observer_analysis_state_root
from .profiles import MCPProfile

# Qualified Skills producer receipt cd149c328 / 9ffcf14; never derived from encountered bytes.
QUALIFIED_OBSERVER_RUNTIME_SHA256 = '99ebb05632b7768403e103bc32bb5f04885c86662272cef14bf83fb25e57ce8b'
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


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
        return self.host.scope(project)


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
    c.worker_unavailable = None
    c.command = command_port
    factory = None
    try:
        catalog = json.loads((root / 'integrations/native-skill-catalog.json').read_text())
        skills = {entry['name']: {'name': entry['name'], 'root': str(root / entry['name'])}
                  for entry in catalog['entries'] if entry.get('status') == 'accessible'}
        def information_tool(name, arguments, scope):
            # Jobs carry a trusted admission scope; requests cannot override it.
            if scope.get('principal') != c.host.principal or scope.get('project') not in {*c.host.projects, 'catalog'}:
                raise PermissionError('job_scope_not_permitted')
            args = dict(arguments)
            project = args.pop('project', None)
            if scope['project'] != 'catalog' and project not in {None, scope['project']}:
                raise PermissionError('job_project_not_permitted')
            project = project or (None if scope['project'] == 'catalog' else scope['project'])
            worker_host = ContextHost('skill_assembler' if scope.get('profile') == 'skill_assembler' else 'investigator',
                                      c.host.principal, c.host.projects)
            info = InformationService(config, c.store, runtime.snapshot, worker_host,
                semantic_provider=c.control.project_context, impact_provider=c.trace,
                todo_adapter=runtime.todo_adapter, job_lookup=lambda ident, access: c.jobs.lookup(ident, access_scope=access))
            return info.call(name, project=project, **args)
        class ScopedTrustedObserverFactory(TrustedObserverFactory):
            def __call__(self, service, job):
                scope = job.scope
                if scope.get('principal') != c.host.principal or scope.get('project') not in {*c.host.projects, 'catalog'}:
                    raise PermissionError('job_scope_not_permitted')
                # Command roots follow the durable admission domain. Catalog and
                # skill jobs never acquire implicit project filesystem access.
                permitted = [root]
                if job.mode != 'skill' and scope['project'] != 'catalog':
                    permitted += [r.root for r in config.workspaces[scope['project']].repositories.values()]
                bound = TrustedObserverFactory(root, self.digest, backend=self.backend,
                    roots=permitted, tools=self.tools, skills=self.skills)
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
                        packets=c.store, worker_factory=factory, backend=c.backend)
    c.information.job_lookup = lambda ident, scope: c.jobs.lookup(ident, access_scope=scope)
    c.skills = SkillService(c.jobs, skills_root=root)
    return c


def register_surface(mcp, c):
    detail_type = Literal['compact', 'standard', 'extended'] if mcp.profile == MCPProfile.OBSERVER else Literal['compact', 'standard']
    def register(fn, description, *, mutation=False):
        if 'detail' in fn.__annotations__:
            fn.__annotations__['detail'] = detail_type
        mcp.add_tool(fn, description=description, annotations=WRITE if mutation else READ, structured_output=True)

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
    def investigate(question: str | None = None, project: str | None = None, hints: list[str] | None = None, request_id: str | None = None, job_id: str | None = None, detail: str = 'compact') -> dict[str, Any]:
        scope = c.scope(project)
        if job_id:
            return c.jobs.lookup(job_id, access_scope=scope)
        if not question:
            raise ValueError('question_required')
        return c.jobs.submit(question=question, access_scope=scope, hints=hints or (), request_id=request_id)
    def skill(query: str | None = None, skill: str | None = None, project: str | None = None, hints: list[str] | None = None, request_id: str | None = None, job_id: str | None = None, detail: str = 'compact') -> dict[str, Any]:
        return c.skills.submit(access_scope=c.scope(project), query=query, skill=skill, hints=hints or (), request_id=request_id, job_id=job_id, detail=detail)
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
        'investigate': 'Submit or poll durable read-only scout jobs. Do not wait; continue useful work and poll the returned ID later.',
        'skill': 'Installed native skill routing, direct resource authority and durable jobs; observer only.',
        'command': 'Internal read-only sandbox command; host clamps limits. No delegation or mutation.',
        'log': 'Internal scoped job findings; prior findings remain attributed evidence.',
    }
    for fn in (overview, delta, frontier, search, evidence, impact, history, machine, read, investigate, skill, command, log, plan, amend_project, maintain_execution):
        register(fn, descriptions.get(fn.__name__, 'Canonical scoped ' + fn.__name__ + ' service.'), mutation=fn.__name__ in {'plan', 'amend_project', 'maintain_execution', 'investigate', 'skill'})
