"""Read-only export of a Project Control Todo v2 authority for migration.

This module is a migration boundary, not a normal Project Control read path.
It opens the legacy SQLite file read-only, validates the versioned legacy
schema, and emits a portable snapshot without session or bearer credentials.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Iterable
from urllib.parse import quote


SNAPSHOT_FORMAT = "todo-orchestrator.legacy-project-snapshot"
SNAPSHOT_VERSION = 1
SOURCE_SYSTEM = "project-control.todo-v2"
PROJECT_SCHEMA_VERSION = 2
SUPPORTED_DATABASE_MIGRATIONS = frozenset({11, 12})

# These are the union of semantic Todo tables observed in legacy database
# migrations 11 and 12. Credentials and host login/session records are omitted.
EXPORT_TABLES = frozenset({
    "events",
    "tasks",
    "task_dependencies",
    "decisions",
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
    "claims",
    "lock_leases",
    "resource_classes",
    "resource_instances",
    "resource_requests",
    "resource_leases",
    "gates",
    "checkpoint_gates",
    "evidence",
    "handoffs",
    "migration_warnings",
    "task_completion_gates",
    "child_executions",
    "child_scope_leases",
    "child_attempts",
    "live_recovery_approvals",
    "live_recovery_audit",
    "workflow_runs",
    "workflow_run_charters",
    "workflow_lanes",
    "workflow_lane_tasks",
    "workflow_workspaces",
    "workflow_dispatches",
    "workflow_capabilities",
    "workflow_messages",
    "workflow_message_recipients",
    "workflow_message_receipts",
    "workflow_rendezvous",
    "workflow_rendezvous_participants",
    "workflow_rendezvous_arrivals",
    "workflow_context_fragments",
    "workflow_integration_queue",
    "workflow_patch_artifacts",
    "workflow_child_result_candidates",
    "workflow_recovery_audit",
    "project_amendments",
    "project_declarations",
    "project_fragment_invalidations",
})

OPTIONAL_TABLES = frozenset({
    "project_amendments",
    "project_declarations",
    "project_fragment_invalidations",
})
REQUIRED_TABLES = frozenset({"tasks", "task_dependencies", "events", "handoffs", "live_recovery_audit", "workflow_recovery_audit"})
METADATA_TABLES = frozenset({"meta", "schema_migrations", "sessions", "projection_status"})
DATABASE_TABLES = EXPORT_TABLES | METADATA_TABLES

# Values in these columns cannot be carried forward as credentials.  The
# recursive predicate also applies inside old JSON payloads.
SENSITIVE_COLUMN_NAMES = frozenset({
    "token_hash", "token_digest", "bearer_token", "session_token",
    "claim_token", "claim_token_hash", "child_token", "worker_token",
    "approval_token", "approval_token_hash", "recovery_approval", "recovery_token",
    "password", "password_hash",
    "passwd", "passwd_hash", "secret", "credential", "credential_hash",
    "lease_hash", "lease_token_hash", "capability_token", "capability_token_hash",
    "authorization", "authorization_token",
    "api_key", "api_key_hash", "access_key", "private_key", "auth_header",
    # Legacy workflow protocol/content keys that must not cross the workflow
    # boundary, even when a payload contains no conventional credential name.
    "gpu_identifier", "model_endpoint", "packet", "packet_body", "child_packet",
    "raw_packet", "log", "logs", "raw_log", "raw_logs", "transcript",
    "transcripts", "stdout", "stderr", "traceback", "capability_lineage", "child_lineage",
})
SENSITIVE_COLUMN_PARTS = ("password", "passwd", "secret", "credential", "bearer_token")
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"(?i)\b(password|passwd|token(?:[_ -]?hash)?|recovery[_ -]?approval|secret|credential|lease[_ -]?hash|authorization|api[_-]?key|access[_-]?key|private[_-]?key)(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^,\s;\]}]+)"),
)


class LegacyTodoExportError(ValueError):
    """The legacy authority cannot be exported under the supported contract."""


class UnsupportedLegacySchemaError(LegacyTodoExportError):
    """The database is not one of the inspected legacy Todo schemas."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise LegacyTodoExportError("snapshot contains a value outside canonical JSON") from exc


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sensitive_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
    if normalized in SENSITIVE_COLUMN_NAMES:
        return True
    if any(part in normalized for part in SENSITIVE_COLUMN_PARTS):
        return True
    # Do not drop ordinary semantic IDs such as capability_id.  Only token
    # fields and lease hashes are credential material.
    if "token" in normalized:
        return True
    if "lease" in normalized and "hash" in normalized:
        return True
    return False


