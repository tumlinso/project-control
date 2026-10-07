from __future__ import annotations

import json
import asyncio
import contextvars
import time
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from .adapters.git import GitReadAdapter
from .adapters.todo import TodoReadAdapter
from .config import ProjectControlConfig, ServerConfig, load_config
from .models import (
    AgentStatusInput,
    ArchitectureContextInput,
    CoordinationViewInput,
    DeltaSince,
    EvidenceInput,
    InspectInput,
    LocalInvestigateInput,
    HistoryTraceInput,
    ImpactPreviewInput,
    PerformanceStatusInput,
    PerformanceProbeInput,
    PlanPreviewInput,
    ProjectDeltaInput,
    ProjectFrontierInput,
    ProjectIdentity,
    ProjectOverviewInput,
    ProjectSnapshot,
    ProgramContextInput,
    RepositoryIdentity,
    SourceContextInput,
    SourceTarget,
    TerminalCaptureInput,
    ToolEnvelope,
    ToolStatus,
    envelope,
    utc_now,
)
from .registry import RegistryError, WorkspaceRegistry
from .services.agents import agent_status as agent_status_service
from .services.architecture import architecture_context as architecture_context_service
from .services.coordination import coordination_view as coordination_view_service
from .services.delta import project_delta as project_delta_service
from .services.evidence import evidence_for
from .services.frontier import project_frontier as project_frontier_service
from .services.history import history_trace as history_trace_service
from .services.impact import impact_preview as impact_preview_service
from .services.inspect import inspect_subject
from .services.overview import project_overview as project_overview_service
from .services.performance import performance_status as performance_status_service
from .services.performance_probe import performance_probe as performance_probe_service
from .services.planning import MutationDetected, plan_preview as plan_preview_service
from .services.program import program_context as program_context_service
from .services.source_context import source_context as source_context_service
from .services.local_investigate import local_investigate as local_investigate_service
from .snapshot import SnapshotBuilder
from .security import redact_output
from .call_audit import caller_var
from .normalize import bounded_envelope, bounded_payload
from .terminal import TerminalSessionRegistry
from .profiles import MCPProfile, ProfiledFastMCP
from .mutation_tools import register_mutation_tools
from .workflow_binding import initialize_workflow_binding, todo_read_port_factory, workflow_protocol
from .workflow_tools import (
    WORKFLOW_INSTRUCTIONS,
    MaintenanceHostContext,
    register_maintenance_tool,
    register_workflow_tools,
)
from .observer_analysis import ObserverAnalysisRegistry, SkillsObserverAnalysisProvider
from .skills import SkillRegistry
from .config import configured_observer_skills_root
from .skill_context import SkillContext


SERVER_INSTRUCTIONS = (
    "Use overview for orientation when needed, search for discovery or exact typed entities, "
    "delta/frontier for current work, evidence/history/impact for supported traces, and machine for host facts. "
    "Information returns scoped immutable packets and relative source locators. No overview is automatic. "
    "Hints and packets confer no mutation authority."
)
CODEX_INSTRUCTIONS = SERVER_INSTRUCTIONS + " " + WORKFLOW_INSTRUCTIONS

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

TERMINAL_OBSERVATION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

# Registered probes may reserve accelerators and write evidence only below the
# app-private artifact root.  They are not repository or Todo mutations, but
# unlike read tools they are explicitly requested measurements.
PERFORMANCE_PROBE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

MUTATOR_INSTRUCTIONS = (
    "Use plan, amend_project and maintain_execution for explicit transactional control. "
    "The native Todo workflow engine is part of Project Control; rescue does not require "
    "the Todo Orchestrator skill or a separate source checkout. For a stopped task, call "
    "maintain_execution with {project, action:'diagnose', task_id}; inspect blockers, then "
    "call {project, action:'prepare', task_id, run_id?} for that same task (include run_id "
    "when needed to select its exact active run). Execute only the returned grant using "
    "{project, action:'execute', authorization_id}; never substitute a task, run, workspace, "
    "principal, or grant. For exact run replacement, use plan(action:'supersede') to prepare "
    "the reviewed intent, then execute its returned grant with maintain_execution. Startup "
    "project/principal access checks and native ownership, cleanliness, and affected-work "
    "guards still apply; blocked or corrupt state may require owner repair outside this route."
)

