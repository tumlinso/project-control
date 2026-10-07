"""Durable first-class workflow runs and immutable run-charter revisions."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..config import utc_now
from ..models import TodoError
from .foundation import NEXT_TASK_BUDGET_BYTES, WorkflowDatabase, content_hash, require_bounded_payload


RUN_STATES = frozenset({"active", "attention_required", "completed", "cancelled"})


def _run_row(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise TodoError("workflow_run_missing", f"Workflow run {run_id} does not exist")
    return row


def create_run_in_transaction(
    conn: sqlite3.Connection,
    revision: int,
    *,
    run_id: str,
    charter: dict[str, Any],
    root_task_id: str | None = None,
) -> dict[str, object]:
    if not run_id:
        raise TodoError("invalid_workflow_run", "Run id must be non-empty")
    require_bounded_payload(charter, limit=NEXT_TASK_BUDGET_BYTES, code="run_charter_too_large")
    if root_task_id and not conn.execute("SELECT 1 FROM tasks WHERE id=?", (root_task_id,)).fetchone():
        raise TodoError("workflow_root_task_missing", f"Root task {root_task_id} does not exist")
    encoded = json.dumps(charter, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = content_hash(charter)
    existing = conn.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
    if existing:
        charter_row = conn.execute(
            "SELECT content_hash FROM workflow_run_charters WHERE run_id=? AND version=?",
            (run_id, existing["active_charter_version"]),
        ).fetchone()
        if existing["root_task_id"] == root_task_id and charter_row and charter_row["content_hash"] == digest:
            return {"run_id": run_id, "charter_version": int(existing["active_charter_version"]), "created": False}
        raise TodoError("workflow_run_exists", f"Workflow run {run_id} already exists with different content")
    now = utc_now()
    conn.execute(
        "INSERT INTO workflow_runs(id,root_task_id,status,active_charter_version,created_at,updated_at,revision) "
        "VALUES(?,?, 'active',1,?,?,?)",
        (run_id, root_task_id, now, now, revision),
    )
    conn.execute(
        "INSERT INTO workflow_run_charters(run_id,version,content_json,content_hash,creation_revision,created_at) "
        "VALUES(?,1,?,?,?,?)",
        (run_id, encoded, digest, revision, now),
    )
    return {"run_id": run_id, "charter_version": 1, "charter_hash": digest, "created": True}


def revise_charter_in_transaction(
    conn: sqlite3.Connection,
    revision: int,
    *,
    run_id: str,
    charter: dict[str, Any],
) -> dict[str, object]:
    run = _run_row(conn, run_id)
    if run["status"] != "active":
        raise TodoError("workflow_run_inactive", f"Workflow run {run_id} is {run['status']}")
    require_bounded_payload(charter, limit=NEXT_TASK_BUDGET_BYTES, code="run_charter_too_large")
    encoded = json.dumps(charter, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = content_hash(charter)
    active = conn.execute(
        "SELECT * FROM workflow_run_charters WHERE run_id=? AND version=?",
        (run_id, run["active_charter_version"]),
    ).fetchone()
    if active and active["content_hash"] == digest:
        return {"run_id": run_id, "charter_version": int(active["version"]), "charter_hash": digest, "changed": False}
    duplicate = conn.execute(
        "SELECT version FROM workflow_run_charters WHERE run_id=? AND content_hash=?",
        (run_id, digest),
    ).fetchone()
    if duplicate:
        raise TodoError("run_charter_reversion_forbidden", "A superseded charter hash cannot be republished as a new version")
    version = int(run["active_charter_version"]) + 1
    now = utc_now()
    conn.execute(
        "UPDATE workflow_run_charters SET superseded_at=?,superseded_revision=? WHERE run_id=? AND version=?",
        (now, revision, run_id, run["active_charter_version"]),
    )
    conn.execute(
        "INSERT INTO workflow_run_charters(run_id,version,content_json,content_hash,creation_revision,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (run_id, version, encoded, digest, revision, now),
    )
    conn.execute(
        "UPDATE workflow_runs SET active_charter_version=?,updated_at=?,revision=? WHERE id=?",
        (version, now, revision, run_id),
    )
    return {"run_id": run_id, "charter_version": version, "charter_hash": digest, "changed": True}


class RunService:
    """Transactional run operations over the existing todo Database."""

    def __init__(self, database: WorkflowDatabase):
        self.database = database

    def create(self, *, run_id: str, charter: dict[str, Any], root_task_id: str | None = None, actor_session_id: str | None = None) -> dict[str, object]:
        result, revision = self.database.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_run",
            entity_id=run_id,
            event_type="workflow.run.created",
            payload=lambda value: {"run_id": run_id, "charter_version": value["charter_version"], "created": value["created"]},
            operation=lambda conn, rev: create_run_in_transaction(conn, rev, run_id=run_id, charter=charter, root_task_id=root_task_id),
        )
        return {**result, "project_revision": revision}

    def revise_charter(self, *, run_id: str, charter: dict[str, Any], actor_session_id: str | None = None) -> dict[str, object]:
        result, revision = self.database.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_run",
            entity_id=run_id,
            event_type="workflow.run_charter.revised",
            payload=lambda value: {"run_id": run_id, "charter_version": value["charter_version"], "changed": value["changed"]},
            operation=lambda conn, rev: revise_charter_in_transaction(conn, rev, run_id=run_id, charter=charter),
        )
        return {**result, "project_revision": revision}

    def inspect(self, run_id: str) -> dict[str, object]:
        with self.database.read() as conn:
            run = _run_row(conn, run_id)
            charter = conn.execute(
                "SELECT * FROM workflow_run_charters WHERE run_id=? AND version=?",
                (run_id, run["active_charter_version"]),
            ).fetchone()
            lanes = [dict(row) for row in conn.execute("SELECT * FROM workflow_lanes WHERE run_id=? ORDER BY parent_lane_id,id", (run_id,))]
        return {"run": dict(run), "charter": {**dict(charter), "content": json.loads(charter["content_json"])}, "lanes": lanes}
