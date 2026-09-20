from __future__ import annotations

import json
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
from .observer_analysis import ObserverAnalysisRegistry


SERVER_INSTRUCTIONS = (
    "Use project-control to inspect live engineering projects through its read-only architectural, source, "
    "history, planning, and coordination observatory. For direct synthesis, use rich reads deliberately: start with "
    "architecture_context for broad questions, project_overview for status, project_delta for material change, and "
    "source_context for bounded source evidence. Use local_investigate for a finished bounded autonomous local-model "
    "investigation. Prefer compact results; request richer projections only when supporting evidence is needed. "
    "Todo semantic workflow owns operational truth; durable export only enriches anchored records. The read-only project "
    "query tools never claim tasks, mark messages read, advance cursors, edit files, run workers or benchmarks, reserve "
    "resources, or mutate Git/todo state. terminal_capture is the one bounded, sandboxed PTY observation capability; it "
    "has no shell or project mutation authority. Cross-project observations are independent, and program membership is "
    "not architectural authority. Proposal envelopes are inert and confer no authority. When preparing Todo/bootstrap "
    "work, compress architectural reasoning into durable intent, constraints, acceptance, and useful references rather "
    "than procedural microtasks."
)

CODEX_INSTRUCTIONS = (
    WORKFLOW_INSTRUCTIONS
    + " "
    + "Start workflow-first: use current task context and current workflow state before broad repository archaeology. "
    "Reserve root context and reasoning for execution, integration, and consequential decisions. Delegate bounded "
    "archaeology or research to cheaper subagents when appropriate; they can use rich Project Control reads for the "
    "specific question. The root may use rich reads directly when synthesis is genuinely useful. Request richer "
    "projections deliberately, rather than routinely. Use the workflow tools exposed by the current Project Control "
    "Codex profile for mutations. Roots and heads may publish non-obvious, durable reusable findings with "
    "coordinate_task(action='publish_context'); cheap subagents should return findings to their parent instead. "
    "Context notes are non-authoritative and never replace decisions, invariants, or interfaces."
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
    "Use Todo Orchestrator as the sole Todo transaction authority. Preview plans and read current context before "
    "broad mutation. Apply only fresh inert ProposalEnvelope values containing native Todo plans; stale proposals "
    "fail closed without rebasing, regeneration, or retry. The six-tool workflow protocol remains preferred for "
    "ordinary claimed implementation work. Plan mutation is for ledger, bootstrap, and control changes, not a "
    "replacement for task claims."
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
        self.todo_read_port_factory = todo_read_port_factory()
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
) -> ProfiledFastMCP:
    active_config = config or load_config()
    runtime = Runtime(active_config)
    observer_analysis_registry = ObserverAnalysisRegistry()
    server = active_config.server
    selected_profile = MCPProfile(profile)
    instructions = SERVER_INSTRUCTIONS
    if selected_profile is MCPProfile.CODEX:
        instructions = CODEX_INSTRUCTIONS
    elif selected_profile is MCPProfile.MUTATOR:
        instructions = CODEX_INSTRUCTIONS + " " + MUTATOR_INSTRUCTIONS

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
        description="Compact full-envelope current state: authority, active/ready/blocked work, freshness, coverage and an exact expansion cursor.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def project_overview(project: str, detail: OverviewDetail = "standard", max_items: Annotated[int, Field(ge=1, le=100)] = 20) -> dict[str, Any]:
        request = ProjectOverviewInput(project=project, detail=detail, max_items=max_items)
        return runtime.invoke("project_overview", project, lambda: project_overview_service(runtime.snapshot(project), detail=request.detail, max_items=request.max_items))

    @mcp.tool(
        description="Compact full-envelope material change since an explicit cursor; retains authority, coverage, blockers and a fresh exact cursor for expansion.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def project_delta(project: str, since: DeltaSince, detail: DeltaDetail = "standard", max_items: Annotated[int, Field(ge=1, le=200)] = 40) -> dict[str, Any]:
        request = ProjectDeltaInput(project=project, since=since, detail=detail, max_items=max_items)
        def operation() -> ToolEnvelope:
            snapshot = runtime.snapshot(project)
            registry = WorkspaceRegistry(active_config)
            adapters = {alias: GitReadAdapter(registry.repository(project, alias).root) for alias in snapshot.repositories}
            todo_adapter = runtime.todo_adapter(project)
            return project_delta_service(snapshot, request.since, adapters, detail=request.detail, max_items=request.max_items, todo_adapter=todo_adapter)
        return runtime.invoke("project_delta", project, operation)

    @mcp.tool(
        description="Compact full-envelope todo-authoritative frontier with active claims, blockers, safe parallelism and exact expansion cursor.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def project_frontier(project: str, max_ready: Annotated[int, Field(ge=1, le=100)] = 20, include_blocked: bool = True, include_parallel_groups: bool = True) -> dict[str, Any]:
        request = ProjectFrontierInput(project=project, max_ready=max_ready, include_blocked=include_blocked, include_parallel_groups=include_parallel_groups)
        return runtime.invoke("project_frontier", project, lambda: project_frontier_service(runtime.snapshot(project), max_ready=request.max_ready, include_blocked=request.include_blocked, include_parallel_groups=request.include_parallel_groups))

    @mcp.tool(
        description="Preferred high-level local read-only investigation. Project Control brokers bounded architecture, source, inspect and workflow reads to a tool-less local model; returns evidence-linked facts, inferences and uncertainty without claims, writes or paid-model fallback.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def local_investigate(project: str, questions: Annotated[list[Annotated[str, Field(min_length=1, max_length=12000)]], Field(min_length=1, max_length=2)], effort: Literal["quick", "standard", "deep"] = "standard", detail: Literal["standard", "trace"] = "standard", compute_profile: Literal["narrow", "wide"] = "wide", parallelism: Literal["layer", "tensor"] = "layer") -> dict[str, Any]:
        request = LocalInvestigateInput(project=project, questions=questions, effort=effort, detail=detail, compute_profile=compute_profile, parallelism=parallelism)
        def operation() -> ToolEnvelope:
            snapshot = runtime.snapshot(project)
            workspace = WorkspaceRegistry(active_config).workspace(project)
            alias = workspace.authority_repository or (sorted(snapshot.repositories)[0] if snapshot.repositories else None)
            root = WorkspaceRegistry(active_config).repository(project, alias).root if alias is not None else None
            def run_branch(branch_question: str, session_id: str | None = None) -> ToolEnvelope:
                branch_request = request.model_copy(update={
                    "questions": [branch_question], "compute_profile": (
                        "narrow" if len(request.questions) > 1 else request.compute_profile)})
                return local_investigate_service(
                    active_config, branch_request, snapshot=snapshot,
                    snapshot_getter=lambda: runtime.snapshot(project),
                    model_turn=lambda turn: ({"status": "unavailable", "reason": "project_has_no_repository"}
                        if root is None else observer_analysis_registry.investigate_turn(
                            root, {**turn, **({"session_id": session_id} if session_id else {})})),
                )
            branch_questions = list(request.questions)
            started = time.monotonic()
            concurrent = False
            if root is None:
                return envelope("local_investigate", snapshot, {"results": [], "status": "unavailable",
                    "reason": "project_has_no_repository"}, warnings=["project_has_no_repository"])
            if len(branch_questions) == 1:
                branches = [run_branch(branch_questions[0])]
                data: dict[str, Any] = {"results": [{"question": branch_questions[0], "result": {
                    "status": branches[0].status.value, "data": branches[0].data,
                    "warnings": branches[0].warnings}}]}
                if request.detail == "trace":
                    data["aggregate"] = {"branches": 1, "concurrent": False,
                        "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}
                return envelope("local_investigate", snapshot, data, warnings=branches[0].warnings)
            prepared = observer_analysis_registry.open_sessions(
                root, count=len(branch_questions), compute_profile="narrow",
                parallelism=request.parallelism)
            sessions = [str(item) for item in prepared.get("session_ids", [])]
            if prepared.get("status") == "available" and len(sessions) == len(branch_questions):
                concurrent = len(branch_questions) > 1
                try:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures = [pool.submit(run_branch, branch_question, session_id)
                                   for branch_question, session_id in zip(branch_questions, sessions, strict=True)]
                        branches = [future.result() for future in futures]
                finally:
                    for session_id in sessions:
                        observer_analysis_registry.close_session(root, session_id)
            else:
                branches = []
                for branch_question in branch_questions:
                    prepared = observer_analysis_registry.open_sessions(
                        root, count=1, compute_profile="narrow", parallelism=request.parallelism)
                    branch_sessions = [str(item) for item in prepared.get("session_ids", [])]
                    if prepared.get("status") != "available" or len(branch_sessions) != 1:
                        unavailable = envelope("local_investigate", snapshot, {"status": "unavailable",
                            "reason": str(prepared.get("reason", "resource_unavailable"))[:500]},
                            warnings=["resource_unavailable"])
                        branches.append(unavailable)
                        continue
                    try:
                        branches.append(run_branch(branch_question, branch_sessions[0]))
                    finally:
                        observer_analysis_registry.close_session(root, branch_sessions[0])
            data: dict[str, Any] = {"results": [
                {"question": branch_question, "result": {
                    "status": branch.status.value, "data": branch.data, "warnings": branch.warnings}}
                for branch_question, branch in zip(branch_questions, branches, strict=True)]}
            if request.detail == "trace":
                data["aggregate"] = {"branches": len(branch_questions), "concurrent": concurrent,
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}
            return envelope("local_investigate", snapshot, data,
                            warnings=[warning for branch in branches for warning in branch.warnings])
        return runtime.invoke("local_investigate", project, operation)

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
        def operation() -> ToolEnvelope:
            snapshot = runtime.snapshot(project)
            workspace = runtime.builder.registry.workspace(project)
            reader = None
            refresher = None
            if (
                request.mode != "context"
                and snapshot.todo_revision is not None
                and workspace.authority_repository is not None
            ):
                reader = runtime.todo_plan_reader(project)
                if reader is not None:
                    refresher = lambda: runtime.snapshot(project)
            return plan_preview_service(
                active_config, snapshot, request,
                todo_plan_reader=reader, snapshot_refresher=refresher,
            )
        return runtime.invoke("plan_preview", project, operation)

    @mcp.tool(
        description="Report observable todo agents, claims, child attempts, stale state, and existing local services without starting or inferring activity.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def agent_status(project: str, include_children: bool = True, include_local_services: bool = True) -> dict[str, Any]:
        request = AgentStatusInput(project=project, include_children=include_children, include_local_services=include_local_services)
        def operation() -> ToolEnvelope:
            snapshot = runtime.snapshot(project)
            observer_backend = observer_analysis_registry.status() if request.include_local_services else None
            return agent_status_service(snapshot, request, observer_backend=observer_backend)
        return runtime.invoke("agent_status", project, operation)

    @mcp.tool(
        description="Summarize existing CUDA campaigns, comparable measurements, regressions, contamination, worker slots, and optional host capacity without executing work.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def performance_status(project: str, campaign: Annotated[str | None, Field(max_length=256)] = None, detail: OverviewDetail = "standard", include_host_capacity: bool = True) -> dict[str, Any]:
        request = PerformanceStatusInput(project=project, campaign=campaign, detail=detail, include_host_capacity=include_host_capacity)
        return runtime.invoke("performance_status", project, lambda: performance_status_service(runtime.snapshot(project, host=request.include_host_capacity, campaign=request.campaign), request, active_config))

    @mcp.tool(
        description="Run one registered, already-built CUDA benchmark or profiler campaign through the host scheduler. Commands, datasets, typed parameter schema and GPU placement come only from the registered campaign; outputs stay app-private and the project worktree is verified unchanged. Set rebuild only for an explicit registered build.",
        annotations=PERFORMANCE_PROBE,
        structured_output=True,
    )
    def performance_probe(project: str, campaign: Annotated[str, Field(min_length=1, max_length=128)], mode: Literal["benchmark", "nsys", "ncu"] = "benchmark", parameters: dict[str, Any] | None = None, rebuild: bool = False) -> dict[str, Any]:
        request = PerformanceProbeInput(project=project, campaign=campaign, mode=mode, parameters=parameters or {}, rebuild=rebuild)
        return runtime.invoke(
            "performance_probe", project,
            lambda: performance_probe_service(
                active_config, request, snapshot=runtime.snapshot(project),
                snapshot_getter=lambda: runtime.snapshot(project),
                skills_root=getattr(runtime.todo_read_port_factory, "_project_control_bound_skills_root", None),
                allow_rebuild=selected_profile is not MCPProfile.OBSERVER,
            ),
        )

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
    def impact_preview(project: str, hypothesis: Annotated[str, Field(min_length=1, max_length=12000)], proposed_change: dict[str, Any] | None = None, target_entities: Annotated[list[str] | None, Field(max_length=64)] = None, detail: ImpactDetail = "standard", max_items: Annotated[int, Field(ge=1, le=1000)] = 100, include_proposal_envelope: bool = False) -> dict[str, Any]:
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
            compact_project, compact_cursor = snapshot.compact_identity()
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
            # Terminal screens can be large, while normal observer reads only
            # need a bounded result and refreshable observation identity.
            return bounded_envelope(ToolEnvelope(
                tool="terminal_capture",
                status=ToolStatus.OK,
                project=compact_project,
                data=bounded_payload(result.as_dict(), 8_000),
                warnings=[],
                cursor=compact_cursor,
            ), 8_192)

        return runtime.invoke("terminal_capture", project, operation)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "config": "parseable"})

    @mcp.custom_route("/readyz", methods=["GET"])
    async def ready(_: Request) -> JSONResponse:
        workspaces = sorted(active_config.workspaces)
        adapters_available = any(
            runtime.builder._todo_provider(item).compatible for item in workspaces
        )
        ok = bool(workspaces and adapters_available)
        return JSONResponse({"status": "ready" if ok else "unavailable", "workspaces": len(workspaces)}, status_code=200 if ok else 503)

    @mcp.custom_route("/version", methods=["GET"])
    async def version(_: Request) -> JSONResponse:
        return JSONResponse({"name": "project-control", "version": "0.3.2", "tool_schema_version": 9})

    if selected_profile in {MCPProfile.CODEX, MCPProfile.MUTATOR}:
        register_workflow_tools(mcp, protocol_factory=workflow_protocol)
    if selected_profile is MCPProfile.CODEX:
        register_maintenance_tool(mcp, host=maintenance_host)
    if selected_profile is MCPProfile.MUTATOR:
        register_mutation_tools(mcp, active_config)

    setattr(mcp, "_project_control_runtime", runtime)
    setattr(mcp, "_project_control_observer_analysis_registry", observer_analysis_registry)
    return mcp


def create_asgi_app(config: ProjectControlConfig | None = None):
    mcp = create_mcp(config)
    app = mcp.streamable_http_app()
    original_lifespan = app.router.lifespan_context
    runtime = getattr(mcp, "_project_control_runtime")
    observer_analysis_registry = getattr(mcp, "_project_control_observer_analysis_registry")

    @asynccontextmanager
    async def application_lifespan(asgi_app):
        async with original_lifespan(asgi_app) as state:
            try:
                yield state
            finally:
                observer_analysis_registry.close()
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


def _serve_stdio(profile: MCPProfile) -> int:
    """Run stdio MCP and release any cached observer model on EOF/error."""
    mcp = create_mcp(profile=profile)
    try:
        mcp.run(transport="stdio")
    finally:
        getattr(mcp, "_project_control_observer_analysis_registry").close()
    return 0


def serve_codex() -> int:
    """Run the explicitly selected Codex profile over stdio."""

    return _serve_stdio(MCPProfile.CODEX)
    return 0


def serve_mutator() -> int:
    """Run the explicitly selected mutation-capable profile over local stdio."""

    return _serve_stdio(MCPProfile.MUTATOR)
