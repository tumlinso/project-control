"""SQLite connection, migration, retry, and semantic transaction primitives."""

from __future__ import annotations

import json
import random
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from .config import utc_now
from .migrations import DATABASE_MIGRATION_VERSION, READ_COMPATIBLE_MIGRATION_VERSION, MIGRATIONS, SCHEMA_VERSION
from .models import ExitCode, TodoError

T = TypeVar("T")


@dataclass(frozen=True)
class Unchanged:
    """Canonical transaction result with ledger writes but no semantic change."""
    value: Any


class Database:
    def __init__(self, path: Path, *, busy_timeout_ms: int = 5000, retries: int = 7, read_only: bool = False):
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms
        self.retries = retries
        self.read_only = read_only

    def connect(self) -> sqlite3.Connection:
        if self.read_only:
            conn = sqlite3.connect(
                f"file:{self.path}?mode=ro",
                uri=True,
                timeout=self.busy_timeout_ms / 1000.0,
                isolation_level=None,
            )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        if self.read_only:
            conn.execute("PRAGMA query_only=ON")
        else:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
        return conn

    def initialize(self, project: dict[str, object]) -> None:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            conn: sqlite3.Connection | None = None
            try:
                conn = self.connect()
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
                current = conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
                for version in sorted(MIGRATIONS):
                    if version <= current:
                        continue
                    for statement in MIGRATIONS[version].split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)", (version, utc_now()))
                conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('project_revision','0')")
                conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
                conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('project_uuid',?)", (str(project["project_uuid"]),))
                conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('project_name',?)", (str(project["project_name"]),))
                conn.commit()
                return
            except sqlite3.OperationalError as exc:
                if conn is not None:
                    conn.rollback()
                last_error = exc
                if "locked" not in str(exc).lower() or attempt + 1 >= self.retries:
                    raise
                time.sleep(min(0.4, 0.01 * (2**attempt)) + random.uniform(0.001, 0.02))
            except Exception:
                if conn is not None:
                    conn.rollback()
                raise
            finally:
                if conn is not None:
                    conn.close()
        raise TodoError("database_contention", str(last_error or "database is busy"))

    def require_current_schema(
        self,
        project: dict[str, object],
        *,
        repo_root: Path | None = None,
    ) -> int:
        """Reject an old authority before current-schema read code executes."""
        with self.read() as conn:
            migrations_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            observed = (
                int(conn.execute(
                    "SELECT COALESCE(MAX(version),0) FROM schema_migrations"
                ).fetchone()[0])
                if migrations_table
                else 0
            )
        if observed < READ_COMPATIBLE_MIGRATION_VERSION:
            raise TodoError(
                "schema_migration_required",
                "Todo authority schema migration is required before read-only observation",
                ExitCode.CONSISTENCY_ERROR,
                {
                    "observed_migration_version": observed,
                    "required_migration_version": READ_COMPATIBLE_MIGRATION_VERSION,
                    "project_uuid": project.get("project_uuid"),
                    "project_name": project.get("project_name"),
                    "repository": str(repo_root) if repo_root is not None else None,
                    "remediation": (
                        "Run a current writable todo status invocation to initialize and "
                        "migrate this authority, then retry the read-only operation."
                    ),
                },
            )
        return observed

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mutate(
        self,
        *,
        actor_session_id: str | None | Callable[[T], str | None],
        entity_type: str,
        entity_id: str | None | Callable[[T], str | None],
        event_type: str,
        payload: dict[str, Any] | Callable[[T], dict[str, Any]] | None,
        operation: Callable[[sqlite3.Connection, int], T],
    ) -> tuple[T, int]:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                current = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
                revision = current + 1
                result = operation(conn, revision)
                if isinstance(result, Unchanged):
                    conn.commit()
                    return result.value, current
                resolved_actor = actor_session_id(result) if callable(actor_session_id) else actor_session_id
                resolved_entity = entity_id(result) if callable(entity_id) else entity_id
                resolved_payload = payload(result) if callable(payload) else payload
                conn.execute("UPDATE meta SET value=? WHERE key='project_revision'", (str(revision),))
                conn.execute(
                    "INSERT INTO events(revision,timestamp,actor_session_id,entity_type,entity_id,event_type,payload_json) VALUES(?,?,?,?,?,?,?)",
                    (revision, utc_now(), resolved_actor, entity_type, resolved_entity, event_type, json.dumps(resolved_payload or {}, sort_keys=True)),
                )
                conn.commit()
                return result, revision
            except sqlite3.OperationalError as exc:
                conn.rollback()
                last_error = exc
                if "locked" not in str(exc).lower() or attempt + 1 >= self.retries:
                    raise
                time.sleep(min(0.4, 0.01 * (2**attempt)) + random.uniform(0.001, 0.02))
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        raise TodoError("database_contention", str(last_error or "database is busy"))

    def revision(self) -> int:
        with self.read() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()
            return int(row[0]) if row else 0

    def integrity(self) -> dict[str, object]:
        with self.read() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            foreign = [dict(row) for row in conn.execute("PRAGMA foreign_key_check")]
            version = conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
        return {"integrity": integrity, "foreign_key_errors": foreign, "schema_version": version}
