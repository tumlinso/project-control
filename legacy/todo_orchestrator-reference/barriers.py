"""Barrier status and explanations."""

from __future__ import annotations

import sqlite3

from .graph import barrier_requirement_satisfied
from .models import TodoError


def barrier_report(conn: sqlite3.Connection, barrier_id: str) -> dict[str, object]:
    barrier = conn.execute("SELECT * FROM barriers WHERE id=?", (barrier_id,)).fetchone()
    if not barrier:
        raise TodoError("barrier_not_found", f"Unknown barrier {barrier_id}")
    requirements = []
    for row in conn.execute("SELECT * FROM barrier_requirements WHERE barrier_id=? ORDER BY id", (barrier_id,)):
        ok, reason = barrier_requirement_satisfied(conn, row)
        requirements.append({"type": row["type"], "entity_id": row["entity_id"], "required_state": row["required_state"], "satisfied": ok, "reason": reason})
    return {**dict(barrier), "requirements": requirements}