def _sanitize_json(value: Any) -> tuple[Any, bool]:
    """Remove secret-bearing keys recursively and report whether changed."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        changed = False
        for key, item in value.items():
            if _sensitive_key(key):
                changed = True
                continue
            clean, nested = _sanitize_json(item)
            result[key] = clean
            changed = changed or nested
        return result, changed
    if isinstance(value, list):
        result = []
        changed = False
        for item in value:
            clean, nested = _sanitize_json(item)
            result.append(clean)
            changed = changed or nested
        return result, changed
    if isinstance(value, str):
        clean = _sanitize_text(value)
        return clean, clean != value
    return value, False


def _sanitize_text(value: str) -> str:
    """Redact explicitly labelled credential values in unstructured text."""
    clean = value
    for pattern in _SENSITIVE_TEXT_PATTERNS:
        if pattern.groups == 1:
            clean = pattern.sub(r"\1[REDACTED]", clean)
        else:
            clean = pattern.sub(r"\1\2[REDACTED]", clean)
    return clean


def _portable_scalar(value: Any, *, context: str) -> Any:
    if value is None or type(value) in {str, int, bool}:
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise LegacyTodoExportError(f"{context} contains invalid Unicode") from exc
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise LegacyTodoExportError(f"{context} contains a non-finite number")
        if not value.is_integer():
            raise LegacyTodoExportError(f"{context} contains a non-integral SQLite float")
        return int(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise LegacyTodoExportError(f"{context} contains binary data")
    raise LegacyTodoExportError(f"{context} contains an unsupported SQLite value")


def _portable_row(row: sqlite3.Row, *, table: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in row.keys():
        if _sensitive_key(key) or key == "command_json":
            continue
        value = _portable_scalar(row[key], context=f"{table}.{key}")
        if isinstance(value, str):
            if key.endswith("_json"):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    result[key] = _sanitize_text(value)
                    continue
                clean, changed = _sanitize_json(parsed)
                # Keep source JSON text byte-for-byte when it has no credentials.
                result[key] = _canonical_bytes(clean).decode("utf-8") if changed else value
            else:
                result[key] = _sanitize_text(value)
        else:
            result[key] = value
    return result


def _sanitize_history_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_history_value(item) for key, item in value.items() if not _sensitive_key(key)}
    if isinstance(value, list):
        return [_sanitize_history_value(item) for item in value]
    if isinstance(value, str):
        value = _sanitize_text(value)
        stripped = value.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return value
            clean, changed = _sanitize_json(parsed)
            return clean if changed else parsed
    return value


def _parse_json_payload(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return _sanitize_history_value(parsed)


def _utc_timestamp(value: str | datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LegacyTodoExportError("exported_at must be an ISO-8601 timestamp") from exc
    elif not isinstance(value, datetime):
        raise LegacyTodoExportError("exported_at must be an ISO-8601 timestamp")
    if value.tzinfo is None or value.utcoffset() is None:
        raise LegacyTodoExportError("exported_at must include a timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _connect_readonly(path: Path) -> sqlite3.Connection:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise LegacyTodoExportError("legacy authority path does not exist") from exc
    if not resolved.is_file():
        raise LegacyTodoExportError("legacy authority path is not a regular file")
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
    except sqlite3.Error as exc:
        raise LegacyTodoExportError("cannot open legacy authority read-only") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _validate_source(connection: sqlite3.Connection) -> tuple[dict[str, str], int]:
    tables = _table_names(connection)
    missing_metadata = METADATA_TABLES - tables
    missing_core = REQUIRED_TABLES - tables
    if missing_metadata or missing_core:
        raise UnsupportedLegacySchemaError("legacy database is missing required metadata or core tables")
    unknown = tables - DATABASE_TABLES
    if unknown:
        raise UnsupportedLegacySchemaError("legacy database contains tables outside the inspected allowlist")

    meta = {str(row["key"]): str(row["value"]) for row in connection.execute("SELECT key,value FROM meta")}
    try:
        schema_version = int(meta["schema_version"])
        project_revision = int(meta["project_revision"])
        project_id = meta["project_uuid"]
        project_name = meta["project_name"]
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsupportedLegacySchemaError("legacy authority metadata is incomplete") from exc
    if schema_version != PROJECT_SCHEMA_VERSION or project_revision < 0:
        raise UnsupportedLegacySchemaError("legacy project schema metadata is unsupported")
    if not project_id.strip() or not project_name.strip():
        raise UnsupportedLegacySchemaError("legacy project identity is incomplete")

    versions = [int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
    if not versions or versions != list(range(1, versions[-1] + 1)):
        raise UnsupportedLegacySchemaError("legacy database migration history is incomplete")
    database_migration = versions[-1]
    if database_migration not in SUPPORTED_DATABASE_MIGRATIONS:
        raise UnsupportedLegacySchemaError("legacy database migration version is unsupported")
    present = tables & EXPORT_TABLES
    missing_semantic = (EXPORT_TABLES - OPTIONAL_TABLES) - present
    if missing_semantic:
        raise UnsupportedLegacySchemaError("legacy database is missing a required semantic table")
    expected_optional = OPTIONAL_TABLES if database_migration == 12 else frozenset()
    if (OPTIONAL_TABLES & present) != expected_optional:
        raise UnsupportedLegacySchemaError("legacy database tables do not match their migration version")
    return meta, database_migration


def _primary_key_order(connection: sqlite3.Connection, table: str) -> list[str]:
    columns = list(connection.execute(f"PRAGMA table_info({_quote_identifier(table)})"))
    primary = sorted((int(row[5]), str(row[1])) for row in columns if row[5])
    return [name for _, name in primary]


def _read_table(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    order = _primary_key_order(connection, table)
    sql = f"SELECT * FROM {_quote_identifier(table)}"
    if order:
        sql += " ORDER BY " + ",".join(_quote_identifier(column) for column in order)
    rows = [_portable_row(row, table=table) for row in connection.execute(sql)]
    if not order:
        rows.sort(key=_canonical_bytes)
    return rows


def _history_record(
    *, source_table: str, source_id: Any, entity_id: Any, revision: Any,
    occurred_at: Any, event_type: Any, payload: Any,
) -> dict[str, Any]:
    source_id = _portable_scalar(source_id, context=f"{source_table}.source_id")
    if source_id is None:
        raise LegacyTodoExportError(f"{source_table} history row has no source identity")
    entity_id = _portable_scalar(entity_id, context=f"{source_table}.entity_id")
    revision = _portable_scalar(revision, context=f"{source_table}.revision")
    occurred_at = _portable_scalar(occurred_at, context=f"{source_table}.occurred_at")
    event_type = _portable_scalar(event_type, context=f"{source_table}.event_type")
    if revision is not None and type(revision) is not int:
        raise LegacyTodoExportError(f"{source_table} history revision is not an integer")
    if occurred_at is not None and not isinstance(occurred_at, str):
        raise LegacyTodoExportError(f"{source_table} history timestamp is not text")
    if not isinstance(event_type, str):
        raise LegacyTodoExportError(f"{source_table} history event type is not text")
    return {
        "source_table": source_table,
        "source_id": str(source_id),
        "entity_id": None if entity_id is None else str(entity_id),
        "revision": revision,
        "occurred_at": occurred_at,
        "event_type": str(event_type),
        "payload": _sanitize_history_value(payload),
    }


def _history(connection: sqlite3.Connection, present_tables: set[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in connection.execute("SELECT * FROM events ORDER BY revision,seq"):
        records.append(_history_record(
            source_table="events", source_id=row["seq"], entity_id=row["entity_id"],
            revision=row["revision"], occurred_at=row["timestamp"],
            event_type=row["event_type"], payload=_parse_json_payload(row["payload_json"]),
        ))
    for row in connection.execute("SELECT * FROM handoffs ORDER BY revision,id"):
        payload = {key: row[key] for key in row.keys() if not _sensitive_key(key) and key != "command_json"}
        payload = {key: _parse_json_payload(value) if key.endswith("_json") else value for key, value in payload.items()}
        records.append(_history_record(
            source_table="handoffs", source_id=row["id"], entity_id=row["task_id"],
            revision=row["revision"], occurred_at=row["created_at"],
            event_type=f"handoff.{row['kind']}", payload=payload,
        ))
    if "live_recovery_audit" in present_tables:
        for row in connection.execute("SELECT * FROM live_recovery_audit ORDER BY project_revision,id"):
            payload = {key: _parse_json_payload(row[key]) if key.endswith("_json") else row[key]
                       for key in row.keys() if not _sensitive_key(key)}
            records.append(_history_record(
                source_table="live_recovery_audit", source_id=row["id"], entity_id=row["task_id"],
                revision=row["project_revision"], occurred_at=row["consumed_at"],
                event_type="recovery.live_overridden", payload=payload,
            ))
    if "workflow_recovery_audit" in present_tables:
        for row in connection.execute("SELECT * FROM workflow_recovery_audit ORDER BY revision,id"):
            entity_id = row["task_id"] or row["lane_id"] or row["run_id"]
            occurred_at = row["completed_at"] or row["created_at"]
            payload = {key: _parse_json_payload(row[key]) if key.endswith("_json") else row[key]
                       for key in row.keys() if not _sensitive_key(key)}
            records.append(_history_record(
                source_table="workflow_recovery_audit", source_id=row["id"], entity_id=entity_id,
                revision=row["revision"], occurred_at=occurred_at,
                event_type="workflow.recovery_audit", payload=payload,
            ))
    records.sort(key=lambda item: (
        -1 if item["revision"] is None else item["revision"],
        "" if item["occurred_at"] is None else str(item["occurred_at"]),
        item["source_table"], item["source_id"],
    ))
    return records


def export_legacy_authority(
    database_path: str | os.PathLike[str],
    *,
    repository_id: str | None = None,
    exported_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Read and export one inspected legacy Todo authority without writes."""
    if repository_id is not None and (not isinstance(repository_id, str) or not repository_id.strip()):
        raise LegacyTodoExportError("repository_id must be non-empty text when supplied")
    connection = _connect_readonly(Path(database_path))
    try:
        connection.execute("BEGIN")
        meta, database_migration = _validate_source(connection)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise LegacyTodoExportError("legacy authority integrity check failed")
        names = _table_names(connection)
        tables: dict[str, list[dict[str, Any]]] = {
            table: (_read_table(connection, table) if table in names else [])
            for table in sorted(EXPORT_TABLES)
        }
        history = _history(connection, names)
        envelope: dict[str, Any] = {
            "format": SNAPSHOT_FORMAT,
            "version": SNAPSHOT_VERSION,
            "source": {
                "system": SOURCE_SYSTEM,
                "project_id": meta["project_uuid"],
                "schema_version": PROJECT_SCHEMA_VERSION,
                "authority_revision": int(meta["project_revision"]),
                "exported_at": _utc_timestamp(exported_at),
            },
            "project": {"name": meta["project_name"]},
            "tables": tables,
            "history": history,
        }
        if repository_id is not None:
            envelope["project"]["repository_id"] = repository_id
        envelope["snapshot_digest"] = hashlib.sha256(_canonical_bytes(envelope)).hexdigest()
        # Confirm the database's internal migration version was checked rather
        # than inferred from the project metadata version. It is intentionally
        # not duplicated in the frozen import envelope.
        assert database_migration in SUPPORTED_DATABASE_MIGRATIONS
        return envelope
    except sqlite3.Error as exc:
        raise LegacyTodoExportError("cannot read a consistent legacy authority snapshot") from exc
    finally:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        connection.close()


