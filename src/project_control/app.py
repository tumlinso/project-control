from __future__ import annotations

import json
from contextlib import asynccontextmanager
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
    HistoryTraceInput,
    ImpactPreviewInput,
    PerformanceStatusInput,
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
from .services.planning import MutationDetected, plan_preview as plan_preview_service
from .services.program import program_context as program_context_service
from .services.source_context import source_context as source_context_service
from .snapshot import SnapshotBuilder, resolve_skills_root
from .security import redact_output
from .terminal import TerminalSessionRegistry
from .profiles import MCPProfile, ProfiledFastMCP
from .workflow_binding import workflow_protocol
from .workflow_tools import WORKFLOW_INSTRUCTIONS, register_workflow_tools


SERVER_INSTRUCTIONS = (
    "Use project-control to inspect live engineering projects through its read-only architectural, source, "
    "history, planning, and coordination "
    "observatory. Start with architecture_context for broad questions, project_overview for status, or "
    "project_delta for change. Use source_context for bounded multi-target source reads and coordination_view for "
    "todo-authoritative workflow state. Todo semantic workflow owns operational truth; durable export only enriches "
    "anchored records. The fourteen project query tools never claim tasks, mark messages read, advance cursors, "
    "edit files, run workers or benchmarks, reserve resources, or mutate Git/todo state. terminal_capture is the one "
    "bounded, sandboxed PTY observation capability; it has no shell or project mutation authority. Cross-project observations "
    "are independent, and program membership is not architectural authority. Send any proposed mutation to Codex "
    "through coding-workflow; proposal envelopes are inert and confer no authority."
)

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
PlanDetail = Literal["compact", "standard"]
ContextDetail = Literal["compact", "standard", "expanded"]
ArchitectureScope = Literal["current", "current_and_reference", "all"]
SourceKind = Literal["path", "symbol", "subsystem", "text"]
SourceSelectorIntent = Literal["architecture", "implementation", "debug", "review", "performance"]
SourceRelation = Literal[
    "definitions", "references", "callers", "callees", "tests", "build_config_references",
    "documentation", "recent_changes", "task_ownership", "interfaces", "performance_evidence",
]


