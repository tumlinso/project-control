"""Durable JSON snapshots and atomic human-readable projections."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, SNAPSHOT_VERSION, WORKFLOW_SNAPSHOT_VERSION
from .config import utc_now

MANAGED_START = "<!-- todo-orchestrator:v2-managed:start -->"
MANAGED_END = "<!-- todo-orchestrator:v2-managed:end -->"

DURABLE_TABLES = [
    "project_amendments",
    "project_fragment_invalidations",
    "tasks",
    "decisions",
    "task_dependencies",
    "checkpoints",
    "barriers",
    "barrier_requirements",
    "interfaces",
    "interface_consumers",
    "checkpoint_interfaces",
    "invariants",
    "task_invariants",
    "ownership_scopes",
    "task_artifacts",
    "named_locks",
    "task_locks",
    "resource_classes",
    "resource_instances",
    "resource_requests",
    "gates",
    "checkpoint_gates",
    "evidence",
    "task_completion_gates",
    "project_declarations",
    "handoffs",
    "live_recovery_audit",
    "migration_warnings",
    "events",
    "workflow_runs",
    "workflow_run_charters",
    "workflow_lanes",
    "workflow_lane_tasks",
    "workflow_messages",
    "workflow_message_recipients",
    "workflow_message_receipts",
    "workflow_rendezvous",
    "workflow_rendezvous_participants",
    "workflow_rendezvous_arrivals",
    "workflow_context_fragments",
    "workflow_workspaces",
    "workflow_patch_artifacts",
    "workflow_integration_queue",
    "workflow_recovery_audit",
]


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except (AttributeError, OSError):
            pass
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, value: object) -> None:
    _atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def atomic_write_text(path: Path, value: str) -> None:
    _atomic_write(path, value.encode("utf-8"))


def atomic_write_text_if_changed(path: Path, value: str) -> bool:
    encoded = value.encode("utf-8")
    try:
        if path.read_bytes() == encoded:
            return False
    except OSError:
        pass
    _atomic_write(path, encoded)
    return True


@contextmanager
def _projection_lock(paths):
    """Serialize complete projection refreshes across processes.

    Atomic replacement prevents partial files; this lock additionally prevents
    an older post-commit writer from overwriting a newer projection.
    """
    lock_path = paths.state_dir / "projection.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except ImportError:  # atomic replacement remains safe on non-POSIX hosts
            pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except ImportError:
            pass
        os.close(descriptor)


def _table_rows(conn, table: str) -> list[dict[str, object]]:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if not exists:
        return []
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    primary = [row["name"] for row in sorted((row for row in info if row["pk"]), key=lambda row: row["pk"])]
    order = ",".join(primary) if primary else "rowid"
    rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]
    if table in {"evidence", "handoffs"}:
        for row in rows:
            row["claim_id"] = None
    if table == "events":
        for row in rows:
            row["actor_session_id"] = None
    return rows


def build_snapshot(conn, project: dict[str, object]) -> dict[str, object]:
    revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
    event = conn.execute("SELECT timestamp FROM events WHERE revision=?", (revision,)).fetchone()
    from .project_amendments import read_context
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_version": SNAPSHOT_VERSION,
        "workflow_snapshot_version": WORKFLOW_SNAPSHOT_VERSION,
        "project": project,
        "project_revision": revision,
        "generated_at": event[0] if event else project.get("created_at"),
        "tables": {table: _table_rows(conn, table) for table in DURABLE_TABLES},
        "semantic_context": read_context(conn, project, optional=True),
    }


def write_snapshot(db, paths, project: dict[str, object]) -> int:
    with db.read() as conn:
        snapshot = build_snapshot(conn, project)
    atomic_write_json(paths.snapshot_file, snapshot)
    return int(snapshot["project_revision"])


def restore_snapshot(db, paths, project: dict[str, object]) -> bool:
    if not paths.snapshot_file.exists():
        return False
    snapshot = json.loads(paths.snapshot_file.read_text(encoding="utf-8"))
    if int(snapshot.get("schema_version", 0)) != SCHEMA_VERSION:
        raise ValueError("snapshot schema version is incompatible")
    if int(snapshot.get("snapshot_version", SNAPSHOT_VERSION)) > SNAPSHOT_VERSION:
        raise ValueError("snapshot format version is newer than this runtime")
    tables = snapshot.get("tables", {})

    snapshot_revision = int(snapshot.get("project_revision", 0))
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in DURABLE_TABLES:
            rows = tables.get(table, [])
            if not rows:
                continue
            columns = list(rows[0])
            placeholders = ",".join("?" for _ in columns)
            sql = f"INSERT OR REPLACE INTO {table}({','.join(columns)}) VALUES({placeholders})"
            conn.executemany(sql, [[row.get(column) for column in columns] for row in rows])
        revision = snapshot_revision + 1
        conn.execute("UPDATE meta SET value=? WHERE key='project_revision'", (str(revision),))
        conn.execute(
            "INSERT INTO events(revision,timestamp,actor_session_id,entity_type,entity_id,event_type,payload_json) VALUES(?,?,?,?,?,?,?)",
            (revision, utc_now(), None, "project", str(project["project_uuid"]), "snapshot.restored", json.dumps({"snapshot_revision": snapshot_revision}, sort_keys=True)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return True


def replace_managed(existing: str, managed: str) -> str:
    block = f"{MANAGED_START}\n{managed.rstrip()}\n{MANAGED_END}\n"
    if MANAGED_START in existing and MANAGED_END in existing:
        prefix, remainder = existing.split(MANAGED_START, 1)
        _, suffix = remainder.split(MANAGED_END, 1)
        return prefix.rstrip() + "\n\n" + block + suffix.lstrip("\n")
    if existing.strip():
        return existing.rstrip() + "\n\n" + block
    return block


def _execution(conn, task_id: str, status: str, kind: str = "task") -> str:
    if kind == "epic":
        return "closed" if status in {"done", "superseded", "cancelled", "stale"} else "inactive"
    if status in {"done", "superseded", "cancelled", "stale"}:
        return "closed"
    if status == "attention_required":
        return "attention_required"
    claim = conn.execute("SELECT state FROM claims WHERE task_id=? AND state IN ('active','orphaned') ORDER BY created_at DESC LIMIT 1", (task_id,)).fetchone()
    if claim:
        return "claimed" if claim[0] == "active" else "orphaned"
    if status == "blocked":
        return "blocked_dependency"
    return "ready" if status == "planned" else "idle"


def project_markdown(conn, revision: int, task_ids: set[str] | None = None) -> tuple[str, str, dict[str, str]]:
    tasks = conn.execute("SELECT * FROM tasks ORDER BY priority DESC,id").fetchall()
    root_lines = ["# Todo Orchestrator v2 Projection", "", f"Project revision: `{revision}`", "", "## Workstreams"]
    status_lines = ["# Todo Status v2 Projection", "", f"Project revision: `{revision}`", "", "## Workstreams"]
    per_task: dict[str, str] = {}
    if not tasks:
        root_lines.append("_No tracked v2 tasks._")
        status_lines.append("_No tracked v2 tasks._")
    for task in tasks:
        execution = _execution(conn, task["id"], task["status"], task["kind"])
        root_lines.append(
            f"- `{task['id']}` | kind: {task['kind']} | status: {task['status']} | parent: {task['parent_id'] or '-'} | objective: {task['objective']}"
        )
        status_lines.append(
            f"- `{task['id']}` | status: {task['status']} | execution: {execution} | next: {task['next_action'] or task['objective']}"
        )
        if task_ids is not None and task["id"] not in task_ids:
            continue
        scopes = conn.execute("SELECT mode,path FROM ownership_scopes WHERE task_id=? ORDER BY mode,path", (task["id"],)).fetchall()
        dependencies = conn.execute("SELECT * FROM task_dependencies WHERE task_id=? ORDER BY id", (task["id"],)).fetchall()
        lines = [
            f"# {task['id']}: {task['title']}",
            "",
            f"Task revision: `{task['revision']}`; current project revision is in `todo-status.md`.",
            "",
            "## Objective",
            task["objective"] or "_None._",
            "",
            "## State",
            f"- Lifecycle: `{task['status']}`",
            f"- Execution: `{execution}`",
            f"- Parallel policy: `{task['parallel_policy']}`",
            f"- Result: `{task['result'] or '-'}`",
            "",
            "## Next Action",
            task["next_action"] or "_None._",
            "",
            "## Ownership",
        ]
        lines.extend(f"- `{row['mode']}`: `{row['path']}`" for row in scopes)
        if not scopes:
            lines.append("_No structured ownership._")
        lines.extend(["", "## Dependencies"])
        lines.extend(
            f"- `{row['type']}`: `{row['prerequisite_task_id'] or row['checkpoint_id'] or row['interface_id'] or row['barrier_id'] or row['decision_id']}`"
            for row in dependencies
        )
        if not dependencies:
            lines.append("_None._")
        per_task[task["id"]] = "\n".join(lines) + "\n"
    return "\n".join(root_lines) + "\n", "\n".join(status_lines) + "\n", per_task


def write_markdown_projections(db, paths, task_ids: set[str] | None = None) -> int:
    with db.read() as conn:
        revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
        root, status, tasks = project_markdown(conn, revision, task_ids)
    targets = [(paths.repo_root / "todos.md", root), (paths.repo_root / "todo-status.md", status)]
    for task_id, body in tasks.items():
        targets.append((paths.repo_root / "todos" / f"{task_id.lower()}.md", body))
    for path, managed in targets:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        projected = replace_managed(existing, managed)
        # Unchanged task files avoid atomic churn. Root/status still change when
        # global execution state changes; task files change with their entity.
        atomic_write_text_if_changed(path, projected)
    return revision


def refresh_projections(db, paths, project: dict[str, object], task_ids: set[str] | None = None) -> dict[str, object]:
    result: dict[str, object] = {"snapshot": False, "markdown": False, "error": None}
    try:
        with _projection_lock(paths):
            # Read each projection after acquiring the lock so every late writer
            # observes at least the newest already-committed database revision.
            snapshot_revision = write_snapshot(db, paths, project)
            markdown_revision = write_markdown_projections(db, paths, task_ids)
            result.update(snapshot=True, markdown=True, revision=min(snapshot_revision, markdown_revision))
            conn = db.connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO projection_status(name,revision,generated_at,error) VALUES('all',?,?,NULL)",
                    (markdown_revision, utc_now()),
                )
            finally:
                conn.close()
    except Exception as exc:  # committed semantic state remains authoritative
        result["error"] = str(exc)
        try:
            conn = db.connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO projection_status(name,revision,generated_at,error) VALUES('all',?,?,?)",
                    (0, utc_now(), str(exc)),
                )
            finally:
                conn.close()
        except Exception:
            pass
    return result