def snapshot_inventory(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Summarize old active/ambiguous work that needs an explicit cutover action."""
    tables = snapshot.get("tables", {})

    def select(table: str, predicate) -> list[dict[str, Any]]:
        return [row for row in tables.get(table, []) if predicate(row)]

    settled = {"released", "recovered", "recovered_released", "completed", "complete", "closed",
               "accepted", "rejected", "cancelled", "canceled", "integrated", "discarded", "failed",
               "expired", "expired_clean", "force_released", "not_applied", "finalized", "superseded",
               "stopped", "inactive", "consumed"}

    def stateful(table: str) -> list[dict[str, Any]]:
        # Unknown and newly introduced states remain visible until they can be
        # explicitly classified as terminal; migrations must not guess them away.
        terminal = settled | ({"adopted"} if table == "claims" else set())
        return select(table, lambda row: str(row.get("state", "")).lower() not in terminal)

    active_claims = stateful("claims")
    active_dispatches = stateful("workflow_dispatches")
    active_children = [(table, row) for table in ("child_executions", "child_attempts") for row in stateful(table)]
    active_leases = [(table, row) for table in ("lock_leases", "resource_leases", "child_scope_leases")
                     for row in stateful(table)]
    pending_candidates = select("workflow_child_result_candidates", lambda row: str(row.get("state", "")).lower() not in settled)
    active_approvals = stateful("live_recovery_approvals")
    active_workspaces = stateful("workflow_workspaces")
    pending_integration = [(table, row) for table in ("workflow_integration_queue", "workflow_patch_artifacts")
                           for row in stateful(table)]

    def reference(
        table: str,
        row: dict[str, Any],
        *,
        id_keys: tuple[str, ...],
        task_key: str | None = "task_id",
    ) -> dict[str, Any]:
        result = {"source_table": table, **{key: row[key] for key in id_keys if key in row}}
        if task_key and task_key in row:
            result[task_key] = row[task_key]
        if "state" in row:
            result["state"] = row["state"]
        if "expires_at" in row:
            result["expires_at"] = row["expires_at"]
        return result

    return {
        "source_project_id": snapshot["source"]["project_id"],
        "authority_revision": snapshot["source"]["authority_revision"],
        "active_claims": [reference("claims", row, id_keys=("id",), task_key="task_id") for row in active_claims],
        "active_dispatches": [reference("workflow_dispatches", row, id_keys=("id", "claim_id", "lane_id", "workspace_id"), task_key=None)
                              for row in active_dispatches],
        "active_children": [reference(table, row, id_keys=("id", "child_execution_id", "parent_claim_id"), task_key="task_id")
                            for table, row in active_children],
        "active_leases": [reference(table, row, id_keys=("id", "claim_id", "lock_name", "instance_id"), task_key=None)
                          for table, row in active_leases],
        "pending_candidates": [reference("workflow_child_result_candidates", row,
                                          id_keys=("id", "child_execution_id", "parent_claim_id"), task_key=None)
                               for row in pending_candidates],
        "pending_recovery_approvals": [reference("live_recovery_approvals", row, id_keys=("id", "task_id"), task_key=None)
                                       for row in active_approvals],
        "active_workspaces": [reference("workflow_workspaces", row,
                                         id_keys=("id", "run_id", "lane_id", "integration_task_id"), task_key=None)
                              for row in active_workspaces],
        "pending_integration_effects": [reference(table, row,
                                                  id_keys=("id", "run_id", "integration_task_id", "patch_artifact_id", "workspace_id"),
                                                  task_key=None)
                                         for table, row in pending_integration],
        "target_needs": [
            "Reconcile each active or unknown claim, dispatch, child, and lease under the legacy authority; never transfer old bearer or capability tokens.",
            "Preserve each referenced worktree and patch artifact, then obtain fresh provider observations before admitting a standalone attempt.",
            "Resolve pending child candidates, recovery approvals, and integration effects explicitly; archive their legacy records and do not replay uncertain effects.",
        ],
    }


def write_snapshot(snapshot: dict[str, Any], destination: str | os.PathLike[str]) -> Path:
    """Create a readable JSON snapshot at a new path, never overwriting files."""
    path = Path(destination)
    data = (json.dumps(snapshot, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise LegacyTodoExportError("snapshot destination already exists") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="legacy state.sqlite3 from a consistent backup")
    parser.add_argument("destination", type=Path, help="new JSON snapshot path")
    parser.add_argument("--repository-id", help="trusted repository identity from the host registry")
    args = parser.parse_args(argv)
    try:
        snapshot = export_legacy_authority(args.database, repository_id=args.repository_id)
        write_snapshot(snapshot, args.destination)
    except (LegacyTodoExportError, OSError) as exc:
        print(f"legacy Todo export failed: {exc}", file=sys.stderr)
        return 2
    inventory = snapshot_inventory(snapshot)
    counts = {name: len(rows) for name, rows in snapshot["tables"].items() if rows}
    print(json.dumps({
        "destination": str(args.destination),
        "snapshot_digest": snapshot["snapshot_digest"],
        "authority_revision": snapshot["source"]["authority_revision"],
        "table_counts": counts,
        "history_count": len(snapshot["history"]),
        "active_claims": len(inventory["active_claims"]),
        "active_dispatches": len(inventory["active_dispatches"]),
        "active_children": len(inventory["active_children"]),
        "active_leases": len(inventory["active_leases"]),
        "pending_integration_effects": len(inventory["pending_integration_effects"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXPORT_TABLES", "LegacyTodoExportError", "OPTIONAL_TABLES",
    "SNAPSHOT_FORMAT", "SNAPSHOT_VERSION", "UnsupportedLegacySchemaError",
    "export_legacy_authority", "snapshot_inventory", "write_snapshot",
]
