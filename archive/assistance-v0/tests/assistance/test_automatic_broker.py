"""CPU-only contracts for private automatic preparation admission and dispatch."""
import json
import threading

import pytest

from project_control.as1_jobs import JobService
from project_control.as1_contracts import DurableJob
from project_control.as1_packets import SQLitePacketStore
from project_control.assistance.frames import ChildFrameSpec, FrameStore, FrameError
from project_control.assistance.power import PowerPolicy, trusted_operator_control


SCOPE = {'principal': 'attention', 'profile': 'observer', 'project': 'repo'}


def make(tmp_path, **kwargs):
    return JobService(tmp_path/'jobs', packets=SQLitePacketStore(tmp_path/'packets'), **kwargs)


def configure_focus(service, *, enabled=True, quiet=False):
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        policy = PowerPolicy(db, clock=service.clock)
        control = trusted_operator_control()
        policy.set_automatic(control, enabled)
        if enabled:
            policy.set_focus_window(control, focus_id='focus-a', project='repo',
                                   starts_at=service.clock(), expires_at=service.clock()+100)
        if quiet:
            policy.set_quiet(control, expires_at=service.clock()+50, focus_id='focus-a')


def lease_automatic(service, job_id, attempt=1):
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()
        job = DurableJob.model_validate_json(row['record'])
        job.status, job.attempt = 'running', attempt
        db.execute('UPDATE jobs SET record=? WHERE id=?', (job.model_dump_json(), job_id))
        db.execute('UPDATE pa1_frames SET state="running" WHERE job_id=?', (job_id,))
        db.execute('INSERT INTO execution_slots(job,attempt,lease) VALUES(?,?,?)',
            (job_id, attempt, service.clock()+100))


def test_preparation_admission_is_opt_in_private_and_readable_by_exact_scope(tmp_path):
    service = make(tmp_path, clock=lambda: 100.0)
    request = dict(question='Prepare the changed module', access_scope=SCOPE,
        focus_id='focus-a', input_fingerprint='a'*64, window_deadline=150,
        expected_dependencies={'file:repo/src/module.py':'b'*64})
    denied = service.enqueue_preparation(**request)
    assert denied == {'accepted': False, 'job_id': None, 'status': 'deferred',
                      'reason': 'focus_window_unavailable'}

    configure_focus(service)
    queued = service.enqueue_preparation(**request)
    assert queued['accepted'] and queued['status'] == 'queued'
    assert 'poll' not in queued
    duplicate = service.enqueue_preparation(**request)
    assert duplicate == {**queued, 'status': 'existing'}

    with service._db() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM inquiry_index').fetchone()[0] == 0
        assert db.execute('SELECT inquiry FROM jobs').fetchone()[0] == 0
        assert service.lookup(queued['job_id'], access_scope=SCOPE)['status'] == 'not_found'
        assert service.log(access_scope=SCOPE) == []
        assert service.is_inquiry(queued['job_id']) is False
    assert service.preparation_lookup(queued['job_id'], access_scope={**SCOPE, 'project':'other'}) == {
        'status':'unavailable','reason':'preparation_unavailable'}
    assert service.preparation_lookup(queued['job_id'], access_scope=SCOPE)['status'] == 'pending'


def test_focus_quiet_release_expiry_and_scope_bound_each_enqueue(tmp_path):
    now = [100.0]
    service = make(tmp_path, clock=lambda: now[0])
    configure_focus(service, quiet=True)
    args = dict(question='Prepare', access_scope=SCOPE, focus_id='focus-a',
                input_fingerprint='a'*64, window_deadline=150)
    quiet = service.enqueue_preparation(**args)
    assert not quiet['accepted'] and quiet['reason'] == 'quiet_active'
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        PowerPolicy(db, clock=service.clock).resume(trusted_operator_control())
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        PowerPolicy(db, clock=service.clock).set_release(trusted_operator_control())
    released = service.enqueue_preparation(**args)
    assert not released['accepted'] and released['reason'] == 'inference_release_veto'
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        PowerPolicy(db, clock=service.clock).resume(trusted_operator_control())
    widened = service.enqueue_preparation(**{**args, 'access_scope':{**SCOPE,'project':'other'}})
    assert not widened['accepted'] and widened['reason'] == 'focus_window_unavailable'
    now[0] = 151
    expired = service.enqueue_preparation(**args)
    assert not expired['accepted'] and expired['reason'] == 'focus_window_expired'


