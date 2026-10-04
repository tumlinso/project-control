"""CPU fixtures exercise real SQLite and the actual installed observer port."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from project_control.as1_jobs import JobService, TrustedObserverFactory, BUSY, SHARED_TOOLS
from project_control.as1_packets import SQLitePacketStore

SCOPE = {'principal': 'alice', 'profile': 'observer', 'project': 'pc'}
SKILLS = Path('/home/tumlinson/.agents/skills')


def wait(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError('bounded wait expired')


def complete_factory(service, job):
    class Worker:
        def run(self, request):
            ref = service.observe(job.job_id, job.attempt, {'path': 'src/module.py', 'entity': 'Thing', 'text': 'observed'})
            return {'status': 'completed', 'answer': 'observed', 'findings': [
                {'text': 'observed', 'evidence_packets': [ref]}]}
    return Worker()


def make(tmp_path, **kwargs):
    return JobService(tmp_path/'jobs', packets=SQLitePacketStore(tmp_path/'packets'), **kwargs)


@pytest.mark.as1_case('JOB-01')
def test_admission_and_process_lived_dispatch_after_disconnect(tmp_path):
    # An independent child process owns dispatch; parent admission ends before processing.
    admitted = tmp_path/'admitted.json'
    release = tmp_path/'release'
    script = r'''
import json, pathlib, sys, time
from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore
root=pathlib.Path(sys.argv[1]); scope=json.loads(sys.argv[2])
def factory(service,job):
 class Worker:
  def run(self,request):
   while not (root/'release').exists(): time.sleep(.01)
   ref=service.observe(job.job_id,job.attempt,{'text':'independent process'})
   return {'status':'completed','answer':'done','findings':[{'text':'independent process','evidence_packets':[ref]}]}
 return Worker()
s=JobService(root/'jobs',packets=SQLitePacketStore(root/'packets'),worker_factory=factory).start()
a=s.submit(question='read',access_scope=scope,request_id='caller'); (root/'admitted.json').write_text(json.dumps(a))
end=time.monotonic()+8
while time.monotonic()<end:
 if s.lookup(a['job_id'],access_scope=scope)['job']['status']=='completed': break
 time.sleep(.01)
else: raise RuntimeError('not drained')
s.shutdown()
'''
    child = subprocess.Popen([sys.executable, '-c', script, str(tmp_path), json.dumps(SCOPE)])
    try:
        wait(admitted.exists)
        accepted = json.loads(admitted.read_text())
        with sqlite3.connect(tmp_path/'jobs/jobs.sqlite3') as db:
            assert db.execute('SELECT count(*) FROM jobs WHERE id=?', (accepted['job_id'],)).fetchone()[0] == 1
        release.touch()  # No poll executes or triggers the child.
        assert child.wait(timeout=10) == 0
        restored = make(tmp_path)
        assert restored.lookup(accepted['job_id'], access_scope=SCOPE)['job']['status'] == 'completed'
    finally:
        if child.poll() is None:
            child.kill(); child.wait()


@pytest.mark.as1_case('JOB-02')
def test_soft_threshold_hard_limit_storage_failure_and_unavailable_dispatch(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def factory(service, job):
        class Worker:
            def run(self, request):
                entered.set(); release.wait(5)
                return {'status': 'completed', 'answer': 'done'}
        return Worker()
    s = make(tmp_path, worker_factory=factory, hard_limit=4).start()
    try:
        first = s.submit(question='one', access_scope=SCOPE); assert first['accepted']; assert entered.wait(2)
        answers = [s.submit(question=str(i), access_scope=SCOPE) for i in range(3)]
        assert all(x['accepted'] for x in answers)
        assert answers[-1]['message'] == BUSY and answers[-1]['poll']['job_id']
        assert s.submit(question='overflow', access_scope=SCOPE) == {'accepted': False, 'reason': 'admission_limit'}
        s.max_storage_bytes = 1
        assert s.submit(question='full', access_scope=SCOPE)['reason'] == 'admission_limit'
        def broken(): raise sqlite3.OperationalError('disk error')
        monkeypatch.setattr(s, '_db', broken)
        assert s.submit(question='broken', access_scope=SCOPE) == {'accepted': False, 'reason': 'storage_unavailable'}
        monkeypatch.undo()
    finally:
        release.set(); s.shutdown()
    assert s.submit(question='offline', access_scope=SCOPE)['reason'] == 'durable_processing_unavailable'


@pytest.mark.as1_case('JOB-03')
def test_scoped_idempotency_exact_lookup_related_questions_and_no_gpu_poll(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def factory(service, job):
        calls.append(job.job_id)
        class Worker:
            def run(self, request):
                entered.set(); release.wait(5)
                return {'status': 'completed', 'answer': 'done'}
        return Worker()
    s = make(tmp_path, worker_factory=factory).start()
    try:
        a = s.submit(question='  inspect  Thing ', access_scope=SCOPE, request_id='r')
        assert entered.wait(2)
        for _ in range(5):
            assert s.submit(question='inspect Thing', access_scope=SCOPE, request_id='r')['job_id'] == a['job_id']
            assert s.poll(a['job_id'], access_scope=SCOPE)['status'] == 'ok'
        assert calls == [a['job_id']]
        assert s.submit(question='changed', access_scope=SCOPE, request_id='r')['reason'] == 'request_id_mismatch'
        other = s.submit(question='inspect Thing', access_scope={**SCOPE, 'principal': 'bob'}, request_id='r')
        assert other['job_id'] != a['job_id']
        assert s.lookup(a['job_id'], access_scope={**SCOPE, 'principal': 'bob'})['status'] == 'forbidden'
        related = s.submit(question='inspect Thing callers', access_scope=SCOPE)
        assert related['job_id'] != a['job_id']
    finally:
        release.set(); s.shutdown()


@pytest.mark.as1_case('JOB-04')
def test_real_sqlite_restart_eviction_cancellation_and_fenced_late_writes(tmp_path):
    now = [1000.0]
    entered, release = threading.Event(), threading.Event()
    def factory(service, job):
        class Worker:
            def run(self, request):
                entered.set(); release.wait(5)
                return {'status': 'queued_after_eviction', 'reason': 'evicted'}
        return Worker()
    s = make(tmp_path, worker_factory=factory, clock=lambda: now[0], lease_seconds=2).start()
    a = s.submit(question='resume', access_scope=SCOPE)
    assert entered.wait(2)
    old = s.lookup(a['job_id'], access_scope=SCOPE)['job']['attempt']
    p = s.observe(a['job_id'], old, {'text': 'checkpoint'})
    now[0] += 3
    second = make(tmp_path, clock=lambda: now[0], lease_seconds=2)
    resumed = second.claim()
    assert resumed.attempt == old + 1
    assert second.lookup(a['job_id'], access_scope=SCOPE)['observations'][0]['packet_id'] == p
    assert not s.finish(a['job_id'], old, {'status': 'completed', 'answer': 'stale'})
    with pytest.raises(RuntimeError, match='stale_attempt'):
        s.observe(a['job_id'], old, {'text': 'late'})
    assert second.finish(resumed.job_id, resumed.attempt, {'status': 'queued_after_eviction'})
    assert second.lookup(resumed.job_id, access_scope=SCOPE)['job']['status'] == 'queued_after_eviction'
    now[0] += 2
    next_attempt = second.claim()
    assert second.cancel(next_attempt.job_id, access_scope=SCOPE)
    assert not second.finish(next_attempt.job_id, next_attempt.attempt, {'status': 'completed'})
    assert second.lookup(next_attempt.job_id, access_scope=SCOPE)['job']['status'] == 'cancelled'
    assert second.packets.lookup(p, access_scope=SCOPE).status == 'ok'
    release.set(); assert s.shutdown()


@pytest.mark.as1_case('JOB-05')
def test_log_revalidates_retained_evidence_and_outbox_crash_replay(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def factory(service, job):
        class Worker:
            def run(self, request):
                entered.set(); release.wait(5)
                return {'status': 'completed', 'answer': 'done'}
        return Worker()
    s = make(tmp_path, worker_factory=factory).start()
    a = s.submit(question='Thing callers', access_scope=SCOPE)
    assert entered.wait(2)
    original = s.packets.put
    monkeypatch.setattr(s.packets, 'put', lambda p: (_ for _ in ()).throw(OSError('crash before packet write')))
    attempt = s.lookup(a['job_id'], access_scope=SCOPE)['job']['attempt']
    with pytest.raises(OSError):
        s.observe(a['job_id'], attempt, {'path': 'src/module.py', 'entity': 'Thing', 'hidden_reasoning': 'never persist'})
    ref = s.lookup(a['job_id'], access_scope=SCOPE)['observations'][0]['packet_id']
    assert 'never persist' not in s.path.read_bytes().decode(errors='ignore')
    monkeypatch.setattr(s.packets, 'put', original)
    restored = make(tmp_path)
    restored.reconcile(); restored.reconcile()
    assert restored.packets.lookup(ref, access_scope=SCOPE).status == 'ok'
    assert restored.finish(a['job_id'], attempt, {'status': 'completed', 'answer': 'done'})
    records = restored.log(access_scope=SCOPE, query='src/module.py')
    assert records[0]['evidence']['packets']
    assert any(o['reason'] == 'stale' for o in records[0]['evidence']['omissions'])
    assert not restored.log(access_scope={**SCOPE, 'principal': 'bob'}, query='Thing')
    release.set(); s.shutdown()


class Turns:
    def __init__(self, turns):
        self.turns = iter(turns); self.requests = []
    def run_observer_turn(self, request):
        self.requests.append(request)
        turn = next(self.turns)
        if callable(turn): turn = turn(request)
        return {'status': 'available', 'text': json.dumps(turn)}


def trusted(tmp_path, backend, tools=lambda *a: {'text': 'shared'}):
    source = SKILLS/'local-coding-worker/local_worker/observer_runtime.py'
    return TrustedObserverFactory(SKILLS, hashlib.sha256(source.read_bytes()).hexdigest(),
        backend=backend, roots=[tmp_path, SKILLS], tools=tools,
        skills={'fixture': {'name': 'fixture', 'root': str(tmp_path/'fixture')}})


@pytest.mark.as1_case('JOB-06')
def test_actual_port_shared_tools_no_injection_and_agentic_registered_skill(tmp_path):
    skill = tmp_path/'fixture'; skill.mkdir()
    entry = '# Fixture\nUse maps.md\n'; resource = 'Exact selected instructions\n'
    (skill/'SKILL.md').write_text(entry); (skill/'maps.md').write_text(resource)
    calls = []
    def tools(name, arguments, scope):
        calls.append((name, scope)); return {'text': 'shared semantic record'}
    def final(request):
        observations = json.loads(request['messages'][1]['content'])['observations']
        return {'answer': 'read sources', 'findings': [{'text': 'selected source', 'evidence_packets': [observations[-1]['packet_id']]}],
            'skill_selection': {'format': 'pc-skill-selection/1', 'selections': [{'skill': 'fixture', 'resource': 'maps.md',
                'content_sha256': hashlib.sha256(resource.encode()).hexdigest(), 'line_start': 1, 'line_end': 1, 'reason': 'useful'}],
                'synthesis': 'selected', 'unresolved': []}}
    backend = Turns([{'tool': 'command', 'arguments': {'argv': ['cat', str(skill/'SKILL.md')], 'cwd': str(skill)}},
        {'tool': 'command', 'arguments': {'argv': ['cat', str(skill/'maps.md')], 'cwd': str(skill)}},
        {'tool': 'search', 'arguments': {'query': 'fixture'}}, final])
    factory = trusted(tmp_path, backend, tools)
    s = make(tmp_path, worker_factory=factory).start()
    try:
        scope = {k: v for k, v in SCOPE.items() if k != 'project'}
        a = s.submit(question='select useful instruction', access_scope=scope, mode='skill', skill='fixture')
        value = wait(lambda: (v if (v := s.lookup(a['job_id'], access_scope=scope))['job']['status'] in {'completed','partial'} else None))
        assert value['job']['status'] == 'completed', value
        assert calls == [('search', scope)]
        context = json.loads(backend.requests[0]['messages'][1]['content'])
        assert set(context) == {'question', 'scope', 'hints', 'skill', 'observations'}
        assert context['observations'] == []
        assert not calls or 'overview' not in [c[0] for c in calls]
        assert SHARED_TOOLS == {'overview','delta','frontier','search','evidence','impact','history','machine'}
        assert value['job']['project'] is None
    finally:
        s.shutdown()
    with pytest.raises(ValueError, match='receipt mismatch'):
        TrustedObserverFactory(SKILLS, '0'*64, backend=backend, roots=[tmp_path], tools=tools)


@pytest.mark.as1_case('JOB-07')
def test_actual_sandbox_network_write_credentials_output_cleanup_and_provenance(tmp_path):
    # Use the real sandbox, with a worker obtained through the trusted receipt seam.
    s = make(tmp_path, worker_factory=trusted(tmp_path, Turns([])))
    # Claim a durably admitted job while a dispatcher is held in another attempt.
    hold = threading.Event(); entered = threading.Event()
    def blocked(service, job):
        class Worker:
            def run(self, request): entered.set(); hold.wait(5); return {'status': 'completed'}
        return Worker()
    s.worker_factory = blocked; s.start()
    try:
        a = s.submit(question='sandbox', access_scope=SCOPE); assert entered.wait(2)
        job = __import__('project_control.as1_contracts', fromlist=['DurableJob']).DurableJob.model_validate(s.lookup(a['job_id'], access_scope=SCOPE)['job'])
        factory = trusted(tmp_path, Turns([]))
        worker = factory(s, job)
        (tmp_path/'source.txt').write_text('exact bytes\n')
        result = worker.command.run(['cat', str(tmp_path/'source.txt')], str(tmp_path))
        assert result['status'] == 'completed', result
        assert result['source_reads'][0]['content_sha256'] == hashlib.sha256(b'exact bytes\n').hexdigest()
        result = worker.command.run(['/bin/sh', '-c', 'touch forbidden; echo scratch >/tmp/probe; cat /tmp/probe; test ! -e /dev/nvidia0; test -z "$SECRET_FOR_TEST"'], str(tmp_path))
        assert result['exit_code'] == 0 and result['stdout'] == 'scratch\n'
        assert not (tmp_path/'forbidden').exists()
        assert result['scope']['provenance'] == 'coarse_volatile' and 'source_reads' not in result
        result = worker.command.run(['/usr/bin/python3', '-c', 'import socket; s=socket.socket(); s.settimeout(.1); s.connect(("1.1.1.1",443))'], str(tmp_path))
        assert result['exit_code'] != 0
        result = worker.command.run(['/bin/sh', '-c', 'yes x'], str(tmp_path), timeout_seconds=.1, max_output_bytes=123)
        assert result['timed_out'] and result['truncated'] and len(result['stdout'].encode()) <= 123
        result = worker.command.run(['/bin/sh', '-c', 'sleep 30 & wait'], str(tmp_path), timeout_seconds=.1)
        assert result['timed_out'] and result['duration_seconds'] < 2
        assert worker.command.run(['pwd'], '/')['status'] == 'denied'
    finally:
        hold.set(); s.shutdown()

@pytest.mark.as1_case('JOB-04')
def test_running_cancel_closes_owned_session_and_rejects_late_checkpoint(tmp_path):
    entered, release = threading.Event(), threading.Event()
    class Backend:
        def __init__(self): self.opened = []; self.closed = []
        def open_sessions(self, count, **kwargs):
            self.opened.append((count, kwargs)); return {'status': 'available', 'session_ids': ['own-session']}
        def close_session(self, session): self.closed.append(session)
    backend = Backend()
    def factory(service, job):
        class Worker:
            def run(self, request):
                assert request['session_id'] == 'own-session'
                entered.set(); release.wait(5)
                assert not service.fence(job.job_id, job.attempt)
                with pytest.raises(RuntimeError, match='stale_attempt'):
                    service.checkpoint(job.job_id, job.attempt, [])
                return {'status': 'stale_attempt'}
        return Worker()
    s = make(tmp_path, worker_factory=factory, backend=backend).start()
    try:
        a = s.submit(question='cancel inflight', access_scope=SCOPE); assert entered.wait(2)
        assert s.cancel(a['job_id'], access_scope=SCOPE)
        release.set()
        wait(lambda: backend.closed)
        assert backend.closed == ['own-session'] and len(backend.opened) == 1
        assert s.lookup(a['job_id'], access_scope=SCOPE)['job']['status'] == 'cancelled'
    finally:
        release.set(); s.shutdown()

@pytest.mark.as1_case('JOB-03')
def test_concurrent_request_id_admission_and_gpu_free_stopped_lookup(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    entered, release = threading.Event(), threading.Event()
    def factory(service, job):
        class Worker:
            def run(self, request): entered.set(); release.wait(5); return {'status': 'completed', 'answer': 'done'}
        return Worker()
    s = make(tmp_path, worker_factory=factory).start()
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(lambda _: s.submit(question='identical', access_scope=SCOPE, request_id='race'), range(8)))
        assert all(r['accepted'] for r in replies)
        assert len({r['job_id'] for r in replies}) == 1
        with sqlite3.connect(s.path) as db: assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
    finally:
        release.set(); s.shutdown()
    restored = make(tmp_path)  # No factory/backend capability at all.
    assert restored.lookup(replies[0]['job_id'], access_scope=SCOPE)['job']['status'] == 'completed'
    assert restored.submit(question='identical', access_scope=SCOPE, request_id='race')['job_id'] == replies[0]['job_id']
    assert restored.log(access_scope=SCOPE, query='identical')

@pytest.mark.as1_case('JOB-05')
def test_hints_are_scoped_and_pinned_before_admission_reply(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def factory(service, job):
        class Worker:
            def run(self, request): entered.set(); release.wait(5); return {'status': 'completed'}
        return Worker()
    s = make(tmp_path, worker_factory=factory).start()
    try:
        packet_now = [time.time()]
        s.packets.clock = lambda: packet_now[0]
        packet = s.packets.create(tool='search', payload={'text': 'live input'}, access_scope=SCOPE, ttl_seconds=1)
        admitted = s.submit(question='read hint', access_scope=SCOPE, hints=[packet.alias])
        assert admitted['accepted']
        packet_now[0] += 2
        assert packet.packet_id not in s.packets.gc()
        assert s.submit(question='unknown hint', access_scope=SCOPE, hints=['missing'])['reason'] == 'hint_unavailable'
        assert s.submit(question='foreign', access_scope={**SCOPE, 'principal': 'bob'}, hints=[packet.alias])['reason'] == 'hint_unavailable'
        assert s.submit(question='skill', access_scope=SCOPE, mode='skill', skill='unknown')['reason'] == 'unregistered_skill'
    finally:
        release.set(); s.shutdown()

@pytest.mark.as1_case('JOB-05', 'JOB-03')
def test_expired_old_terminal_does_not_block_drain_or_request_id_retry(tmp_path):
    now = [1000.]
    packets = SQLitePacketStore(tmp_path/'packets', recent_terminal_limit=1, clock=lambda: now[0])
    entered, release = threading.Event(), threading.Event()
    def factory(service, job):
        class Worker:
            def run(self, request): entered.set(); release.wait(5); return {'status': 'completed', 'answer': 'done'}
        return Worker()
    s = JobService(tmp_path/'jobs', packets=packets, worker_factory=factory).start()
    try:
        hint = packets.create(tool='search', payload={'text': 'old'}, access_scope=SCOPE, ttl_seconds=1)
        first = s.submit(question='old', hints=[hint.alias], request_id='old', access_scope=SCOPE)
        assert entered.wait(2); release.set()
        wait(lambda: s.lookup(first['job_id'], access_scope=SCOPE)['job']['status']=='completed')
        second = s.submit(question='new', access_scope=SCOPE)
        wait(lambda: s.lookup(second['job_id'], access_scope=SCOPE)['job']['status']=='completed')
        s.reconcile()  # Wait for eventual packet retention following terminal commit.
        now[0] += 2; assert hint.packet_id in packets.gc()
        s.reconcile()
        retry = s.submit(question='old', hints=[hint.alias], request_id='old', access_scope=SCOPE)
        assert retry['accepted'] and retry['job_id'] == first['job_id']
        third = s.submit(question='still drain', access_scope=SCOPE)
        wait(lambda: s.lookup(third['job_id'], access_scope=SCOPE)['job']['status']=='completed')
    finally:
        release.set(); s.shutdown()

@pytest.mark.as1_case('JOB-02')
def test_unicode_storage_cap_counts_real_utf8_bytes(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def factory(service, job):
        class Worker:
            def run(self, request): entered.set(); release.wait(5); return {'status': 'completed'}
        return Worker()
    s = make(tmp_path, worker_factory=factory, max_storage_bytes=18000).start()
    try:
        a = s.submit(question='unicode', access_scope=SCOPE); assert entered.wait(2)
        attempt = s.lookup(a['job_id'], access_scope=SCOPE)['job']['attempt']
        with pytest.raises(ValueError, match='storage cap'):
            for _ in range(10): s.observe(a['job_id'], attempt, {'text': '😀'*1000})
        with sqlite3.connect(s.path) as db:
            usage = db.execute('SELECT sum(length(CAST(record AS BLOB))+length(CAST(observations AS BLOB))) FROM jobs').fetchone()[0]
            usage += db.execute('SELECT sum(length(CAST(packet AS BLOB))) FROM outbox').fetchone()[0]
        assert usage <= s.max_storage_bytes
    finally:
        release.set(); s.shutdown()
