"""Trusted operator reconciliation for terminal orphan execution slots."""
import json
import os
import time

import pytest

from project_control.as1_contracts import DurableJob
from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore
from project_control.assistance.power import PowerPolicy, ReleaseIntent, trusted_operator_control
from project_control.assistance.resources import ResourceController
from project_control.runtime_binding import RELEASE_DIGEST_VARIABLE


JOB_ID = "job-stale-cleanup-123"
OWNER_PID = 2_147_483_647
OWNER_START = "4949350"
GPU_UUIDS = tuple(f"GPU-00000000-0000-0000-0000-{index:012d}" for index in range(1, 5))
RELEASE_DIGEST = "a" * 64


def make_service(tmp_path):
    class Backend:
        _allowed_gpu_uuids = GPU_UUIDS

    service = JobService(tmp_path / "jobs", packets=SQLitePacketStore(tmp_path / "packets"),
                         backend=Backend(), clock=time.time)
    terminal = DurableJob(job_id=JOB_ID, mode="investigate", question="terminal historical job",
        hints=[], status="partial", attempt=2, created_at="2026-10-05T00:00:00Z",
        findings=[], evidence_packets=[], unresolved_questions=[], answer="retained result")
    with service._db() as db:
        db.execute("INSERT INTO jobs(id,scope,request_hash,record,updated,lease,available) VALUES(?,?,?,?,?,?,?)",
            (JOB_ID, "{}", "b" * 64, terminal.model_dump_json(), time.time(), 0, 0))
        db.execute("INSERT INTO execution_slots(job,attempt,lease,owner_pid,owner_start,cleanup_failed) VALUES(?,?,?,?,?,1)",
            (JOB_ID, 1, 0, OWNER_PID, OWNER_START))
        PowerPolicy(db, clock=service.clock).set_release(trusted_operator_control())
    return service, terminal.model_dump_json()


def valid_proof(*, job_id=JOB_ID, attempt=1, owner_pid=OWNER_PID,
                owner_start=OWNER_START, now=None):
    return {
        "format": "PC-AS1-STALE-EXECUTION-CLEANUP/1",
        "job_id": job_id,
        "attempt": attempt,
        "owner_pid": owner_pid,
        "owner_start": owner_start,
        "observed_at": time.time() if now is None else now,
        "release_manifest_sha256": RELEASE_DIGEST,
        "helper": {"unit": "project-control-inference.service", "active_state": "inactive",
                   "main_pid": 0, "kill_mode": "control-group"},
        "owner_process_absent": True,
        "gpu_uuids": list(GPU_UUIDS),
        "compute_processes": [],
        "gpu_memory_mib": {item: 0 for item in GPU_UUIDS},
        "host_conflicts": [],
        "native": {"active_leases": 0, "session_count": 0},
        "process_released": True,
        "memory_released": True,
        "verified": True,
    }


def add_released_row(service, session_id, release_request_id):
    receipt = {
        "format": "PA1-OWNED-RESOURCE/1", "session_id": session_id,
        "daemon_epoch": "e" * 64, "slot_id": f"slot-{session_id}",
        "owner_id": f"owner-{session_id}", "host_lease_id": f"owner-{session_id}",
        "residency_generation": f"generation-{session_id}", "server_pid": 9001,
        "server_process_start": "server-start", "gpu_uuids": [GPU_UUIDS[0]],
        "capability": "c" * 64,
    }
    proof = {
        "format": "PC-PA1-OWNED-RELEASE-PROOF/1", "request_id": release_request_id,
        **{key: receipt[key] for key in ("daemon_epoch", "session_id", "slot_id", "owner_id",
            "host_lease_id", "residency_generation", "server_pid", "server_process_start", "gpu_uuids")},
        "process_released": True, "memory_released": True, "host_released": True,
    }
    with service._db() as db:
        db.execute("""INSERT INTO pa1_owned_resource_sessions
            (session_id,supervisor_epoch,state,close_receipt,release_request_id,release_proof,updated)
            VALUES(?,?,?,?,?,?,?)""",
            (session_id, json.dumps({"daemon_epoch": receipt["daemon_epoch"], "supervisor_pid": 10,
                "supervisor_process_start": "old-supervisor", "runtime_fingerprint": "f" * 64}),
             "released_verified", json.dumps(receipt), release_request_id, json.dumps(proof), time.time()))


