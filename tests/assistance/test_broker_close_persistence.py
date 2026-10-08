"""Broker close and receipt-persistence failure classification."""
from __future__ import annotations

import sqlite3
import time

from project_control.as1_contracts import DurableJob
from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore
from project_control.assistance.resources import ResourceController, ResourceControllerError
from project_control.observer_analysis import _orphan_cleanup_slots_from_status


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
        slot = db.execute("SELECT cleanup_failed,cleanup_error_code FROM execution_slots WHERE job=?", (job_id,)).fetchone()
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
    assert slot["cleanup_error_code"] == "observer_close_receipt_persist_failed"
    assert resource["state"] == "active"
    assert service.last_error == "observer_close_receipt_persist_failed"


def test_broker_preserves_only_allowlisted_receipt_error_code(tmp_path, monkeypatch):
    service, job = execute_one(tmp_path, Backend())

    def fail_persist(*_args, **_kwargs):
        raise ResourceControllerError("close_receipt_identity_invalid")

    monkeypatch.setattr(ResourceController, "record_session", fail_persist)
    service._execute(job)

    slot, _resource = cleanup_rows(service, job.job_id)
    assert slot["cleanup_error_code"] == (
        "observer_close_receipt_persist_failed_close_receipt_identity_invalid")
    assert service.last_error == (
        "observer_close_receipt_persist_failed_close_receipt_identity_invalid")


def test_broker_reports_close_failure_separately(tmp_path):
    service, job = execute_one(tmp_path, Backend(close_error=True))

    service._execute(job)

    slot, resource = cleanup_rows(service, job.job_id)
    assert slot["cleanup_failed"] == 1
    assert slot["cleanup_error_code"] == "observer_session_close_failed"
    assert resource["state"] == "active"
    assert service.last_error == "observer_session_close_failed"


def test_broker_persists_only_allowlisted_cleanup_code(tmp_path):
    service, job = execute_one(tmp_path, Backend(close_error=True))

    service._execute(job)

    slot, _resource = cleanup_rows(service, job.job_id)
    assert slot["cleanup_failed"] == 1
    assert slot["cleanup_error_code"] == "observer_session_close_failed"
    assert "private transport detail" not in str(dict(slot))


def test_cleanup_error_code_migration_preserves_old_rows_and_is_idempotent(tmp_path):
    directory = tmp_path / "jobs"
    directory.mkdir()
    path = directory / "jobs.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE execution_slots(
            job TEXT PRIMARY KEY, attempt INTEGER NOT NULL, lease REAL NOT NULL,
            owner_pid INTEGER, owner_start TEXT, cleanup_failed INTEGER NOT NULL DEFAULT 0)""")
        db.execute("INSERT INTO execution_slots VALUES(?,?,?,?,?,?)",
            ("legacy-job", 4, 123.0, 987, "legacy-process-start", 1))

    first = JobService(directory, packets=SQLitePacketStore(tmp_path / "packets"))
    first.shutdown(timeout=0)
    second = JobService(directory, packets=SQLitePacketStore(tmp_path / "packets"))
    with second._db() as db:
        columns = [row["name"] for row in db.execute("PRAGMA table_info(execution_slots)")]
        row = db.execute("SELECT * FROM execution_slots WHERE job='legacy-job'").fetchone()
    assert columns.count("cleanup_error_code") == 1
    assert row["attempt"] == 4
    assert row["owner_pid"] == 987
    assert row["owner_start"] == "legacy-process-start"
    assert row["cleanup_failed"] == 1
    assert row["cleanup_error_code"] is None


def test_demand_work_status_reports_cleanup_degraded_capacity(tmp_path):
    service, job = execute_one(tmp_path, Backend())
    with service._db() as db:
        db.execute("UPDATE execution_slots SET cleanup_failed=1,cleanup_error_code=? WHERE job=?",
            ("observer_session_close_failed", job.job_id))

    status = service.demand_work_status()

    assert status["execution_capacity"] == {
        "total": 2, "occupied": 1, "available": 1,
        "degraded_by_failed_cleanup": True,
    }
    assert status["execution_cleanup_errors"] == [{"job_id": job.job_id, "attempt": 1,
        "cleanup_error_code": "observer_session_close_failed"}]


def test_demand_status_slot_projection_remains_accepted_by_orphan_verifier(tmp_path, monkeypatch):
    service, job = execute_one(tmp_path, Backend())
    terminal = job.model_copy(update={"status": "failed"})
    with service._db() as db:
        db.execute("UPDATE jobs SET record=? WHERE id=?", (terminal.model_dump_json(), job.job_id))
        db.execute("""UPDATE execution_slots SET owner_pid=321,owner_start='11111',
            cleanup_failed=1,cleanup_error_code=? WHERE job=?""",
            ("observer_session_close_failed", job.job_id))
    monkeypatch.setattr("project_control.observer_analysis._proc_start_time",
                        lambda _pid: "22222")

    status = service.demand_work_status()

    assert status["active_work"] == []
    slot = status["active_execution_slots"][0]
    assert set(slot) == {"job_id", "attempt", "cleanup_pending", "owner_pid", "owner_process_start"}
    assert status["execution_cleanup_errors"] == [{"job_id": job.job_id, "attempt": 1,
        "cleanup_error_code": "observer_session_close_failed"}]
    assert _orphan_cleanup_slots_from_status(status) == [{"job_id": job.job_id,
        "attempt": 1, "cleanup_pending": True, "owner_pid": 321,
        "owner_process_start": "11111"}]
