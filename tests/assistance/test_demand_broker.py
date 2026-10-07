"""Broker demand gates and stop proofs exercise real durable admission."""
import json
import time
import threading
from unittest.mock import patch
import pytest

from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore
from project_control.assistance.power import trusted_operator_control
from project_control.assistance.power import PowerPolicy
from project_control.assistance.resources import ResourceController
from project_control.observer_analysis import _PHYSICAL_RELEASE_SEAL, _VerifiedPhysicalRelease


SCOPE = {'principal': 'tester', 'profile': 'observer', 'project': 'pc'}


def wait(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError('bounded wait expired')


def test_fresh_explicit_inquiry_ensures_runtime_but_fresh_cache_does_not(tmp_path):
    starts = []

    def worker_factory(_service, job):
        class Worker:
            def run(self, _request):
                return {'status': 'completed', 'answer': f'answer for {job.question}'}
        return Worker()

    service = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
        worker_factory=worker_factory, freshness_provider=lambda _job: {'fresh': True})
    service.demand_runtime_ready = lambda **kwargs: starts.append(kwargs) or {'status': 'ready'}
    service.start()
    try:
        first = service.inquire('What changed?', SCOPE, foreground_timeout=0)
        assert first['status'] in {'thinking', 'completed'}
        assert len(starts) == 1 and isinstance(starts[0].get('deadline_epoch'), float)
        with service._db() as db:
            job_id = db.execute('SELECT id FROM jobs ORDER BY updated DESC LIMIT 1').fetchone()[0]
        wait(lambda: service.lookup(job_id, access_scope=SCOPE).get('job', {}).get('status') == 'completed'
            and service._settled(job_id))
        completed = service.inquire('What changed?', SCOPE, foreground_timeout=0)
        assert completed['job']['answer'] == 'answer for What changed?'
        assert len(starts) == 1
    finally:
        service.shutdown()


def test_runtime_identity_failure_prevents_new_durable_admission(tmp_path):
    service = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'))

    def unavailable(**_kwargs):
        raise RuntimeError('runtime_identity_mismatch')

    service.demand_runtime_ready = unavailable
    result = service.inquire('Use the model', SCOPE, foreground_timeout=0)
    assert result == {'status': 'unavailable', 'reason': 'runtime_identity_mismatch'}
    with service._db() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0


def test_stop_cancels_queued_work_and_requires_zero_live_owner_admissions(tmp_path):
    entered, release = threading.Event(), threading.Event()
    class Backend:
        def central_status(self, *, deadline_epoch=None):
            return {'status': 'available', 'running': True, 'active_leases': 0,
                'active_admissions': 0, 'supervisor_process_start': '12345',
                'supervisor_pid': 123, 'daemon_epoch': 'b' * 64,
                'runtime_fingerprint': 'c' * 64, 'slots': [], 'source_sha256': 'a' * 64}
        def open_sessions(self, *_args, **_kwargs):
            return {'status': 'available', 'session_ids': ['session']}
        def close_session(self, _session):
            return {'released': True}

    def worker_factory(_service, _job):
        class Worker:
            def run(self, _request):
                entered.set()
                release.wait(3)
                return {'status': 'completed', 'answer': 'finished'}
        return Worker()

    service = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
                         backend=Backend(), worker_factory=worker_factory).start()
    accepted = service.submit(question='queued work', access_scope=SCOPE)
    assert accepted['accepted'] is True
    try:
        assert entered.wait(2)
        first = service.coordinate_demand_stop(trusted_operator_control(), timeout=.1)
        assert first['active_work_cancelled'] is False
        # The broker slot still represents work whose exact cleanup has not
        # completed, even though the fake owner reports no active lease.
        assert first['owned_resources_released'] is False
        job = service.lookup(accepted['job_id'], access_scope=SCOPE)
        assert job['status'] == 'ok' and job['job']['status'] == 'cancelled'
        release.set()
        wait(lambda: not service._active_work_snapshot()['execution_slots'])
        final = service.coordinate_demand_stop(trusted_operator_control(), timeout=.2)
        assert final['active_work_cancelled'] is True
        assert final['owned_resources_released'] is True
    finally:
        release.set()
        service.shutdown()


