"""Append-only event querying and relevance filtering."""

from __future__ import annotations

import json
import sqlite3


def changes_since(conn: sqlite3.Connection, revision: int, task_id: str | None = None) -> list[dict[str, object]]:
    params: list[object] = [revision]
    sql = "SELECT * FROM events WHERE revision>?"
    if task_id:
        related = {task_id}
        current = task_id
        while current:
            row = conn.execute("SELECT parent_id FROM tasks WHERE id=?", (current,)).fetchone()
            current = row[0] if row else None
            if current:
                related.add(current)
        dependencies = conn.execute(
            "SELECT prerequisite_task_id,checkpoint_id,interface_id,barrier_id FROM task_dependencies WHERE task_id=?",
            (task_id,),
        ).fetchall()
        entity_ids = set(related)
        for row in dependencies:
            entity_ids.update(value for value in row if value)
        placeholders = ",".join("?" for _ in entity_ids)
        sql += f" AND (entity_id IN ({placeholders}) OR entity_type='project')"
        params.extend(sorted(entity_ids))
    sql += " ORDER BY revision"
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "sequence": row["seq"],
            "revision": row["revision"],
            "timestamp": row["timestamp"],
            "actor_session_id": row["actor_session_id"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload_json"]),
        }
        for row in rows
    ]
