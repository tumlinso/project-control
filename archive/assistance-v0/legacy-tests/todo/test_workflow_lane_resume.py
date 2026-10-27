from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.workflow.capabilities import WorkflowCapabilityLocator
from todo_orchestrator.workflow.protocol import WorkflowProtocol
from todo_orchestrator.workflow.recovery import RecoveryEngine
from todo_orchestrator.workflow.service import WorkflowKernel


class WorkflowLaneResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        (self.repo.root / "src" / "a").mkdir(parents=True)
        (self.repo.root / "src" / "a" / "unit.txt").write_text("base\n", encoding="utf-8")
        self.repo.apply(base_plan([safe_task("A", "src/a"), safe_task("T", "src/t")]))
        self.locator_temp = tempfile.TemporaryDirectory()
        self.locator = WorkflowCapabilityLocator(Path(self.locator_temp.name))
        self.protocol = WorkflowProtocol(WorkflowKernel(locator=self.locator), self.locator)
        self.old_thread = os.environ.get("CODEX_THREAD_ID")
        os.environ["CODEX_THREAD_ID"] = "lane-resume-test"

    def tearDown(self) -> None:
        if self.old_thread is None:
            os.environ.pop("CODEX_THREAD_ID", None)
        else:
            os.environ["CODEX_THREAD_ID"] = self.old_thread
        self.locator_temp.cleanup()
        self.repo.close()

    def lane_state(self) -> tuple[str, str]:
        with self.repo.service.db.read() as conn:
            lane = conn.execute("SELECT state FROM workflow_lanes WHERE id='compat-v2-main'").fetchone()[0]
            task = conn.execute(
                "SELECT state FROM workflow_lane_tasks WHERE lane_id='compat-v2-main' AND task_id='A'"
            ).fetchone()[0]
        return lane, task

    def add_duplicate_task_run(self) -> None:
        def seed(conn, revision):
            conn.execute(
                "INSERT INTO workflow_runs(id,root_task_id,status,created_at,updated_at,revision) "
                "VALUES('RUN-SECOND','T','active','now','now',?)", (revision,)
            )
            conn.execute(
                "INSERT INTO workflow_lanes(id,run_id,role,state,created_at,updated_at,revision) "
                "VALUES('LANE-SECOND','RUN-SECOND','implementer','ready','now','now',?)", (revision,)
            )
            conn.execute(
                "INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) "
                "VALUES('LANE-SECOND',0,'A','queued','now',?)", (revision,)
            )

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="RUN-SECOND",
            event_type="fixture.duplicate_task_run", payload={}, operation=seed,
        )

    def test_task_only_duplicate_active_runs_requires_explicit_focus_without_mutation(self) -> None:
        self.add_duplicate_task_run()
        before = self.repo.service.db.revision()
        result = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        self.assertEqual(result["status"], "run_selection_required")
        self.assertEqual(result["task_id"], "A")
        self.assertEqual(result["choices"], [{"run_id": "RUN-SECOND"}, {"run_id": "compat-v2"}])
        self.assertIsNone(result["recommended_next_call"])
        self.assertEqual(self.repo.service.db.revision(), before)
        with self.repo.service.db.read() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM claims WHERE state='active'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM workflow_dispatches WHERE state='active'").fetchone())

    def test_explicit_run_focus_claims_its_lane_and_never_resumes_another_run(self) -> None:
        self.add_duplicate_task_run()
        first = self.protocol.next_task(
            repo_root=str(self.repo.root), task_id="A", run_id="RUN-SECOND"
        )
        self.assertEqual((first["status"], first["run_id"], first["lane_id"]), ("claimed", "RUN-SECOND", "LANE-SECOND"))
        second = self.protocol.next_task(repo_root=str(self.repo.root), run_id="compat-v2")
        self.assertEqual(second["status"], "idle")
        self.assertEqual(second["warnings"], ["no_actionable_work"])

    def test_unavailable_explicit_focus_does_not_fall_back_to_compatibility_run(self) -> None:
        before = self.repo.service.db.revision()
        result = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A", run_id="missing")
        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["warnings"], ["run_focus_unavailable"])
        self.assertEqual(result["run_id"], "missing")
        self.assertIsNone(result["recommended_next_call"])
        self.assertEqual(self.repo.service.db.revision(), before)
        with self.repo.service.db.read() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM claims WHERE state='active'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM workflow_dispatches WHERE state='active'").fetchone())

    def test_unambiguous_legacy_task_call_keeps_compatibility_behavior(self) -> None:
        result = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        self.assertEqual((result["status"], result["run_id"], result["lane_id"]),
                         ("claimed", "compat-v2", "compat-v2-main"))

    def test_active_resume_renews_same_claim_and_its_leases(self):
        self.protocol.next_task(repo_root=str(self.repo.root), task_id='A')
        soon = (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat().replace('+00:00', 'Z')
        def seed(conn, revision):
            claim = conn.execute("SELECT * FROM claims WHERE task_id='A' AND state='active'").fetchone()
            conn.execute("UPDATE claims SET expires_at=? WHERE id=?", (soon, claim['id']))
            conn.execute("INSERT INTO named_locks(name,capacity,metadata_json) VALUES('lease-test',1,'{}')")
            conn.execute("INSERT INTO lock_leases(id,lock_name,claim_id,session_id,token_hash,state,acquired_at,heartbeat_at,expires_at) VALUES('LOCK','lease-test',?,?,'lock-hash','active','now','now',?)", (claim['id'], claim['session_id'], soon))
            conn.execute("INSERT INTO resource_classes(id,mode,metadata_json) VALUES('cpu','exclusive','{}')")
            conn.execute("INSERT INTO resource_instances(id,class_id,capacity,metadata_json) VALUES('cpu:0','cpu',1,'{}')")
            conn.execute("INSERT INTO resource_leases(id,instance_id,claim_id,session_id,token_hash,state,hostname,acquired_at,heartbeat_at,expires_at) VALUES('RESOURCE','cpu:0',?,?,'resource-hash','active','test-host','now','now',?)", (claim['id'], claim['session_id'], soon))
            return claim['id']
        claim_id, _ = self.repo.service.db.mutate(actor_session_id=None, entity_type='fixture', entity_id='A', event_type='fixture', payload={}, operation=seed)
        resumed = self.protocol.next_task(repo_root=str(self.repo.root), task_id='A')
        self.assertEqual(resumed['status'], 'resumed')
        with self.repo.service.db.read() as conn:
            claim = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
            self.assertGreater(claim['expires_at'], soon)
            self.assertEqual(claim['state'], 'active')
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM claims WHERE task_id='A'").fetchone()[0], 1)
            for table in ('lock_leases', 'resource_leases'):
                self.assertEqual(conn.execute(f"SELECT expires_at FROM {table} WHERE claim_id=? AND state='active'", (claim_id,)).fetchone()[0], claim['expires_at'])

    def test_expired_resume_does_not_revive_claim(self):
        self.protocol.next_task(repo_root=str(self.repo.root), task_id='A')
        self.repo.service.db.mutate(actor_session_id=None, entity_type='fixture', entity_id='A', event_type='fixture', payload={}, operation=lambda conn, rev: conn.execute("UPDATE claims SET expires_at='2000-01-01T00:00:00Z' WHERE task_id='A'"))
        result = self.protocol.next_task(repo_root=str(self.repo.root), task_id='A')
        self.assertEqual(result['status'], 'idle')
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT expires_at FROM claims WHERE task_id='A'").fetchone()[0], '2000-01-01T00:00:00Z')

    def test_renewal_rejects_foreign_session_without_mutation(self):
        from todo_orchestrator.claims import renew_claim_for_session
        from todo_orchestrator.models import TodoError
        self.protocol.next_task(repo_root=str(self.repo.root), task_id='A')
        with self.repo.service.db.read() as conn:
            before = dict(conn.execute("SELECT * FROM claims WHERE task_id='A'").fetchone())
            with self.assertRaises(TodoError) as caught:
                renew_claim_for_session(conn, before['id'], 'foreign-session', 7200)
            self.assertEqual(caught.exception.code, 'claim_not_resumable')
            self.assertEqual(dict(conn.execute("SELECT * FROM claims WHERE task_id='A'").fetchone()), before)

    def test_release_and_handoff_requeue_the_same_serial_head(self) -> None:
        for action in ("release", "handoff"):
            with self.subTest(action=action):
                claimed = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
                finished = self.protocol.finish_task(
                    workflow_handle=claimed["workflow_handle"], action=action,
                    disposition="validated", reason="nonterminal", note="preserved handoff",
                )
                self.assertTrue(finished["terminal"])
                self.assertEqual(self.lane_state(), ("ready", "queued"))
                resumed = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
                self.assertEqual(resumed["status"], "claimed")
                self.protocol.finish_task(
                    workflow_handle=resumed["workflow_handle"], action="release",
                    disposition="validated", reason="next subtest",
                )

    def test_block_requeues_but_keeps_lane_explicitly_attention_required(self) -> None:
        claimed = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        self.protocol.finish_task(
            workflow_handle=claimed["workflow_handle"], action="block",
            disposition="failed", reason="needs owner", note="blocked",
        )
        self.assertEqual(self.lane_state(), ("attention_required", "queued"))
        self.assertEqual(self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")["status"], "idle")
        engine = RecoveryEngine(
            self.repo.service.db, self.repo.root, str(self.repo.service.project["project_uuid"]),
            process_probe=lambda hostname, pid, process_start: False,
        )
        plan = engine.inspect("A")
        self.assertEqual([item["kind"] for item in plan["actions"]], ["requeue_blocked_task"])
        recovered = engine.execute(plan, "resolved the recorded blocking condition")
        self.assertEqual(recovered["resume"], "next_task")
        self.assertEqual(self.lane_state(), ("ready", "queued"))
        resumed = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        self.assertEqual(resumed["status"], "claimed")

    def test_task_local_requeue_ignores_unrelated_read_shared_coordinator_lock(self) -> None:
        claimed = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        self.protocol.finish_task(
            workflow_handle=claimed["workflow_handle"], action="block",
            disposition="failed", reason="external dependency pending", note="blocked",
        )
        coordinator = self.repo.service.continue_work(task_id="T")

        def seed(conn, revision):
            conn.execute("INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('COORD-RUN','T','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lanes(id,run_id,role,workspace_mode,state,created_at,updated_at,revision) VALUES('COORD-LANE','COORD-RUN','coordinator','read_shared','active','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,context_version,heartbeat_at,created_at,revision) VALUES('COORD-D','COORD-LANE',?,?,1,'2999-01-01T00:00:00Z','now',?)", (coordinator['session']['agent_id'], coordinator['claim']['claim_id'], revision))
            conn.execute("INSERT INTO named_locks(name,capacity,metadata_json) VALUES('coordinator-seat',1,'{}')")
            conn.execute("INSERT INTO lock_leases(id,lock_name,claim_id,session_id,token_hash,state,acquired_at,heartbeat_at,expires_at) VALUES('COORD-L','coordinator-seat',?,?, 'lock','active','now','now','2999-01-01T00:00:00Z')", (coordinator['claim']['claim_id'], coordinator['session']['agent_id']))

        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="coordinator", event_type="fixture", payload={}, operation=seed)
        engine = RecoveryEngine(self.repo.service.db, self.repo.root, str(self.repo.service.project["project_uuid"]), process_probe=lambda *_: None)
        plan = engine.inspect("A")
        self.assertEqual(plan["blockers"], [])
        self.assertEqual([item["kind"] for item in plan["actions"]], ["requeue_blocked_task"])

    def test_clean_owner_recovery_requeues_and_next_task_reissues_capability(self) -> None:
        claimed = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        engine = RecoveryEngine(
            self.repo.service.db, self.repo.root, str(self.repo.service.project["project_uuid"]),
            process_probe=lambda hostname, pid, process_start: False,
        )
        result = engine.execute(engine.inspect("A"), "stopped clean first-class worker")
        self.assertEqual(result["resume"], "next_task")
        self.assertFalse(result["files_mutated"])
        self.assertEqual(self.lane_state(), ("ready", "queued"))
        resumed = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        self.assertEqual(resumed["status"], "claimed")
        self.assertNotEqual(resumed["workflow_handle"], claimed["workflow_handle"])

    def test_dirty_owner_recovery_preserves_files_and_lane_attention(self) -> None:
        self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        dirty = self.repo.root / "src" / "a" / "unit.txt"
        dirty.write_text("preserve dirty\n", encoding="utf-8")
        before = dirty.read_bytes()
        engine = RecoveryEngine(
            self.repo.service.db, self.repo.root, str(self.repo.service.project["project_uuid"]),
            process_probe=lambda hostname, pid, process_start: False,
        )
        result = engine.execute(engine.inspect("A"), "paused dirty first-class worker")
        self.assertEqual(dirty.read_bytes(), before)
        self.assertIn("A", result["dirty_tasks"])
        self.assertEqual(self.lane_state(), ("attention_required", "queued"))
        self.assertEqual(self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")["status"], "idle")


if __name__ == "__main__":
    unittest.main()