def test_stop_fails_closed_while_owner_reports_active_admission(tmp_path):
    class Backend:
        def central_status(self, *, deadline_epoch=None):
            return {'status': 'available', 'running': True, 'active_leases': 1,
                'active_admissions': 1, 'supervisor_process_start': '12345',
                'supervisor_pid': 123, 'daemon_epoch': 'b' * 64,
                'runtime_fingerprint': 'c' * 64, 'slots': [], 'source_sha256': 'a' * 64}

    service = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
                         backend=Backend(), worker_factory=lambda _service, _job: object())
    receipt = service.coordinate_demand_stop(trusted_operator_control(), timeout=.1)
    assert receipt['active_work_cancelled'] is True
    assert receipt['owned_resources_released'] is False
    with service._db() as db:
        state = db.execute('SELECT release_veto FROM pa1_power_state WHERE singleton=1').fetchone()
        assert state[0] == 1


def test_stop_releases_untracked_idle_warm_slots_before_reporting_physical_release(tmp_path):
    class Backend:
        slots = [{'slot_id': 'owned-slot', 'owner_id': 'owner-a', 'server_pid': 456,
                  'gpu_uuids': ['GPU-a'], 'state': 'idle', 'leased': False}]
        status_calls = 0
        def central_status(self, *, deadline_epoch=None):
            self.status_calls += 1
            return {'status': 'available', 'running': True, 'active_leases': 0,
                'active_admissions': 0, 'supervisor_pid': 123,
                'supervisor_process_start': '12345', 'daemon_epoch': 'b' * 64,
                'runtime_fingerprint': 'c' * 64, 'source_sha256': 'a' * 64,
                'slots': list(self.slots)}
        def release_idle_runtime(self, *, deadline_epoch):
            self.slots = []
            return {'status': 'released_verified', 'released_verified': True,
                    'supervisor_pid': 123, 'supervisor_process_start': '12345',
                    'daemon_epoch': 'b' * 64, 'runtime_fingerprint': 'c' * 64,
                    'quiescent': True,
                    'evicted': True, 'stopped': True,
                    'cleanup_receipts': [{'owner_id': 'owner-a', 'owned_pid': 456,
                        'gpu_uuids': ['GPU-a'], 'released': True,
                        'process_released': True, 'memory_released': True}]}

    backend = Backend()
    service = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
                         backend=backend)
    receipt = service.coordinate_demand_stop(trusted_operator_control(), timeout=.2)
    assert receipt['active_work_cancelled'] is True
    assert receipt['owned_resources_released'] is True
    assert backend.slots == []
    assert backend.status_calls == 1  # no status request after the owner has stopped


def test_stop_does_not_claim_release_when_idle_slot_cleanup_proof_fails(tmp_path):
    class Backend:
        slots = [{'slot_id': 'owned-slot', 'owner_id': 'owner-a', 'server_pid': 456,
                  'gpu_uuids': ['GPU-a'], 'state': 'idle', 'leased': False}]
        def central_status(self, *, deadline_epoch=None):
            return {'status': 'available', 'running': True, 'active_leases': 0,
                'active_admissions': 0, 'supervisor_pid': 123,
                'supervisor_process_start': '12345', 'daemon_epoch': 'b' * 64,
                'runtime_fingerprint': 'c' * 64, 'source_sha256': 'a' * 64,
                'slots': list(self.slots)}
        def release_idle_runtime(self, *, deadline_epoch):
            return {'status': 'released_verified', 'released_verified': True,
                    'supervisor_pid': 123, 'supervisor_process_start': '12345',
                    'daemon_epoch': 'b' * 64, 'runtime_fingerprint': 'c' * 64,
                    'quiescent': True,
                    'evicted': True, 'stopped': False}

    service = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
                         backend=Backend())
    receipt = service.coordinate_demand_stop(trusted_operator_control(), timeout=.2)
    assert receipt['active_work_cancelled'] is True
    assert receipt['owned_resources_released'] is False


def test_stop_rejects_cleanup_receipt_for_different_idle_slot(tmp_path):
    class Backend:
        slots = [{'slot_id': 'owned-slot', 'owner_id': 'owner-a', 'server_pid': 456,
                  'gpu_uuids': ['GPU-a'], 'state': 'idle', 'leased': False}]
        def central_status(self, *, deadline_epoch=None):
            return {'status': 'available', 'active_leases': 0,
                'active_admissions': 0, 'supervisor_pid': 123,
                'supervisor_process_start': '12345', 'daemon_epoch': 'b' * 64,
                'runtime_fingerprint': 'c' * 64, 'source_sha256': 'a' * 64,
                'slots': list(self.slots)}
        def release_idle_runtime(self, *, deadline_epoch):
            self.slots = []
            return {'status': 'released_verified', 'released_verified': True,
                    'supervisor_pid': 123, 'supervisor_process_start': '12345',
                    'daemon_epoch': 'b' * 64, 'runtime_fingerprint': 'c' * 64,
                    'quiescent': True,
                    'evicted': True, 'stopped': True,
                    'cleanup_receipts': [{'owner_id': 'owner-b', 'owned_pid': 456,
                        'gpu_uuids': ['GPU-a'], 'released': True,
                        'process_released': True, 'memory_released': True}]}

    service = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
                         backend=Backend())
    receipt = service.coordinate_demand_stop(trusted_operator_control(), timeout=.2)
    assert receipt['active_work_cancelled'] is True
    assert receipt['owned_resources_released'] is False


