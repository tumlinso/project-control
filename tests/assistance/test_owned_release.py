"""CPU-only PA1 owned-resource accounting and private supervisor RPC tests."""
from __future__ import annotations

import os
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from project_control.assistance.power import PowerPolicy, trusted_operator_control
from project_control.assistance.resources import ResourceController, ResourceControllerError
from project_control.observer_analysis import (
    SkillsObserverAnalysisProvider, _PHYSICAL_RELEASE_SEAL, _VerifiedPhysicalRelease,
)

_tests_root = Path(__file__).resolve().parents[1]
if str(_tests_root) not in sys.path:
    sys.path.insert(0, str(_tests_root))
from local_runtime import receiver_runtime_path  # noqa: E402

_receiver = receiver_runtime_path()
if str(_receiver) not in sys.path:
    sys.path.insert(0, str(_receiver))

from local_worker import supervisor  # noqa: E402


def _identity(pid: int) -> dict[str, object]:
    if pid == os.getpid():
        from local_worker.residency import process_identity
        return process_identity(pid)
    return {"pid": pid, "process_start": f"fixture-start-{pid}", "executable": "/fixture/llama-server"}


class _Backend:
    def __init__(self, *, session_id="session-A", slot_id="slot-A", pid=4242):
        self._pool_lock = threading.RLock()
        self.session_id = session_id
        self.slot_id = slot_id
        self.slot = SimpleNamespace(
            slot_id=slot_id, service_lease_id=session_id, state="leased", active_turns=0,
            owner_id="host-owner-A", residency_generation="generation-A",
            endpoint_descriptor={"server_pid": pid}, gpu_uuids=("GPU-fixture-A",),
            last_cleanup={})
        self._slots = {slot_id: self.slot}
        self._leases = {session_id: slot_id}
        self.cleanup_receipts = []
        self.evictions = 0

    def close_observer_session(self, session_id):
        if session_id != self.session_id:
            return {"released": False}
        self.slot.service_lease_id = None
        self.slot.state = "idle"
        self._leases.pop(session_id, None)
        return {"released": True, "slot_id": self.slot_id, "service_lease_id": session_id}

    def _evict_slot(self, slot_id):
        slot = self._slots.get(slot_id)
        if slot is None or slot.active_turns or slot.service_lease_id is not None:
            return False
        self.evictions += 1
        observation = {"observed_unix": time.time(), "devices": [], "processes": []}
        self.cleanup_receipts.append({"owner_id": slot.owner_id, "gpu_uuids": list(slot.gpu_uuids),
            "owned_pid": slot.endpoint_descriptor["server_pid"], "released": True,
            "process_released": True, "memory_released": True, "observation": observation})
        self._slots.pop(slot_id)
        return True


def _server(backend: _Backend):
    server = object.__new__(supervisor.SupervisorServer)
    server.backend = backend
    server.observer_only = True
    server.runtime_identity = object()
    server.runtime_context = {"fixture": "source"}
    server._daemon_epoch = "a" * 64
    server._runtime_fingerprint = "b" * 64
    server._source_sha256 = "c" * 64
    server._process_start = "supervisor-start"
    server._borrowers = {}
    server._borrowers_lock = threading.Lock()
    server._closed_owned = {}
    server._reclaimed_closed = {}
    server._reclaimed_deliveries = {}
    server._owned_release_receipts = {}
    server._stop_event = threading.Event()
    server.stopping = False
    return server


class ResourceControllerTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.clock = [100.0]
        self.power = PowerPolicy(self.db, clock=lambda: self.clock[0])
        self.resources = ResourceController(self.db, power_policy=self.power, clock=lambda: self.clock[0])
        self.control = trusted_operator_control()
        self.epoch = {"daemon_epoch": "a" * 64, "supervisor_pid": 10,
            "supervisor_process_start": "supervisor-start", "runtime_fingerprint": "b" * 64}
        self.session_id = "session-A"
        self.receipt = {"format": "PA1-OWNED-RESOURCE/1", "session_id": self.session_id,
            "daemon_epoch": "a" * 64, "slot_id": "slot-A", "owner_id": "host-owner-A",
            "host_lease_id": "host-owner-A", "residency_generation": "generation-A",
            "server_pid": 4242, "server_process_start": "fixture-start-4242",
            "gpu_uuids": ["GPU-fixture-A"], "capability": "d" * 64}

    def tearDown(self):
        self.db.close()

    def _intent(self):
        intent = self.power.set_release(self.control, reason="user-requested-owned-release")
        self.db.commit()
        return intent

    def test_active_session_keeps_release_pending_without_backend_callback(self):
        self.resources.record_active_session(self.session_id, self.epoch)
        self.db.commit()
        intent = self._intent()
        callbacks = []
        self.assertEqual(self.resources.release_owned(intent,
            lambda *args: callbacks.append(args)), "pending_sessions_open")
        self.assertEqual(callbacks, [])
        self.assertEqual(self.resources.snapshot()[0]["state"], "active")

    def _physical_release_record(self):
        uuids = [
            "GPU-21131915-1488-23af-38dd-1743ae1f5cc8",
            "GPU-d745da9c-7649-8334-41f1-483afa6f3206",
            "GPU-cf22c41f-5b58-77b1-3535-8fadd1ca6505",
            "GPU-6c1cac7f-a360-0aef-ba98-2828bfd1db1a",
        ]
        cleanup = []
        for index in range(2):
            pair = uuids[index * 2:index * 2 + 2]
            cleanup.append({"owner_id": f"owner-{index}", "gpu_uuids": pair,
                "owned_pid": 98000000 + index, "server_process_start": f"server-start-{index}",
                "generation": f"generation-{index}", "released": True,
                "process_released": True, "memory_released": True,
                "observation": {"available": True,
                    "devices": [{"uuid": uuid, "memory_used_mib": 0.0} for uuid in pair],
                    "processes": [], "observed_unix": time.time()}})
        before = {"format": "CORE4-OBSERVER-STATUS/1", "running": True, "healthy": True,
            "active_leases": 0, "active_admissions": 0, "supervisor_pid": 98000001,
            "supervisor_process_start": "supervisor-start", "daemon_epoch": "a" * 64,
            "runtime_fingerprint": "b" * 64,
            "slots": [{"state": "idle", "leased": False, "service_lease_id": None,
                "server_pid": item["owned_pid"], "owner_id": item["owner_id"],
                "gpu_uuids": item["gpu_uuids"]} for item in cleanup]}
        return {"captured_at": time.time(), "selected_manifest_sha256": "f" * 64,
            "before": before, "broker_before": {"active_work": [], "active_work_truncated": False,
                "active_execution_slots": [], "active_execution_slots_truncated": False},
            "release": {"status": "released_verified", "released_verified": True,
                "supervisor_pid": 98000001, "supervisor_process_start": "supervisor-start",
                "daemon_epoch": "a" * 64, "runtime_fingerprint": "b" * 64,
                "quiescent": True, "evicted": True, "stopped": True,
                "cleanup_receipts": cleanup}}

    def _release_reconciliation_fixture(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE jobs(record TEXT NOT NULL)")
        db.execute("CREATE TABLE execution_slots(job TEXT PRIMARY KEY)")
        power = PowerPolicy(db, clock=time.time)
        resources = ResourceController(db, power_policy=power, clock=time.time)
        control = trusted_operator_control()
        intent = power.set_release(control, reason="operator-requested-recovery")
        record = self._physical_release_record()
        proof = _VerifiedPhysicalRelease(_PHYSICAL_RELEASE_SEAL, {
            "format": "PA1-PHYSICAL-RELEASE/1", "captured_at": time.time(),
            "daemon_epoch": "a" * 64, "supervisor_pid": 98000001,
            "supervisor_process_start": "supervisor-start", "runtime_fingerprint": "b" * 64,
            "quiescent": True, "stopped": True,
            "gpu_uuids": [uuid for item in record["release"]["cleanup_receipts"] for uuid in item["gpu_uuids"]],
            "cleanup_receipts": record["release"]["cleanup_receipts"]}, "test-host")
        epoch = {"daemon_epoch": "a" * 64, "supervisor_pid": 98000001,
            "supervisor_process_start": "supervisor-start", "runtime_fingerprint": "b" * 64}
        return db, power, resources, control, intent, proof, epoch

    def test_orphaned_active_session_reconciles_only_after_verified_whole_epoch_release(self):
        db, power, resources, control, intent, proof, epoch = self._release_reconciliation_fixture()
        try:
            resources.record_active_session("orphan-session", epoch)
            db.commit()
            self.assertEqual(resources.record_orphaned_sessions_after_physical_release(control, intent, proof),
                             ["orphan-session"])
            row = resources.snapshot()[0]
            self.assertEqual(row["state"], "released_verified")
            audit = db.execute("SELECT session_ids,proof_sha256,controller_host FROM pa1_orphan_resource_reconciliation_audit").fetchone()
            self.assertIsNotNone(audit)
            self.assertIn("orphan-session", audit[0])
            self.assertEqual(len(audit[1]), 64)
            self.assertTrue(audit[2])
            close = json.loads(db.execute("SELECT close_receipt FROM pa1_owned_resource_sessions").fetchone()[0])
            self.assertEqual(close["format"], "PA1-ORPHANED-SESSION-RELEASE/1")
            db.commit()
            ack = resources.verified_release_ack(intent)
            self.assertEqual(ack.target_session_ids, ("orphan-session",))
            resources.acknowledge_verified_release(intent)
            self.assertEqual(power.snapshot()["physical_state"], "released_verified")
        finally:
            db.close()

    def test_orphan_reconciliation_rejects_unsealed_proofs_foreign_epochs_and_active_work(self):
        db, power, resources, control, intent, proof, epoch = self._release_reconciliation_fixture()
        try:
            resources.record_active_session("orphan-session", epoch)
            db.commit()
            with self.assertRaisesRegex(ResourceControllerError, "verified_physical_release_required"):
                resources.record_orphaned_sessions_after_physical_release(control, intent, dict(proof))
            foreign = dict(epoch, daemon_epoch="c" * 64)
            resources.record_active_session("foreign-session", foreign)
            db.commit()
            with self.assertRaisesRegex(ResourceControllerError, "active_session_epoch_mismatch"):
                resources.record_orphaned_sessions_after_physical_release(control, intent, proof)
            db.execute("INSERT INTO jobs(record) VALUES('{\"status\":\"running\"}')")
            db.commit()
            with self.assertRaisesRegex(ResourceControllerError, "controller_work_not_quiescent"):
                resources.record_orphaned_sessions_after_physical_release(control, intent, proof)
        finally:
            db.close()

    def test_orphan_reconciliation_rejects_nonzero_gpu_observation(self):
        db, power, resources, control, intent, proof, epoch = self._release_reconciliation_fixture()
        try:
            resources.record_active_session("orphan-session", epoch)
            db.commit()
            record = self._physical_release_record()
            record["release"]["cleanup_receipts"][0]["observation"]["devices"][0]["memory_used_mib"] = 16
            provider = SkillsObserverAnalysisProvider()
            provider._allowed_gpu_uuids = tuple(uuid for item in record["release"]["cleanup_receipts"]
                                                 for uuid in item["gpu_uuids"])
            with self.assertRaisesRegex(RuntimeError, "physical_release_gpu_memory_not_zero"):
                provider.verify_saved_physical_release(record)
        finally:
            db.close()

    def test_saved_release_proof_can_be_revalidated_after_capture_with_fresh_host_state(self):
        import types
        from unittest.mock import patch

        record = self._physical_release_record()
        record["captured_at"] -= 3600
        for item in record["release"]["cleanup_receipts"]:
            item["observation"]["observed_unix"] = record["captured_at"] - 1
        provider = SkillsObserverAnalysisProvider()
        provider._allowed_gpu_uuids = tuple(uuid for item in record["release"]["cleanup_receipts"]
                                             for uuid in item["gpu_uuids"])
        gpu_output = "".join(f"{uuid}, 0\n" for uuid in provider._allowed_gpu_uuids)

        class Cursor:
            @staticmethod
            def fetchone():
                return (0,)

        class Connection:
            @staticmethod
            def execute(*_args):
                return Cursor()

            @staticmethod
            def close():
                return None

        class HostCoordinator:
            def __init__(self, **_kwargs):
                pass

            @staticmethod
            def connect(*_args, **_kwargs):
                return Connection()

        host_module = types.ModuleType("todo_orchestrator.background.host")
        host_module.HostCoordinator = HostCoordinator
        parent_module = types.ModuleType("todo_orchestrator")
        parent_module.__path__ = []
        background_module = types.ModuleType("todo_orchestrator.background")
        background_module.__path__ = []
        with patch.dict(sys.modules, {"todo_orchestrator": parent_module,
                "todo_orchestrator.background": background_module,
                "todo_orchestrator.background.host": host_module}), \
             patch("project_control.observer_analysis._proc_start_time", return_value=None), \
             patch("project_control.observer_analysis.subprocess.run", side_effect=[
                 types.SimpleNamespace(stdout=gpu_output), types.SimpleNamespace(stdout="")]):
            proof = provider.verify_saved_physical_release(record)
        self.assertLessEqual(time.time() - proof["captured_at"], 2)
        self.assertEqual(proof["source_proof_captured_at"], record["captured_at"])

    def test_close_receipt_is_required_and_verified_release_is_exact(self):
        self.resources.record_active_session(self.session_id, self.epoch)
        with self.assertRaises(ResourceControllerError):
            self.resources.record_session(self.session_id, {**self.receipt, "session_id": "foreign"})
        self.resources.record_session(self.session_id, self.receipt)
        self.db.commit()
        intent = self._intent()
        seen = []

        def release(request_id, reason, records):
            seen.append((request_id, reason, records))
            return {"status": "released", "sessions": [{"session_id": self.session_id,
                "daemon_epoch": self.receipt["daemon_epoch"], "slot_id": self.receipt["slot_id"],
                "owner_id": self.receipt["owner_id"], "host_lease_id": self.receipt["host_lease_id"],
                "residency_generation": self.receipt["residency_generation"],
                "server_pid": self.receipt["server_pid"],
                "server_process_start": self.receipt["server_process_start"],
                "gpu_uuids": self.receipt["gpu_uuids"], "owned_pid": self.receipt["server_pid"],
                "released": True, "process_released": True, "memory_released": True,
                "host_released": True, "observation": {"observed_unix": 101.0, "processes": []}}]}

        self.assertEqual(self.resources.release_owned(intent, release), "released_verified")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][2][0]["capability"], "d" * 64)
        self.assertEqual(self.resources.snapshot()[0]["state"], "released_verified")
        self.assertNotIn("capability", str(self.resources.snapshot()))

    def test_unverified_cleanup_and_stale_epoch_never_report_released(self):
        self.resources.record_active_session(self.session_id, self.epoch)
        self.resources.record_session(self.session_id, self.receipt)
        self.db.commit()
        intent = self._intent()
        self.assertEqual(self.resources.release_owned(intent, lambda *_: {"status": "pending",
            "sessions": [{"session_id": self.session_id, "released": False}]}),
            "pending_owner_verification")
        self.assertEqual(self.resources.snapshot()[0]["state"], "release_pending")

        # A later owner result must prove all three independent conditions.
        self.db.commit()
        self.assertEqual(self.resources.release_owned(intent, lambda *_: {"status": "released",
            "sessions": [{"session_id": self.session_id, "released": True,
                "process_released": True, "memory_released": True, "host_released": False,
                "observation": {}}]}), "pending_owner_verification")

    def test_sequential_sessions_on_two_slots_supersede_invalidated_caps(self):
        for index in range(4):
            session_id = f"session-{index}"
            slot_index = index % 2
            self.resources.record_active_session(session_id, self.epoch)
            receipt = {**self.receipt, "session_id": session_id, "slot_id": f"slot-{slot_index}",
                "owner_id": f"owner-{slot_index}", "host_lease_id": f"owner-{slot_index}",
                "residency_generation": f"generation-{index}", "server_pid": 4242 + slot_index,
                "server_process_start": f"fixture-start-{4242 + slot_index}",
                "gpu_uuids": [f"GPU-fixture-{slot_index}"], "capability": f"{index + 1:064x}"}
            self.resources.record_session(session_id, receipt)
            self.db.commit()
        snapshot = {item["session_id"]: item["state"] for item in self.resources.snapshot()}
        self.assertEqual(snapshot, {"session-0": "superseded", "session-1": "superseded",
                                    "session-2": "idle_owned", "session-3": "idle_owned"})
        intent = self._intent()
        sent = []
        def pending(request_id, reason, records):
            sent.extend(item["session_id"] for item in records)
            return {"status": "pending", "sessions": []}
        self.assertEqual(self.resources.release_owned(intent, pending), "pending_owner_verification")
        self.assertEqual(set(sent), {"session-2", "session-3"})

    def test_three_slot_release_chunks_and_keeps_partial_stale_pending(self):
        receipts = []
        for index in range(3):
            session_id = f"history-{index}"
            receipt = {**self.receipt, "session_id": session_id, "slot_id": f"slot-{index}",
                "owner_id": f"owner-{index}", "host_lease_id": f"owner-{index}",
                "residency_generation": f"generation-{index}", "server_pid": 4242 + index,
                "server_process_start": f"fixture-start-{4242 + index}",
                "gpu_uuids": [f"GPU-fixture-{index}"], "capability": f"{index + 10:064x}"}
            self.resources.record_active_session(session_id, self.epoch)
            self.resources.record_session(session_id, receipt)
            receipts.append(receipt)
            self.db.commit()
        intent = self._intent()

        client = object.__new__(supervisor.SupervisorClient)
        client.observer_status = lambda **_kwargs: {
            "daemon_epoch": self.epoch["daemon_epoch"],
            "supervisor_pid": self.epoch["supervisor_pid"],
            "supervisor_process_start": self.epoch["supervisor_process_start"],
            "runtime_fingerprint": self.epoch["runtime_fingerprint"]}
        client._validate_owned_release_status = lambda _status: None
        chunks = []

        def rpc(operation, **parameters):
            self.assertEqual(operation, "observer-release-owned")
            chunk = parameters["records"]
            self.assertLessEqual(len(chunk), 2)
            chunks.append([record["session_id"] for record in chunk])
            outcomes = []
            for record in chunk:
                receipt = next(item for item in receipts if item["session_id"] == record["session_id"])
                if receipt["session_id"] == "history-0":
                    outcomes.append({"session_id": "history-0", "released": False, "stale": True})
                else:
                    outcomes.append({**{key: receipt[key] for key in (
                        "session_id", "daemon_epoch", "slot_id", "owner_id", "host_lease_id",
                        "residency_generation", "server_pid", "server_process_start", "gpu_uuids")},
                        "released": True, "process_released": True, "memory_released": True,
                        "host_released": True, "observation": {"observed_unix": 101.0, "processes": []}})
            return {"status": "stale" if any(item.get("stale") for item in outcomes) else "released",
                    "sessions": outcomes}

        client._observer_request = rpc
        status = self.resources.release_owned(intent, lambda request_id, _reason, records:
            client.release_owned_observer_resources(list(records), request_id=request_id))
        self.assertEqual(status, "pending_reconciliation_required")
        self.assertEqual(chunks, [["history-0", "history-1"], ["history-2"]])
        states = {item["session_id"]: item["state"] for item in self.resources.snapshot()}
        self.assertEqual(states, {"history-0": "stale", "history-1": "released_verified",
                                  "history-2": "released_verified"})

    def test_more_than_client_capacity_keeps_current_release_progress_and_stale_history_pending(self):
        receipts = {}
        for index in range(66):
            session_id = f"history-{index:03d}"
            receipt = {**self.receipt, "session_id": session_id, "slot_id": f"slot-{index}",
                "owner_id": f"owner-{index}", "host_lease_id": f"owner-{index}",
                "residency_generation": f"generation-{index}", "server_pid": 5000 + index,
                "server_process_start": f"fixture-start-{5000 + index}",
                "gpu_uuids": [f"GPU-fixture-{index}"], "capability": f"{index + 1:064x}"}
            self.resources.record_active_session(session_id, self.epoch)
            self.resources.record_session(session_id, receipt)
            receipts[session_id] = receipt
            self.db.commit()
        intent = self._intent()

        client = object.__new__(supervisor.SupervisorClient)
        client.observer_status = lambda **_kwargs: {
            "daemon_epoch": self.epoch["daemon_epoch"],
            "supervisor_pid": self.epoch["supervisor_pid"],
            "supervisor_process_start": self.epoch["supervisor_process_start"],
            "runtime_fingerprint": self.epoch["runtime_fingerprint"]}
        client._validate_owned_release_status = lambda _status: None
        input_batches = []
        rpc_batches = []

        def rpc(operation, **parameters):
            self.assertEqual(operation, "observer-release-owned")
            chunk = parameters["records"]
            self.assertLessEqual(len(chunk), 2)
            rpc_batches.append([record["session_id"] for record in chunk])
            outcomes = []
            for record in chunk:
                receipt = receipts[record["session_id"]]
                if record["session_id"] != "history-065":
                    outcomes.append({"session_id": record["session_id"], "released": False, "stale": True})
                else:
                    outcomes.append({**{key: receipt[key] for key in (
                        "session_id", "daemon_epoch", "slot_id", "owner_id", "host_lease_id",
                        "residency_generation", "server_pid", "server_process_start", "gpu_uuids")},
                        "released": True, "process_released": True, "memory_released": True,
                        "host_released": True,
                        "observation": {"observed_unix": 101.0, "processes": []}})
            return {"status": "stale" if any(item.get("stale") for item in outcomes) else "released",
                    "sessions": outcomes}

        client._observer_request = rpc

        def bounded_release(request_id, reason, records):
            self.assertLessEqual(len(records), 64)
            input_batches.append(len(records))
            return client.release_owned_observer_resources(list(records), request_id=request_id)

        status = self.resources.release_owned(intent, bounded_release)
        self.assertEqual(status, "pending_reconciliation_required")
        self.assertEqual(input_batches, [64, 2])
        self.assertEqual(len(rpc_batches), 33)
        states = {item["session_id"]: item["state"] for item in self.resources.snapshot()}
        self.assertEqual(states["history-065"], "released_verified")
        self.assertEqual(sum(state == "stale" for state in states.values()), 65)
        self.assertEqual(self.power.snapshot()["physical_state"], "pending")

    def test_same_veto_reuses_verified_proof_until_a_new_session_is_owned(self):
        self.resources.record_active_session(self.session_id, self.epoch)
        self.resources.record_session(self.session_id, self.receipt)
        self.db.commit()
        intent = self._intent()

        def outcome(receipt):
            return {**{key: receipt[key] for key in ("session_id", "daemon_epoch", "slot_id",
                "owner_id", "host_lease_id", "residency_generation", "server_pid",
                "server_process_start", "gpu_uuids")}, "released": True,
                "process_released": True, "memory_released": True, "host_released": True,
                "observation": {"observed_unix": 101.0, "processes": []}}

        self.assertEqual(self.resources.release_owned(intent,
            lambda _rid, _reason, records: {"status": "released",
                "sessions": [outcome(records[0])] }), "released_verified")
        self.db.commit()
        self.resources.acknowledge_verified_release(intent)
        self.db.commit()
        self.assertEqual(self.power.snapshot()["physical_state"], "released_verified")
        self.assertEqual(self.resources.release_owned(intent,
            lambda *_args: self.fail("repeat verification must reuse committed proof")), "released_verified")

        next_id = "new-owned-session"
        self.resources.record_active_session(next_id, self.epoch)
        self.assertEqual(self.power.snapshot()["release_request_id"], intent.request_id)
        self.assertTrue(self.power.snapshot()["release_veto_active"])
        self.assertEqual(self.power.snapshot()["physical_state"], "pending")
        next_receipt = {**self.receipt, "session_id": next_id, "slot_id": "slot-B",
            "owner_id": "owner-B", "host_lease_id": "owner-B", "residency_generation": "generation-B",
            "server_pid": 4243, "server_process_start": "fixture-start-4243",
            "gpu_uuids": ["GPU-fixture-B"], "capability": "e" * 64}
        self.resources.record_session(next_id, next_receipt)
        self.db.commit()
        self.assertEqual(self.resources.release_owned(intent,
            lambda _rid, _reason, records: {"status": "released",
                "sessions": [outcome(records[0])] }), "released_verified")
        self.db.commit()
        self.resources.acknowledge_verified_release(intent)
        self.db.commit()
        self.assertEqual(self.power.snapshot()["physical_state"], "released_verified")
        self.assertEqual(self.power.snapshot()["release_verified_sessions"], 2)

    def test_no_empty_target_rpc_and_close_cap_is_epoch_bound(self):
        intent = self._intent()
        callbacks = []
        self.assertEqual(self.resources.release_owned(intent, lambda *args: callbacks.append(args)),
                         "pending_no_owned_resources")
        self.assertEqual(callbacks, [])
        self.resources.record_active_session(self.session_id, self.epoch)
        changed = {**self.receipt, "daemon_epoch": "e" * 64}
        with self.assertRaises(ResourceControllerError):
            self.resources.record_session(self.session_id, changed)


