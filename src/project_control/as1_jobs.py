"""Service-private durable observer queue; no Todo authority or GPU scheduler."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import fcntl
import importlib.util
import json
import os
import math
import re
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from .as1_contracts import DurableJob, Finding, InformationPacket, canonical_digest
from .as1_packets import mask_payload

TERMINAL = {'completed', 'partial', 'failed', 'cancelled'}
SHARED_TOOLS = frozenset({'overview', 'delta', 'frontier', 'search', 'evidence', 'impact', 'history', 'machine'})
CHECKPOINT_MAX_OBSERVATIONS = 24
CHECKPOINT_MAX_FRAME_BYTES = 32768
CHECKPOINT_MAX_BYTES = 60000
_DB_LOCK = threading.RLock()
BUSY = 'Read-only analysis is pending. Continue reasoning or other useful work and poll with job_id; reuse request_id for retries.'


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
        scope = dict(job.scope)
        def fence(job_id, attempt):
            return (job_id == job.job_id and attempt == job.attempt
                    and service.fence(job_id, attempt, access_scope=scope))
        def checkpoint(job_id, attempt, observations):
            if job_id != job.job_id or attempt != job.attempt:
                raise RuntimeError('stale_attempt')
            service.checkpoint(job_id, attempt, observations, access_scope=scope)
        return module.ObserverWorkerPort(Adapter(), command=command, tools=tools,
            fence=fence, checkpoint=checkpoint)


class JobService:
    """A process-lived dispatcher explicitly started by its host lifespan.

    Poll/log never start inference. Multiple processes may use the same local
    SQLite database: leases and generations serialize claims, not GPU policy.
    """
    def __init__(self, directory, *, packets, worker_factory=None, backend=None,
                 hard_limit=100, max_storage_bytes=64 * 1024 * 1024,
                 lease_seconds=120, retry_seconds=None, clock=time.time, freshness_provider=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'jobs.sqlite3'
        self.packets, self.worker_factory, self.backend = packets, worker_factory, backend
        self.hard_limit, self.max_storage_bytes = hard_limit, max_storage_bytes
        self.lease_seconds, self.retry_seconds, self.clock = lease_seconds, retry_seconds, clock
        self._stop, self._wake = threading.Event(), threading.Event()
        self._thread = None
        self._threads = []
        self.freshness_provider = freshness_provider
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
                packet TEXT NOT NULL, materialized INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS job_checkpoints(
                job TEXT PRIMARY KEY, scope TEXT NOT NULL, attempt INTEGER NOT NULL,
                frames TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS inquiry_index(identity TEXT PRIMARY KEY, job TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS execution_slots(job TEXT PRIMARY KEY, attempt INTEGER NOT NULL, lease REAL NOT NULL, owner_pid INTEGER, owner_start TEXT, cleanup_failed INTEGER NOT NULL DEFAULT 0);''')
            if 'owner_pid' not in {r['name'] for r in db.execute('PRAGMA table_info(execution_slots)')}:
                db.execute('ALTER TABLE execution_slots ADD COLUMN owner_pid INTEGER')
            if 'owner_start' not in {r['name'] for r in db.execute('PRAGMA table_info(execution_slots)')}:
                db.execute('ALTER TABLE execution_slots ADD COLUMN owner_start TEXT')
            if 'cleanup_failed' not in {r['name'] for r in db.execute('PRAGMA table_info(execution_slots)')}:
                db.execute('ALTER TABLE execution_slots ADD COLUMN cleanup_failed INTEGER NOT NULL DEFAULT 0')
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
            self._threads = [threading.Thread(target=self._drain, name=f'pc-job-dispatch-{i}', daemon=True) for i in range(2)]
            self._thread = self._threads[0]
            for thread in self._threads:
                thread.start()
        return self

    def shutdown(self, timeout=95):
        self._stop.set(); self._wake.set()
        end = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0, end-time.monotonic()))
        return not any(thread.is_alive() for thread in self._threads)

    def submit(self, *, question, access_scope, request_id=None, mode='investigate', hints=(), skill=None,
               _identity=None, _execution_question=None, _freshness_snapshot=None):
        scope = dict(access_scope)
        if not scope.get('principal') or not scope.get('profile'):
            raise ValueError('trusted principal/profile scope required')
        if _identity is None:
            question = ' '.join(question.split())
        hints = list(dict.fromkeys(hints))
        job = DurableJob(job_id='job_' + uuid.uuid4().hex, mode=mode, question=question,
            hints=hints, status='queued', attempt=0, created_at=stamp(self.clock()), findings=[],
            evidence_packets=[], unresolved_questions=[], project=scope.get('project'),
            scope=scope, request_id=request_id if _identity is None else None, skill=skill,
            deadline_epoch=float(self.clock()+300), execution_question=_execution_question)
        request_hash = canonical_digest({'question': question, 'scope': scope, 'mode': mode,
                                         'hints': hints, 'skill': skill})
        if len(wire(job.model_dump()).encode()) > 32768:
            return {'accepted': False, 'reason': 'admission_limit'}
        committed = False
        pinned = False
        try:
            # Retries must survive expiry of their original inputs.
            if request_id and _identity is None:
                with self._db() as db:
                    old = db.execute('SELECT * FROM jobs WHERE scope=? AND request_id=?',
                                     (wire(scope), request_id)).fetchone()
                if old:
                    if old['request_hash'] != request_hash:
                        return {'accepted': False, 'reason': 'request_id_mismatch'}
                    return self._accepted(DurableJob.model_validate_json(old['record']), False, retry=True)
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                if _identity is not None:
                    old = db.execute('SELECT jobs.* FROM inquiry_index JOIN jobs ON jobs.id=inquiry_index.job WHERE identity=?', (_identity,)).fetchone()
                    if old:
                        previous = DurableJob.model_validate_json(old['record'])
                        if previous.status not in TERMINAL or db.execute('SELECT 1 FROM execution_slots WHERE job=?', (previous.job_id,)).fetchone():
                            return self._accepted(previous, False, retry=True)
                        if (_freshness_snapshot is None or _freshness_snapshot['job_id'] != old['id']
                                or _freshness_snapshot['record'] != old['record']):
                            return {'accepted': False, 'reason': 'inquiry_changed'}
                        freshness = _freshness_snapshot['freshness']
                        if previous.status in {'completed', 'partial'} and freshness.get('fresh'):
                            return self._accepted(previous, False, retry=True)
                        job.refresh_context = {'prior_job': previous.model_dump(exclude={'refresh_context', 'execution_question'}),
                            'observations': self._public_observations(db, old), 'change_info': freshness}
                    old = None
                else:
                    old = db.execute('SELECT * FROM jobs WHERE scope=? AND request_id=?',
                                     (wire(scope), request_id)).fetchone() if request_id else None
                if old:
                    if old['request_hash'] != request_hash:
                        return {'accepted': False, 'reason': 'request_id_mismatch'}
                    return self._accepted(DurableJob.model_validate_json(old['record']), False, retry=True)
                if not self._thread or not self._thread.is_alive():
                    return {'accepted': False, 'reason': 'durable_processing_unavailable'}
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
                records = [json.loads(r[0]) for r in db.execute('SELECT record FROM jobs')]
                occupied_terminal = db.execute("SELECT count(*) FROM execution_slots JOIN jobs ON jobs.id=execution_slots.job WHERE json_extract(jobs.record,'$.status') IN ('completed','partial','failed','cancelled')").fetchone()[0]
                count = sum(r['status'] not in TERMINAL for r in records) + occupied_terminal
                usage = self._storage_bytes(db)
                if count >= min(6, self.hard_limit) or usage + len(wire(job.model_dump()).encode()) > self.max_storage_bytes:
                    return {'accepted': False, 'reason': 'admission_limit'}
                db.execute('INSERT INTO jobs(id,scope,request_id,request_hash,record,updated) VALUES(?,?,?,?,?,?)',
                    (job.job_id, wire(scope), job.request_id, request_hash, job.model_dump_json(), self.clock()))
                if _identity is not None:
                    db.execute('INSERT INTO inquiry_index(identity,job) VALUES(?,?) ON CONFLICT(identity) DO UPDATE SET job=excluded.job', (_identity, job.job_id))
            committed = True
            # Packet references are reconciled from committed broker state.
            self._wake.set()
            return {**self._accepted(job, count >= 2), "immediate": count < 2}
        except (sqlite3.Error, OSError):
            return {'accepted': False, 'reason': 'storage_unavailable'}
        except ValueError:
            return {'accepted': False, 'reason': 'hint_unavailable'}
        finally:
            if pinned and not committed:
                self.packets.unpin(job.job_id)

    def inquire(self, question, access_scope, mode='investigate', skill=None, hints=(),
                request_id=None, execution_question=None, foreground_timeout=30):
        try:
            return self._inquire(question, access_scope, mode, skill, hints, request_id,
                                 execution_question, foreground_timeout)
        except (sqlite3.Error, OSError):
            return {'status': 'unavailable', 'reason': 'storage_unavailable'}

    def _inquire(self, question, access_scope, mode, skill, hints, request_id,
                 execution_question, foreground_timeout):
        scope = dict(access_scope)
        if not scope.get('principal') or not scope.get('profile'):
            raise ValueError('trusted principal/profile scope required')
        identity = canonical_digest({'question': question, 'scope': scope, 'mode': mode, 'skill': skill})
        # Read-only fast path: a duplicate does not validate new hints, touch order,
        # mutate TTL, or require a live dispatcher.
        with self._db() as db:
            row = db.execute('SELECT jobs.* FROM inquiry_index JOIN jobs ON jobs.id=inquiry_index.job WHERE identity=?', (identity,)).fetchone()
        freshness_snapshot = None
        if row:
            previous = DurableJob.model_validate_json(row['record'])
            if previous.status not in TERMINAL or not self._settled(previous.job_id):
                return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}
            freshness = self.freshness_provider(previous.model_dump()) if self.freshness_provider else {'fresh': False}
            freshness_snapshot = {'job_id': row['id'], 'record': row['record'], 'freshness': freshness}
            if previous.status in {'completed', 'partial'} and freshness.get('fresh'):
                with self._db() as db:
                    current = db.execute('SELECT jobs.* FROM inquiry_index JOIN jobs ON jobs.id=inquiry_index.job WHERE identity=?', (identity,)).fetchone()
                    if not current or current['id'] != row['id'] or current['record'] != row['record']:
                        return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}
                    observations = self._public_observations(db, current)
                return {'status': previous.status, 'job': previous.model_dump(), 'observations': observations}
        admitted = self.submit(question=question, access_scope=scope, mode=mode, skill=skill,
            hints=hints, request_id=request_id, _identity=identity, _execution_question=execution_question,
            _freshness_snapshot=freshness_snapshot)
        if not admitted['accepted']:
            if admitted['reason'] == 'inquiry_changed':
                return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}
            return {'status': 'busy' if admitted['reason'] == 'admission_limit' else 'unavailable',
                    'reason': admitted['reason']}
        value = self.lookup(admitted['job_id'], access_scope=scope)
        if value['job']['status'] in {'completed', 'partial'} and self._settled(value['job']['job_id']):
            return {**value, 'status': value['job']['status']}
        if not admitted.get('immediate') or admitted.get('retry'):
            return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}
        end = time.monotonic() + max(0, min(30, foreground_timeout))
        while time.monotonic() < end:
            value = self.lookup(admitted['job_id'], access_scope=scope)
            if value['job']['status'] in {'completed', 'partial'} and self._settled(value['job']['job_id']):
                return {**value, 'status': value['job']['status']}
            if value['job']['status'] in {'failed', 'cancelled'}:
                return {'status': 'unavailable', 'reason': 'analysis_unavailable'}
            time.sleep(min(.02, max(0, end-time.monotonic())))
        return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}

    def _settled(self, job_id):
        with self._db() as db:
            return (not db.execute('SELECT 1 FROM execution_slots WHERE job=?', (job_id,)).fetchone()
                    and not db.execute('SELECT 1 FROM outbox WHERE job=? AND materialized=0', (job_id,)).fetchone())

    @staticmethod
    def _accepted(job, busy, retry=False):
        return {'accepted': True, 'job_id': job.job_id, 'status': job.status, 'retry': retry,
                'poll': {'job_id': job.job_id}, 'message': BUSY if busy else 'Accepted. Continue other work and poll this ID later.'}

    def _storage_bytes(self, db):
        usage = db.execute('SELECT coalesce(sum(length(CAST(record AS BLOB))+length(CAST(observations AS BLOB))),0) FROM jobs').fetchone()[0]
        usage += db.execute('SELECT coalesce(sum(length(CAST(packet AS BLOB))),0) FROM outbox').fetchone()[0]
        usage += db.execute('SELECT coalesce(sum(length(CAST(frames AS BLOB))),0) FROM job_checkpoints').fetchone()[0]
        return usage

    def _public_observations(self, db, row):
        raw = json.loads(row['observations'])
        checkpoint = db.execute('SELECT * FROM job_checkpoints WHERE job=?', (row['id'],)).fetchone()
        if checkpoint is None:
            return raw
        if checkpoint['scope'] != row['scope']:
            raise PermissionError('checkpoint_scope_mismatch')
        job = DurableJob.model_validate_json(row['record'])
        if checkpoint['attempt'] > job.attempt:
            raise RuntimeError('invalid_checkpoint_attempt')
        saved = {frame['packet_id']: frame for frame in json.loads(checkpoint['frames'])}
        # Preserve raw observations committed after the last checkpoint/crash.
        return [saved.get(observation['packet_id'], observation) for observation in raw]

    def lookup(self, job_id, *, access_scope):
        with self._db() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                return {'status': 'not_found'}
            if json.loads(row['scope']) != dict(access_scope):
                return {'status': 'forbidden'}
            observations = self._public_observations(db, row)
        return {'status': 'ok', 'job': DurableJob.model_validate_json(row['record']).model_dump(),
                'observations': observations}

    poll = lookup

    def log(self, *, access_scope, query='', limit=5, current_dependencies=None):
        if not 1 <= limit <= 50:
            raise ValueError('log limit must be 1..50')
        with self._db() as db:
            rows = db.execute('SELECT * FROM jobs WHERE scope=? ORDER BY updated DESC,id DESC', (wire(dict(access_scope)),)).fetchall()
        candidates = []
        for row in rows:
            job = json.loads(row['record'])
            if job['status'] not in {'completed', 'partial'}:
                continue
            if job['status'] == 'partial' and not (job.get('answer') or job['findings']):
                continue
            candidates.append((job, json.loads(row['observations'])))
            if len(candidates) == 50:
                break
        tokens = lambda value: re.findall(r'\w+', value.casefold())
        documents = [tokens(wire({'question': j['question'], 'answer': j.get('answer'),
                                  'findings': j['findings'], 'evidence': obs})) for j, obs in candidates]
        terms = tokens(query)
        ranks = []
        average = sum(map(len, documents))/max(1, len(documents))
        for index, document in enumerate(documents):
            score = 0.
            for term in set(terms):
                frequency = document.count(term)
                if not frequency:
                    continue
                df = sum(term in d for d in documents)
                idf = math.log(1 + (len(documents)-df+.5)/(df+.5))
                score += idf*frequency*2.2/(frequency+1.2*(.25+.75*len(document)/max(1, average)))
            if not terms or score > 0:
                ranks.append((score, index))
        ranks.sort(key=lambda x: (-x[0], x[1]))
        result = []
        for _, index in ranks[:min(5, limit)]:
            job, _ = candidates[index]
            refs = list(dict.fromkeys(job['hints'] + job['evidence_packets'] + ([job['result_packet']] if job['result_packet'] else [])))
            evidence = self.packets.assemble_hints(refs, access_scope=access_scope, current_dependencies=current_dependencies)
            # Retain compact source/evidence summaries, never nested log packets or
            # complete historical jobs in the next worker's tool response.
            evidence = {**evidence, 'packets': [
                {'packet': {key: item['packet'][key] for key in ('packet_id', 'tool', 'sources') if key in item['packet']}, 'freshness': item['freshness']}
                for item in evidence.get('packets', [])]}
            result.append({'question': job['question'], 'answer': (job.get('answer') or ' '.join(f['text'] for f in job['findings']))[:2000],
                           'status': job['status'], 'evidence': evidence,
                           'freshness': self.freshness_provider(job) if self.freshness_provider else {'fresh': False}, 'authoritative': False})
        return result

    def fence(self, job_id, attempt, *, access_scope=None):
        with self._db() as db:
            row = db.execute('SELECT record,lease,scope FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or (access_scope is not None and json.loads(row['scope']) != dict(access_scope)):
            return False
        job = json.loads(row['record'])
        return job['attempt'] == attempt and job['status'] == 'running' and row['lease'] > self.clock() and (job.get('deadline_epoch') is None or self.clock() < job['deadline_epoch'])

    @staticmethod
    def _process_start(pid):
        try:
            # comm can contain spaces and parentheses; fields after its final
            # parenthesis begin at field 3, with starttime at field 22.
            return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
        except (OSError, IndexError):
            return None

    @classmethod
    def _owner_alive(cls, pid, start):
        return pid is not None and start is not None and cls._process_start(pid) == start

    def claim(self, *, _dispatch=False):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            now = self.clock()
            for expired in db.execute('SELECT * FROM execution_slots WHERE lease<=?', (now,)).fetchall():
                # A live host still owns its underlying operation even when a
                # noncooperative startup or clock jump outlasts the lease.
                if not expired['cleanup_failed'] and not self._owner_alive(expired['owner_pid'], expired['owner_start']):
                    db.execute('DELETE FROM execution_slots WHERE job=? AND attempt=?', (expired['job'], expired['attempt']))
            rows = db.execute('SELECT * FROM jobs ORDER BY updated,id').fetchall()
            for row in rows:
                job = DurableJob.model_validate_json(row['record'])
                if job.status in TERMINAL:
                    continue
                occupied = db.execute('SELECT 1 FROM execution_slots WHERE job=?', (job.job_id,)).fetchone()
                if occupied:
                    continue
                if (job.deadline_epoch is not None and now >= job.deadline_epoch) or job.attempt >= 3:
                    job.status = 'partial' if job.findings else 'failed'
                    job.unresolved_questions = ['Analysis deadline or attempt budget exhausted.']
                    db.execute('UPDATE jobs SET record=?,lease=0,updated=? WHERE id=?', (job.model_dump_json(), now, job.job_id))
                    self._trim_index(db, job.scope)
                    continue
                if row['available'] > now:
                    continue
                if db.execute('SELECT count(*) FROM execution_slots').fetchone()[0] >= 2:
                    continue
                job.attempt += 1; job.status = 'running'
                db.execute('UPDATE jobs SET record=?,lease=?,updated=? WHERE id=?',
                    (job.model_dump_json(), now + self.lease_seconds, now, job.job_id))
                db.execute('INSERT INTO execution_slots(job,attempt,lease,owner_pid,owner_start) VALUES(?,?,?,?,?)', (job.job_id, job.attempt, now+self.lease_seconds, os.getpid() if _dispatch else None, self._process_start(os.getpid()) if _dispatch else None))
                return job
        return None

    def _heartbeat(self, job, stop):
        while not stop.wait(max(.01, min(1, self.lease_seconds/3))):
            with self._db() as db:
                lease = self.clock()+self.lease_seconds
                db.execute('UPDATE execution_slots SET lease=? WHERE job=? AND attempt=?', (lease, job.job_id, job.attempt))
                db.execute('UPDATE jobs SET lease=? WHERE id=? AND json_extract(record,\'$.attempt\')=? AND json_extract(record,\'$.status\')=\'running\'', (lease, job.job_id, job.attempt))

    def _running(self, db, job_id, attempt):
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise RuntimeError('stale_attempt')
        job = DurableJob.model_validate_json(row['record'])
        slot = db.execute('SELECT owner_pid,owner_start FROM execution_slots WHERE job=? AND attempt=?', (job_id, attempt)).fetchone()
        if job.attempt != attempt or job.status != 'running' or (row['lease'] <= self.clock() and not (slot and self._owner_alive(slot['owner_pid'], slot['owner_start']))):
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
        usage = self._storage_bytes(db)
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

    def checkpoint(self, job_id, attempt, observations, *, access_scope=None):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row, job = self._running(db, job_id, attempt)
            if wire(job.scope) != row['scope'] or (access_scope is not None and dict(access_scope) != job.scope):
                raise PermissionError('checkpoint_scope_mismatch')
            if not isinstance(observations, list) or len(observations) > CHECKPOINT_MAX_OBSERVATIONS:
                raise ValueError('checkpoint observation count cap')
            canonical = {o['packet_id']: o for o in json.loads(row['observations'])}
            positions = {ident: index for index, ident in enumerate(canonical)}
            frames, seen, previous = [], set(), -1
            for observation in observations:
                if not isinstance(observation, dict):
                    raise ValueError('checkpoint requires broker observations')
                ident = observation.get('packet_id')
                if not isinstance(ident, str) or ident not in job.evidence_packets or ident not in canonical or ident in seen:
                    raise ValueError('checkpoint requires unique same-job broker observations')
                if positions[ident] <= previous:
                    raise ValueError('checkpoint observations out of order')
                previous = positions[ident]
                seen.add(ident)
                payload, _ = mask_payload({k: v for k, v in observation.items() if k != 'public_tool_call'})
                frame = dict(payload)
                if 'public_tool_call' in observation:
                    call = observation['public_tool_call']
                    if (not isinstance(call, dict) or set(call) != {'tool', 'arguments'}
                            or not isinstance(call['tool'], str) or call['tool'] not in SHARED_TOOLS | {'command', 'log'} or not isinstance(call['arguments'], dict)):
                        raise ValueError('invalid checkpoint public tool call')
                    # Preserve the actual accepted public JSON, never a differently
                    # masked/reconstructed call. Only the packet payload is masked.
                    frame['public_tool_call'] = json.loads(json.dumps(call, ensure_ascii=False, allow_nan=False))
                full = {**canonical[ident], **({'public_tool_call': frame['public_tool_call']} if 'public_tool_call' in frame else {})}
                omission = {'packet_id': ident, 'omissions': ['tool payload exceeded worker context budget']}
                if payload != canonical[ident] and not (
                        payload == omission and len(json.dumps(full, ensure_ascii=False).encode()) > CHECKPOINT_MAX_FRAME_BYTES):
                    raise ValueError('checkpoint payload differs from broker observation')
                if len(json.dumps(frame, ensure_ascii=False, allow_nan=False).encode()) > CHECKPOINT_MAX_FRAME_BYTES:
                    raise ValueError('checkpoint frame byte cap')
                frames.append(frame)
            if len(json.dumps(frames, ensure_ascii=False, allow_nan=False).encode()) > CHECKPOINT_MAX_BYTES:
                raise ValueError('checkpoint byte cap')
            encoded = wire(frames)
            old = db.execute('SELECT length(CAST(frames AS BLOB)) FROM job_checkpoints WHERE job=?', (job_id,)).fetchone()
            if self._storage_bytes(db) - (old[0] if old else 0) + len(encoded.encode()) > self.max_storage_bytes:
                raise ValueError('durable checkpoint storage cap')
            db.execute('INSERT INTO job_checkpoints(job,scope,attempt,frames) VALUES(?,?,?,?) '
                       'ON CONFLICT(job) DO UPDATE SET scope=excluded.scope,attempt=excluded.attempt,frames=excluded.frames',
                       (job_id, row['scope'], attempt, encoded))
            db.execute('UPDATE jobs SET lease=?,updated=? WHERE id=?',
                       (self.clock()+self.lease_seconds, self.clock(), job_id))

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
            if self.clock() >= (job.deadline_epoch or float('inf')) or (status not in TERMINAL and job.attempt >= 3):
                status = result['status'] = 'partial'
                result['reason'] = 'attempt_or_deadline_exhausted'
            job.status = status
            job.answer = result.get('answer', job.answer)
            job.findings = [Finding.model_validate(f) for f in result.get('findings', [f.model_dump() for f in job.findings])]
            job.unresolved_questions = result.get('unresolved_questions', job.unresolved_questions)
            job = DurableJob.model_validate(job.model_dump())
            if any(p not in job.evidence_packets for f in job.findings for p in f.evidence_packets):
                raise ValueError('unobserved finding evidence')
            if status in TERMINAL:
                job.result_packet, _ = self._packet(db, job, result, job.mode)
            db.execute('UPDATE jobs SET record=?,updated=?,lease=0,available=? WHERE id=?',
                (job.model_dump_json(), self.clock(), self.clock()+(self.retry_seconds if self.retry_seconds is not None else (5 if job.attempt == 1 else 15)), job_id))
            self._trim_index(db, job.scope)
        self.reconcile()
        return True

    def _trim_index(self, db, scope):
        cached = db.execute('SELECT inquiry_index.identity,jobs.record FROM inquiry_index JOIN jobs ON jobs.id=inquiry_index.job WHERE jobs.scope=? ORDER BY jobs.updated DESC,jobs.id DESC', (wire(scope),)).fetchall()
        terminal = [r['identity'] for r in cached if json.loads(r['record'])['status'] in TERMINAL]
        for identity in terminal[50:]:
            db.execute('DELETE FROM inquiry_index WHERE identity=?', (identity,))

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
            self._trim_index(db, job.scope)
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
        by_scope = {}
        for job in jobs:
            if job.status in TERMINAL:
                by_scope.setdefault(wire(job.scope), []).append(job)
        terminal = [job for group in by_scope.values() for job in group[-self.packets.recent_terminal_limit:]]
        retained = [j for j in jobs if j.status not in TERMINAL] + terminal
        for job in retained:
            refs = job.hints + job.evidence_packets + ([job.result_packet] if job.result_packet else [])
            if job.refresh_context:
                prior = job.refresh_context.get('prior_job', {})
                refs += prior.get('hints', []) + prior.get('evidence_packets', [])
                if prior.get('result_packet'):
                    refs.append(prior['result_packet'])
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
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(job, heartbeat_stop), daemon=True)
        heartbeat.start()
        try:
            if self.backend:
                response = self.backend.open_sessions(1, compute_profile='narrow', parallelism='default', deadline_epoch=job.deadline_epoch)
                sessions = response.get('session_ids', response.get('sessions', []))
                if response.get('status') != 'available' or not sessions:
                    self.finish(job.job_id, job.attempt, {'status': 'yielding', 'reason': response.get('reason', 'session_unavailable')})
                    return
                session = sessions[0]
                if isinstance(session, dict):
                    session = session['session_id']
            if job.deadline_epoch is not None and self.clock() >= job.deadline_epoch:
                self.finish(job.job_id, job.attempt, {'status': 'partial', 'reason': 'deadline_exhausted',
                    'unresolved_questions': ['The analysis deadline expired during startup.']})
                return
            stored = self.lookup(job.job_id, access_scope=job.scope)
            request = {'job_id': job.job_id, 'attempt': job.attempt, 'mode': job.mode,
                'question': job.execution_question or job.question, 'scope': job.scope,
                'deadline_epoch': job.deadline_epoch, 'refresh_context': job.refresh_context,
                'log_guidance': 'Use log for the last 50 answered inquiries; lexical retrieval returns at most five records.',
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
            cleanup_failed = False
            try:
                if session:
                    closed = self.backend.close_session(session)
                    if isinstance(closed, dict) and closed.get('status') in {'unavailable', 'busy', 'failed'}:
                        raise RuntimeError('session_cleanup_unproved')
            except Exception:
                cleanup_failed = True
                self.last_error = 'session_cleanup_unproved'
                with self._db() as db:
                    db.execute('UPDATE execution_slots SET cleanup_failed=1 WHERE job=? AND attempt=?', (job.job_id, job.attempt))
            finally:
                heartbeat_stop.set(); heartbeat.join()
                if not cleanup_failed:
                    with self._db() as db:
                        db.execute('DELETE FROM execution_slots WHERE job=? AND attempt=?', (job.job_id, job.attempt))

    def _drain(self):
        while not self._stop.is_set():
            try:
                self.reconcile()
                job = self.claim(_dispatch=True)
                if job:
                    self._execute(job)
                    continue
            except Exception as error:
                self.last_error = type(error).__name__
            self._wake.wait(.1); self._wake.clear()
