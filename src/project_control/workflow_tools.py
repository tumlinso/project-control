"""Compact Project Control workflow tools over the standalone Todo engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .profiles import WORKFLOW_TOOL_NAMES


WORKFLOW_INSTRUCTIONS = (
    "For substantial repository work, start with next_task. It resumes or claims work and "
    "returns the task sheet, bounded context, an opaque workflow handle, permitted actions, "
    "and a recommended next call. Use inspect_task only for needed context that is missing; "
    "use coordinate_task for synchronization and supported workflow actions; finish_task "
    "records completion, handoff, blocking, or release, and allows authorized reviewers to "
    "accept or reject outcomes and integrate accepted work. Required gates still apply. The "
    "handle carries task authorization, so do not manage Todo attempt IDs, leases, or "
    "credentials directly. Project-wide reads remain available without a task claim."
)


@dataclass(frozen=True, slots=True)
class MaintenanceHostContext:
    """Trusted startup identity retained for non-workflow host wiring."""

    recipient_principal: str


def trusted_maintenance_context(principal: str) -> MaintenanceHostContext:
    """Construct an immutable host context; never accept it from tool input."""
    if not isinstance(principal, str) or not principal or len(principal) > 160:
        raise ValueError("maintenance_host_principal_invalid")
    return MaintenanceHostContext(recipient_principal=principal)


def register_maintenance_tool(*_args, **_kwargs) -> str:
    """Reject the removed standalone recovery tool without adding a raw route."""
    raise RuntimeError("standalone_maintenance_tool_removed_use_plan_recover")

_MUTATING = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False,
)
_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
)


def register_workflow_tools(server: FastMCP, workflow_host: object) -> tuple[str, ...]:
    """Register the four handle-based tools backed by a trusted host facade.

    Production passes ``NativeWorkflowHost`` and the compact facade binds it to
    host identity, profile, registry, and durable handle storage. Structural
    facade instances are accepted for focused contract tests.
    """
    if workflow_host is None:
        raise ValueError("workflow_host_required")

    facade = workflow_host
    if not all(callable(getattr(facade, name, None)) for name in WORKFLOW_TOOL_NAMES):
        from .compact_workflow import CompactWorkflowHost

        facade = CompactWorkflowHost(workflow_host)

    @server.tool(
        description=(
            "Resume your current task or claim ready work in a registered workspace. "
            "Use task_id or run_id only to select the intended work. Returns a bounded task "
            "sheet, context, opaque workflow_handle, allowed_actions, and recommended_next_call. "
            "Claiming or resuming changes workflow state."
        ),
        annotations=_MUTATING,
        structured_output=True,
    )
    def next_task(
        project: str,
        repository: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        return facade.next_task(project=project, repository=repository, task_id=task_id, run_id=run_id)

    @server.tool(
        description=(
            "Read a bounded view related to workflow_handle. Choose a supported kind and, "
            "when needed, target a task, source, evidence, run, candidate, effect, resource, "
            "message, context fragment, or legacy record. Read-only."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def inspect_task(
        workflow_handle: str,
        kind: Literal[
            "task", "source", "evidence", "run", "lane", "decision", "messages",
            "rendezvous", "context_fragment", "workspace", "integration", "candidate",
            "effect", "resource", "child", "recovery",
        ],
        target: str | None = None,
        budget_bytes: int = 8192,
    ) -> dict[str, Any]:
        return facade.inspect_task(
            workflow_handle=workflow_handle, kind=kind, target=target, budget_bytes=budget_bytes
        )

    @server.tool(
        description=(
            "Perform a supported workflow action under workflow_handle, such as synchronize, "
            "message, answer a question, publish context, run gates, request integration, "
            "coordinate children or resources, or recover an interrupted operation. Put the "
            "action's typed fields in payload; the host checks role and task scope."
        ),
        annotations=_MUTATING,
        structured_output=True,
    )
    def coordinate_task(
        workflow_handle: str,
        action: Literal[
            "sync", "fork", "message", "answer", "arrive", "publish_interface",
            "publish_context", "run_gates", "bind_required_gates", "request_integration",
            "accept_child", "reject_child", "resolve_decision", "accept_checkpoint",
            "release_barrier", "arrive_rendezvous", "open_blocker", "clear_blocker",
            "publish_finding", "invalidate_finding", "start_child", "submit_child_candidate",
            "review_child_candidate", "cancel_child", "reconcile_child", "integrate", "recover",
            "verify", "heartbeat", "resume", "release_attempt", "report_outcome",
            "accept_outcome", "reject_outcome", "reserve_resource", "reconcile_resource",
            "retry_resource", "release_resource",
        ],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return facade.coordinate_task(workflow_handle=workflow_handle, action=action, payload=payload)

    @server.tool(
        description=(
            "Finish the task named by workflow_handle as complete, handoff, block, or release. "
            "For a reviewer handle, accept or reject the candidate, or integrate accepted work. "
            "The host applies required verification and acceptance rules and reports when "
            "independent review or integration remains pending."
        ),
        annotations=_MUTATING,
        structured_output=True,
    )
    def finish_task(
        workflow_handle: str,
        action: Literal["complete", "handoff", "block", "release", "accept", "reject", "integrate"],
        disposition: str | None = None,
        note: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return facade.finish_task(
            workflow_handle=workflow_handle,
            action=action,
            disposition=disposition,
            note=note,
            reason=reason,
        )

    return WORKFLOW_TOOL_NAMES


def create_workflow_mcp(workflow_host: object) -> FastMCP:
    """Create a small stdio-capable server for the compact workflow adapter."""
    server = FastMCP("project-control", instructions=WORKFLOW_INSTRUCTIONS, log_level="ERROR")
    register_workflow_tools(server, workflow_host)
    return server