class OwnedReleaseRpcTests(unittest.TestCase):
    def setUp(self):
        self.backend = _Backend()
        self.server = _server(self.backend)
        self.peer_start = _identity(os.getpid())["process_start"]
        self.server._borrowers[self.backend.session_id] = {
            "pid": os.getpid(), "process_start": self.peer_start,
            "daemon_epoch": self.server._daemon_epoch, "slot_id": self.backend.slot_id,
            "deadline_epoch": time.time() + 30}

    def _close(self):
        with patch.object(supervisor, "process_identity", side_effect=_identity), \
             patch.object(supervisor, "validate_canonical_runtime"):
            return self.server._dispatch({"operation": "observer-close",
                "session_id": self.backend.session_id}, peer_pid=os.getpid(),
                peer_process_start=self.peer_start)

    def _release(self, receipt, *, request_id="f" * 32):
        with patch.object(supervisor, "process_identity", side_effect=_identity), \
             patch.object(supervisor, "validate_canonical_runtime"):
            return self.server._dispatch({"operation": "observer-release-owned", "request_id": request_id,
                "records": [{"session_id": receipt["session_id"], "capability": receipt["capability"]}]})

    def test_owner_close_then_exact_release_requires_pid_vram_and_host_proof(self):
        with patch.object(supervisor, "process_identity", side_effect=_identity), \
             patch.object(supervisor, "validate_canonical_runtime"):
            result = self._close()
            receipt = result["owned_resource_receipt"]
            self.assertEqual(receipt["owner_id"], "host-owner-A")
            self.assertEqual(receipt["residency_generation"], "generation-A")
            released = self._release(receipt)
        self.assertEqual(released["status"], "released")
        self.assertTrue(released["sessions"][0]["process_released"])
        self.assertTrue(released["sessions"][0]["memory_released"])
        self.assertTrue(released["sessions"][0]["host_released"])
        self.assertEqual(self.backend.evictions, 1)
        self.assertNotIn(self.backend.slot_id, self.backend._slots)
        replay = self._release(receipt)
        self.assertEqual(replay, released)
        with self.assertRaises(supervisor.SupervisorError):
            self._release(receipt, request_id="e" * 32)

    def test_dead_borrower_reap_mints_one_shot_receipt_but_live_borrower_stays_bound(self):
        session_id = self.backend.session_id
        self.server._borrowers[session_id]["pid"] = os.getpid() + 100000
        self.server._borrowers[session_id]["process_start"] = "old-process-generation"
        with patch.object(supervisor, "process_start_time", return_value="reused-process-generation"), \
             patch.object(supervisor, "process_identity", side_effect=_identity), \
             patch.object(supervisor, "validate_canonical_runtime"):
            self.server._reap_borrowers()
            self.assertNotIn(session_id, self.server._borrowers)
            self.assertIn(session_id, self.server._reclaimed_closed)
            first = self.server._dispatch({"operation": "observer-reclaim-closed",
                "session_ids": [session_id], "request_id": "a" * 32},
                peer_pid=os.getpid(), peer_process_start=self.peer_start)
            self.assertEqual(first["status"], "available")
            self.assertEqual(first["receipts"][0]["session_id"], session_id)
            retry = self.server._dispatch({"operation": "observer-reclaim-closed",
                "session_ids": [session_id], "request_id": "a" * 32},
                peer_pid=os.getpid(), peer_process_start=self.peer_start)
            self.assertEqual(retry, first)
            unavailable = self.server._dispatch({"operation": "observer-reclaim-closed",
                "session_ids": [session_id], "request_id": "b" * 32},
                peer_pid=os.getpid(), peer_process_start=self.peer_start)
            self.assertEqual(unavailable["status"], "pending")
            self.assertEqual(unavailable["receipts"], [])

    def test_unreadable_live_borrower_is_never_reclaimed(self):
        session_id = self.backend.session_id
        with patch.object(supervisor, "process_start_time", side_effect=PermissionError("unknown")):
            self.server._reap_borrowers()
        self.assertIn(session_id, self.server._borrowers)
        self.assertNotIn(session_id, self.server._reclaimed_closed)

    def test_foreign_close_and_reused_or_active_slots_cannot_be_evicted(self):
        with patch.object(supervisor, "process_identity", side_effect=_identity), \
             patch.object(supervisor, "validate_canonical_runtime"):
            with self.assertRaises(supervisor.SupervisorError):
                self.server._dispatch({"operation": "observer-close", "session_id": self.backend.session_id},
                    peer_pid=os.getpid() + 1, peer_process_start="foreign-start")
            receipt = self._close()["owned_resource_receipt"]
            self.backend.slot.service_lease_id = "foreign-session"
            self.backend._leases["foreign-session"] = self.backend.slot_id
            self.server._borrowers["foreign-session"] = {"pid": os.getpid() + 1,
                "process_start": "foreign-start", "slot_id": self.backend.slot_id}
            stale = self._release(receipt)
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(self.backend.evictions, 0)

    def test_strict_rpc_shape_and_internal_reuse_invalidation(self):
        with patch.object(supervisor, "process_identity", side_effect=_identity), \
             patch.object(supervisor, "validate_canonical_runtime"):
            receipt = self._close()["owned_resource_receipt"]
            with self.assertRaises(supervisor.SupervisorError):
                self.server._dispatch({"operation": "observer-release-owned", "request_id": "f" * 32,
                    "records": [], "owner_id": "caller-chosen"})
            self.server._invalidate_owned_slots({self.backend.slot_id})
            stale = self._release(receipt)
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(self.backend.evictions, 0)

    def test_client_forwards_only_session_and_close_capability(self):
        client = object.__new__(supervisor.SupervisorClient)
        client.observer_status = lambda **_kwargs: {"daemon_epoch": "a" * 64}
        client._validate_owned_release_status = lambda _status: None
        forwarded = {}
        client._observer_request = lambda operation, **parameters: forwarded.update(
            operation=operation, **parameters) or {"status": "pending", "sessions": []}
        record = {"session_id": "session-A", "capability": "d" * 64,
            "daemon_epoch": "a" * 64, "owner_id": "private-owner", "gpu_uuids": ["GPU-private"]}
        result = client.release_owned_observer_resources([record], request_id="f" * 32)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(forwarded["operation"], "observer-release-owned")
        self.assertEqual(forwarded["records"], [{"session_id": "session-A", "capability": "d" * 64}])
        self.assertNotIn("owner_id", str(forwarded))
        with self.assertRaises(supervisor.SupervisorError):
            client.release_owned_observer_resources([], request_id="f" * 32)

    def test_client_rejects_old_epoch_and_releases_current_caps_in_bounded_chunks(self):
        client = object.__new__(supervisor.SupervisorClient)
        client.observer_status = lambda **_kwargs: {"daemon_epoch": "a" * 64}
        client._validate_owned_release_status = lambda _status: None
        chunks = []

        def rpc(operation, **parameters):
            self.assertEqual(operation, "observer-release-owned")
            chunk = parameters["records"]
            self.assertLessEqual(len(chunk), 2)
            chunks.append([item["session_id"] for item in chunk])
            return {"status": "released", "sessions": [
                {"session_id": item["session_id"], "released": True} for item in chunk]}

        client._observer_request = rpc
        records = [{"session_id": f"slot-{index}", "capability": f"{index + 1:064x}",
                    "daemon_epoch": "a" * 64} for index in range(3)]
        records.append({"session_id": "old-epoch", "capability": "f" * 64,
                        "daemon_epoch": "e" * 64})
        result = client.release_owned_observer_resources(records, request_id="f" * 32)
        self.assertEqual(chunks, [["slot-0", "slot-1"], ["slot-2"]])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["sessions"][-1]["reason"], "owned_daemon_epoch_changed")


if __name__ == "__main__":
    unittest.main()