def acknowledge_current_release(service, session_id="current-session"):
    with service._db() as db:
        power = PowerPolicy(db, clock=service.clock).snapshot()
    intent = ReleaseIntent(power["release_request_id"], power["release_created_at"],
                           power["release_until"], power["release_reason"])
    add_released_row(service, session_id, intent.request_id)
    with service._db() as db:
        policy = PowerPolicy(db, clock=service.clock)
        resources = ResourceController(db, power_policy=policy, clock=service.clock)
        db.commit()
        resources.acknowledge_verified_release(intent)
    return intent


def invoke(service, proof=None, **overrides):
    args = {"job_id": JOB_ID, "attempt": 1, "owner_pid": OWNER_PID,
            "owner_start": OWNER_START, "proof": proof or valid_proof()}
    args.update(overrides)
    return service.reconcile_stale_execution_slot(trusted_operator_control(), **args)


def test_reconciles_exact_stale_slot_and_preserves_job_and_proof(tmp_path, monkeypatch):
    monkeypatch.setenv(RELEASE_DIGEST_VARIABLE, RELEASE_DIGEST)
    service, original_record = make_service(tmp_path)
    add_released_row(service, "historical-session-a", "old-release-a")
    add_released_row(service, "historical-session-b", "old-release-b")
    current_intent = acknowledge_current_release(service)
    proof = valid_proof()

    result = invoke(service, proof)

    assert result["status"] == "reconciled"
    with service._db() as db:
        assert db.execute("SELECT record FROM jobs WHERE id=?", (JOB_ID,)).fetchone()[0] == original_record
        assert db.execute("SELECT count(*) FROM execution_slots WHERE job=?", (JOB_ID,)).fetchone()[0] == 0
        receipt = db.execute("SELECT * FROM stale_execution_cleanup_audit WHERE receipt_id=?",
                             (result["receipt_id"],)).fetchone()
        assert receipt["job"] == JOB_ID and receipt["slot_attempt"] == 1
        assert receipt["owner_pid"] == OWNER_PID and receipt["owner_start"] == OWNER_START
        assert receipt["release_request_id"] == current_intent.request_id
        assert json.loads(receipt["proof"]) == proof
        assert receipt["proof_sha256"] == result["proof_sha256"]
        resource_rows = db.execute("SELECT session_id,release_request_id FROM pa1_owned_resource_sessions ORDER BY session_id").fetchall()
        assert [(row["session_id"], row["release_request_id"]) for row in resource_rows] == [
            ("current-session", current_intent.request_id),
            ("historical-session-a", "old-release-a"),
            ("historical-session-b", "old-release-b"),
        ]


@pytest.mark.parametrize("change, error", [
    (lambda p: p.update(owner_start="reused"), "identity_or_freshness"),
    (lambda p: p.update(observed_at=time.time() - 61), "identity_or_freshness"),
    (lambda p: p.update(memory_released=False), "release_unproved"),
    (lambda p: p["gpu_memory_mib"].update({GPU_UUIDS[0]: 1}), "gpu_scope_or_memory"),
    (lambda p: p.update(gpu_uuids=GPU_UUIDS[:-1]), "gpu_scope_or_memory"),
    (lambda p: p.update(native={"active_leases": 1, "session_count": 0}), "native_state"),
    (lambda p: p.update(attempt=True), "identity_or_freshness"),
    (lambda p: p["helper"].update(main_pid=False), "helper_state"),
    (lambda p: p.update(native={"active_leases": True, "session_count": 0}), "native_state"),
])
def test_rejects_invalid_physical_cleanup_proof_without_deleting_slot(tmp_path, monkeypatch, change, error):
    monkeypatch.setenv(RELEASE_DIGEST_VARIABLE, RELEASE_DIGEST)
    service, _ = make_service(tmp_path)
    proof = valid_proof()
    change(proof)

    with pytest.raises(ValueError, match=error):
        invoke(service, proof)

    with service._db() as db:
        assert db.execute("SELECT count(*) FROM execution_slots WHERE job=?", (JOB_ID,)).fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM stale_execution_cleanup_audit").fetchone()[0] == 0


