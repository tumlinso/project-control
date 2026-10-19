"""Whole-interval coalescing of todo-native history."""

from __future__ import annotations

import json
from collections import defaultdict

from ..models import TodoError
from .anchors import resolve_anchor


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def semantic_delta(
    conn, *, since_revision: int | None = None, since_task: str | None = None,
    since_checkpoint: str | None = None, since_interface: str | None = None,
    until_revision: int | None = None, task_phase: str = "created",
) -> dict[str, object]:
    selectors = [since_revision is not None, since_task is not None, since_checkpoint is not None, since_interface is not None]
    if sum(selectors) != 1:
        raise TodoError("semantic_delta_anchor_required", "Select exactly one semantic delta anchor")
    anchor = None
    if since_task:
        anchor = resolve_anchor(conn, task_id=since_task, phase=task_phase)
    elif since_checkpoint:
        anchor = resolve_anchor(conn, checkpoint_id=since_checkpoint)
    elif since_interface:
        anchor = resolve_anchor(conn, interface_id=since_interface)
    start = int(anchor["todo_revision"] if anchor else since_revision)
    current = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
    end = current if until_revision is None else min(int(until_revision), current)
    if end < start:
        raise TodoError("semantic_delta_interval_invalid", "The until revision precedes the semantic anchor")
    rows = conn.execute("SELECT * FROM events WHERE revision>? AND revision<=? ORDER BY revision", (start, end)).fetchall()

    tasks = {"completed": [], "superseded": [], "blocked": [], "reopened": []}
    interfaces = {"frozen": [], "revised": []}
    checkpoints = {"reached": [], "revoked": []}
    decisions = {"resolved": [], "changed": []}
    validation: dict[str, dict[str, int | str]] = defaultdict(lambda: {"passed": 0, "failed": 0, "invalidated": 0})
    coordination = {"claims_started": 0, "claims_released": 0, "children_completed": 0}
    material_events: list[dict[str, object]] = []
    omitted_heartbeats = 0

    for row in rows:
        event_type = str(row["event_type"])
        entity_id = str(row["entity_id"] or "")
        payload = json.loads(row["payload_json"] or "{}")
        if event_type in {"claim.pulsed", "gate.heartbeat", "child.heartbeat"}:
            omitted_heartbeats += 1
            continue
        if event_type == "task.completed":
            disposition = str(payload.get("disposition", ""))
            tasks["superseded" if disposition == "superseded" else "completed"].append(entity_id)
        elif event_type in {"task.blocked", "task.handed_off"} and payload.get("status") == "blocked":
            tasks["blocked"].append(entity_id)
        elif event_type in {"task.reopened", "task.recovered"}:
            tasks["reopened"].append(entity_id)
        elif event_type == "interface.freeze":
            interfaces["frozen"].append(entity_id)
        elif event_type == "interface.revise":
            interfaces["revised"].append(entity_id)
        elif event_type in {"checkpoint.reach", "checkpoint.reached"}:
            checkpoints["reached"].append(entity_id)
        elif event_type == "checkpoint.revoke":
            checkpoints["revoked"].append(entity_id)
        elif event_type == "decision.set":
            prior = conn.execute(
                "SELECT 1 FROM events WHERE entity_id=? AND event_type='decision.set' AND revision<=? LIMIT 1",
                (entity_id, start),
            ).fetchone()
            decisions["changed" if prior else "resolved"].append(entity_id)
        elif event_type == "gate.completed":
            gate = conn.execute("SELECT task_id FROM gates WHERE id=?", (entity_id,)).fetchone()
            task_id = str(gate["task_id"]) if gate and gate["task_id"] else "unowned"
            if bool(payload.get("valid")):
                validation[task_id]["passed"] = int(validation[task_id]["passed"]) + 1
            else:
                validation[task_id]["failed"] = int(validation[task_id]["failed"]) + 1
        elif event_type in {"gate.invalidated", "gate.reconciled_invalid"}:
            gate = conn.execute("SELECT task_id FROM gates WHERE id=?", (entity_id,)).fetchone()
            task_id = str(gate["task_id"]) if gate and gate["task_id"] else "unowned"
            validation[task_id]["invalidated"] = int(validation[task_id]["invalidated"]) + 1
        elif event_type in {"claim.started", "continue.completed"} and entity_id:
            coordination["claims_started"] += 1
        elif event_type == "claim.released":
            coordination["claims_released"] += 1
        elif event_type in {"child.reported", "child.accepted", "child.completed"}:
            coordination["children_completed"] += 1
        if event_type not in {
            "claim.started", "continue.completed", "claim.released",
            "gate.started", "gate.completed", "gate.invalidated", "gate.reconciled_invalid",
            "child.reported", "child.accepted", "child.completed",
        }:
            material_events.append({
                "revision": int(row["revision"]), "timestamp": row["timestamp"],
                "entity_type": row["entity_type"], "entity_id": row["entity_id"], "event_type": event_type,
            })

    for section in (tasks, interfaces, checkpoints, decisions):
        for key, values in section.items():
            section[key] = _unique(values)
    validation_rows = [
        {"task_id": task_id, **counts} for task_id, counts in sorted(validation.items())
    ]
    coalesced_count = len(material_events) + sum(1 for count in coordination.values() if count)
    return {
        "interval": {"from_revision": start, "to_revision": end},
        "anchor": anchor,
        "tasks": tasks,
        "interfaces": interfaces,
        "checkpoints": checkpoints,
        "decisions": decisions,
        "validation_by_task": validation_rows,
        "coordination_summary": coordination,
        "material_events": material_events,
        "raw_event_count": len(rows),
        "coalesced_event_count": coalesced_count,
        "heartbeat_events_omitted": omitted_heartbeats,
    }
