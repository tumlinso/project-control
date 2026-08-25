from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from .adapters.git import GitReadAdapter
from .config import ProjectControlConfig, ServerConfig, load_config
from .models import (
    AgentStatusInput,
    DeltaSince,
    EvidenceInput,
    InspectInput,
    PerformanceStatusInput,
    PlanPreviewInput,
    ProjectDeltaInput,
    ProjectFrontierInput,
    ProjectIdentity,
    ProjectOverviewInput,
    ProjectSnapshot,
    RepositoryIdentity,
    ToolEnvelope,
    ToolStatus,
    utc_now,
)
from .registry import RegistryError, WorkspaceRegistry
from .services.agents import agent_status as agent_status_service
from .services.delta import project_delta as project_delta_service
from .services.evidence import evidence_for
from .services.frontier import project_frontier as project_frontier_service
from .services.inspect import inspect_subject
from .services.overview import project_overview as project_overview_service
from .services.performance import performance_status as performance_status_service
from .services.planning import MutationDetected, plan_preview as plan_preview_service
from .snapshot import SnapshotBuilder, resolve_skills_root


SERVER_INSTRUCTIONS = (
    "Use project-control to inspect live engineering projects and reason about architecture, planning, review, "
    "coordination, evidence, and performance. Start with project_overview or project_delta. Use project_frontier "
    "for safe next work, inspect/evidence for bounded detail, and plan_preview to validate or package prospective "
    "work for Codex. All tools are strictly read-only: they never claim tasks, edit files, run workers or "
    "benchmarks, reserve GPUs, or mutate Git/todo state. Bind recommendations to observed todo revisions and Git "
    "commits. Agent state is observable-only. Hand proposed mutations to Codex through the authoritative coding "
    "workflow and skills read interfaces. A plan preview is prospective and has never been applied."
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


class Runtime:
    def __init__(self, config: ProjectControlConfig):
        self.config = config
        self.builder = SnapshotBuilder(config)

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
            return operation().model_dump(mode="json")
        except (RegistryError, ValidationError, ValueError) as exc:
            return self.failure(tool, project, ToolStatus.INVALID_REQUEST, str(exc))
        except MutationDetected:
            return self.failure(tool, project, ToolStatus.INTERNAL_ERROR, "read_only_mutation_guard_failed")
        except Exception:
            return self.failure(tool, project, ToolStatus.INTERNAL_ERROR, "bounded_read_failed")


def create_mcp(config: ProjectControlConfig | None = None) -> FastMCP:
    active_config = config or load_config()
    runtime = Runtime(active_config)
    server = active_config.server
    mcp = FastMCP(
        "project-control",
        instructions=SERVER_INSTRUCTIONS,
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
    def project_overview(project: str, detail: str = "standard", max_items: int = 20) -> dict[str, Any]:
        request = ProjectOverviewInput(project=project, detail=detail, max_items=max_items)
        return runtime.invoke("project_overview", project, lambda: project_overview_service(runtime.snapshot(project), detail=request.detail, max_items=request.max_items))

    @mcp.tool(
        description="Classify material todo, interface, validation, coordination, performance, and Git changes since an explicit caller cursor; returns a new cursor.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def project_delta(project: str, since: DeltaSince, detail: str = "standard", max_items: int = 40) -> dict[str, Any]:
        request = ProjectDeltaInput(project=project, since=since, detail=detail, max_items=max_items)
        def operation() -> ToolEnvelope:
            snapshot = runtime.snapshot(project)
            registry = WorkspaceRegistry(active_config)
            adapters = {alias: GitReadAdapter(registry.repository(project, alias).root) for alias in snapshot.repositories}
            return project_delta_service(snapshot, request.since, adapters, detail=request.detail, max_items=request.max_items)
        return runtime.invoke("project_delta", project, operation)

    @mcp.tool(
        description="Report todo-authoritative ready work, active claims and blockers plus clearly labeled heuristic critical path and safe parallel groups.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def project_frontier(project: str, max_ready: int = 20, include_blocked: bool = True, include_parallel_groups: bool = True) -> dict[str, Any]:
        request = ProjectFrontierInput(project=project, max_ready=max_ready, include_blocked=include_blocked, include_parallel_groups=include_parallel_groups)
        return runtime.invoke("project_frontier", project, lambda: project_frontier_service(runtime.snapshot(project), max_ready=request.max_ready, include_blocked=request.include_blocked, include_parallel_groups=request.include_parallel_groups))

    @mcp.tool(
        description="Inspect one bounded registered task, contract, decision, dependency, symbol, path, or subsystem with source location and freshness.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def inspect(project: str, kind: str, target: str, repository: str | None = None, intent: str = "architecture", budget_tokens: int = 4000) -> dict[str, Any]:
        request = InspectInput(project=project, kind=kind, target=target, repository=repository, intent=intent, budget_tokens=budget_tokens)
        return runtime.invoke("inspect", project, lambda: inspect_subject(active_config, runtime.snapshot(project), request))

    @mcp.tool(
        description="Synthesize support, contradictions, caveats, confidence, and bounded provenance for a project subject without raw logs or transcripts.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def evidence(project: str, subject: str, kinds: list[str] | None = None, detail: str = "summary", max_items: int = 30) -> dict[str, Any]:
        request = EvidenceInput(project=project, subject=subject, kinds=kinds or [], detail=detail, max_items=max_items)
        return runtime.invoke("evidence", project, lambda: evidence_for(runtime.snapshot(project), request))

    @mcp.tool(
        description="Return planning context, non-mutating plan validation/diff, or a prospective Codex handoff; never applies a plan.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def plan_preview(project: str, mode: str, objective: str | None = None, proposal: dict[str, Any] | None = None, detail: str = "standard") -> dict[str, Any]:
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
    def performance_status(project: str, campaign: str | None = None, detail: str = "standard", include_host_capacity: bool = True) -> dict[str, Any]:
        request = PerformanceStatusInput(project=project, campaign=campaign, detail=detail, include_host_capacity=include_host_capacity)
        return runtime.invoke("performance_status", project, lambda: performance_status_service(runtime.snapshot(project, host=request.include_host_capacity, campaign=request.campaign), request))

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
        return JSONResponse({"name": "project-control", "version": "0.1.0", "tool_schema_version": 1})

    return mcp


def create_asgi_app(config: ProjectControlConfig | None = None):
    return create_mcp(config).streamable_http_app()


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
