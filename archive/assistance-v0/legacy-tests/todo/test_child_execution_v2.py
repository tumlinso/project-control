from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from todo_orchestrator.child_execution import (  # noqa: E402
    adopt_child_execution,
    authorize_child_execution,
    disposition_child_execution,
    report_child_result,
)
from todo_orchestrator.db import Database  # noqa: E402
from todo_orchestrator.migrations import DATABASE_MIGRATION_VERSION, MIGRATIONS  # noqa: E402
from todo_orchestrator.models import TodoError  # noqa: E402
from todo_orchestrator.ownership import guard_paths  # noqa: E402
from tests.todo.v2_helpers import V2Repo, base_plan, safe_task  # noqa: E402


class ChildExecutionV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        self.repo.apply(base_plan([safe_task("PARENT", "src/owned")]))
        self.claim = self.repo.service.continue_work(task_id="PARENT")
        self.token = self.claim["claim"]["claim_token"]

    def tearDown(self) -> None:
        self.repo.close()

    def mutate(self, operation):
        value, _ = self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="test",
            entity_id="child",
            event_type="test.child.v2",
            payload={},
            operation=lambda conn, revision: operation(conn),
        )
        return value

    def child(self):
        return self.mutate(lambda conn: authorize_child_execution(
            conn,
            self.repo.root,
            self.token,
            objective="bounded writable child",
            scopes=["src/owned/child"],
            gates=[],
        ))

    def test_read_child_records_scope_without_excluding_parent_edits(self) -> None:
        child = self.mutate(lambda conn: authorize_child_execution(
            conn, self.repo.root, self.token, objective="bounded read child",
            scopes=["src/owned/child"], gates=[], access="read",
        ))
        self.assertEqual(child["access"], "read")
        with self.repo.service.db.read() as conn:
            leases = conn.execute(
                "SELECT COUNT(*) FROM child_scope_leases WHERE child_execution_id=?",
                (child["child_execution_id"],),
            ).fetchone()[0]
            self.assertEqual(leases, 0)
            allowed = guard_paths(conn, self.repo.root, self.claim["claim"]["claim_id"], ["src/owned/child/value.py"])
            self.assertEqual(allowed["allowed"], ["src/owned/child/value.py"])
        status = self.mutate(lambda conn: __import__(
            "todo_orchestrator.child_execution", fromlist=["child_execution_status"]
        ).child_execution_status(conn, self.token, child["child_execution_id"]))
        self.assertEqual(status["access"], "read")
        self.assertEqual(status["scopes"][0]["state"], "authorized_read")
        with self.assertRaises(TodoError) as blocked:
            self.mutate(lambda conn: report_child_result(
                conn, child["child_token"], status="succeeded",
                changed_paths=["src/owned/child/value.py"],
            ))
        self.assertEqual(blocked.exception.code, "child_read_only_change")

    def test_ready_result_is_durable_and_blocks_parent_until_rejected(self) -> None:
        child = self.child()
        reported = self.mutate(lambda conn: report_child_result(
            conn,
            child["child_token"],
            status="ready_for_acceptance",
            changed_paths=["src/owned/child/result.py"],
            references={"patch": "/evidence/patch.bin", "source_identity": "/evidence/source.json"},
        ))
        self.assertEqual(reported["state"], "ready_for_acceptance")
        with self.repo.service.db.read() as conn:
            row = conn.execute(
                "SELECT state,result_refs_json FROM child_executions WHERE id=?",
                (child["child_execution_id"],),
            ).fetchone()
            self.assertEqual(row["state"], "ready_for_acceptance")
            with self.assertRaises(TodoError) as blocked:
                guard_paths(conn, self.repo.root, self.claim["claim"]["claim_id"], ["src/owned/child/result.py"])
            self.assertEqual(blocked.exception.code, "active_child_scope")
        rejected = self.mutate(lambda conn: disposition_child_execution(
            conn, self.token, child["child_execution_id"], action="reject",
        ))
        self.assertEqual(rejected["state"], "rejected")
        with self.repo.service.db.read() as conn:
            allowed = guard_paths(conn, self.repo.root, self.claim["claim"]["claim_id"], ["src/owned/child/result.py"])
        self.assertEqual(allowed["allowed"], ["src/owned/child/result.py"])

    def test_ready_result_can_be_adopted_after_parent_release(self) -> None:
        child = self.child()
        self.mutate(lambda conn: report_child_result(
            conn,
            child["child_token"],
            status="ready_for_acceptance",
            changed_paths=["src/owned/child/result.py"],
        ))
        self.repo.service.release(self.token)
        resumed = self.repo.service.continue_work(task_id="PARENT")
        new_token = resumed["claim"]["claim_token"]
        adopted = self.mutate(lambda conn: adopt_child_execution(conn, new_token, child["child_execution_id"]))
        self.assertEqual(adopted["parent_claim_id"], resumed["claim"]["claim_id"])

    def test_migration_from_v1_child_tables_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "old.sqlite3"
            conn = sqlite3.connect(path)
            try:
                for version in range(1, 5):
                    for statement in MIGRATIONS[version].split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    conn.execute(
                        "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
                        (version, "2026-01-01T00:00:00Z"),
                    )
                conn.commit()
            finally:
                conn.close()
            database = Database(path)
            project = {"project_uuid": "migration-test", "project_name": "migration-test"}
            database.initialize(project)
            database.initialize(project)
            with database.read() as migrated:
                columns = {row[1] for row in migrated.execute("PRAGMA table_info(child_executions)")}
                self.assertTrue({
                    "candidate_gates_json", "acceptance_gates_json", "result_refs_json",
                    "access_mode", "authorized_scopes_json",
                } <= columns)
                approval_columns = {
                    row[1] for row in migrated.execute("PRAGMA table_info(live_recovery_approvals)")
                }
                audit_columns = {
                    row[1] for row in migrated.execute("PRAGMA table_info(live_recovery_audit)")
                }
                self.assertTrue({"approval_kind", "context_json"} <= approval_columns)
                self.assertIn("context_json", audit_columns)
                self.assertEqual(
                    migrated.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                    DATABASE_MIGRATION_VERSION,
                )


if __name__ == "__main__":
    unittest.main()
