"""Serial first-class lane queues, dispatches, and wait diagnostics.

The functions in this module only operate on first-class sessions, todo claims,
and workflow lanes.  Existing local-worker child tables are intentionally not
joined into assignment or dispatch state.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections import defaultdict
from typing import Any

from ..config import utc_now
from ..models import TodoError
from ..readiness import explain_task
from ..resources import matching_instances
from .foundation import WORKSPACE_MODES, WorkflowDatabase
from .roles import require_lane_action, validate_role


ACTIVE_DISPATCH_STATES = frozenset({"active"})
TERMINAL_LANE_TASK_STATES = frozenset({"completed", "cancelled", "skipped"})


def _lane(conn: sqlite3.Connection, lane_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM workflow_lanes WHERE id=?", (lane_id,)).fetchone()
    if not row:
        raise TodoError("workflow_lane_missing", f"Workflow lane {lane_id} does not exist")
    return row


def _run_active(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise TodoError("workflow_run_missing", f"Workflow run {run_id} does not exist")
    if row["status"] != "active":
        raise TodoError("workflow_run_inactive", f"Workflow run {run_id} is {row['status']}")
    return row


def create_lane_in_transaction(
    conn: sqlite3.Connection,
    revision: int,
    *,
    run_id: str,
    lane_id: str,
    role: str,
    parent_lane_id: str | None = None,
    workspace_mode: str = "exclusive",
    allow_workspace_mode_update: bool = False,
) -> dict[str, object]:
    # Schema-v3 plan maintenance may repair an unprovisioned lane contract.
    # Ordinary create remains idempotent and never revises existing authority.
    _run_active(conn, run_id)
    validate_role(role)
    if workspace_mode not in WORKSPACE_MODES:
        raise TodoError("invalid_workspace_mode", f"Unknown workspace mode: {workspace_mode}")
    if not lane_id:
        raise TodoError("invalid_workflow_lane", "Lane id must be non-empty")
    if parent_lane_id:
        parent = _lane(conn, parent_lane_id)
        if parent["run_id"] != run_id:
            raise TodoError("workflow_lane_parent_mismatch", "Parent lane belongs to a different run")
    existing = conn.execute("SELECT * FROM workflow_lanes WHERE id=?", (lane_id,)).fetchone()
    if existing:
        expected = (run_id, parent_lane_id, role, workspace_mode)
        actual = (existing["run_id"], existing["parent_lane_id"], existing["role"], existing["workspace_mode"])
        if actual == expected:
            return {"run_id": run_id, "lane_id": lane_id, "created": False, "role": role}
        if actual[:3] != expected[:3] or not allow_workspace_mode_update:
            raise TodoError("workflow_lane_exists", f"Workflow lane {lane_id} already exists with different authority")
        if existing["state"] in {"closed", "cancelled"}:
            raise TodoError("workflow_lane_closed", f"Workflow lane {lane_id} is {existing['state']}")
        if existing["state"] == "active" or conn.execute(
            "SELECT 1 FROM workflow_dispatches WHERE lane_id=? AND state IN ('active','revoking')",
            (lane_id,),
        ).fetchone() or conn.execute(
            "SELECT 1 FROM workflow_lane_tasks WHERE lane_id=? AND state='active'",
            (lane_id,),
        ).fetchone():
            raise TodoError("workflow_lane_workspace_in_use", f"Cannot change workspace mode for active lane {lane_id}")
        if conn.execute("SELECT 1 FROM workflow_workspaces WHERE lane_id=?", (lane_id,)).fetchone():
            raise TodoError("workflow_lane_workspace_in_use", f"Cannot change workspace mode for provisioned lane {lane_id}")
        conn.execute(
            "UPDATE workflow_lanes SET workspace_mode=?,updated_at=?,revision=? WHERE id=?",
            (workspace_mode, utc_now(), revision, lane_id),
        )
        return {"run_id": run_id, "lane_id": lane_id, "created": False, "role": role,
                "workspace_mode": workspace_mode, "previous_workspace_mode": actual[3], "changed": True}
    now = utc_now()
    try:
        conn.execute(
            "INSERT INTO workflow_lanes(id,run_id,parent_lane_id,role,state,context_cursor,workspace_mode,created_at,updated_at,revision) "
            "VALUES(?,?,?,?, 'ready',0,?,?,?,?)",
            (lane_id, run_id, parent_lane_id, role, workspace_mode, now, now, revision),
        )
    except sqlite3.IntegrityError as exc:
        if parent_lane_id is None:
            raise TodoError("workflow_root_lane_exists", f"Run {run_id} already has a root lane") from exc
        raise
    return {"run_id": run_id, "lane_id": lane_id, "created": True, "role": role}


def enqueue_tasks_in_transaction(
    conn: sqlite3.Connection,
    revision: int,
    *,
    lane_id: str,
    task_ids: list[str],
) -> dict[str, object]:
    lane = _lane(conn, lane_id)
    if len(task_ids) != len(set(task_ids)):
        raise TodoError("duplicate_lane_task", "A task may appear only once in an enqueue request")
    assigned_by_task = {
        str(row["task_id"]): str(row["lane_id"])
        for row in conn.execute(
            "SELECT lt.task_id,lt.lane_id FROM workflow_lane_tasks lt "
            "JOIN workflow_lanes l ON l.id=lt.lane_id WHERE l.run_id=?",
            (lane["run_id"],),
        )
    }
    new_task_ids = [task_id for task_id in task_ids if task_id not in assigned_by_task]
    if new_task_ids and lane["state"] in {"closed", "cancelled"}:
        raise TodoError("workflow_lane_closed", f"Workflow lane {lane_id} is {lane['state']}")
    position = int(conn.execute(
        "SELECT COALESCE(MAX(position),-1)+1 FROM workflow_lane_tasks WHERE lane_id=?",
        (lane_id,),
    ).fetchone()[0])
    now = utc_now()
    added: list[dict[str, object]] = []
    for task_id in task_ids:
        if not conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
            raise TodoError("workflow_lane_task_missing", f"Task {task_id} does not exist")
        assigned_lane = assigned_by_task.get(task_id)
        if assigned_lane:
            if assigned_lane == lane_id:
                continue
            raise TodoError(
                "workflow_task_already_assigned",
                f"Task {task_id} is already assigned to lane {assigned_lane} in this run",
            )
        conn.execute(
            "INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) "
            "VALUES(?,?,?,'queued',?,?)",
            (lane_id, position, task_id, now, revision),
        )
        added.append({"task_id": task_id, "position": position})
        position += 1
    conn.execute("UPDATE workflow_lanes SET updated_at=?,revision=? WHERE id=?", (now, revision, lane_id))
    return {"run_id": lane["run_id"], "lane_id": lane_id, "enqueued": added}


def reconcile_lane_task_order_in_transaction(
    conn: sqlite3.Connection,
    revision: int,
    *,
    lane_id: str,
    task_ids: list[str],
) -> dict[str, object]:
    """Align an idle lane with declared plan order while preserving omitted tasks."""

    lane = _lane(conn, lane_id)
    if len(task_ids) != len(set(task_ids)):
        raise TodoError("duplicate_lane_task", "A task may appear only once in a lane order")
    rows = conn.execute(
        "SELECT position,task_id,state FROM workflow_lane_tasks WHERE lane_id=? ORDER BY position",
        (lane_id,),
    ).fetchall()
    current = [str(row["task_id"]) for row in rows]
    current_set = set(current)
    missing = [task_id for task_id in task_ids if task_id not in current_set]
    if missing:
        raise TodoError("workflow_lane_task_missing", f"Lane {lane_id} is missing declared task {missing[0]}")
    declared = set(task_ids)
    desired = list(task_ids) + [task_id for task_id in current if task_id not in declared]
    if desired == current:
        return {"run_id": lane["run_id"], "lane_id": lane_id, "changed": False, "task_ids": current}
    if conn.execute(
        "SELECT 1 FROM workflow_dispatches WHERE lane_id=? AND state IN ('active','revoking')",
        (lane_id,),
    ).fetchone() or any(str(row["state"]) == "active" for row in rows):
        raise TodoError(
            "workflow_lane_order_in_use",
            f"Cannot reorder workflow lane {lane_id} while it has active mutable work",
        )

    temporary_base = max((int(row["position"]) for row in rows), default=-1) + len(rows) + 1
    for offset, task_id in enumerate(current):
        conn.execute(
            "UPDATE workflow_lane_tasks SET position=? WHERE lane_id=? AND task_id=?",
            (temporary_base + offset, lane_id, task_id),
        )
    for position, task_id in enumerate(desired):
        conn.execute(
            "UPDATE workflow_lane_tasks SET position=?,revision=? WHERE lane_id=? AND task_id=?",
            (position, revision, lane_id, task_id),
        )
    conn.execute("UPDATE workflow_lanes SET updated_at=?,revision=? WHERE id=?", (utc_now(), revision, lane_id))
    return {"run_id": lane["run_id"], "lane_id": lane_id, "changed": True, "task_ids": desired}


def assign_lane_role_in_transaction(
    conn: sqlite3.Connection,
    revision: int,
    *,
    actor_lane_id: str,
    target_lane_id: str,
    role: str,
    actor_kind: str = "first_class",
) -> dict[str, object]:
    """Assign a role through an authoritative coordinator lane."""

    actor = require_lane_action(conn, actor_lane_id, "assign_role", actor_kind=actor_kind)
    target = _lane(conn, target_lane_id)
    if actor["run_id"] != target["run_id"]:
        raise TodoError("workflow_run_scope_mismatch", "Coordinator and target lane belong to different runs")
    validate_role(role)
    if conn.execute("SELECT 1 FROM workflow_dispatches WHERE lane_id=? AND state='active'", (target_lane_id,)).fetchone():
        raise TodoError("workflow_lane_role_in_use", "Cannot change the role of an actively dispatched lane")
    if target["role"] == role:
        return {"run_id": target["run_id"], "lane_id": target_lane_id, "role": role, "changed": False}
    now = utc_now()
    conn.execute(
        "UPDATE workflow_lanes SET role=?,updated_at=?,revision=? WHERE id=?",
        (role, now, revision, target_lane_id),
    )
    return {"run_id": target["run_id"], "lane_id": target_lane_id, "role": role, "changed": True}


def _lane_head(conn: sqlite3.Connection, lane_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM workflow_lane_tasks WHERE lane_id=? AND state NOT IN ('completed','cancelled','skipped') "
        "ORDER BY position LIMIT 1",
        (lane_id,),
    ).fetchone()


def lane_candidates(conn: sqlite3.Connection, run_id: str, *, role: str | None = None) -> list[dict[str, object]]:
    """Return deterministic, safe queue-head candidates without claiming them."""

    _run_active(conn, run_id)
    if role is not None:
        validate_role(role)
    candidates: list[dict[str, object]] = []
    for lane in conn.execute(
        "SELECT * FROM workflow_lanes WHERE run_id=? AND state IN ('ready','active') "
        "AND (? IS NULL OR role=?) ORDER BY id",
        (run_id, role, role),
    ).fetchall():
        if conn.execute("SELECT 1 FROM workflow_dispatches WHERE lane_id=? AND state='active'", (lane["id"],)).fetchone():
            continue
        head = _lane_head(conn, lane["id"])
        if not head or head["state"] != "queued":
            continue
        explanation = explain_task(conn, head["task_id"])
        if not explanation["ready"]:
            continue
        task = conn.execute("SELECT priority,created_at FROM tasks WHERE id=?", (head["task_id"],)).fetchone()
        candidates.append({
            "run_id": run_id,
            "lane_id": lane["id"],
            "role": lane["role"],
            "task_id": head["task_id"],
            "position": int(head["position"]),
            "priority": int(task["priority"]),
            "created_at": task["created_at"],
            "explanation": explanation,
        })
    return sorted(candidates, key=lambda item: (-int(item["priority"]), int(item["position"]), str(item["lane_id"]), str(item["task_id"])))


def deterministic_lane_assignment(conn: sqlite3.Connection, run_id: str, *, role: str | None = None) -> dict[str, object] | None:
    candidates = lane_candidates(conn, run_id, role=role)
    return candidates[0] if candidates else None


def dispatch_claim_in_transaction(
    conn: sqlite3.Connection,
    revision: int,
    *,
    run_id: str,
    session_id: str,
    claim_id: str,
    context_version: int,
    dispatch_id: str | None = None,
    hostname: str | None = None,
    pid: int | None = None,
    process_start: str | None = None,
    workspace_id: str | None = None,
    lane_id: str | None = None,
    actor_kind: str = "first_class",
) -> dict[str, object]:
    if actor_kind != "first_class":
        raise TodoError("child_lane_dispatch_forbidden", "Local-worker children cannot receive first-class lane dispatches")
    _run_active(conn, run_id)
    if context_version < 1:
        raise TodoError("invalid_context_version", "Dispatch context version must be positive")
    session = conn.execute("SELECT * FROM sessions WHERE id=? AND state='active'", (session_id,)).fetchone()
    if not session:
        raise TodoError("workflow_session_inactive", "A live first-class todo session is required")
    claim = conn.execute("SELECT * FROM claims WHERE id=? AND state='active'", (claim_id,)).fetchone()
    if not claim or claim["session_id"] != session_id:
        raise TodoError("workflow_claim_mismatch", "Dispatch requires an active claim owned by the assigned session")
    existing = conn.execute(
        "SELECT d.*,l.run_id,l.role,lt.task_id FROM workflow_dispatches d "
        "JOIN workflow_lanes l ON l.id=d.lane_id "
        "JOIN workflow_lane_tasks lt ON lt.lane_id=l.id AND lt.state='active' "
        "WHERE d.session_id=? AND d.state='active'",
        (session_id,),
    ).fetchone()
    if existing:
        if existing["claim_id"] == claim_id and existing["run_id"] == run_id and existing["task_id"] == claim["task_id"]:
            return {
                "status": "resumed", "dispatch_id": existing["id"], "run_id": run_id,
                "lane_id": existing["lane_id"], "role": existing["role"], "task_id": existing["task_id"],
            }
        raise TodoError("workflow_session_already_dispatched", "Session already has a different active lane dispatch")
    if lane_id is None:
        rows = conn.execute(
            "SELECT l.*,lt.position,lt.state AS task_state FROM workflow_lanes l "
            "JOIN workflow_lane_tasks lt ON lt.lane_id=l.id "
            "WHERE l.run_id=? AND lt.task_id=? ORDER BY l.id",
            (run_id, claim["task_id"]),
        ).fetchall()
        if len(rows) != 1:
            raise TodoError("workflow_claim_not_assignable", "Claimed task is not assigned to exactly one lane in the run")
        lane_id = str(rows[0]["id"])
    lane = require_lane_action(conn, lane_id, "claim_task", actor_kind=actor_kind, allowed_run_ids=[run_id])
    head = _lane_head(conn, lane_id)
    if not head or head["task_id"] != claim["task_id"] or head["state"] not in {"queued", "active"}:
        raise TodoError("workflow_lane_order_violation", "Claimed task is not the current serial queue head")
    active_lane = conn.execute("SELECT * FROM workflow_dispatches WHERE lane_id=? AND state='active'", (lane_id,)).fetchone()
    if active_lane:
        raise TodoError("workflow_lane_already_dispatched", f"Lane {lane_id} already has an active dispatch")
    if lane["workspace_mode"] in {"isolated_merge", "contract_split"} and not workspace_id:
        raise TodoError(
            "workflow_workspace_required",
            "Lane contract requires an assigned managed workspace",
            details={
                "run_id": run_id,
                "lane_id": lane_id,
                "task_id": claim["task_id"],
                "workspace_mode": lane["workspace_mode"],
                "root_action": "prepare_run_workspaces",
            },
        )
    if workspace_id:
        workspace = conn.execute("SELECT * FROM workflow_workspaces WHERE id=?", (workspace_id,)).fetchone()
        if not workspace or workspace["run_id"] != run_id or workspace["lane_id"] != lane_id:
            raise TodoError("workflow_workspace_mismatch", "Workspace is not assigned to the selected run and lane")
        if workspace["mode"] != lane["workspace_mode"]:
            raise TodoError("workflow_workspace_mode_mismatch", "Workspace mode differs from the lane contract")
        dispatchable_states = {"active", "artifact_ready", "queued"}
        if lane["role"] in {"integrator", "validator"}:
            dispatchable_states.update({
                # A declared wave is an intentional root-owned checkpoint.  It
                # is safe to resume its lane for observation/handoff, but never
                # to resume an in-flight apply/finalization transaction.
                "apply_failed", "conflict", "awaiting_gates", "gate_failed",
                "applied_pending_wave", "finalization_failed", "integrated",
            })
        if workspace["state"] not in dispatchable_states:
            raise TodoError("workflow_workspace_inactive", "Workspace is not in a dispatchable state")
    now = utc_now()
    dispatch_id = dispatch_id or str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,workspace_id,state,context_version,heartbeat_at,hostname,pid,process_start,created_at,revision) "
            "VALUES(?,?,?,?,?,'active',?,?,?,?,?,?,?)",
            (dispatch_id, lane_id, session_id, claim_id, workspace_id, context_version, now, hostname, pid, process_start, now, revision),
        )
        conn.execute(
            "UPDATE workflow_lane_tasks SET state='active',activated_at=COALESCE(activated_at,?),revision=? "
            "WHERE lane_id=? AND task_id=?",
            (now, revision, lane_id, claim["task_id"]),
        )
    except sqlite3.IntegrityError as exc:
        raise TodoError("workflow_dispatch_contention", "Lane or session was dispatched concurrently") from exc
    conn.execute("UPDATE workflow_lanes SET state='active',updated_at=?,revision=? WHERE id=?", (now, revision, lane_id))
    return {
        "status": "claimed", "dispatch_id": dispatch_id, "run_id": run_id, "lane_id": lane_id,
        "role": lane["role"], "task_id": claim["task_id"], "context_version": context_version,
    }


def heartbeat_dispatch_in_transaction(
    conn: sqlite3.Connection,
    revision: int,
    *,
    dispatch_id: str,
    session_id: str,
    process_start: str | None = None,
) -> dict[str, object]:
    dispatch = conn.execute("SELECT * FROM workflow_dispatches WHERE id=? AND state='active'", (dispatch_id,)).fetchone()
    if not dispatch or dispatch["session_id"] != session_id:
        raise TodoError("workflow_dispatch_inactive", "Active dispatch does not belong to this session")
    if process_start and dispatch["process_start"] and dispatch["process_start"] != process_start:
        raise TodoError("workflow_process_identity_changed", "Dispatch process incarnation does not match")
    now = utc_now()
    conn.execute("UPDATE workflow_dispatches SET heartbeat_at=?,revision=? WHERE id=?", (now, revision, dispatch_id))
    conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (now, session_id))
    return {"dispatch_id": dispatch_id, "lane_id": dispatch["lane_id"], "heartbeat_at": now}


def advance_lane_in_transaction(
    conn: sqlite3.Connection,
    revision: int,
    *,
    lane_id: str,
    task_id: str,
    dispatch_id: str,
) -> dict[str, object]:
    lane = _lane(conn, lane_id)
    active = conn.execute(
        "SELECT * FROM workflow_lane_tasks WHERE lane_id=? AND task_id=? AND state='active'",
        (lane_id, task_id),
    ).fetchone()
    dispatch = conn.execute(
        "SELECT * FROM workflow_dispatches WHERE id=? AND lane_id=? AND state='active'",
        (dispatch_id, lane_id),
    ).fetchone()
    claim = conn.execute("SELECT task_id FROM claims WHERE id=?", (dispatch["claim_id"],)).fetchone() if dispatch else None
    if not active or not dispatch or not claim or claim["task_id"] != task_id:
        raise TodoError("workflow_lane_completion_mismatch", "Task, claim, and active dispatch do not match")
    task = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task or task["status"] != "done":
        raise TodoError("workflow_parent_task_not_complete", "Only authoritative todo completion may advance a lane")
    now = utc_now()
    conn.execute(
        "UPDATE workflow_lane_tasks SET state='completed',completed_at=?,revision=? WHERE lane_id=? AND task_id=?",
        (now, revision, lane_id, task_id),
    )
    conn.execute("UPDATE workflow_dispatches SET state='released',released_at=?,revision=? WHERE id=?", (now, revision, dispatch_id))
    remaining = _lane_head(conn, lane_id)
    state = "ready" if remaining else "closed"
    conn.execute("UPDATE workflow_lanes SET state=?,updated_at=?,revision=? WHERE id=?", (state, now, revision, lane_id))
    run = conn.execute("SELECT root_task_id,status FROM workflow_runs WHERE id=?", (lane["run_id"],)).fetchone()
    run_completed = False
    pending = conn.execute(
        "SELECT lt.task_id FROM workflow_lane_tasks lt JOIN workflow_lanes l ON l.id=lt.lane_id "
        "WHERE l.run_id=? AND lt.state NOT IN ('completed','cancelled','skipped') ORDER BY l.id,lt.position LIMIT 1",
        (lane["run_id"],),
    ).fetchone()
    root = conn.execute("SELECT status FROM tasks WHERE id=?", (run["root_task_id"],)).fetchone() if run and run["root_task_id"] else None
    if run and run["status"] == "active" and root and root["status"] == "done" and not pending:
        conn.execute(
            "UPDATE workflow_runs SET status='completed',updated_at=?,revision=? WHERE id=?",
            (now, revision, lane["run_id"]),
        )
        run_completed = True
    return {
        "run_id": lane["run_id"],
        "run_completed": run_completed,
        "lane_id": lane_id,
        "task_id": task_id,
        "lane_state": state,
        "next_task_id": remaining["task_id"] if remaining else None,
    }


def reconcile_stale_dispatches_in_transaction(
    conn: sqlite3.Connection,
    revision: int,
    *,
    stale_before: str,
) -> dict[str, object]:
    stale: list[dict[str, object]] = []
    now = utc_now()
    for dispatch in conn.execute(
        "SELECT d.*,c.state AS claim_state,s.state AS session_state FROM workflow_dispatches d "
        "JOIN claims c ON c.id=d.claim_id JOIN sessions s ON s.id=d.session_id "
        "WHERE d.state='active' ORDER BY d.id"
    ).fetchall():
        reasons: list[str] = []
        if dispatch["claim_state"] != "active":
            reasons.append("claim_inactive")
        if dispatch["session_state"] != "active":
            reasons.append("session_inactive")
        if dispatch["heartbeat_at"] <= stale_before:
            reasons.append("heartbeat_stale")
        if not reasons:
            continue
        conn.execute("UPDATE workflow_dispatches SET state='stale',released_at=?,revision=? WHERE id=?", (now, revision, dispatch["id"]))
        conn.execute("UPDATE workflow_lanes SET state='attention_required',updated_at=?,revision=? WHERE id=?", (now, revision, dispatch["lane_id"]))
        stale.append({"dispatch_id": dispatch["id"], "lane_id": dispatch["lane_id"], "reasons": reasons})
    return {"stale_dispatches": stale, "stale_before": stale_before}


def _add_edge(edges: list[dict[str, str]], source: str, target: str, reason: str) -> None:
    if source != target or reason:
        edges.append({"from": source, "to": target, "reason": reason})


def wait_graph(conn: sqlite3.Connection, run_id: str) -> dict[str, object]:
    """Build bounded authoritative wait edges and report strongly connected cycles."""

    _run_active(conn, run_id)
    edges: list[dict[str, str]] = []
    lane_for_task = {
        row["task_id"]: row["lane_id"]
        for row in conn.execute(
            "SELECT lt.task_id,lt.lane_id FROM workflow_lane_tasks lt JOIN workflow_lanes l ON l.id=lt.lane_id WHERE l.run_id=?",
            (run_id,),
        )
    }
    for row in conn.execute(
        "SELECT d.task_id,d.prerequisite_task_id FROM task_dependencies d "
        "WHERE d.type='task' AND d.task_id IN (SELECT lt.task_id FROM workflow_lane_tasks lt JOIN workflow_lanes l ON l.id=lt.lane_id WHERE l.run_id=?)",
        (run_id,),
    ):
        if row["prerequisite_task_id"]:
            _add_edge(edges, f"task:{row['task_id']}", f"task:{row['prerequisite_task_id']}", "task_dependency")
    for lane_id in sorted(set(lane_for_task.values())):
        prior: str | None = None
        for row in conn.execute(
            "SELECT task_id,state FROM workflow_lane_tasks WHERE lane_id=? AND state NOT IN ('completed','cancelled','skipped') ORDER BY position",
            (lane_id,),
        ):
            if prior:
                _add_edge(edges, f"task:{row['task_id']}", f"task:{prior}", "serial_lane_queue")
            prior = row["task_id"]
    for row in conn.execute(
        "SELECT ic.task_id,i.owner_task_id FROM interface_consumers ic JOIN interfaces i ON i.id=ic.interface_id "
        "WHERE ic.task_id IN (SELECT lt.task_id FROM workflow_lane_tasks lt JOIN workflow_lanes l ON l.id=lt.lane_id WHERE l.run_id=?) "
        "AND (i.state!=ic.required_state OR (ic.required_version IS NOT NULL AND i.version!=ic.required_version))",
        (run_id,),
    ):
        _add_edge(edges, f"task:{row['task_id']}", f"task:{row['owner_task_id']}", "interface_wait")
    for message in conn.execute(
        "SELECT id,author_lane_id FROM workflow_messages WHERE run_id=? AND blocking=1 AND state='open' ORDER BY id",
        (run_id,),
    ):
        for recipient in conn.execute(
            "SELECT recipient_type,recipient_id FROM workflow_message_recipients WHERE message_id=? ORDER BY recipient_type,recipient_id",
            (message["id"],),
        ):
            if recipient["recipient_type"] == "lane":
                _add_edge(edges, f"lane:{message['author_lane_id']}", f"lane:{recipient['recipient_id']}", f"blocking_message:{message['id']}")
            elif recipient["recipient_type"] == "task" and recipient["recipient_id"] in lane_for_task:
                _add_edge(edges, f"lane:{message['author_lane_id']}", f"task:{recipient['recipient_id']}", f"blocking_message:{message['id']}")
    for rendezvous in conn.execute("SELECT id,join_task_id FROM workflow_rendezvous WHERE run_id=? AND state='closed' ORDER BY id", (run_id,)):
        for participant in conn.execute(
            "SELECT p.lane_id FROM workflow_rendezvous_participants p LEFT JOIN workflow_rendezvous_arrivals a "
            "ON a.rendezvous_id=p.rendezvous_id AND a.lane_id=p.lane_id AND a.state='valid' "
            "WHERE p.rendezvous_id=? AND p.required=1 AND a.lane_id IS NULL ORDER BY p.lane_id",
            (rendezvous["id"],),
        ):
            _add_edge(edges, f"task:{rendezvous['join_task_id']}", f"lane:{participant['lane_id']}", f"rendezvous:{rendezvous['id']}")
    for row in conn.execute(
        "SELECT q.integration_task_id,a.task_id FROM workflow_integration_queue q "
        "JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id WHERE q.run_id=? "
        "AND q.state NOT IN ('integrated','rejected')",
        (run_id,),
    ):
        _add_edge(edges, f"task:{row['integration_task_id']}", f"task:{row['task_id']}", "integration_wait")
    run_tasks = sorted(lane_for_task)
    for task_id in run_tasks:
        for lock in conn.execute(
            "SELECT tl.lock_name,c.task_id AS owner_task_id FROM task_locks tl "
            "JOIN lock_leases ll ON ll.lock_name=tl.lock_name AND ll.state='active' "
            "LEFT JOIN claims c ON c.id=ll.claim_id WHERE tl.task_id=? AND tl.phase='claim'",
            (task_id,),
        ):
            owner = lock["owner_task_id"]
            if owner and owner != task_id:
                _add_edge(edges, f"task:{task_id}", f"task:{owner}", f"lock:{lock['lock_name']}")
        for request in conn.execute(
            "SELECT id,selector FROM resource_requests WHERE task_id=? AND phase='claim' AND required=1 ORDER BY id",
            (task_id,),
        ):
            for instance in matching_instances(conn, request["selector"]):
                for lease in conn.execute(
                    "SELECT c.task_id AS owner_task_id FROM resource_leases rl LEFT JOIN claims c ON c.id=rl.claim_id "
                    "WHERE rl.instance_id=? AND rl.state='active'",
                    (instance["id"],),
                ):
                    owner = lease["owner_task_id"]
                    if owner and owner != task_id:
                        _add_edge(edges, f"task:{task_id}", f"task:{owner}", f"resource:{instance['id']}")
    edges = sorted(edges, key=lambda edge: (edge["from"], edge["to"], edge["reason"]))[:2048]
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adjacency[edge["from"]].append(edge["to"])
        adjacency.setdefault(edge["to"], [])
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in adjacency[node]:
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while stack:
                value = stack.pop()
                on_stack.remove(value)
                component.append(value)
                if value == node:
                    break
            self_loop = len(component) == 1 and component[0] in adjacency[component[0]]
            if len(component) > 1 or self_loop:
                cycles.append(sorted(component))

    for node in sorted(adjacency):
        if node not in indices:
            visit(node)
    cycles.sort()
    return {
        "run_id": run_id,
        "status": "attention_required" if cycles else "clear",
        "edges": edges,
        "cycles": [{"nodes": cycle, "kind": "runtime_wait_cycle"} for cycle in cycles],
        "local_children_are_run_nodes": False,
    }


class LaneService:
    """Transactional facade for run-lane state, suitable for WorkflowKernel composition."""

    def __init__(self, database: WorkflowDatabase):
        self.database = database

    def _mutate(self, *, actor: str | None, entity_id: str, event_type: str, payload, operation) -> dict[str, object]:
        result, revision = self.database.mutate(
            actor_session_id=actor,
            entity_type="workflow_lane",
            entity_id=entity_id,
            event_type=event_type,
            payload=payload,
            operation=operation,
        )
        return {**result, "project_revision": revision}

    def create(self, *, run_id: str, lane_id: str, role: str, parent_lane_id: str | None = None, workspace_mode: str = "exclusive", actor_session_id: str | None = None) -> dict[str, object]:
        return self._mutate(
            actor=actor_session_id, entity_id=lane_id, event_type="workflow.lane.created",
            payload={"run_id": run_id, "lane_id": lane_id, "role": role, "parent_lane_id": parent_lane_id},
            operation=lambda conn, rev: create_lane_in_transaction(conn, rev, run_id=run_id, lane_id=lane_id, role=role, parent_lane_id=parent_lane_id, workspace_mode=workspace_mode),
        )

    def enqueue(self, *, lane_id: str, task_ids: list[str], actor_session_id: str | None = None) -> dict[str, object]:
        return self._mutate(
            actor=actor_session_id, entity_id=lane_id, event_type="workflow.lane_tasks.enqueued",
            payload={"lane_id": lane_id, "task_ids": task_ids},
            operation=lambda conn, rev: enqueue_tasks_in_transaction(conn, rev, lane_id=lane_id, task_ids=task_ids),
        )

    def assign_role(self, *, actor_lane_id: str, target_lane_id: str, role: str, actor_session_id: str | None = None, actor_kind: str = "first_class") -> dict[str, object]:
        return self._mutate(
            actor=actor_session_id, entity_id=target_lane_id, event_type="workflow.lane.role_assigned",
            payload={"actor_lane_id": actor_lane_id, "target_lane_id": target_lane_id, "role": role},
            operation=lambda conn, rev: assign_lane_role_in_transaction(
                conn, rev, actor_lane_id=actor_lane_id, target_lane_id=target_lane_id,
                role=role, actor_kind=actor_kind,
            ),
        )

    def candidates(self, run_id: str, *, role: str | None = None) -> list[dict[str, object]]:
        with self.database.read() as conn:
            return lane_candidates(conn, run_id, role=role)

    def dispatch(self, **kwargs: Any) -> dict[str, object]:
        session_id = str(kwargs["session_id"])
        claim_id = str(kwargs["claim_id"])
        return self._mutate(
            actor=session_id, entity_id=str(kwargs.get("lane_id") or claim_id), event_type="workflow.dispatch.created",
            payload={"run_id": kwargs["run_id"], "session_id": session_id, "claim_id": claim_id},
            operation=lambda conn, rev: dispatch_claim_in_transaction(conn, rev, **kwargs),
        )

    def heartbeat(self, *, dispatch_id: str, session_id: str, process_start: str | None = None) -> dict[str, object]:
        return self._mutate(
            actor=session_id, entity_id=dispatch_id, event_type="workflow.dispatch.heartbeat",
            payload={"dispatch_id": dispatch_id},
            operation=lambda conn, rev: heartbeat_dispatch_in_transaction(conn, rev, dispatch_id=dispatch_id, session_id=session_id, process_start=process_start),
        )

    def advance(self, *, lane_id: str, task_id: str, dispatch_id: str, actor_session_id: str | None = None) -> dict[str, object]:
        return self._mutate(
            actor=actor_session_id, entity_id=lane_id, event_type="workflow.lane.advanced",
            payload={"lane_id": lane_id, "task_id": task_id, "dispatch_id": dispatch_id},
            operation=lambda conn, rev: advance_lane_in_transaction(conn, rev, lane_id=lane_id, task_id=task_id, dispatch_id=dispatch_id),
        )

    def reconcile_stale(self, *, stale_before: str, actor_session_id: str | None = None) -> dict[str, object]:
        return self._mutate(
            actor=actor_session_id, entity_id="dispatches", event_type="workflow.dispatches.reconciled",
            payload=lambda value: {"stale_before": stale_before, "count": len(value["stale_dispatches"])},
            operation=lambda conn, rev: reconcile_stale_dispatches_in_transaction(conn, rev, stale_before=stale_before),
        )

    def wait_diagnostics(self, run_id: str) -> dict[str, object]:
        with self.database.read() as conn:
            return wait_graph(conn, run_id)
