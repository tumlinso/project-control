"""Broker close and receipt-persistence failure classification."""
from __future__ import annotations

import time

from project_control.as1_contracts import DurableJob
from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore
from project_control.assistance.resources import ResourceController, ResourceControllerError


SCOPE = {"principal": "cleanup-test", "profile": "observer", "project": "fixture"}
EPOCH = {
    "daemon_epoch": "a" * 64,
    "supervisor_pid": 4242,
    "supervisor_process_start": "supervisor-test-start",
    "runtime_fingerprint": "b" * 64,
}
RECEIPT = {
    "format": "PA1-OWNED-RESOURCE/1",
    "session_id": "cleanup-session",
    "daemon_epoch": EPOCH["daemon_epoch"],
    "slot_id": "cleanup-slot",
    "owner_id": "cleanup-owner",
    "host_lease_id": "cleanup-owner",
    "residency_generation": "cleanup-generation",
    "server_pid": 4343,
    "server_process_start": "server-test-start",
    "gpu_uuids": ["GPU-fixture-0001"],
    "capability": "b" * 64,
}


class Backend:
    def __init__(self, *, close_error=False):
        self.close_error = close_error

    def open_sessions(self, *_args, **_kwargs):
        return {"status": "available", "session_ids": [RECEIPT["session_id"]], **EPOCH}

    def close_session(self, _session_id):
        if self.close_error:
            raise OSError("private transport detail")
        return {"released": True, "owned_resource_receipt": RECEIPT}


class Worker:
    def run(self, _request):
        return {"status": "completed", "answer": "bounded answer", "model_turns_used": 0}


def execute_one(tmp_path, backend):
    service = JobService(tmp_path / "jobs", packets=SQLitePacketStore(tmp_path / "packets"),
                         worker_factory=lambda *_args: Worker(), backend=backend)
    service._project_worker_request = lambda request, _calls: request
    job = DurableJob(job_id="job-cleanup-test", mode="investigate", question="fixture question",
        hints=[], status="running", attempt=1, created_at="2026-10-07T00:00:00Z", findings=[],
        evidence_packets=[], unresolved_questions=[], answer=None, project="fixture", scope=SCOPE,
        deadline_epoch=time.time() + 60)
    with service._db() as db:
        db.execute("INSERT INTO jobs(id,scope,request_hash,record,updated,lease,available) VALUES(?,?,?,?,?,?,?)",
            (job.job_id, "{}", "c" * 64, job.model_dump_json(), time.time(), time.time() + 60, 0))
        db.execute("INSERT INTO execution_slots(job,attempt,lease) VALUES(?,?,?)",
            (job.job_id, job.attempt, time.time() + 60))
    return service, job


def cleanup_rows(service, job_id):
    with service._db() as db:
        slot = db.execute("SELECT cleanup_failed FROM execution_slots WHERE job=?", (job_id,)).fetchone()
        resources = db.execute("SELECT state FROM pa1_owned_resource_sessions WHERE session_id=?",
            (RECEIPT["session_id"],)).fetchone()
    return slot, resources


def test_broker_receipt_persistence_commits_after_close(tmp_path):
    service, job = execute_one(tmp_path, Backend())

    service._execute(job)

    slot, resource = cleanup_rows(service, job.job_id)
    assert slot is None
    assert resource["state"] == "idle_owned"
    assert service.last_error is None


def test_broker_reports_receipt_persistence_failure_separately(tmp_path, monkeypatch):
    service, job = execute_one(tmp_path, Backend())

    def fail_persist(*_args, **_kwargs):
        raise RuntimeError("private persistence detail")

    monkeypatch.setattr(ResourceController, "record_session", fail_persist)
    service._execute(job)

    slot, resource = cleanup_rows(service, job.job_id)
    assert slot["cleanup_failed"] == 1
    assert resource["state"] == "active"
    assert service.last_error == "observer_close_receipt_persist_failed"


def test_broker_preserves_only_allowlisted_receipt_error_code(tmp_path, monkeypatch):
    service, job = execute_one(tmp_path, Backend())

    def fail_persist(*_args, **_kwargs):
        raise ResourceControllerError("close_receipt_identity_invalid")

    monkeypatch.setattr(ResourceController, "record_session", fail_persist)
    service._execute(job)

    assert service.last_error == (
        "observer_close_receipt_persist_failed_close_receipt_identity_invalid")


def test_broker_reports_close_failure_separately(tmp_path):
    service, job = execute_one(tmp_path, Backend(close_error=True))

    service._execute(job)

    slot, resource = cleanup_rows(service, job.job_id)
    assert slot["cleanup_failed"] == 1
    assert resource["state"] == "active"
    assert service.last_error == "observer_session_close_failed"
