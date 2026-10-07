"""Checkpoint transitions and required-gate enforcement."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import utc_now
from .graph import reevaluate_barriers
from .models import ExitCode, TodoError
from .interfaces import freeze as freeze_interface


def checkpoint_status(conn: sqlite3.Connection, checkpoint_id: str) -> dict[str, object]:
    row = conn.execute("SELECT * FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
    if not row:
        raise TodoError("checkpoint_not_found", f"Unknown checkpoint {checkpoint_id}")
    gates = [dict(item) for item in conn.execute(
        "SELECT g.id,g.status,g.valid,g.required FROM checkpoint_gates cg JOIN gates g ON g.id=cg.gate_id WHERE cg.checkpoint_id=? ORDER BY g.id",
        (checkpoint_id,),
    )]
    return {**dict(row), "gates": gates}


def reach_checkpoint(conn: sqlite3.Connection, repo_root: Path, checkpoint_id: str, revision: int) -> dict[str, object]:
    report = checkpoint_status(conn, checkpoint_id)
    failed = [gate for gate in report["gates"] if gate["required"] and (not gate["valid"] or gate["status"] not in {"passed", "evaluated_not_promoted"})]
    if failed:
        raise TodoError("checkpoint_gates_unsatisfied", f"Checkpoint {checkpoint_id} has unsatisfied required gates", ExitCode.GATE_FAILURE, failed)
    now = utc_now()
    conn.execute("UPDATE checkpoints SET state='reached',reached_at=?,revoked_at=NULL,revision=? WHERE id=?", (now, revision, checkpoint_id))
    published = []
    for row in conn.execute("SELECT interface_id,version FROM checkpoint_interfaces WHERE checkpoint_id=?", (checkpoint_id,)):
        published.append(freeze_interface(conn, repo_root, row["interface_id"], row["version"], revision))
    barrier_changes = reevaluate_barriers(conn, revision)
    return {"checkpoint_id": checkpoint_id, "state": "reached", "reached_at": now, "published_interfaces": published, "barrier_changes": barrier_changes}


def revoke_checkpoint(conn: sqlite3.Connection, checkpoint_id: str, revision: int) -> dict[str, object]:
    checkpoint_status(conn, checkpoint_id)
    now = utc_now()
    conn.execute("UPDATE checkpoints SET state='revoked',revoked_at=?,revision=? WHERE id=?", (now, revision, checkpoint_id))
    affected = [row[0] for row in conn.execute("SELECT task_id FROM task_dependencies WHERE type='checkpoint' AND checkpoint_id=?", (checkpoint_id,))]
    for task_id in affected:
        active = conn.execute("SELECT 1 FROM claims WHERE task_id=? AND state='active'", (task_id,)).fetchone()
        if active:
            conn.execute(
                "UPDATE tasks SET status='attention_required',attention_reason=?,updated_at=?,revision=? WHERE id=?",
                (f"checkpoint {checkpoint_id} was revoked", now, revision, task_id),
            )
    barrier_changes = reevaluate_barriers(conn, revision)
    return {"checkpoint_id": checkpoint_id, "state": "revoked", "affected_active_tasks": affected, "barrier_changes": barrier_changes}
