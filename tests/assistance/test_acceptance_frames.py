"""Focused CPU acceptance checks for frame crash windows and admission."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from project_control.as1_contracts import DurableJob
from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore
from project_control.assistance.frames import ChildFrameSpec, FrameStore


SCOPE = {"principal": "acceptance", "profile": "observer", "project": "repo"}


class _LiveDispatcher:
    """Allow public admission without starting a background worker."""

    def is_alive(self):
        return True


def _service(tmp_path, *, clock=lambda: 100.0):
    service = JobService(tmp_path / "jobs", packets=SQLitePacketStore(tmp_path / "packets"),
                         clock=clock, worker_factory=lambda *_: None)
    service._thread = _LiveDispatcher()
    return service


def _set_running(service, job_id, attempt=1):
    with service._db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT record FROM jobs WHERE id=?", (job_id,)).fetchone()
        job = DurableJob.model_validate_json(row["record"])
        job.status, job.attempt = "running", attempt
        db.execute("UPDATE jobs SET record=?,lease=? WHERE id=?",
                   (job.model_dump_json(), service.clock() + 60, job_id))
        db.execute("UPDATE pa1_frames SET state='running' WHERE job_id=?", (job_id,))
        db.execute("INSERT INTO execution_slots(job,attempt,lease) VALUES(?,?,?)",
                   (job_id, attempt, service.clock() + 60))


def test_restart_replays_unmaterialized_outbox_around_checkpoint(tmp_path):
    service = _service(tmp_path)
    accepted = service.submit(question="checkpoint and replay", access_scope=SCOPE)
    assert accepted["accepted"]
    job_id = accepted["job_id"]
    _set_running(service, job_id)

    # Persist packets and observations, then simulate process loss after their
    # broker transaction but before the packet store confirms either put.
    real_put = service.packets.put
    calls = 0

    def fail_first_put(packet):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated packet-store interruption")
        return real_put(packet)

    service.packets.put = fail_first_put
    try:
        service.observe(job_id, 1, {"text": "checkpointed"}, tool="source")
    except OSError:
        pass
    else:
        raise AssertionError("fault injection did not interrupt outbox materialization")

    # The observation transaction committed despite failed cross-store delivery.
    saved = service._worker_lookup(job_id, access_scope=SCOPE)["observations"]
    first = saved[0]["packet_id"]
    assert [row["packet_id"] for row in saved] == [first]
    service.checkpoint(job_id, 1, saved, access_scope=SCOPE)
    service.packets.put = real_put

    # A fresh service instance represents restart. Reconciliation can safely
    # deliver the stable outbox identity and the checkpoint remains readable.
    restarted = JobService(tmp_path / "jobs", packets=service.packets,
                           clock=service.clock, worker_factory=lambda *_: None,
                           can_execute=lambda *_: False)
    restarted.reconcile()
    with restarted._db() as db:
        row = db.execute("SELECT materialized FROM outbox WHERE id=?", (first,)).fetchone()
        checkpoint = db.execute("SELECT attempt,frames FROM job_checkpoints WHERE job=?", (job_id,)).fetchone()
    assert row["materialized"] == 1
    assert checkpoint["attempt"] == 1
    assert json.loads(checkpoint["frames"])[0]["packet_id"] == first
    # Packet insertion is idempotent on restart and the checkpointed packet is
    # visible through the normal public lookup projection.
    visible = restarted.lookup(job_id, access_scope=SCOPE)
    assert visible["status"] == "ok"
    assert [row["packet_id"] for row in visible["observations"]] == [first]
    assert restarted.packets.lookup(first, access_scope=SCOPE).status == "ok"


def test_cancel_fences_a_late_attempt_from_current_frame_and_public_result(tmp_path):
    service = _service(tmp_path)
    accepted = service.submit(question="cancel then late finish", access_scope=SCOPE)
    assert accepted["accepted"]
    job_id = accepted["job_id"]
    _set_running(service, job_id)

    assert service.cancel(job_id, access_scope=SCOPE)
    assert service.finish(job_id, 1, {"status": "completed", "answer": "late result"}) is False
    current = service.lookup(job_id, access_scope=SCOPE)
    assert current["status"] == "ok"
    assert current["job"]["status"] == "cancelled"
    assert current["job"].get("answer") != "late result"
    with service._db() as db:
        frame = FrameStore(db).get_frame(job_id)
    assert frame.state == "cancelled"


def test_saturated_public_roots_keep_suspended_parent_and_allow_child_dispatch(tmp_path):
    service = _service(tmp_path)
    parent = service.submit(question="suspended public parent", access_scope=SCOPE)
    assert parent["accepted"]
    parent_id = parent["job_id"]
    _set_running(service, parent_id)

    child_id = "acceptance-private-child"
    with service._db() as db:
        db.execute("BEGIN IMMEDIATE")
        frames = FrameStore(db)
        frames.register_wait(parent_id, 1, [ChildFrameSpec(
            child_id, SCOPE, service.clock() + 50, question="bounded dependency")], now=service.clock())
        now = service.clock()
        child = DurableJob(job_id=child_id, mode="investigate", question="bounded dependency",
            status="queued", attempt=0, created_at=datetime.fromtimestamp(now, timezone.utc).isoformat(), findings=[], hints=[],
            evidence_packets=[], unresolved_questions=[], project="repo", scope=SCOPE,
            deadline_epoch=service.clock() + 50)
        db.execute("INSERT INTO jobs(id,scope,request_id,request_hash,record,updated,inquiry) "
                   "VALUES(?,?,?,?,?,?,0)",
                   (child_id, json.dumps(SCOPE, sort_keys=True, separators=(",", ":")), None,
                    "private-child", child.model_dump_json(), now))
        db.execute("DELETE FROM execution_slots WHERE job=?", (parent_id,))
        assert frames.mark_parent_released(parent_id, 1, now=service.clock())

    # Five more public roots fill capacity, while the suspended parent still
    # counts as one of the six public roots. The private child consumes none.
    for index in range(5):
        admitted = service.submit(question=f"foreground-{index}", access_scope=SCOPE)
        assert admitted["accepted"]
    denied = service.submit(question="seventh foreground", access_scope=SCOPE)
    assert denied == {"accepted": False, "reason": "admission_limit"}
    with service._db() as db:
        assert db.execute("SELECT count(*) FROM pa1_frames WHERE parent_id IS NULL").fetchone()[0] == 6
        assert FrameStore(db).get_frame(parent_id).state == "suspended"
        assert FrameStore(db).eligible(child_id, service.clock())
        db.execute("UPDATE jobs SET updated=updated+1000 WHERE id<>?", (child_id,))

    # The eligible internal dependency can claim a production execution slot
    # despite the full public-root budget and without making the parent runnable.
    claimed = service.claim()
    assert claimed is not None and claimed.job_id == child_id
    with service._db() as db:
        assert db.execute("SELECT 1 FROM execution_slots WHERE job=?", (child_id,)).fetchone()
        assert FrameStore(db).get_frame(parent_id).state == "suspended"
