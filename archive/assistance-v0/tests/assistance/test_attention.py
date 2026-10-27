import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore
from project_control.assistance.attention import AttentionController, AttentionError, FocusConfig
from project_control.assistance.power import PowerPolicy, trusted_operator_control
from project_control.assistance import attention as attention_module


class FakeClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


class FakeBroker:
    def __init__(self):
        self.calls = []
        self.results = {}
        self.before_enqueue = None
        self.by_fingerprint = {}
        self.lose_first_reply = False

    def enqueue_preparation(self, **kwargs):
        if self.before_enqueue:
            self.before_enqueue(kwargs)
        self.calls.append(kwargs)
        prior = self.by_fingerprint.get(kwargs["input_fingerprint"])
        if prior:
            return {"accepted": True, "job_id": prior, "status": "existing"}
        job_id = "job-" + str(len(self.calls))
        self.by_fingerprint[kwargs["input_fingerprint"]] = job_id
        if self.lose_first_reply:
            self.lose_first_reply = False
            raise TimeoutError("reply_lost_after_admission")
        return {"accepted": True, "job_id": job_id, "status": "queued"}

    def preparation_lookup(self, job_id, *, access_scope):
        self.lookup_scopes = getattr(self, "lookup_scopes", []) + [dict(access_scope)]
        return self.results.get(job_id, {"status": "pending"})


class FakeNotebook:
    def __init__(self):
        self.notes = []

    def put_note(self, record, *, access_scope):
        self.notes.append((dict(record), dict(access_scope)))
        return dict(record)


class AttentionControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        (self.root / "src.py").write_text("first\n")
        self.db = sqlite3.connect(":memory:")
        self.clock = FakeClock()
        self.power = PowerPolicy(self.db, clock=self.clock)
        self.control = trusted_operator_control()
        self.broker = FakeBroker()
        self.notebook = FakeNotebook()
        self.scope = {"project": "alpha", "principal": "operator", "paths": ["src.py"]}
        self.controller = AttentionController(
            self.db, power_policy=self.power, trusted_roots={"alpha": self.root},
            repository_for=lambda _project: "repo",
            access_scope=lambda _project: self.scope, broker=self.broker,
            notebook=self.notebook, clock=self.clock)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def configure(self, *, focus_id="focus-a", paths=("src.py",), automatic=True,
                  starts=100, expires=300):
        config = FocusConfig(focus_id, "alpha", tuple(paths), starts, expires,
                             automatic, "goal-user-1")
        self.controller.configure_focus(self.control, config)
        # Trusted operator adapter is responsible for PowerPolicy grants.
        self.power.set_focus_window(self.control, focus_id=focus_id, project="alpha",
                                    starts_at=starts, expires_at=expires)
        self.power.set_automatic(self.control, automatic)
        return config

    def change(self, value="second\n"):
        (self.root / "src.py").write_text(value)

    def test_focus_defaults_and_restart_reconciliation_do_not_dispatch(self):
        state_path = Path(self.tmp.name) / "state.sqlite"
        first_db = sqlite3.connect(state_path)
        first = AttentionController(first_db, power_policy=PowerPolicy(first_db, clock=self.clock),
            trusted_roots={"alpha": self.root}, repository_for=lambda _p: "repo",
            access_scope=lambda _p: self.scope,
            broker=self.broker, notebook=self.notebook, clock=self.clock)
        config = FocusConfig("focus-a", "alpha", ("src.py",), 100, 200)
        first.configure_focus(self.control, config)
        first_db.close()
        self.change("edited while stopped\n")
        resumed_db = sqlite3.connect(state_path)
        resumed = AttentionController(resumed_db, power_policy=PowerPolicy(resumed_db, clock=self.clock),
            trusted_roots={"alpha": self.root}, repository_for=lambda _p: "repo",
            access_scope=lambda _p: self.scope,
            broker=self.broker, notebook=self.notebook, clock=self.clock)
        self.assertIsNone(resumed.read_focus("focus-a")["goal_card_id"])
        self.assertEqual(resumed.scan("focus-a")["status"], "nominated")
        resumed_db.close()
        self.assertEqual(self.broker.calls, [])
        self.assertFalse(config.permit_automatic_inference)

    def test_edit_burst_coalesces_and_dispatches_only_after_debounce(self):
        self.configure()
        self.change("second\n")
        self.assertEqual(self.controller.scan("focus-a")["status"], "nominated")
        candidate = self.controller.candidates()[0]
        self.assertEqual(self.controller.dispatch_next(self.control)["status"], "empty")
        self.clock.value += 1
        self.change("third\n")
        self.assertEqual(self.controller.scan("focus-a")["status"], "coalesced")
        self.clock.value += 2.1
        dispatched = self.controller.dispatch_next(self.control)
        self.assertEqual(dispatched["status"], "dispatched")
        self.assertEqual(len(self.broker.calls), 1)
        self.assertEqual(self.broker.calls[0]["focus_id"], "focus-a")
        self.assertEqual(self.broker.calls[0]["access_scope"], self.scope)
        self.assertEqual(self.broker.calls[0]["input_fingerprint"],
                         self.controller.candidates()[0].input_fingerprint)
        self.assertEqual(self.controller.read_focus("focus-a")["reserved_turns"], 6)
        self.assertEqual(self.controller.read_focus("focus-a")["active_seconds"], 300)
        self.assertLessEqual(self.broker.calls[0]["window_deadline"], 300)
        self.assertNotIn("goal-user-1", self.broker.calls[0]["question"])
        self.assertNotEqual(candidate.input_fingerprint,
                            self.controller.candidates()[0].input_fingerprint)

    def test_focus_expiry_quiet_and_release_veto_block_dispatch(self):
        self.configure()
        self.change()
        self.controller.scan("focus-a")
        self.clock.value += 3
        self.power.set_quiet(self.control, expires_at=150, focus_id="focus-a")
        self.db.commit()
        self.assertEqual(self.controller.dispatch_next(self.control)["reason"], "quiet_active")
        self.power.set_quiet(self.control, expires_at=None)
        self.db.commit()
        self.power.set_release(self.control, reason="operator-release")
        self.db.commit()
        self.assertEqual(self.controller.dispatch_next(self.control)["reason"], "inference_release_veto")
        self.power.resume(self.control)
        self.db.commit()
        self.clock.value = 300
        self.assertEqual(self.controller.dispatch_next(self.control)["reason"], "focus_expired")
        self.assertEqual(self.broker.calls, [])

    def test_changed_source_after_debounce_is_deferred(self):
        self.configure()
        self.change("changed\n")
        self.controller.scan("focus-a")
        self.clock.value += 3
        self.change("changed again\n")
        result = self.controller.dispatch_next(self.control)
        self.assertEqual(result, {"status": "deferred", "reason": "source_changed_during_debounce"})
        self.assertEqual(self.broker.calls, [])

    def test_path_denies_private_generated_and_symlink_escape(self):
        for path in (".git/config", "outputs/result.json", "../outside", "cache/result.pyc"):
            with self.subTest(path=path), self.assertRaises(AttentionError):
                FocusConfig("f", "alpha", (path,), 100, 200).validated()
        outside = Path(self.tmp.name) / "outside.py"
        outside.write_text("secret\n")
        (self.root / "link.py").symlink_to(outside)
        with self.assertRaisesRegex(AttentionError, "symlink"):
            self.configure(paths=("link.py",))

    def test_default_fingerprint_denies_file_symlink_replacement_without_external_read(self):
        outside = Path(self.tmp.name) / "outside-file.py"
        outside.write_text("external secret bytes")
        source = self.root / "src.py"
        original_open = os.open
        original_read = os.read
        replaced = False
        reads = []

        def swap_then_open(path, flags, *args, **kwargs):
            nonlocal replaced
            if path == "src.py" and not replaced:
                source.rename(self.root / "src.real")
                source.symlink_to(outside)
                replaced = True
            return original_open(path, flags, *args, **kwargs)

        def track_read(fd, count):
            reads.append(fd)
            return original_read(fd, count)

        with patch.object(attention_module.os, "open", side_effect=swap_then_open), \
             patch.object(attention_module.os, "read", side_effect=track_read):
            with self.assertRaisesRegex(AttentionError, "symlink"):
                self.controller._digest("alpha", "src.py")
        self.assertTrue(replaced)
        self.assertEqual(reads, [])

    def test_default_fingerprint_denies_parent_symlink_replacement_without_external_read(self):
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "src.py").write_text("inside")
        outside = Path(self.tmp.name) / "outside-directory"
        outside.mkdir()
        (outside / "src.py").write_text("external secret bytes")
        original_open = os.open
        original_read = os.read
        replaced = False
        reads = []

        def swap_then_open(path, flags, *args, **kwargs):
            nonlocal replaced
            if path == "nested" and not replaced:
                nested.rename(self.root / "nested.real")
                nested.symlink_to(outside, target_is_directory=True)
                replaced = True
            return original_open(path, flags, *args, **kwargs)

        def track_read(fd, count):
            reads.append(fd)
            return original_read(fd, count)

        with patch.object(attention_module.os, "open", side_effect=swap_then_open), \
             patch.object(attention_module.os, "read", side_effect=track_read):
            with self.assertRaisesRegex(AttentionError, "symlink"):
                self.controller._digest("alpha", "nested/src.py")
        self.assertTrue(replaced)
        self.assertEqual(reads, [])

    def test_fingerprint_dismissal_suppresses_repeat_and_changed_focus_can_nominate(self):
        self.configure()
        self.change("second\n")
        self.controller.scan("focus-a")
        candidate = self.controller.candidates()[0]
        self.controller.dismiss(project="alpha", focus_id="focus-a",
                                fingerprint=candidate.input_fingerprint)
        # A later byte change creates a different fingerprint and remains eligible.
        self.change("third\n")
        self.assertEqual(self.controller.scan("focus-a")["status"], "nominated")

    def test_declared_determinant_change_nominates_without_file_edits(self):
        values = {"model_profile": "profile-a"}
        self.controller = AttentionController(self.db, power_policy=self.power,
            trusted_roots={"alpha": self.root}, repository_for=lambda _p: "repo",
            access_scope=lambda _p: self.scope,
            broker=self.broker, notebook=self.notebook, clock=self.clock,
            resolve_determinants=lambda _p: dict(values))
        self.configure()
        self.assertEqual(self.controller.scan("focus-a")["status"], "unchanged")
        values["model_profile"] = "profile-b"
        self.assertEqual(self.controller.scan("focus-a")["status"], "nominated")

    def test_broker_completion_is_rechecked_and_only_original_packets_are_written(self):
        self.configure()
        self.change("second\n")
        self.controller.scan("focus-a")
        self.clock.value += 3
        admitted = self.controller.dispatch_next(self.control)
        self.assertEqual(admitted["status"], "dispatched")
        candidate = admitted["candidate_id"]
        self.assertEqual(self.controller.record_result(candidate_id=candidate,
                                                       control=self.control)["status"],
                         "pending")
        self.assertEqual(self.notebook.notes, [])
        source_digest = hashlib.sha256((self.root / "src.py").read_bytes()).hexdigest()
        self.broker.results[admitted["job_id"]] = {
            "status": "completed", "answer": "Visible preparation",
            "findings": [], "evidence_packets": ["pkt_original_1"],
            "sources": [{"project": "alpha", "repository": "repo", "path": "src.py",
                         "content_sha256": source_digest}],
            "dependencies": {"file:repo/src.py": source_digest}, "unresolved_questions": []}
        saved = self.controller.record_result(candidate_id=candidate, control=self.control)
        self.assertEqual(saved["status"], "stored_advisory")
        self.assertEqual(self.notebook.notes[0][0]["provenance"], "model")
        self.assertEqual(self.notebook.notes[0][0]["evidence_packets"], ["pkt_original_1"])
        self.assertFalse(self.notebook.notes[0][0]["is_independent_evidence"])

    def test_lost_broker_reply_retries_same_identity_without_double_reservation(self):
        self.configure()
        self.change("second\n")
        self.controller.scan("focus-a")
        candidate_id = self.controller.candidates()[0].candidate_id
        self.clock.value += 3
        self.broker.lose_first_reply = True
        self.assertEqual(self.controller.dispatch_next(self.control)["status"], "admission_unknown")
        before = self.controller.read_focus("focus-a")
        self.assertEqual((before["reserved_turns"], before["active_seconds"]), (6, 300))
        # Ordinary dispatch never retries an uncertain callback. Only explicit
        # reconciliation resends the immutable persisted request.
        self.assertEqual(self.controller.dispatch_next(self.control)["status"], "empty")
        self.assertEqual(len(self.broker.calls), 1)
        self.assertEqual(self.controller.reconcile_admission(candidate_id)["status"], "dispatched")
        after = self.controller.read_focus("focus-a")
        self.assertEqual((after["reserved_turns"], after["active_seconds"]), (6, 300))
        self.assertEqual(self.broker.calls[0], self.broker.calls[1])

    def test_two_connections_claim_one_candidate_and_reserve_near_budget_limit_once(self):
        for initial_turns, initial_seconds in ((0, 0), (18, 600)):
            with self.subTest(initial_turns=initial_turns, initial_seconds=initial_seconds):
                state_path = Path(self.tmp.name) / f"concurrent-{initial_turns}.sqlite"
                db1 = sqlite3.connect(state_path, timeout=5, check_same_thread=False)
                clock = FakeClock(100)
                control = trusted_operator_control()
                power1 = PowerPolicy(db1, clock=clock)
                broker = FakeBroker()
                scope = {"project": "alpha", "principal": "operator", "profile": "default"}
                ctrl1 = AttentionController(db1, power_policy=power1,
                    trusted_roots={"alpha": self.root}, repository_for=lambda _p: "repo",
                    access_scope=lambda _p: scope, broker=broker, notebook=FakeNotebook(), clock=clock)
                config = FocusConfig("focus-race", "alpha", ("src.py",), 100, 500, True)
                ctrl1.configure_focus(control, config)
                power1.set_focus_window(control, focus_id=config.focus_id, project=config.project,
                                        starts_at=config.starts_at, expires_at=config.expires_at)
                power1.set_automatic(control, True)
                (self.root / "src.py").write_text(f"race {initial_turns}\n")
                ctrl1.scan(config.focus_id)
                if initial_turns:
                    with db1:
                        db1.execute("UPDATE pa1_attention_focus SET reserved_turns=?,active_seconds=? WHERE focus_id=?",
                                    (initial_turns, initial_seconds, config.focus_id))
                db2 = sqlite3.connect(state_path, timeout=5, check_same_thread=False)
                ctrl2 = AttentionController(db2, power_policy=PowerPolicy(db2, clock=clock),
                    trusted_roots={"alpha": self.root}, repository_for=lambda _p: "repo",
                    access_scope=lambda _p: scope, broker=broker, notebook=FakeNotebook(), clock=clock)
                rendezvous = threading.Barrier(2)
                def determinant_barrier(_project):
                    rendezvous.wait(timeout=5)
                    return {}
                ctrl1.resolve_determinants = determinant_barrier
                ctrl2.resolve_determinants = determinant_barrier
                clock.value += 3
                with ThreadPoolExecutor(max_workers=2) as pool:
                    outcomes = list(pool.map(lambda ctrl: ctrl.dispatch_next(control), (ctrl1, ctrl2)))
                statuses = sorted(item["status"] for item in outcomes)
                self.assertEqual(statuses.count("dispatched"), 1)
                self.assertEqual(len(broker.calls), 1)
                budget = db1.execute("SELECT reserved_turns,active_seconds FROM pa1_attention_focus WHERE focus_id=?",
                                     (config.focus_id,)).fetchone()
                self.assertEqual(tuple(budget), (initial_turns + 6, initial_seconds + 300))
                self.assertLessEqual(budget[0], 24)
                self.assertLessEqual(budget[1], 900)
                db2.close()
                db1.close()

    def test_terminal_result_with_changed_source_is_discarded_and_releases_slot(self):
        self.configure()
        self.change("second\n")
        self.controller.scan("focus-a")
        self.clock.value += 3
        admission = self.controller.dispatch_next(self.control)
        self.broker.results[admission["job_id"]] = {"status": "completed",
            "focus_id": "focus-a", "input_fingerprint": self.controller.candidates()[0].input_fingerprint,
            "answer": "must not be stored", "evidence_packets": ["pkt_original"],
            "sources": [], "dependencies": {}}
        self.change("new source bytes after completion\n")
        outcome = self.controller.record_result(candidate_id=admission["candidate_id"],
                                                control=self.control)
        self.assertEqual(outcome, {"status": "stale", "reason": "source_changed_before_effect"})
        self.assertEqual(self.notebook.notes, [])
        self.assertNotIn("dispatched", [c.status for c in self.controller.candidates()])

    def test_user_goal_precedes_inference_and_ui_proposals_do_not_create_goals(self):
        handoff = self.controller.handoff(project="alpha", focus_id="focus-a",
            user_goal_notes=[{"project": "alpha", "kind": "goal", "provenance": "user",
                              "claim": "explicit goal"}],
            inferred_notes=[{"project": "alpha", "kind": "preparation", "provenance": "model"}])
        self.assertEqual(handoff["status"], "user_goal_precedes_inference")
        self.assertTrue(handoff["requires_operator_decision"])
        self.assertEqual(len(self.notebook.notes), 0)

    def test_budget_intent_is_committed_before_cross_connection_broker_call(self):
        state_path = Path(self.tmp.name) / "shared.sqlite"
        self.db.close()
        self.db = sqlite3.connect(state_path)
        self.power = PowerPolicy(self.db, clock=self.clock)
        observer = sqlite3.connect(state_path)
        self.controller = AttentionController(self.db, power_policy=self.power,
            trusted_roots={"alpha": self.root}, repository_for=lambda _p: "repo",
            access_scope=lambda _p: self.scope,
            broker=self.broker, notebook=self.notebook, clock=self.clock)
        self.configure()
        self.change("changed\n")
        self.controller.scan("focus-a")
        self.clock.value += 3
        def see_committed_intent(_kwargs):
            row = observer.execute("SELECT status,active_charge FROM pa1_attention_candidates").fetchone()
            budget = observer.execute("SELECT reserved_turns,active_seconds FROM pa1_attention_focus").fetchone()
            self.assertEqual(row[0], "admitting")
            self.assertEqual(row[1], 300)
            self.assertEqual(tuple(budget), (6, 300))
        self.broker.before_enqueue = see_committed_intent
        self.assertEqual(self.controller.dispatch_next(self.control)["status"], "dispatched")
        observer.close()

    def test_real_job_service_uses_shared_sqlite_without_controller_transaction_lock(self):
        jobs = JobService(Path(self.tmp.name) / "actual-jobs",
                          packets=SQLitePacketStore(Path(self.tmp.name) / "packets",
                                                    clock=self.clock),
                          clock=self.clock)
        db = sqlite3.connect(jobs.path, timeout=1)
        power = PowerPolicy(db, clock=self.clock)
        scope = {"project": "alpha", "principal": "operator", "profile": "default",
                 "paths": ["src.py"]}
        controller = AttentionController(db, power_policy=power,
            trusted_roots={"alpha": self.root}, repository_for=lambda _p: "repo",
            access_scope=lambda _p: scope, broker=jobs, notebook=self.notebook,
            clock=self.clock)
        config = FocusConfig("focus-live", "alpha", ("src.py",), 100, 300, True)
        controller.configure_focus(self.control, config)
        power.set_focus_window(self.control, focus_id=config.focus_id, project=config.project,
                               starts_at=config.starts_at, expires_at=config.expires_at)
        power.set_automatic(self.control, True)
        (self.root / "src.py").write_text("job service source edit\n")
        self.assertEqual(controller.scan(config.focus_id)["status"], "nominated")
        self.clock.value += 3
        admission = controller.dispatch_next(self.control)
        self.assertEqual(admission["status"], "dispatched")
        # The production JobService requires scope by keyword. This call
        # exercises AttentionController.record_result on a pending exact job.
        self.assertEqual(controller.record_result(candidate_id=admission["candidate_id"],
                                                   control=self.control), {"status": "pending"})
        self.assertFalse(jobs._thread)
        stored = db.execute("SELECT focus_id,input_fingerprint,expected_dependencies,window_deadline "
                            "FROM pa1_automatic_work WHERE job_id=?", (admission["job_id"],)).fetchone()
        self.assertEqual(stored[0], config.focus_id)
        self.assertEqual(stored[1], controller.candidates()[0].input_fingerprint)
        self.assertIn("file:repo/src.py", stored[2])
        self.assertLessEqual(stored[3], config.expires_at)
        db.close()


if __name__ == "__main__":
    unittest.main()
