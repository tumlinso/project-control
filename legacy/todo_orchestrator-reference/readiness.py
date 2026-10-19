"""Computed execution state and deterministic candidate scoring."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .graph import downstream_unlock_value, evaluate_dependencies
from .ownership import ownership_conflicts
from .resources import matching_instances

TERMINAL = {"done", "superseded", "cancelled", "stale"}


def explain_task(conn: sqlite3.Connection, task_id: str) -> dict[str, object]:
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        return {"task_id": task_id, "execution": "missing", "ready": False, "reasons": ["task does not exist"]}
    reasons: list[object] = []
    if task["kind"] in {"epic", "workstream"}:
        children = conn.execute(
            "SELECT id,status FROM tasks WHERE parent_id=? ORDER BY id",
            (task_id,),
        ).fetchall()
        incomplete = [str(row["id"]) for row in children if str(row["status"]) != "done"]
        if incomplete:
            return {
                "task_id": task_id,
                "lifecycle": task["status"],
                "execution": "inactive",
                "ready": False,
                "reasons": [{"incomplete_children": incomplete}],
            }
        if task["kind"] == "epic" and not children:
            return {
                "task_id": task_id,
                "lifecycle": task["status"],
                "execution": "inactive",
                "ready": False,
                "reasons": ["leaf epics are not claimable"],
            }
    if task["status"] in TERMINAL:
        return {"task_id": task_id, "lifecycle": task["status"], "execution": "closed", "ready": False, "reasons": ["task is terminal"]}
    if task["status"] == "attention_required":
        return {"task_id": task_id, "lifecycle": task["status"], "execution": "attention_required", "ready": False, "reasons": [task["attention_reason"] or "reconciliation required"]}
    claim = conn.execute("SELECT id,session_id,state,expires_at FROM claims WHERE task_id=? AND state IN ('active','orphaned') ORDER BY created_at DESC LIMIT 1", (task_id,)).fetchone()
    if claim:
        execution = "claimed" if claim["state"] == "active" else "orphaned"
        return {"task_id": task_id, "lifecycle": task["status"], "execution": execution, "ready": False, "claim": dict(claim), "reasons": [f"task has {claim['state']} claim"]}
    if task["status"] == "blocked":
        reasons.append(task["attention_reason"] or "task is explicitly blocked")
        return {"task_id": task_id, "lifecycle": task["status"], "execution": "blocked_dependency", "ready": False, "reasons": reasons}
    dependencies_ok, dependency_details = evaluate_dependencies(conn, task_id)
    reasons.extend(dependency_details)
    if not dependencies_ok:
        state = next((item["blocked_state"] for item in dependency_details if not item["satisfied"]), "blocked_dependency")
        return {"task_id": task_id, "lifecycle": task["status"], "execution": state, "ready": False, "reasons": reasons}
    conflicts = ownership_conflicts(conn, task_id)
    if conflicts:
        reasons.extend(conflicts)
        return {"task_id": task_id, "lifecycle": task["status"], "execution": "blocked_scope", "ready": False, "reasons": reasons}
    busy_locks = [
        row[0]
        for row in conn.execute(
            "SELECT tl.lock_name FROM task_locks tl JOIN lock_leases ll ON ll.lock_name=tl.lock_name AND ll.state='active' WHERE tl.task_id=? AND tl.phase='claim' ORDER BY tl.lock_name",
            (task_id,),
        )
    ]
    if busy_locks:
        return {"task_id": task_id, "lifecycle": task["status"], "execution": "blocked_scope", "ready": False, "reasons": [{"busy_locks": busy_locks}]}
    unavailable = []
    for request in conn.execute("SELECT * FROM resource_requests WHERE task_id=? AND phase='claim' AND required=1", (task_id,)):
        available = False
        for instance in matching_instances(conn, request["selector"]):
            active = conn.execute("SELECT COUNT(*) FROM resource_leases WHERE instance_id=? AND state='active'", (instance["id"],)).fetchone()[0]
            if int(active) < int(instance["capacity"]):
                available = True
                break
        if not available:
            unavailable.append(request["selector"])
    if unavailable:
        return {"task_id": task_id, "lifecycle": task["status"], "execution": "blocked_resource", "ready": False, "reasons": [{"unavailable_resources": unavailable}]}
    return {"task_id": task_id, "lifecycle": task["status"], "execution": "ready", "ready": True, "reasons": reasons or ["all prerequisites and scopes are satisfied"]}


def ready_tasks(conn: sqlite3.Connection) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    now = datetime.now(timezone.utc)
    for task in conn.execute(
        "SELECT * FROM tasks WHERE status IN ('planned','in_progress','review') ORDER BY id"
    ).fetchall():
        explanation = explain_task(conn, task["id"])
        if not explanation["ready"]:
            continue
        created = datetime.fromisoformat(task["created_at"].replace("Z", "+00:00"))
        age_seconds = max(0, int((now - created).total_seconds()))
        unlock = downstream_unlock_value(conn, task["id"])
        score = int(task["priority"]) * 1_000_000 + unlock * 10_000 + min(age_seconds, 9999)
        candidates.append({"task_id": task["id"], "priority": task["priority"], "unlock_value": unlock, "age_seconds": age_seconds, "score": score, "explanation": explanation})
    return sorted(candidates, key=lambda item: (-int(item["score"]), str(item["task_id"])))
