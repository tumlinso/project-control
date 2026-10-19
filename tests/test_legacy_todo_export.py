from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from project_control.legacy_todo_export import (
    EXPORT_TABLES,
    LegacyTodoExportError,
    OPTIONAL_TABLES,
    UnsupportedLegacySchemaError,
    export_legacy_authority,
    snapshot_inventory,
    write_snapshot,
)


PROJECT_ID = "legacy-project-id"
PROJECT_NAME = "Legacy Project"


def make_authority(path: Path, *, migration_version: int = 12) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE sessions(
                id TEXT PRIMARY KEY, label TEXT NOT NULL, token_hash TEXT NOT NULL,
                hostname TEXT NOT NULL, repo_root TEXT NOT NULL, worktree_root TEXT NOT NULL,
                created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, state TEXT NOT NULL
            );
            CREATE TABLE projection_status(id TEXT PRIMARY KEY);
            CREATE TABLE tasks(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                objective TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE claims(
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, session_id TEXT NOT NULL,
                token_hash TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL, baseline_revision INTEGER NOT NULL
            );
            CREATE TABLE events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL UNIQUE,
                timestamp TEXT NOT NULL, actor_session_id TEXT, entity_type TEXT NOT NULL,
                entity_id TEXT, event_type TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE TABLE handoffs(
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, claim_id TEXT, kind TEXT NOT NULL,
                note TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                revision INTEGER NOT NULL
            );
            CREATE TABLE live_recovery_audit(
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, consumed_at TEXT,
                project_revision INTEGER NOT NULL
            );
            CREATE TABLE workflow_recovery_audit(
                id TEXT PRIMARY KEY, task_id TEXT, lane_id TEXT, run_id TEXT,
                completed_at TEXT, created_at TEXT NOT NULL, revision INTEGER NOT NULL
            );
        """)
        special = {
            "schema_migrations", "meta", "sessions", "projection_status", "tasks", "claims",
            "events", "handoffs", "live_recovery_audit", "workflow_recovery_audit",
        }
        for table in sorted(EXPORT_TABLES - special):
            if table in OPTIONAL_TABLES and migration_version < 12:
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            connection.execute(f"CREATE TABLE {quoted}(id TEXT PRIMARY KEY)")
        for version in range(1, migration_version + 1):
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, "2026-01-01T00:00:00Z"),
            )
        connection.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (
                ("schema_version", "2"),
                ("project_uuid", PROJECT_ID),
                ("project_name", PROJECT_NAME),
                ("project_revision", "3"),
            ),
        )
        connection.execute(
            "INSERT INTO sessions(id,label,token_hash,hostname,repo_root,worktree_root,created_at,last_seen_at,state) "
            "VALUES ('session-1','test','session-hash-do-not-export','host','/repo','/repo','now','now','active')"
        )
        connection.execute(
            "INSERT INTO tasks(id,kind,title,objective,notes,created_at,updated_at) "
            "VALUES ('TASK-1','task','Task','objective','token=inline-secret-do-not-export','now','now')"
        )
        connection.execute(
            "INSERT INTO claims(id,task_id,session_id,token_hash,state,created_at,heartbeat_at,expires_at,baseline_revision) "
            "VALUES ('claim-1','TASK-1','session-1','claim-hash-do-not-export','active','now','now','later',3)"
        )
        connection.execute(
            "INSERT INTO events(revision,timestamp,actor_session_id,entity_type,entity_id,event_type,payload_json) "
            "VALUES (3,'2026-01-01T00:00:03Z','session-1','task','TASK-1','task.created',?)",
            (json.dumps({"ok": True, "token": "event-token-do-not-export", "nested": {"lease_hash": "lease-secret-do-not-export"}}),),
        )
        connection.execute(
            "INSERT INTO handoffs(id,task_id,kind,note,payload_json,created_at,revision) "
            "VALUES ('handoff-1','TASK-1','checkpoint','Bearer handoff-secret-do-not-export',?, '2026-01-01T00:00:04Z',3)",
            (json.dumps({"summary": "checkpoint", "password": "handoff-password-do-not-export"}),),
        )
        connection.commit()
    finally:
        connection.close()


class LegacyTodoExportTests(unittest.TestCase):
    def test_exports_deterministic_versioned_snapshot_and_keeps_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self._assert_snapshot_contract(Path(temporary))

    def _assert_snapshot_contract(self, tmp_path: Path) -> None:
        database = tmp_path / "state.sqlite3"
        make_authority(database)
        before = hashlib.sha256(database.read_bytes()).hexdigest()

        snapshot = export_legacy_authority(
            database,
            repository_id="github:example/legacy-project",
            exported_at="2026-10-19T12:00:00Z",
        )
        repeated = export_legacy_authority(
            database,
            repository_id="github:example/legacy-project",
            exported_at="2026-10-19T12:00:00Z",
        )
        after = hashlib.sha256(database.read_bytes()).hexdigest()

        self.assertEqual(before, after)
        self.assertEqual(snapshot, repeated)
        self.assertEqual(snapshot["format"], "todo-orchestrator.legacy-project-snapshot")
        self.assertEqual(snapshot["version"], 1)
        self.assertEqual(snapshot["source"], {
            "system": "project-control.todo-v2",
            "project_id": PROJECT_ID,
            "schema_version": 2,
            "authority_revision": 3,
            "exported_at": "2026-10-19T12:00:00Z",
        })
        self.assertEqual(snapshot["project"], {"name": PROJECT_NAME, "repository_id": "github:example/legacy-project"})
        self.assertEqual(set(snapshot["tables"]), EXPORT_TABLES)
        self.assertEqual(snapshot["tables"]["project_amendments"], [])
        self.assertEqual(snapshot["tables"]["events"][0]["revision"], 3)
        self.assertNotIn("token", snapshot["tables"]["events"][0]["payload_json"])
        self.assertEqual(snapshot["history"][0]["source_table"], "events")
        self.assertEqual(snapshot["history"][0]["payload"], {"ok": True, "nested": {}})

        envelope = {key: value for key, value in snapshot.items() if key != "snapshot_digest"}
        expected_digest = hashlib.sha256(
            json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        ).hexdigest()
        self.assertEqual(snapshot["snapshot_digest"], expected_digest)

        encoded = json.dumps(snapshot, ensure_ascii=False)
        for secret in (
            "session-hash-do-not-export",
            "claim-hash-do-not-export",
            "event-token-do-not-export",
            "lease-secret-do-not-export",
            "inline-secret-do-not-export",
            "handoff-secret-do-not-export",
            "handoff-password-do-not-export",
        ):
            self.assertNotIn(secret, encoded)


    def test_migration_11_exports_union_with_absent_12_tables_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state-v11.sqlite3"
            make_authority(database, migration_version=11)

            snapshot = export_legacy_authority(database, exported_at="2026-10-19T12:00:00Z")

            self.assertTrue(snapshot["tables"]["events"])
            self.assertEqual(snapshot["tables"]["project_declarations"], [])
            self.assertEqual(snapshot["tables"]["project_fragment_invalidations"], [])


    def test_redacts_legacy_private_content_keys_without_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state.sqlite3"
            make_authority(database)
            private_values = {
                key: f"private-value-for-{key}"
                for key in (
                    "recovery_approval", "recovery_token", "gpu_identifier", "model_endpoint",
                    "packet", "packet_body", "child_packet", "raw_packet", "log", "logs",
                    "raw_log", "raw_logs", "transcript", "transcripts", "stdout", "stderr",
                    "traceback", "capability_lineage", "child_lineage",
                )
            }
            raw_payload = json.dumps({**private_values, "safe": "retained"})
            connection = sqlite3.connect(database)
            try:
                connection.execute("UPDATE events SET payload_json=? WHERE revision=3", (raw_payload,))
                connection.commit()
            finally:
                connection.close()
            before = hashlib.sha256(database.read_bytes()).hexdigest()

            snapshot = export_legacy_authority(database, exported_at="2026-10-19T12:00:00Z")

            after = hashlib.sha256(database.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            encoded_snapshot = json.dumps(snapshot, ensure_ascii=False)
            for private_value in private_values.values():
                self.assertNotIn(private_value, encoded_snapshot)
            event_payload = json.loads(snapshot["tables"]["events"][0]["payload_json"])
            self.assertEqual(event_payload, {"safe": "retained"})
            self.assertEqual(snapshot["history"][0]["payload"], {"safe": "retained"})
            connection = sqlite3.connect(database)
            try:
                source_payload = connection.execute(
                    "SELECT payload_json FROM events WHERE revision=3"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(json.loads(source_payload), {**private_values, "safe": "retained"})


    def test_rejects_unknown_schema_without_writing_or_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state.sqlite3"
            make_authority(database)
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE unexpected_table(secret TEXT)")
                connection.commit()
            finally:
                connection.close()
            before = hashlib.sha256(database.read_bytes()).hexdigest()

            with self.assertRaises(UnsupportedLegacySchemaError):
                export_legacy_authority(database)

            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)


    def test_write_is_exclusive_and_inventory_keeps_unknown_work_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = {
                "source": {"project_id": PROJECT_ID, "authority_revision": 3},
                "tables": {
                    "claims": [
                        {"id": "claim-adopted", "task_id": "TASK-OLD", "state": "adopted"},
                        {"id": "claim-unknown", "task_id": "TASK-X", "state": "new-unknown-state"},
                    ],
                    "workflow_dispatches": [{"id": "dispatch-x", "claim_id": "claim-x", "lane_id": "lane-x", "state": "active"}],
                    "workflow_workspaces": [{"id": "workspace-x", "state": "quarantined"}],
                    "workflow_integration_queue": [{"id": "integration-x", "state": "queued"}],
                    "workflow_patch_artifacts": [{"id": "patch-x", "state": "integrated"}],
                },
            }
            inventory = snapshot_inventory(snapshot)
            destination = write_snapshot(snapshot, Path(temporary) / "snapshot.json")

            self.assertEqual(inventory["active_claims"], [{"source_table": "claims", "id": "claim-unknown", "task_id": "TASK-X", "state": "new-unknown-state"}])
            self.assertEqual(inventory["active_dispatches"][0]["id"], "dispatch-x")
            self.assertEqual(inventory["active_workspaces"][0]["id"], "workspace-x")
            self.assertEqual(inventory["pending_integration_effects"], [
                {"source_table": "workflow_integration_queue", "id": "integration-x", "state": "queued"}
            ])
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(LegacyTodoExportError, "already exists"):
                write_snapshot(snapshot, destination)


if __name__ == "__main__":
    unittest.main()
