"""Real SQLite/Bubblewrap restart replay over the qualified native observer port."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pytest

from project_control.as1_jobs import JobService, TrustedObserverFactory
from project_control.as1_packets import SQLitePacketStore

RUNTIME_ROOT = Path(__file__).resolve().parents[2]/'src/project_control/local_runtime'
SCOPE = {'principal': 'checkpoint-user', 'profile': 'observer', 'project': 'pc'}
CALL = {'tool': 'command', 'arguments': {'argv': ['pwd'], 'cwd': '/fixture'}}


def wait(predicate):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError('bounded wait expired')


def make(root, now, **kwargs):
    return JobService(root/'jobs', packets=SQLitePacketStore(root/'packets', clock=lambda: now[0]),
                      clock=lambda: now[0], **kwargs)


def unit_runtime_digest():
    """Pin this unit fixture to the currently bound receiver source.

    The checkpoint replay uses an in-process fake backend; it does not consume
    a live model qualification receipt. The receiver manifest remains the
    authority for the source digest and the runtime binder verifies its files.
    """
    manifest = json.loads((RUNTIME_ROOT/'receiver-manifest.json').read_text())
    return manifest['files']['local_worker/observer_runtime.py']


def queued_factory(service, job):
    class Worker:
        def run(self, request):
            return {'status': 'queued_after_eviction', 'reason': 'session_unavailable'}
    return Worker()


@pytest.fixture
def running(tmp_path):
    now = [1000.]
    service = make(tmp_path, now, worker_factory=queued_factory, lease_seconds=10).start()
    admitted = service.submit(question='read fixture', access_scope=SCOPE)
    wait(lambda: service.lookup(admitted['job_id'], access_scope=SCOPE)['job']['status'] == 'queued_after_eviction')
    assert service.shutdown()
    # The first recoverable attempt uses the required five-second backoff.
    now[0] += 5
    job = service.claim()
    assert job
    return service, job, now


@pytest.mark.as1_case('JOB-01', 'JOB-04', 'JOB-05', 'JOB-06')
def test_native_command_checkpoint_reopen_replays_call_packet_without_reread(tmp_path):
    # The native producer interprets deadline_epoch as UNIX wall-clock seconds.
    now = [time.time()]
    source = tmp_path/'module.py'
    content = 'def calculate_total(values):\n    return sum(values)\n'
    source.write_text(content)
    actual_call = {'tool': 'command', 'arguments': {'argv': ['cat', str(source)], 'cwd': str(tmp_path)}}
    commands = []
    class Backend:
        def __init__(self, resume=False):
            self.requests = []; self.closed = []; self.resume = resume
        def open_sessions(self, count, **policy):
            return {'status': 'available', 'session_ids': ['resumed' if self.resume else 'initial']}
        def close_session(self, session):
            self.closed.append(session)
        def preemption_status(self, session):
            return {'preempt_requested': bool(self.requests) and not self.resume}
        def run_observer_turn(self, request):
            self.requests.append(request)
            if not self.resume:
                value = actual_call
            else:
                messages = request['messages']
                assert json.loads(messages[1]['content'])['question'] == 'Explain calculate_total'
                assert messages[2]['role'] == 'assistant'
                assert json.loads(messages[2]['content']) == actual_call
                assert messages[3]['role'] == 'user'
                packet = json.loads(messages[3]['content'])
                assert packet['packet_id'] == ref
                assert packet['stdout'] == content
                assert packet['source_reads'][0]['content_sha256'] == hashlib.sha256(content.encode()).hexdigest()
                assert 'public_tool_call' not in packet
                resumed = [json.loads(message['content']) for message in messages
                           if message['role'] == 'user' and 'progress' in json.loads(message['content'])]
                assert any(item['progress']['stage'] in {'resumed', 'continuation'}
                           and item['progress']['observation_count'] > 0 for item in resumed)
                value = {'answer': 'calculate_total sums values.', 'findings': [
                    {'text': 'The function returns sum(values).', 'evidence_packets': [ref]}],
                    'unresolved_questions': []}
            return {'status': 'available', 'text': json.dumps(value)}
    def factory(backend):
        from project_control.runtime_binding import local_runtime_identity
        identity = local_runtime_identity(root=RUNTIME_ROOT)
        trusted = TrustedObserverFactory(identity.root, unit_runtime_digest(),
            backend=backend, roots=[tmp_path], tools=lambda *args: {})
        def bind(service, job):
            worker = trusted(service, job)
            command = worker.command.run
            def counted(*args, **kwargs):
                commands.append(args)
                return command(*args, **kwargs)
            worker.command.run = counted
            return worker
        return bind
    first_backend = Backend()
    first = make(tmp_path, now, worker_factory=factory(first_backend), backend=first_backend).start()
    admitted = first.submit(question='Explain calculate_total', access_scope=SCOPE, request_id='restart')
    initial = wait(lambda: (v if (v := first.poll(admitted['job_id'], access_scope=SCOPE))['job']['status'] == 'yielding' else None))
    assert first.shutdown()
    ref = initial['observations'][0]['packet_id']
    assert initial['observations'][0]['public_tool_call'] == actual_call
    original_packet = first.packets.lookup(ref, access_scope=SCOPE).packet.model_dump_json()
    with sqlite3.connect(first.path) as db:
        raw = json.loads(db.execute('SELECT observations FROM jobs WHERE id=?', (admitted['job_id'],)).fetchone()[0])
    assert 'public_tool_call' not in raw[0]
    assert first_backend.closed == ['initial']
    source.unlink()  # A reread could no longer produce this retained source proof.
    # The first recoverable attempt uses the required five-second backoff.
    now[0] += 5
    second_backend = Backend(resume=True)
    restored = make(tmp_path, now, worker_factory=factory(second_backend), backend=second_backend)
    assert restored.poll(admitted['job_id'], access_scope=SCOPE)['observations'] == initial['observations']
    restored.start()
    try:
        final = wait(lambda: (v if (v := restored.poll(admitted['job_id'], access_scope=SCOPE))['job']['status'] == 'completed' else None))
        assert final['job']['attempt'] == 2
        assert final['job']['findings'][0]['evidence_packets'] == [ref]
        assert len(commands) == 1 and len(second_backend.requests) == 1
        assert restored.packets.lookup(ref, access_scope=SCOPE).packet.model_dump_json() == original_packet
        assert restored.poll(admitted['job_id'], access_scope={**SCOPE, 'principal': 'other'})['status'] == 'ok'
        assert restored.poll(admitted['job_id'], access_scope={**SCOPE, 'project': 'other'})['status'] == 'forbidden'
        assert restored.submit(question='Explain calculate_total', access_scope=SCOPE, request_id='restart')['job_id'] == admitted['job_id']
    finally:
        assert restored.shutdown()
    assert second_backend.closed == ['resumed']


@pytest.mark.as1_case('JOB-04', 'JOB-05')
def test_checkpoint_payload_scope_generation_and_expired_lease_fences(running):
    service, job, now = running
    ref = service.observe(job.job_id, job.attempt, {'stdout': 'exact source', 'source_reads': [{'content_sha256': 'exact'}]})
    raw = service.lookup(job.job_id, access_scope=SCOPE)['observations'][0]
    frame = {**raw, 'public_tool_call': CALL}
    service.checkpoint(job.job_id, job.attempt, [frame], access_scope=SCOPE)
    before = service.lookup(job.job_id, access_scope=SCOPE)['observations']
    with sqlite3.connect(service.path) as db:
        original_lease = db.execute('SELECT lease FROM jobs WHERE id=?', (job.job_id,)).fetchone()[0]
    now[0] += 1
    with pytest.raises(PermissionError, match='scope'):
        service.checkpoint(job.job_id, job.attempt, [frame], access_scope={**SCOPE, 'principal': 'other'})
    assert not service.fence(job.job_id, job.attempt, access_scope={**SCOPE, 'principal': 'other'})
    with pytest.raises(ValueError, match='differs'):
        service.checkpoint(job.job_id, job.attempt, [{**frame, 'stdout': 'forged source'}], access_scope=SCOPE)
    with pytest.raises(RuntimeError, match='stale_attempt'):
        service.checkpoint(job.job_id, job.attempt - 1, [frame], access_scope=SCOPE)
    now[0] += 11
    with pytest.raises(RuntimeError, match='stale_attempt'):
        service.checkpoint(job.job_id, job.attempt, [frame], access_scope=SCOPE)
    assert service.lookup(job.job_id, access_scope=SCOPE)['observations'] == before
    with sqlite3.connect(service.path) as db:
        assert db.execute('SELECT lease FROM jobs WHERE id=?', (job.job_id,)).fetchone()[0] == original_lease
    assert service.packets.lookup(ref, access_scope=SCOPE).packet.payload['stdout'] == 'exact source'


@pytest.mark.as1_case('JOB-04', 'JOB-05')
def test_checkpoint_cross_job_copy_legacy_and_crash_gap(running):
    service, job, now = running
    first = service.observe(job.job_id, job.attempt, {'stdout': 'first'})
    raw = service.lookup(job.job_id, access_scope=SCOPE)['observations']
    assert 'public_tool_call' not in raw[0]  # Legacy rows need no fabricated call.
    service.checkpoint(job.job_id, job.attempt, [{**raw[0], 'public_tool_call': CALL}], access_scope=SCOPE)
    second = service.observe(job.job_id, job.attempt, {'stdout': 'crash before checkpoint'})
    restored = make(service.directory.parent, now)
    frames = restored.lookup(job.job_id, access_scope=SCOPE)['observations']
    assert [frame['packet_id'] for frame in frames] == [first, second]
    assert frames[0]['public_tool_call'] == CALL and 'public_tool_call' not in frames[1]
    other_root = service.directory.parent/'other-admission'
    other = make(other_root, now, worker_factory=queued_factory).start()
    admitted = other.submit(question='other', access_scope=SCOPE)
    wait(lambda: other.lookup(admitted['job_id'], access_scope=SCOPE)['job']['status'] == 'queued_after_eviction')
    assert other.shutdown()
    # The first recoverable attempt uses the required five-second backoff.
    now[0] += 5
    other_job = other.claim()
    foreign = other.observe(other_job.job_id, other_job.attempt, {'stdout': 'other caller/job'})
    foreign_frame = other.lookup(other_job.job_id, access_scope=SCOPE)['observations'][0]
    with pytest.raises(ValueError, match='same-job'):
        restored.checkpoint(job.job_id, job.attempt, [{**foreign_frame, 'public_tool_call': CALL}], access_scope=SCOPE)
    assert foreign not in restored.lookup(job.job_id, access_scope=SCOPE)['job']['evidence_packets']


@pytest.mark.as1_case('JOB-04', 'JOB-05')
def test_checkpoint_bounds_exact_call_privacy_and_oversized_payload_omission(running):
    service, job, _ = running
    ref = service.observe(job.job_id, job.attempt, {'hidden_reasoning': 'never persist', 'stdout': 'visible'})
    raw = service.lookup(job.job_id, access_scope=SCOPE)['observations'][0]
    call = {'tool': 'command', 'arguments': {'argv': ['printf', 'Bearer publicCallArgument'], 'cwd': '/fixture'}}
    frame = {**raw, 'public_tool_call': call}
    service.checkpoint(job.job_id, job.attempt, [frame], access_scope=SCOPE)
    saved = service.lookup(job.job_id, access_scope=SCOPE)['observations'][0]
    assert saved['public_tool_call'] == call
    assert saved['hidden_reasoning'] == '[masked]'
    with pytest.raises(ValueError, match='count cap'):
        service.checkpoint(job.job_id, job.attempt, [frame]*25, access_scope=SCOPE)
    with pytest.raises(ValueError, match='unique'):
        service.checkpoint(job.job_id, job.attempt, [frame]*2, access_scope=SCOPE)
    with pytest.raises(ValueError, match='public tool call'):
        service.checkpoint(job.job_id, job.attempt, [{**raw, 'public_tool_call': {'tool': 'investigate', 'arguments': {}}}], access_scope=SCOPE)
    large = service.observe(job.job_id, job.attempt, {'stdout': 'x'*33000})
    large_raw = service.lookup(job.job_id, access_scope=SCOPE)['observations'][-1]
    with pytest.raises(ValueError, match='frame byte cap'):
        service.checkpoint(job.job_id, job.attempt, [{**large_raw, 'public_tool_call': CALL}], access_scope=SCOPE)
    omitted = {'packet_id': large, 'omissions': ['tool payload exceeded worker context budget'], 'public_tool_call': CALL}
    service.checkpoint(job.job_id, job.attempt, [omitted], access_scope=SCOPE)
    assert service.lookup(job.job_id, access_scope=SCOPE)['observations'][-1] == omitted
    assert service.packets.lookup(large, access_scope=SCOPE).packet.payload['stdout'] == 'x'*33000
    small_refs = [service.observe(job.job_id, job.attempt, {'stdout': 'x'*21000}) for _ in range(3)]
    frames = [o for o in service.lookup(job.job_id, access_scope=SCOPE)['observations'] if o['packet_id'] in small_refs]
    encoded = json.dumps(frames, ensure_ascii=False, allow_nan=False).encode()
    assert 60000 < len(encoded) <= 96 * 1024
    service.checkpoint(job.job_id, job.attempt, frames, access_scope=SCOPE)
    saved_checkpoint = [o for o in service.lookup(job.job_id, access_scope=SCOPE)['observations']
                       if o['packet_id'] in small_refs]
    over_refs = [service.observe(job.job_id, job.attempt, {'stdout': 'y'*25000}) for _ in range(4)]
    over_frames = [o for o in service.lookup(job.job_id, access_scope=SCOPE)['observations']
                   if o['packet_id'] in over_refs]
    assert len(json.dumps(over_frames, ensure_ascii=False, allow_nan=False).encode()) > 96 * 1024
    with pytest.raises(ValueError, match='checkpoint byte cap'):
        service.checkpoint(job.job_id, job.attempt, over_frames, access_scope=SCOPE)
    service.max_storage_bytes = 1
    with pytest.raises(ValueError, match='storage cap'):
        service.checkpoint(job.job_id, job.attempt, [frame], access_scope=SCOPE)
    current = [o for o in service.lookup(job.job_id, access_scope=SCOPE)['observations']
               if o['packet_id'] in small_refs]
    assert current == saved_checkpoint


@pytest.mark.as1_case('JOB-04', 'JOB-05')
def test_same_database_cross_caller_and_bound_native_identity(running):
    service, alice, now = running
    alice_ref = service.observe(alice.job_id, alice.attempt, {'stdout': 'Alice source'})
    bob_scope = {**SCOPE, 'principal': 'Bob'}
    service.start()
    admitted = service.submit(question='Bob source', access_scope=bob_scope)
    wait(lambda: service.lookup(admitted['job_id'], access_scope=bob_scope)['job']['status'] == 'queued_after_eviction')
    assert service.shutdown()
    # The first recoverable attempt uses the required five-second backoff.
    now[0] += 5
    bob = service.claim()
    assert bob.job_id == admitted['job_id']
    bob_ref = service.observe(bob.job_id, bob.attempt, {'stdout': 'Bob source'})
    alice_frame = service.lookup(alice.job_id, access_scope=SCOPE)['observations'][0]
    bob_frame = service.lookup(bob.job_id, access_scope=bob_scope)['observations'][0]
    with pytest.raises(ValueError, match='same-job'):
        service.checkpoint(alice.job_id, alice.attempt, [{**bob_frame, 'public_tool_call': CALL}], access_scope=SCOPE)
    with pytest.raises(ValueError, match='same-job'):
        service.checkpoint(bob.job_id, bob.attempt, [{**alice_frame, 'public_tool_call': CALL}], access_scope=bob_scope)
    # Principal/profile are provenance in this shared oracle. Project remains
    # an authority boundary, so the same job is visible within its project but
    # stays unavailable to a different project.
    assert service.lookup(bob.job_id, access_scope=SCOPE)['status'] == 'ok'
    assert service.lookup(bob.job_id, access_scope={**SCOPE, 'project': 'other'})['status'] == 'forbidden'
    class Backend:
        def run_observer_turn(self, request):
            raise AssertionError('foreign job reached model')
    from project_control.runtime_binding import local_runtime_identity
    identity = local_runtime_identity(root=RUNTIME_ROOT)
    trusted = TrustedObserverFactory(identity.root, unit_runtime_digest(),
        backend=Backend(), roots=[service.directory.parent], tools=lambda *args: {})
    worker = trusted(service, alice)
    result = worker.run({'job_id': bob.job_id, 'attempt': bob.attempt, 'mode': 'investigate',
                         'question': bob.question, 'scope': bob_scope, 'hints': {}, 'observations': []})
    assert result['status'] == 'stale_attempt'
    assert alice_ref != bob_ref
    assert 'public_tool_call' not in service.lookup(alice.job_id, access_scope=SCOPE)['observations'][0]


@pytest.mark.as1_case('JOB-04', 'JOB-05')
def test_invalid_checkpoint_atomicity_preserves_prior_frames_and_lease(running):
    service, job, now = running
    service.observe(job.job_id, job.attempt, {'stdout': 'baseline'})
    raw = service.lookup(job.job_id, access_scope=SCOPE)['observations'][0]
    service.checkpoint(job.job_id, job.attempt, [{**raw, 'public_tool_call': CALL}], access_scope=SCOPE)
    def state():
        with sqlite3.connect(service.path) as db:
            frame_bytes = db.execute('SELECT frames FROM job_checkpoints WHERE job=?', (job.job_id,)).fetchone()[0]
            lease = db.execute('SELECT lease FROM jobs WHERE id=?', (job.job_id,)).fetchone()[0]
        return frame_bytes, lease
    before = state()
    now[0] += 1
    with pytest.raises(ValueError, match='differs'):
        service.checkpoint(job.job_id, job.attempt, [{**raw, 'source_reads': [{'content_sha256': 'invented'}]}], access_scope=SCOPE)
    assert state() == before
    service.max_storage_bytes = 1
    with pytest.raises(ValueError, match='storage cap'):
        service.checkpoint(job.job_id, job.attempt, [raw], access_scope=SCOPE)
    assert state() == before