def test_changed_input_cancels_old_private_root_and_dispatch_limits_auto_to_one_slot(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class Live:
        def is_alive(self):
            return True

    def worker_factory(_service, _job):
        class Worker:
            def run(self, request):
                entered.set()
                release.wait(1)
                return {'status':'completed','answer':'prepared','findings':[],
                        'unresolved_questions':[]}
        return Worker()

    service = make(tmp_path, clock=lambda:100.0, worker_factory=worker_factory)
    configure_focus(service)
    first = service.enqueue_preparation('Prepare A', SCOPE, 'focus-a', 'a'*64, 150)
    second = service.enqueue_preparation('Prepare B', SCOPE, 'focus-a', 'c'*64, 150)
    assert first['accepted'] and second['accepted']
    assert service.preparation_lookup(first['job_id'], access_scope=SCOPE)['status'] == 'unavailable'
    service._thread = Live()
    auto_one = service.claim()
    assert auto_one is not None and auto_one.job_id == second['job_id']
    assert service.claim() is None  # A second automatic root cannot occupy the other slot.
    demand = service.submit(question='foreground question', access_scope=SCOPE)
    assert demand['accepted']
    foreground = service.claim()
    assert foreground is not None and foreground.job_id == demand['job_id']
    # Both logical slots now belong to work; the automatic slot is never hidden
    # or reclassified as public demand.
    with service._db() as db:
        rows = db.execute('SELECT job FROM execution_slots ORDER BY job').fetchall()
        assert {row['job'] for row in rows} == {auto_one.job_id, foreground.job_id}
        assert db.execute('SELECT count(*) FROM pa1_automatic_work').fetchone()[0] == 2
    release.set()


def test_automatic_root_and_child_keep_origin_and_private_visibility(tmp_path):
    service = make(tmp_path, clock=lambda:100.0)
    configure_focus(service)
    admitted = service.enqueue_preparation('Prepare', SCOPE, 'focus-a', 'd'*64, 150,
        expected_dependencies={'file:repo/src/module.py':'e'*64})
    lease_automatic(service, admitted['job_id'])
    with service._db() as db:
        from project_control.assistance.frames import FrameStore
        frames = FrameStore(db)
        origin = PowerPolicy(db, clock=service.clock).root_demand_origin(admitted['job_id'])
        assert origin.kind == 'automatic'
        assert frames.is_private(admitted['job_id'])
        assert db.execute('SELECT count(*) FROM pa1_private_ids WHERE job_id=?',
                          (admitted['job_id'],)).fetchone()[0] == 1
    assert service.preparation_read_scope(admitted['job_id'], access_scope=SCOPE, expected_attempt=1) == {
        'version': 1, 'files': [{'repository':'repo','path':'src/module.py','sha256':'e'*64}]}
    assert service.preparation_read_scope('unknown-job', access_scope=SCOPE, expected_attempt=1) is None


def test_automatic_scope_is_sealed_from_dependencies_and_fails_closed(tmp_path):
    service = make(tmp_path, clock=lambda:100.0)
    configure_focus(service)
    malformed = service.enqueue_preparation('Prepare', SCOPE, 'focus-a', 'a'*64, 150,
        expected_dependencies={'file:repo/../secrets.txt':'b'*64})
    assert malformed == {'accepted':False, 'job_id':None, 'status':'deferred',
        'reason':'invalid_dependencies'}

    admitted = service.enqueue_preparation('Prepare', SCOPE, 'focus-a', 'c'*64, 150,
        expected_dependencies={'file:repo/src/selected.py':'d'*64})
    lease_automatic(service, admitted['job_id'])
    seal = {'version':1, 'files':[{'repository':'repo','path':'src/selected.py','sha256':'d'*64}]}
    assert service.preparation_read_scope(admitted['job_id'], access_scope=SCOPE, expected_attempt=1) == seal
    with pytest.raises(FrameError):
        service.preparation_read_scope(admitted['job_id'], access_scope={**SCOPE,'project':'other'}, expected_attempt=1)
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        PowerPolicy(db, clock=service.clock).set_quiet(trusted_operator_control(),
            expires_at=140, focus_id='focus-a')
    with pytest.raises(FrameError):
        service.preparation_read_scope(admitted['job_id'], access_scope=SCOPE, expected_attempt=1)
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        PowerPolicy(db, clock=service.clock).resume(trusted_operator_control())
        active = DurableJob.model_validate_json(db.execute(
            'SELECT record FROM jobs WHERE id=?', (admitted['job_id'],)).fetchone()['record'])
        active.attempt = 2
        db.execute('UPDATE jobs SET record=? WHERE id=?',
            (active.model_dump_json(), admitted['job_id']))
        db.execute('UPDATE execution_slots SET attempt=2 WHERE job=?', (admitted['job_id'],))
    # A stale reader cannot borrow the new retry's otherwise-valid lease.
    with pytest.raises(FrameError):
        service.preparation_read_scope(admitted['job_id'], access_scope=SCOPE, expected_attempt=1)
    assert service.preparation_read_scope(admitted['job_id'], access_scope=SCOPE, expected_attempt=2) == seal
    class Live:
        def is_alive(self):
            return True
    service._thread = Live()
    public = service.submit(question='ordinary demand', access_scope=SCOPE)
    assert public['accepted']
    assert service.preparation_read_scope(public['job_id'], access_scope=SCOPE, expected_attempt=1) is None
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE pa1_frames SET read_scope_seal=NULL WHERE job_id=?', (admitted['job_id'],))
    with pytest.raises(FrameError):
        service.preparation_read_scope(admitted['job_id'], access_scope=SCOPE, expected_attempt=1)


def test_automatic_descendant_shares_one_auto_slot_and_demand_uses_second(tmp_path):
    class Live:
        def is_alive(self):
            return True

    service = make(tmp_path, clock=lambda:100.0)
    configure_focus(service)
    service._thread = Live()
    root = service.enqueue_preparation('Prepare', SCOPE, 'focus-a', 'e'*64, 150,
        expected_dependencies={'file:repo/src/selected.py':'f'*64})
    child_id = 'job_private_child'
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        frame_store = FrameStore(db)
        root_job = DurableJob.model_validate_json(db.execute(
            'SELECT record FROM jobs WHERE id=?', (root['job_id'],)).fetchone()['record'])
        root_job.status, root_job.attempt = 'running', 1
        db.execute('UPDATE jobs SET record=? WHERE id=?', (root_job.model_dump_json(), root['job_id']))
        db.execute('UPDATE pa1_frames SET state="running" WHERE job_id=?', (root['job_id'],))
        db.execute('INSERT INTO execution_slots(job,attempt,lease) VALUES(?,?,?)',
            (root['job_id'], 1, 200))
        frame_store.register_wait(root['job_id'], 1, [ChildFrameSpec(
            child_id, SCOPE, 150, question='Inspect one source', turn_reservation=1)], now=100)
        child = DurableJob(job_id=child_id, mode='investigate', question='Inspect one source',
            status='queued', attempt=0, created_at=root_job.created_at, findings=[],
            hints=[], evidence_packets=[], unresolved_questions=[], project='repo', scope=SCOPE,
            deadline_epoch=150)
        db.execute('INSERT INTO jobs(id,scope,request_id,request_hash,record,updated,inquiry) VALUES(?,?,?,?,?,?,0)',
            (child_id, json.dumps(SCOPE, sort_keys=True, separators=(',',':')), None,
             'private-child', child.model_dump_json(), 100))
        db.execute('DELETE FROM execution_slots WHERE job=?', (root['job_id'],))
        assert frame_store.mark_parent_released(root['job_id'], 1, now=100)
        origin = PowerPolicy(db, clock=service.clock).root_demand_origin(root['job_id'])
        assert origin.kind == 'automatic'
        assert frame_store.is_private(child_id)

    expected_seal = {'version':1, 'files':[{'repository':'repo','path':'src/selected.py','sha256':'f'*64}]}
    claimed_child = service.claim()
    assert claimed_child is not None and claimed_child.job_id == child_id
    assert service.preparation_read_scope(child_id, access_scope=SCOPE, expected_attempt=1) == expected_seal
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE pa1_frames SET generation=2 WHERE job_id=?', (root['job_id'],))
    with pytest.raises(FrameError):
        service.preparation_read_scope(child_id, access_scope=SCOPE, expected_attempt=1)
    public = service.submit(question='foreground', access_scope=SCOPE)
    assert public['accepted']
    assert service.preparation_read_scope(public['job_id'], access_scope=SCOPE, expected_attempt=1) is None
    claimed_public = service.claim()
    assert claimed_public is not None and claimed_public.job_id == public['job_id']


def test_protocol_feedback_is_scoped_operational_state_not_public_evidence(tmp_path):
    service = make(tmp_path, clock=lambda:100.0)
    class Live:
        def is_alive(self):
            return True
    service._thread = Live()
    job = service.submit(question='foreground', access_scope=SCOPE)
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO execution_slots(job,attempt,lease) VALUES(?,?,?)',
            (job['job_id'], 1, 200))
        feedback = [{'role':'user','content':json.dumps({
            'protocol_error':'invalid_json_object','detail':'Extra data at line 1',
            'instruction':'Return one JSON object.'})}]
        frames = FrameStore(db)
        frames.save_protocol_feedback(job['job_id'], SCOPE, 0, 1, feedback, now=100)
        loaded = frames.load_protocol_feedback(job['job_id'], SCOPE, 0)
        assert json.loads(loaded[0]['content']) == json.loads(feedback[0]['content'])
        with pytest.raises(FrameError):
            frames.load_protocol_feedback(job['job_id'], {**SCOPE,'project':'other'}, 0)
        with pytest.raises(FrameError):
            frames.save_protocol_feedback(job['job_id'], SCOPE, 0, 2, feedback, now=100)
        with pytest.raises(FrameError):
            frames.save_protocol_feedback(job['job_id'], SCOPE, 0, 1, [
                {'role':'user','content':json.dumps({'protocol_error':'invalid_json_object',
                 'instruction':'x','raw_completion':'must not persist'})}], now=100)
        # Only operational feedback is stored: no observation, outbox packet,
        # inquiry identity, or public log entry is created by the checkpoint.
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM inquiry_index').fetchone()[0] == 0
        assert service.log(access_scope=SCOPE) == []
