"""CPU-only inquiry identity, capacity, refresh, lease and retrieval contracts."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
import time

from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore

SCOPE = {'principal': 'alice', 'profile': 'observer', 'project': 'pc'}


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


def test_scoped_identity_and_coalesced_race(tmp_path):
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
            assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==4
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
    assert s.lookup(j,access_scope=SCOPE)['job']['status']=='partial'
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
            question='rare-old' if i==0 else f'question {i}'
            s.inquire(question,SCOPE,foreground_timeout=0)
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
        assert not s.log(access_scope={**SCOPE,'principal':'bob'},query='question')
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
            assert job['status']=='partial'
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


def test_cleanup_failure_keeps_durable_capacity_after_owner_death(tmp_path):
    now=[1000.]
    closes=[]
    class Backend:
        def open_sessions(self,count,**kwargs): return {'status':'available','session_ids':['session']}
        def close_session(self,session): closes.append(session);raise RuntimeError('model_process_not_quiescent')
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
