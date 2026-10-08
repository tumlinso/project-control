"""Fail-closed source-mode recovery for mixed orphan resource rows and slots."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from project_control.as1_jobs import JobService
from project_control.as1_contracts import DurableJob
from project_control.as1_packets import SQLitePacketStore
from project_control.assistance.power import PowerPolicy, ReleaseIntent, trusted_operator_control
from project_control.assistance.resources import ResourceController, ResourceControllerError
from project_control.observer_analysis import (
    SkillsObserverAnalysisProvider, _PHYSICAL_RELEASE_SEAL, _VerifiedPhysicalRelease,
    _fresh_host_release_observation,
)
from project_control.as1_jobs import _host_admission_fence


GPU_UUIDS = tuple(f"GPU-00000000-0000-0000-0000-{index:012d}" for index in range(1, 5))
EPOCH = {
    "daemon_epoch": "a" * 64,
    "supervisor_pid": 98_000_001,
    "supervisor_process_start": "1234567",
    "runtime_fingerprint": "b" * 64,
}
SOURCE_IDENTITY = {
    "format": "PC-SOURCE-IDENTITY/1",
    "project_control_root": "/fixture/project-control/src/project_control",
    "project_control_fingerprint": "c" * 64,
    "todo_orchestrator_root": "/fixture/project-control/src/todo_orchestrator",
    "todo_orchestrator_fingerprint": "f" * 64,
    "receiver_root": "/fixture/project-control/src/project_control/local_runtime",
    "receiver_manifest_sha256": "d" * 64,
    "receiver_fingerprint": "e" * 64,
    "receiver_source_commit": "working-tree",
}


def cleanup_receipts():
    return [
        {"owner_id": "owner-a", "owned_pid": 71_001, "server_process_start": "71001",
         "generation": "generation-a", "gpu_uuids": list(GPU_UUIDS[:2]),
         "released": True, "process_released": True, "memory_released": True},
        {"owner_id": "owner-b", "owned_pid": 71_002, "server_process_start": "71002",
         "generation": "generation-b", "gpu_uuids": list(GPU_UUIDS[2:]),
         "released": True, "process_released": True, "memory_released": True},
    ]


def broker_slots():
    return [
        {"job_id": "failed-job-a", "attempt": 2, "cleanup_pending": True,
         "owner_pid": 72_001, "owner_process_start": "72001"},
        {"job_id": "failed-job-b", "attempt": 3, "cleanup_pending": True,
         "owner_pid": 72_002, "owner_process_start": "72002"},
    ]


def fresh_observations():
    return {
        "service_units": [
            {"unit": "project-control.service", "active_state": "inactive", "main_pid": 0},
            {"unit": "project-control-inference.service", "active_state": "inactive", "main_pid": 0},
        ],
        "gpu": {"observed_at": 1000.0,
            "devices": [{"uuid": uuid, "memory_used_mib": 0.0} for uuid in GPU_UUIDS],
            "processes": []},
        "host": {"active_owners": 0, "active_reservations": 0,
            "active_foreground_intents": 0},
    }


def sealed_recovery_proof(*, slots=None):
    value = {
        "format": "PA1-PHYSICAL-RELEASE/1", "captured_at": 1000.0,
        "status": "released_verified", "released_verified": True,
        **EPOCH, "quiescent": True, "evicted": True, "stopped": True,
        "gpu_uuids": list(GPU_UUIDS), "cleanup_receipts": cleanup_receipts(),
        "orphan_execution_slots": broker_slots() if slots is None else slots,
        "recovery_kind": "source_mode_archived_whole_epoch",
        "source_identity": dict(SOURCE_IDENTITY),
        "source_proof_captured_at": 900.0,
        "fresh_observations": fresh_observations(),
    }
    return _VerifiedPhysicalRelease(_PHYSICAL_RELEASE_SEAL, value, "fixture-host")


def archived_release():
    return {
        "format": "PA1-PHYSICAL-RELEASE/1", "captured_at": 900.0,
        "status": "released_verified", "released_verified": True,
        **EPOCH, "quiescent": True, "evicted": True, "stopped": True,
        "gpu_uuids": list(GPU_UUIDS),
        "cleanup_receipts": [
            {**item, "observation": {"observed_unix": 899.0, "devices": [], "processes": []},
             "memory_baseline": {uuid: 1 for uuid in item["gpu_uuids"]}}
            for item in cleanup_receipts()
        ],
    }


def _add_mixed_rows(db, *, clock):
    db.execute("CREATE TABLE jobs(id TEXT, record TEXT NOT NULL)")
    db.execute("""CREATE TABLE execution_slots(job TEXT PRIMARY KEY, attempt INTEGER NOT NULL,
        owner_pid INTEGER, owner_start TEXT, cleanup_failed INTEGER NOT NULL DEFAULT 0)""")
    power = PowerPolicy(db, clock=clock)
    resources = ResourceController(db, power_policy=power, clock=clock)
    control = trusted_operator_control()
    intent = power.set_release(control, reason="operator-requested-recovery")
    db.commit()

    for index, session_id in enumerate(("orphan-a", "orphan-b")):
        resources.record_active_session(session_id, EPOCH)
        db.execute("UPDATE pa1_owned_resource_sessions SET release_request_id=? WHERE session_id=?",
                   (intent.request_id, session_id))
    prior_receipts = {}
    for index, (session_id, physical) in enumerate(zip(("closed-a", "closed-b"), cleanup_receipts())):
        resources.record_active_session(session_id, EPOCH)
        receipt = {"format": "PA1-OWNED-RESOURCE/1", "session_id": session_id,
            "daemon_epoch": EPOCH["daemon_epoch"], "slot_id": f"slot-{index}",
            "owner_id": physical["owner_id"], "host_lease_id": physical["owner_id"],
            "residency_generation": physical["generation"], "server_pid": physical["owned_pid"],
            "server_process_start": physical["server_process_start"],
            "gpu_uuids": physical["gpu_uuids"], "capability": str(index + 1) * 64}
        resources.record_session(session_id, receipt)
        db.execute("UPDATE pa1_owned_resource_sessions SET state='release_pending',release_request_id=? WHERE session_id=?",
                   (intent.request_id, session_id))
        prior_receipts[session_id] = receipt

    for slot in broker_slots():
        db.execute("INSERT INTO jobs(id,record) VALUES(?,?)",
                   (slot["job_id"], json.dumps({"status": "partial", "attempt": slot["attempt"]})))
        db.execute("""INSERT INTO execution_slots(job,attempt,owner_pid,owner_start,cleanup_failed)
            VALUES(?,?,?,?,1)""", (slot["job_id"], slot["attempt"], slot["owner_pid"],
                                    slot["owner_process_start"]))
    db.commit()
    return resources, control, intent, prior_receipts


def _absence_proof_patches():
    return (
        patch("project_control.observer_analysis._source_identity_snapshot",
              return_value=SOURCE_IDENTITY),
        patch("project_control.assistance.resources._process_start_time", return_value=None),
        patch("project_control.assistance.resources.os.kill", side_effect=ProcessLookupError),
    )


def test_archived_source_verifier_mints_only_after_fresh_local_rechecks(monkeypatch):
    provider = SkillsObserverAnalysisProvider()
    provider._allowed_gpu_uuids = GPU_UUIDS
    monkeypatch.setattr(provider, "_get_backend", lambda: pytest.fail("must_not_construct_backend"))
    monkeypatch.setattr("project_control.observer_analysis._source_identity_snapshot",
                        lambda: dict(SOURCE_IDENTITY))
    monkeypatch.setattr("project_control.observer_analysis._require_process_absent", lambda *_a, **_k: None)
    monkeypatch.setattr("project_control.observer_analysis._inactive_service_state",
        lambda unit: {"unit": unit, "active_state": "inactive", "main_pid": 0})
    monkeypatch.setattr("project_control.observer_analysis._fresh_gpu_release_observation",
        lambda _uuids: fresh_observations()["gpu"])
    monkeypatch.setattr("project_control.observer_analysis._fresh_host_release_observation",
        lambda: fresh_observations()["host"])
    monkeypatch.setattr("project_control.observer_analysis._proc_start_time", lambda _pid: None)

    proof = provider.verify_archived_physical_release(archived_release(), broker_status={
        "active_work": [], "active_work_truncated": False,
        "active_execution_slots": broker_slots(), "active_execution_slots_truncated": False,
    })

    assert isinstance(proof, _VerifiedPhysicalRelease)
    assert proof["recovery_kind"] == "source_mode_archived_whole_epoch"
    assert proof["source_identity"] == SOURCE_IDENTITY
    assert proof["daemon_epoch"] == EPOCH["daemon_epoch"]
    assert proof["orphan_execution_slots"] == broker_slots()
    assert proof["fresh_observations"]["gpu"]["processes"] == []


@pytest.mark.parametrize("row", ["active_owner", "intent_owner", "reservation", "foreground_intent"])
def test_host_fresh_observation_rejects_all_admission_states(tmp_path, monkeypatch, row):
    database = tmp_path / "physical-resources.sqlite3"
    db = sqlite3.connect(database)
    db.executescript("""
        CREATE TABLE host_owners(id TEXT, state TEXT);
        CREATE TABLE host_reservations(owner_id TEXT, state TEXT);
        CREATE TABLE host_foreground_intents(id TEXT, state TEXT);
    """)
    if row == "active_owner":
        db.execute("INSERT INTO host_owners VALUES('owner','active')")
    elif row == "intent_owner":
        db.execute("INSERT INTO host_owners VALUES('owner','intent')")
    elif row == "reservation":
        db.execute("INSERT INTO host_reservations VALUES('owner','active')")
    else:
        db.execute("INSERT INTO host_foreground_intents VALUES('intent','active')")
    db.commit()
    db.close()

    class Host:
        def __init__(self, *, create):
            assert create is False
            self.database = database

        def connect(self, *, readonly=False):
            assert readonly is True
            return sqlite3.connect(f"file:{database}?mode=ro", uri=True)

    monkeypatch.setattr("todo_orchestrator.background.host.HostCoordinator", Host)
    with pytest.raises(RuntimeError, match="host_owner_reappeared"):
        _fresh_host_release_observation()


def test_host_admission_fence_serializes_new_reservations_without_writing(tmp_path, monkeypatch):
    root = tmp_path / "host-runtime"
    root.mkdir()
    database = root / "physical-resources.sqlite3"
    db = sqlite3.connect(database)
    db.execute("CREATE TABLE retained(value TEXT)")
    db.execute("INSERT INTO retained VALUES('unchanged')")
    db.commit()
    db.close()
    monkeypatch.setenv("TODO_BACKGROUND_HOST_RUNTIME_DIR", str(root))

    with _host_admission_fence(max_seconds=2) as fence:
        fence.check()
        contender = sqlite3.connect(database, timeout=0.01, isolation_level=None)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("BEGIN IMMEDIATE")
        finally:
            contender.close()

    db = sqlite3.connect(database)
    try:
        assert db.execute("SELECT value FROM retained").fetchone()[0] == "unchanged"
    finally:
        db.close()


def test_host_admission_fence_fails_closed_without_creating_database(tmp_path, monkeypatch):
    root = tmp_path / "missing-host-runtime"
    root.mkdir()
    monkeypatch.setenv("TODO_BACKGROUND_HOST_RUNTIME_DIR", str(root))
    with pytest.raises(RuntimeError, match="host_admission_fence_unavailable"):
        with _host_admission_fence():
            pytest.fail("missing_host_database_must_not_open")
    assert not (root / "physical-resources.sqlite3").exists()


@pytest.mark.parametrize("failure, expected", [
    ("occupied_gpu", "gpu_memory_not_zero"),
    ("active_host", "host_owner_reappeared"),
    ("active_unit", "service_not_inactive"),
    ("source_changes_during_check", "source_identity_changed"),
    ("reused_process", "present_or_reused"),
])
def test_archived_source_verifier_fails_closed_on_fresh_observation_failures(
        monkeypatch, failure, expected):
    provider = SkillsObserverAnalysisProvider()
    provider._allowed_gpu_uuids = GPU_UUIDS
    monkeypatch.setattr(provider, "_get_backend", lambda: pytest.fail("must_not_construct_backend"))
    if failure == "source_changes_during_check":
        changed = dict(SOURCE_IDENTITY, todo_orchestrator_fingerprint="9" * 64)
        identities = iter((dict(SOURCE_IDENTITY), changed))
        monkeypatch.setattr("project_control.observer_analysis._source_identity_snapshot",
                            lambda: next(identities))
    else:
        monkeypatch.setattr("project_control.observer_analysis._source_identity_snapshot",
                            lambda: dict(SOURCE_IDENTITY))
    if failure == "occupied_gpu":
        def gpu(_uuids):
            raise RuntimeError("physical_release_gpu_memory_not_zero")
        monkeypatch.setattr("project_control.observer_analysis._fresh_gpu_release_observation", gpu)
    else:
        monkeypatch.setattr("project_control.observer_analysis._fresh_gpu_release_observation",
                            lambda _uuids: fresh_observations()["gpu"])
    monkeypatch.setattr("project_control.observer_analysis._fresh_host_release_observation",
        lambda: (_ for _ in ()).throw(RuntimeError("physical_release_host_owner_reappeared"))
        if failure == "active_host" else fresh_observations()["host"])
    monkeypatch.setattr("project_control.observer_analysis._inactive_service_state",
        lambda unit: (_ for _ in ()).throw(RuntimeError("physical_release_service_not_inactive"))
        if failure == "active_unit" and unit == "project-control.service" else
        {"unit": unit, "active_state": "inactive", "main_pid": 0})
    monkeypatch.setattr("project_control.observer_analysis._proc_start_time", lambda _pid: None)
    monkeypatch.setattr("project_control.observer_analysis._require_process_absent",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("physical_release_supervisor_present_or_reused"))
        if failure == "reused_process" else None)
    with pytest.raises(RuntimeError, match=expected):
        provider.verify_archived_physical_release(archived_release(), broker_status={
            "active_work": [], "active_work_truncated": False,
            "active_execution_slots": broker_slots(), "active_execution_slots_truncated": False,
        })


def test_mixed_epoch_recovery_preserves_close_receipts_and_releases_only_exact_slots(monkeypatch):
    db = sqlite3.connect(":memory:")
    clock = lambda: 1000.0
    resources, control, intent, prior_receipts = _add_mixed_rows(db, clock=clock)
    proof = sealed_recovery_proof()
    try:
        with _absence_proof_patches()[0], _absence_proof_patches()[1], _absence_proof_patches()[2]:
            recovered = resources.record_archived_epoch_recovery_after_physical_release(
                control, intent, proof)
            assert recovered == ["closed-a", "closed-b", "orphan-a", "orphan-b"]
            db.commit()

        # Simulate an operator interruption after the durable resource audit
        # commits but before the separate PowerPolicy release ACK is written.
        with _absence_proof_patches()[0], _absence_proof_patches()[1], _absence_proof_patches()[2]:
            resumed = resources.record_archived_epoch_recovery_after_physical_release(
                control, intent, sealed_recovery_proof())
        assert resumed == recovered
        assert db.execute("SELECT count(*) FROM pa1_orphan_resource_reconciliation_audit").fetchone()[0] == 1

        rows = db.execute("SELECT session_id,state,close_receipt,release_proof FROM "
                          "pa1_owned_resource_sessions ORDER BY session_id").fetchall()
        assert all(row[1] == "released_verified" for row in rows)
        assert all(len(json.loads(row[2])["receipt_id"]) == 32 for row in rows)
        for session_id, _state, close_text, proof_text in rows:
            close = json.loads(close_text)
            assert close["format"] == "PA1-ORPHANED-SESSION-RELEASE/1"
            aggregate = json.loads(proof_text)
            preserved = aggregate["recovery_context"]["preserved_close_receipts"]
            assert preserved[session_id] == prior_receipts.get(session_id)
        ack = resources.verified_release_ack(intent)
        assert ack.target_session_ids == tuple(recovered)
        resources.acknowledge_verified_release(intent)
        assert resources.power_policy.snapshot()["physical_state"] == "released_verified"
    finally:
        db.close()


@pytest.mark.parametrize("mutation, expected", [
    ("active_job", "controller_work_not_quiescent"),
    ("release_veto", "permanent_release_veto_required"),
    ("unrelated_epoch", "orphaned_epoch_session_set_incomplete"),
    ("receipt_owner", "orphaned_epoch_physical_identity_mismatch"),
    ("source_identity", "source_identity_mismatch"),
    ("source_identity_missing", "source_identity_mismatch"),
    ("occupied_gpu", "fresh_observation_invalid"),
    ("live_or_reused_owner", "present_or_reused"),
])
def test_mixed_epoch_recovery_rejects_unsafe_state_without_mutation(monkeypatch, mutation, expected):
    db = sqlite3.connect(":memory:")
    clock = lambda: 1000.0
    resources, control, intent, _prior_receipts = _add_mixed_rows(db, clock=clock)
    proof = sealed_recovery_proof()
    if mutation == "active_job":
        db.execute("UPDATE jobs SET record=? WHERE id='failed-job-a'",
                   (json.dumps({"status": "running", "attempt": 2}),))
    elif mutation == "release_veto":
        db.execute("UPDATE pa1_power_state SET release_veto=0 WHERE singleton=1")
    elif mutation == "unrelated_epoch":
        other = dict(EPOCH, daemon_epoch="f" * 64)
        resources.record_active_session("foreign-session", other)
        db.execute("UPDATE pa1_owned_resource_sessions SET release_request_id=? WHERE session_id=?",
                   (intent.request_id, "foreign-session"))
    elif mutation == "receipt_owner":
        proof["cleanup_receipts"][0]["owner_id"] = "foreign-owner"
    elif mutation == "source_identity":
        proof["source_identity"]["project_control_fingerprint"] = "9" * 64
    elif mutation == "source_identity_missing":
        proof.pop("source_identity")
    elif mutation == "occupied_gpu":
        proof["fresh_observations"]["gpu"]["devices"][0]["memory_used_mib"] = 64.0
    db.commit()
    try:
        source = SOURCE_IDENTITY if mutation != "source_identity" else dict(SOURCE_IDENTITY)
        if mutation == "source_identity":
            proof["source_identity"]["project_control_fingerprint"] = "9" * 64
        with patch("project_control.observer_analysis._source_identity_snapshot", return_value=source), \
             patch("project_control.assistance.resources._process_start_time",
                   return_value="different-start" if mutation == "live_or_reused_owner" else None), \
             patch("project_control.assistance.resources.os.kill", side_effect=ProcessLookupError):
            with pytest.raises(ResourceControllerError, match=expected):
                resources.record_archived_epoch_recovery_after_physical_release(control, intent, proof)
            assert resources.power_policy.snapshot()["physical_state"] == "pending"
            assert db.execute("SELECT count(*) FROM pa1_orphan_resource_reconciliation_audit").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM execution_slots").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM pa1_owned_resource_sessions WHERE state='released_verified'").fetchone()[0] == 0
    finally:
        db.close()


def test_job_service_operator_recovery_runs_resource_ack_before_exact_slot_removal(tmp_path, monkeypatch):
    proof = sealed_recovery_proof()

    class Backend:
        _allowed_gpu_uuids = GPU_UUIDS

        @staticmethod
        def verify_archived_physical_release(_archive, *, broker_status):
            assert broker_status["active_work"] == []
            assert broker_status["active_execution_slots"] == broker_slots()
            return proof

    service = JobService(tmp_path / "jobs", packets=SQLitePacketStore(tmp_path / "packets"),
                         backend=Backend(), clock=lambda: 1000.0)
    try:
        with service._db() as db:
            power = PowerPolicy(db, clock=service.clock)
            resources = ResourceController(db, power_policy=power, clock=service.clock)
            control = trusted_operator_control()
            intent = power.set_release(control, reason="operator-requested-recovery")
            for session_id in ("orphan-a", "orphan-b"):
                resources.record_active_session(session_id, EPOCH)
                db.execute("UPDATE pa1_owned_resource_sessions SET release_request_id=? WHERE session_id=?",
                           (intent.request_id, session_id))
            for index, physical in enumerate(cleanup_receipts()):
                session_id = ("closed-a", "closed-b")[index]
                resources.record_active_session(session_id, EPOCH)
                receipt = {"format": "PA1-OWNED-RESOURCE/1", "session_id": session_id,
                    "daemon_epoch": EPOCH["daemon_epoch"], "slot_id": f"slot-{index}",
                    "owner_id": physical["owner_id"], "host_lease_id": physical["owner_id"],
                    "residency_generation": physical["generation"], "server_pid": physical["owned_pid"],
                    "server_process_start": physical["server_process_start"],
                    "gpu_uuids": physical["gpu_uuids"], "capability": str(index + 1) * 64}
                resources.record_session(session_id, receipt)
                db.execute("UPDATE pa1_owned_resource_sessions SET state='release_pending',release_request_id=? WHERE session_id=?",
                           (intent.request_id, session_id))
            for slot in broker_slots():
                job_record = DurableJob(job_id=slot["job_id"], mode="investigate",
                    question="retained", hints=[], status="partial", attempt=slot["attempt"],
                    created_at="2026-10-08T00:00:00Z", findings=[], evidence_packets=[],
                    unresolved_questions=[])
                db.execute("INSERT INTO jobs(id,scope,request_hash,record,updated,lease,available) VALUES(?,?,?,?,?,?,?)",
                    (slot["job_id"], "{}", "b" * 64,
                     job_record.model_dump_json(), 1000.0, 0.0, 0.0))
                db.execute("""INSERT INTO execution_slots(job,attempt,lease,owner_pid,owner_start,cleanup_failed)
                    VALUES(?,?,?,?,?,1)""", (slot["job_id"], slot["attempt"], 0.0,
                        slot["owner_pid"], slot["owner_process_start"]))
        monkeypatch.setattr("project_control.observer_analysis._source_identity_snapshot",
                            lambda: dict(SOURCE_IDENTITY))
        monkeypatch.setattr("project_control.assistance.resources._process_start_time", lambda _pid: None)
        monkeypatch.setattr("project_control.assistance.resources.os.kill",
                            lambda *_a, **_k: (_ for _ in ()).throw(ProcessLookupError()))
        monkeypatch.setattr("project_control.observer_analysis._source_identity_snapshot",
                            lambda: dict(SOURCE_IDENTITY))
        monkeypatch.setattr("project_control.observer_analysis._require_process_absent", lambda *_a, **_k: None)
        monkeypatch.setattr(JobService, "_process_start", staticmethod(lambda _pid: None))
        class FakeFence:
            def check(self):
                return None
        @contextmanager
        def fake_host_fence():
            yield FakeFence()
        monkeypatch.setattr("project_control.as1_jobs._host_admission_fence", fake_host_fence)

        result = service.recover_archived_execution_cleanup(trusted_operator_control(), archived_proof=archived_release())

        assert result["status"] == "reconciled"
        assert result["sessions"] == ["closed-a", "closed-b", "orphan-a", "orphan-b"]
        assert len(result["execution_slots"]) == 2
        with service._db() as db:
            assert db.execute("SELECT count(*) FROM execution_slots").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM stale_execution_cleanup_audit").fetchone()[0] == 2
            assert PowerPolicy(db, clock=service.clock).snapshot()["physical_state"] == "released_verified"
    finally:
        service.shutdown()