def test_stop_accepts_authenticated_empty_slots_as_no_gpu_release_proof(tmp_path):
    calls = []
    class Backend:
        def central_status(self, *, deadline_epoch=None):
            return {'status': 'available', 'active_leases': 0,
                'active_admissions': 0, 'supervisor_pid': 123,
                'supervisor_process_start': '12345', 'daemon_epoch': 'b' * 64,
                'runtime_fingerprint': 'c' * 64, 'source_sha256': 'a' * 64,
                'slots': []}
        def release_idle_runtime(self, *, deadline_epoch):
            calls.append(deadline_epoch)
            raise AssertionError('empty slot snapshot must not trigger a stop request')

    service = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
                         backend=Backend())
    receipt = service.coordinate_demand_stop(trusted_operator_control(), timeout=.2)
    assert receipt['active_work_cancelled'] is True
    assert receipt['owned_resources_released'] is True
    assert calls == []


def test_cold_supervisor_does_not_deadlock_eligible_finite_automatic_work(tmp_path):
    starts = []

    class Backend:
        ready = False
        def central_status(self, *, deadline_epoch=None):
            if not self.ready:
                raise RuntimeError('central_supervisor_unavailable')
            return {'status': 'available', 'running': False, 'active_leases': 0,
                    'active_admissions': 0, 'supervisor_process_start': '12345',
                    'supervisor_pid': 123, 'daemon_epoch': 'b' * 64,
                    'runtime_fingerprint': 'c' * 64, 'slots': [], 'source_sha256': 'a' * 64}
        def open_sessions(self, *_args, **_kwargs):
            return {'status': 'available', 'session_ids': ['automatic-session']}
        def close_session(self, _session):
            return {'released': True}

    backend = Backend()
    def worker_factory(_service, _job):
        class Worker:
            def run(self, _request):
                return {'status': 'completed', 'answer': 'bounded preparation'}
        return Worker()

    service = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
        backend=backend, worker_factory=worker_factory, clock=lambda: 100.0)
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        policy = PowerPolicy(db, clock=service.clock)
        control = trusted_operator_control()
        policy.set_automatic(control, True)
        policy.set_focus_window(control, focus_id='finite', project='pc',
            starts_at=service.clock(), expires_at=service.clock() + 90)
    queued = service.enqueue_preparation('Review selected change', SCOPE, 'finite',
        'a' * 64, service.clock() + 90,
        expected_dependencies={'file:repo/src/change.py': 'b' * 64})
    assert queued['accepted'] is True

    def ensure(**kwargs):
        assert isinstance(kwargs.get('deadline_epoch'), float)
        starts.append(kwargs)
        backend.ready = True
        return {'status': 'ready'}

    service.demand_runtime_ready = ensure
    service.start()
    try:
        finished = wait(lambda: (value if (value := service.preparation_lookup(
            queued['job_id'], access_scope=SCOPE)).get('status') == 'completed' else None))
        assert finished['answer'] == 'bounded preparation'
        assert len(starts) == 1
    finally:
        service.shutdown()


def test_veto_committed_during_owner_admission_blocks_model_turn(tmp_path):
    calls = {'open': 0, 'turn': 0}

    class Backend:
        def __init__(self, service):
            self.service = service
        def open_sessions(self, *_args, **_kwargs):
            calls['open'] += 1
            with self.service._db() as db:
                db.execute('BEGIN IMMEDIATE')
                PowerPolicy(db, clock=self.service.clock).set_release(trusted_operator_control())
            return {'status': 'available', 'session_ids': ['session']}
        def close_session(self, _session):
            return {'released': True}

    def worker_factory(_service, _job):
        class Worker:
            def run(self, _request):
                calls['turn'] += 1
                return {'status': 'completed', 'answer': 'must not run'}
        return Worker()

    service = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
        worker_factory=worker_factory)
    service.backend = Backend(service)
    service.start()
    try:
        first = service.inquire('Do not infer after release', SCOPE, foreground_timeout=0)
        assert first['status'] in {'thinking', 'unavailable'}
        wait(lambda: calls['open'] == 1)
        time.sleep(.1)
        assert calls == {'open': 1, 'turn': 0}
    finally:
        service.shutdown()