OverviewDetail = Literal["compact", "standard", "expanded"]
DeltaDetail = Literal["architectural", "standard", "implementation"]
InspectKind = Literal[
    "task", "interface", "checkpoint", "decision", "dependency", "symbol", "path", "subsystem",
    "run", "lane", "dispatch", "message", "rendezvous", "context_fragment", "workspace",
    "worktree", "patch", "integration", "gate", "invariant", "artifact", "commit", "test",
]
InspectIntent = Literal["architecture", "implementation", "debug", "review", "performance"]
EvidenceKind = Literal[
    "source", "tests", "gates", "worker", "cuda", "git", "architecture", "decision",
    "message", "context", "workspace", "integration",
]
EvidenceDetail = Literal["summary", "provenance", "bounded_excerpt"]
PlanMode = Literal["context", "validate", "handoff"]
PlanDetail = Literal["compact", "standard", "exact"]
ContextDetail = Literal["compact", "standard", "expanded"]
ImpactDetail = Literal["compact", "standard", "expanded", "exact"]
ArchitectureScope = Literal["current", "current_and_reference", "all"]
SourceKind = Literal["path", "symbol", "subsystem", "text"]
SourceSelectorIntent = Literal["architecture", "implementation", "debug", "review", "performance"]
SourceRelation = Literal[
    "definitions", "references", "callers", "callees", "tests", "build_config_references",
    "documentation", "recent_changes", "task_ownership", "interfaces", "performance_evidence", "context_notes",
]


