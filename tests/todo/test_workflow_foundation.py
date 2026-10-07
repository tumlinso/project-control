from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.db import Database
from todo_orchestrator.migrations import DATABASE_MIGRATION_VERSION, MIGRATIONS, PROJECT_SCHEMA_VERSION
from todo_orchestrator.models import TodoError
from todo_orchestrator.workflow.foundation import (
    CHILD_PACKET_BUDGET_BYTES,
    RUN_LEVEL_ACTIONS,
    CapabilityLineage,
    canonical_json,
    child_scope_is_subset,
    content_hash,
    require_bounded_payload,
    require_child_scope_subset,
    schema_contract,
)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "workflow" / "schema-v10.json"


def apply_through(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    for number in range(1, version + 1):
        for statement in MIGRATIONS[number].split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)", (number, "2026-01-01T00:00:00Z"))


class WorkflowMigrationTests(unittest.TestCase):
    def test_forward_migration_from_current_schema_preserves_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.sqlite3"
            conn = sqlite3.connect(path)
            try:
                apply_through(conn, 9)
                conn.execute("INSERT INTO meta(key,value) VALUES('project_revision','7')")
                conn.execute("INSERT INTO tasks(id,kind,title,objective,status,created_at,updated_at) VALUES('LEGACY','task','Legacy','kept','planned','now','now')")
                conn.execute("INSERT INTO sessions(id,label,token_hash,hostname,repo_root,worktree_root,created_at,last_seen_at,state) VALUES('S','legacy','session-hash','host','/repo','/repo','now','now','active')")
                conn.execute("INSERT INTO claims(id,task_id,session_id,token_hash,state,created_at,heartbeat_at,expires_at,baseline_revision) VALUES('C','LEGACY','S','claim-hash','active','now','now','later',7)")
                conn.execute("INSERT INTO child_executions(id,parent_claim_id,task_id,objective,state,created_at) VALUES('CHILD','C','LEGACY','bounded','created','now')")
                conn.execute("INSERT INTO child_attempts(id,child_execution_id,attempt_number,token_hash,state,created_at,heartbeat_at,expires_at) VALUES('ATTEMPT','CHILD',1,'child-hash','created','now','now','later')")
                conn.commit()
            finally:
                conn.close()

            database = Database(path)
            database.initialize({"project_uuid": "project", "project_name": "legacy"})
            database.initialize({"project_uuid": "project", "project_name": "legacy"})
            with database.read() as migrated:
                self.assertEqual(migrated.execute("SELECT objective FROM tasks WHERE id='LEGACY'").fetchone()[0], "kept")
                self.assertEqual(migrated.execute("SELECT state FROM claims WHERE id='C'").fetchone()[0], "active")
                self.assertEqual(migrated.execute("SELECT objective FROM child_executions WHERE id='CHILD'").fetchone()[0], "bounded")
                self.assertEqual(migrated.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 11)
                self.assertEqual(migrated.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "2")

    def test_migration_failure_rolls_back_all_pending_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.sqlite3"
            conn = sqlite3.connect(path)
            try:
                apply_through(conn, 9)
                conn.commit()
            finally:
                conn.close()
            MIGRATIONS[12] = "CREATE TABLE should_rollback(id INTEGER); INVALID SQL"
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    Database(path).initialize({"project_uuid": "project", "project_name": "rollback"})
            finally:
                del MIGRATIONS[12]
            check = sqlite3.connect(path)
            try:
                self.assertEqual(check.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 9)
                self.assertIsNone(check.execute("SELECT name FROM sqlite_master WHERE name='workflow_runs'").fetchone())
                self.assertIsNone(check.execute("SELECT name FROM sqlite_master WHERE name='should_rollback'").fetchone())
            finally:
                check.close()

    def test_concurrent_migration_is_serialized_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.sqlite3"
            seed = sqlite3.connect(path)
            try:
                apply_through(seed, 9)
                seed.execute("PRAGMA journal_mode=WAL")
                seed.commit()
            finally:
                seed.close()
            barrier = threading.Barrier(2)
            errors: list[BaseException] = []

            def initialize() -> None:
                try:
                    barrier.wait()
                    Database(path, retries=20).initialize({"project_uuid": "project", "project_name": "concurrent"})
                except BaseException as error:  # captured for assertion in the main thread
                    errors.append(error)

            threads = [threading.Thread(target=initialize) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            with Database(path).read() as conn:
                versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
            self.assertEqual(versions, list(range(1, DATABASE_MIGRATION_VERSION + 1)))


class WorkflowFoundationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        self.repo.apply(base_plan([
            safe_task("A", "src/a"),
            safe_task("B", "src/b"),
            safe_task("JOIN", "src/join"),
        ]))

    def tearDown(self) -> None:
        self.repo.close()

    def mutate(self, operation):
        return self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="workflow_test",
            entity_id=None,
            event_type="workflow_test.mutated",
            payload={},
            operation=operation,
        )[0]

    def test_fixture_and_version_names_are_separate(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        contract = schema_contract()
        self.assertEqual(DATABASE_MIGRATION_VERSION, fixture["database_migration_version"])
        self.assertEqual(PROJECT_SCHEMA_VERSION, fixture["project_schema_version"])
        self.assertEqual(contract["database_migration_version"], 11)
        self.assertEqual(contract["project_schema_version"], 2)
        with self.repo.service.db.read() as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(set(fixture["required_tables"]) <= tables)

    def test_lane_and_dispatch_constraints_are_transactional(self) -> None:
        claim = self.repo.service.continue_work(task_id="A")
        session_id = claim["session"]["agent_id"]
        claim_id = claim["claim"]["claim_id"]

        def seed(conn, revision):
            conn.execute("INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('RUN','A','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lanes(id,run_id,role,created_at,updated_at,revision) VALUES('LANE','RUN','implementer','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('LANE',0,'A','active','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,context_version,heartbeat_at,created_at,revision) VALUES('D1','LANE',?,?,1,'now','now',?)", (session_id, claim_id, revision))

        self.mutate(seed)
        with self.assertRaises(sqlite3.IntegrityError):
            self.mutate(lambda conn, revision: conn.execute(
                "INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('LANE',1,'B','active','now',?)",
                (revision,),
            ))
        with self.assertRaises(sqlite3.IntegrityError):
            self.mutate(lambda conn, revision: conn.execute(
                "INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,context_version,heartbeat_at,created_at,revision) VALUES('D2','LANE',?,?,1,'now','now',?)",
                (session_id, claim_id, revision),
            ))

    def test_rendezvous_participants_can_only_reference_first_class_lanes(self) -> None:
        def seed(conn, revision):
            conn.execute("INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('RUN','A','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lanes(id,run_id,role,created_at,updated_at,revision) VALUES('LANE','RUN','coordinator','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_rendezvous(id,run_id,mode,join_task_id,created_at,revision) VALUES('RV','RUN','all','JOIN','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_rendezvous_participants(rendezvous_id,lane_id) VALUES('RV','LANE')")

        self.mutate(seed)
        with self.assertRaises(sqlite3.IntegrityError):
            self.mutate(lambda conn, revision: conn.execute(
                "INSERT INTO workflow_rendezvous_participants(rendezvous_id,lane_id) VALUES('RV','LOCAL-CHILD')"
            ))
        with self.repo.service.db.read() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_rendezvous_participants)")}
        self.assertNotIn("child_execution_id", columns)

    def test_capability_storage_is_hash_only_and_class_separated(self) -> None:
        with self.repo.service.db.read() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_capabilities)")}
        self.assertIn("token_hash", columns)
        self.assertNotIn("token", columns)
        parent = CapabilityLineage(
            "first_class", "project", "repo", "session", "claim", "run", "lane", "implementer", "A",
            frozenset({"sync", "accept_child", "test_result"}), 1,
        )
        parent.validate()
        child = CapabilityLineage(
            "child", "project", "repo", None, "claim", None, None, None, "A",
            frozenset({"test_result"}), 1, parent_capability_id="parent", child_execution_id="child",
        )
        child.validate(parent)
        invalid = CapabilityLineage(
            "child", "project", "repo", None, "claim", "run", "lane", "implementer", "A",
            frozenset(RUN_LEVEL_ACTIONS), 1, parent_capability_id="parent", child_execution_id="child",
        )
        with self.assertRaises(TodoError) as error:
            invalid.validate(parent)
        self.assertEqual(error.exception.code, "child_run_authority_forbidden")

    def test_hashes_budgets_and_child_scope_are_stable(self) -> None:
        value = {"kind": "task_brief", "content": ["a", "b"], "version": 1}
        self.assertEqual(canonical_json(value), canonical_json(dict(reversed(list(value.items())))))
        self.assertEqual(content_hash(value), content_hash(json.loads(canonical_json(value))))
        self.assertLess(require_bounded_payload(value, limit=CHILD_PACKET_BUDGET_BYTES), CHILD_PACKET_BUDGET_BYTES)
        with self.assertRaises(TodoError):
            require_bounded_payload({"value": "x" * CHILD_PACKET_BUDGET_BYTES}, limit=CHILD_PACKET_BUDGET_BYTES)
        self.assertTrue(child_scope_is_subset(["src/owned"], ["src/owned/file.py"]))
        self.assertFalse(child_scope_is_subset(["src/owned"], ["src/sibling/file.py"]))
        require_child_scope_subset(["src/owned"], ["src/owned/file.py"])
        with self.assertRaises(TodoError) as error:
            require_child_scope_subset(["src/owned"], ["src/sibling/file.py"])
        self.assertEqual(error.exception.code, "child_scope_expansion")


if __name__ == "__main__":
    unittest.main()
