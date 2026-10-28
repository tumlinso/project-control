"""Deterministic MCP composition for source, evidence and workflow access."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Literal

from mcp.types import ToolAnnotations

from .as1_context import ContextHost, InformationService
from .as1_contracts import ExactEntityQuery, RetrievalTarget
from .as1_control import ControlService
from .as1_packets import SQLitePacketStore
from .as1_trace import TraceService
from .config import ProjectControlConfig
from .models import DeltaSince
from .native_workflow import NativeWorkflowHost
from .profiles import MCPProfile

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
    idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
    idempotentHint=False, openWorldHint=False)


class SurfaceComposition:
    def __init__(self, runtime, profile, host, state_root):
        self.runtime = runtime
        self.host = host
        self.state_root = state_root
        self.store = SQLitePacketStore(state_root / "packets")
        canonical = "coder" if profile == MCPProfile.CODEX else profile.value
        self.native_workflow = NativeWorkflowHost(runtime.config, host,
            providers_for_project=_workflow_providers_for_project) if canonical in {"coder", "mutator"} else None
        self.workflow = None
        if self.native_workflow is not None:
            from .compact_workflow import CompactWorkflowHost
            self.workflow = CompactWorkflowHost(self.native_workflow,
                state_directory=state_root / "workflow-handles")
        self.control = ControlService(runtime.config, host) if canonical == "mutator" else None
        if self.control is not None:
            self.control.workflow = self.native_workflow
        self.trace = TraceService(runtime.config, runtime.snapshot, host,
            semantic_provider=self.control.project_context if self.control else None)
        self.information = InformationService(runtime.config, self.store, runtime.snapshot, host,
            semantic_provider=self.control.project_context if self.control else None,
            impact_provider=self.trace, todo_adapter=runtime.todo_adapter)

    def scope(self, project):
        if project is not None and project not in self.host.projects:
            raise PermissionError("project_not_permitted")
        return self.host.scope(project)

    def start(self):
        return None

    def close(self):
        return True


def _workflow_providers_for_project(config, project, project_root, *, principal, profile,
                                    repository=None, principal_authenticator=None):
    """Load the trusted deterministic Todo provider composition on first workflow use."""
    from .native_providers import providers_for_project
    return providers_for_project(config, project, project_root, principal=principal,
        profile=profile, repository=repository,
        principal_authenticator=principal_authenticator)


def compose_surface(runtime, profile, *, host=None, state_directory=None, **_ignored):
    config = runtime.config
    canonical = "coder" if profile == MCPProfile.CODEX else profile.value
    active_host = host or ContextHost(canonical, "project-control-" + canonical,
        frozenset(config.workspaces))
    if active_host.profile != canonical or not active_host.projects <= config.workspaces.keys():
        raise ValueError("startup_host_profile_or_scope_mismatch")
    if state_directory is None:
        default_state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "project-control/as1"
        state_directory = Path(os.environ.get("PROJECT_CONTROL_AS1_STATE_DIR", default_state))
    return SurfaceComposition(runtime, profile, active_host, Path(state_directory))


def register_surface(mcp, composition: SurfaceComposition):
    info = composition.information

    def overview(project: str | None = None) -> dict[str, Any]:
        return info.call("overview", project=project)

    def frontier(project: str, scope: dict[str, Any] | None = None) -> dict[str, Any]:
        return info.call("frontier", project=project, scope=scope)

    def delta(project: str, mode: Literal["changes", "history"] = "changes",
              since: str | DeltaSince | None = None, subject: str | None = None,
              from_revision: int | None = None, to_revision: int | None = None,
              from_commit: str | None = None, to_commit: str | None = None,
              from_time: str | None = None, max_events: int = 100,
              cursor: str | None = None) -> dict[str, Any]:
        """Retrieve a change delta or a subject-bounded historical trace."""
        value = since.model_dump(mode="json", exclude_none=True) if isinstance(since, DeltaSince) else since
        return info.call("delta", project=project, mode=mode, since=value,
            subject=subject, from_revision=from_revision, to_revision=to_revision,
            from_commit=from_commit, to_commit=to_commit, from_time=from_time,
            max_events=max(1, min(1000, max_events)), cursor=cursor)

    def search(query: str | ExactEntityQuery, project: str | None = None,
               repository: str | None = None, limit: int = 30, cursor: str | None = None) -> dict[str, Any]:
        value = query.model_dump(mode="json") if isinstance(query, ExactEntityQuery) else query
        return info.call("search", project=project, query=value, repository=repository,
            limit=max(1, min(500, limit)), cursor=cursor)

    def make_context(default_budget: int, default_sections: list[str]):
        def context(project: str, targets: list[str | RetrievalTarget],
                include: list[Literal["implementation", "callers", "references", "tests", "guidance", "changes", "evidence"]] | None = None,
                budget_bytes: int = default_budget, cursor: str | None = None) -> dict[str, Any]:
            normalized = [target.model_dump(exclude_none=True) if isinstance(target, RetrievalTarget) else target
                          for target in targets]
            return info.call("context", project=project, targets=normalized,
                include=include if include is not None else default_sections,
                budget_bytes=max(1, min(1_000_000, budget_bytes)), cursor=cursor)
        context.__name__ = "context"
        return context

    context = make_context(48_000,
        ["implementation", "callers", "references", "tests", "guidance", "changes"])
    local_context = make_context(6_000, ["callers", "tests", "guidance", "changes"])

    def trace(project: str, targets: list[str | RetrievalTarget], predicates: list[str] | None = None,
              direction: Literal["outgoing", "incoming", "both"] = "both",
              max_hops: int = 2, limit: int = 100) -> dict[str, Any]:
        normalized = [target.model_dump(exclude_none=True) if isinstance(target, RetrievalTarget) else target
                      for target in targets]
        return info.call("trace", project=project, targets=normalized, predicates=predicates or [],
            direction=direction, max_hops=max(0, min(8, max_hops)), limit=max(1, min(1000, limit)))

    def evidence(project: str, subject: str, kinds: list[str] | None = None,
                 limit: int = 50) -> dict[str, Any]:
        return info.call("evidence", project=project, subject=subject,
            kinds=kinds or [], max_items=max(1, min(500, limit)))

    def machine(view: Literal["host", "toolchain", "diagnostics"] = "host") -> dict[str, Any]:
        return info.call("machine", view=view)

    if mcp.profile == MCPProfile.OBSERVER:
        async def read(project: str, paths: list[str | dict[str, Any]], repository: str | None = None,
                       revision: str | None = None, budget_bytes: int = 65536) -> dict[str, Any]:
            return await asyncio.to_thread(info.call, "read", project=project, paths=paths,
                repository=repository, revision=revision, budget_bytes=max(1, min(65536, budget_bytes)))

        def inspect(project: str, operation: Literal["tree", "files", "git", "build"] = "tree",
                    repository: str | None = None, path: str | None = None, limit: int = 100,
                    cursor: str | None = None) -> dict[str, Any]:
            return info.call("inspect", project=project, operation=operation,
                repository=repository, path=path, limit=max(1, min(2000, limit)), cursor=cursor)

    selected_context = context if mcp.profile == MCPProfile.OBSERVER else local_context
    tools = (overview, frontier, delta, search, selected_context, trace, evidence, machine)
    if mcp.profile == MCPProfile.OBSERVER:
        tools += (read, inspect)
    descriptions = {
        "overview": "List registered projects or orient around a project: repositories, entry points, architecture and readiness.",
        "frontier": "Show current work, blockers, open questions and recent outcomes for a project.",
        "delta": "Retrieve changes since a cursor, or select history mode for a subject and revision, commit or time range.",
        "search": "Search source text literally or resolve an exact typed entity. Search never interprets a question.",
        "context": "Assemble implementation and selected related sections for explicit targets within a byte budget.",
        "trace": "Traverse typed source relationships from explicit targets with bounded direction, depth and result count.",
        "evidence": "Retrieve recorded evidence for an explicit subject, including provenance and attributed assessments.",
        "machine": "Read bounded host, toolchain and environment diagnostics; caller-supplied commands are not accepted.",
        "read": "Read exact registered source files and optional line ranges with content hashes and provenance.",
        "inspect": "Browse source trees and files, inspect Git state, or discover build-system metadata.",
    }
    for fn in tools:
        mcp.add_tool(fn, description=descriptions[fn.__name__], annotations=READ, structured_output=True)

    if mcp.profile == MCPProfile.MUTATOR:
        def plan(project: str, action: str, native_plan: dict[str, Any] | None = None,
                 proposal: dict[str, Any] | None = None, replan: dict[str, Any] | None = None,
                 intent: dict[str, Any] | None = None, prepared_request: dict[str, Any] | None = None,
                 authorization_id: str | None = None, repository: str | None = None) -> dict[str, Any]:
            return composition.control.plan(project, action, native_plan=native_plan,
                proposal=proposal, replan=replan, intent=intent, prepared_request=prepared_request,
                authorization_id=authorization_id, repository=repository)

        def amend_project(request: dict[str, Any]) -> dict[str, Any]:
            return composition.control.amend_project(request)

        def maintain_execution(request: dict[str, Any]) -> dict[str, Any]:
            return composition.control.maintain_execution(request)

        for fn in (plan, amend_project, maintain_execution):
            mcp.add_tool(fn, description=f"{fn.__name__}: authorized Project Control workflow mutation.",
                annotations=WRITE, structured_output=True)