class Runtime:
    def __init__(self, config: ProjectControlConfig):
        self.config = config
        self.workflow_binding = None
        self.workflow_binding_error = None
        try:
            self.workflow_binding = initialize_workflow_binding()
            self.workflow_binding.validate()
            self.todo_read_port_factory = todo_read_port_factory()
        except Exception as error:
            # Keep liveness and unrelated read surfaces available so readiness
            # can report the actual core binding failure. Workflow operations
            # still fail through their own verified binding path.
            self.todo_read_port_factory = None
            self.workflow_binding = None
            self.workflow_binding_error = error
        self.builder = SnapshotBuilder(
            config, todo_read_port_factory=self.todo_read_port_factory,
        )
        self.terminals = TerminalSessionRegistry(config)

    def todo_adapter(self, project: str) -> TodoReadAdapter | None:
        workspace = self.builder.registry.workspace(project)
        if not workspace.authority_repository:
            return None
        provider = self.builder._todo_provider(project)
        if not provider.compatible or not (provider.read_port or provider.todo_script):
            return None
        repository = self.builder.registry.repository(project, workspace.authority_repository)
        return TodoReadAdapter(
            repository.root,
            provider.todo_script,
            read_port=provider.read_port,
        )

    def snapshot(self, project: str, *, host: bool = False, campaign: str | None = None) -> ProjectSnapshot:
        return self.builder.build(project, include_host=host, campaign=campaign)

    def todo_plan_reader(self, project: str):
        """Use Todo's verified in-process plan service when this Runtime is bound.

        The fallback preview path remains for unbound compatibility setups. A
        bound Runtime must not select a second Todo script just to preview.
        """

        if self.todo_read_port_factory is None:
            return None
        workspace = self.builder.registry.workspace(project)
        if not workspace.authority_repository:
            return None
        root = self.builder.registry.repository(project, workspace.authority_repository).root
        binding = initialize_workflow_binding()
        binding.validate()
        from todo_orchestrator.service import Service

        service = Service(root, read_only=True)

        def read(plan_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
            binding.validate()
            validation = service.plan_validate(str(plan_path))
            diff = service.plan_diff(str(plan_path))
            binding.validate()

            def envelope_result(value: object) -> dict[str, Any]:
                # Todo's in-process Service returns its native payload, while
                # the compatibility CLI returns {ok, data}. Keep the existing
                # preview consumer on one envelope without changing what Todo
                # validates or diffs.
                if isinstance(value, dict) and isinstance(value.get("data"), dict) and "ok" in value:
                    return value
                return {"ok": True, "data": dict(value) if isinstance(value, dict) else {}}

            return envelope_result(validation), envelope_result(diff)

        return read

    def failure(self, tool: str, project: str, status: ToolStatus, warning: str) -> dict[str, Any]:
        observed = utc_now()
        snapshot = ProjectSnapshot(workspace_id=project, observed_at=observed, repositories={}, warnings=[warning])
        value = ToolEnvelope(
            tool=tool,
            status=status,
            project=snapshot.identity(),
            data={},
            warnings=[warning],
            cursor=snapshot.cursor(),
        )
        return value.model_dump(mode="json")

    def invoke(self, tool: str, project: str, operation: Callable[[], ToolEnvelope]) -> dict[str, Any]:
        try:
            return redact_output(operation().model_dump(mode="json"))
        except (RegistryError, ValidationError, ValueError) as exc:
            return self.failure(tool, project, ToolStatus.INVALID_REQUEST, str(exc))
        except MutationDetected:
            return self.failure(tool, project, ToolStatus.INTERNAL_ERROR, "read_only_mutation_guard_failed")
        except Exception:
            return self.failure(tool, project, ToolStatus.INTERNAL_ERROR, "bounded_read_failed")


def create_mcp(
    config: ProjectControlConfig | None = None,
    *,
    profile: MCPProfile | str = MCPProfile.OBSERVER,
    maintenance_host: MaintenanceHostContext | None = None,
    host=None,
    state_directory: Path | None = None,
    observer_backend=None,
    observer_runtime_sha256: str | None = None,
    coder_claim_provider=None,
    command_port=None,
) -> ProfiledFastMCP:
    from .as1_surface import compose_surface, register_surface

    active_config = config or load_config()
    selected_profile = MCPProfile(profile)
    instructions = SERVER_INSTRUCTIONS
    if selected_profile in {MCPProfile.CODER, MCPProfile.CODEX, MCPProfile.MUTATOR}:
        instructions += " " + WORKFLOW_INSTRUCTIONS
    if selected_profile == MCPProfile.OBSERVER:
        instructions += " Observer tools provide read-only context, investigation and installed skill guidance. Use read or evidence for authoritative selected source. While investigate or skill is thinking, continue useful work and repeat the identical question later; avoid submitting variants. If busy, use search, read or evidence to contextualize or refine a later question."
    if selected_profile == MCPProfile.MUTATOR:
        instructions += " " + MUTATOR_INSTRUCTIONS
    if maintenance_host is not None:
        from .as1_context import ContextHost
        if selected_profile != MCPProfile.MUTATOR or host is not None:
            raise ValueError('maintenance requires an explicit mutator startup principal')
        host = ContextHost('mutator', maintenance_host.recipient_principal, frozenset(active_config.workspaces))
    runtime = Runtime(active_config)
    composition = compose_surface(runtime, selected_profile, host=host,
        state_directory=state_directory, backend=observer_backend,
        observer_runtime_sha256=observer_runtime_sha256,
        coder_claim_provider=coder_claim_provider, command_port=command_port)
    server = active_config.server
    mcp = ProfiledFastMCP("project-control", profile=selected_profile, instructions=instructions,
        host=server.host, port=server.port, streamable_http_path="/mcp", stateless_http=True,
        json_response=True, max_request_body_size=384 * 1024)
    register_surface(mcp, composition)
    if selected_profile in {MCPProfile.CODER, MCPProfile.CODEX, MCPProfile.MUTATOR}:
        register_workflow_tools(mcp, protocol_factory=workflow_protocol, context_publisher=composition.publish_context)

    def central_health():
        status = getattr(composition.backend, "central_status", None)
        if not callable(status):
            return {"status": "unavailable", "reason": "central_supervisor_status_unavailable"}
        try:
            value = status()
            return {"status": "available", "observer_contract": value.get("observer_contract"),
                    "supervisor_pid": value.get("supervisor_pid"),
                    "supervisor_process_start": value.get("supervisor_process_start"),
                    "source_sha256": value.get("source_sha256")}
        except Exception as error:
            return {"status": "unavailable", "reason": SkillsObserverAnalysisProvider._failure(error)}

    def workflow_health():
        binding = runtime.workflow_binding
        error = runtime.workflow_binding_error
        if binding is None:
            reason = getattr(error, "code", None) or "workflow_binding_unavailable"
            return {"status": "unavailable", "configuration": "valid",
                    "workflow_engine": "unavailable", "reason": str(reason)}
        try:
            binding.validate()
            identity = binding.identity
            return {"status": "available", "configuration": "valid",
                    "workflow_engine": "available", "fingerprint": identity.fingerprint}
        except Exception as error:
            reason = getattr(error, "code", None) or "workflow_binding_unavailable"
            return {"status": "unavailable", "configuration": "valid",
                    "workflow_engine": "unavailable", "reason": str(reason)}

    def content_health():
        try:
            registrations = getattr(composition.skills, "skills", None)
            if not isinstance(registrations, dict):
                return {"status": "unavailable", "reason": "skill_registry_unavailable"}
            if not registrations:
                return {"status": "unavailable", "reason": "no_registered_skills",
                        "skill_count": 0}
            identity = composition.skills.catalog_identity()
            if not isinstance(identity, str) or not identity:
                raise ValueError("skill_catalog_identity_unavailable")
            return {"status": "available", "catalog_sha256": identity,
                    "skill_count": len(registrations)}
        except Exception as error:
            reason = getattr(error, "code", None) or "skill_catalog_unavailable"
            return {"status": "unavailable", "reason": str(reason)}

    @mcp.custom_route("/healthz", methods=["GET"])
    async def health(_: Request):
        central = await asyncio.to_thread(central_health)
        return JSONResponse({"status": "ok", "jobs": composition.jobs.health(), "central_inference": central})

    @mcp.custom_route("/readyz", methods=["GET"])
    async def ready(_: Request):
        core = await asyncio.to_thread(workflow_health)
        central = await asyncio.to_thread(central_health)
        content = await asyncio.to_thread(content_health)
        ok = core.get("status") == "available"
        return JSONResponse({"status": "ready" if ok else "unavailable", "core": core,
                             "central_inference": central, "optional_content": content},
                            status_code=200 if ok else 503)

    @mcp.custom_route("/version", methods=["GET"])
    async def version(_: Request):
        return JSONResponse({"name": "project-control", "version": "0.3.2", "tool_schema_version": 10,
                             "features": mcp.feature_metadata})

    setattr(mcp, "_project_control_runtime", runtime)
    setattr(mcp, "_project_control_surface", composition)
    return mcp


class AuditCallerMiddleware:
    """Capture the immediate HTTP peer without trusting forwarded headers."""

    def __init__(self, wrapped_app: Any) -> None:
        self.wrapped_app = wrapped_app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.wrapped_app(scope, receive, send)
            return
        peer = scope.get("client")
        caller: dict[str, Any] = {"transport": "http", "upstream_identity": "unknown"}
        if isinstance(peer, (tuple, list)) and len(peer) >= 2:
            caller.update({"peer_ip": str(peer[0]), "peer_port": peer[1]})
        token = caller_var.set(caller)
        try:
            await self.wrapped_app(scope, receive, send)
        finally:
            caller_var.reset(token)


def create_asgi_app(config: ProjectControlConfig | None = None):
    mcp = create_mcp(config)
    app = mcp.streamable_http_app()
    app.add_middleware(AuditCallerMiddleware)
    original_lifespan = app.router.lifespan_context
    runtime = getattr(mcp, "_project_control_runtime")
    composition = getattr(mcp, "_project_control_surface")

    @asynccontextmanager
    async def application_lifespan(asgi_app):
        async with original_lifespan(asgi_app) as state:
            composition.start()
            try:
                yield state
            finally:
                composition.close()
                runtime.terminals.shutdown()

    app.router.lifespan_context = application_lifespan
    return app


def serve(*, host: str | None = None, port: int | None = None) -> int:
    config = load_config()
    selected = ServerConfig(
        host=host or config.server.host,
        port=port or config.server.port,
        transport=config.server.transport,
    )
    config.server = selected
    import uvicorn

    # The audit must identify the socket peer, not an untrusted forwarded header.
    uvicorn.run(create_asgi_app(config), host=selected.host, port=selected.port,
                log_level="info", proxy_headers=False)
    return 0


def _serve_stdio(
    profile: MCPProfile,
    *,
    maintenance_host: MaintenanceHostContext | None = None,
) -> int:
    """Run stdio MCP and disconnect its observer client on EOF/error."""
    mcp = create_mcp(profile=profile, maintenance_host=maintenance_host)
    try:
        getattr(mcp, "_project_control_surface").start()
        mcp.run(transport="stdio")
    finally:
        getattr(mcp, "_project_control_surface").close()
    return 0


def serve_codex() -> int:
    """Run the explicitly selected Codex profile over stdio."""

    return _serve_stdio(MCPProfile.CODEX)


def serve_maintenance_operator(principal: str) -> int:
    """Run a mutator stdio server bound to one trusted host principal."""
    from .workflow_tools import trusted_maintenance_context

    return _serve_stdio(
        MCPProfile.MUTATOR,
        maintenance_host=trusted_maintenance_context(principal),
    )


def serve_mutator() -> int:
    """Run the explicitly selected mutation-capable profile over local stdio."""

    return _serve_stdio(MCPProfile.MUTATOR)