@pytest.mark.parametrize('veto', ['quiet', 'release', 'expired'])
def test_ineligible_automatic_work_never_starts_cold_runtime(tmp_path, veto):
    now = [100.0]
    starts = []
    class Backend:
        def central_status(self, *, deadline_epoch=None):
            raise RuntimeError('central_supervisor_unavailable')
    def worker_factory(_service, _job):
        class Worker:
            def run(self, _request):
                return {'status': 'completed', 'answer': 'should not run'}
        return Worker()
    service = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
        backend=Backend(), worker_factory=worker_factory, clock=lambda: now[0])
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        policy = PowerPolicy(db, clock=service.clock)
        control = trusted_operator_control()
        policy.set_automatic(control, True)
        policy.set_focus_window(control, focus_id='finite', project='pc',
            starts_at=100, expires_at=190)
    queued = service.enqueue_preparation('Review selected change', SCOPE, 'finite',
        'a' * 64, 190, expected_dependencies={'file:repo/src/change.py': 'b' * 64})
    assert queued['accepted'] is True
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        policy = PowerPolicy(db, clock=service.clock)
        if veto == 'quiet':
            policy.set_quiet(trusted_operator_control(), expires_at=180)
        elif veto == 'release':
            policy.set_release(trusted_operator_control())
        else:
            now[0] = 191
    service.demand_runtime_ready = lambda **kwargs: starts.append(kwargs) or {'status': 'ready'}
    service.start()
    try:
        time.sleep(.2)
        assert starts == []
        assert service.preparation_lookup(queued['job_id'], access_scope=SCOPE)['status'] in {
            'pending', 'unavailable', 'failed'}
    finally:
        service.shutdown()


