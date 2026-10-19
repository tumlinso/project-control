"""Interface contract hashing, freeze, revision, and consumer invalidation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .config import utc_now
from .git_state import canonical_relative, file_hash
from .models import TodoError


def interface_hash(repo_root: Path, paths: list[str]) -> tuple[str, list[dict[str, str]]]:
    records: list[dict[str, str]] = []
    for value in sorted(paths):
        relative = canonical_relative(repo_root, value)
        target = repo_root / relative
        if not target.is_file():
            raise TodoError("interface_artifact_missing", f"Interface contract file does not exist: {relative}")
        records.append({"path": relative, "sha256": file_hash(target)})
    digest = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    return digest, records


def status(conn: sqlite3.Connection, interface_id: str) -> dict[str, object]:
    row = conn.execute("SELECT * FROM interfaces WHERE id=?", (interface_id,)).fetchone()
    if not row:
        raise TodoError("interface_not_found", f"Unknown interface {interface_id}")
    consumers = [row[0] for row in conn.execute("SELECT task_id FROM interface_consumers WHERE interface_id=? ORDER BY task_id", (interface_id,))]
    return {**dict(row), "contract_paths": json.loads(row["contract_paths_json"]), "consumers": consumers}


def freeze(conn: sqlite3.Connection, repo_root: Path, interface_id: str, version: str | None, revision: int) -> dict[str, object]:
    report = status(conn, interface_id)
    digest, records = interface_hash(repo_root, report["contract_paths"])
    now = utc_now()
    effective_version = version or str(int(report["version"] or "0") + 1 if str(report["version"] or "0").isdigit() else report["version"])
    conn.execute(
        "UPDATE interfaces SET state='frozen',version=?,content_hash=?,frozen_at=?,revision=? WHERE id=?",
        (effective_version, digest, now, revision, interface_id),
    )
    return {"interface_id": interface_id, "state": "frozen", "version": effective_version, "content_hash": digest, "contracts": records}


def revise(conn: sqlite3.Connection, repo_root: Path, interface_id: str, version: str | None, revision: int) -> dict[str, object]:
    report = status(conn, interface_id)
    digest, records = interface_hash(repo_root, report["contract_paths"])
    now = utc_now()
    effective_version = version or str(int(report["version"] or "0") + 1 if str(report["version"] or "0").isdigit() else f"{report['version']}.1")
    conn.execute(
        "UPDATE interfaces SET state='revised',version=?,content_hash=?,revised_at=?,revision=? WHERE id=?",
        (effective_version, digest, now, revision, interface_id),
    )
    affected: list[str] = []
    consumers = {row[0] for row in conn.execute("SELECT task_id FROM interface_consumers WHERE interface_id=?", (interface_id,))}
    consumers.update(row[0] for row in conn.execute("SELECT task_id FROM task_dependencies WHERE type='interface' AND interface_id=?", (interface_id,)))
    for task_id in sorted(consumers):
        if conn.execute("SELECT 1 FROM claims WHERE task_id=? AND state='active'", (task_id,)).fetchone():
            affected.append(task_id)
            conn.execute(
                "UPDATE tasks SET status='attention_required',attention_reason=?,updated_at=?,revision=? WHERE id=?",
                (f"consumed interface {interface_id} revised to {effective_version}", now, revision, task_id),
            )
    return {"interface_id": interface_id, "state": "revised", "version": effective_version, "content_hash": digest, "contracts": records, "affected_active_consumers": affected}
