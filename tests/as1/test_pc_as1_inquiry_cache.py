"""CPU-only inquiry identity, capacity, refresh, lease and retrieval contracts."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from project_control.as1_jobs import (JobService, TrustedObserverFactory,
    WORKER_JOB_INPUT_MAX_BYTES, WORKER_OBSERVATION_MAX_BYTES)
from project_control.as1_packets import SQLitePacketStore
from project_control.as1_surface import public_inquiry

SCOPE = {'principal': 'alice', 'profile': 'observer', 'project': 'pc'}
SKILLS = Path('/home/tumlinson/.agents/skills')


def make(tmp_path, **kwargs):
    return JobService(tmp_path/'jobs', packets=SQLitePacketStore(tmp_path/'packets'), **kwargs)


def wait(predicate):
    end = time.monotonic()+5
    while time.monotonic() < end:
        if value := predicate():
            return value
        time.sleep(.01)
    raise AssertionError('bounded wait expired')


def blocking(entered, release, requests):
    def factory(service, job):
        class Worker:
            def run(self, request):
                requests.append(request); entered.set(); release.wait(5)
                return {'status': 'completed', 'answer': 'answer '+job.question}
        return Worker()
    return factory


def trusted_worker(tmp_path, backend, calls):
    source = SKILLS/'local-coding-worker/local_worker/observer_runtime.py'
    return TrustedObserverFactory(SKILLS, hashlib.sha256(source.read_bytes()).hexdigest(),
        backend=backend, roots=[tmp_path], tools=lambda name, arguments, scope: calls.append((name, arguments, scope))
        or {'text': 'observed answer text', 'nested': {'packet_id': 'nested-packet-id'}})


def test_literal_identity_read_only_duplicates_and_refresh(tmp_path):
    entered, release, requests = threading.Event(), threading.Event(), []
    fresh = [True]
    s = make(tmp_path, worker_factory=blocking(entered, release, requests),
             freshness_provider=lambda job: {'fresh': fresh[0], 'changed_sources': ['module.py']}).start()
    try:
        assert s.inquire('  raw  question ', SCOPE, foreground_timeout=0)['status']=='thinking'
        assert entered.wait(2)
        before = s.path.read_bytes()
        with s._db() as db:
            snapshot = [tuple(r) for r in db.execute('SELECT * FROM jobs')]
        for n in range(4):
            assert s.inquire('  raw  question ', SCOPE, hints=['missing'], request_id=str(n))['status']=='thinking'
        with s._db() as db:
            assert [tuple(r) for r in db.execute('SELECT * FROM jobs')] == snapshot
        release.set()
        wait(lambda: s.log(access_scope=SCOPE))
        cached = wait(lambda: (v if (v:=s.inquire('  raw  question ', SCOPE))['status']=='completed' else None))
        assert cached['status']=='completed' and cached['job']['question']=='  raw  question '
        assert len(requests)==1
        s.inquire('raw question', SCOPE, foreground_timeout=0)
        wait(lambda: len(requests)==2)
        fresh[0]=False
        release.clear(); entered.clear()
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies=list(pool.map(lambda n: s.inquire('  raw  question ', SCOPE, request_id=str(n),
                execution_question='augmented instructions', foreground_timeout=0), range(8)))
        assert all(r['status']=='thinking' for r in replies)
        wait(lambda: len(requests)==3)
        context=requests[-1]['refresh_context']
        assert context['prior_job']['answer']=='answer   raw  question '
        assert context['change_info']['changed_sources']==['module.py']
        assert requests[-1]['question']=='augmented instructions'
        assert requests[-1]['deadline_epoch']-time.time() <= 300
    finally:
        release.set(); s.shutdown()


def test_transactional_capacity_two_dispatchers_and_cancelled_operation_slot(tmp_path):
    entered, release, requests = threading.Event(), threading.Event(), []
    factory=blocking(entered, release, requests)
    a=make(tmp_path, worker_factory=factory, lease_seconds=.15).start()
    b=make(tmp_path, worker_factory=factory, lease_seconds=.15).start()
    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            replies=list(pool.map(lambda n: (a if n%2 else b).inquire(str(n), SCOPE, foreground_timeout=0), range(10)))
        assert sum(r['status']=='thinking' for r in replies)==6
        assert sum(r['status']=='busy' for r in replies)==4
        wait(lambda: len(requests)==2)
        time.sleep(.4)
        assert len(requests)==2  # heartbeat spans inference, including another host
        assert a.cancel(requests[0]['job_id'], access_scope=SCOPE)
        time.sleep(.2)
        assert len(requests)==2  # cancellation fences writes but holds operation slot
        release.set()
        wait(lambda: len(requests)==6)
    finally:
        release.set(); a.shutdown(); b.shutdown()


def test_global_identity_and_coalesced_race(tmp_path):
    entered, release, requests = threading.Event(), threading.Event(), []
    s=make(tmp_path, worker_factory=blocking(entered, release, requests)).start()
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            replies=list(pool.map(lambda n: s.inquire('exact', SCOPE, request_id=str(n), foreground_timeout=0), range(12)))
        assert all(r['status']=='thinking' for r in replies)
        with s._db() as db:
            assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
        for key in ['principal','profile','project']:
            s.inquire('exact', {**SCOPE,key:'other'}, foreground_timeout=0)
        with s._db() as db:
            assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==2
    finally:
        release.set(); s.shutdown()


def test_retry_backoff_deadline_and_max_three_attempts(tmp_path):
    now=[1000.]
    s=make(tmp_path, clock=lambda:now[0])
    # Direct fixture admission with a live-looking dispatcher avoids inference.
    class Live:
        def is_alive(self): return True
    s._thread=Live()
    j=s.submit(question='retry',access_scope=SCOPE)['job_id']
    for attempt, delay in [(1,5),(2,15),(3,15)]:
        job=s.claim(); assert job.attempt==attempt
        assert s.finish(j,attempt,{'status':'yielding','reason':'session_unavailable'})
        # Direct finish is a fixture; emulate dispatcher releasing the operation.
        with s._db() as db: db.execute('DELETE FROM execution_slots WHERE job=?',(j,))
        if attempt<3:
            assert s.claim() is None
            now[0]+=delay
    assert s.lookup(j,access_scope=SCOPE)['job']['status']=='failed'
    assert s.claim() is None
    deadline=s.submit(question='wait expires',access_scope=SCOPE)['job_id']
    now[0]+=301
    assert s.claim() is None
    assert s.lookup(deadline,access_scope=SCOPE)['job']['status']=='failed'


def test_cache_window_history_and_last_fifty_bm25_before_ranking(tmp_path):
    def factory(service,job):
        class Worker:
            def run(self,request): return {'status':'completed','answer':'answer '+job.question}
        return Worker()
    s=make(tmp_path,worker_factory=factory,freshness_provider=lambda job:{'fresh':True}).start()
    try:
        for i in range(53):
            caller = {**SCOPE, 'principal': f'caller-{i}', 'profile': 'coder' if i % 2 else 'observer'}
            question='rare-old' if i==0 else f'question {i}'
            s.inquire(question,caller,foreground_timeout=0)
            result=wait(lambda: (v if (v:=s.inquire(question,SCOPE,foreground_timeout=0))['status']=='completed' else None))
            assert result['status']=='completed'
        with s._db() as db:
            assert db.execute('SELECT count(*) FROM inquiry_index').fetchone()[0]==50
            assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==53
        assert not s.log(access_scope=SCOPE,query='rare-old')
        recent=s.log(access_scope=SCOPE)
        assert len(recent)==5 and recent[0]['question']=='question 52'
        assert 'job' not in recent[0] and 'observations' not in recent[0]
        assert s.log(access_scope=SCOPE,query='question 48')[0]['question']=='question 48'
        assert s.log(access_scope={**SCOPE,'principal':'bob','profile':'coder'},query='question')
        s.inquire('rare-old',SCOPE,foreground_timeout=2)
        with s._db() as db: assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==54
    finally: s.shutdown()


def test_startup_deadline_forwarding_and_noncooperative_slot(tmp_path):
    now=[1000.]
    opened, release=threading.Event(),threading.Event()
    calls=[]
    class Backend:
        def open_sessions(self,count,**kwargs):
            calls.append(kwargs);opened.set();release.wait(5)
            return {'status':'available','session_ids':['session']}
        def close_session(self,session): calls.append(session)
    factory_calls=[]
    def factory(service,job): factory_calls.append(job);raise AssertionError('expired startup must not begin inference')
    s=make(tmp_path,worker_factory=factory,backend=Backend(),clock=lambda:now[0]).start()
    try:
        s.inquire('startup deadline',SCOPE,foreground_timeout=0)
        assert opened.wait(2) and calls[0]['deadline_epoch']==1300.
        with s._db() as db: job_id=db.execute('SELECT id FROM jobs').fetchone()[0]
        now[0]=1301.
        with s._db() as db: assert db.execute('SELECT count(*) FROM execution_slots').fetchone()[0]==1
        release.set()
        wait(lambda: calls[-1]=='session')
        wait(lambda: s._settled(job_id))
        with s._db() as db:
            assert db.execute('SELECT count(*) FROM execution_slots').fetchone()[0]==0
            job=json.loads(db.execute('SELECT record FROM jobs').fetchone()[0])
            assert job['status']=='failed'
        assert not factory_calls and not s.log(access_scope=SCOPE)
    finally: release.set();s.shutdown()


def test_stopped_fresh_cache_and_storage_failure(tmp_path,monkeypatch):
    entered,release,requests=threading.Event(),threading.Event(),[]
    release.set()
    s=make(tmp_path,worker_factory=blocking(entered,release,requests),freshness_provider=lambda job:{'fresh':True}).start()
    try: assert s.inquire('answer',SCOPE,foreground_timeout=2)['status']=='completed'
    finally: s.shutdown()
    restored=make(tmp_path,freshness_provider=lambda job:{'fresh':True})
    assert restored.inquire('answer',SCOPE)['status']=='completed'
    assert restored.inquire('new',SCOPE)['status']=='unavailable'
    monkeypatch.setattr(restored,'_db',lambda: (_ for _ in ()).throw(sqlite3.OperationalError('offline')))
    assert restored.inquire('answer',SCOPE)=={'status':'unavailable','reason':'storage_unavailable'}


def test_crashed_owner_lease_recovers_without_duplicate_live_claim(tmp_path):
    now=[1000.]
    s=make(tmp_path,clock=lambda:now[0],lease_seconds=2)
    class Live:
        def is_alive(self): return True
    s._thread=Live()
    s.submit(question='crashed host',access_scope=SCOPE)
    first=s.claim(_dispatch=True)
    now[0]+=3
    assert s.claim() is None  # PID still owns an underlying operation
    with s._db() as db:
        # A reused PID with a different process start identity is not ownership.
        db.execute("UPDATE execution_slots SET owner_start='different-process'")
    restored=make(tmp_path,clock=lambda:now[0],lease_seconds=2)
    recovered=restored.claim()
    assert recovered.job_id==first.job_id and recovered.attempt==2
    assert not s.finish(first.job_id,first.attempt,{'status':'completed','answer':'late'})


def test_freshness_callback_outside_transaction_and_generation_cas(tmp_path):
    now=[1000.]
    s=make(tmp_path,clock=lambda:now[0])
    class Live:
        def is_alive(self): return True
    s._thread=Live()
    s.inquire('cas',SCOPE,foreground_timeout=0)
    first=s.claim()
    s.finish(first.job_id,first.attempt,{'status':'completed','answer':'old'})
    with s._db() as db: db.execute('DELETE FROM execution_slots')
    entered,release=threading.Event(),threading.Event()
    def freshness(job):
        # A separate writer can proceed while freshness is evaluated.
        assert s.lookup(job['job_id'],access_scope=SCOPE)['status']=='ok'
        entered.set();release.wait(5)
        return {'fresh':False,'changed_sources':['source']}
    s.freshness_provider=freshness
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(s.inquire,'cas',SCOPE,foreground_timeout=0)
        assert entered.wait(2)
        with s._db() as db:
            db.execute('BEGIN IMMEDIATE')
            job=json.loads(db.execute('SELECT record FROM jobs WHERE id=?',(first.job_id,)).fetchone()[0])
            job['answer']='concurrent corrected answer'
            db.execute('UPDATE jobs SET record=? WHERE id=?',(json.dumps(job),first.job_id))
        release.set()
        assert pending.result(timeout=2)['status']=='thinking'
    with s._db() as db: assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1


@pytest.mark.parametrize('changed_sources', [
    [{'reference':'pkt_unverified', 'reason':'stale',
      'dependencies':[{'dependency':'source', 'reason':'unverified'}]}],
    [{'reason':'dependency_manifest_missing'}],
], ids=['nested_unverified', 'missing_manifest'])
def test_unverifiable_terminal_freshness_does_not_start_identical_refresh(tmp_path, changed_sources):
    calls=[]
    def factory(service, job):
        class Worker:
            def run(self, request):
                calls.append(request)
                return {'status':'completed','answer':'retained answer'}
        return Worker()
    s=make(tmp_path,worker_factory=factory,freshness_provider=lambda job:{
        'fresh':False,'changed_sources':changed_sources}).start()
    try:
        assert s.inquire('same evidence question',SCOPE,foreground_timeout=2)['status']=='completed'
        retry=s.inquire('same evidence question',SCOPE,foreground_timeout=0)
        assert retry=={'status':'unavailable','reason':'freshness_unverifiable'}
        with s._db() as db:
            assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
        assert len(calls)==1
    finally:s.shutdown()


def test_zero_ttl_command_history_is_delivered_as_partial_without_mutating_cache(tmp_path):
    calls=[]
    def factory(service, job):
        class Worker:
            def run(self, request):
                calls.append(request)
                service.observe(job.job_id, job.attempt, {'status':'completed','exit_code':0,
                    'stdout':'historical command output','truncated':False,'timed_out':False})
                return {'status':'completed','answer':'answer supported by the command output'}
        return Worker()
    def freshness(job):
        return {'fresh':False,'changed_sources':[
            {'reference':job['evidence_packets'][0], 'reason':'stale',
             'dependencies':[{'reason':'volatile_observation_expired'}]},
            {'reason':'dependency_manifest_missing'}]}
    s=make(tmp_path,worker_factory=factory,freshness_provider=freshness).start()
    try:
        first=s.inquire('historical command question',SCOPE,foreground_timeout=2)
        assert first['status']=='completed'
        with s._db() as db:
            before=db.execute('SELECT record,observations FROM jobs').fetchone()
            original_record,original_observations=before['record'],before['observations']
        retry=s.inquire('historical command question',SCOPE,foreground_timeout=0)
        assert retry['status']=='partial'
        assert retry['job']['answer']=='answer supported by the command output'
        assert retry['job']['status']=='partial'
        assert any('freshness is not verified' in note and 'new question or add context' in note
                   for note in retry['job']['unresolved_questions'])
        assert retry['observations'][0]['stdout']=='historical command output'
        with s._db() as db:
            after=db.execute('SELECT record,observations FROM jobs').fetchone()
            assert (after['record'],after['observations'])==(original_record,original_observations)
            assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
        second=s.inquire('historical command question',SCOPE,foreground_timeout=0)
        assert second['status']=='partial' and second['job']['job_id']==retry['job']['job_id']
        assert len(calls)==1
    finally:s.shutdown()


@pytest.mark.parametrize('guard', [
    {'reason':'changed','path':'/repo/source.py'},
    {'reason':'selection_proof_missing','skill':'example','resource':'SKILL.md'},
    {'reason':'unverified','path':'/repo/source.py'},
], ids=['changed_hash','selection_proof_missing','unverified_source_read'])
def test_historical_zero_ttl_delivery_rejects_source_and_selection_failures(tmp_path, guard):
    s=make(tmp_path)
    packet=s.packets.create(tool='command',access_scope=SCOPE,payload={
        'status':'completed','exit_code':0,'stdout':'historical','truncated':False,'timed_out':False},
        freshness={'volatile':True,'max_age_seconds':0})
    freshness={'fresh':False,'changed_sources':[
        {'reference':packet.packet_id,'reason':'stale',
         'dependencies':[{'reason':'volatile_observation_expired'}]},guard]}
    try:
        assert not s._historical_zero_ttl_delivery(freshness,SimpleNamespace(mode='investigate'),SCOPE)
        unverified_packet=s.packets.create(tool='command',access_scope=SCOPE,payload={
            'status':'completed','exit_code':0,'source_reads':[{'method':'unknown'}]},
            freshness={'volatile':True,'max_age_seconds':0})
        source_read_only={'fresh':False,'changed_sources':[
            {'reference':unverified_packet.packet_id,'reason':'stale',
             'dependencies':[{'reason':'volatile_observation_expired'}]}]}
        assert not s._historical_zero_ttl_delivery(source_read_only,SimpleNamespace(mode='investigate'),SCOPE)
    finally:s.shutdown()


@pytest.mark.parametrize("cleanup", ["exception", "unreleased"])
def test_cleanup_failure_keeps_durable_capacity_after_owner_death(tmp_path, cleanup):
    now=[1000.]
    closes=[]
    class Backend:
        def open_sessions(self,count,**kwargs): return {'status':'available','session_ids':['session']}
        def close_session(self,session):
            closes.append(session)
            if cleanup == 'unreleased':
                return {'released': False}
            raise RuntimeError('model_process_not_quiescent')
    def factory(service,job):
        class Worker:
            def run(self,request):return {'status':'completed','answer':'computed'}
        return Worker()
    s=make(tmp_path,worker_factory=factory,backend=Backend(),clock=lambda:now[0]).start()
    try:
        s.inquire('close one',SCOPE,foreground_timeout=0)
        s.inquire('close two',SCOPE,foreground_timeout=0)
        wait(lambda:len(closes)==2)
        def failed_slots():
            with s._db() as db:return db.execute('SELECT count(*) FROM execution_slots WHERE cleanup_failed=1').fetchone()[0]==2
        wait(failed_slots)
        now[0]+=1000
        with s._db() as db:db.execute("UPDATE execution_slots SET owner_start='dead-owner'")
        s.inquire('must wait',SCOPE,foreground_timeout=0)
        time.sleep(.15)
        assert len(closes)==2
        with s._db() as db:assert db.execute('SELECT count(*) FROM execution_slots').fetchone()[0]==2
    finally:s.shutdown()


@pytest.mark.parametrize('stored_status', ['failed', 'partial'])
def test_empty_terminal_negative_cache_never_restarts_or_checks_freshness(tmp_path, stored_status):
    opened=[]
    freshness=[]
    class Backend:
        def open_sessions(self,count,**kwargs):
            opened.append(kwargs)
            return {'status':'unavailable','reason':'gpu_role_unavailable'}
    def factory(service,job):
        raise AssertionError('unavailable sessions must not start an observer')
    def check(job):
        freshness.append(job)
        return {'fresh':False,'reason':'manifest_missing'}
    s=make(tmp_path,worker_factory=factory,backend=Backend(),freshness_provider=check,retry_seconds=0).start()
    try:
        result=s.inquire('literal backend failure',SCOPE,request_id='same',foreground_timeout=2)
        assert result=={'status':'unavailable','reason':'analysis_unavailable'}
        with s._db() as db:
            row=db.execute('SELECT * FROM jobs').fetchone()
            job=json.loads(row['record'])
        wait(lambda:s._settled(job['job_id']))
        assert len(opened)==3 and job['attempt']==3
        assert job['status']=='failed' and not job['answer'] and not job['findings'] and not job['evidence_packets']
        assert job['failure_reason']=='gpu_role_unavailable'
        assert job['terminal_reason']=='attempt_or_deadline_exhausted'
        packet=s.packets.lookup(job['result_packet'],access_scope=SCOPE).packet
        assert packet.payload['reason']=='attempt_or_deadline_exhausted'
        assert packet.payload['failure_reason']=='gpu_role_unavailable'
        # Historical empty partials produced before this fix also stay negative.
        with s._db() as db:
            job['status']=stored_status
            db.execute('UPDATE jobs SET record=? WHERE id=?',(json.dumps(job),job['job_id']))
            before=[tuple(r) for r in db.execute('SELECT * FROM jobs')]
            index=[tuple(r) for r in db.execute('SELECT * FROM inquiry_index')]
        for n in range(8):
            assert s.inquire('literal backend failure',SCOPE,hints=['missing'],request_id=str(n),
                foreground_timeout=30)=={'status':'unavailable','reason':'analysis_unavailable'}
        with s._db() as db:
            assert [tuple(r) for r in db.execute('SELECT * FROM jobs')]==before
            assert [tuple(r) for r in db.execute('SELECT * FROM inquiry_index')]==index
        assert len(opened)==3 and not freshness
        assert not s.log(access_scope=SCOPE)
    finally:s.shutdown()


def test_private_failure_diagnostics_are_bounded_safe_and_restart_durable(tmp_path):
    reasons = {
        'context target': 'context_budget',
        'finding target': 'finding_requires_observed_packet',
        'json target': 'json.loads_extra_data',
        'legacy json target': 'Extra data: line 1 column 24 (char 23)',
        'deadline target': 'attempt_or_deadline_exhausted',
        'unknown target': 'private prompt fragment: user supplied text',
    }
    def factory(service, job):
        class Worker:
            def run(self, request):
                return {'status': 'failed', 'reason': reasons[job.question]}
        return Worker()

    s = make(tmp_path, worker_factory=factory).start()
    ids = {}
    try:
        for question in reasons:
            assert s.inquire(question, SCOPE, foreground_timeout=2) == {
                'status': 'unavailable', 'reason': 'analysis_unavailable'}
            with s._db() as db:
                row = db.execute("SELECT id FROM jobs WHERE json_extract(record,'$.question')=?", (question,)).fetchone()
            ids[question] = row['id']
        diagnostics = s.inquiry_failure_diagnostics(limit=50)
        by_id = {row['job_id']: row for row in diagnostics}
        assert by_id[ids['context target']]['failure_class'] == 'context_budget'
        assert by_id[ids['finding target']]['failure_class'] == 'finding_requires_observed_packet'
        assert by_id[ids['json target']]['failure_class'] == 'model_output_invalid_json'
        assert by_id[ids['legacy json target']]['failure_class'] == 'model_output_invalid_json'
        assert by_id[ids['deadline target']]['failure_class'] == 'attempt_or_deadline_exhausted'
        assert by_id[ids['unknown target']]['failure_class'] == 'worker_failure'
        assert all(set(row) == {'job_id', 'mode', 'status', 'failure_class'} for row in diagnostics)
        assert all(row['mode'] == 'investigate' and row['status'] == 'failed' for row in diagnostics)
        assert len(s.inquiry_failure_diagnostics(limit=2)) <= 2
        with pytest.raises(ValueError):
            s.inquiry_failure_diagnostics(limit=51)
        assert 'private prompt fragment' not in json.dumps(diagnostics)
        assert all(question not in json.dumps(diagnostics) for question in reasons)
    finally:
        s.shutdown()

    reopened = make(tmp_path)
    diagnostics = reopened.inquiry_failure_diagnostics(limit=50)
    by_id = {row['job_id']: row for row in diagnostics}
    assert by_id[ids['unknown target']]['failure_class'] == 'worker_failure'
    assert 'private prompt fragment' not in json.dumps(diagnostics)


def test_failure_diagnostics_limit_counts_only_negative_terminal_jobs(tmp_path):
    def factory(service, job):
        class Worker:
            def run(self, request):
                if job.question == 'supported partial':
                    return {'status': 'partial', 'answer': 'supported partial answer'}
                if job.question == 'completed with evidence only':
                    service.observe(job.job_id, job.attempt, {'text': 'observed evidence, no answer'})
                    return {'status': 'completed'}
                if job.question == 'whitespace answer':
                    return {'status': 'completed', 'answer': '\n\t\u2003'}
                return {'status': 'failed', 'reason': 'final_round_requires_answer'}
        return Worker()

    s = make(tmp_path, worker_factory=factory).start()
    try:
        assert s.inquire('negative target', SCOPE, foreground_timeout=2)['status'] == 'unavailable'
        assert s.inquire('completed with evidence only', SCOPE, foreground_timeout=2)['status'] == 'unavailable'
        assert s.inquire('whitespace answer', SCOPE, foreground_timeout=2)['status'] == 'unavailable'
        assert s.inquire('supported partial', SCOPE, foreground_timeout=2)['status'] == 'partial'
        with s._db() as db:
            db.execute("UPDATE jobs SET updated=1 WHERE json_extract(record,'$.question')='negative target'")
            db.execute("UPDATE jobs SET updated=3 WHERE json_extract(record,'$.question')='completed with evidence only'")
            db.execute("UPDATE jobs SET updated=2 WHERE json_extract(record,'$.question')='whitespace answer'")
            db.execute("UPDATE jobs SET updated=4 WHERE json_extract(record,'$.question')='supported partial'")
        rows = s.inquiry_failure_diagnostics(limit=1)
        assert len(rows) == 1
        assert rows[0]['failure_class'] == 'worker_failure'
        with s._db() as db:
            negative_id = db.execute("SELECT id FROM jobs WHERE json_extract(record,'$.question')='negative target'").fetchone()[0]
            evidence_only_id = db.execute("SELECT id FROM jobs WHERE json_extract(record,'$.question')='completed with evidence only'").fetchone()[0]
            whitespace_id = db.execute("SELECT id FROM jobs WHERE json_extract(record,'$.question')='whitespace answer'").fetchone()[0]
        ids = {row['job_id'] for row in s.inquiry_failure_diagnostics(limit=50)}
        assert {negative_id, evidence_only_id, whitespace_id} <= ids
        assert rows[0]['job_id'] == evidence_only_id
        by_id = {row['job_id']: row for row in s.inquiry_failure_diagnostics(limit=50)}
        assert by_id[negative_id]['failure_class'] == 'final_answer_missing'
    finally:
        s.shutdown()


def test_real_worker_port_repairs_json_and_citations_then_assembles_result(tmp_path):
    dispatches = []

    class Backend:
        def __init__(self):
            self.requests = []
            self.observation_id = None

        def run_observer_turn(self, request):
            self.requests.append(request)
            turn = len(self.requests)
            if turn == 1:
                # The first valid-looking tool call must be rejected with its
                # trailing object; no tool is dispatched from a JSON prefix.
                return {'status': 'available', 'text': '{"tool":"search","arguments":{"query":"README"}} {}'}
            if turn == 2:
                feedback = [json.loads(message['content']) for message in request['messages']
                    if message['role'] == 'user' and 'protocol_error' in message['content']]
                assert feedback and feedback[-1]['protocol_error'] == 'invalid_json_object'
                assert dispatches == []
                return {'status': 'available', 'text': json.dumps({'tool': 'search', 'arguments': {'query': 'README'}})}
            if turn == 3:
                for index, message in enumerate(request['messages'][:-1]):
                    if message['role'] == 'assistant' and json.loads(message['content']).get('tool') == 'search':
                        self.observation_id = json.loads(request['messages'][index + 1]['content'])['packet_id']
                        break
                assert self.observation_id
                # This nested source/tool ID is deliberately not the broker's
                # outer observation packet ID and must fail citation validation.
                invalid = {'answer': 'A draft answer.', 'findings': [
                    {'text': 'Observed fact.', 'evidence_packets': ['nested-packet-id']}],
                    'unresolved_questions': []}
                return {'status': 'available', 'text': json.dumps(invalid)}
            assert turn == 4
            retained = [json.loads(message['content']) for message in request['messages']
                if message['role'] == 'user' and 'retained_observation' in message['content']]
            assert any(item['retained_observation'].get('reason') == 'investigate_final_validation_failed'
                and item['retained_observation'].get('failure_classification') == 'finding_requires_observed_packet'
                for item in retained)
            valid = {'answer': 'The README was observed.', 'findings': [
                {'text': 'The README supports this answer.', 'evidence_packets': [self.observation_id]}],
                'unresolved_questions': []}
            return {'status': 'available', 'text': json.dumps(valid)}

    backend = Backend()
    worker_factory = trusted_worker(tmp_path, backend, dispatches)
    s = make(tmp_path, worker_factory=worker_factory).start()
    try:
        value = s.inquire('What does the README say?', SCOPE, foreground_timeout=3)
        assert value['status'] == 'completed'
        assert len(backend.requests) == 4
        assert [name for name, _, _ in dispatches] == ['search']
        job = value['job']
        assert job['answer'] == 'The README was observed.'
        assert job['result_packet']
        result = s.packets.lookup(job['result_packet'], access_scope=SCOPE)
        assert result.status == 'ok'
        assert result.packet.payload['answer'] == job['answer']
        public = public_inquiry({**result.packet.payload, 'status': value['status'],
            'packet_id': result.packet.packet_id, 'alias': result.packet.alias,
            'evidence_packets': job['evidence_packets'],
            'sources': [source.model_dump(exclude_none=True) for source in result.packet.sources]})
        assert public['status'] == 'completed' and public['answer'] == job['answer']
        assert public['findings'][0]['evidence_packets'] == [backend.observation_id]
        assert not {'job', 'job_id', 'observations', 'attempt'} & public.keys()
    finally:
        s.shutdown()


def test_real_worker_port_unrepairable_json_becomes_negative_cache(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class Backend:
        def __init__(self):
            self.calls = 0
        def run_observer_turn(self, request):
            self.calls += 1
            entered.set()
            release.wait(5)
            return {'status': 'available', 'text': '{"answer":"unterminated"'}

    backend = Backend()
    worker_factory = trusted_worker(tmp_path, backend, [])
    s = make(tmp_path, worker_factory=worker_factory, retry_seconds=0).start()
    try:
        assert s.inquire('unrepairable JSON question', SCOPE, foreground_timeout=0)['status'] == 'thinking'
        assert entered.wait(2)
        release.set()

        def terminal_job():
            with s._db() as db:
                row = db.execute("SELECT record FROM jobs WHERE json_extract(record,'$.question')=?",
                    ('unrepairable JSON question',)).fetchone()
            if not row:
                return None
            job = json.loads(row['record'])
            return job if job['status'] in {'failed', 'partial', 'completed'} and s._settled(job['job_id']) else None

        job = wait(terminal_job)
        assert job['status'] == 'failed'
        assert job['failure_reason'] == 'model_output_invalid_json'
        assert s.inquire('unrepairable JSON question', SCOPE) == {
            'status': 'unavailable', 'reason': 'analysis_unavailable'}
        calls = backend.calls
        time.sleep(.05)
        assert s.inquire('unrepairable JSON question', SCOPE) == {
            'status': 'unavailable', 'reason': 'analysis_unavailable'}
        assert backend.calls == calls and calls == 6
        diagnostic = s.inquiry_failure_diagnostics(limit=50)
        assert any(row['job_id'] == job['job_id'] and row['failure_class'] == 'model_output_invalid_json'
            for row in diagnostic)
    finally:
        release.set()
        s.shutdown()


def test_resumed_worker_projects_large_raw_observations_without_losing_evidence(tmp_path):
    first_entered, first_release = threading.Event(), threading.Event()
    second_entered, second_release = threading.Event(), threading.Event()
    dispatches, worker_requests, small_ids = [], [], []

    class Backend:
        def __init__(self):
            self.opens = 0
            self.sessions = {}
            self.model_requests = []
            self.large_id = None

        def open_sessions(self, count, **policy):
            self.opens += 1
            session = f'resume-{self.opens}'
            self.sessions[session] = self.opens
            if self.opens == 1:
                first_entered.set()
                first_release.wait(5)
                return {'status': 'available', 'session_ids': [session]}
            if self.opens == 2:
                second_entered.set()
                second_release.wait(5)
                return {'status': 'available', 'session_ids': [session]}
            raise AssertionError('unexpected inference retry')

        def close_session(self, session):
            return {'released': True}

        def preemption_status(self, session):
            return {'preempt_requested': self.sessions[session] == 1}

        def run_observer_turn(self, request):
            self.model_requests.append(request)
            assert len(request['messages']) <= 24
            turn_bytes = len(json.dumps(request, ensure_ascii=False).encode())
            assert 90000 < turn_bytes <= 1024 * 1024
            if len(self.model_requests) == 1:
                observations = []
                for message in request['messages']:
                    if message['role'] != 'user':
                        continue
                    try:
                        payload = json.loads(message['content'])
                    except ValueError:
                        continue
                    observation = payload.get('retained_observation', payload)
                    if isinstance(observation, dict) and observation.get('packet_id'):
                        observations.append(observation)
                marker = next(o for o in observations if o.get('packet_id') == self.large_id)
                assert marker == {'packet_id': self.large_id,
                    'omissions': ['tool payload exceeded worker context budget']}
                # The expanded turn envelope retains the complete projected
                # evidence bodies above the former 90 KB ceiling. Their public
                # broker IDs remain the citation IDs; the oversized frame stays
                # explicitly omitted and cannot be cited.
                retained_small = [o for o in observations if o.get('packet_id') in set(small_ids)]
                assert retained_small
                assert all(o.get('text') == 'x'*13000 for o in retained_small)
                progress = next(payload['progress'] for message in request['messages'] if message['role'] == 'user'
                    for payload in [json.loads(message['content'])]
                    if isinstance(payload, dict) and 'progress' in payload
                    and 'allowed_observation_packet_ids' in payload['progress'])
                assert self.large_id not in progress['allowed_observation_packet_ids']
                assert set(o['packet_id'] for o in retained_small) <= set(progress['allowed_observation_packet_ids'])
                assert progress['input_omitted_observation_packet_ids'] == worker_requests[1][
                    'omitted_observation_packet_ids']
                return {'status': 'available', 'text': json.dumps({'answer': 'Draft.', 'findings': [
                    {'text': 'This omitted body supports the claim.', 'evidence_packets': [self.large_id]}],
                    'unresolved_questions': []})}
            assert len(self.model_requests) == 2
            correction = [json.loads(message['content']) for message in request['messages']
                if message['role'] == 'user' and 'retained_observation' in message['content']]
            assert any(item['retained_observation'].get('failure_classification') == 'finding_requires_observed_packet'
                for item in correction)
            progress = next(payload['progress'] for message in request['messages'] if message['role'] == 'user'
                for payload in [json.loads(message['content'])]
                if isinstance(payload, dict) and 'progress' in payload
                and 'allowed_observation_packet_ids' in payload['progress'])
            allowed = progress['allowed_observation_packet_ids']
            assert allowed and self.large_id not in allowed
            return {'status': 'available', 'text': json.dumps({'answer': 'Retained source evidence supports this answer.',
                'findings': [{'text': 'A retained observation supports this answer.',
                    'evidence_packets': [allowed[-1]]}], 'unresolved_questions': []})}

    backend = Backend()
    base_factory = trusted_worker(tmp_path, backend, dispatches)
    def capture_factory(service, job):
        worker = base_factory(service, job)
        run = worker.run
        def captured(request):
            worker_requests.append(request)
            return run(request)
        worker.run = captured
        return worker

    s = make(tmp_path, worker_factory=capture_factory, backend=backend, retry_seconds=0).start()
    try:
        assert s.inquire('Resume with accumulated evidence', SCOPE, foreground_timeout=0)['status'] == 'thinking'
        assert first_entered.wait(2)
        with s._db() as db:
            row = db.execute("SELECT id,record FROM jobs WHERE json_extract(record,'$.question')=?",
                ('Resume with accumulated evidence',)).fetchone()
        job = json.loads(row['record'])
        seed = s.observe(row['id'], job['attempt'], {'text': 'small preemption checkpoint'})
        seed_frame = s.lookup(row['id'], access_scope=SCOPE)['observations'][0]
        s.checkpoint(row['id'], job['attempt'], [{**seed_frame,
            'public_tool_call': {'tool': 'search', 'arguments': {'query': 'retained'}}}], access_scope=SCOPE)
        first_release.set()
        assert second_entered.wait(5)
        with s._db() as db:
            current = json.loads(db.execute('SELECT record FROM jobs WHERE id=?', (row['id'],)).fetchone()[0])
        assert current['attempt'] == 2
        small_ids = [s.observe(row['id'], current['attempt'], {'text': 'x'*13000, 'index': n})
            for n in range(20)]
        current_observations = s.lookup(row['id'], access_scope=SCOPE)['observations']
        checkpoint_subset = [current_observations[-2], current_observations[-1]]
        s.checkpoint(row['id'], current['attempt'], [
            {**checkpoint_subset[0], 'public_tool_call': {'tool': 'search', 'arguments': {'query': 'recent one'}}},
            {**checkpoint_subset[1], 'public_tool_call': {'tool': 'search', 'arguments': {'query': 'recent two'}}},
        ], access_scope=SCOPE)
        fake_call = {'tool': 'overview', 'arguments': {}}
        fake_id = s.observe(row['id'], current['attempt'], {'text': 'raw packet payload',
            'public_tool_call': fake_call})
        backend.large_id = s.observe(row['id'], current['attempt'], {'text': 'z'*40000, 'source_reads': [
            {'path': 'README.md', 'content_sha256': 'a'*64}]})
        second_release.set()

        result = wait(lambda: (v if (v := s.lookup(row['id'], access_scope=SCOPE))['job']['status']
            in {'completed', 'partial', 'failed'} else None))
        assert result['job']['status'] == 'completed', ({key: result['job'].get(key) for key in
            ('status', 'failure_reason', 'terminal_reason', 'answer', 'unresolved_questions')},
            s.last_error, len(worker_requests), len(backend.model_requests), backend.opens)
        assert result['job']['attempt'] == 2
        assert len(worker_requests) == 2
        projected = worker_requests[1]
        assert len(json.dumps(projected, ensure_ascii=False).encode()) <= WORKER_JOB_INPUT_MAX_BYTES
        assert all(len(json.dumps(observation, ensure_ascii=False).encode()) <= WORKER_OBSERVATION_MAX_BYTES
            for observation in projected['observations'])
        assert len(projected['observations']) < 22
        all_ids = [seed, *small_ids, fake_id, backend.large_id]
        selected_ids = [frame['packet_id'] for frame in projected['observations']]
        assert projected['omitted_observation_packet_ids'] == all_ids[:len(all_ids)-len(selected_ids)]
        assert selected_ids == all_ids[len(projected['omitted_observation_packet_ids']):]
        assert projected['observations'][-1] == {'packet_id': backend.large_id,
            'omissions': ['tool payload exceeded worker context budget']}
        projected_by_id = {frame['packet_id']: frame for frame in projected['observations']}
        assert fake_id in projected_by_id and 'public_tool_call' not in projected_by_id[fake_id]
        assert projected_by_id[checkpoint_subset[0]['packet_id']]['public_tool_call'] == {
            'tool': 'search', 'arguments': {'query': 'recent one'}}
        assert projected_by_id[checkpoint_subset[1]['packet_id']]['public_tool_call'] == {
            'tool': 'search', 'arguments': {'query': 'recent two'}}
        assert seed in {frame['packet_id'] for frame in s.lookup(row['id'], access_scope=SCOPE)['observations']}
        assert len(backend.model_requests) == 2 and not dispatches
        assert len(small_ids) == 20

        with s._db() as db:
            raw = json.loads(db.execute('SELECT observations FROM jobs WHERE id=?', (row['id'],)).fetchone()[0])
        assert len(raw) == 24
        large_raw = next(observation for observation in raw if observation['packet_id'] == backend.large_id)
        assert large_raw['text'] == 'z'*40000 and len(json.dumps(large_raw).encode()) > WORKER_OBSERVATION_MAX_BYTES
        stored_fake = next(observation for observation in raw if observation['packet_id'] == fake_id)
        assert stored_fake['public_tool_call'] == fake_call
        packet = s.packets.lookup(backend.large_id, access_scope=SCOPE)
        assert packet.status == 'ok' and packet.packet.payload['text'] == 'z'*40000
        assert result['job']['findings'][0]['evidence_packets'][-1] != backend.large_id
    finally:
        first_release.set()
        second_release.set()
        s.shutdown()


def test_oversized_trusted_worker_base_fails_without_model_dispatch(tmp_path):
    class Backend:
        def __init__(self):
            self.turns = 0
        def open_sessions(self, count, **policy):
            return {'status': 'available', 'session_ids': ['oversized-base']}
        def close_session(self, session):
            return {'released': True}
        def run_observer_turn(self, request):
            self.turns += 1
            raise AssertionError('oversized trusted base reached inference')

    backend = Backend()
    def unavailable_factory(service, job):
        raise AssertionError('oversized trusted base constructed a worker')
    s = make(tmp_path, worker_factory=unavailable_factory, backend=backend,
        inquiry_context_provider=lambda job: {'inquiry_repositories': [
            {'project': 'pc', 'repository': 'pc', 'root': 'x'*(WORKER_JOB_INPUT_MAX_BYTES+1)}]}).start()
    try:
        assert s.inquire('large trusted context', SCOPE, foreground_timeout=2) == {
            'status': 'unavailable', 'reason': 'analysis_unavailable'}
        with s._db() as db:
            row = db.execute("SELECT id,record FROM jobs WHERE json_extract(record,'$.question')=?",
                ('large trusted context',)).fetchone()
        job = json.loads(row['record'])
        assert job['failure_reason'] == 'job_input_budget_exhausted'
        assert backend.turns == 0
        assert s.inquiry_failure_diagnostics(limit=50) == [{'job_id': row['id'], 'mode': 'investigate',
            'status': 'failed', 'failure_class': 'job_input_budget_exhausted'}]
    finally:
        s.shutdown()


def test_operator_releases_only_selected_matching_negative_inquiries(tmp_path):
    pending_entered, pending_release = threading.Event(), threading.Event()
    calls = {}
    def factory(service, job):
        class Worker:
            def run(self, request):
                count = calls.get(job.question, 0) + 1
                calls[job.question] = count
                if job.question == 'pending repair target':
                    pending_entered.set()
                    pending_release.wait(5)
                    return {'status': 'completed', 'answer': 'pending answer'}
                if job.question == 'parse repair target' and count == 1:
                    return {'status': 'failed', 'reason': 'json.loads_extra_data'}
                if job.question == 'changed reason target':
                    return {'status': 'failed', 'reason': 'other_failure'}
                return {'status': 'completed', 'answer': 'answer ' + job.question}
        return Worker()

    s = make(tmp_path, worker_factory=factory).start()
    try:
        assert s.inquire('parse repair target', SCOPE, foreground_timeout=2)['reason'] == 'analysis_unavailable'
        assert s.inquire('supported repair target', SCOPE, foreground_timeout=2)['status'] == 'completed'
        assert s.inquire('pending repair target', SCOPE, foreground_timeout=0)['status'] == 'thinking'
        assert pending_entered.wait(2)
        assert s.inquire('changed reason target', SCOPE, foreground_timeout=2)['reason'] == 'analysis_unavailable'

        def indexed_jobs():
            with s._db() as db:
                return {json.loads(row['record'])['question']: row['id']
                        for row in db.execute('''SELECT jobs.id,jobs.record FROM jobs
                            JOIN inquiry_index ON inquiry_index.job=jobs.id''')}
        ids = wait(lambda: (found if len(found := indexed_jobs()) == 4 else None))
        old_id = ids['parse repair target']
        def outbox_settled():
            with s._db() as db:
                return not db.execute('SELECT 1 FROM outbox WHERE materialized=0').fetchone()
        wait(outbox_settled)
        with s._db() as db:
            before_jobs = [tuple(row) for row in db.execute('SELECT * FROM jobs ORDER BY id')]
            before_packets = [tuple(row) for row in db.execute('SELECT * FROM outbox ORDER BY id')]

        recovered = s.release_failed_inquiries({
            old_id: 'json.loads_extra_data',
            ids['supported repair target']: 'json.loads_extra_data',
            ids['pending repair target']: 'json.loads_extra_data',
            ids['changed reason target']: 'json.loads_extra_data',
        })
        assert recovered == [old_id]
        assert old_id not in {row['job_id'] for row in s.inquiry_failure_diagnostics(limit=50)}
        with s._db() as db:
            assert [tuple(row) for row in db.execute('SELECT * FROM jobs ORDER BY id')] == before_jobs
            assert [tuple(row) for row in db.execute('SELECT * FROM outbox ORDER BY id')] == before_packets
            assert s.lookup(old_id, access_scope=SCOPE)['status'] == 'ok'
            retained = {row[0] for row in db.execute('SELECT job FROM inquiry_index')}
        assert retained == {ids['supported repair target'], ids['pending repair target'], ids['changed reason target']}
        assert s.lookup(old_id, access_scope=SCOPE)['status'] == 'ok'

        pending_release.set()
        wait(lambda: s._settled(ids['pending repair target']))
        retried = s.inquire('parse repair target', SCOPE, foreground_timeout=2)
        assert retried['status'] == 'completed'
        assert retried['job']['job_id'] != old_id
        assert s.lookup(old_id, access_scope=SCOPE)['status'] == 'ok'
    finally:
        pending_release.set()
        s.shutdown()


def test_dispatcher_parks_queued_jobs_when_central_identity_fails(tmp_path):
    class Backend:
        def __init__(self):
            self.allowed = True
            self.mismatch_after_first_check = True
            self.checks = 0
        def central_status(self):
            self.checks += 1
            if not self.allowed or (self.mismatch_after_first_check and self.checks >= 2):
                raise RuntimeError('runtime_identity_mismatch')
            return {'observer_contract': 'PC-OBSERVER-SUPERVISOR/1'}
        def open_sessions(self, count, **kwargs):
            return {'status': 'available', 'session_ids': ['session']}
        def close_session(self, session):
            return {'released': True}

    backend = Backend()
    def factory(service, job):
        class Worker:
            def run(self, request):
                return {'status': 'completed', 'answer': 'recovered ' + job.question}
        return Worker()

    s = make(tmp_path, worker_factory=factory, backend=backend).start()
    try:
        assert s.inquire('identity mismatch must park', SCOPE, foreground_timeout=0)['status'] == 'thinking'
        def queued_id():
            with s._db() as db:
                row = db.execute('SELECT job FROM inquiry_index').fetchone()
                return row[0] if row else None
        job_id = wait(queued_id)
        wait(lambda: s.last_error == 'runtime_identity_mismatch')
        with s._db() as db:
            job = json.loads(db.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()[0])
            assert db.execute('SELECT count(*) FROM execution_slots').fetchone()[0] == 0
        assert job['status'] == 'queued' and job['attempt'] == 0
        assert backend.checks >= 2  # a fresh claim guard rejects the formerly valid status

        backend.allowed = True
        backend.mismatch_after_first_check = False
        completed = wait(lambda: (value if (value := s.lookup(job_id, access_scope=SCOPE))['job']['status'] == 'completed' else None))
        assert completed['job']['attempt'] == 1
        assert s.last_error is None
    finally:
        s.shutdown()


def test_preflight_covers_expired_incompatible_jobs_and_pending_outbox(tmp_path):
    class Backend:
        def central_status(self):
            raise RuntimeError('runtime_identity_mismatch')
    def factory(service, job):
        raise AssertionError('mismatched identity must prevent worker startup')
    s = make(tmp_path, backend=Backend(), worker_factory=factory,
        can_execute=lambda job, shared: False)
    class Live:
        def is_alive(self): return True
    s._thread = Live()
    ident = s.submit(question='expired and incompatible', access_scope=SCOPE, _identity='identity')['job_id']
    with s._db() as db:
        record = json.loads(db.execute('SELECT record FROM jobs WHERE id=?', (ident,)).fetchone()[0])
        record['deadline_epoch'] = time.time() - 10
        db.execute('UPDATE jobs SET record=? WHERE id=?', (json.dumps(record), ident))
        db.execute('INSERT INTO outbox(id,job,packet,materialized) VALUES(?,?,?,0)', ('pending-packet', ident, '{}'))

    s._thread = None
    s.start()
    try:
        wait(lambda: s.last_error == 'runtime_identity_mismatch')
        with s._db() as db:
            current = json.loads(db.execute('SELECT record FROM jobs WHERE id=?', (ident,)).fetchone()[0])
            assert db.execute('SELECT materialized FROM outbox WHERE id=?', ('pending-packet',)).fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM execution_slots').fetchone()[0] == 0
        assert current['status'] == 'queued' and current['attempt'] == 0
    finally:
        s.shutdown()


def test_legacy_snapshot_is_quiescent_one_time_and_isolated(tmp_path):
    legacy_dir, v2_dir = tmp_path/'as1'/'jobs', tmp_path/'as1'/'jobs-v2'
    packet_dir = tmp_path/'as1'/'packets'
    entered, release = threading.Event(), threading.Event()
    def factory(service, job):
        class Worker:
            def run(self, request):
                if job.question == 'new namespace only':
                    entered.set()
                    release.wait(5)
                if job.question == 'legacy answer':
                    service.observe(job.job_id, job.attempt, {'text': 'retained legacy observation'})
                return {'status': 'completed', 'answer': 'answer ' + job.question}
        return Worker()

    old = JobService(legacy_dir, packets=SQLitePacketStore(packet_dir), worker_factory=factory,
        freshness_provider=lambda job: {'fresh': True}).start()
    try:
        result = old.inquire('legacy answer', SCOPE, foreground_timeout=2)
        assert result['status'] == 'completed'
        old_id = result['job']['job_id']
        wait(lambda: old._settled(old_id))
        with old._db() as db:
            db.execute('INSERT INTO job_checkpoints(job,scope,attempt,frames) VALUES(?,?,?,?)',
                (old_id, json.dumps(SCOPE, sort_keys=True, separators=(',', ':')), 1, '[]'))
            before = {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                      for table in ('jobs', 'outbox', 'job_checkpoints', 'inquiry_index')}
    finally:
        old.shutdown()

    new = JobService(v2_dir, packets=SQLitePacketStore(packet_dir), worker_factory=factory,
        freshness_provider=lambda job: {'fresh': True}, legacy_directory=legacy_dir).start()
    try:
        with new._db() as db:
            copied = {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                      for table in before}
        assert copied == before
        assert new.lookup(old_id, access_scope=SCOPE)['status'] == 'ok'
        assert new.inquire('legacy answer', SCOPE)['job']['job_id'] == old_id

        pending = new.inquire('new namespace only', SCOPE, foreground_timeout=0)
        assert pending['status'] == 'thinking' and entered.wait(2)
        with new._db() as db:
            new_id = db.execute("""SELECT jobs.id FROM jobs JOIN inquiry_index
                ON inquiry_index.job=jobs.id WHERE json_extract(jobs.record,'$.question')=?""",
                ('new namespace only',)).fetchone()[0]
        assert old.lookup(new_id, access_scope=SCOPE)['status'] == 'not_found'
        with sqlite3.connect(legacy_dir/'jobs.sqlite3') as db:
            assert db.execute('SELECT count(*) FROM jobs WHERE id=?', (new_id,)).fetchone()[0] == 0
    finally:
        release.set()
        new.shutdown()


def test_legacy_snapshot_rejects_active_source_without_modifying_it(tmp_path):
    legacy_dir, v2_dir = tmp_path/'legacy', tmp_path/'v2'
    old = JobService(legacy_dir, packets=SQLitePacketStore(tmp_path/'legacy-packets'))
    class Live:
        def is_alive(self): return True
    old._thread = Live()
    old.submit(question='still queued', access_scope=SCOPE, _identity='legacy-identity')
    before = old.path.read_bytes()
    with pytest.raises(RuntimeError, match='legacy_broker_active'):
        JobService(v2_dir, packets=SQLitePacketStore(tmp_path/'v2-packets'), legacy_directory=legacy_dir)
    assert old.path.read_bytes() == before
    assert not (v2_dir/'jobs.sqlite3').exists()


def test_legacy_snapshot_marker_supports_empty_first_boot_and_fails_closed_if_lost(tmp_path):
    legacy_dir, v2_dir = tmp_path/'legacy', tmp_path/'v2'
    first = JobService(v2_dir, packets=SQLitePacketStore(tmp_path/'packets'), legacy_directory=legacy_dir)
    assert first.path.is_file()
    marker = v2_dir/'legacy-broker-snapshot-v1.complete'
    assert marker.is_file()
    first.shutdown()

    second = JobService(v2_dir, packets=SQLitePacketStore(tmp_path/'packets'), legacy_directory=legacy_dir)
    assert second.path.is_file()
    second.shutdown()

    second.path.unlink()
    with pytest.raises(RuntimeError, match='legacy_broker_snapshot_missing'):
        JobService(v2_dir, packets=SQLitePacketStore(tmp_path/'packets'), legacy_directory=legacy_dir)
    assert not second.path.exists()


def test_evidence_only_partial_is_retained_without_automatic_refresh(tmp_path):
    calls=[]
    freshness=[]
    def factory(service,job):
        class Worker:
            def run(self,request):
                calls.append(job.job_id)
                service.observe(job.job_id,job.attempt,{'text':'retained source without an answer'})
                return {'status':'partial','reason':'step_budget_exhausted'}
        return Worker()
    s=make(tmp_path,worker_factory=factory,freshness_provider=lambda job:freshness.append(job)).start()
    try:
        expected={'status':'unavailable','reason':'analysis_unavailable'}
        assert s.inquire('source-only',SCOPE,foreground_timeout=2)==expected
        with s._db() as db:
            job=json.loads(db.execute('SELECT record FROM jobs').fetchone()[0])
        wait(lambda:s._settled(job['job_id']))
        assert job['status']=='partial' and job['evidence_packets'] and not job['answer'] and not job['findings']
        assert s.inquire('source-only',SCOPE)==expected
        assert len(calls)==1 and not freshness
    finally:s.shutdown()


def test_shared_answer_refresh_packets_and_cross_role_hints(tmp_path):
    entered, release, requests = threading.Event(), threading.Event(), []
    release.set()
    fresh = [True]
    s = make(tmp_path, worker_factory=blocking(entered, release, requests),
             freshness_provider=lambda job: {'fresh': fresh[0]}).start()
    other = {**SCOPE, 'principal': 'bob', 'profile': 'coder'}
    try:
        first = s.inquire('global answer', SCOPE, foreground_timeout=2)
        assert first['status'] == 'completed'
        job_id, ref = first['job']['job_id'], first['job']['result_packet']
        assert s.lookup(job_id, access_scope=other)['status'] == 'ok'
        assert s.inquire('global answer', other)['job']['answer'] == 'answer global answer'
        assert len(requests) == 1
        assert s.packets.lookup(ref, access_scope=other).status == 'ok'
        assert s.packets.lookup(ref, access_scope={**other, 'project': 'denied'}).status == 'forbidden'
        private = s.packets.create(tool='read', payload={'text': 'caller private source'}, access_scope=SCOPE)
        assert s.packets.lookup(private.packet_id, access_scope=other).status == 'ok'
        fresh[0] = False
        refreshed = s.inquire('global answer', other, foreground_timeout=2)
        assert refreshed['status'] == 'completed' and len(requests) == 2
        assert requests[-1]['refresh_context']['prior_job']['answer'] == 'answer global answer'
        fresh[0] = True
        assert s.inquire('private-derived', SCOPE, hints=[private.packet_id], foreground_timeout=2)['status'] == 'completed'
        before = len(requests)
        assert s.inquire('private-derived', other)['status'] == 'completed'
        assert s.log(access_scope=other, query='private-derived')
        assert s.inquire('private-derived', SCOPE)['status'] == 'completed'
        fresh[0] = False
        assert s.inquire('private-derived', SCOPE, foreground_timeout=2)['status'] == 'completed'
        fresh[0] = True
        assert s.inquire('private-derived', other)['status'] == 'completed'
        assert len(requests) == before + 1
        with s._db() as db:
            record = json.loads(db.execute("SELECT record FROM jobs WHERE id IN (SELECT job FROM inquiry_index) AND json_extract(record,'$.question')='private-derived'").fetchone()[0])
        assert private.packet_id in record['hints']
        assert s.packets.lookup(record['result_packet'], access_scope=other).status == 'ok'
    finally:
        release.set(); s.shutdown()


def test_cross_caller_pending_and_hint_pending_are_shared_read_only(tmp_path):
    entered, release, requests = threading.Event(), threading.Event(), []
    s = make(tmp_path, worker_factory=blocking(entered, release, requests)).start()
    other = {**SCOPE, 'principal': 'bob', 'profile': 'coder'}
    try:
        assert s.inquire('pending', SCOPE, foreground_timeout=0)['status'] == 'thinking'
        assert entered.wait(2)
        with s._db() as db:
            before = [tuple(r) for r in db.execute('SELECT * FROM jobs')]
        assert s.inquire('pending', other, hints=['missing'])['status'] == 'thinking'
        with s._db() as db:
            assert [tuple(r) for r in db.execute('SELECT * FROM jobs')] == before
        private = s.packets.create(tool='read', payload={'text': 'private'}, access_scope=SCOPE)
        assert s.inquire('private pending', SCOPE, hints=[private.packet_id], foreground_timeout=0)['status'] == 'thinking'
        assert s.inquire('private pending', other)['status'] == 'thinking'
        with s._db() as db:
            assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 2
    finally:
        release.set(); s.shutdown()


def test_startup_migration_collision_policy_preserves_jobs_slots_and_legacy(tmp_path):
    from project_control.as1_contracts import canonical_digest
    now = [1000.]
    s = make(tmp_path, clock=lambda: now[0])
    class Live:
        def is_alive(self): return True
    s._thread = Live()
    early = s.submit(question='collision', access_scope=SCOPE)['job_id']
    now[0] += 1
    later = s.submit(question='collision', access_scope={**SCOPE, 'principal': 'bob'})['job_id']
    now[0] += 1
    terminal = s.submit(question='collision', access_scope={**SCOPE, 'principal': 'carol'})['job_id']
    older_answer = s.submit(question='answered', access_scope=SCOPE)['job_id']
    newer_answer = s.submit(question='answered', access_scope={**SCOPE, 'profile': 'coder'})['job_id']
    legacy = s.submit(question='private legacy', access_scope=SCOPE)['job_id']
    with s._db() as db:
        for ident in [early, later, terminal, older_answer, newer_answer]:
            row = db.execute('SELECT record FROM jobs WHERE id=?', (ident,)).fetchone()
            job = json.loads(row['record'])
            if ident in [terminal, older_answer, newer_answer]:
                job['status'], job['answer'] = 'completed', ident
                db.execute('UPDATE jobs SET record=?,updated=? WHERE id=?',
                           (json.dumps(job), 1005 if ident == newer_answer else 1004, ident))
            old_key = canonical_digest({'question': job['question'], 'scope': job['scope'],
                                        'mode': job['mode'], 'skill': job['skill']})
            db.execute('INSERT INTO inquiry_index VALUES(?,?)', (old_key, ident))
        db.execute("DELETE FROM broker_migrations WHERE name='global_inquiry_context_v1'")
        db.execute('INSERT INTO execution_slots(job,attempt,lease) VALUES(?,?,?)', (later, 1, 2000))
        before = [tuple(r) for r in db.execute('SELECT * FROM jobs')]
        slots = [tuple(r) for r in db.execute('SELECT * FROM execution_slots')]
    restored = make(tmp_path, clock=lambda: now[0])
    with restored._db() as db:
        assert {r[0] for r in db.execute('SELECT job FROM inquiry_index')} == {early, newer_answer}
        assert [tuple(r) for r in db.execute('SELECT * FROM execution_slots')] == slots
        assert db.execute('SELECT inquiry FROM jobs WHERE id=?', (legacy,)).fetchone()[0] == 0
        after = [tuple(r) for r in db.execute('SELECT * FROM jobs')]
        assert [r[:-1] for r in after] == [r[:-1] for r in before]
    again = make(tmp_path, clock=lambda: now[0])
    with again._db() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM jobs')] == after
        assert [tuple(r) for r in db.execute('SELECT * FROM execution_slots')] == slots
    assert again.lookup(legacy, access_scope={**SCOPE, 'principal': 'bob'})['status'] == 'ok'


def test_dispatch_skips_incompatible_authority_and_shares_legacy_execution(tmp_path):
    from project_control.as1_context import ContextHost
    from project_control.as1_surface import SurfaceComposition
    a, b = SurfaceComposition(), SurfaceComposition()
    a.host = ContextHost('observer', 'alice', frozenset({'p'}))
    b.host = ContextHost('coder', 'bob', frozenset({'q'}))
    s = make(tmp_path)
    class Live:
        def is_alive(self): return True
    s._thread = Live()
    assert s.inquire('p inquiry', a.scope('p'), foreground_timeout=0)['status'] == 'thinking'
    assert s.inquire('p catalog', a.scope(None), foreground_timeout=0)['status'] == 'thinking'
    private = s.submit(question='private legacy', access_scope=a.scope('p'))['job_id']
    incompatible = make(tmp_path, can_execute=b.can_execute_inquiry)
    with s._db() as db:
        before = [tuple(r) for r in db.execute('SELECT * FROM jobs')]
    assert incompatible.claim() is None
    with s._db() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM jobs')] == before
        assert db.execute('SELECT count(*) FROM execution_slots').fetchone()[0] == 0
    b.host = ContextHost('coder', 'bob', frozenset({'p'}))
    same_project = incompatible.claim()
    assert same_project.question == 'p inquiry' and same_project.scope['principal'] == 'alice'
    same_catalog = incompatible.claim()
    assert same_catalog.question == 'p catalog'
    with s._db() as db:
        db.execute('DELETE FROM execution_slots')
        for ident in [same_project.job_id, same_catalog.job_id]:
            db.execute("UPDATE jobs SET record=json_set(record,'$.status','completed') WHERE id=?", (ident,))
    assert incompatible.claim().job_id == private
    assert s.lookup(private, access_scope=a.scope('p'))['job']['attempt'] == 1


def test_storage_and_payload_limits_are_unavailable_not_global_busy(tmp_path):
    s = make(tmp_path)
    class Live:
        def is_alive(self): return True
    s._thread = Live()
    assert s.inquire('x'*40000, SCOPE, foreground_timeout=0) == {'status': 'unavailable', 'reason': 'inquiry_too_large'}
    s.max_storage_bytes = 1
    assert s.inquire('storage', SCOPE, foreground_timeout=0) == {'status': 'unavailable', 'reason': 'storage_limit'}
    with s._db() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0


def test_old_catalog_without_authority_manifest_has_no_cross_caller_grant(tmp_path):
    from project_control.as1_context import ContextHost
    from project_control.as1_surface import SurfaceComposition
    c = SurfaceComposition(); c.host = ContextHost('coder', 'bob', frozenset())
    legacy_catalog = {'principal': 'alice', 'profile': 'observer', 'project': 'catalog'}
    assert not c.inquiry_access({'project': 'catalog'}, c.scope(None), log=True)
    assert c.can_execute_inquiry(type('Job', (), {'scope': legacy_catalog})(), True)
    assert c.inquiry_access({'project': 'catalog', 'catalog_projects': []}, c.scope(None), log=True)


def test_legacy_answer_log_uses_one_global_window_and_read_does_not_grant_mutation(tmp_path):
    now = [1000.]
    s = make(tmp_path, clock=lambda: now[0])
    class Live:
        def is_alive(self): return True
    s._thread = Live()
    for i in range(53):
        scope = {**SCOPE, 'principal': f'caller-{i}', 'profile': 'coder' if i % 2 else 'observer'}
        ident = s.submit(question='rare-old' if i == 0 else f'legacy {i}', access_scope=scope)['job_id']
        job = s.claim()
        assert s.finish(ident, job.attempt, {'status': 'completed', 'answer': f'legacy answer {i}'})
        with s._db() as db:
            db.execute('DELETE FROM execution_slots WHERE job=?', (ident,))
        now[0] += 1
    reader = {**SCOPE, 'principal': 'another', 'profile': 'coder'}
    assert not s.log(access_scope=reader, query='rare-old')
    assert s.log(access_scope=reader)[0]['question'] == 'legacy 52'
    assert len(s.log(access_scope=reader)) == 5
    pending = s.submit(question='pending mutation', access_scope=SCOPE)['job_id']
    assert s.lookup(pending, access_scope=reader)['status'] == 'ok'
    assert not s.cancel(pending, access_scope=reader)
    assert s.lookup(pending, access_scope={**reader, 'project': 'outside'})['status'] == 'forbidden'
