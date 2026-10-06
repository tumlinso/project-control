"""Source-mode tests for durable quiet, focus-window and release policy."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from project_control.assistance.power import (
    PowerPolicy,
    PowerPolicyError,
    TrustedOperatorControl,
    trusted_operator_control,
)
from project_control.assistance.frames import FrameError, FrameStore
from project_control.assistance.resources import ResourceController, ResourceControllerError


class FakeClock:
    def __init__(self, value: float = 100.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class PowerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pa1-power-")
        self.path = Path(self.temp.name) / "jobs.sqlite3"
        self.clock = FakeClock()
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        FrameStore.initialize(self.db)
        self.frames = FrameStore(self.db)
        scope = {"principal": "power-test", "profile": "observer", "project": "repo"}
        self.frames.register_root("public-root", scope, 500, origin_class="public_demand", now=100)
        self.frames.register_root("auto-root", scope, 500, origin_class="automatic", now=100)
        self.policy = PowerPolicy(self.db, clock=self.clock)
        self.control = trusted_operator_control()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_demand_only_is_default_and_status_is_read_only(self):
        before = self.db.total_changes
        snapshot = self.policy.snapshot()
        self.assertFalse(snapshot["automatic_enabled"])
        self.assertFalse(snapshot["quiet_active"])
        self.assertFalse(snapshot["release_veto_active"])
        self.assertEqual(before, self.db.total_changes)
        self.assertFalse(self.policy.allow_dispatch("opportunistic", True).allowed)
        self.assertTrue(self.policy.allow_dispatch("public_root", True).allowed)
        self.assertFalse(self.policy.allow_dispatch("public_root", False).allowed)

    def test_role_text_and_forged_controls_cannot_mutate_state(self):
        for fake in ("operator", {"role": "operator"}, object()):
            with self.subTest(fake=fake), self.assertRaises(PowerPolicyError):
                self.policy.set_quiet(fake, expires_at=150)
        with self.assertRaises(PowerPolicyError):
            TrustedOperatorControl(object())
        self.assertFalse(self.policy.snapshot()["quiet_active"])

    def test_root_origin_uses_real_frame_store_and_survives_restart_unchanged(self):
        public_before = self.policy.root_demand_origin("public-root")
        automatic_before = self.policy.root_demand_origin("auto-root")
        self.assertEqual((public_before.root_id, public_before.kind), ("public-root", "public_demand"))
        self.assertEqual((automatic_before.root_id, automatic_before.kind), ("auto-root", "automatic"))
        with self.assertRaises(FrameError):
            self.frames.register_root("public-root", {"principal": "other"}, 600,
                                      origin_class="automatic", now=100)

        self.db.commit()
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.policy = PowerPolicy(self.db, clock=self.clock)
        public_after = self.policy.root_demand_origin("public-root")
        automatic_after = self.policy.root_demand_origin("auto-root")
        self.assertEqual((public_after.root_id, public_after.kind), (public_before.root_id, public_before.kind))
        self.assertEqual((automatic_after.root_id, automatic_after.kind),
                         (automatic_before.root_id, automatic_before.kind))

    def test_quiet_suppresses_automatic_work_but_explicit_public_demand_runs(self):
        with self.db:
            self.policy.set_quiet(self.control, expires_at=160, focus_id="analysis-a")
        self.assertEqual(self.policy.allow_dispatch("automatic_root", True).reason, "automatic_work_disabled")
        self.assertTrue(self.policy.allow_dispatch("public_root", True).allowed)
        self.assertEqual(self.policy.allow_dispatch("private_child", True).reason,
                         "persisted_root_origin_required")
        self.assertEqual(self.policy.snapshot()["quiet_focus"], "analysis-a")

    def test_private_children_inherit_persisted_root_demand_origin(self):
        public_origin = self.policy.root_demand_origin("public-root")
        public_child = self.policy.allow_dispatch("private_child", True, root_id="public-root",
                                                  demand_origin=public_origin)
        self.assertTrue(public_child.allowed)
        self.assertFalse(self.policy.allow_dispatch("private_child", True, root_id="another-root",
                                                    demand_origin=public_origin).allowed)
        self.assertFalse(self.policy.allow_dispatch("private_child", True, root_id="public-root",
                                                    demand_origin="public_demand").allowed)

        automatic_origin = self.policy.root_demand_origin("auto-root")
        self.assertFalse(self.policy.allow_dispatch("private_child", True, root_id="auto-root",
                                                    demand_origin=automatic_origin).allowed)
        with self.db:
            self.policy.set_automatic(self.control, True)
            self.policy.set_focus_window(self.control, focus_id="focus-a", project="repo",
                                         starts_at=100, expires_at=150)
        permission = self.control.permission("dispatch_automatic")
        admitted = self.policy.allow_dispatch("private_child", True, permission,
                                             focus_id="focus-a", project="repo", root_id="auto-root",
                                             demand_origin=automatic_origin)
        self.assertTrue(admitted.allowed)
        with self.db:
            self.policy.set_quiet(self.control, expires_at=140)
        public_quiet = self.policy.allow_dispatch("private_child", True, root_id="public-root",
                                                  demand_origin=public_origin)
        self.assertTrue(public_quiet.allowed)
        quiet = self.policy.allow_dispatch("private_child", True, permission,
                                           focus_id="focus-a", project="repo", root_id="auto-root",
                                           demand_origin=automatic_origin)
        self.assertFalse(quiet.allowed)
        self.assertEqual(quiet.reason, "quiet_active")

    def test_quiet_expiry_is_persisted_and_focus_cannot_resurrect_it(self):
        with self.db:
            self.policy.set_automatic(self.control, True)
            self.policy.set_focus_window(self.control, focus_id="f1", project="repo", starts_at=100, expires_at=130)
            self.policy.set_quiet(self.control, expires_at=110, focus_id="f1")
        self.clock.advance(11)
        # First scheduling decision records expiry; status alone remains read-only.
        self.assertFalse(self.policy.snapshot()["quiet_active"])
        self.assertTrue(self.policy.allow_dispatch("opportunistic", True,
                                                   self.control.permission("dispatch_automatic"),
                                                   focus_id="f1", project="repo").allowed)
        self.clock.value = 105  # wall-clock adjustment after observed expiry
        self.assertFalse(self.policy.snapshot()["quiet_active"])
        self.assertTrue(self.policy.allow_dispatch("opportunistic", True,
                                                   self.control.permission("dispatch_automatic"),
                                                   focus_id="f1", project="repo").allowed)
        self.assertFalse(self.policy.allow_dispatch("opportunistic", True,
                                                    self.control.permission("dispatch_automatic"),
                                                    focus_id="other", project="repo").allowed)

    def test_automatic_work_requires_explicit_bounded_focus_and_permission(self):
        with self.db:
            self.policy.set_automatic(self.control, True)
            self.policy.set_focus_window(self.control, focus_id="f1", project="repo", starts_at=100, expires_at=120)
        self.assertFalse(self.policy.allow_dispatch("opportunistic", True).allowed)
        self.assertFalse(self.policy.allow_dispatch("opportunistic", True,
                                                    self.control.permission("dispatch_automatic"),
                                                    focus_id="f1", project="elsewhere").allowed)
        admitted = self.policy.allow_dispatch("opportunistic", True,
                                              self.control.permission("dispatch_automatic"),
                                              focus_id="f1", project="repo")
        self.assertTrue(admitted.allowed)
        self.assertEqual(admitted.priority, "opportunistic")
        self.clock.advance(20)
        expired = self.policy.allow_dispatch("opportunistic", True,
                                             self.control.permission("dispatch_automatic"),
                                             focus_id="f1", project="repo")
        self.assertFalse(expired.allowed)
        self.clock.value = 105
        self.assertFalse(self.policy.allow_dispatch("opportunistic", True,
                                                    self.control.permission("dispatch_automatic"),
                                                    focus_id="f1", project="repo").allowed)

    def test_timestamp_boundaries_reject_nonfinite_and_focus_windows_over_24_hours(self):
        bad_values = (float("nan"), float("inf"), float("-inf"))
        for value in bad_values:
            with self.subTest(kind="focus", value=value), self.assertRaises(PowerPolicyError):
                self.policy.set_focus_window(self.control, focus_id="f", project="repo",
                                             starts_at=100, expires_at=value)
            with self.subTest(kind="focus-start", value=value), self.assertRaises(PowerPolicyError):
                self.policy.set_focus_window(self.control, focus_id="f", project="repo",
                                             starts_at=value, expires_at=200)
            with self.subTest(kind="quiet", value=value), self.assertRaises(PowerPolicyError):
                self.policy.set_quiet(self.control, expires_at=value)
            with self.subTest(kind="release", value=value), self.assertRaises(PowerPolicyError):
                self.policy.set_release(self.control, declared_end=value)
        with self.assertRaises(PowerPolicyError):
            self.policy.set_focus_window(self.control, focus_id="f", project="repo",
                                         starts_at=100, expires_at=100 + 86_400.001)
        with self.assertRaises(PowerPolicyError):
            self.policy.set_focus_window(self.control, focus_id="f", project="repo",
                                         starts_at=100, expires_at=100.5)

    def _owned_resource_fixture(self):
        resources = ResourceController(self.db, power_policy=self.policy, clock=self.clock)
        epoch = {"daemon_epoch": "a" * 64, "supervisor_pid": 10,
                 "supervisor_process_start": "supervisor-start", "runtime_fingerprint": "b" * 64}
        receipt = {
            "format": "PA1-OWNED-RESOURCE/1", "session_id": "owned-session",
            "daemon_epoch": "a" * 64, "slot_id": "slot-A", "owner_id": "host-owner-A",
            "host_lease_id": "host-owner-A", "residency_generation": "generation-A",
            "server_pid": 4242, "server_process_start": "server-start",
            "gpu_uuids": ["GPU-fixture-A"], "capability": "d" * 64,
        }
        resources.record_active_session("owned-session", epoch)
        resources.record_session("owned-session", receipt)
        self.db.commit()
        return resources

    @staticmethod
    def _release_response(_request_id, _reason, records):
        return {"status": "released", "sessions": [
            {key: record[key] for key in (
                "session_id", "daemon_epoch", "slot_id", "owner_id", "host_lease_id",
                "residency_generation", "server_pid", "server_process_start", "gpu_uuids",
            )} | {"released": True, "process_released": True,
             "memory_released": True, "host_released": True,
             "observation": {"observed_unix": 101.0, "processes": [], "devices": []}}
            for record in records
        ]}

    def test_owner_verification_marks_physical_release_and_survives_restart(self):
        resources = self._owned_resource_fixture()
        intent = self.policy.set_release(self.control)
        self.db.commit()
        self.assertEqual(resources.release_owned(intent, self._release_response), "released_verified")
        with self.assertRaises(ResourceControllerError):
            resources.acknowledge_verified_release(intent)
        self.assertEqual(self.policy.snapshot()["physical_state"], "pending")
        self.db.commit()  # owner proof rows are committed before acknowledgment
        resources.acknowledge_verified_release(intent)
        self.db.commit()
        status = self.policy.snapshot()
        self.assertEqual(status["physical_state"], "released_verified")
        self.assertEqual(status["release_verified_sessions"], 1)
        self.assertEqual(status["release_verified_at"], self.clock())
        self.assertEqual(len(status["release_verified_proof_digest"]), 64)
        self.assertEqual(len(status["release_verified_targets_digest"]), 64)
        self.assertTrue(status["release_veto_active"])

        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.policy = PowerPolicy(self.db, clock=self.clock)
        resources = ResourceController(self.db, power_policy=self.policy, clock=self.clock)
        self.db.commit()
        status = self.policy.snapshot()
        self.assertEqual(status["physical_state"], "released_verified")
        self.assertEqual(status["release_verified_sessions"], 1)
        self.assertEqual(len(status["release_verified_proof_digest"]), 64)
        self.assertEqual(len(status["release_verified_targets_digest"]), 64)
        self.assertTrue(status["release_veto_active"])
        resources.acknowledge_verified_release(intent)
        self.db.commit()

    def test_repeated_active_release_reuses_id_and_preserves_verified_proof(self):
        resources = self._owned_resource_fixture()
        intent = self.policy.set_release(self.control, declared_end=200, reason="quiet the model owner")
        self.db.commit()
        self.assertEqual(resources.release_owned(intent, self._release_response), "released_verified")
        self.db.commit()
        resources.acknowledge_verified_release(intent)
        self.db.commit()
        before = self.policy.snapshot()

        repeated = self.policy.set_release(self.control, declared_end=250, reason="same quiet request")
        after = self.policy.snapshot()
        self.assertEqual(repeated.request_id, intent.request_id)
        self.assertEqual(repeated.created_at, intent.created_at)
        self.assertEqual(repeated.declared_end, 250)
        self.assertEqual(repeated.reason, "same quiet request")
        self.assertEqual(after["release_request_id"], before["release_request_id"])
        self.assertEqual(after["physical_state"], "released_verified")
        self.assertEqual(after["release_verified_at"], before["release_verified_at"])
        self.assertEqual(after["release_verified_sessions"], before["release_verified_sessions"])
        self.assertEqual(after["release_verified_proof_digest"], before["release_verified_proof_digest"])
        self.assertEqual(after["release_verified_targets_digest"], before["release_verified_targets_digest"])
        self.assertTrue(after["release_veto_active"])
        self.db.commit()
        again = self.policy.set_release(self.control)
        self.assertEqual(again.request_id, intent.request_id)
        self.assertEqual(again.created_at, intent.created_at)
        self.assertEqual(again.declared_end, 250)
        self.assertEqual(again.reason, "same quiet request")
        self.assertEqual(self.policy.snapshot()["physical_state"], "released_verified")
        self.db.commit()
        # An identical retry reuses the committed complete proof; it performs no new owner call.
        self.assertEqual(resources.release_owned(again, lambda *_: self.fail("unexpected owner call")),
                         "released_verified")

    def test_resume_or_expiry_ends_idempotency_and_creates_a_fresh_intent(self):
        with self.db:
            first = self.policy.set_release(self.control, reason="first request")
        with self.db:
            self.policy.resume(self.control)
        resumed = self.policy.set_release(self.control, reason="after resume")
        self.assertNotEqual(resumed.request_id, first.request_id)
        self.assertEqual(self.policy.snapshot()["physical_state"], "pending")
        with self.db:
            expiring = self.policy.set_release(self.control, declared_end=110)
        self.db.commit()
        self.clock.advance(10)
        self.assertTrue(self.policy.allow_dispatch("public_root", True).allowed)
        expired_reissue = self.policy.set_release(self.control, reason="after expiry")
        self.assertNotEqual(expired_reissue.request_id, expiring.request_id)
        self.assertEqual(self.policy.snapshot()["release_request_id"], expired_reissue.request_id)
        self.assertEqual(self.policy.snapshot()["physical_state"], "pending")

    def test_new_owned_target_invalidates_same_veto_proof_until_full_set_is_verified(self):
        resources = self._owned_resource_fixture()
        intent = self.policy.set_release(self.control)
        self.db.commit()
        self.assertEqual(resources.release_owned(intent, self._release_response), "released_verified")
        self.db.commit()
        resources.acknowledge_verified_release(intent)
        self.db.commit()
        self.assertEqual(self.policy.snapshot()["physical_state"], "released_verified")

        new_epoch = {"daemon_epoch": "e" * 64, "supervisor_pid": 11,
                     "supervisor_process_start": "supervisor-start-2", "runtime_fingerprint": "f" * 64}
        resources.record_active_session("new-owned-session", new_epoch)
        self.db.commit()
        status = self.policy.snapshot()
        self.assertEqual(status["release_request_id"], intent.request_id)
        self.assertTrue(status["release_veto_active"])
        self.assertEqual(status["physical_state"], "pending")
        with self.assertRaises(ResourceControllerError):
            resources.acknowledge_verified_release(intent)

    def test_partial_unknown_and_replaced_owner_proofs_cannot_mark_release_verified(self):
        resources = self._owned_resource_fixture()
        intent = self.policy.set_release(self.control)
        self.db.commit()
        def partial(_request_id, _reason, records):
            record = records[0]
            return {"status": "released", "sessions": [
                {key: record[key] for key in (
                    "session_id", "daemon_epoch", "slot_id", "owner_id", "host_lease_id",
                    "residency_generation", "server_pid", "server_process_start", "gpu_uuids",
                )} | {"released": True, "process_released": True, "memory_released": False,
                     "host_released": True, "observation": {"observed_unix": 101.0, "processes": []}}
            ]}
        self.assertEqual(resources.release_owned(intent, partial), "pending_owner_verification")
        self.db.commit()
        with self.assertRaises(ResourceControllerError):
            resources.acknowledge_verified_release(intent)
        self.assertEqual(self.policy.snapshot()["physical_state"], "pending")

        self.policy.resume(self.control)
        replacement = self.policy.set_release(self.control, reason="replacement intent")
        self.db.commit()
        with self.assertRaises(PowerPolicyError):
            self.policy.mark_release_verified(intent)
        self.assertEqual(self.policy.snapshot()["release_request_id"], replacement.request_id)
        self.assertEqual(self.policy.snapshot()["physical_state"], "pending")

    def test_empty_or_unbound_owner_proof_cannot_mark_physical_release(self):
        intent = self.policy.set_release(self.control)
        self.db.commit()
        with self.assertRaises(PowerPolicyError):
            self.policy.mark_release_verified(intent)
        self.assertEqual(self.policy.snapshot()["physical_state"], "pending")

        resources = ResourceController(self.db, power_policy=self.policy, clock=self.clock)
        self.db.commit()
        with self.assertRaises(ResourceControllerError):
            resources.acknowledge_verified_release(intent)
        self.assertEqual(self.policy.snapshot()["physical_state"], "pending")

    def test_release_veto_survives_restart_status_and_new_demand(self):
        with self.db:
            intent = self.policy.set_release(self.control, reason="operator wants models quiet")
        self.db.commit()
        self.db.close()
        reopened = sqlite3.connect(self.path)
        self.addCleanup(reopened.close)
        policy = PowerPolicy(reopened, clock=self.clock)
        backend_calls = []
        for _ in range(3):
            state = policy.snapshot()
            self.assertTrue(state["release_veto_active"])
            self.assertEqual(state["physical_state"], "pending")
            self.assertFalse(policy.allow_dispatch("public_root", True).allowed)
            self.assertFalse(policy.allow_dispatch("foreground", True,
                                                   self.control.permission("dispatch_during_quiet")).allowed)
        self.assertEqual(backend_calls, [])
        self.assertEqual(policy.snapshot()["release_request_id"], intent.request_id)

    def test_release_callback_requires_committed_veto_and_errors_stay_pending(self):
        with self.db:
            intent = self.policy.set_release(self.control)
            calls = []
            with self.assertRaises(PowerPolicyError):
                self.policy.deliver_release_intent(intent, lambda *args: calls.append(args))
        self.db.commit()

        def failing(request_id, reason):
            calls.append((request_id, reason))
            raise RuntimeError("owner unavailable")

        self.assertEqual(self.policy.deliver_release_intent(intent, failing), "pending_after_callback_error")
        self.assertEqual(calls, [(intent.request_id, intent.reason)])
        self.assertTrue(self.policy.snapshot()["release_veto_active"])
        self.assertEqual(self.policy.snapshot()["physical_state"], "pending")

    def test_declared_release_end_or_explicit_resume_clears_logical_veto_only(self):
        with self.db:
            self.policy.set_automatic(self.control, True)
            self.policy.set_focus_window(self.control, focus_id="f1", project="repo", starts_at=100, expires_at=300)
            self.policy.set_release(self.control, declared_end=120)
        self.clock.advance(20)
        allowed = self.policy.allow_dispatch("opportunistic", True,
                                             self.control.permission("dispatch_automatic"),
                                             focus_id="f1", project="repo")
        self.assertTrue(allowed.allowed)
        self.assertFalse(self.policy.snapshot()["release_veto_active"])
        self.assertEqual(self.policy.snapshot()["physical_state"], "pending")

        with self.db:
            self.policy.set_release(self.control)
            self.policy.resume(self.control)
        self.assertFalse(self.policy.snapshot()["release_veto_active"])
        self.assertEqual(self.policy.snapshot()["physical_state"], "pending")


if __name__ == "__main__":
    unittest.main()