class Runtime:
    def __init__(self, config: ProjectControlConfig):
        self.config = config
        self.builder = SnapshotBuilder(config)
        self.terminals = TerminalSessionRegistry(config)

    def snapshot(self, project: str, *, host: bool = False, campaign: str | None = None) -> ProjectSnapshot:
        return self.builder.build(project, include_host=host, campaign=campaign)

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
) -> ProfiledFastMCP:
    active_config = config or load_config()
    runtime = Runtime(active_config)
    server = active_config.server
    selected_profile = MCPProfile(profile)
    instructions = SERVER_INSTRUCTIONS
    if selected_profile is MCPProfile.CODEX:
        instructions = WORKFLOW_INSTRUCTIONS + " " + SERVER_INSTRUCTIONS

    mcp = ProfiledFastMCP(
        "project-control",
        profile=selected_profile,
        instructions=instructions,
        host=server.host,
        port=server.port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        max_request_body_size=384 * 1024,
    )

    @mcp.tool(
        description="Synthesize current project identity, active/ready/blocked work, recent outcomes, architectural attention, and recommended focus.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def project_overview(project: str, detail: OverviewDetail = "standard", max_items: Annotated[int, Field(ge=1, le=100)] = 20) -> dict[str, Any]:
        request = ProjectOverviewInput(project=project, detail=detail, max_items=max_items)
        return runtime.invoke("project_overview", project, lambda: project_overview_service(runtime.snapshot(project), detail=request.detail, max_items=request.max_items))

    @mcp.tool(
        description="Classify material todo, interface, validation, coordination, performance, and Git changes since an explicit caller cursor; returns a new cursor.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def project_delta(project: str, since: DeltaSince, detail: DeltaDetail = "standard", max_items: Annotated[int, Field(ge=1, le=200)] = 40) -> dict[str, Any]:
        request = ProjectDeltaInput(project=project, since=since, detail=detail, max_items=max_items)
        def operation() -> ToolEnvelope:
            snapshot = runtime.snapshot(project)
            registry = WorkspaceRegistry(active_config)
            adapters = {alias: GitReadAdapter(registry.repository(project, alias).root) for alias in snapshot.repositories}
            workspace = registry.workspace(project)
            skills_root = resolve_skills_root(active_config, project)
            todo_adapter = None
            if workspace.authority_repository and skills_root:
                todo_adapter = TodoReadAdapter(
                    registry.repository(project, workspace.authority_repository).root,
                    skills_root / "todo-orchestrator" / "scripts" / "todo.py",
                )
            return project_delta_service(snapshot, request.since, adapters, detail=request.detail, max_items=request.max_items, todo_adapter=todo_adapter)
        return runtime.invoke("project_delta", project, operation)

    @mcp.tool(
        description="Report todo-authoritative ready work, active claims and blockers plus clearly labeled heuristic critical path and safe parallel groups.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def project_frontier(project: str, max_ready: Annotated[int, Field(ge=1, le=100)] = 20, include_blocked: bool = True, include_parallel_groups: bool = True) -> dict[str, Any]:
        request = ProjectFrontierInput(project=project, max_ready=max_ready, include_blocked=include_blocked, include_parallel_groups=include_parallel_groups)
        return runtime.invoke("project_frontier", project, lambda: project_frontier_service(runtime.snapshot(project), max_ready=request.max_ready, include_blocked=request.include_blocked, include_parallel_groups=request.include_parallel_groups))

    @mcp.tool(
        description="Inspect one bounded registered task, contract, decision, dependency, symbol, path, or subsystem with source location and freshness.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def inspect(project: str, kind: InspectKind, target: Annotated[str, Field(min_length=1, max_length=512)], repository: str | None = None, intent: InspectIntent = "architecture", budget_tokens: Annotated[int, Field(ge=256, le=32768)] = 4000, line_start: Annotated[int | None, Field(ge=1)] = None, line_end: Annotated[int | None, Field(ge=1)] = None, worktree_id: Annotated[str | None, Field(max_length=128)] = None, source_selector: Annotated[str, Field(max_length=128)] = "working_tree", continuation_cursor: Annotated[str | None, Field(max_length=2048)] = None) -> dict[str, Any]:
        request = InspectInput(project=project, kind=kind, target=target, repository=repository, intent=intent, budget_tokens=budget_tokens, line_start=line_start, line_end=line_end, worktree_id=worktree_id, source_selector=source_selector, continuation_cursor=continuation_cursor)
        return runtime.invoke("inspect", project, lambda: inspect_subject(active_config, runtime.snapshot(project), request))

    @mcp.tool(
        description="Synthesize support, contradictions, caveats, confidence, and bounded provenance for a project subject without raw logs or transcripts.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def evidence(project: str, subject: Annotated[str, Field(min_length=1, max_length=512)], kinds: list[EvidenceKind] | None = None, detail: EvidenceDetail = "summary", max_items: Annotated[int, Field(ge=1, le=100)] = 30) -> dict[str, Any]:
        request = EvidenceInput(project=project, subject=subject, kinds=kinds or [], detail=detail, max_items=max_items)
        return runtime.invoke("evidence", project, lambda: evidence_for(active_config, runtime.snapshot(project), request))

    @mcp.tool(
        description="Return planning context, non-mutating plan validation/diff, or a prospective Codex handoff; never applies a plan.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def plan_preview(project: str, mode: PlanMode, objective: Annotated[str | None, Field(max_length=4000)] = None, proposal: dict[str, Any] | None = None, detail: PlanDetail = "standard") -> dict[str, Any]:
        request = PlanPreviewInput(project=project, mode=mode, objective=objective, proposal=proposal, detail=detail)
        return runtime.invoke("plan_preview", project, lambda: plan_preview_service(active_config, runtime.snapshot(project), request))

    @mcp.tool(
        description="Report observable todo agents, claims, child attempts, stale state, and existing local services without starting or inferring activity.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def agent_status(project: str, include_children: bool = True, include_local_services: bool = True) -> dict[str, Any]:
        request = AgentStatusInput(project=project, include_children=include_children, include_local_services=include_local_services)
        return runtime.invoke("agent_status", project, lambda: agent_status_service(runtime.snapshot(project), request))

    @mcp.tool(
        description="Summarize existing CUDA campaigns, comparable measurements, regressions, contamination, worker slots, and optional host capacity without executing work.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def performance_status(project: str, campaign: Annotated[str | None, Field(max_length=256)] = None, detail: OverviewDetail = "standard", include_host_capacity: bool = True) -> dict[str, Any]:
        request = PerformanceStatusInput(project=project, campaign=campaign, detail=detail, include_host_capacity=include_host_capacity)
        return runtime.invoke("performance_status", project, lambda: performance_status_service(runtime.snapshot(project, host=request.include_host_capacity, campaign=request.campaign), request, active_config))

    @mcp.tool(
        description="Orient a broad architectural or planning question with multi-seed retrieval, authority labels, active context, risks, and observation preconditions.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def architecture_context(project: str, question: Annotated[str, Field(min_length=1, max_length=12000)], repository: str | None = None, worktree_id: Annotated[str | None, Field(max_length=128)] = None, detail: ContextDetail = "standard", scope: ArchitectureScope = "current_and_reference", inclusion_categories: Annotated[list[str] | None, Field(max_length=32)] = None, max_items: Annotated[int, Field(ge=1, le=500)] = 60, continuation_cursor: Annotated[str | None, Field(max_length=4096)] = None) -> dict[str, Any]:
        request = ArchitectureContextInput(project=project, question=question, repository=repository, worktree_id=worktree_id, detail=detail, scope=scope, inclusion_categories=inclusion_categories or [], max_items=max_items, continuation_cursor=continuation_cursor)
        return runtime.invoke("architecture_context", project, lambda: architecture_context_service(runtime.snapshot(project), request))

    @mcp.tool(
        description="Observe todo-authoritative runs, lanes, dispatches and children separately, enriched read-only with messages, fragments, rendezvous and integration state.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def coordination_view(project: str, run_id: Annotated[str | None, Field(max_length=256)] = None, lane_id: Annotated[str | None, Field(max_length=256)] = None, task_id: Annotated[str | None, Field(max_length=256)] = None, since_revision: Annotated[int | None, Field(ge=0)] = None, detail: ContextDetail = "standard", include_resolved_messages: bool = False, include_historical_arrivals: bool = False, max_items: Annotated[int, Field(ge=1, le=1000)] = 100, continuation_cursor: Annotated[str | None, Field(max_length=4096)] = None) -> dict[str, Any]:
        request = CoordinationViewInput(project=project, run_id=run_id, lane_id=lane_id, task_id=task_id, since_revision=since_revision, detail=detail, include_resolved_messages=include_resolved_messages, include_historical_arrivals=include_historical_arrivals, max_items=max_items, continuation_cursor=continuation_cursor)
        return runtime.invoke("coordination_view", project, lambda: coordination_view_service(runtime.snapshot(project), request))

    @mcp.tool(
        description="Read one to thirty-two registered source targets with worktree, line-range, relation, race, provenance and continuation controls.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def source_context(project: str, repository: str, targets: Annotated[list[SourceTarget], Field(min_length=1, max_length=32)], worktree_id: Annotated[str | None, Field(max_length=128)] = None, source_selector: Annotated[str, Field(min_length=1, max_length=128)] = "working_tree", intent: SourceSelectorIntent = "implementation", requested_relations: Annotated[list[SourceRelation] | None, Field(max_length=16)] = None, detail: ContextDetail = "standard", budget_bytes: Annotated[int, Field(ge=1024, le=128 * 1024)] = 48 * 1024, continuation_cursor: Annotated[str | None, Field(max_length=4096)] = None) -> dict[str, Any]:
        request = SourceContextInput(project=project, repository=repository, targets=targets, worktree_id=worktree_id, source_selector=source_selector, intent=intent, requested_relations=requested_relations or [], detail=detail, budget_bytes=budget_bytes, continuation_cursor=continuation_cursor)
        return runtime.invoke("source_context", project, lambda: source_context_service(active_config, runtime.snapshot(project), request))

    @mcp.tool(
        description="Trace how a task, interface, architecture, path or subsystem reached its current state without exposing logs or inventing causality.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def history_trace(project: str, subject: Annotated[str, Field(min_length=1, max_length=1024)], from_revision: Annotated[int | None, Field(ge=0)] = None, from_time: Annotated[str | None, Field(max_length=64)] = None, from_task: Annotated[str | None, Field(max_length=256)] = None, from_checkpoint: Annotated[str | None, Field(max_length=256)] = None, from_interface: Annotated[str | None, Field(max_length=256)] = None, from_commit: Annotated[str | None, Field(max_length=128)] = None, to_revision: Annotated[int | None, Field(ge=0)] = None, to_commit: Annotated[str | None, Field(max_length=128)] = None, detail: ContextDetail = "standard", max_events: Annotated[int, Field(ge=1, le=1000)] = 100, continuation_cursor: Annotated[str | None, Field(max_length=4096)] = None) -> dict[str, Any]:
        request = HistoryTraceInput(project=project, subject=subject, from_revision=from_revision, from_time=from_time, from_task=from_task, from_checkpoint=from_checkpoint, from_interface=from_interface, from_commit=from_commit, to_revision=to_revision, to_commit=to_commit, detail=detail, max_events=max_events, continuation_cursor=continuation_cursor)
        return runtime.invoke("history_trace", project, lambda: history_trace_service(runtime.snapshot(project), request))

    @mcp.tool(
        description="Preview proven, possible and unknown consequences of an architectural hypothesis and optionally return an inert proposal envelope.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def impact_preview(project: str, hypothesis: Annotated[str, Field(min_length=1, max_length=12000)], proposed_change: dict[str, Any] | None = None, target_entities: Annotated[list[str] | None, Field(max_length=64)] = None, detail: ContextDetail = "standard", max_items: Annotated[int, Field(ge=1, le=1000)] = 100, include_proposal_envelope: bool = True) -> dict[str, Any]:
        request = ImpactPreviewInput(project=project, hypothesis=hypothesis, proposed_change=proposed_change, target_entities=target_entities or [], detail=detail, max_items=max_items, include_proposal_envelope=include_proposal_envelope)
        return runtime.invoke("impact_preview", project, lambda: impact_preview_service(runtime.snapshot(project), request))

    @mcp.tool(
        description="Synthesize independently observed context across a configured query-only program or explicit bounded registered workspace list.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def program_context(question: Annotated[str, Field(min_length=1, max_length=12000)], program_id: Annotated[str | None, Field(max_length=128)] = None, workspaces: Annotated[list[str] | None, Field(max_length=16)] = None, detail: ContextDetail = "standard", max_items: Annotated[int, Field(ge=1, le=1000)] = 100, continuation_cursor: Annotated[str | None, Field(max_length=4096)] = None) -> dict[str, Any]:
        project = program_id or "program"
        try:
            request = ProgramContextInput(program_id=program_id, workspaces=workspaces or [], question=question, detail=detail, max_items=max_items, continuation_cursor=continuation_cursor)
            return program_context_service(active_config, request)
        except (RegistryError, ValidationError, ValueError) as exc:
            return runtime.failure("program_context", project, ToolStatus.INVALID_REQUEST, str(exc))
        except Exception:
            return runtime.failure("program_context", project, ToolStatus.INTERNAL_ERROR, "bounded_read_failed")

    @mcp.tool(
        description="Launch one registered repository executable in a bounded sandboxed PTY, render its visible screen, or recapture the same live bonded terminal session.",
        annotations=TERMINAL_OBSERVATION,
        structured_output=True,
    )
    def terminal_capture(
        project: str,
        executable: Annotated[str | None, Field(min_length=1, max_length=512)] = None,
        session: Annotated[str | None, Field(min_length=1, max_length=128)] = None,
        repository: Annotated[str | None, Field(max_length=64)] = None,
        argv: Annotated[list[Annotated[str, Field(max_length=1024)]], Field(max_length=64)] = [],
        cwd: Annotated[str, Field(min_length=1, max_length=512)] = ".",
        label: Annotated[str | None, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")] = None,
        wait_ms: Annotated[int, Field(ge=0, le=30_000)] = 250,
        rows: Annotated[int | None, Field(ge=5, le=200)] = None,
        cols: Annotated[int | None, Field(ge=20, le=400)] = None,
        kill_after_capture: bool = True,
    ) -> dict[str, Any]:
        request = TerminalCaptureInput(
            project=project,
            executable=executable,
            session=session,
            repository=repository,
            argv=argv,
            cwd=cwd,
            label=label,
            wait_ms=wait_ms,
            rows=rows,
            cols=cols,
            kill_after_capture=kill_after_capture,
        )

        def operation() -> ToolEnvelope:
            snapshot = runtime.snapshot(project)
            if request.executable is not None:
                result = runtime.terminals.launch(
                    workspace_id=project,
                    repository=request.repository,
                    executable=request.executable,
                    argv=request.argv,
                    cwd=request.cwd,
                    label=request.label,
                    wait_ms=request.wait_ms,
                    rows=request.rows,
                    cols=request.cols,
                    kill_after_capture=request.kill_after_capture,
                )
            else:
                result = runtime.terminals.recapture(
                    workspace_id=project,
                    session_identity=request.session or "",
                    wait_ms=request.wait_ms,
                    rows=request.rows,
                    cols=request.cols,
                    kill_after_capture=request.kill_after_capture,
                )
            return ToolEnvelope(
                tool="terminal_capture",
                status=ToolStatus.OK,
                project=snapshot.identity(),
                data=result.as_dict(),
                warnings=[],
                cursor=snapshot.cursor(),
            )

        return runtime.invoke("terminal_capture", project, operation)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "config": "parseable"})

    @mcp.custom_route("/readyz", methods=["GET"])
    async def ready(_: Request) -> JSONResponse:
        workspaces = sorted(active_config.workspaces)
        adapters_available = any(resolve_skills_root(active_config, item) is not None for item in workspaces)
        ok = bool(workspaces and adapters_available)
        return JSONResponse({"status": "ready" if ok else "unavailable", "workspaces": len(workspaces)}, status_code=200 if ok else 503)

    @mcp.custom_route("/version", methods=["GET"])
    async def version(_: Request) -> JSONResponse:
        return JSONResponse({"name": "project-control", "version": "0.3.1", "tool_schema_version": 3})

    if selected_profile is MCPProfile.CODEX:
        register_workflow_tools(mcp, protocol_factory=workflow_protocol)

    setattr(mcp, "_project_control_runtime", runtime)
    return mcp


def create_asgi_app(config: ProjectControlConfig | None = None):
    mcp = create_mcp(config)
    app = mcp.streamable_http_app()
    original_lifespan = app.router.lifespan_context
    runtime = getattr(mcp, "_project_control_runtime")

    @asynccontextmanager
    async def application_lifespan(asgi_app):
        async with original_lifespan(asgi_app) as state:
            try:
                yield state
            finally:
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

    uvicorn.run(create_asgi_app(config), host=selected.host, port=selected.port, log_level="info")
    return 0


def serve_codex() -> int:
    """Run the explicitly selected Codex profile over stdio."""

    create_mcp(profile=MCPProfile.CODEX).run(transport="stdio")
    return 0
