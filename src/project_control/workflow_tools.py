"""Project Control's exact-six MCP adapter for the canonical workflow protocol.

This module is deliberately only a transport adapter.  Claims, capabilities,
scheduling, response bounds, recovery, and all other workflow semantics remain
inside :class:`todo_orchestrator.workflow.protocol.WorkflowProtocol`.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, Protocol

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

if TYPE_CHECKING:  # Avoid selecting a Todo installation before runtime identity verification.
    from todo_orchestrator.workflow.protocol import WorkflowProtocol


WORKFLOW_TOOL_NAMES = (
    "next_task",
    "inspect_task",
    "coordinate_task",
    "delegate_task",
    "collect_delegation",
    "finish_task",
)

WORKFLOW_INSTRUCTIONS = (
    "For substantial repository work, use Project Control's workflow tools as the ordinary "
    "workflow protocol. Start with next_task, use inspect_task for bounded current-task "
    "context, and use coordinate_task for synchronization. Rich Project Control reads are "
    "secondary escalation tools when current-task context is insufficient. First-class Codex "
    "agents receive durable run lanes and roles. Local workers are subordinate bounded children "
    "of one parent claim and never act as first-class lanes. Delegation is nonblocking. Opaque "
    "handles are the only model-facing authorization."
)

_MUTATING = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


class _WorkflowProtocolPort(Protocol):
    """Structural type for the already-verified canonical protocol instance."""

    def next_task(self, **arguments: object) -> dict[str, object]: ...
    def inspect_task(self, **arguments: object) -> dict[str, object]: ...
    def coordinate_task(self, **arguments: object) -> dict[str, object]: ...
    def delegate_task(self, **arguments: object) -> dict[str, object]: ...
    def collect_delegation(self, **arguments: object) -> dict[str, object]: ...
    def finish_task(self, **arguments: object) -> dict[str, object]: ...


def _protocol_version() -> int:
    """Read the canonical version lazily, after runtime identity is established."""

    try:
        from todo_orchestrator.workflow.foundation import PROTOCOL_VERSION

        return int(PROTOCOL_VERSION)
    except Exception:
        # Error envelopes must remain bounded even when binding itself failed.
        return 2


def register_workflow_tools(
    server: FastMCP,
    protocol: "WorkflowProtocol | _WorkflowProtocolPort | None" = None,
    *,
    protocol_factory: Callable[[], "WorkflowProtocol | _WorkflowProtocolPort"] | None = None,
    diagnostic_factory: Callable[[], str] | None = None,
) -> tuple[str, ...]:
    """Register the canonical six tools on ``server`` without opening any authority.

    The protocol is resolved lazily on first invocation.  The verified runtime
    binding may therefore construct the MCP application without importing or
    opening a Todo database.  Resolution is sticky: this adapter never rebinds a
    live server to another protocol instance.
    """

    instance = protocol

    def active_protocol() -> _WorkflowProtocolPort:
        nonlocal instance
        if instance is None:
            if protocol_factory is None:
                raise RuntimeError("workflow_kernel_unconfigured")
            instance = protocol_factory()
        return instance

    def diagnostic_id() -> str:
        return diagnostic_factory() if diagnostic_factory is not None else "diag_" + secrets.token_urlsafe(12)

    def invoke(method: str, **arguments: object) -> dict[str, object]:
        try:
            return getattr(active_protocol(), method)(**arguments)
        except Exception as error:
            code = getattr(error, "code", None)
            if isinstance(code, str) and code:
                result: dict[str, object] = {
                    "protocol_version": _protocol_version(),
                    "status": "attention_required",
                    "reason": code,
                    "allowed_actions": [],
                    "recommended_next_call": "next_task",
                    "warnings": [],
                }
                details = getattr(error, "details", None)
                if code == "runtime_identity_mismatch" and isinstance(details, dict):
                    result["compatibility"] = details
                return result
            return {
                "protocol_version": _protocol_version(),
                "status": "attention_required",
                "reason": "unexpected_internal_failure",
                "diagnostic_id": diagnostic_id(),
                "allowed_actions": [],
                "recommended_next_call": "next_task",
                "warnings": [],
            }

    @server.tool(
        description="Atomically resume or claim a first-class run lane and its current task.",
        annotations=_MUTATING,
        structured_output=True,
    )
    def next_task(repo_root: str, task_id: str | None = None) -> dict[str, object]:
        return invoke("next_task", repo_root=repo_root, task_id=task_id)

    @server.tool(
        description="Read one bounded, scope-aware workflow or source context target.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def inspect_task(
        workflow_handle: str,
        kind: Literal[
            "task", "source", "evidence", "run", "lane", "decision", "messages",
            "rendezvous", "workspace", "integration",
        ],
        target: str | None = None,
        budget_bytes: int = 8192,
    ) -> dict[str, object]:
        return invoke(
            "inspect_task",
            workflow_handle=workflow_handle,
            kind=kind,
            target=target,
            budget_bytes=budget_bytes,
        )

    @server.tool(
        description="Perform one role- and scope-validated typed coordination action.",
        annotations=_MUTATING,
        structured_output=True,
    )
    def coordinate_task(
        workflow_handle: str,
        action: Literal[
            "sync", "fork", "message", "answer", "arrive", "publish_interface",
            "run_gates", "request_integration", "accept_child", "reject_child",
        ],
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return invoke(
            "coordinate_task", workflow_handle=workflow_handle, action=action, payload=payload
        )

    @server.tool(
        description="Opportunistically start one bounded subordinate local-worker child.",
        annotations=_MUTATING,
        structured_output=True,
    )
    def delegate_task(
        workflow_handle: str,
        delegated_objective: str,
        mode: Literal["auto", "readonly", "writable"] = "auto",
    ) -> dict[str, object]:
        return invoke(
            "delegate_task",
            workflow_handle=workflow_handle,
            delegated_objective=delegated_objective,
            mode=mode,
        )

    @server.tool(
        description="Nonblockingly collect a candidate result from one subordinate child.",
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def collect_delegation(delegation_handle: str) -> dict[str, object]:
        return invoke("collect_delegation", delegation_handle=delegation_handle)

    @server.tool(
        description="Complete, hand off, block, or release the first-class parent task.",
        annotations=_MUTATING,
        structured_output=True,
    )
    def finish_task(
        workflow_handle: str,
        action: Literal["complete", "handoff", "block", "release"],
        disposition: str | None = None,
        note: str | None = None,
        reason: str | None = None,
    ) -> dict[str, object]:
        return invoke(
            "finish_task",
            workflow_handle=workflow_handle,
            action=action,
            disposition=disposition,
            note=note,
            reason=reason,
        )

    return WORKFLOW_TOOL_NAMES


def create_workflow_mcp(
    protocol: "WorkflowProtocol | _WorkflowProtocolPort | None" = None,
    *,
    protocol_factory: Callable[[], "WorkflowProtocol | _WorkflowProtocolPort"] | None = None,
    diagnostic_factory: Callable[[], str] | None = None,
) -> FastMCP:
    """Create a standalone stdio-capable Project Control workflow server."""

    server = FastMCP("project-control", instructions=WORKFLOW_INSTRUCTIONS, log_level="ERROR")
    register_workflow_tools(
        server,
        protocol,
        protocol_factory=protocol_factory,
        diagnostic_factory=diagnostic_factory,
    )
    return server
