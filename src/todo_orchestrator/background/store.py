"""WAL-backed private queue, leases, results, and resource reservations."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import JobState, RuntimePaths, canonical_json, digest


SCHEMA = """
CREATE TABLE IF NOT EXISTS background_watches(
 id TEXT PRIMARY KEY, project_root TEXT NOT NULL, state TEXT NOT NULL,
 spec_json TEXT NOT NULL, event_cursor INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS background_jobs(
 id TEXT PRIMARY KEY, watch_id TEXT, kind TEXT NOT NULL, state TEXT NOT NULL,
 priority INTEGER NOT NULL, argv_json TEXT NOT NULL, cwd TEXT NOT NULL,
 env_json TEXT NOT NULL, timeout_seconds REAL NOT NULL,
 resource_json TEXT NOT NULL, source_fingerprint TEXT,
 outputs_json TEXT NOT NULL, retry_limit INTEGER NOT NULL, retries_used INTEGER NOT NULL DEFAULT 0,
 dedup_key TEXT NOT NULL UNIQUE, task_id TEXT, todo_revision INTEGER,
 snapshot_json TEXT, not_before REAL NOT NULL DEFAULT 0,
 cancel_requested INTEGER NOT NULL DEFAULT 0, pid INTEGER, process_start TEXT,
 result_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 FOREIGN KEY(watch_id) REFERENCES background_watches(id)
);
CREATE INDEX IF NOT EXISTS idx_background_jobs_ready ON background_jobs(state,priority,not_before,created_at);
CREATE TABLE IF NOT EXISTS background_dependencies(
 job_id TEXT NOT NULL, depends_on_job_id TEXT NOT NULL,
 PRIMARY KEY(job_id,depends_on_job_id)
);
CREATE TABLE IF NOT EXISTS background_attempts(
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL, attempt_number INTEGER NOT NULL,
 state TEXT NOT NULL, worker_id TEXT, pid INTEGER, process_start TEXT,
 started_at REAL NOT NULL, heartbeat_at REAL NOT NULL, finished_at REAL,
 returncode INTEGER, reason TEXT, stdout_path TEXT, stderr_path TEXT,
 stdout_tail TEXT, stderr_tail TEXT, metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS background_workers(
 id TEXT PRIMARY KEY, pid INTEGER NOT NULL, process_start TEXT,
 state TEXT NOT NULL, started_at REAL NOT NULL, heartbeat_at REAL NOT NULL,
 stopped_at REAL
);
CREATE TABLE IF NOT EXISTS background_artifacts(
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL, attempt_id TEXT,
 kind TEXT NOT NULL, path TEXT NOT NULL, content_hash TEXT,
 complete INTEGER NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS background_results(
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL, status TEXT NOT NULL,
 classification TEXT, severity INTEGER NOT NULL DEFAULT 0,
 summary_json TEXT NOT NULL, valid INTEGER NOT NULL,
 contaminated INTEGER NOT NULL DEFAULT 0, parser_version TEXT,
 created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS background_resources(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, tags_json TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS background_reservations(
 resource_id TEXT NOT NULL, owner_id TEXT NOT NULL, owner_kind TEXT NOT NULL,
 state TEXT NOT NULL, acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL,
 PRIMARY KEY(resource_id,owner_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_background_resource_exclusive
 ON background_reservations(resource_id) WHERE state='active';
CREATE TABLE IF NOT EXISTS background_foreground_intents(
 id TEXT PRIMARY KEY, resources_json TEXT NOT NULL, state TEXT NOT NULL,
 created_at REAL NOT NULL, heartbeat_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS background_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""


def runtime_paths(project_root: str | Path) -> RuntimePaths:
    root = Path(project_root).resolve() / ".todo-orchestrator" / "runtime"
    return RuntimePaths(root=root, database=root / "background.sqlite3", artifacts=root / "background-artifacts", wake_lock=root / "worker-wake.lock")


def _process_start(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21]
    except (OSError, IndexError):
        return None


def _alive(pid: int | None, expected: str | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        actual = _process_start(pid)
        return not expected or not actual or expected == actual
    except OSError:
        return False


class BackgroundStore:
    def __init__(self, project_root: str | Path, *, create: bool = True, busy_timeout_ms: int = 2500):
        self.project_root = Path(project_root).resolve()
        self.paths = runtime_paths(self.project_root)
        self.busy_timeout_ms = busy_timeout_ms
        if create:
            self.initialize()

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            conn = sqlite3.connect(f"file:{self.paths.database}?mode=ro", uri=True, timeout=self.busy_timeout_ms / 1000)
        else:
            self.paths.root.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.paths.database, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        if not readonly:
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
        finally:
            conn.close()
        self.paths.artifacts.mkdir(parents=True, exist_ok=True)

    def _tx(self):
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        return conn

    def arm_watch(self, spec: dict[str, object], *, event_cursor: int = 0) -> str:
        now = time.time()
        project_root = str(Path(str(spec["project_root"])).resolve())
        watch_id = str(spec.get("watch_id") or digest({"project_root": project_root, "watch": spec.get("watch", {})})[:24])
        conn = self._tx()
        try:
            conn.execute(
                "INSERT INTO background_watches(id,project_root,state,spec_json,event_cursor,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET state='armed',spec_json=excluded.spec_json,updated_at=excluded.updated_at",
                (watch_id, project_root, "armed", canonical_json(spec), event_cursor, now, now),
            )
            conn.commit()
            return watch_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def watch(self, watch_id: str) -> dict[str, object] | None:
        with closing(self.connect(readonly=True)) as conn:
            row = conn.execute("SELECT * FROM background_watches WHERE id=?", (watch_id,)).fetchone()
        if not row:
            return None
        value = dict(row)
        value["spec"] = json.loads(value.pop("spec_json"))
        return value

    def watches(self, state: str = "armed") -> list[dict[str, object]]:
        with closing(self.connect(readonly=True)) as conn:
            rows = conn.execute("SELECT * FROM background_watches WHERE state=? ORDER BY created_at", (state,)).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["spec"] = json.loads(value.pop("spec_json"))
            result.append(value)
        return result

    def set_watch_state(self, state: str) -> int:
        conn = self._tx()
        try:
            count = conn.execute("UPDATE background_watches SET state=?,updated_at=? WHERE project_root=?", (state, time.time(), str(self.project_root))).rowcount
            conn.commit()
            return count
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def cancel_background(self) -> int:
        conn = self._tx()
        try:
            now = time.time()
            running = conn.execute("UPDATE background_jobs SET cancel_requested=1,updated_at=? WHERE state='running'", (now,)).rowcount
            conn.execute("UPDATE background_jobs SET state='canceled',updated_at=? WHERE state='queued'", (now,))
            conn.commit()
            return running
        finally:
            conn.close()

    def update_watch_cursor(self, watch_id: str, cursor: int) -> None:
        conn = self._tx()
        try:
            conn.execute("UPDATE background_watches SET event_cursor=MAX(event_cursor,?),updated_at=? WHERE id=?", (cursor, time.time(), watch_id))
            conn.commit()
        finally:
            conn.close()

    def enqueue(self, job: dict[str, object], dependencies: Iterable[str] = ()) -> tuple[str, bool]:
        now = time.time()
        argv = job.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            raise ValueError("background jobs require a non-empty structured argv list")
        dedup_key = str(job.get("dedup_key") or digest({
            "kind": job.get("kind"), "argv": argv, "cwd": job.get("cwd"),
            "source_fingerprint": job.get("source_fingerprint"), "resources": job.get("resources", {}),
        }))
        conn = self._tx()
        try:
            existing = conn.execute("SELECT id FROM background_jobs WHERE dedup_key=?", (dedup_key,)).fetchone()
            if existing:
                conn.commit()
                return str(existing[0]), False
            job_id = str(job.get("id") or uuid.uuid4())
            conn.execute(
                "INSERT INTO background_jobs(id,watch_id,kind,state,priority,argv_json,cwd,env_json,timeout_seconds,resource_json,source_fingerprint,outputs_json,retry_limit,dedup_key,task_id,todo_revision,snapshot_json,not_before,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, job.get("watch_id"), str(job.get("kind", "command")), JobState.QUEUED.value,
                 int(job.get("priority", 40)), canonical_json(argv), str(job.get("cwd") or self.project_root),
                 canonical_json(job.get("env", {})), float(job.get("timeout", 3600)), canonical_json(job.get("resources", {})),
                 job.get("source_fingerprint"), canonical_json(job.get("outputs", [])), int(job.get("retry_limit", 0)),
                 dedup_key, job.get("task_id"), job.get("todo_revision"), canonical_json(job.get("snapshot", {})),
                 float(job.get("not_before", 0)), now, now),
            )
            for dependency in dependencies:
                conn.execute("INSERT OR IGNORE INTO background_dependencies(job_id,depends_on_job_id) VALUES(?,?)", (job_id, dependency))
            conn.commit()
            return job_id, True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _decode_job(row: sqlite3.Row) -> dict[str, object]:
        value = dict(row)
        for source, target in (("argv_json", "argv"), ("env_json", "env"), ("resource_json", "resources"), ("outputs_json", "outputs"), ("snapshot_json", "snapshot")):
            value[target] = json.loads(value.pop(source) or "{}")
        return value

    def _sweep_stale_locked(self, conn: sqlite3.Connection, stale_seconds: float = 30.0) -> None:
        cutoff = time.time() - stale_seconds
        attempts = conn.execute(
            "SELECT a.*,j.cancel_requested,w.state AS watch_state FROM background_attempts a "
            "JOIN background_jobs j ON j.id=a.job_id "
            "LEFT JOIN background_watches w ON w.id=j.watch_id "
            "WHERE a.state='running' AND a.heartbeat_at<?", (cutoff,),
        ).fetchall()
        for attempt in attempts:
            if _alive(attempt["pid"], attempt["process_start"]):
                continue
            canceled = bool(attempt["cancel_requested"]) or attempt["watch_state"] == "stopped"
            attempt_state, job_state = ("canceled", "canceled") if canceled else ("preempted", "queued")
            conn.execute(
                "UPDATE background_attempts SET state=?,finished_at=?,reason=? WHERE id=?",
                (attempt_state, time.time(), "stale-worker-canceled" if canceled else "stale-worker", attempt["id"]),
            )
            conn.execute(
                "UPDATE background_jobs SET state=?,pid=NULL,process_start=NULL,cancel_requested=0,updated_at=? WHERE id=?",
                (job_state, time.time(), attempt["job_id"]),
            )
            conn.execute("UPDATE background_reservations SET state='released',heartbeat_at=? WHERE owner_id=? AND state='active'", (time.time(), attempt["job_id"]))
        workers = conn.execute("SELECT * FROM background_workers WHERE state='running' AND heartbeat_at<?", (cutoff,)).fetchall()
        for worker in workers:
            if not _alive(worker["pid"], worker["process_start"]):
                conn.execute("UPDATE background_workers SET state='stale',stopped_at=? WHERE id=?", (time.time(), worker["id"]))

    def claim(self, worker_id: str, *, defer_resources: bool = False) -> tuple[dict[str, object], str] | None:
        conn = self._tx()
        try:
            self._sweep_stale_locked(conn)
            conn.execute(
                "UPDATE background_jobs SET state='skipped',updated_at=? WHERE state='queued' AND EXISTS("
                "SELECT 1 FROM background_dependencies d JOIN background_jobs p ON p.id=d.depends_on_job_id "
                "WHERE d.job_id=background_jobs.id AND p.state IN ('failed','canceled','skipped'))",
                (time.time(),),
            )
            if conn.execute("SELECT 1 FROM background_foreground_intents WHERE state='active' LIMIT 1").fetchone():
                conn.commit()
                return None
            rows = conn.execute(
                "SELECT j.* FROM background_jobs j WHERE j.state='queued' AND j.not_before<=? "
                "AND NOT EXISTS(SELECT 1 FROM background_dependencies d JOIN background_jobs p ON p.id=d.depends_on_job_id WHERE d.job_id=j.id AND p.state!='succeeded') "
                "ORDER BY j.priority ASC,j.created_at ASC LIMIT 16", (time.time(),),
            ).fetchall()
            selected = None
            resources: list[str] = []
            for row in rows:
                request = json.loads(row["resource_json"] or "{}")
                resources = [] if defer_resources else self._select_resources_locked(conn, request)
                if defer_resources or resources or not request.get("count") and not request.get("ids"):
                    selected = row
                    break
            if selected is None:
                conn.commit()
                return None
            attempt_id = str(uuid.uuid4())
            now = time.time()
            number = conn.execute("SELECT COUNT(*) FROM background_attempts WHERE job_id=?", (selected["id"],)).fetchone()[0] + 1
            conn.execute("UPDATE background_jobs SET state='running',cancel_requested=0,updated_at=? WHERE id=?", (now, selected["id"]))
            conn.execute("INSERT INTO background_attempts(id,job_id,attempt_number,state,worker_id,started_at,heartbeat_at,metadata_json) VALUES(?,?,?,?,?,?,?,?)", (attempt_id, selected["id"], number, "running", worker_id, now, now, canonical_json({"resources": resources})))
            for resource_id in resources:
                conn.execute("INSERT INTO background_reservations(resource_id,owner_id,owner_kind,state,acquired_at,heartbeat_at) VALUES(?,?,?,'active',?,?)", (resource_id, selected["id"], "background", now, now))
            conn.commit()
            job = self._decode_job(selected)
            job["allocated_resources"] = resources
            return job, attempt_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def defer_job(self, job_id: str, delay_seconds: float = 0.5) -> None:
        conn = self._tx()
        try:
            conn.execute("UPDATE background_jobs SET not_before=MAX(not_before,?),updated_at=? WHERE id=? AND state='queued'",
                         (time.time() + max(0.0, delay_seconds), time.time(), job_id))
            conn.commit()
        finally:
            conn.close()

    def _select_resources_locked(self, conn: sqlite3.Connection, request: dict[str, object]) -> list[str]:
        ids = [str(item) for item in request.get("ids", [])]
        count = int(request.get("count", 0) or 0)
        kind = str(request.get("kind", "accelerator"))
        candidates = conn.execute("SELECT * FROM background_resources WHERE enabled=1 AND kind=? ORDER BY id", (kind,)).fetchall()
        required_tags = {str(key): str(value) for key, value in dict(request.get("tags", {})).items()}
        free = []
        for row in candidates:
            tags = {str(key): str(value) for key, value in json.loads(row["tags_json"]).items()}
            if any(tags.get(key) != value for key, value in required_tags.items()):
                continue
            if conn.execute("SELECT 1 FROM background_reservations WHERE resource_id=? AND state='active'", (row["id"],)).fetchone():
                continue
            free.append(row)
        if ids:
            found = [row["id"] for row in free if row["id"] in ids]
            return found if len(found) == len(ids) else []
        if count:
            return [row["id"] for row in free[:count]] if len(free) >= count else []
        return []

    def register_worker(self) -> str:
        worker_id = str(uuid.uuid4())
        now = time.time()
        conn = self._tx()
        try:
            conn.execute("INSERT INTO background_workers(id,pid,process_start,state,started_at,heartbeat_at) VALUES(?,?,?,'running',?,?)", (worker_id, os.getpid(), _process_start(os.getpid()), now, now))
            conn.commit()
            return worker_id
        finally:
            conn.close()

    def heartbeat(self, worker_id: str, job_id: str | None = None, attempt_id: str | None = None, pid: int | None = None) -> None:
        conn = self._tx()
        try:
            now = time.time()
            conn.execute("UPDATE background_workers SET heartbeat_at=? WHERE id=? AND state='running'", (now, worker_id))
            if job_id and attempt_id:
                start = _process_start(pid) if pid else None
                conn.execute("UPDATE background_attempts SET heartbeat_at=?,pid=COALESCE(?,pid),process_start=COALESCE(?,process_start) WHERE id=?", (now, pid, start, attempt_id))
                conn.execute("UPDATE background_jobs SET pid=COALESCE(?,pid),process_start=COALESCE(?,process_start),updated_at=? WHERE id=?", (pid, start, now, job_id))
                conn.execute("UPDATE background_reservations SET heartbeat_at=? WHERE owner_id=? AND state='active'", (now, job_id))
            conn.commit()
        finally:
            conn.close()

    def cancellation_requested(self, job_id: str) -> bool:
        with closing(self.connect(readonly=True)) as conn:
            row = conn.execute("SELECT cancel_requested FROM background_jobs WHERE id=?", (job_id,)).fetchone()
        return bool(row and row[0])

    def has_pending_jobs(self) -> bool:
        with closing(self.connect(readonly=True)) as conn:
            row = conn.execute("SELECT 1 FROM background_jobs WHERE state IN ('queued','running') LIMIT 1").fetchone()
        return row is not None

    def finish(self, job_id: str, attempt_id: str, *, state: str, returncode: int | None, reason: str,
               stdout_path: str, stderr_path: str, stdout_tail: str, stderr_tail: str,
               metadata: dict[str, object], result: dict[str, object] | None = None) -> str | None:
        conn = self._tx()
        try:
            now = time.time()
            job = conn.execute(
                "SELECT j.retries_used,j.retry_limit,j.cancel_requested,w.state AS watch_state "
                "FROM background_jobs j LEFT JOIN background_watches w ON w.id=j.watch_id WHERE j.id=?",
                (job_id,),
            ).fetchone()
            final_state = state
            retries = int(job["retries_used"])
            if state == JobState.PREEMPTED.value:
                final_state = (JobState.CANCELED.value if bool(job["cancel_requested"]) or
                               job["watch_state"] == "stopped" else JobState.QUEUED.value)
            elif state == JobState.FAILED.value and retries < int(job["retry_limit"]):
                retries += 1
                final_state = JobState.QUEUED.value
            result_id = None
            valid = state == JobState.SUCCEEDED.value and bool((result or {}).get("valid", True))
            contaminated = bool((result or {}).get("contaminated", False))
            if state != JobState.PREEMPTED.value:
                result_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO background_results(id,job_id,status,classification,severity,summary_json,valid,contaminated,parser_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (result_id, job_id, state, (result or {}).get("classification"), int((result or {}).get("severity", 0)), canonical_json(result or metadata), int(valid and not contaminated), int(contaminated), (result or {}).get("parser_version"), now),
                )
            conn.execute("UPDATE background_attempts SET state=?,finished_at=?,returncode=?,reason=?,stdout_path=?,stderr_path=?,stdout_tail=?,stderr_tail=?,metadata_json=? WHERE id=?",
                         (state, now, returncode, reason, stdout_path, stderr_path, stdout_tail, stderr_tail, canonical_json(metadata), attempt_id))
            defer_until = now + 2.0 if state == JobState.PREEMPTED.value and final_state == JobState.QUEUED.value else 0.0
            conn.execute("UPDATE background_jobs SET state=?,retries_used=?,result_id=?,pid=NULL,process_start=NULL,"
                         "cancel_requested=0,not_before=MAX(not_before,?),updated_at=? WHERE id=?",
                         (final_state, retries, result_id, defer_until, now, job_id))
            conn.execute("UPDATE background_reservations SET state='released',heartbeat_at=? WHERE owner_id=? AND state='active'", (now, job_id))
            conn.commit()
            return result_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def stop_worker(self, worker_id: str) -> None:
        conn = self._tx()
        try:
            conn.execute("UPDATE background_workers SET state='stopped',stopped_at=?,heartbeat_at=? WHERE id=?", (time.time(), time.time(), worker_id))
            conn.commit()
        finally:
            conn.close()

    def upsert_resources(self, resources: list[dict[str, object]]) -> None:
        conn = self._tx()
        try:
            now = time.time()
            for item in resources:
                conn.execute("INSERT INTO background_resources(id,kind,tags_json,enabled,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,tags_json=excluded.tags_json,enabled=excluded.enabled,updated_at=excluded.updated_at",
                             (str(item["id"]), str(item.get("kind", "accelerator")), canonical_json(item.get("tags", {})), int(item.get("enabled", True)), now))
            conn.commit()
        finally:
            conn.close()
        # Physical inventory is mirrored lazily into the host authority. The
        # project table remains for compatibility and contains no migrated
        # ownership state.
        try:
            from .host import HostCoordinator

            HostCoordinator().upsert_resources(resources)
        except Exception:
            pass

    def foreground_intent(self, resource_ids: list[str]) -> str:
        intent_id = str(uuid.uuid4())
        conn = self._tx()
        try:
            now = time.time()
            conn.execute("INSERT INTO background_foreground_intents(id,resources_json,state,created_at,heartbeat_at) VALUES(?,?,'active',?,?)", (intent_id, canonical_json(resource_ids), now, now))
            running = conn.execute("SELECT id,resource_json FROM background_jobs WHERE state='running'").fetchall()
            for row in running:
                held = {value[0] for value in conn.execute("SELECT resource_id FROM background_reservations WHERE owner_id=? AND state='active'", (row["id"],))}
                if not resource_ids or held.intersection(resource_ids):
                    conn.execute("UPDATE background_jobs SET cancel_requested=1,updated_at=? WHERE id=?", (now, row["id"]))
            conn.commit()
            return intent_id
        finally:
            conn.close()

    def reserve_foreground(self, intent_id: str, resource_ids: list[str]) -> bool:
        conn = self._tx()
        try:
            if any(conn.execute("SELECT 1 FROM background_reservations WHERE resource_id=? AND state='active'", (item,)).fetchone() for item in resource_ids):
                conn.commit()
                return False
            now = time.time()
            for item in resource_ids:
                conn.execute("INSERT INTO background_reservations(resource_id,owner_id,owner_kind,state,acquired_at,heartbeat_at) VALUES(?,?,?,'active',?,?)", (item, intent_id, "foreground", now, now))
            conn.commit()
            return True
        finally:
            conn.close()

    def clear_foreground(self, intent_id: str) -> None:
        conn = self._tx()
        try:
            now = time.time()
            conn.execute("UPDATE background_reservations SET state='released',heartbeat_at=? WHERE owner_id=? AND state='active'", (now, intent_id))
            conn.execute("UPDATE background_foreground_intents SET state='released',heartbeat_at=? WHERE id=?", (now, intent_id))
            conn.commit()
        finally:
            conn.close()

    def running_conflicts(self, resource_ids: list[str]) -> list[dict[str, object]]:
        with closing(self.connect(readonly=True)) as conn:
            if resource_ids:
                marks = ",".join("?" for _ in resource_ids)
                rows = conn.execute(f"SELECT j.* FROM background_jobs j JOIN background_reservations r ON r.owner_id=j.id WHERE j.state='running' AND r.state='active' AND r.resource_id IN ({marks})", resource_ids).fetchall()
            else:
                rows = conn.execute("SELECT * FROM background_jobs WHERE state='running'").fetchall()
        return [self._decode_job(row) for row in rows]

    def result(self, identifier: str) -> dict[str, object] | None:
        with closing(self.connect(readonly=True)) as conn:
            row = conn.execute("SELECT r.*,j.kind,j.task_id,j.todo_revision,j.source_fingerprint,j.snapshot_json FROM background_results r JOIN background_jobs j ON j.id=r.job_id WHERE r.id=? OR r.job_id=? ORDER BY r.created_at DESC LIMIT 1", (identifier, identifier)).fetchone()
            if not row:
                return None
            artifacts = [dict(item) for item in conn.execute("SELECT * FROM background_artifacts WHERE job_id=? ORDER BY created_at", (row["job_id"],))]
        value = dict(row)
        value["summary"] = json.loads(value.pop("summary_json"))
        value["snapshot"] = json.loads(value.pop("snapshot_json") or "{}")
        value["artifacts"] = artifacts
        return value

    def latest_valid_result(self, *, watch_id: str, kind: str, source_fingerprint: str) -> dict[str, object] | None:
        """Return one private cached result without exposing it through todo APIs."""
        with closing(self.connect(readonly=True)) as conn:
            row = conn.execute(
                "SELECT r.*,j.snapshot_json FROM background_results r JOIN background_jobs j ON j.id=r.job_id "
                "WHERE j.watch_id=? AND j.kind=? AND j.source_fingerprint=? AND r.valid=1 AND r.contaminated=0 "
                "ORDER BY r.created_at DESC LIMIT 1",
                (watch_id, kind, source_fingerprint),
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        value["summary"] = json.loads(value.pop("summary_json"))
        value["snapshot"] = json.loads(value.pop("snapshot_json") or "{}")
        return value

    def visible_findings(self, *, focus: str = "", limit: int = 3) -> list[dict[str, object]]:
        with closing(self.connect(readonly=True)) as conn:
            rows = conn.execute("SELECT r.*,j.kind,j.task_id,j.todo_revision,j.source_fingerprint FROM background_results r JOIN background_jobs j ON j.id=r.job_id WHERE r.severity>0 AND r.contaminated=0 AND r.status!='preempted' ORDER BY r.severity DESC,r.created_at DESC LIMIT 50").fetchall()
        terms = {term.lower() for term in focus.split() if term}
        result = []
        for row in rows:
            item = dict(row)
            summary = json.loads(item.pop("summary_json"))
            haystack = canonical_json({**item, "summary": summary}).lower()
            if terms and not any(term in haystack for term in terms):
                continue
            item["summary"] = summary
            result.append(item)
            if len(result) >= limit:
                break
        return result

    def record_artifact(self, job_id: str, attempt_id: str | None, kind: str, path: str, content_hash: str | None, complete: bool) -> str:
        artifact_id = str(uuid.uuid4())
        conn = self._tx()
        try:
            conn.execute("INSERT INTO background_artifacts(id,job_id,attempt_id,kind,path,content_hash,complete,created_at) VALUES(?,?,?,?,?,?,?,?)", (artifact_id, job_id, attempt_id, kind, path, content_hash, int(complete), time.time()))
            conn.commit()
            return artifact_id
        finally:
            conn.close()

    def record_external_result(self, *, kind: str, argv: list[str], cwd: str, source_fingerprint: str | None,
                               snapshot: dict[str, object], result: dict[str, object], artifacts: list[dict[str, object]]) -> tuple[str, str]:
        now = time.time()
        job_id = str(uuid.uuid4())
        result_id = str(uuid.uuid4())
        state = str(result.get("status", "succeeded"))
        conn = self._tx()
        try:
            conn.execute(
                "INSERT INTO background_jobs(id,kind,state,priority,argv_json,cwd,env_json,timeout_seconds,resource_json,source_fingerprint,outputs_json,retry_limit,dedup_key,snapshot_json,result_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, kind, state, 10, canonical_json(argv), cwd, "{}", 0.0, "{}", source_fingerprint, "[]", 0,
                 f"foreground:{job_id}", canonical_json(snapshot), result_id, now, now),
            )
            conn.execute("INSERT INTO background_results(id,job_id,status,classification,severity,summary_json,valid,contaminated,parser_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (result_id, job_id, state, result.get("classification"), int(result.get("severity", 0)), canonical_json(result), int(bool(result.get("valid", False))), int(bool(result.get("contaminated", False))), result.get("parser_version"), now))
            for item in artifacts:
                conn.execute("INSERT INTO background_artifacts(id,job_id,attempt_id,kind,path,content_hash,complete,created_at) VALUES(?,?,?,?,?,?,?,?)",
                             (str(uuid.uuid4()), job_id, None, str(item["kind"]), str(item["path"]), item.get("content_hash"), int(item.get("complete", True)), now))
            conn.commit()
            return job_id, result_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def set_meta(self, key: str, value: object) -> None:
        conn = self._tx()
        try:
            conn.execute("INSERT INTO background_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, canonical_json(value)))
            conn.commit()
        finally:
            conn.close()

    def get_meta(self, key: str, default: Any = None) -> Any:
        try:
            with closing(self.connect(readonly=True)) as conn:
                row = conn.execute("SELECT value FROM background_meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default
        except sqlite3.OperationalError:
            return default
