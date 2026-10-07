"""Declarative task graph validation, dependency evaluation, and barrier reevaluation."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict, deque

from .config import utc_now
from .models import TodoError


def validate_acyclic(tasks: list[dict[str, object]]) -> None:
    ids = {str(task["id"]) for task in tasks}
    children: dict[str, list[str]] = defaultdict(list)
    indegree = {task_id: 0 for task_id in ids}
    for task in tasks:
        task_id = str(task["id"])
        parent = task.get("parent_id")
        if parent:
            if str(parent) not in ids:
                raise TodoError("unknown_parent", f"Task {task_id} references missing parent {parent}")
            children[str(parent)].append(task_id)
            indegree[task_id] += 1
        for dependency in task.get("depends_on", []):
            prerequisite = dependency.get("task_id")
            if dependency.get("type") == "task" and prerequisite:
                if str(prerequisite) not in ids:
                    raise TodoError("unknown_dependency", f"Task {task_id} references missing task {prerequisite}")
                children[str(prerequisite)].append(task_id)
                indegree[task_id] += 1
    queue = deque(sorted(key for key, value in indegree.items() if value == 0))
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(ids):
        raise TodoError("cyclic_graph", "Task parent/prerequisite relationships contain a cycle")


def _decision_matches(value: object, condition: dict[str, object]) -> bool:
    operator = condition.get("operator", "equals")
    expected = condition.get("value")
    if operator == "equals":
        return value == expected
    if operator == "in":
        return value in (expected or [])
    raise TodoError("unsafe_condition", f"Unsupported declarative decision operator: {operator}")


def dependency_status(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[bool, str, str]:
    kind = row["type"]
    if kind == "task":
        task = conn.execute("SELECT status,result FROM tasks WHERE id=?", (row["prerequisite_task_id"],)).fetchone()
        condition = json.loads(row["condition_json"] or "{}")
        allowed = condition.get("dispositions")
        satisfied = bool(task and task["status"] == "done" and (not allowed or task["result"] in allowed))
        return satisfied, "blocked_dependency", f"task {row['prerequisite_task_id']} must be done"
    if kind == "checkpoint":
        checkpoint = conn.execute("SELECT state FROM checkpoints WHERE id=?", (row["checkpoint_id"],)).fetchone()
        return bool(checkpoint and checkpoint[0] == "reached"), "blocked_dependency", f"checkpoint {row['checkpoint_id']} must be reached"
    if kind == "interface":
        interface = conn.execute("SELECT state,version FROM interfaces WHERE id=?", (row["interface_id"],)).fetchone()
        condition = json.loads(row["condition_json"] or "{}")
        required_state = condition.get("state", "frozen")
        required_version = condition.get("version")
        satisfied = bool(interface and interface["state"] == required_state and (not required_version or interface["version"] == required_version))
        return satisfied, "blocked_dependency", f"interface {row['interface_id']} must be {required_state}"
    if kind == "barrier":
        barrier = conn.execute("SELECT state FROM barriers WHERE id=?", (row["barrier_id"],)).fetchone()
        return bool(barrier and barrier[0] == "open"), "blocked_barrier", f"barrier {row['barrier_id']} must be open"
    if kind == "decision":
        decision = conn.execute("SELECT value_json FROM decisions WHERE id=?", (row["decision_id"],)).fetchone()
        if not decision:
            return False, "inactive", f"decision {row['decision_id']} has no value"
        value = json.loads(decision[0]) if decision[0] is not None else None
        condition = json.loads(row["condition_json"] or "{}")
        matched = _decision_matches(value, condition)
        return matched, "inactive", f"decision {row['decision_id']} condition is false"
    return False, "blocked_dependency", f"unknown dependency type {kind}"


def evaluate_dependencies(conn: sqlite3.Connection, task_id: str) -> tuple[bool, list[dict[str, object]]]:
    explanations: list[dict[str, object]] = []
    satisfied = True
    for row in conn.execute("SELECT * FROM task_dependencies WHERE task_id=? ORDER BY id", (task_id,)):
        ok, blocked_state, reason = dependency_status(conn, row)
        explanations.append({"type": row["type"], "satisfied": ok, "blocked_state": blocked_state, "reason": reason})
        satisfied = satisfied and ok
    for row in conn.execute(
        "SELECT i.id,i.state,i.version,ic.required_state,ic.required_version "
        "FROM interface_consumers ic JOIN interfaces i ON i.id=ic.interface_id "
        "WHERE ic.task_id=? ORDER BY i.id",
        (task_id,),
    ):
        ok = row["state"] == row["required_state"] and (not row["required_version"] or row["version"] == row["required_version"])
        explanations.append(
            {
                "type": "consumed_interface",
                "satisfied": ok,
                "blocked_state": "blocked_dependency",
                "reason": f"interface {row['id']} must be {row['required_state']} at version {row['required_version'] or 'any'}",
            }
        )
        satisfied = satisfied and ok
    return satisfied, explanations


def barrier_requirement_satisfied(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[bool, str]:
    kind = row["type"]
    required = row["required_state"]
    if kind in {"task", "validation_task"}:
        entity = conn.execute("SELECT status,result FROM tasks WHERE id=?", (row["entity_id"],)).fetchone()
        allowed = json.loads(row["dispositions_json"] or "[]")
        ok = bool(entity and entity["status"] == required and (not allowed or entity["result"] in allowed))
    elif kind == "checkpoint":
        entity = conn.execute("SELECT state FROM checkpoints WHERE id=?", (row["entity_id"],)).fetchone()
        ok = bool(entity and entity[0] == required)
    elif kind == "interface":
        entity = conn.execute("SELECT state FROM interfaces WHERE id=?", (row["entity_id"],)).fetchone()
        ok = bool(entity and entity[0] == required)
    elif kind == "gate":
        entity = conn.execute("SELECT status,valid FROM gates WHERE id=?", (row["entity_id"],)).fetchone()
        ok = bool(entity and entity[0] == required and entity[1])
    elif kind == "rendezvous":
        entity = conn.execute("SELECT state FROM workflow_rendezvous WHERE id=?", (row["entity_id"],)).fetchone()
        ok = bool(entity and entity[0] == required)
    else:
        ok = False
    return ok, f"{kind} {row['entity_id']} must be {required}"


def reevaluate_barriers(conn: sqlite3.Connection, revision: int) -> list[dict[str, object]]:
    changes: list[dict[str, object]] = []
    for barrier in conn.execute("SELECT * FROM barriers ORDER BY id").fetchall():
        requirements = conn.execute("SELECT * FROM barrier_requirements WHERE barrier_id=? ORDER BY id", (barrier["id"],)).fetchall()
        results = [barrier_requirement_satisfied(conn, row) for row in requirements]
        count = sum(1 for ok, _ in results if ok)
        target = len(results) if barrier["mode"] == "all" else int(barrier["quorum"] or len(results))
        opened = bool(requirements) and count >= target
        new_state = "open" if opened else "closed"
        explanation = f"{count}/{len(results)} requirements satisfied; target={target}"
        if new_state != barrier["state"] or explanation != barrier["explanation"]:
            conn.execute(
                "UPDATE barriers SET state=?,explanation=?,opened_at=CASE WHEN ?='open' THEN COALESCE(opened_at,datetime('now')) ELSE NULL END,revision=? WHERE id=?",
                (new_state, explanation, new_state, revision, barrier["id"]),
            )
            affected: list[str] = []
            if barrier["state"] == "open" and new_state == "closed":
                for row in conn.execute("SELECT task_id FROM task_dependencies WHERE type='barrier' AND barrier_id=?", (barrier["id"],)):
                    if conn.execute("SELECT 1 FROM claims WHERE task_id=? AND state='active'", (row["task_id"],)).fetchone():
                        affected.append(row["task_id"])
                        conn.execute(
                            "UPDATE tasks SET status='attention_required',attention_reason=?,updated_at=?,revision=? WHERE id=?",
                            (f"barrier {barrier['id']} closed after claim", utc_now(), revision, row["task_id"]),
                        )
            changes.append({"barrier_id": barrier["id"], "state": new_state, "explanation": explanation, "affected_active_tasks": affected})
    return changes


def downstream_unlock_value(conn: sqlite3.Connection, task_id: str) -> int:
    direct = conn.execute("SELECT COUNT(*) FROM task_dependencies WHERE prerequisite_task_id=?", (task_id,)).fetchone()[0]
    checkpoints = conn.execute(
        "SELECT COUNT(*) FROM task_dependencies WHERE checkpoint_id IN (SELECT id FROM checkpoints WHERE task_id=?)",
        (task_id,),
    ).fetchone()[0]
    return int(direct) + int(checkpoints)
