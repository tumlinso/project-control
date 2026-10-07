from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from todo_orchestrator.db import Database
from todo_orchestrator.migrations import DATABASE_MIGRATION_VERSION, READ_COMPATIBLE_MIGRATION_VERSION, MIGRATIONS
from todo_orchestrator.models import ExitCode, TodoError
from todo_orchestrator.semantic import SemanticReader
from todo_orchestrator.read_port import create_todo_read_port
from todo_orchestrator.service import Service


TODO_SCRIPT = Path(__file__).resolve().parent / "scripts" / "todo.py"
OLD_MIGRATION_VERSION = 8


def _apply_through(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for number in range(1, version + 1):
        for statement in MIGRATIONS[number].split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
            (number, "2026-01-01T00:00:00Z"),
        )


class OldSchemaFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.state_root = base / "state"
        self.project = {
            "schema_version": 2,
            "project_uuid": "old-schema-project",
            "project_name": "old-schema-fixture",
            "created_at": "2026-01-01T00:00:00Z",
            "configuration": {},
        }
        control = self.root / ".todo-orchestrator"
        control.mkdir()
        (control / "project.json").write_text(json.dumps(self.project), encoding="utf-8")
        self.projections = {
            control / "state.snapshot.json": b'{"durable":"unchanged"}\n',
            self.root / "todos.md": b"# unchanged\n",
            self.root / "todo-status.md": b"# unchanged\n",
        }
        for path, value in self.projections.items():
            path.write_bytes(value)
        self.db_path = self.state_root / str(self.project["project_uuid"]) / "state.sqlite3"
        self.db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(self.db_path)
        try:
            _apply_through(conn, OLD_MIGRATION_VERSION)
            conn.execute("INSERT INTO meta(key,value) VALUES('project_revision','7')")
            conn.execute("INSERT INTO meta(key,value) VALUES('schema_version','2')")
            conn.execute("INSERT INTO meta(key,value) VALUES('project_uuid',?)", (self.project["project_uuid"],))
            conn.execute("INSERT INTO meta(key,value) VALUES('project_name',?)", (self.project["project_name"],))
            conn.execute(
                "INSERT INTO tasks(id,kind,title,objective,status,created_at,updated_at) "
                "VALUES('OLD','task','Old','Old','done','now','now')"
            )
            conn.commit()
        finally:
            conn.close()

    def environment(self) -> dict[str, str]:
        result = os.environ.copy()
        result["TODO_ORCHESTRATOR_STATE_DIR"] = str(self.state_root)
        result["TODO_ORCHESTRATOR_READ_ONLY"] = "1"
        result["PYTHONDONTWRITEBYTECODE"] = "1"
        return result

    def authority_fingerprint(self) -> tuple[str, int, dict[Path, tuple[bytes, int]]]:
        database = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            revision = int(conn.execute(
                "SELECT value FROM meta WHERE key='project_revision'"
            ).fetchone()[0])
        finally:
            conn.close()
        projections = {
            path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.projections
        }
        return database, revision, projections

    def close(self) -> None:
        self.temporary.cleanup()


class ReadOnlySchemaCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = OldSchemaFixture()
        self.environment = patch.dict(os.environ, self.fixture.environment(), clear=True)
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.fixture.close()

    def assert_migration_required(self, operation) -> None:
        with self.assertRaises(TodoError) as caught:
            operation()
        error = caught.exception
        self.assertEqual(error.code, "schema_migration_required")
        self.assertEqual(error.exit_code, ExitCode.CONSISTENCY_ERROR)
        self.assertEqual(error.details["observed_migration_version"], OLD_MIGRATION_VERSION)
        self.assertEqual(error.details["required_migration_version"], READ_COMPATIBLE_MIGRATION_VERSION)
        self.assertEqual(error.details["project_uuid"], self.fixture.project["project_uuid"])
        self.assertEqual(error.details["project_name"], self.fixture.project["project_name"])
        self.assertEqual(error.details["repository"], str(self.fixture.root))
        self.assertIn("writable todo status", error.details["remediation"])

    def test_current_read_only_operations_reject_old_schema_without_mutation(self) -> None:
        before = self.fixture.authority_fingerprint()
        operations = {
            "service status": lambda: Service(self.fixture.root).status(),
            "service export": lambda: Service(self.fixture.root).export(),
            "semantic state": lambda: SemanticReader(self.fixture.root).state(),
            "semantic workflow": lambda: SemanticReader(self.fixture.root).workflow(),
        }
        for label, operation in operations.items():
            with self.subTest(operation=label):
                self.assert_migration_required(operation)
        self.assertEqual(self.fixture.authority_fingerprint(), before)

    def test_in_process_read_port_preserves_schema_failure_envelope_without_mutation(self) -> None:
        before = self.fixture.authority_fingerprint()
        port = create_todo_read_port(Path(__file__).resolve().parents[2])
        for operation in ("status", "export", "semantic.state", "semantic.workflow"):
            with self.subTest(operation=operation):
                payload = port.invoke(operation, repo_root=self.fixture.root)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["code"], "schema_migration_required")
                self.assertEqual(
                    payload["error"]["details"]["observed_migration_version"],
                    OLD_MIGRATION_VERSION,
                )
        self.assertEqual(self.fixture.authority_fingerprint(), before)

    def test_cli_surfaces_typed_error_instead_of_internal_error(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(TODO_SCRIPT),
                "status",
                "--repo-root",
                str(self.fixture.root),
                "--json",
            ],
            capture_output=True,
            text=True,
            env=self.fixture.environment(),
            check=False,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, int(ExitCode.CONSISTENCY_ERROR))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "schema_migration_required")
        self.assertNotEqual(payload["code"], "internal_error")
        self.assertEqual(
            payload["error"]["details"]["observed_migration_version"],
            OLD_MIGRATION_VERSION,
        )

    def test_writable_status_initialization_migrates_without_revision_change(self) -> None:
        writable_environment = self.fixture.environment()
        writable_environment.pop("TODO_ORCHESTRATOR_READ_ONLY")
        result = subprocess.run(
            [
                sys.executable,
                str(TODO_SCRIPT),
                "status",
                "--repo-root",
                str(self.fixture.root),
                "--json",
            ],
            capture_output=True,
            text=True,
            env=writable_environment,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["data"]["project_revision"], 7)

        os.environ.pop("TODO_ORCHESTRATOR_READ_ONLY")
        writable = Service(self.fixture.root)
        with writable.db.read() as conn:
            versions = [
                int(row[0])
                for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
            ]
        self.assertEqual(versions, list(range(1, DATABASE_MIGRATION_VERSION + 1)))

        second = Service(self.fixture.root)
        self.assertEqual(second.status()["project_revision"], 7)
        with second.db.read() as conn:
            self.assertEqual(
                [int(row[0]) for row in conn.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )],
                versions,
            )

        os.environ["TODO_ORCHESTRATOR_READ_ONLY"] = "1"
        service = Service(self.fixture.root)
        reader = SemanticReader(self.fixture.root)
        self.assertEqual(service.status()["project_revision"], 7)
        self.assertEqual(service.export()["project_revision"], 7)
        self.assertEqual(reader.state()["revision"], 7)
        self.assertTrue(reader.workflow()["available"])


if __name__ == "__main__":
    unittest.main()
