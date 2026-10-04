"""Service-private durable observer queue; no Todo authority or GPU scheduler."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from .as1_contracts import DurableJob, Finding, InformationPacket, canonical_digest
from .as1_packets import mask_payload

TERMINAL = {'completed', 'partial', 'failed', 'cancelled'}
SHARED_TOOLS = frozenset({'overview', 'delta', 'frontier', 'search', 'evidence', 'impact', 'history', 'machine'})
_DB_LOCK = threading.RLock()
BUSY = 'Queue busy. Your question is queued. Do not wait; continue reasoning or other useful work and ask again later using this ID.'


def stamp(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat()


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


class TrustedObserverFactory:
    """Startup-owned installed Skills binding, verified before executing code.

    Digests must come from the validated producer receipt, never tool arguments.
    No ambient package is imported to establish this trust.
    """
    def __init__(self, skills_root, expected_sha256, *, backend, roots, tools, skills=None):
        self.root = Path(skills_root).resolve(strict=True)
        path = self.root / 'local-coding-worker/local_worker/observer_runtime.py'
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
            raise ValueError('observer runtime receipt mismatch')
        self.path, self.digest = path, expected_sha256
        self.backend, self.roots, self.tools = backend, tuple(roots), tools
        self.skills = dict(skills or {})

    def __call__(self, service, job):
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != self.digest:
            raise ValueError('observer runtime changed after validation')
        spec = importlib.util.spec_from_file_location('pc_trusted_observer_runtime', self.path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        command = module.ReadOnlyCommandRunner(self.roots,
            packetize=lambda payload: service.observe(job.job_id, job.attempt, payload))
        backend = self.backend
        # The legacy PC provider exposes investigate_turn rather than the port name.
        class Adapter:
            def run_observer_turn(self, request):
                method = getattr(backend, 'run_observer_turn', None) or backend.investigate_turn
                return method(request)
            def preemption_status(self, session):
                method = getattr(backend, 'preemption_status', None)
                if method is None and callable(getattr(backend, '_get_backend', None)):
                    method = getattr(backend._get_backend(), 'preemption_status', None)
                return method(session) if method else {}
        def tools(name, arguments):
            if name == 'log':
                payload = {'records': service.log(access_scope=job.scope, **arguments)}
            elif name in SHARED_TOOLS:
                payload = self.tools(name, arguments, job.scope)
            else:
                raise ValueError('tool denied')
            ident = service.observe(job.job_id, job.attempt, payload, tool=name)
            return {**payload, 'packet_id': ident}
        return module.ObserverWorkerPort(Adapter(), command=command, tools=tools,
            fence=service.fence, checkpoint=service.checkpoint)


class JobService:
    """A process-lived dispatcher explicitly started by its host lifespan.

    Poll/log never start inference. Multiple processes may use the same local
    SQLite database: leases and generations serialize claims, not GPU policy.
    """
    def __init__(self, directory, *, packets, worker_factory=None, backend=None,
                 hard_limit=100, max_storage_bytes=64 * 1024 * 1024,
                 lease_seconds=120, retry_seconds=1, clock=time.time):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'jobs.sqlite3'
        self.packets, self.worker_factory, self.backend = packets, worker_factory, backend
        self.hard_limit, self.max_storage_bytes = hard_limit, max_storage_bytes
        self.lease_seconds, self.retry_seconds, self.clock = lease_seconds, retry_seconds, clock
        self._stop, self._wake = threading.Event(), threading.Event()
        self._thread = None
        self.last_error = None
        # WAL mode persists; set it once, before dispatch, not on every racing connection.
        with _DB_LOCK:
            initial = sqlite3.connect(self.path, timeout=10)
            try:
                initial.execute('PRAGMA journal_mode=WAL').fetchone()
            finally:
                initial.close()
        with self._db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_id TEXT, request_hash TEXT NOT NULL,
                record TEXT NOT NULL, observations TEXT NOT NULL DEFAULT '[]',
                updated REAL NOT NULL, lease REAL NOT NULL DEFAULT 0, available REAL NOT NULL DEFAULT 0,
                UNIQUE(scope,request_id));
                CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, job TEXT NOT NULL,
                packet TEXT NOT NULL, materialized INTEGER NOT NULL DEFAULT 0);''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def _db(self):
        # Bound same-process SQLite open/close races; transactions remain short.
        with _DB_LOCK:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            try:
                with db:
                    yield db
            finally:
                db.close()


    def health(self):
        return {'dispatcher': 'running' if self._thread and self._thread.is_alive() else 'stopped',
                'last_error': self.last_error, 'durable': True}

    def start(self):
        if self.worker_factory is None:
            raise RuntimeError('durable_processing_unavailable')
        if not self._thread or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._drain, name='pc-job-dispatch', daemon=True)
            self._thread.start()
        return self

    def shutdown(self, timeout=95):
        self._stop.set(); self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        return not (self._thread and self._thread.is_alive())

    def submit(self, *, question, access_scope, request_id=None, mode='investigate', hints=(), skill=None):
        scope = dict(access_scope)
        if not scope.get('principal') or not scope.get('profile'):
            raise ValueError('trusted principal/profile scope required')
        question = ' '.join(question.split())
        hints = list(dict.fromkeys(hints))
        job = DurableJob(job_id='job_' + uuid.uuid4().hex, mode=mode, question=question,
            hints=hints, status='queued', attempt=0, created_at=stamp(self.clock()), findings=[],
            evidence_packets=[], unresolved_questions=[], project=scope.get('project'),
            scope=scope, request_id=request_id, skill=skill)
        request_hash = canonical_digest({'question': question, 'scope': scope, 'mode': mode,
                                         'hints': hints, 'skill': skill})
        if len(wire(job.model_dump()).encode()) > 32768:
            return {'accepted': False, 'reason': 'admission_limit'}
        committed = False
        pinned = False
        try:
            # Retries must survive expiry of their original inputs.
            if request_id:
                with self._db() as db:
                    old = db.execute('SELECT * FROM jobs WHERE scope=? AND request_id=?',
                                     (wire(scope), request_id)).fetchone()
                if old:
                    if old['request_hash'] != request_hash:
                        return {'accepted': False, 'reason': 'request_id_mismatch'}
                    return self._accepted(DurableJob.model_validate_json(old['record']), False, retry=True)
            if mode == 'skill' and skill not in getattr(self.worker_factory, 'skills', {}):
                return {'accepted': False, 'reason': 'unregistered_skill'}
            # Pin before admission so concurrent packet GC cannot expire accepted inputs.
            # The owner is unique; unsuccessful admission releases its temporary pins.
            for ref in hints:
                if self.packets.lookup(ref, access_scope=scope).status != 'ok':
                    return {'accepted': False, 'reason': 'hint_unavailable'}
            if hints:
                self.packets.pin(job.job_id, hints)
                pinned = True
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                old = db.execute('SELECT * FROM jobs WHERE scope=? AND request_id=?',
                                 (wire(scope), request_id)).fetchone() if request_id else None
                if old:
                    if old['request_hash'] != request_hash:
                        return {'accepted': False, 'reason': 'request_id_mismatch'}
                    return self._accepted(DurableJob.model_validate_json(old['record']), False, retry=True)
                if not self._thread or not self._thread.is_alive():
                    return {'accepted': False, 'reason': 'durable_processing_unavailable'}
                records = [json.loads(r[0]) for r in db.execute('SELECT record FROM jobs')]
                count = sum(r['status'] not in TERMINAL for r in records)
                usage = db.execute('SELECT coalesce(sum(length(CAST(record AS BLOB))+length(CAST(observations AS BLOB))),0) FROM jobs').fetchone()[0]
                usage += db.execute('SELECT coalesce(sum(length(CAST(packet AS BLOB))),0) FROM outbox').fetchone()[0]
                if count >= self.hard_limit or usage + len(wire(job.model_dump()).encode()) > self.max_storage_bytes:
                    return {'accepted': False, 'reason': 'admission_limit'}
                db.execute('INSERT INTO jobs(id,scope,request_id,request_hash,record,updated) VALUES(?,?,?,?,?,?)',
                    (job.job_id, wire(scope), request_id, request_hash, job.model_dump_json(), self.clock()))
            committed = True
            # Packet references are reconciled from committed broker state.
            self._wake.set()
            return self._accepted(job, count >= 3)
        except (sqlite3.Error, OSError):
            return {'accepted': False, 'reason': 'storage_unavailable'}
        except ValueError:
            return {'accepted': False, 'reason': 'hint_unavailable'}
        finally:
            if pinned and not committed:
                self.packets.unpin(job.job_id)

    @staticmethod
    def _accepted(job, busy, retry=False):
        return {'accepted': True, 'job_id': job.job_id, 'status': job.status, 'retry': retry,
                'poll': {'job_id': job.job_id}, 'message': BUSY if busy else 'Accepted. Continue other work and poll this ID later.'}

    def lookup(self, job_id, *, access_scope):
        with self._db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            return {'status': 'not_found'}
        if json.loads(row['scope']) != dict(access_scope):
            return {'status': 'forbidden'}
        return {'status': 'ok', 'job': DurableJob.model_validate_json(row['record']).model_dump(),
                'observations': json.loads(row['observations'])}

    poll = lookup

    def log(self, *, access_scope, query='', limit=50, current_dependencies=None):
        if not 1 <= limit <= 50:
            raise ValueError('log limit must be 1..50')
        with self._db() as db:
            rows = db.execute('SELECT * FROM jobs WHERE scope=? ORDER BY updated DESC', (wire(dict(access_scope)),)).fetchall()
        result = []
        for row in rows:
            job = json.loads(row['record']); observations = json.loads(row['observations'])
            if query.casefold() not in wire({'job': job, 'observations': observations}).casefold():
                continue
            refs = list(dict.fromkeys(job['hints'] + job['evidence_packets'] + ([job['result_packet']] if job['result_packet'] else [])))
            result.append({'job': job, 'evidence': self.packets.assemble_hints(refs,
                access_scope=access_scope, current_dependencies=current_dependencies), 'authoritative': False})
            if len(result) >= limit:
                break
        return result

    def fence(self, job_id, attempt):
        with self._db() as db:
            row = db.execute('SELECT record,lease FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            return False
        job = json.loads(row['record'])
        return job['attempt'] == attempt and job['status'] == 'running' and row['lease'] > self.clock()

    def claim(self):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            now = self.clock()
            rows = db.execute('SELECT * FROM jobs WHERE available<=? ORDER BY updated,id', (now,)).fetchall()
            for row in rows:
                job = DurableJob.model_validate_json(row['record'])
                if job.status in TERMINAL or (job.status == 'running' and row['lease'] > now):
                    continue
                job.attempt += 1; job.status = 'running'
                db.execute('UPDATE jobs SET record=?,lease=?,updated=? WHERE id=?',
                    (job.model_dump_json(), now + self.lease_seconds, now, job.job_id))
                return job
        return None

    def _running(self, db, job_id, attempt):
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise RuntimeError('stale_attempt')
        job = DurableJob.model_validate_json(row['record'])
        if job.attempt != attempt or job.status != 'running' or row['lease'] <= self.clock():
            raise RuntimeError('stale_attempt')
        return row, job

    def _packet(self, db, job, payload, tool):
        cleaned, omissions = mask_payload(payload)
        ident = 'pkt_' + uuid.uuid4().hex
        # UUID encoded as words keeps aliases memorable-shaped and collision-safe.
        letters = ''.join(chr(97 + int(c, 16)) for c in ident[4:])
        packet = InformationPacket(packet_id=ident, alias='job-' + letters,
            created_at=stamp(self.clock()), tool=tool, payload=cleaned,
            payload_sha256=canonical_digest(cleaned), sources=[], parents=[],
            access_scope=job.scope, omissions=omissions,
            freshness={'volatile': True, 'max_age_seconds': 0}, pinned_by=[job.job_id])
        usage = db.execute('SELECT coalesce(sum(length(CAST(record AS BLOB))+length(CAST(observations AS BLOB))),0) FROM jobs').fetchone()[0]
        usage += db.execute('SELECT coalesce(sum(length(CAST(packet AS BLOB))),0) FROM outbox').fetchone()[0]
        if usage + 2 * len(packet.model_dump_json().encode()) > self.max_storage_bytes:
            raise ValueError('durable observation storage cap')
        if len(packet.model_dump_json().encode()) > self.packets.max_payload_bytes:
            raise ValueError('packet exceeds configured payload cap')
        db.execute('INSERT INTO outbox(id,job,packet) VALUES(?,?,?)', (ident, job.job_id, packet.model_dump_json()))
        return ident, cleaned

    def observe(self, job_id, attempt, payload, *, tool='command'):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row, job = self._running(db, job_id, attempt)
            ident, cleaned = self._packet(db, job, payload, tool)
            observations = json.loads(row['observations']) + [{**cleaned, 'packet_id': ident}]
            job.evidence_packets.append(ident)
            db.execute('UPDATE jobs SET record=?,observations=?,updated=?,lease=? WHERE id=?',
                (job.model_dump_json(), wire(observations), self.clock(), self.clock()+self.lease_seconds, job_id))
        self.reconcile()
        return ident

    def checkpoint(self, job_id, attempt, observations):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            _, job = self._running(db, job_id, attempt)
            cleaned, _ = mask_payload(observations)
            if any(o.get('packet_id') not in job.evidence_packets for o in cleaned):
                raise ValueError('checkpoint requires broker observations')
            # Canonical observations already persisted by observe; checkpoint only renews lease.
            db.execute('UPDATE jobs SET lease=? WHERE id=?', (self.clock()+self.lease_seconds, job_id))

    def finish(self, job_id, attempt, result):
        result, _ = mask_payload(result)
        if result.get('status') == 'stale_attempt':
            return False
        # Ordinary turn exhaustion is terminal, including legacy producer output.
        # Foreground preemption and session eviction retain their resumable status.
        if (result.get('status'), result.get('reason')) in {
                ('yielding', 'step_budget'), ('partial', 'step_budget_exhausted')}:
            result['status'] = 'partial'
            result['reason'] = 'step_budget_exhausted'
            if not result.get('unresolved_questions'):
                result['unresolved_questions'] = [
                    'The observer exhausted its bounded turn budget before answering the question. '
                    'Review retained observations and submit a new question for further investigation.']
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                _, job = self._running(db, job_id, attempt)
            except RuntimeError:
                return False
            status = result.get('status')
            if status not in TERMINAL | {'yielding', 'queued_after_eviction'}:
                raise ValueError('invalid worker status')
            job.status = status
            job.findings = [Finding.model_validate(f) for f in result.get('findings', [])]
            job.unresolved_questions = result.get('unresolved_questions', [])
            job = DurableJob.model_validate(job.model_dump())
            if any(p not in job.evidence_packets for f in job.findings for p in f.evidence_packets):
                raise ValueError('unobserved finding evidence')
            if status in TERMINAL:
                job.result_packet, _ = self._packet(db, job, result, job.mode)
            db.execute('UPDATE jobs SET record=?,updated=?,lease=0,available=? WHERE id=?',
                (job.model_dump_json(), self.clock(), self.clock()+self.retry_seconds, job_id))
        self.reconcile()
        return True

    def cancel(self, job_id, *, access_scope):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=? AND scope=?', (job_id, wire(dict(access_scope)))).fetchone()
            if not row:
                return False
            job = DurableJob.model_validate_json(row['record'])
            if job.status in TERMINAL:
                return False
            job.attempt += 1; job.status = 'cancelled'
            db.execute('UPDATE jobs SET record=?,lease=0,updated=? WHERE id=?', (job.model_dump_json(), self.clock(), job_id))
        self.reconcile()
        return True

    def reconcile(self):
        # Serialize retention snapshots across broker processes without a DB transaction.
        path = self.directory / 'reconcile.lock'
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._reconcile()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _reconcile(self):
        # Idempotent put consumes stable outbox identities. Never cross-store transaction claims.
        with self._db() as db:
            rows = db.execute('SELECT * FROM outbox WHERE materialized=0 ORDER BY rowid').fetchall()
        for row in rows:
            self.packets.put(InformationPacket.model_validate_json(row['packet']))
            with self._db() as db:
                db.execute('UPDATE outbox SET materialized=1 WHERE id=?', (row['id'],))
        with self._db() as db:
            jobs = [DurableJob.model_validate_json(r[0]) for r in db.execute('SELECT record FROM jobs ORDER BY updated')]
        terminal = [j for j in jobs if j.status in TERMINAL][-self.packets.recent_terminal_limit:]
        retained = [j for j in jobs if j.status not in TERMINAL] + terminal
        for job in retained:
            refs = job.hints + job.evidence_packets + ([job.result_packet] if job.result_packet else [])
            # Historical expiry is a log omission, not a dispatcher outage.
            live = [ref for ref in refs if self.packets.lookup(ref, access_scope=job.scope).status == 'ok']
            self.packets.pin(job.job_id, live)
        # Broker updated times determine recency, independent of packet clock ties.
        retained_ids = {job.job_id for job in retained}
        for job in jobs:
            if job.job_id not in retained_ids:
                self.packets.unpin(job.job_id)

    def _execute(self, job):
        session = None
        try:
            if self.backend:
                response = self.backend.open_sessions(1, compute_profile='narrow', parallelism='default')
                sessions = response.get('session_ids', response.get('sessions', []))
                if response.get('status') != 'available' or not sessions:
                    self.finish(job.job_id, job.attempt, {'status': 'yielding', 'reason': response.get('reason', 'session_unavailable')})
                    return
                session = sessions[0]
                if isinstance(session, dict):
                    session = session['session_id']
            stored = self.lookup(job.job_id, access_scope=job.scope)
            request = {'job_id': job.job_id, 'attempt': job.attempt, 'mode': job.mode,
                'question': job.question, 'scope': job.scope,
                'hints': self.packets.assemble_hints(job.hints, access_scope=job.scope),
                'observations': stored['observations'][-24:], 'max_steps': 6}
            if session:
                request['session_id'] = session
            if job.mode == 'skill':
                request['skill'] = self.worker_factory.skills[job.skill]
            worker = self.worker_factory(self, job)
            self.finish(job.job_id, job.attempt, worker.run(request))
        except Exception as error:
            self.last_error = type(error).__name__
            self.finish(job.job_id, job.attempt, {'status': 'partial', 'reason': type(error).__name__,
                'unresolved_questions': ['Retry from retained observations']})
        finally:
            if session:
                self.backend.close_session(session)

    def _drain(self):
        while not self._stop.is_set():
            try:
                self.reconcile()
                job = self.claim()
                if job:
                    self._execute(job)
                    continue
            except Exception as error:
                self.last_error = type(error).__name__
            self._wake.wait(.1); self._wake.clear()
