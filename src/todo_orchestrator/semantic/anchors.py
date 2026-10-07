"""Resolve semantic todo entities to historical revisions."""

from __future__ import annotations

import json

from ..models import TodoError


def _event(conn, *, entity_id: str | None = None, event_types: tuple[str, ...] = (), revision: int | None = None):
    if revision is not None:
        return conn.execute("SELECT * FROM events WHERE revision=?", (revision,)).fetchone()
    placeholders = ",".join("?" for _ in event_types)
    return conn.execute(
        f"SELECT * FROM events WHERE entity_id=? AND event_type IN ({placeholders}) ORDER BY revision LIMIT 1",
        (entity_id, *event_types),
    ).fetchone()


def _baseline_heads(conn, task_id: str | None, up_to_revision: int) -> list[str]:
    if not task_id:
        return []
    return sorted({
        str(row[0]) for row in conn.execute(
            "SELECT baseline_head FROM claims WHERE task_id=? AND baseline_head IS NOT NULL AND baseline_revision<=?",
            (task_id, up_to_revision),
        ) if row[0]
    })


def resolve_anchor(
    conn, *, task_id: str | None = None, checkpoint_id: str | None = None,
    interface_id: str | None = None, revision: int | None = None, phase: str = "created",
) -> dict[str, object]:
    selectors = [task_id is not None, checkpoint_id is not None, interface_id is not None, revision is not None]
    if sum(selectors) != 1:
        raise TodoError("semantic_anchor_selector_required", "Select exactly one task, checkpoint, interface, or revision")
    row = None
    entity: dict[str, object]
    reason = ""
    confidence = "high"
    anchor_task: str | None = None
    if revision is not None:
        row = _event(conn, revision=revision)
        if row is None:
            raise TodoError("semantic_anchor_not_found", f"No todo event exists at revision {revision}")
        entity = {"type": row["entity_type"], "id": row["entity_id"], "phase": "revision"}
        reason = "exact_event_revision"
    elif task_id is not None:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if task is None:
            raise TodoError("semantic_anchor_not_found", f"Unknown task {task_id}")
        anchor_task = task_id
        mapping = {
            "created": ("task.created", "plan.applied", "migration.markdown_applied"),
            "first_claim": ("claim.started", "continue.completed"),
            "completed": ("task.completed",),
        }
        if phase not in mapping:
            raise TodoError("semantic_anchor_phase_invalid", f"Unsupported task phase {phase}")
        row = _event(conn, entity_id=task_id, event_types=mapping[phase])
        if row is None and phase == "created":
            row = conn.execute(
                "SELECT * FROM events WHERE event_type IN ('plan.applied','migration.markdown_applied') "
                "AND timestamp>=? ORDER BY revision LIMIT 1",
                (task["created_at"],),
            ).fetchone()
            if row is not None:
                confidence = "medium"
                reason = "task_creation_timestamp_matched_to_project_event"
            else:
                row = conn.execute("SELECT * FROM events WHERE revision=?", (task["revision"],)).fetchone()
                confidence = "low"
                reason = "task_row_revision_best_available"
        elif row is None and phase == "first_claim":
            claim = conn.execute("SELECT * FROM claims WHERE task_id=? ORDER BY created_at LIMIT 1", (task_id,)).fetchone()
            if claim:
                row = conn.execute("SELECT * FROM events WHERE revision=?", (claim["baseline_revision"],)).fetchone()
                confidence = "medium"
                reason = "claim_baseline_revision_best_available"
        if row is None:
            raise TodoError("semantic_anchor_not_found", f"No {phase} anchor is recorded for task {task_id}")
        entity = {"type": "task", "id": task_id, "phase": phase}
        reason = reason or f"exact_task_{phase}_event"
    elif checkpoint_id is not None:
        checkpoint = conn.execute("SELECT task_id FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
        if checkpoint is None:
            raise TodoError("semantic_anchor_not_found", f"Unknown checkpoint {checkpoint_id}")
        anchor_task = str(checkpoint["task_id"])
        row = _event(conn, entity_id=checkpoint_id, event_types=("checkpoint.reach", "checkpoint.reached", "checkpoint.revoke"))
        if row is None:
            raise TodoError("semantic_anchor_not_found", f"No transition anchor is recorded for checkpoint {checkpoint_id}")
        entity = {"type": "checkpoint", "id": checkpoint_id, "phase": row["event_type"].split(".")[-1]}
        reason = "exact_checkpoint_transition_event"
    else:
        interface = conn.execute("SELECT owner_task_id FROM interfaces WHERE id=?", (interface_id,)).fetchone()
        if interface is None:
            raise TodoError("semantic_anchor_not_found", f"Unknown interface {interface_id}")
        anchor_task = str(interface["owner_task_id"])
        row = _event(conn, entity_id=interface_id, event_types=("interface.freeze", "interface.revise"))
        if row is None:
            raise TodoError("semantic_anchor_not_found", f"No transition anchor is recorded for interface {interface_id}")
        entity = {"type": "interface", "id": interface_id, "phase": row["event_type"].split(".")[-1]}
        reason = "exact_interface_transition_event"

    resolved_revision = int(row["revision"])
    return {
        "todo_revision": resolved_revision,
        "timestamp": row["timestamp"],
        "entity": entity,
        "baseline_git_heads": _baseline_heads(conn, anchor_task, resolved_revision),
        "confidence": confidence,
        "reason": reason,
        "event": {"type": row["event_type"], "payload": json.loads(row["payload_json"] or "{}")},
    }
