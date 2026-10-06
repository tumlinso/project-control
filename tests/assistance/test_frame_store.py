from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from project_control.assistance.frames import (
    ChildFrameSpec,
    FrameError,
    FrameStore,
    MAX_PRIVATE_FRAMES,
)


class FrameStoreTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE execution_slots(job TEXT PRIMARY KEY, attempt INTEGER NOT NULL)")
        FrameStore.initialize(self.db)
        self.store = FrameStore(self.db)
        self.scope = {"principal": "alice", "profile": "observer", "project": "repo"}
        with self.db:
            self.store.register_root("root", self.scope, 500.0)

    def tearDown(self):
        self.db.close()

    def wait(self, parent="root", generation=1, ids=("child",), *, scope=None, deadline=400):
        scope = scope or self.scope
        with self.db:
            return self.store.register_wait(parent, generation, [
                ChildFrameSpec(job_id=child, scope=scope, deadline=deadline, question="inspect source")
                for child in ids
            ], now=100.0)

    def set_running(self, job_id, attempt=1):
        self.db.execute("UPDATE pa1_frames SET state='running' WHERE job_id=?", (job_id,))
        self.db.execute("INSERT OR REPLACE INTO execution_slots(job,attempt) VALUES(?,?)", (job_id, attempt))

    def test_lost_wake_reconciliation_and_duplicate_terminal_delivery(self):
        self.wait()
        # The child may finish before the parent starts polling for its wake.
        with self.db:
            self.assertTrue(self.store.record_terminal("child", 0, "completed", {"answer": "done"}, now=120))
            self.assertFalse(self.store.record_terminal("child", 0, "completed", {"answer": "done"}, now=121))
            outcome = self.store.reconcile("root", 1)
            self.assertTrue(outcome.ready)
            self.assertEqual(outcome.wake_version, 1)
            duplicate = self.store.reconcile("root", 1)
            self.assertEqual(duplicate, outcome)
            self.assertTrue(self.store.prepare_wake_ack("root", 1, ["child"]))
            self.assertTrue(self.store.mark_parent_released("root", 1, now=122))
            consumed = self.store.consume_wake("root", 1, outcome.wake_version)
            self.assertEqual(consumed.children[0].result, {"answer": "done"})
            self.assertIsNone(self.store.consume_wake("root", 1, outcome.wake_version))
            self.store.register_wait("root", 2, [ChildFrameSpec("next-child", self.scope, 300)], now=130)
            self.assertIsNone(self.store.consume_wake("root", 1, outcome.wake_version))
            self.assertEqual(self.store.get_frame("root").state, "waiting")

    def test_release_is_armed_only_after_parent_execution_slot_is_gone(self):
        self.wait()
        self.db.execute("INSERT INTO execution_slots VALUES('root',1)")
        with self.db:
            self.assertFalse(self.store.mark_parent_released("root", 99, now=110))
            self.assertFalse(self.store.mark_parent_released("root", 1, now=110))
        self.assertFalse(self.store.eligible("child", 110))
        self.db.execute("DELETE FROM execution_slots WHERE job='root'")
        with self.db:
            self.assertTrue(self.store.mark_parent_released("root", 1, now=111))
            self.assertFalse(self.store.mark_parent_released("root", 1, now=112))
        self.assertTrue(self.store.eligible("child", 111))

    def test_scope_deadline_cycle_credit_depth_and_global_capacity_fail_closed(self):
        with self.assertRaises(FrameError):
            self.wait(ids=("root",))
        with self.assertRaises(FrameError):
            self.wait(scope={"principal": "alice", "profile": "observer", "project": "other"})
        with self.assertRaises(FrameError):
            self.wait(deadline=501)
        self.wait(ids=("c1", "c2", "c3", "c4"))
        with self.db:
            for child in ("c1", "c2", "c3", "c4"):
                self.store.record_terminal(child, 0, "completed", {"answer": child}, now=120)
            outcome = self.store.reconcile("root", 1)
            self.assertTrue(outcome.ready)
            self.store.prepare_wake_ack("root", 1, ["c1", "c2", "c3", "c4"])
            self.store.mark_parent_released("root", 1, now=121)
            self.store.consume_wake("root", 1, outcome.wake_version)
        # Root credits remain spent even after consuming the wait.
        with self.assertRaises(FrameError):
            self.wait(generation=2, ids=("c5",))
        # A child can create one final generation, but that grandchild cannot
        # add a third descendant level.
        with self.db:
            with self.assertRaises(FrameError):
                self.store.register_wait("c1", 1, [ChildFrameSpec("grandchild", self.scope, 300)], now=120)

        with self.db:
            self.store.register_root("depth-root", self.scope, 500)
            self.store.register_wait("depth-root", 1, [ChildFrameSpec("depth-child", self.scope, 400)], now=100)
            self.store.register_wait("depth-child", 1, [ChildFrameSpec("depth-grandchild", self.scope, 300)], now=110)
            with self.assertRaises(FrameError):
                self.store.register_wait("depth-grandchild", 1, [ChildFrameSpec("depth-great-grandchild", self.scope, 200)], now=120)

    def test_global_private_frame_limit_spans_roots(self):
        # Eight roots can each admit four descendants; a ninth root is refused.
        for n in range(1, 9):
            with self.db:
                self.store.register_root(f"root{n}", self.scope, 500)
                self.store.register_wait(f"root{n}", 1, [
                    ChildFrameSpec(f"child{n}-{i}", self.scope, 400) for i in range(4)
                ], now=100)
        self.assertEqual(self.db.execute("SELECT count(*) FROM pa1_frames WHERE parent_id IS NOT NULL").fetchone()[0], MAX_PRIVATE_FRAMES)
        with self.db:
            self.store.register_root("root9", self.scope, 500)
            with self.assertRaises(FrameError):
                self.store.register_wait("root9", 1, [ChildFrameSpec("extra", self.scope, 400)], now=100)

    def test_cumulative_turns_and_stale_generation_are_fenced(self):
        with self.db, self.assertRaises(FrameError):
            self.store.consume_turns("root", 0, 1, attempt=1, now=100)
        with self.db:
            self.set_running("root")
            with self.assertRaises(FrameError):
                self.store.consume_turns("root", 0, 1, attempt=0, now=100)
            self.store.consume_turns("root", 0, 4, attempt=1, now=101)
            self.store.consume_turns("root", 0, 2, attempt=1, now=102)
            with self.assertRaises(FrameError):
                self.store.consume_turns("root", 0, 1, attempt=1, now=103)
            self.assertEqual(self.store.remaining_turns("root"), 0)
            self.store.register_root("stale-root", self.scope, 500)
            self.store.register_wait("stale-root", 1, [ChildFrameSpec("stale-child", self.scope, 400)], now=100)
        with self.db:
            self.assertFalse(self.store.record_terminal("stale-child", 4, "completed", {"answer": "stale"}, now=120))
            self.assertIsNone(self.store.reconcile("stale-root", 0))

    def test_child_and_parent_resume_reservations_share_one_root_budget(self):
        with self.db:
            self.set_running("root")
            self.store.consume_turns("root", 0, 2, attempt=1, now=101)
            self.store.register_wait("root", 1, [
                ChildFrameSpec("budget-child", self.scope, 400, turn_reservation=2)
            ], now=110)
            root = self.store.get_frame("root")
            self.assertEqual((root.root_turns_used, root.root_turns_reserved), (2, 3))
            self.assertEqual(self.store.remaining_turns("root"), 1)
            self.db.execute("DELETE FROM execution_slots WHERE job='root'")
            self.assertTrue(self.store.mark_parent_released("root", 1, now=111))
            self.set_running("budget-child")
            self.store.consume_turns("budget-child", 0, 2, attempt=1, now=120)
            self.assertEqual(self.store.get_frame("root").root_turns_used, 4)
            self.store.record_terminal("budget-child", 0, "completed", {"answer": "ok"}, now=130)
            self.db.execute("DELETE FROM execution_slots WHERE job='budget-child'")
            self.assertEqual(self.store.get_frame("root").root_turns_reserved, 1)
            outcome = self.store.reconcile("root", 1)
            self.store.prepare_wake_ack("root", 1, ["budget-child"])
            self.assertTrue(self.store.consume_wake("root", 1, outcome.wake_version))
            self.set_running("root", attempt=2)
            self.store.consume_turns("root", 1, 1, attempt=2, now=140)
            root = self.store.get_frame("root")
            self.assertEqual((root.root_turns_used, root.root_turns_reserved), (5, 0))
            with self.assertRaises(FrameError):
                self.store.consume_turns("root", 1, 2, attempt=2, now=150)

    def test_cancelled_child_wakes_parent_with_explicit_failure(self):
        self.wait()
        with self.db:
            self.assertTrue(self.store.record_terminal("child", 0, "cancelled", {"reason": "cancelled"}, now=120))
            outcome = self.store.reconcile("root", 1)
        self.assertTrue(outcome.ready)
        self.assertEqual(outcome.children[0].status, "cancelled")
        self.assertEqual(outcome.children[0].result, {"reason": "cancelled"})

    def test_private_output_rejects_hidden_reasoning_fields(self):
        self.wait()
        with self.db, self.assertRaises(FrameError):
            self.store.record_terminal("child", 0, "completed", {"analysis": "private"}, now=120)

    def test_origin_is_typed_immutable_and_inherited(self):
        with self.db:
            self.store.register_root("automatic-root", self.scope, 500, origin_class="automatic")
            self.store.register_root("automatic-root", self.scope, 500, origin_class="automatic")
            with self.assertRaises(FrameError):
                self.store.register_root("automatic-root", self.scope, 500, origin_class="public_demand")
            with self.assertRaises(FrameError):
                self.store.register_root("invalid-root", self.scope, 500, origin_class="user supplied")
            self.store.register_wait("automatic-root", 1, [
                ChildFrameSpec("automatic-child", self.scope, 400)
            ], now=100)
            self.assertEqual(self.store.get_frame("automatic-root").origin_class, "automatic")
            self.assertEqual(self.store.get_frame("automatic-child").origin_class, "automatic")

    def test_additive_migration_defaults_legacy_roots_to_public_demand(self):
        db = sqlite3.connect(":memory:")
        db.execute("""CREATE TABLE pa1_frames(
            job_id TEXT PRIMARY KEY,root_id TEXT NOT NULL,parent_id TEXT,generation INTEGER NOT NULL DEFAULT 0,
            depth INTEGER NOT NULL,scope TEXT NOT NULL,deadline REAL NOT NULL,turn_limit INTEGER NOT NULL,
            turns_used INTEGER NOT NULL DEFAULT 0,turns_reserved INTEGER NOT NULL DEFAULT 0,
            root_turns_reserved INTEGER NOT NULL DEFAULT 0,credit_limit INTEGER NOT NULL DEFAULT 0,
            credit_used INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'queued',
            terminal_version INTEGER NOT NULL DEFAULT 0,terminal_status TEXT,terminal_result TEXT,
            updated REAL NOT NULL DEFAULT 0)""")
        db.execute("""CREATE TABLE pa1_frame_waits(parent_id TEXT NOT NULL,generation INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'waiting',release_verified INTEGER NOT NULL DEFAULT 0,wake_version INTEGER,
            wake_consumed INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(parent_id,generation))""")
        db.execute("""INSERT INTO pa1_frames(job_id,root_id,parent_id,generation,depth,scope,deadline,
            turn_limit,credit_limit) VALUES('legacy','legacy',NULL,0,0,?,500,6,4)""",
                   ('{"principal":"alice","profile":"observer","project":"repo"}',))
        FrameStore.initialize(db)
        store = FrameStore(db)
        self.assertEqual(store.get_frame("legacy").origin_class, "public_demand")
        self.assertIn("pending_ack", {row["name"] for row in db.execute("PRAGMA table_info(pa1_frame_waits)")})
        db.close()

    def test_ack_survives_restart_then_consumes_once_after_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "frames.sqlite"
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE execution_slots(job TEXT PRIMARY KEY, attempt INTEGER NOT NULL)")
            FrameStore.initialize(db)
            store = FrameStore(db)
            with db:
                store.register_root("restart-root", self.scope, 500)
                store.register_wait("restart-root", 1, [ChildFrameSpec("restart-child", self.scope, 400)], now=100)
                store.record_terminal("restart-child", 0, "completed", {"answer": "survives"}, now=120)
                outcome = store.reconcile("restart-root", 1)
                self.assertTrue(store.prepare_wake_ack("restart-root", 1, ["restart-child"]))
            db.close()

            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            FrameStore.initialize(db)
            store = FrameStore(db)
            with db:
                self.assertEqual(store.pending_wake_ack("restart-root", 1), ("restart-child",))
                self.assertTrue(store.mark_parent_released("restart-root", 1, now=121))
                resumed = store.consume_wake("restart-root", 1, outcome.wake_version)
                self.assertEqual(resumed.children[0].result, {"answer": "survives"})
                self.assertIsNone(store.pending_wake_ack("restart-root", 1))
                self.assertTrue(store.is_private("restart-child"))
                self.assertIsNone(store.get_frame("restart-child"))
                self.assertIsNone(store.consume_wake("restart-root", 1, outcome.wake_version))
            db.close()

    def test_ack_waits_for_child_physical_cleanup(self):
        self.wait()
        with self.db:
            self.set_running("child")
            self.store.record_terminal("child", 0, "completed", {"answer": "done"}, now=120)
            outcome = self.store.reconcile("root", 1)
            self.store.prepare_wake_ack("root", 1, ["child"])
            self.assertTrue(self.store.mark_parent_released("root", 1, now=121))
            self.assertIsNone(self.store.consume_wake("root", 1, outcome.wake_version))
            self.assertIsNotNone(self.store.get_frame("child"))
            self.db.execute("DELETE FROM execution_slots WHERE job='child'")
            self.assertIsNotNone(self.store.consume_wake("root", 1, outcome.wake_version))
            self.assertTrue(self.store.is_private("child"))

    def test_cancelled_parent_ack_cleans_without_resurrection_and_late_turn_is_charged(self):
        self.wait()
        with self.db:
            self.set_running("child")
            self.assertTrue(self.store.record_terminal("child", 0, "cancelled", {"reason": "cancelled"}, now=120))
            self.assertEqual(self.store.consume_turns("child", 0, 1, attempt=1, now=900), 1)
            self.assertEqual(self.store.get_frame("root").root_turns_used, 1)
            self.db.execute("DELETE FROM execution_slots WHERE job='child'")
            outcome = self.store.reconcile("root", 1)
            self.assertTrue(self.store.prepare_wake_ack("root", 1, ["child"]))
            self.assertTrue(self.store.mark_parent_released("root", 1, now=123))
            self.set_running("root", attempt=2)
            self.assertTrue(self.store.record_terminal("root", 1, "cancelled", {"reason": "cancelled"}, now=124))
            self.db.execute("DELETE FROM execution_slots WHERE job='root'")
            self.assertEqual(self.store.get_frame("root").state, "cancelled")
            self.assertTrue(self.store.consume_wake("root", 1, outcome.wake_version))
            self.assertEqual(self.store.get_frame("root").state, "cancelled")

    def test_terminal_parent_retirement_requires_no_slot_and_terminal_children(self):
        self.wait()
        with self.db:
            self.store.record_terminal("root", 1, "failed", {"reason": "failed"}, now=120)
            self.assertFalse(self.store.retire_wait("root", 1))
            self.store.record_terminal("child", 0, "cancelled", {"reason": "parent_failed"}, now=121)
            self.db.execute("INSERT INTO execution_slots VALUES('root',1)")
            self.assertFalse(self.store.retire_wait("root", 1))
            self.db.execute("DELETE FROM execution_slots WHERE job='root'")
            self.assertTrue(self.store.retire_wait("root", 1))
            self.assertTrue(self.store.is_private("child"))
            self.assertIsNone(self.store.get_frame("child"))
            self.assertEqual(self.store.get_frame("root").state, "failed")


if __name__ == "__main__":
    unittest.main()