def test_stop_recovers_exact_stale_receipt_from_same_idle_model_epoch(tmp_path):
    receipt_calls = []
    epoch = {"daemon_epoch": "a" * 64, "supervisor_pid": 321,
        "supervisor_process_start": "supervisor-start", "runtime_fingerprint": "b" * 64}
    close_receipt = {"format": "PA1-OWNED-RESOURCE/1", "session_id": "stale-session",
        "daemon_epoch": epoch["daemon_epoch"], "slot_id": "slot-1", "owner_id": "owner-1",
        "host_lease_id": "owner-1", "residency_generation": "generation-1",
        "server_pid": 654, "server_process_start": "server-start-654",
        "gpu_uuids": ["GPU-fixture"], "capability": "d" * 64}
    stop_proof = _VerifiedPhysicalRelease(_PHYSICAL_RELEASE_SEAL, {
        "format": "PA1-PHYSICAL-RELEASE/1", "captured_at": time.time(),
        "status": "released_verified", "released_verified": True,
        "daemon_epoch": epoch["daemon_epoch"], "supervisor_pid": epoch["supervisor_pid"],
        "supervisor_process_start": epoch["supervisor_process_start"],
        "runtime_fingerprint": epoch["runtime_fingerprint"], "quiescent": True,
        "evicted": True, "stopped": True, "gpu_uuids": close_receipt["gpu_uuids"],
        "cleanup_receipts": [{"owner_id": close_receipt["owner_id"],
            "owned_pid": close_receipt["server_pid"],
            "server_process_start": close_receipt["server_process_start"],
            "generation": close_receipt["residency_generation"],
            "gpu_uuids": close_receipt["gpu_uuids"], "released": True,
            "process_released": True, "memory_released": True}]}, "fixture-host")

    class Backend:
        def central_status(self, *, deadline_epoch=None):
            return {"status": "available", "running": True, "active_leases": 0,
                "active_admissions": 0, "supervisor_process_start": epoch["supervisor_process_start"],
                "supervisor_pid": epoch["supervisor_pid"], "daemon_epoch": epoch["daemon_epoch"],
                "runtime_fingerprint": epoch["runtime_fingerprint"], "source_sha256": "c" * 64,
                "slots": [{"slot_id": close_receipt["slot_id"], "state": "idle", "leased": False,
                    "owner_id": close_receipt["owner_id"], "server_pid": close_receipt["server_pid"],
                    "gpu_uuids": close_receipt["gpu_uuids"]}]}
        def release_idle_runtime(self, *, deadline_epoch):
            receipt_calls.append(deadline_epoch)
            return stop_proof

    service = JobService(tmp_path / "jobs", packets=SQLitePacketStore(tmp_path / "packets"), backend=Backend())
    control = trusted_operator_control()
    with service._db() as db:
        policy = PowerPolicy(db, clock=service.clock)
        intent = policy.set_release(control, reason="test-stale-idle-owner")
        resources = ResourceController(db, power_policy=policy, clock=service.clock)
        resources.record_active_session("stale-session", epoch)
        resources.record_session("stale-session", close_receipt)
        db.execute("UPDATE pa1_owned_resource_sessions SET state='stale',release_request_id=? WHERE session_id=?",
            (intent.request_id, "stale-session"))
    with patch("project_control.assistance.resources._process_start_time",
               return_value=close_receipt["server_process_start"]):
        result = service.coordinate_demand_stop(control, timeout=.5)
    assert len(receipt_calls) == 1
    assert result["active_work_cancelled"] is True
    assert result["owned_resources_released"] is True, (result, service.last_error)
    with service._db() as db:
        row = db.execute("SELECT state,close_receipt FROM pa1_owned_resource_sessions WHERE session_id=?",
            ("stale-session",)).fetchone()
        assert row["state"] == "released_verified"
        assert row["close_receipt"] == json.dumps(close_receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_stale_stop_recovery_waits_for_zero_broker_slots(tmp_path):
    stop_calls = []
    epoch = {"daemon_epoch": "a" * 64, "supervisor_pid": 321,
        "supervisor_process_start": "supervisor-start", "runtime_fingerprint": "b" * 64}
    close_receipt = {"format": "PA1-OWNED-RESOURCE/1", "session_id": "stale-session",
        "daemon_epoch": epoch["daemon_epoch"], "slot_id": "slot-1", "owner_id": "owner-1",
        "host_lease_id": "owner-1", "residency_generation": "generation-1",
        "server_pid": 654, "server_process_start": "server-start-654",
        "gpu_uuids": ["GPU-fixture"], "capability": "d" * 64}

    class Backend:
        def central_status(self, *, deadline_epoch=None):
            return {"status": "available", "running": True, "active_leases": 0,
                "active_admissions": 0, "supervisor_process_start": epoch["supervisor_process_start"],
                "supervisor_pid": epoch["supervisor_pid"], "daemon_epoch": epoch["daemon_epoch"],
                "runtime_fingerprint": epoch["runtime_fingerprint"], "source_sha256": "c" * 64,
                "slots": [{"slot_id": close_receipt["slot_id"], "state": "idle", "leased": False,
                    "owner_id": close_receipt["owner_id"], "server_pid": close_receipt["server_pid"],
                    "gpu_uuids": close_receipt["gpu_uuids"]}]}
        def release_idle_runtime(self, *, deadline_epoch):
            stop_calls.append(deadline_epoch)
            raise AssertionError("must not stop while a broker execution slot remains")

    service = JobService(tmp_path / "jobs", packets=SQLitePacketStore(tmp_path / "packets"), backend=Backend())
    control = trusted_operator_control()
    with service._db() as db:
        policy = PowerPolicy(db, clock=service.clock)
        intent = policy.set_release(control, reason="test-stale-owner-with-active-slot")
        resources = ResourceController(db, power_policy=policy, clock=service.clock)
        resources.record_active_session("stale-session", epoch)
        resources.record_session("stale-session", close_receipt)
        db.execute("UPDATE pa1_owned_resource_sessions SET state='stale',release_request_id=? WHERE session_id=?",
            (intent.request_id, "stale-session"))
        db.execute("""INSERT INTO execution_slots(job,attempt,lease,owner_pid,owner_start,cleanup_failed)
            VALUES('unfinished-job',1,?,654,'slot-owner-start',0)""", (time.time() + 60,))
    with patch("project_control.assistance.resources._process_start_time",
               return_value=close_receipt["server_process_start"]):
        result = service.coordinate_demand_stop(control, timeout=.12)
    assert result["active_work_cancelled"] is False
    assert result["execution_slots"] == 1
    assert result["owned_resources_released"] is False
    assert stop_calls == []
    with service._db() as db:
        row = db.execute("SELECT state FROM pa1_owned_resource_sessions WHERE session_id=?",
            ("stale-session",)).fetchone()
        assert row["state"] == "stale"
        assert db.execute("SELECT count(*) FROM pa1_stale_resource_reconciliation_audit").fetchone()[0] == 0