def test_requires_trusted_control_and_permanent_release_veto(tmp_path, monkeypatch):
    monkeypatch.setenv(RELEASE_DIGEST_VARIABLE, RELEASE_DIGEST)
    service, _ = make_service(tmp_path)
    with pytest.raises(ValueError, match="trusted_operator_control"):
        service.reconcile_stale_execution_slot(object(), job_id=JOB_ID, attempt=1,
            owner_pid=OWNER_PID, owner_start=OWNER_START, proof=valid_proof())

    with service._db() as db:
        db.execute("BEGIN IMMEDIATE")
        PowerPolicy(db, clock=service.clock).resume(trusted_operator_control())
    with pytest.raises(ValueError, match="permanent_release_veto"):
        invoke(service)
    with service._db() as db:
        assert db.execute("SELECT count(*) FROM execution_slots WHERE job=?", (JOB_ID,)).fetchone()[0] == 1


def test_rejects_present_or_reused_process_and_mismatched_slot(tmp_path, monkeypatch):
    monkeypatch.setenv(RELEASE_DIGEST_VARIABLE, RELEASE_DIGEST)
    service, _ = make_service(tmp_path)
    live_start = service._process_start(os.getpid())
    with pytest.raises(ValueError, match="owner_present_or_reused"):
        invoke(service, valid_proof(owner_pid=os.getpid(), owner_start=live_start),
               owner_pid=os.getpid(), owner_start=live_start)
    with pytest.raises(ValueError, match="exact_owner_mismatch"):
        invoke(service, valid_proof(attempt=2), attempt=2)
    with service._db() as db:
        assert db.execute("SELECT count(*) FROM execution_slots WHERE job=?", (JOB_ID,)).fetchone()[0] == 1


def test_rejects_owned_resource_rows_that_are_not_physically_released(tmp_path, monkeypatch):
    monkeypatch.setenv(RELEASE_DIGEST_VARIABLE, RELEASE_DIGEST)
    service, _ = make_service(tmp_path)
    with service._db() as db:
        db.execute("INSERT INTO pa1_owned_resource_sessions(session_id,supervisor_epoch,state,close_receipt,updated) VALUES(?,?,?,?,?)",
            ("active-session", json.dumps({"daemon_epoch": "c" * 64, "supervisor_pid": 123,
             "supervisor_process_start": "456", "runtime_fingerprint": "d" * 64}),
             "active", None, time.time()))
    with pytest.raises(ValueError, match="owned_resources_not_released"):
        invoke(service)
    with service._db() as db:
        assert db.execute("SELECT count(*) FROM execution_slots WHERE job=?", (JOB_ID,)).fetchone()[0] == 1


def test_prior_release_rows_do_not_substitute_for_current_physical_ack(tmp_path, monkeypatch):
    monkeypatch.setenv(RELEASE_DIGEST_VARIABLE, RELEASE_DIGEST)
    service, _ = make_service(tmp_path)
    add_released_row(service, "historical-session-a", "old-release-a")
    add_released_row(service, "historical-session-b", "old-release-b")

    with pytest.raises(ValueError, match="owned_resource_release_mismatch"):
        invoke(service)

    with service._db() as db:
        assert db.execute("SELECT count(*) FROM execution_slots WHERE job=?", (JOB_ID,)).fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM stale_execution_cleanup_audit").fetchone()[0] == 0


def test_current_ack_cannot_override_an_active_resource_row(tmp_path, monkeypatch):
    monkeypatch.setenv(RELEASE_DIGEST_VARIABLE, RELEASE_DIGEST)
    service, _ = make_service(tmp_path)
    acknowledge_current_release(service)
    with service._db() as db:
        db.execute("""INSERT INTO pa1_owned_resource_sessions
            (session_id,supervisor_epoch,state,close_receipt,updated) VALUES(?,?,?,?,?)""",
            ("still-active", json.dumps({"daemon_epoch": "d" * 64, "supervisor_pid": 11,
                "supervisor_process_start": "active-supervisor", "runtime_fingerprint": "e" * 64}),
             "active", None, time.time()))

    with pytest.raises(ValueError, match="owned_resources_not_released"):
        invoke(service)

    with service._db() as db:
        assert db.execute("SELECT count(*) FROM execution_slots WHERE job=?", (JOB_ID,)).fetchone()[0] == 1
