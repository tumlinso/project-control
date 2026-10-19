"""Server-enforced authority for first-class workflow lane roles.

Role authority is deliberately additive to the existing todo checks.  Passing a
role name never grants a task scope, claim, lock, resource, interface, or gate;
callers must satisfy those authorities independently.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from ..models import TodoError
from .foundation import ROLES


ROLE_ACTIONS: dict[str, frozenset[str]] = {
    "coordinator": frozenset({
        "inspect", "sync", "claim_task", "resume_dispatch", "heartbeat",
        "finish_task", "fork", "assign_role", "propose_plan", "create_rendezvous",
        "message", "answer", "publish_decision", "request_integration", "run_gates",
        "publish_context", "publish_project_context", "delegate_child", "accept_child", "reject_child", "bind_required_gates",
    }),
    "implementer": frozenset({
        "inspect", "sync", "claim_task", "resume_dispatch", "heartbeat",
        "edit_scope", "publish_artifact", "publish_interface", "publish_project_context", "message", "answer",
        "arrive", "finish_task", "delegate_child", "accept_child", "reject_child",
        "run_gates",
        "bind_required_gates",
    }),
    "validator": frozenset({
        "inspect", "sync", "claim_task", "resume_dispatch", "heartbeat", "run_gates",
        "publish_validation_artifact", "message", "answer", "arrive", "finish_task",
    }),
    "integrator": frozenset({
        "inspect", "sync", "claim_task", "resume_dispatch", "heartbeat", "edit_scope",
        "publish_artifact", "publish_interface", "publish_project_context", "message", "answer", "arrive", "finish_task",
        "manage_integration_queue", "resolve_conflict", "run_gates",
        "publish_context",
    }),
    "specialist": frozenset({
        "inspect", "sync", "claim_task", "resume_dispatch", "heartbeat", "edit_scope",
        "publish_artifact", "publish_interface", "publish_project_context", "message", "answer", "arrive",
        "finish_task", "delegate_child", "accept_child", "reject_child",
        "run_gates",
    }),
}


def validate_role(role: str) -> str:
    if role not in ROLES:
        raise TodoError("invalid_workflow_role", f"Unknown workflow role: {role}")
    return role


def allowed_actions(role: str) -> list[str]:
    """Return the stable, sorted action set for a server-assigned role."""

    validate_role(role)
    return sorted(ROLE_ACTIONS[role])


def require_role_action(role: str, action: str, *, actor_kind: str = "first_class") -> None:
    """Reject actions outside a first-class lane's server-assigned role.

    Local-worker children are intentionally rejected before role evaluation;
    they have no first-class role to exercise.
    """

    if actor_kind != "first_class":
        raise TodoError(
            "child_run_authority_forbidden",
            "Local-worker children cannot exercise first-class lane role authority",
        )
    validate_role(role)
    if action not in ROLE_ACTIONS[role]:
        raise TodoError(
            "workflow_role_forbidden",
            f"Role {role} cannot perform workflow action {action}",
            details={"role": role, "action": action, "allowed_actions": allowed_actions(role)},
        )


def require_lane_action(
    conn: sqlite3.Connection,
    lane_id: str,
    action: str,
    *,
    actor_kind: str = "first_class",
    allowed_run_ids: Iterable[str] | None = None,
) -> sqlite3.Row:
    """Load authoritative lane role state and enforce one action."""

    lane = conn.execute("SELECT * FROM workflow_lanes WHERE id=?", (lane_id,)).fetchone()
    if not lane:
        raise TodoError("workflow_lane_missing", f"Workflow lane {lane_id} does not exist")
    if allowed_run_ids is not None and lane["run_id"] not in set(allowed_run_ids):
        raise TodoError("workflow_run_scope_mismatch", "Lane is outside the authorized run scope")
    if lane["state"] in {"closed", "cancelled"}:
        raise TodoError("workflow_lane_closed", f"Workflow lane {lane_id} is {lane['state']}")
    require_role_action(str(lane["role"]), action, actor_kind=actor_kind)
    return lane
