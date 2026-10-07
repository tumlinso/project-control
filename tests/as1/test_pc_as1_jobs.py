"""CPU fixtures exercise real SQLite and the actual installed observer port."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from project_control.as1_jobs import JobService, TrustedObserverFactory, BUSY, SHARED_TOOLS
from project_control.as1_packets import SQLitePacketStore

SCOPE = {'principal': 'alice', 'profile': 'observer', 'project': 'pc'}
SKILLS = Path('/home/tumlinson/.agents/skills')
ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT/'src/project_control/local_runtime'


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


def source_environment():
    environment = dict(os.environ)
    for key in ('PROJECT_CONTROL_RELEASE_MANIFEST', 'PROJECT_CONTROL_RELEASE_DIGEST',
                'PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT', 'CODING_WORKFLOW_RUNTIME_FINGERPRINT',
                'PROJECT_CONTROL_SKILLS_ROOT', 'PROJECT_CONTROL_OBSERVER_SKILLS_ROOT', 'OBSERVER_SKILLS_ROOT',
                'CODING_WORKFLOW_SKILLS_ROOT', 'TODO_ORCHESTRATOR_READ_ONLY', 'TODO_ORCHESTRATOR_STATE_DIR'):
        environment.pop(key, None)
    environment.pop('PROJECT_CONTROL_SKILLS_ROOT', None)
    environment['PROJECT_CONTROL_OBSERVER_SKILLS_ROOT'] = str(SKILLS)
    environment['PROJECT_CONTROL_LOCAL_RUNTIME_ROOT'] = str(RUNTIME_ROOT)
    environment['PROJECT_CONTROL_LOCAL_RUNTIME_MANIFEST_SHA256'] = hashlib.sha256((RUNTIME_ROOT/'receiver-manifest.json').read_bytes()).hexdigest()
    environment['PYTHONPATH'] = str(ROOT/'src')
    environment['AS1_SOURCE_HASHES'] = json.dumps({
        name: hashlib.sha256((ROOT/'src/project_control'/ (name+'.py')).read_bytes()).hexdigest()
        for name in ('as1_jobs', 'as1_packets', 'as1_surface')}, sort_keys=True)
    return environment


@pytest.mark.as1_case('JOB-01')
def test_admission_and_process_lived_dispatch_after_disconnect(tmp_path):
    # An independent child process owns dispatch; parent admission ends before processing.
    admitted = tmp_path/'admitted.json'
    release = tmp_path/'release'
    script = r'''
import hashlib, json, os, pathlib, sys, time
from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore
for name, expected in json.loads(os.environ['AS1_SOURCE_HASHES']).items():
 module=__import__('project_control.'+name,fromlist=[''])
 source=pathlib.Path(module.__file__).resolve()
 assert source == pathlib.Path.cwd()/'src/project_control'/ (name+'.py')
 assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
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
    # Match the control fixtures: bind only the child to actual candidate source,
    # leaving the native gate parent's deployed release identity untouched.
    environment = source_environment()
    child = subprocess.Popen([sys.executable, '-c', script, str(tmp_path), json.dumps(SCOPE)],
                             cwd=ROOT, env=environment)
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
        assert s.lookup(a['job_id'], access_scope={**SCOPE, 'principal': 'bob'})['status'] == 'ok'
        related = s.submit(question='inspect Thing callers', access_scope=SCOPE)
        assert related['job_id'] != a['job_id']
    finally:
        release.set(); s.shutdown()


@pytest.mark.as1_case('JOB-03')
def test_cancel_inquiry_uses_exact_inquiry_identity_without_exposing_job_id(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def factory(service, job):
        class Worker:
            def run(self, request):
                entered.set(); release.wait(30)
                return {'status': 'completed', 'answer': 'late result'}
        return Worker()
    s = make(tmp_path, worker_factory=factory).start()
    question = 'read the configured startup timeout'
    try:
        identity = s._inquiry_identity(question, SCOPE, 'investigate', None)
        admitted = s.submit(question=question, access_scope=SCOPE,
                            mode='investigate', _identity=identity)
        assert admitted['accepted']
        assert entered.wait(2)

        # Near matches and a same inquiry from another full scope are safe no-ops.
        assert not s.cancel_inquiry(question + ' ', SCOPE)
        assert not s.cancel_inquiry(question, {**SCOPE, 'principal': 'bob'})
        assert not s.cancel_inquiry(question, SCOPE, mode='skill', skill='cuda')

        assert s.cancel_inquiry(question, SCOPE) is True
        assert not s.cancel_inquiry(question, SCOPE)
        assert s.lookup(admitted['job_id'], access_scope=SCOPE)['job']['status'] == 'cancelled'
    finally:
        release.set(); s.shutdown()


@pytest.mark.as1_case('JOB-03')
def test_inquiry_startup_timeout_bounds_readiness_and_keeps_zero_budget_cache_reads(tmp_path):
    deadlines = []

    def factory(service, job):
        class Worker:
            def run(self, request):
                return {'status': 'completed', 'answer': 'cached answer'}
        return Worker()

    s = make(tmp_path, worker_factory=factory,
             freshness_provider=lambda job: {'fresh': True}).start()
    def ready(*, deadline_epoch):
        deadlines.append((deadline_epoch, time.time()))
        return {'status': 'ready'}
    s.demand_runtime_ready = ready
    try:
        first = s.inquire('bounded startup', SCOPE, foreground_timeout=3,
                          startup_timeout=2.5)
        assert first['status'] == 'completed'
        assert 2.4 <= deadlines[0][0] - deadlines[0][1] <= 2.5

        # A fresh exact cache hit needs no model/runtime startup, even at zero.
        cached = s.inquire('bounded startup', SCOPE, foreground_timeout=0,
                           startup_timeout=0)
        assert cached['status'] == 'completed'
        assert len(deadlines) == 1

        # A new inquiry at the expired boundary cannot invoke the readiness hook.
        exhausted = s.inquire('startup expired', SCOPE, foreground_timeout=0,
                              startup_timeout=0)
        assert exhausted == {
            'status': 'unavailable',
            'reason': 'demand_deadline_exhausted_before_start',
        }
        assert len(deadlines) == 1

        for invalid in (True, -0.01, 120.01, float('inf'), float('nan'), 10**400):
            with pytest.raises(ValueError, match='invalid_startup_timeout'):
                s.inquire('invalid timeout', SCOPE, startup_timeout=invalid)
    finally:
        s.shutdown()


@pytest.mark.as1_case('JOB-03')
def test_dispatch_readiness_failure_retains_only_stable_private_reason(tmp_path):
    class Backend:
        def open_sessions(self, *_args, **_kwargs):
            pytest.fail('model session must not open after readiness failure')

    def unexpected_worker(*_args):
        pytest.fail('worker must not be created after readiness failure')

    s = make(tmp_path, backend=Backend(), worker_factory=unexpected_worker)
    s.demand_runtime_ready = lambda **_kwargs: {
        'status': 'unavailable',
        'reason': 'inference_project_control_fingerprint_mismatch: /private/runtime/path',
    }
    s.start()
    try:
        admitted = s.submit(question='retain readiness reason', access_scope=SCOPE)
        def failed_readiness():
            value = s.lookup(admitted['job_id'], access_scope=SCOPE)
            return (value if value['job'].get('failure_reason') is not None else None)
        value = wait(failed_readiness)
        job = value['job']
        assert job['failure_reason'] == 'inference_project_control_fingerprint_mismatch'
        assert '/private/runtime/path' not in json.dumps(job)
        assert s._inquiry_failure_class(job) == 'runtime_mismatch'
    finally:
        s.shutdown()


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
    # Simulate a crashed owner. A live noncooperative operation retains its slot.
    with s._db() as db:
        db.execute('UPDATE execution_slots SET owner_pid=NULL WHERE job=?', (a['job_id'],))
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
    now[0] += 16
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
    assert restored.log(access_scope={**SCOPE, 'principal': 'bob'}, query='Thing')
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
    from project_control.runtime_binding import local_runtime_identity
    identity = local_runtime_identity(root=RUNTIME_ROOT)
    source = identity.root/'local_worker/observer_runtime.py'
    return TrustedObserverFactory(identity.root, hashlib.sha256(source.read_bytes()).hexdigest(),
        backend=backend, roots=[tmp_path, SKILLS], tools=tools,
        skills={'fixture': {'name': 'fixture', 'root': str(tmp_path/'fixture')}})


@pytest.mark.as1_case('JOB-06')
def test_actual_port_shared_tools_no_injection_and_agentic_registered_skill(tmp_path, monkeypatch):
    skill = tmp_path/'fixture'; skill.mkdir()
    entry = '# Fixture\nUse maps.md\n'; resource = 'Exact selected instructions\n'
    (skill/'SKILL.md').write_text(entry); (skill/'maps.md').write_text(resource)
    calls = []
    def tools(name, arguments, scope):
        calls.append((name, scope)); return {'text': 'shared semantic record'}
    def final(request):
        messages = request['messages']
        replayed = [(json.loads(message['content']), json.loads(messages[index + 1]['content']))
            for index, message in enumerate(messages[:-1])
            if message['role'] == 'assistant' and messages[index + 1]['role'] == 'user']
        assert [call['tool'] for call, packet in replayed] == ['command', 'command', 'search']
        assert replayed[0][0]['arguments']['argv'] == ['cat', str(skill/'SKILL.md')]
        assert replayed[0][1]['stdout'] == entry
        selected_call, selected_packet = replayed[1]
        assert selected_call['arguments']['argv'] == ['cat', str(skill/'maps.md')]
        assert selected_packet['stdout'] == resource
        assert selected_packet['source_reads'][0]['path'] == str(skill/'maps.md')
        assert selected_packet['source_reads'][0]['content_sha256'] == hashlib.sha256(resource.encode()).hexdigest()
        assert selected_packet['packet_id']
        return {'answer': 'read sources', 'findings': [{'text': 'selected source', 'evidence_packets': [selected_packet['packet_id']]}],
            'skill_selection': {'format': 'pc-skill-selection/1', 'selections': [{'skill': 'fixture', 'resource': 'maps.md',
                'content_sha256': hashlib.sha256(resource.encode()).hexdigest(), 'line_start': 1, 'line_end': 1, 'reason': 'useful'}],
                'synthesis': 'selected', 'unresolved': []}}
    backend = Turns([{'tool': 'command', 'arguments': {'argv': ['cat', str(skill/'SKILL.md')], 'cwd': str(skill)}},
        {'tool': 'command', 'arguments': {'argv': ['cat', str(skill/'maps.md')], 'cwd': str(skill)}},
        {'tool': 'search', 'arguments': {'query': 'fixture'}}, final])
    factory = trusted(tmp_path, backend, tools)
    # This test qualifies the real broker/observer/skill-selection path. The
    # host's Bubblewrap cannot create its network namespace here
    # (NETLINK_ROUTE: Operation not permitted), so use a strict, read-only
    # direct-cat executor while retaining the runner's root check and broker
    # packetization. Bubblewrap isolation remains a separate host-qualified
    # boundary and is not claimed by this fixture.
    runtime = factory.load_runtime_module()
    def fixture_direct_cat(runner, argv, cwd, timeout_seconds=10, max_output_bytes=8192,
                           *, guard=None, deadline_epoch=None):
        if (not isinstance(argv, list) or len(argv) != 2 or argv[0] != 'cat'
                or not isinstance(argv[1], str)):
            raise ValueError('fixture command accepts direct cat only')
        working = Path(cwd).resolve(strict=True)
        path = Path(argv[1]).resolve(strict=True)
        if not runner.allows(working) or not runner.allows(path) or not path.is_file():
            return runner._packet({'status': 'denied', 'exit_code': None, 'stdout': '',
                'stderr': '', 'truncated': False, 'timed_out': False,
                'reason': 'fixture_path_outside_allowed_roots'}, guard)
        raw = path.read_bytes()
        truncated = len(raw) > max_output_bytes
        output = raw[:max_output_bytes].decode('utf-8', errors='strict')
        payload = {'status': 'completed', 'exit_code': 0, 'stdout': output,
            'stderr': '', 'truncated': truncated, 'timed_out': False,
            'duration_seconds': 0.0, 'cwd': str(working),
            'scope': {'roots': [str(root) for root in runner.roots],
                      'external': False, 'provenance': 'fixture_direct_cat'}}
        if not truncated:
            payload['source_reads'] = [{'path': str(path),
                'content_sha256': hashlib.sha256(raw).hexdigest(),
                'line_count': len(output.splitlines()), 'method': 'direct_cat'}]
        return runner._packet(payload, guard)
    monkeypatch.setattr(runtime.ReadOnlyCommandRunner, 'run', fixture_direct_cat)
    project_root = tmp_path/'project-repo'; project_root.mkdir()
    from project_control.as1_jobs import source_locator_for_registered_roots
    s = make(tmp_path, worker_factory=factory,
        source_locator_provider=lambda job, path, digest:
            source_locator_for_registered_roots(path, digest, [
                    ('skills', str(tmp_path), str(tmp_path)),
                    ('pc', 'repo', str(project_root)),
                ])).start()
    try:
        scope = dict(SCOPE)
        a = s.submit(question='select useful instruction', access_scope=scope, mode='skill', skill='fixture')
        value = wait(lambda: (v if (v := s.lookup(a['job_id'], access_scope=scope))['job']['status'] in {'completed','partial'} else None))
        assert value['job']['status'] == 'completed', repr(value['observations'][0].get('stderr'))
        # Public MCP inquiry reconciles the cross-store outbox before reading
        # its answer packet; mirror that ordering instead of racing the commit.
        s.reconcile()
        answer_packet = s.packets.lookup(value['job']['result_packet'], access_scope=scope)
        assert answer_packet.status == 'ok', {
            'result_packet': value['job']['result_packet'],
            'job_scope': value['job']['scope'], 'lookup': answer_packet.status}
        assert {(source.project, source.repository, source.path)
                for source in answer_packet.packet.sources} == {
                    ('skills', str(tmp_path.resolve()), 'fixture/SKILL.md'),
                    ('skills', str(tmp_path.resolve()), 'fixture/maps.md'),
                }
        assert calls == [('search', scope)]
        context = json.loads(backend.requests[0]['messages'][1]['content'])
        assert set(context) == {'question', 'scope', 'hints', 'skill', 'progress', 'instruction',
                                'refresh_context', 'log_guidance'}
        assert context['refresh_context'] is None
        assert 'last 50 answered inquiries' in context['log_guidance']
        progress = context['progress']
        assert {key: progress[key] for key in ('stage', 'observation_count', 'remaining_steps')} == {
            'stage': 'initial', 'observation_count': 0, 'remaining_steps': 6}
        assert progress['allowed_observation_packet_ids'] == []
        assert progress['omitted_observation_packet_ids'] == []
        assert progress['input_omitted_observation_packet_ids'] == []
        from project_control.as1_surface import public_inquiry
        public = public_inquiry({'result': {'progress': progress}})
        assert public['result']['progress'] == {
            'stage': 'initial', 'observation_count': 0, 'remaining_steps': 6}
        assert not any(message['role'] == 'assistant' for message in backend.requests[0]['messages'])
        assert not calls or 'overview' not in [c[0] for c in calls]
        assert SHARED_TOOLS == {'overview','delta','frontier','search','evidence','impact','history','machine'}
        assert value['job']['project'] == 'pc'
    finally:
        s.shutdown()
    with pytest.raises(ValueError, match='receipt mismatch'):
        TrustedObserverFactory(RUNTIME_ROOT, '0'*64, backend=backend, roots=[tmp_path], tools=tools)


def test_answer_packet_promotes_verified_command_reads_and_stales_when_file_changes(tmp_path):
    from project_control.as1_surface import InquiryFreshness
    from project_control.as1_contracts import InformationPacket

    skills_root = tmp_path/'skills'
    source = skills_root/'fixture'/'resource.md'
    source.parent.mkdir(parents=True)
    content = 'Exact source text\n'
    source.write_text(content)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    project_root = tmp_path/'project-repo'; project_root.mkdir()
    from project_control.as1_jobs import source_locator_for_registered_roots
    service = make(tmp_path/'broker', source_locator_provider=lambda job, path, digest:
        source_locator_for_registered_roots(path, digest, [
            ('skills', str(skills_root), str(skills_root)),
            ('pc', 'repo', str(project_root)),
        ]))
    job = SimpleNamespace(job_id='job_source_proof_fixture', scope=SCOPE,
        mode='investigate', refresh_context=None)
    project_source = project_root/'src'/'module.py'
    project_source.parent.mkdir()
    project_source.write_text('project-owned source\n')
    project_digest = hashlib.sha256(project_source.read_bytes()).hexdigest()
    project_locator = service.source_locator_provider(job, project_source.resolve(), project_digest)
    assert project_locator.model_dump(exclude_none=True) == {
        'project': 'pc', 'repository': 'repo', 'path': 'src/module.py',
        'content_sha256': project_digest}
    command_payload = {'status': 'completed', 'exit_code': 0, 'truncated': False,
        'timed_out': False, 'stdout': content,
        'source_reads': [{'method': 'direct_cat', 'path': str(source),
                          'content_sha256': digest}]}
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        command_id, _ = service._packet(db, job, command_payload, 'command')
        answer_id, _ = service._packet(db, job, {'answer': 'The source says this.',
            'findings': [{'text': 'The source says this.', 'evidence_packets': [command_id]}]},
            'investigate', parents=[command_id])
        command = json.loads(db.execute('SELECT packet FROM outbox WHERE id=?',
            (command_id,)).fetchone()['packet'])
        answer = json.loads(db.execute('SELECT packet FROM outbox WHERE id=?',
            (answer_id,)).fetchone()['packet'])
    service.packets.put(InformationPacket.model_validate(command))
    service.packets.put(InformationPacket.model_validate(answer))
    service.packets.pin(job.job_id, [command_id, answer_id])
    result = service.packets.lookup(answer_id, access_scope=SCOPE)
    assert result.status == 'ok'
    assert [source.model_dump(exclude_none=True) for source in result.packet.sources] == [{
        'project': 'skills', 'repository': str(skills_root.resolve()),
        'path': 'fixture/resource.md', 'content_sha256': digest,
    }]
    assert result.packet.freshness['dependencies'] == {
        f'file:{skills_root.resolve()}/fixture/resource.md': digest}

    composition = SimpleNamespace(store=service.packets,
        skills=SimpleNamespace(root=skills_root),
        host=SimpleNamespace(projects=frozenset()),
        control=SimpleNamespace(), information=SimpleNamespace(), jobs=service)
    freshness = InquiryFreshness(composition)
    inquiry = {'scope': SCOPE, 'mode': 'investigate', 'hints': [],
        'evidence_packets': [command_id],
        'findings': [{'text': 'The source says this.', 'evidence_packets': [command_id]}],
        'result_packet': answer_id}
    assert freshness(inquiry)['fresh']
    source.write_text('Changed source text\n')
    stale = freshness(inquiry)
    assert not stale['fresh']
    assert {'path': str(source), 'reason': 'changed'} in stale['changed_sources']


def test_registered_source_root_ambiguity_is_not_guessed(tmp_path):
    from project_control.as1_jobs import source_locator_for_registered_roots

    outer = tmp_path/'repository'; skills = outer/'skills'
    skills.mkdir(parents=True)
    source = skills/'guide.md'; source.write_text('source\n')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    # The path is contained by both roots. Neither scope nor prefix depth
    # determines which registered identity owns it.
    assert source_locator_for_registered_roots(source.resolve(), digest, [
        ('skills', str(skills), str(skills)),
        ('pc', 'repo', str(outer)),
    ]) is None
    assert source_locator_for_registered_roots(source.resolve(), digest, [
        ('pc', 'repo-a', str(skills)),
        ('pc', 'repo-b', str(skills)),
    ]) is None


def test_answer_packet_does_not_promote_truncated_or_unrecognized_command_reads(tmp_path):
    from project_control.as1_contracts import InformationPacket

    source = tmp_path/'resource.md'
    source.write_text('Exact source text\n')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    service = make(tmp_path/'broker')
    job = SimpleNamespace(job_id='job_unverified_source_fixture', scope=SCOPE,
        mode='investigate', refresh_context=None)
    with service._db() as db:
        db.execute('BEGIN IMMEDIATE')
        truncated_id, _ = service._packet(db, job, {
            'status': 'completed', 'exit_code': 0, 'truncated': True,
            'source_reads': [{'method': 'direct_cat', 'path': str(source),
                              'content_sha256': digest}]}, 'command')
        unknown_id, _ = service._packet(db, job, {
            'status': 'completed', 'exit_code': 0, 'truncated': False,
            'source_reads': [{'method': 'unknown', 'path': str(source),
                              'content_sha256': digest}]}, 'command')
        answer_id, _ = service._packet(db, job, {'answer': 'No trusted source.'},
            'investigate', parents=[truncated_id, unknown_id])
        answer = json.loads(db.execute('SELECT packet FROM outbox WHERE id=?',
            (answer_id,)).fetchone()['packet'])
    packet = InformationPacket.model_validate(answer)
    assert packet.sources == []
    assert packet.freshness == {'volatile': True, 'max_age_seconds': 0}


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
        # Caller/profile are provenance; project and other authority fields
        # define sharing. A cross-project hint must not be pinned/admitted.
        assert s.submit(question='foreign project', access_scope={**SCOPE, 'project': 'other'},
                        hints=[packet.alias])['reason'] == 'hint_unavailable'
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


@pytest.mark.as1_case('JOB-04', 'JOB-05', 'JOB-03')
@pytest.mark.parametrize('legacy', [False, True], ids=['installed-port', 'legacy-producer'])
def test_step_budget_is_terminal_with_retained_source_and_idempotent_restart(tmp_path, legacy):
    source = tmp_path / 'module.py'
    source.write_text('def calculate_total(values):\n    return sum(values)\n')
    backend = Turns([{'tool': 'command', 'arguments': {
        'argv': ['cat', str(source)], 'cwd': str(tmp_path)}}] * 6)
    factory = trusted(tmp_path, backend)
    if legacy:
        native_factory = factory
        def factory(service, job):
            native = native_factory(service, job)
            class Legacy:
                def run(self, request):
                    result = native.run(request)
                    result.update(status='yielding', reason='step_budget', unresolved_questions=[])
                    return result
            return Legacy()
    s = make(tmp_path, worker_factory=factory, retry_seconds=0).start()
    try:
        hint = s.packets.create(tool='search', payload={'path': str(source)}, access_scope=SCOPE)
        args = dict(question='Explain calculate_total', access_scope=SCOPE,
                    request_id='budget', hints=[hint.alias])
        admitted = s.submit(**args)
        value = wait(lambda: (v if (v := s.poll(admitted['job_id'], access_scope=SCOPE))['job']['status'] == 'partial' else None))
        s.reconcile()
        job = value['job']
        expected_attempt = 1 if legacy else 6
        assert job['attempt'] == expected_attempt and job['unresolved_questions']
        assert len(backend.requests) == 6
        assert s.claim() is None
        # Five tool effects are observed. The sixth model round is final-only,
        # so its scripted tool proposal is never dispatched or persisted.
        assert len(value['observations']) == 5
        from project_control.assistance.frames import FrameStore
        with s._db() as db:
            frame = FrameStore(db).get_frame(admitted['job_id'])
        if not legacy:
            assert frame.turns_used == 6
        else:
            assert frame.turns_used == 0  # compatibility producers omit slice-turn telemetry
        for observation in value['observations']:
            assert observation['source_reads'][0]['content_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
            assert s.packets.lookup(observation['packet_id'], access_scope=SCOPE).status == 'ok'
        result = s.packets.lookup(job['result_packet'], access_scope=SCOPE)
        assert result.packet.payload['status'] == 'partial'
        expected_reason = 'step_budget_exhausted' if legacy else 'final_round_requires_answer'
        assert result.packet.payload['reason'] == expected_reason
        assert result.packet.payload['unresolved_questions'] == job['unresolved_questions']
        assert s.submit(**args)['job_id'] == job['job_id']
        assert s.poll(job['job_id'], access_scope=SCOPE)['job']['attempt'] == expected_attempt
        assert s.claim() is None and len(backend.requests) == 6
    finally:
        s.shutdown()
    restored = make(tmp_path)
    restored.reconcile()
    assert restored.claim() is None
    assert restored.submit(**args)['job_id'] == job['job_id']
    assert restored.poll(job['job_id'], access_scope=SCOPE)['job'] == job
    assert restored.packets.lookup(job['result_packet'], access_scope=SCOPE).status == 'ok'
    assert restored.packets.lookup(hint.packet_id, access_scope=SCOPE).status == 'ok'


@pytest.mark.as1_case('JOB-04')
@pytest.mark.parametrize('status,reason', [
    ('yielding', 'foreground_preemption'), ('yielding', 'session_unavailable'),
    ('queued_after_eviction', 'session_evicted')])
def test_recoverable_yields_resume_same_job_from_retained_observation(tmp_path, status, reason):
    attempts = []
    def factory(service, job):
        class Worker:
            def run(self, request):
                attempts.append(request)
                if job.attempt == 1:
                    service.observe(job.job_id, job.attempt, {'text': 'checkpoint source'})
                    return {'status': status, 'reason': reason}
                assert request['observations'][0]['text'] == 'checkpoint source'
                return {'status': 'completed', 'answer': 'Resumed from retained source.'}
        return Worker()
    s = make(tmp_path, worker_factory=factory, retry_seconds=0).start()
    try:
        admitted = s.submit(question='Resume after interruption', access_scope=SCOPE)
        value = wait(lambda: (v if (v := s.poll(admitted['job_id'], access_scope=SCOPE))['job']['status'] == 'completed' else None))
        assert value['job']['attempt'] == 2
        assert len(attempts) == 2 and attempts[0]['job_id'] == attempts[1]['job_id']
        assert value['observations'][0]['text'] == 'checkpoint source'
        assert s.claim() is None
    finally:
        s.shutdown()


@pytest.mark.as1_case('JOB-01', 'JOB-04', 'JOB-05')
def test_progress_regressions_bind_child_source_without_changing_parent_deployment(tmp_path):
    parent_environment = dict(os.environ)
    script = r"""
import hashlib, json, os, pathlib, pytest
for name, expected in json.loads(os.environ['AS1_SOURCE_HASHES']).items():
 module=__import__('project_control.'+name,fromlist=[''])
 source=pathlib.Path(module.__file__).resolve()
 assert source == pathlib.Path.cwd()/'src/project_control'/(name+'.py')
 assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
from project_control.runtime_binding import local_runtime_identity
identity=local_runtime_identity(root=os.environ['PROJECT_CONTROL_LOCAL_RUNTIME_ROOT'])
native=identity.root/'local_worker/observer_runtime.py'
manifest=json.loads((identity.root/'receiver-manifest.json').read_text())
assert hashlib.sha256(native.read_bytes()).hexdigest() == manifest['files']['local_worker/observer_runtime.py']
assert identity.manifest_sha256 == os.environ['PROJECT_CONTROL_LOCAL_RUNTIME_MANIFEST_SHA256']
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','tests/as1/test_pc_as1_jobs.py',
 '-k','step_budget_is_terminal or recoverable_yields_resume']))
"""
    child = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                           env=source_environment(), capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    assert '5 passed' in child.stdout
    assert dict(os.environ) == parent_environment
