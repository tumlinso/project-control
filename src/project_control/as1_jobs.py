"""Service-private durable observer queue; no Todo authority or GPU scheduler."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import fcntl
import importlib
import json
import os
import math
import re
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .as1_contracts import DurableJob, Finding, InformationPacket, SourceLocator, canonical_digest
from .as1_packets import SQLITE_CONNECTION_LOCK, mask_payload
from .runtime_binding import bind_local_runtime, local_runtime_identity

TERMINAL = {'completed', 'partial', 'failed', 'cancelled'}
SHARED_TOOLS = frozenset({'overview', 'delta', 'frontier', 'search', 'evidence', 'impact', 'history', 'machine'})
CHECKPOINT_MAX_OBSERVATIONS = 24
CHECKPOINT_MAX_FRAME_BYTES = 32768
CHECKPOINT_MAX_BYTES = 96 * 1024
WORKER_JOB_INPUT_MAX_BYTES = 256 * 1024
WORKER_OBSERVATION_MAX_BYTES = 32768
_DB_LOCK = threading.RLock()
_RECONCILE_LOCKS_GUARD = threading.Lock()
_RECONCILE_LOCKS: dict[Path, threading.RLock] = {}
_RECONCILE_LOCK_DEPTH = threading.local()
BUSY = 'Read-only analysis is pending. Continue reasoning or other useful work and poll with job_id; reuse request_id for retries.'


@contextmanager
def _reconcile_file_lock(directory):
    """Serialize reconcile across processes while allowing same-thread nesting.

    Independently opened ``flock`` descriptors can conflict within one
    process. A process-local reentrant lock serializes threads first, and a
    per-thread depth counter makes a recursively nested call reuse the
    outermost flock. Cross-process exclusion remains provided by that flock.
    """
    path = (Path(directory) / 'reconcile.lock').resolve()
    with _RECONCILE_LOCKS_GUARD:
        process_lock = _RECONCILE_LOCKS.setdefault(path, threading.RLock())
    with process_lock:
        depths = getattr(_RECONCILE_LOCK_DEPTH, 'paths', None)
        if depths is None:
            depths = _RECONCILE_LOCK_DEPTH.paths = {}
        depth = depths.get(path, 0)
        if depth:
            depths[path] = depth + 1
            try:
                yield
            finally:
                depths[path] -= 1
            return

        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        locked = False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            locked = True
            depths[path] = 1
            yield
        finally:
            depths.pop(path, None)
            try:
                if locked:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _load_assistance_components():
    """Load PA1 extensions after this module is fully initialized.

    The assistance package exports source-evaluation helpers that use
    JobService. Delaying these imports avoids a package-init cycle while
    retaining ordinary canonical module identities.
    """
    global ChildFrameSpec, FrameError, FrameStore, PowerPolicy, policy_for, ResourceController, ResourceControllerError, trusted_operator_control
    global automatic_read_scope_from_dependencies
    if 'FrameStore' not in globals():
        from .assistance.frames import (ChildFrameSpec as child_spec, FrameError as frame_error,
            FrameStore as frame_store, automatic_read_scope_from_dependencies as seal_read_scope)
        from .assistance.power import PowerPolicy as power_policy, trusted_operator_control as mint_operator_control
        from .assistance.policies import policy_for as select_policy
        from .assistance.resources import (ResourceController as resource_controller,
            ResourceControllerError as resource_controller_error)
        ChildFrameSpec, FrameError, FrameStore = child_spec, frame_error, frame_store
        automatic_read_scope_from_dependencies = seal_read_scope
        PowerPolicy, policy_for = power_policy, select_policy
        ResourceController = resource_controller
        ResourceControllerError = resource_controller_error
        trusted_operator_control = mint_operator_control

# Operator diagnostics intentionally expose only stable, non-content classes.
# Raw worker/backend reasons remain in the private durable job record.
_INQUIRY_FAILURE_CLASSES = {
    'context_budget': 'context_budget',
    'finding_requires_observed_packet': 'finding_requires_observed_packet',
    'final_answer_missing': 'final_answer_missing',
    'final_round_requires_answer': 'final_answer_missing',
    'invalid_unresolved_questions': 'invalid_unresolved_questions',
    'model_output_invalid_json': 'model_output_invalid_json',
    'model_output_not_object': 'model_output_invalid_json',
    'invalid_json_object': 'model_output_invalid_json',
    'final_response_not_object': 'model_output_invalid_json',
    'malformed_tool_envelope': 'model_output_invalid_json',
    'json.loads_extra_data': 'model_output_invalid_json',
    'JSONDecodeError': 'model_output_invalid_json',
    'tool_denied_for_readonly_mode': 'tool_denied_for_readonly_mode',
    'step_budget_exhausted': 'step_budget_exhausted',
    'step_budget': 'step_budget_exhausted',
    'runtime_identity_mismatch': 'runtime_mismatch',
    'central_supervisor_runtime_mismatch': 'runtime_mismatch',
    'central_supervisor_root_mismatch': 'runtime_mismatch',
    'assistance_release_veto_active': 'release_veto_active',
    'demand_runtime_not_ready': 'runtime_unavailable',
    'demand_runtime_unavailable': 'runtime_unavailable',
    'demand_deadline_exhausted_before_start': 'deadline_exhausted',
    'inference_supervisor_readiness_timeout': 'runtime_readiness_unavailable',
    'inference_service_start_failed': 'runtime_start_failed',
    'inference_start_lock_timeout': 'runtime_start_timeout',
    'attempt_or_deadline_exhausted': 'attempt_or_deadline_exhausted',
    'deadline_exhausted': 'deadline_exhausted',
    'analysis_deadline_exhausted': 'deadline_exhausted',
    'worker_deadline_exhausted': 'deadline_exhausted',
    'job_input_budget_exhausted': 'job_input_budget_exhausted',
}
_PUBLIC_WAIT_REASONS = frozenset({'release_veto_active', 'automatic_disabled',
    'quiet_window_active', 'foreground_priority', 'session_unavailable',
    'runtime_unavailable', 'capacity_wait', 'dispatch_policy_changed',
    'preemption_requested', 'foreground_preemption', 'session_evicted'})
_COLD_SUPERVISOR_FAILURES = frozenset({
    'central_supervisor_unavailable',
    'central_supervisor_timeout',
    'central_supervisor_transport_timeout',
    'central_supervisor_transport_error',
})
_CLOSE_RECEIPT_ERROR_CODES = frozenset({
    'session_id_invalid', 'active_session_record_required',
    'close_receipt_fields_invalid', 'close_receipt_identity_invalid',
    'close_receipt_gpu_scope_invalid', 'session_not_active',
    'resource_metadata_must_be_json',
})


def _demand_readiness_failure_reason(value):
    """Keep a bounded stable readiness code in private job diagnostics.

    Demand startup errors may include operator guidance, paths or systemd
    stderr. Persist only their leading stable code; unknown strings collapse
    to a generic class rather than becoming durable inquiry content.
    """
    reason = value.get('reason') if isinstance(value, dict) else None
    if not isinstance(reason, str) or not reason.strip():
        return 'demand_runtime_not_ready'
    code = reason.strip().split(';', 1)[0].split(':', 1)[0].strip()
    if (len(code) <= 96 and re.fullmatch(r'[a-z][a-z0-9_]*', code)
            and code.startswith(('assistance_', 'central_supervisor_', 'demand_',
                                 'inference_', 'observer_'))):
        return code
    return 'demand_runtime_unavailable'


def _public_wait_reason(value):
    if not isinstance(value, str):
        return None
    if value in _PUBLIC_WAIT_REASONS:
        return value
    if value in _INQUIRY_FAILURE_CLASSES:
        return _INQUIRY_FAILURE_CLASSES[value]
    if value.startswith(('central_supervisor_', 'runtime_identity_')):
        return 'runtime_mismatch'
    return 'pending'


class InvalidToolArguments(ValueError):
    """Pre-dispatch argument error, distinct from authority/backend failures."""


class ObserverLogArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    query: str = ''
    limit: int = Field(default=5, ge=1, le=50)


def stamp(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat()


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def source_locator_for_registered_roots(source_path, digest, roots):
    """Resolve a verified source to one host-authorized root, fail closed on overlap."""
    matches = []
    for project, repository, root_value in roots:
        try:
            root = Path(root_value).resolve(strict=True)
            relative = source_path.relative_to(root).as_posix()
            if relative:
                matches.append(SourceLocator(project=project, repository=repository,
                    path=relative, content_sha256=digest))
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
    return matches[0] if len(matches) == 1 else None


class TrustedObserverFactory:
    """Startup-owned receiver runtime binding, verified before executing code.

    The validated receiver manifest supplies the file inventory; a producer
    receipt may additionally pin the observer entrypoint digest. The legacy
    Skills root is not a runtime source and cannot redirect this binding.
    """
    def __init__(self, runtime_root, expected_sha256, *, backend, roots, tools, skills=None,
                 command_factory=None):
        identity = bind_local_runtime(root=runtime_root)
        path = identity.root / 'local_worker/observer_runtime.py'
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected_sha256:
            raise ValueError('observer runtime receipt mismatch')
        self.runtime_identity = identity
        self.root, self.path, self.digest = identity.root, path, digest
        self.backend, self.roots, self.tools = backend, tuple(roots), tools
        self.skills = dict(skills or {})
        self.command_factory = command_factory

    def __call__(self, service, job):
        module = self.load_runtime_module()
        # This capability comes from sealed broker metadata, never from the
        # request or a model tool argument. Resolve it before user interaction
        # so malformed automatic state fails closed before any read port runs.
        automatic_read_scope = service.preparation_read_scope(job.job_id, access_scope=job.scope,
            expected_attempt=job.attempt)
        if automatic_read_scope is not None:
            if not callable(self.command_factory):
                raise RuntimeError('automatic_selected_read_adapter_unavailable')
            command = self.command_factory(service, job, automatic_read_scope)
            if not callable(getattr(command, 'run', None)):
                raise RuntimeError('automatic_selected_read_adapter_invalid')
        else:
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
            try:
                if name == 'log':
                    try:
                        ObserverLogArguments.model_validate(arguments)
                    except ValidationError as error:
                        raise InvalidToolArguments(str(error)) from error
                    payload = {'records': service.log(access_scope=job.scope, **arguments)}
                elif name in SHARED_TOOLS:
                    payload = self.tools(name, arguments, job.scope)
                else:
                    raise ValueError('tool denied')
            except InvalidToolArguments as error:
                payload = {'status': 'denied', 'reason': 'invalid_arguments',
                    'accepted': False, 'dispatched': False, 'tool': name,
                    'validation_error': str(error)[:800],
                    'validation_data': {'is_source_evidence': False},
                    'corrective_action': 'Use the advertised argument schema and retained evidence; correct the arguments before calling again.'}
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
            fence=fence, checkpoint=checkpoint, skills=self.skills)

    def load_runtime_module(self):
        """Return the single canonical observer module after rechecking its source."""
        identity = bind_local_runtime(root=self.runtime_identity.root)
        if identity.fingerprint != self.runtime_identity.fingerprint:
            raise ValueError('observer runtime changed after validation')
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != self.digest:
            raise ValueError('observer runtime changed after validation')
        module = importlib.import_module('local_worker.observer_runtime')
        loaded_path = Path(str(getattr(module, '__file__', ''))).resolve(strict=True)
        if loaded_path != self.path.resolve(strict=True):
            raise ValueError('observer runtime imported from an unexpected path')
        return module


class JobService:
    """A process-lived dispatcher explicitly started by its host lifespan.

    Poll/log never start inference. Multiple processes may use the same local
    SQLite database: leases and generations serialize claims, not GPU policy.
    """
    def __init__(self, directory, *, packets, worker_factory=None, backend=None,
                 hard_limit=100, max_storage_bytes=64 * 1024 * 1024,
                 lease_seconds=120, retry_seconds=None, clock=time.time, freshness_provider=None, inquiry_access=None, can_execute=None,
                 inquiry_context_provider=None, legacy_directory=None,
                 analysis_runtime_identity=None, skill_catalog_identity_provider=None,
                 source_locator_provider=None):
        _load_assistance_components()
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'jobs.sqlite3'
        self._legacy_snapshot_marker_to_write = None
        if legacy_directory is not None:
            self._snapshot_legacy_broker(Path(legacy_directory))
        self.packets, self.worker_factory, self.backend = packets, worker_factory, backend
        self.hard_limit, self.max_storage_bytes = hard_limit, max_storage_bytes
        self.lease_seconds, self.retry_seconds, self.clock = lease_seconds, retry_seconds, clock
        self._stop, self._wake = threading.Event(), threading.Event()
        self._thread = None
        self._threads = []
        self._watchdog = None
        self.freshness_provider = freshness_provider
        self.inquiry_access = inquiry_access
        self.can_execute = can_execute
        self.inquiry_context_provider = inquiry_context_provider
        self.skill_catalog_identity_provider = skill_catalog_identity_provider
        self.source_locator_provider = source_locator_provider
        if analysis_runtime_identity is not None:
            if (not isinstance(analysis_runtime_identity, str)
                    or not analysis_runtime_identity or len(analysis_runtime_identity) > 512):
                raise ValueError('invalid_analysis_runtime_identity')
            # Hash caller supplied identity once so persisted inquiry keys stay bounded.
            analysis_runtime_identity = canonical_digest({'analysis_runtime_identity': analysis_runtime_identity})
        self._analysis_runtime_identity = analysis_runtime_identity
        # Hosts may bind the unified, identity-verifying systemd demand gate.
        # It is intentionally called only after inquiry cache/freshness reads
        # establish that this explicit request needs live analysis.
        self.demand_runtime_ready = None
        self.demand_startup_timeout = 120.0
        self.last_error = None
        self._central_preflight_lock = threading.Lock()
        self._central_preflight_checked = 0.0
        self._central_preflight_allowed = True
        self._central_preflight_error = None
        self._central_preflight_cold = False
        # WAL mode persists; set it once, before dispatch, not on every racing connection.
        with _DB_LOCK:
            with SQLITE_CONNECTION_LOCK:
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
                CREATE TABLE IF NOT EXISTS pa1_child_packets(parent_id TEXT NOT NULL,generation INTEGER NOT NULL,
                    child_id TEXT NOT NULL,packet_id TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(parent_id,generation,child_id));
                CREATE TABLE IF NOT EXISTS execution_slots(job TEXT PRIMARY KEY, attempt INTEGER NOT NULL, lease REAL NOT NULL, owner_pid INTEGER, owner_start TEXT, cleanup_failed INTEGER NOT NULL DEFAULT 0);''')
            db.execute('''CREATE TABLE IF NOT EXISTS stale_execution_cleanup_audit(
                receipt_id TEXT PRIMARY KEY, job TEXT NOT NULL, slot_attempt INTEGER NOT NULL,
                owner_pid INTEGER NOT NULL, owner_start TEXT NOT NULL, release_request_id TEXT NOT NULL,
                proof TEXT NOT NULL, proof_sha256 TEXT NOT NULL, reconciled_at REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS pa1_frame_attempts(
                job TEXT NOT NULL, attempt INTEGER NOT NULL, generation INTEGER NOT NULL,
                policy_id TEXT NOT NULL, requested TEXT NOT NULL, telemetry TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(job,attempt))''')
            db.execute('''CREATE TABLE IF NOT EXISTS pa1_automatic_work(
                job_id TEXT PRIMARY KEY, focus_id TEXT NOT NULL, project TEXT NOT NULL,
                input_fingerprint TEXT NOT NULL, expected_dependencies TEXT NOT NULL,
                window_deadline REAL NOT NULL, identity TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id))''')
            db.execute('BEGIN IMMEDIATE')
            if 'inquiry' not in {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN inquiry INTEGER NOT NULL DEFAULT 0')
                db.execute('UPDATE jobs SET inquiry=1 WHERE id IN (SELECT job FROM inquiry_index)')
            if 'owner_pid' not in {r['name'] for r in db.execute('PRAGMA table_info(execution_slots)')}:
                db.execute('ALTER TABLE execution_slots ADD COLUMN owner_pid INTEGER')
            if 'owner_start' not in {r['name'] for r in db.execute('PRAGMA table_info(execution_slots)')}:
                db.execute('ALTER TABLE execution_slots ADD COLUMN owner_start TEXT')
            if 'cleanup_failed' not in {r['name'] for r in db.execute('PRAGMA table_info(execution_slots)')}:
                db.execute('ALTER TABLE execution_slots ADD COLUMN cleanup_failed INTEGER NOT NULL DEFAULT 0')
            db.execute('CREATE TABLE IF NOT EXISTS broker_migrations(name TEXT PRIMARY KEY)')
            FrameStore.initialize(db)
            PowerPolicy.initialize(db)
            ResourceController.initialize(db)
            frames = FrameStore(db)
            for row in db.execute('SELECT id,record,updated FROM jobs').fetchall():
                if frames.get_frame(row['id']) is None and not frames.is_private(row['id']):
                    legacy = DurableJob.model_validate_json(row['record'])
                    now = self.clock()
                    deadline = max(float(legacy.deadline_epoch or row['updated'] + 300), now + 1)
                    frames.register_root(legacy.job_id, legacy.scope or {}, deadline,
                        max_turns=6, now=min(float(row['updated']), now),
                        origin_class='public_demand')
                    if legacy.status in TERMINAL:
                        frames.record_terminal(legacy.job_id, 0, legacy.status,
                            {'status': legacy.status, 'answer': legacy.answer,
                             'findings': [finding.model_dump() for finding in legacy.findings],
                             'evidence_packets': list(legacy.evidence_packets)}, now=now)
            if not db.execute("SELECT 1 FROM broker_migrations WHERE name='global_inquiry_context_v1'").fetchone():
                rows = db.execute('SELECT jobs.* FROM inquiry_index JOIN jobs ON jobs.id=inquiry_index.job').fetchall()
                # Preserve every job/slot, choosing one canonical cache generation only.
                def preference(row):
                    job = json.loads(row['record'])
                    active = job['status'] not in TERMINAL
                    return (not active, job['created_at'] if active else -row['updated'], row['id'])
                retained = {}
                for row in sorted(rows, key=preference):
                    job = json.loads(row['record'])
                    identity = canonical_digest({'question': job['question'], 'context': self.inquiry_context(job['scope']),
                                                 'mode': job['mode'], 'skill': job.get('skill')})
                    retained.setdefault(identity, row['id'])
                    db.execute('UPDATE jobs SET inquiry=1 WHERE id=?', (row['id'],))
                db.execute('DELETE FROM inquiry_index')
                db.executemany('INSERT INTO inquiry_index(identity,job) VALUES(?,?)', retained.items())
                self._trim_index(db, None)
                db.execute("INSERT INTO broker_migrations VALUES('global_inquiry_context_v1')")

        os.chmod(self.path, 0o600)
        if self._legacy_snapshot_marker_to_write is not None:
            self._write_legacy_snapshot_marker(self._legacy_snapshot_marker_to_write)

    @property
    def analysis_runtime_identity(self):
        """Read-only, process-configured epoch used by inquiry cache keys."""
        return self._analysis_runtime_identity

    def _snapshot_legacy_broker(self, legacy_directory):
        """Publish one quiescent legacy broker snapshot into a fresh namespace."""
        source_path = legacy_directory / 'jobs.sqlite3'
        marker = self.directory / 'legacy-broker-snapshot-v1.complete'
        lock_path = self.directory / 'legacy-broker-snapshot.lock'
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            # Once a destination database exists, never replace it, including
            # an empty database that another process may already have opened.
            if marker.exists():
                if not self.path.exists():
                    raise RuntimeError('legacy_broker_snapshot_missing')
                return
            if self.path.exists():
                self._legacy_snapshot_marker_to_write = marker
                return
            if not source_path.is_file():
                self._legacy_snapshot_marker_to_write = marker
                return

            snapshot = self.directory / f'.legacy-snapshot-{uuid.uuid4().hex}.sqlite3'
            try:
                source = sqlite3.connect(source_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
                target = sqlite3.connect(snapshot, timeout=10)
                try:
                    source.backup(target)
                finally:
                    target.close()
                    source.close()
                if not self._legacy_snapshot_is_quiescent(snapshot):
                    raise RuntimeError('legacy_broker_active')
                os.chmod(snapshot, 0o600)
                # A hard link is an atomic no-replace publish even if another
                # process created or opened an empty destination meanwhile.
                if not self.path.exists():
                    try:
                        os.link(snapshot, self.path)
                    except FileExistsError:
                        pass
                self._legacy_snapshot_marker_to_write = marker
            except RuntimeError:
                raise
            except (OSError, sqlite3.Error, ValueError, TypeError) as error:
                raise RuntimeError('legacy_broker_active') from error
            finally:
                try:
                    snapshot.unlink()
                except FileNotFoundError:
                    pass
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    @staticmethod
    def _write_legacy_snapshot_marker(path):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return
        with os.fdopen(fd, 'w') as stream:
            stream.write('complete\n')
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _legacy_snapshot_is_quiescent(path):
        with sqlite3.connect(path, timeout=10) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'jobs' not in tables:
                return False
            if 'execution_slots' in tables and db.execute('SELECT 1 FROM execution_slots LIMIT 1').fetchone():
                return False
            for row in db.execute('SELECT record FROM jobs'):
                job = json.loads(row[0])
                if job.get('status') not in TERMINAL:
                    return False
        return True

    @contextmanager
    def _db(self):
        # Lock order is broker transactions, then cross-store native lifecycle.
        # The shared lifecycle lock is never held while yielding a transaction.
        with _DB_LOCK:
            with SQLITE_CONNECTION_LOCK:
                db = sqlite3.connect(self.path, timeout=10)
                try:
                    db.row_factory = sqlite3.Row
                    db.execute('PRAGMA synchronous=FULL')
                except Exception:
                    db.close()
                    raise
            try:
                with db:
                    yield db
            finally:
                with SQLITE_CONNECTION_LOCK:
                    db.close()


    def health(self):
        return {'dispatcher': 'running' if self._thread and self._thread.is_alive() else 'stopped',
                'last_error': self.last_error, 'durable': True}

    def demand_work_status(self):
        """Return bounded, content-free demand queue and owned-slot diagnostics."""
        with self._db() as db:
            rows = db.execute("""SELECT id,record,available,updated FROM jobs
                WHERE json_extract(record,'$.status') NOT IN ('completed','partial','failed','cancelled')
                ORDER BY updated,id LIMIT 64""").fetchall()
            slots = db.execute('SELECT job,attempt,owner_pid,owner_start,cleanup_failed FROM execution_slots ORDER BY job LIMIT 16').fetchall()
            slot_count = db.execute('SELECT count(*) FROM execution_slots').fetchone()[0]
            resources = ResourceController(db, power_policy=PowerPolicy(db, clock=self.clock),
                                           clock=self.clock).summary()
        active = []
        now = self.clock()
        for row in rows:
            try:
                job = json.loads(row['record'])
                active.append({'job_id': row['id'], 'mode': job.get('mode'),
                    'status': job.get('status'), 'attempt': job.get('attempt'),
                    'wait_reason': _public_wait_reason(job.get('failure_reason')),
                    'available_in_seconds': round(max(0.0, float(row['available']) - now), 2)})
            except (TypeError, ValueError, AttributeError):
                active.append({'job_id': row['id'], 'status': 'unavailable',
                               'wait_reason': 'job_record_invalid'})
        return {'active_work': active, 'active_work_truncated': len(rows) >= 64,
            'active_execution_slots': [{'job_id': row['job'], 'attempt': row['attempt'],
                'cleanup_pending': bool(row['cleanup_failed']),
                'owner_pid': row['owner_pid'], 'owner_process_start': row['owner_start']}
                for row in slots], 'active_execution_slots_truncated': slot_count > len(slots),
            'owned_resources': resources,
            'dispatcher': self.health()['dispatcher']}

    def release_owned_resources(self, control, *, callback=None, declared_end=None,
                                reason='operator-requested'):
        """Persist release veto before asking the current supervisor to release owned sessions.

        control is a trusted host capability, never request or model data. The
        callback is optional for hosts that reconcile release separately;
        without it the durable veto remains pending.
        """
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            policy = PowerPolicy(db, clock=self.clock)
            intent = policy.set_release(control, declared_end=declared_end, reason=reason)
        return self._deliver_owned_release(intent, callback=callback)

    def reconcile_stale_execution_slot(self, control, *, job_id, attempt,
                                       owner_pid, owner_start, proof):
        """Remove one terminal job's orphaned slot after trusted host proof.

        This is an operator-only recovery path for slots whose ordinary cleanup
        failed.  It never treats missing host/resource observations as release
        evidence, and it preserves the durable job record and proof receipt.
        """
        PowerPolicy._authorized(control, 'request_release')
        if (not isinstance(job_id, str) or not job_id or len(job_id) > 256
                or type(attempt) is not int or attempt <= 0
                or type(owner_pid) is not int or owner_pid <= 0
                or not isinstance(owner_start, str) or not owner_start or len(owner_start) > 128):
            raise ValueError('stale_execution_slot_identity_invalid')

        # Missing / unreadable / reused owners are all unverifiable.  Only the
        # kernel's explicit ESRCH result proves this exact PID is now absent.
        if self._process_start(owner_pid) is not None:
            raise ValueError('stale_execution_slot_owner_present_or_reused')
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            pass
        except (PermissionError, OSError) as error:
            raise ValueError('stale_execution_slot_owner_absence_unverifiable') from error
        else:
            raise ValueError('stale_execution_slot_owner_absence_unverifiable')

        if not isinstance(proof, dict):
            raise ValueError('stale_execution_cleanup_proof_required')
        required = {'format', 'job_id', 'attempt', 'owner_pid', 'owner_start', 'observed_at',
            'release_manifest_sha256', 'helper', 'owner_process_absent', 'gpu_uuids',
            'compute_processes', 'gpu_memory_mib', 'host_conflicts', 'native',
            'process_released', 'memory_released', 'verified'}
        if set(proof) != required:
            raise ValueError('stale_execution_cleanup_proof_fields_invalid')
        now = float(self.clock())
        observed_at = proof.get('observed_at')
        if (proof.get('format') != 'PC-AS1-STALE-EXECUTION-CLEANUP/1'
                or proof.get('job_id') != job_id or type(proof.get('attempt')) is not int
                or proof.get('attempt') != attempt or type(proof.get('owner_pid')) is not int
                or proof.get('owner_pid') != owner_pid or proof.get('owner_start') != owner_start
                or isinstance(observed_at, bool) or not isinstance(observed_at, (int, float))
                or not math.isfinite(float(observed_at)) or not now - 60 <= float(observed_at) <= now + 5):
            raise ValueError('stale_execution_cleanup_proof_identity_or_freshness_invalid')
        from .runtime_binding import RELEASE_DIGEST_VARIABLE
        expected_release_digest = os.environ.get(RELEASE_DIGEST_VARIABLE)
        if (not isinstance(expected_release_digest, str)
                or not re.fullmatch(r'[0-9a-f]{64}', expected_release_digest)
                or proof.get('release_manifest_sha256') != expected_release_digest):
            raise ValueError('stale_execution_cleanup_release_pin_mismatch')
        helper = proof.get('helper')
        if (not isinstance(helper, dict)
                or set(helper) != {'unit', 'active_state', 'main_pid', 'kill_mode'}
                or type(helper.get('main_pid')) is not int
                or helper != {'unit': 'project-control-inference.service', 'active_state': 'inactive',
                              'main_pid': 0, 'kill_mode': 'control-group'}):
            raise ValueError('stale_execution_cleanup_helper_state_invalid')
        if (proof.get('owner_process_absent') is not True
                or proof.get('process_released') is not True
                or proof.get('memory_released') is not True
                or proof.get('verified') is not True
                or proof.get('compute_processes') != []
                or proof.get('host_conflicts') != []):
            raise ValueError('stale_execution_cleanup_release_unproved')
        native = proof.get('native')
        if (not isinstance(native, dict) or set(native) != {'active_leases', 'session_count'}
                or type(native.get('active_leases')) is not int
                or type(native.get('session_count')) is not int
                or native != {'active_leases': 0, 'session_count': 0}):
            raise ValueError('stale_execution_cleanup_native_state_invalid')
        allowed_gpu_uuids = getattr(self.backend, '_allowed_gpu_uuids', None)
        gpu_uuids = proof.get('gpu_uuids')
        memory = proof.get('gpu_memory_mib')
        if (not isinstance(allowed_gpu_uuids, (list, tuple)) or not allowed_gpu_uuids
                or any(not isinstance(item, str) or not item.startswith('GPU-') for item in allowed_gpu_uuids)
                or not isinstance(gpu_uuids, list)
                or any(not isinstance(item, str) for item in gpu_uuids)
                or set(gpu_uuids) != set(allowed_gpu_uuids)
                or len(gpu_uuids) != len(set(gpu_uuids))
                or not isinstance(memory, dict) or set(memory) != set(allowed_gpu_uuids)
                or any(type(memory[item]) is not int or memory[item] != 0 for item in allowed_gpu_uuids)):
            raise ValueError('stale_execution_cleanup_gpu_scope_or_memory_invalid')

        proof_text = wire(proof)
        proof_digest = hashlib.sha256(proof_text.encode('utf-8')).hexdigest()

        # Current physical release must carry a ResourceController-issued
        # acknowledgement for this exact intent. Verify it on committed rows
        # before taking the write transaction; the transaction below rechecks
        # the persisted acknowledgment and target rows to fence a concurrent
        # intent change. Older released rows remain valid history and are
        # intentionally outside the current intent's acknowledged target set.
        from .assistance.power import ReleaseIntent
        verified_release_ack = None
        with self._db() as check_db:
            check_policy = PowerPolicy(check_db, clock=self.clock)
            check_resources = ResourceController(check_db, power_policy=check_policy,
                clock=self.clock)
            check_db.commit()
            check_power = check_policy.snapshot()
            if check_power.get('physical_state') == 'released_verified':
                try:
                    check_intent = ReleaseIntent(check_power['release_request_id'],
                        float(check_power['release_created_at']), check_power['release_until'],
                        check_power['release_reason'])
                    verified_release_ack = check_resources.verified_release_ack(check_intent)
                except Exception:
                    # The locked transaction below classifies outstanding
                    # resource rows first, then rejects a missing or invalid
                    # current-intent acknowledgment without changing state.
                    verified_release_ack = None

        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            job_row = db.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()
            if job_row is None:
                raise ValueError('stale_execution_slot_job_missing')
            job = DurableJob.model_validate_json(job_row['record'])
            if job.status not in TERMINAL or job.attempt < attempt:
                raise ValueError('stale_execution_slot_job_not_terminal_or_generation_invalid')
            slot = db.execute('''SELECT attempt,owner_pid,owner_start,cleanup_failed
                FROM execution_slots WHERE job=?''', (job_id,)).fetchone()
            if (slot is None or slot['attempt'] != attempt or slot['owner_pid'] != owner_pid
                    or slot['owner_start'] != owner_start or not slot['cleanup_failed']):
                raise ValueError('stale_execution_slot_exact_owner_mismatch')

            policy = PowerPolicy(db, clock=self.clock)
            power = policy.snapshot()
            if (not power['release_veto_active'] or power['release_until'] is not None
                    or not power['release_request_id']):
                raise ValueError('stale_execution_slot_permanent_release_veto_required')
            resources = ResourceController(db, power_policy=policy, clock=self.clock).snapshot()
            if any(item['state'] not in {'released_verified', 'superseded'} for item in resources):
                raise ValueError('stale_execution_slot_owned_resources_not_released')
            current_targets = sorted(item['session_id'] for item in resources
                if item['state'] == 'released_verified'
                and item['release_request_id'] == power['release_request_id'])
            current_targets_digest = hashlib.sha256(wire(current_targets).encode('utf-8')).hexdigest()
            verified_at = power.get('release_verified_at')
            if (verified_release_ack is None
                    or power['physical_state'] != 'released_verified'
                    or verified_release_ack.request_id != power['release_request_id']
                    or sorted(verified_release_ack.target_session_ids) != current_targets
                    or not current_targets
                    or power.get('release_verified_sessions') != len(current_targets)
                    or power.get('release_verified_proof_digest') != verified_release_ack.proof_digest
                    or power.get('release_verified_targets_digest') != current_targets_digest
                    or isinstance(verified_at, bool) or not isinstance(verified_at, (int, float))
                    or not math.isfinite(float(verified_at)) or verified_at > now + 5):
                raise ValueError('stale_execution_slot_owned_resource_release_mismatch')

            # Recheck at the final mutation edge so a PID reused after proof
            # validation cannot be mistaken for the historical dead owner.
            if self._process_start(owner_pid) is not None:
                raise ValueError('stale_execution_slot_owner_present_or_reused')
            try:
                os.kill(owner_pid, 0)
            except ProcessLookupError:
                pass
            except (PermissionError, OSError) as error:
                raise ValueError('stale_execution_slot_owner_absence_unverifiable') from error
            else:
                raise ValueError('stale_execution_slot_owner_absence_unverifiable')

            deleted = db.execute('''DELETE FROM execution_slots WHERE job=? AND attempt=?
                AND owner_pid=? AND owner_start=? AND cleanup_failed=1''',
                (job_id, attempt, owner_pid, owner_start))
            if deleted.rowcount != 1:
                raise ValueError('stale_execution_slot_exact_owner_mismatch')
            receipt_id = uuid.uuid4().hex
            db.execute('''INSERT INTO stale_execution_cleanup_audit
                (receipt_id,job,slot_attempt,owner_pid,owner_start,release_request_id,
                 proof,proof_sha256,reconciled_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                (receipt_id, job_id, attempt, owner_pid, owner_start,
                 power['release_request_id'], proof_text, proof_digest, now))
        return {'status': 'reconciled', 'receipt_id': receipt_id,
                'job_id': job_id, 'attempt': attempt, 'proof_sha256': proof_digest}

    def coordinate_demand_stop(self, control, *, timeout=95.0):
        """Cancel broker work, then prove its exact owned resources released.

        This is the callback used by the unified demand-runtime stop operation.
        A durable release veto is committed first, so neither automatic nor
        foreground work can rewarm while cancellation and owner cleanup finish.
        The supervisor remains running whenever logical cancellation or exact
        physical release cannot be proved within the bounded wait.
        """
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
            raise ValueError('demand_stop_timeout_out_of_bounds')
        # Committing the veto before touching jobs closes the race with the
        # dispatcher: any later claim fails its native power-policy admission.
        with self._db() as db:
            policy = PowerPolicy(db, clock=self.clock)
            release_state = policy.snapshot()
        if release_state.get('release_veto_active'):
            outcome = self._reconcile_owned_release()
        else:
            outcome = self.release_owned_resources(control, reason='explicit-demand-stop')
        deadline = time.monotonic() + float(timeout)
        cancelled = self._cancel_active_work_for_stop()
        self._wake.set()
        settled = False
        while time.monotonic() < deadline:
            self._reconcile_owned_release()
            active = self._active_work_snapshot()
            if active['active_jobs'] == 0 and not active['execution_slots']:
                settled = True
                break
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        snapshot = self._active_work_snapshot()
        cancelled = cancelled and snapshot['active_jobs'] == 0
        try:
            with self._db() as db:
                state = PowerPolicy(db, clock=self.clock).snapshot()
                policy = PowerPolicy(db, clock=self.clock)
                controller = ResourceController(db, power_policy=policy, clock=self.clock)
                resources = controller.snapshot()
            unresolved = any(item.get('state') in {'active', 'idle_owned', 'release_pending', 'stale'}
                             for item in resources)
            records_proved = (not unresolved and (state.get('physical_state') == 'released_verified'
                or all(item.get('state') in {'released_verified', 'superseded'} for item in resources)))
            current = self._central_quiescent_snapshot()
            slots = current.get('slots') if current is not None else None
            cleanup_proved = current is not None and slots == []
            release_idle = getattr(self.backend, 'release_idle_runtime', None)
            stale_targets = []
            stale_intent = None
            if (unresolved and settled and snapshot['active_jobs'] == 0
                    and snapshot['execution_slots'] == 0
                    and current is not None and slots and state.get('release_veto_active') is True
                    and state.get('release_until') is None and state.get('physical_state') == 'pending'
                    and isinstance(state.get('release_request_id'), str)):
                from .assistance.power import ReleaseIntent
                stale_intent = ReleaseIntent(state['release_request_id'],
                    float(state['release_created_at']), state['release_until'],
                    state.get('release_reason') or 'operator-requested')
                with self._db() as db:
                    policy = PowerPolicy(db, clock=self.clock)
                    controller = ResourceController(db, power_policy=policy, clock=self.clock)
                    stale_targets = controller.stale_sessions_matching_idle_owner(stale_intent, current)
            if (records_proved or stale_targets) and current is not None and slots:
                # Idle warm slots are quiescent, not physically released.
                # Ask the selected supervisor to stop only its own idle slots.
                # Its response must pin the pre-stop owner and prove a physical
                # cleanup receipt for every slot. The CPU daemon is stopping,
                # so a post-stop central_status call would be invalid evidence.
                if callable(release_idle):
                    remaining = max(0.0, deadline - time.monotonic())
                    if remaining > 0:
                        receipt = release_idle(deadline_epoch=time.time() + remaining)
                        cleanup = receipt.get('cleanup_receipts') if isinstance(receipt, dict) else None
                        expected = set()
                        for slot in slots:
                            owner_id = slot.get('owner_id')
                            owned_pid = slot.get('server_pid')
                            gpu_uuids = slot.get('gpu_uuids')
                            if (not isinstance(owner_id, str) or not owner_id
                                    or type(owned_pid) is not int or owned_pid <= 0
                                    or not isinstance(gpu_uuids, list) or not gpu_uuids
                                    or any(not isinstance(uuid, str) or not uuid for uuid in gpu_uuids)):
                                expected = set()
                                break
                            key = (owner_id, owned_pid, tuple(sorted(set(gpu_uuids))))
                            if key in expected:
                                expected = set()
                                break
                            expected.add(key)
                        observed = set()
                        receipts_valid = isinstance(cleanup, list) and len(cleanup) == len(slots)
                        if receipts_valid:
                            for item in cleanup:
                                if not isinstance(item, dict):
                                    receipts_valid = False
                                    break
                                gpu_uuids = item.get('gpu_uuids')
                                key = (item.get('owner_id'), item.get('owned_pid'),
                                    tuple(sorted(set(gpu_uuids))) if isinstance(gpu_uuids, list)
                                    and all(isinstance(uuid, str) for uuid in gpu_uuids) else ())
                                if (item.get('released') is not True
                                        or item.get('process_released') is not True
                                        or item.get('memory_released') is not True
                                        or key not in expected or key in observed):
                                    receipts_valid = False
                                    break
                                observed.add(key)
                        cleanup_proved = (isinstance(receipt, dict)
                            and receipt.get('status') == 'released_verified'
                            and receipt.get('released_verified') is True
                            and receipt.get('supervisor_pid') == current.get('supervisor_pid')
                            and receipt.get('supervisor_process_start') == current.get('supervisor_process_start')
                            and receipt.get('daemon_epoch') == current.get('daemon_epoch')
                            and receipt.get('runtime_fingerprint') == current.get('runtime_fingerprint')
                            and receipt.get('quiescent') is True
                            and receipt.get('evicted') is True
                            and receipt.get('stopped') is True
                            and bool(expected) and receipts_valid and observed == expected)
                        if cleanup_proved and stale_targets and stale_intent is not None:
                            try:
                                with self._db() as db:
                                    policy = PowerPolicy(db, clock=self.clock)
                                    controller = ResourceController(db, power_policy=policy, clock=self.clock)
                                    db.commit()
                                    controller.record_stale_sessions_after_physical_release(
                                        control, stale_intent, receipt, stale_targets)
                                acknowledged = self._ack_owned_release(stale_intent)
                                if acknowledged:
                                    outcome = 'released_verified'
                                with self._db() as db:
                                    policy = PowerPolicy(db, clock=self.clock)
                                    resources = ResourceController(db, power_policy=policy,
                                        clock=self.clock).snapshot()
                                    state = policy.snapshot()
                                unresolved = any(item.get('state') in
                                    {'active', 'idle_owned', 'release_pending', 'stale'} for item in resources)
                                records_proved = (not unresolved and state.get('physical_state') == 'released_verified')
                            except Exception as error:
                                self.last_error = f"{type(error).__name__}:{error}"
                                cleanup_proved = False
            final_work = self._active_work_snapshot()
            owner_proof = (records_proved and cleanup_proved
                and final_work['active_jobs'] == 0
                and final_work['execution_slots'] == 0)
        except Exception as error:
            self.last_error = type(error).__name__
            owner_proof = False
        return {'active_work_cancelled': bool(cancelled and settled),
                'owned_resources_released': bool(owner_proof),
                'release_outcome': outcome,
                'active_jobs': snapshot['active_jobs'],
                'execution_slots': snapshot['execution_slots']}

    def _active_work_snapshot(self):
        """Read bounded active-work identities and physical execution slots."""
        with self._db() as db:
            count = db.execute("SELECT count(*) FROM jobs WHERE json_extract(record,'$.status') NOT IN ('completed','partial','failed','cancelled')").fetchone()[0]
            slots = db.execute('SELECT count(*) FROM execution_slots').fetchone()[0]
        return {'active_jobs': int(count), 'execution_slots': int(slots)}

    def _central_quiescent_snapshot(self):
        """Read exact owner state and accept only zero-lease idle slots."""
        status = getattr(self.backend, 'central_status', None)
        current = status(deadline_epoch=time.time() + 3) if callable(status) else None
        slots = current.get('slots') if isinstance(current, dict) else None
        if not (isinstance(current, dict)
                and current.get('status') in (None, 'available')
                and type(current.get('supervisor_pid')) is int
                and current.get('supervisor_pid') > 0
                and isinstance(current.get('daemon_epoch'), str)
                and len(current.get('daemon_epoch')) == 64
                and isinstance(current.get('runtime_fingerprint'), str)
                and len(current.get('runtime_fingerprint')) == 64
                and type(current.get('active_leases')) is int
                and current.get('active_leases') == 0
                and type(current.get('active_admissions')) is int
                and current.get('active_admissions') == 0
                and isinstance(slots, list)
                and len(slots) <= 4
                and all(isinstance(slot, dict) and slot.get('leased') is False
                        and slot.get('state') in {'idle', 'ready'} for slot in slots)
                and isinstance(current.get('supervisor_process_start'), str)
                and bool(current.get('supervisor_process_start'))
                and isinstance(current.get('source_sha256'), str)
                and len(current.get('source_sha256')) == 64):
            return None
        return current

    @staticmethod
    def _same_supervisor_owner(before, after):
        return all(before.get(key) == after.get(key) for key in (
            'supervisor_pid', 'supervisor_process_start', 'daemon_epoch',
            'runtime_fingerprint', 'source_sha256'))

    def _cancel_active_work_for_stop(self):
        """Cancel exact public roots and opted-in preparation roots by service APIs."""
        with self._db() as db:
            rows = db.execute("""SELECT j.id,j.scope,j.record,
                EXISTS(SELECT 1 FROM pa1_automatic_work a WHERE a.job_id=j.id) automatic
                FROM jobs j
                WHERE json_extract(j.record,'$.status') NOT IN ('completed','partial','failed','cancelled')
                  AND NOT EXISTS(SELECT 1 FROM pa1_private_ids p WHERE p.job_id=j.id)
                ORDER BY j.updated,j.id LIMIT 256""").fetchall()
            active_roots = db.execute("""SELECT count(*) FROM jobs j
                WHERE json_extract(j.record,'$.status') NOT IN ('completed','partial','failed','cancelled')
                  AND NOT EXISTS(SELECT 1 FROM pa1_private_ids p WHERE p.job_id=j.id)""").fetchone()[0]
        okay = active_roots <= len(rows)
        for row in rows:
            try:
                scope = json.loads(row['scope'])
                if row['automatic']:
                    accepted = self.cancel_preparation(row['id'], access_scope=scope)
                else:
                    accepted = self.cancel(row['id'], access_scope=scope)
                # A concurrent terminal transition is safe only if the exact
                # row is now terminal; verify through the supported lookup.
                if not accepted:
                    current = self.lookup(row['id'], access_scope=scope)
                    accepted = current.get('status') == 'ok' and current.get('job', {}).get('status') in TERMINAL
                okay = okay and bool(accepted)
            except Exception:
                okay = False
        return okay

    def _ensure_demand_runtime(self, startup_timeout=None):
        """Start and verify the unified runtime for a fresh explicit demand."""
        ensure = self.demand_runtime_ready
        if not callable(ensure):
            return None
        timeout = self.demand_startup_timeout if startup_timeout is None else startup_timeout
        timeout = self._coerce_startup_timeout(timeout)
        if timeout == 0:
            return {'status': 'unavailable', 'reason': 'demand_deadline_exhausted_before_start'}
        deadline_epoch = self.clock() + float(timeout)
        try:
            receipt = ensure(deadline_epoch=deadline_epoch)
        except Exception as error:
            reason = str(error).strip()[:160] or type(error).__name__
            return {'status': 'unavailable', 'reason': reason}
        if not isinstance(receipt, dict):
            return {'status': 'unavailable', 'reason': 'demand_runtime_not_ready'}
        if receipt.get('status') != 'ready':
            return {'status': 'unavailable',
                'reason': _demand_readiness_failure_reason(receipt)}
        return None

    @staticmethod
    def _coerce_startup_timeout(timeout):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError('invalid_startup_timeout')
        try:
            timeout = float(timeout)
        except (OverflowError, TypeError, ValueError):
            raise ValueError('invalid_startup_timeout') from None
        if not math.isfinite(timeout) or not 0 <= timeout <= 120:
            raise ValueError('invalid_startup_timeout')
        return timeout

    def _dispatch_gate(self, job_id, expected_attempt):
        """Revalidate job generation and canonical operator policy at each admission edge."""
        with self._db() as db:
            row = db.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                return False, 'stale_attempt'
            current = DurableJob.model_validate_json(row['record'])
            if current.attempt != expected_attempt or current.status != 'running':
                return False, 'stale_attempt'
            frame = FrameStore(db).get_frame(job_id)
            if frame is None:
                return True, None
            policy = PowerPolicy(db, clock=self.clock)
            origin = policy.root_demand_origin(frame.root_id)
            frame_class = ('automatic_root' if frame.origin_class == 'automatic' and frame.parent_id is None
                           else 'public_root' if frame.parent_id is None else 'private_child')
            automatic_work = (db.execute('SELECT * FROM pa1_automatic_work WHERE job_id=?',
                (frame.root_id,)).fetchone() if origin.kind == 'automatic' else None)
            permission = (trusted_operator_control().permission('dispatch_automatic')
                          if origin.kind == 'automatic' else None)
            decision = policy.allow_dispatch(frame_class, True, root_id=frame.root_id,
                demand_origin=origin, permission=permission,
                focus_id=automatic_work['focus_id'] if automatic_work else None,
                project=automatic_work['project'] if automatic_work else None)
            return decision.allowed, decision.reason

    def resume_owned_resources(self, control):
        """Clear quiet/release veto through the trusted local operator path."""
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            PowerPolicy(db, clock=self.clock).resume(control)

    def _owned_release_callback(self, callback=None):
        if callable(callback):
            return callback
        checked_client = getattr(self.backend, '_checked_client', None)
        if callable(checked_client):
            def release(request_id, reason, records):
                deadline = time.time() + 10
                client = checked_client(deadline)
                method = getattr(client, 'release_owned_observer_resources', None)
                if not callable(method):
                    raise RuntimeError('owned_release_rpc_unavailable')
                return method(list(records), request_id=request_id, deadline_epoch=deadline)
            return release
        method = getattr(self.backend, 'release_owned_observer_resources', None)
        if callable(method):
            # Test/host adapters may implement ResourceController's direct
            # callback shape rather than expose a SupervisorClient.
            return method
        return None

    def _deliver_owned_release(self, intent, *, callback=None):
        sender = self._owned_release_callback(callback)
        if sender is None:
            return 'pending_owner_unavailable'
        # The release intent was committed by its caller. Keep the callback
        # outside the broker lock and outside every open write transaction;
        # ResourceController commits only its post-callback proof projection.
        with _DB_LOCK:
            with SQLITE_CONNECTION_LOCK:
                db = sqlite3.connect(self.path, timeout=10)
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA synchronous=FULL')
        try:
            controller = ResourceController(db, power_policy=PowerPolicy(db, clock=self.clock), clock=self.clock)
            db.commit()
            try:
                outcome = controller.release_owned(intent, sender)
            except Exception as error:
                self.last_error = type(error).__name__
                outcome = 'pending_owner_verification'
            if db.in_transaction:
                db.commit()
        finally:
            with SQLITE_CONNECTION_LOCK:
                db.close()
        if outcome == 'released_verified' and not self._ack_owned_release(intent):
            outcome = 'pending_reconciliation_required'
        self._wake.set()
        return outcome

    def _ack_owned_release(self, intent):
        """Commit the power-policy release state from already committed proof rows."""
        with _DB_LOCK:
            with SQLITE_CONNECTION_LOCK:
                db = sqlite3.connect(self.path, timeout=10)
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA synchronous=FULL')
        try:
            policy = PowerPolicy(db, clock=self.clock)
            controller = ResourceController(db, power_policy=policy, clock=self.clock)
            db.commit()
            controller.acknowledge_verified_release(intent)
            if db.in_transaction:
                db.commit()
            return policy.snapshot().get('physical_state') == 'released_verified'
        except Exception as error:
            self.last_error = type(error).__name__
            if db.in_transaction:
                db.rollback()
            return False
        finally:
            with SQLITE_CONNECTION_LOCK:
                db.close()

    def _reconcile_owned_release(self):
        """Retry the latest durable release intent after a session has closed."""
        with self._db() as db:
            row = db.execute('SELECT release_request_id,release_until,release_reason,updated,release_veto '
                'FROM pa1_power_state WHERE singleton=1').fetchone()
            if not row or not row['release_veto'] or not row['release_request_id']:
                return None
            from .assistance.power import ReleaseIntent
            intent = ReleaseIntent(row['release_request_id'], float(row['updated']),
                row['release_until'], row['release_reason'] or 'operator-requested')
            from .assistance.resources import ResourceController
            resources = ResourceController(db, power_policy=PowerPolicy(db, clock=self.clock),
                                           clock=self.clock)
            reclaimable_sessions = resources.reclaimable_session_ids(intent.request_id)
        # A CLI frontend can die between observer-open and writing the minted
        # close receipt. The supervisor first proves that borrower's exact
        # process generation is gone; only then can it return its one-shot
        # receipt to this broker for ordinary ResourceController validation.
        reclaim = getattr(self.backend, 'reclaim_orphaned_sessions', None)
        if reclaimable_sessions and callable(reclaim):
            with self._db() as db:
                try:
                    resources = ResourceController(db, power_policy=PowerPolicy(db, clock=self.clock),
                                                   clock=self.clock)
                    reclaim(resources, release_request_id=intent.request_id)
                except Exception as error:
                    self.last_error = type(error).__name__
                    if db.in_transaction:
                        db.rollback()
        with self._db() as db:
            completed = db.execute("SELECT count(*) FROM pa1_owned_resource_sessions "
                "WHERE release_request_id=? AND state='released_verified'", (intent.request_id,)).fetchone()[0]
            pending = db.execute("SELECT physical_state FROM pa1_power_state WHERE singleton=1").fetchone()[0]
        if completed and pending != 'released_verified':
            return 'released_verified' if self._ack_owned_release(intent) else 'pending_reconciliation_required'
        return self._deliver_owned_release(intent)

    def start(self):
        if self.worker_factory is None:
            raise RuntimeError('durable_processing_unavailable')
        if not self._thread or not self._thread.is_alive():
            self._stop.clear()
            self._threads = [threading.Thread(target=self._drain, name=f'pc-job-dispatch-{i}', daemon=True) for i in range(2)]
            self._thread = self._threads[0]
            for thread in self._threads:
                thread.start()
            # Logical deadlines must progress even when both dispatcher threads
            # are blocked inside a non-cooperative worker call. Resource slots
            # remain occupied until those calls return and cleanup is proved.
            self._watchdog = threading.Thread(target=self._watchdog_loop,
                name='pc-job-deadline-watchdog', daemon=True)
            self._watchdog.start()
        return self

    def shutdown(self, timeout=95):
        self._stop.set(); self._wake.set()
        end = time.monotonic() + timeout
        for thread in [*self._threads, *([self._watchdog] if self._watchdog else [])]:
            thread.join(max(0, end-time.monotonic()))
        return not any(thread.is_alive() for thread in [*self._threads, *([self._watchdog] if self._watchdog else [])])

    def _watchdog_loop(self):
        """Fence expired generations independently of worker responsiveness.

        This only expires logical work. It deliberately leaves execution_slots
        in place: a blocked call still owns its physical session until its own
        finally block proves cleanup.
        """
        while not self._stop.wait(.1):
            try:
                self._expire_deadlines()
                self._consume_pending_frame_acks()
            except Exception as error:
                self.last_error = type(error).__name__

    def _expire_deadlines(self):
        now = self.clock()
        expired = []
        expired_children = False
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            frames = FrameStore(db)
            rows = db.execute("""SELECT * FROM jobs
                WHERE json_extract(record,'$.status') NOT IN ('completed','partial','failed','cancelled')
                  AND json_extract(record,'$.deadline_epoch') IS NOT NULL
                  AND json_extract(record,'$.deadline_epoch')<=?""", (now,)).fetchall()
            for row in rows:
                job = DurableJob.model_validate_json(row['record'])
                # A bumped attempt fences every late packet/checkpoint/answer.
                job.attempt += 1
                job.status = 'partial' if job.findings or job.evidence_packets else 'failed'
                job.unresolved_questions = ['The analysis deadline expired before the current worker generation completed.']
                job.failure_reason = job.failure_reason or 'deadline_exhausted'
                job.terminal_reason = 'deadline_exhausted'
                db.execute('UPDATE jobs SET record=?,lease=0,updated=? WHERE id=?',
                    (job.model_dump_json(), now, job.job_id))
                frame = frames.get_frame(job.job_id)
                if frame is not None:
                    self._cancel_descendants(db, frames, job.job_id, now)
                    frames.record_terminal(job.job_id, frame.generation, 'expired',
                        {'status': 'expired', 'reason': 'deadline_exhausted',
                         'answer': job.answer, 'findings': [finding.model_dump() for finding in job.findings],
                         'evidence_packets': list(job.evidence_packets)}, now=now)
                    expired_children |= frame.parent_id is not None
                self._trim_index(db, job.scope)
                expired.append(job.job_id)
        if expired:
            # Packet retention reconciliation is outside the transaction. Slot
            # rows are intentionally retained until physical cleanup completes.
            self.reconcile()
            if expired_children:
                self._wake_ready_parents()
            self._wake.set()
        return tuple(expired)

    def _consume_pending_frame_acks(self):
        """Commit a validated wake only after physical parent-slot release."""
        wake_parent = False
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            frames = FrameStore(db)
            waits = db.execute('SELECT parent_id,generation FROM pa1_frame_waits WHERE pending_ack IS NOT NULL').fetchall()
            for wait in waits:
                parent_id, generation = wait['parent_id'], int(wait['generation'])
                if db.execute('SELECT 1 FROM execution_slots WHERE job=? LIMIT 1', (parent_id,)).fetchone():
                    continue
                outcome = frames.reconcile(parent_id, generation)
                ack = frames.pending_wake_ack(parent_id, generation)
                expected = tuple(sorted(child.job_id for child in outcome.children)) if outcome and outcome.ready else ()
                if not outcome or not outcome.ready or ack != expected or outcome.wake_version is None:
                    continue
                consumed = frames.consume_wake(parent_id, generation, outcome.wake_version)
                if consumed is None:
                    continue
                row = db.execute('SELECT record FROM jobs WHERE id=?', (parent_id,)).fetchone()
                if not row:
                    continue
                job = DurableJob.model_validate_json(row['record'])
                if job.status in TERMINAL:
                    frame = frames.get_frame(parent_id)
                    if frame is not None:
                        visible = {'status': job.status, 'answer': job.answer,
                            'findings': [finding.model_dump() for finding in job.findings],
                            'unresolved_questions': job.unresolved_questions,
                            'reason': job.terminal_reason, 'result_packet': job.result_packet,
                            'evidence_packets': list(job.evidence_packets)}
                        frames.record_terminal(parent_id, generation,
                            'expired' if job.terminal_reason in {'deadline_exhausted', 'attempt_or_deadline_exhausted'} else job.status,
                            visible, now=self.clock())
                        wake_parent |= frame.parent_id is not None
            terminal_waits = db.execute("""SELECT w.parent_id,w.generation FROM pa1_frame_waits w
                JOIN jobs j ON j.id=w.parent_id
                WHERE w.pending_ack IS NULL AND json_extract(j.record,'$.status') IN ('completed','partial','failed','cancelled')
                AND w.state!='retired'""").fetchall()
            for wait in terminal_waits:
                frames.retire_wait(wait['parent_id'], int(wait['generation']))
        if wake_parent:
            self._wake_ready_parents()

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
            return {'accepted': False, 'reason': 'inquiry_too_large'}
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
                        if not self._inquiry_authorized(previous.model_dump(), scope):
                            return {'accepted': False, 'reason': 'access_unavailable'}
                        if previous.status not in TERMINAL or db.execute('SELECT 1 FROM execution_slots WHERE job=?', (previous.job_id,)).fetchone():
                            return self._accepted(previous, False, retry=True)
                        if not self._cache_eligible(previous.model_dump()):
                            return self._accepted(previous, False, retry=True)
                        if (_freshness_snapshot is None or _freshness_snapshot['job_id'] != old['id']
                                or _freshness_snapshot['record'] != old['record']):
                            return {'accepted': False, 'reason': 'inquiry_changed'}
                        freshness = _freshness_snapshot['freshness']
                        if previous.status in {'completed', 'partial'} and freshness.get('fresh'):
                            return self._accepted(previous, False, retry=True)
                        # Preserve original material access requirements across every refresh.
                        job.hints = list(dict.fromkeys(previous.hints + job.hints))
                        hints = job.hints
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
                if (mode == 'skill' and skill is not None
                        and skill not in getattr(self.worker_factory, 'skills', {})):
                    return {'accepted': False, 'reason': 'unregistered_skill'}
                # Pin before admission so concurrent packet GC cannot expire accepted inputs.
                # The owner is unique; unsuccessful admission releases its temporary pins.
                for ref in hints:
                    if self.packets.lookup(ref, access_scope=scope).status != 'ok':
                        return {'accepted': False, 'reason': 'hint_unavailable'}
                if hints:
                    self.packets.pin(job.job_id, hints)
                    pinned = True
                count = db.execute("""SELECT count(*) FROM jobs j LEFT JOIN pa1_frames f ON f.job_id=j.id
                    WHERE (f.parent_id IS NULL) AND NOT EXISTS(SELECT 1 FROM pa1_private_ids p WHERE p.job_id=j.id) AND
                    (json_extract(j.record,'$.status') NOT IN ('completed','partial','failed','cancelled')
                     OR EXISTS(SELECT 1 FROM execution_slots s WHERE s.job=j.id))""").fetchone()[0]
                usage = self._storage_bytes(db)
                if count >= min(6, self.hard_limit):
                    return {'accepted': False, 'reason': 'admission_limit'}
                if usage + len(wire(job.model_dump()).encode()) > self.max_storage_bytes:
                    return {'accepted': False, 'reason': 'storage_limit'}
                db.execute('INSERT INTO jobs(id,scope,request_id,request_hash,record,updated,inquiry) VALUES(?,?,?,?,?,?,?)',
                    (job.job_id, wire(scope), job.request_id, request_hash, job.model_dump_json(), self.clock(), int(_identity is not None)))
                FrameStore(db).register_root(job.job_id, scope, job.deadline_epoch,
                    max_turns=6, now=self.clock(), origin_class='public_demand')
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

    def enqueue_preparation(self, question, access_scope, focus_id, input_fingerprint,
                            window_deadline, expected_dependencies=None):
        """Admit one trusted, private automatic preparation under the live focus window.

        This is an internal controller API. It has no public inquiry index,
        retry identity, poll capability, or answer-cache entry. The persisted
        window and root-origin checks are repeated at every dispatch.
        """
        scope = dict(access_scope) if isinstance(access_scope, dict) else None
        if (not isinstance(question, str) or not question.strip() or len(question.encode('utf-8')) > 8192
                or not scope or not scope.get('principal') or not scope.get('profile')
                or not isinstance(focus_id, str) or not focus_id or len(focus_id) > 256
                or not isinstance(input_fingerprint, str) or not input_fingerprint
                or len(input_fingerprint) > 256 or not isinstance(window_deadline, (int, float))
                or isinstance(window_deadline, bool) or not math.isfinite(float(window_deadline))):
            return {'accepted': False, 'job_id': None, 'status': 'deferred', 'reason': 'invalid_preparation'}
        dependencies = expected_dependencies if expected_dependencies is not None else {}
        if not isinstance(dependencies, dict):
            return {'accepted': False, 'job_id': None, 'status': 'deferred', 'reason': 'invalid_dependencies'}
        try:
            dependency_wire = wire(dependencies)
            if len(dependency_wire.encode('utf-8')) > 64 * 1024:
                return {'accepted': False, 'job_id': None, 'status': 'deferred', 'reason': 'dependencies_too_large'}
            read_scope_seal = automatic_read_scope_from_dependencies(dependencies)
        except (TypeError, ValueError):
            return {'accepted': False, 'job_id': None, 'status': 'deferred', 'reason': 'invalid_dependencies'}
        dependency_value = json.loads(dependency_wire)
        project = scope.get('project')
        if not isinstance(project, str) or not project:
            return {'accepted': False, 'job_id': None, 'status': 'deferred', 'reason': 'project_scope_required'}
        now = float(self.clock())
        if float(window_deadline) <= now:
            return {'accepted': False, 'job_id': None, 'status': 'deferred', 'reason': 'focus_window_expired'}
        identity = canonical_digest({'focus_id': focus_id, 'project': project,
            'input_fingerprint': input_fingerprint, 'expected_dependencies': dependency_value,
            'automatic_read_scope': read_scope_seal, 'scope': scope, 'question': question})
        try:
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                policy = PowerPolicy(db, clock=self.clock)
                state = policy.snapshot()
                if (not state['automatic_enabled'] or state['automatic_focus'] != focus_id
                        or state['automatic_project'] != project or state['automatic_until'] is None
                        or float(window_deadline) > float(state['automatic_until'])):
                    return {'accepted': False, 'job_id': None, 'status': 'deferred', 'reason': 'focus_window_unavailable'}
                control = trusted_operator_control()
                decision = policy.allow_dispatch('automatic_root', True,
                    permission=control.permission('dispatch_automatic'), focus_id=focus_id,
                    project=project)
                if not decision.allowed:
                    return {'accepted': False, 'job_id': None, 'status': 'deferred', 'reason': decision.reason}
                existing = db.execute('''SELECT a.job_id,j.record FROM pa1_automatic_work a
                    JOIN jobs j ON j.id=a.job_id WHERE a.identity=?''', (identity,)).fetchone()
                if existing:
                    old_job = DurableJob.model_validate_json(existing['record'])
                    if (old_job.status not in TERMINAL
                            or db.execute('SELECT 1 FROM execution_slots WHERE job=?', (old_job.job_id,)).fetchone()):
                        return {'accepted': True, 'job_id': old_job.job_id, 'status': 'existing'}
                    # The same exact stable input is never rerun by retrying an
                    # enqueue. Attention policy owns any later cooldown or
                    # explicit refresh decision.
                    return {'accepted': True, 'job_id': old_job.job_id, 'status': 'existing'}
                # A changed focus/source identity invalidates older queued
                # automatic work. Active calls are fenced, but their slot
                # remains until normal physical cleanup completes.
                stale = db.execute('''SELECT a.job_id,j.record FROM pa1_automatic_work a
                    JOIN jobs j ON j.id=a.job_id WHERE (a.focus_id<>? OR a.project<>? OR a.identity<>?)
                    AND a.job_id IN (SELECT job_id FROM pa1_frames WHERE origin_class='automatic')''',
                    (focus_id, project, identity)).fetchall()
                for old_row in stale:
                    if old_row['job_id'] == (existing['job_id'] if existing else None):
                        continue
                    old = DurableJob.model_validate_json(old_row['record'])
                    if old.status in TERMINAL:
                        continue
                    old.attempt += 1
                    old.status = 'cancelled'
                    old.failure_reason = 'preparation_source_changed'
                    old.terminal_reason = 'preparation_source_changed'
                    old.unresolved_questions = ['The source fingerprint changed before preparation completed.']
                    db.execute('UPDATE jobs SET record=?,lease=0,updated=? WHERE id=?',
                        (old.model_dump_json(), now, old.job_id))
                    frames = FrameStore(db)
                    frame = frames.get_frame(old.job_id)
                    if frame is not None:
                        self._cancel_descendants(db, frames, old.job_id, now)
                        frames.record_terminal(old.job_id, frame.generation, 'cancelled',
                            {'status': 'cancelled', 'reason': 'preparation_source_changed'}, now=now)
                job_id = 'job_' + uuid.uuid4().hex
                job = DurableJob(job_id=job_id, mode='investigate', question=question,
                    hints=[], status='queued', attempt=0, created_at=stamp(now), findings=[],
                    evidence_packets=[], unresolved_questions=[], project=project, scope=scope,
                    deadline_epoch=float(window_deadline))
                record_wire = job.model_dump_json()
                if len(record_wire.encode('utf-8')) > 32768:
                    return {'accepted': False, 'job_id': None, 'status': 'deferred', 'reason': 'preparation_too_large'}
                if self._storage_bytes(db) + len(record_wire.encode('utf-8')) > self.max_storage_bytes:
                    return {'accepted': False, 'job_id': None, 'status': 'deferred', 'reason': 'storage_limit'}
                db.execute('INSERT INTO jobs(id,scope,request_id,request_hash,record,updated,inquiry) VALUES(?,?,?,?,?,?,0)',
                    (job_id, wire(scope), None, identity, record_wire, now))
                FrameStore(db).register_root(job_id, scope, float(window_deadline),
                    max_turns=6, origin_class='automatic', automatic_read_scope=read_scope_seal, now=now)
                db.execute('INSERT INTO pa1_private_ids(job_id) VALUES(?)', (job_id,))
                db.execute('INSERT INTO pa1_automatic_work VALUES(?,?,?,?,?,?,?)',
                    (job_id, focus_id, project, input_fingerprint, dependency_wire,
                     float(window_deadline), identity))
                self._wake.set()
                return {'accepted': True, 'job_id': job_id, 'status': 'queued'}
        except (sqlite3.Error, OSError, ValueError):
            return {'accepted': False, 'job_id': None, 'status': 'deferred', 'reason': 'storage_unavailable'}

    def preparation_read_scope(self, job_id, *, access_scope, expected_attempt):
        """Return the trusted persisted file seal for an automatic lease or descendant.

        This accessor is for the host-owned worker factory, not public callers or
        model context. It cross-checks the frame lineage against the admission
        dependency map each time the worker binds its read tools.
        """
        if not isinstance(job_id, str) or not job_id:
            return None
        try:
            with self._db() as db:
                db.execute('BEGIN')
                frames = FrameStore(db)
                frame = frames.get_frame(job_id)
                if frame is None or frame.origin_class != 'automatic':
                    return None
                if not isinstance(access_scope, dict):
                    raise FrameError('automatic read scope requires the leased trusted scope')
                if (not isinstance(expected_attempt, int) or isinstance(expected_attempt, bool)
                        or expected_attempt < 1):
                    raise FrameError('automatic read scope requires its captured positive attempt')
                durable = db.execute('SELECT scope,record FROM jobs WHERE id=?', (job_id,)).fetchone()
                slot = db.execute('SELECT attempt FROM execution_slots WHERE job=?', (job_id,)).fetchone()
                if durable is None or slot is None:
                    raise FrameError('automatic read scope requires a live execution lease')
                leased = DurableJob.model_validate_json(durable['record'])
                if (frame.state != 'running' or durable['scope'] != wire(access_scope) or frame.scope != access_scope
                        or leased.scope != access_scope or leased.status != 'running'
                        or leased.attempt != int(slot['attempt']) or leased.attempt != expected_attempt):
                    raise FrameError('automatic read scope does not match the leased job scope')
                seal = frames.automatic_read_scope(job_id)
                work = db.execute('SELECT focus_id,project,window_deadline,expected_dependencies '
                    'FROM pa1_automatic_work WHERE job_id=?',
                    (frame.root_id,)).fetchone()
                if work is None:
                    raise FrameError('automatic admission dependency seal is missing')
                now = float(self.clock())
                if (float(work['window_deadline']) <= now or float(frame.deadline) <= now
                        or (leased.deadline_epoch is not None and float(leased.deadline_epoch) <= now)):
                    raise FrameError('automatic read scope expired')
                if frame.parent_id is not None:
                    edge = db.execute('''SELECT c.generation,w.state,p.generation AS current_generation
                        FROM pa1_frame_children c JOIN pa1_frame_waits w
                          ON w.parent_id=c.parent_id AND w.generation=c.generation
                        JOIN pa1_frames p ON p.job_id=c.parent_id
                        WHERE c.parent_id=? AND c.child_id=?''',
                        (frame.parent_id, job_id)).fetchone()
                    if (edge is None or edge['state'] != 'waiting'
                            or int(edge['generation']) != int(edge['current_generation'])):
                        raise FrameError('automatic descendant frame generation is stale')
                policy = PowerPolicy(db, clock=self.clock)
                origin = policy.root_demand_origin(frame.root_id)
                frame_class = 'automatic_root' if frame.parent_id is None else 'private_child'
                permission = trusted_operator_control().permission('dispatch_automatic')
                decision = policy.allow_dispatch(frame_class, True, root_id=frame.root_id,
                    demand_origin=origin, permission=permission, focus_id=work['focus_id'],
                    project=work['project'])
                if not decision.allowed:
                    raise FrameError('automatic read scope policy is no longer allowed')
                dependencies = json.loads(work['expected_dependencies'])
                if automatic_read_scope_from_dependencies(dependencies) != seal:
                    raise FrameError('automatic read scope differs from its admission dependencies')
                return seal
        except (sqlite3.Error, OSError, TypeError, ValueError):
            raise FrameError('automatic read scope is unavailable or inconsistent')

    def preparation_lookup(self, job_id, *, access_scope):
        """Read a private preparation result for its exact originating scope."""
        if not isinstance(job_id, str) or not job_id:
            return {'status': 'unavailable', 'reason': 'preparation_unavailable'}
        try:
            with self._db() as db:
                db.execute('BEGIN')
                row = db.execute('''SELECT j.*,a.focus_id,a.project,a.input_fingerprint,
                    a.expected_dependencies FROM jobs j JOIN pa1_automatic_work a ON a.job_id=j.id
                    WHERE j.id=?''', (job_id,)).fetchone()
                if not row or row['scope'] != wire(dict(access_scope)):
                    return {'status': 'unavailable', 'reason': 'preparation_unavailable'}
                job = DurableJob.model_validate_json(row['record'])
                if job.status not in TERMINAL or db.execute(
                        'SELECT 1 FROM execution_slots WHERE job=?', (job_id,)).fetchone():
                    return {'status': 'pending'}
                if job.terminal_reason == 'preparation_source_changed':
                    return {'status': 'unavailable', 'reason': 'preparation_source_changed'}
                frame = FrameStore(db).get_frame(job_id)
                dependencies = json.loads(row['expected_dependencies'])
                source_values = {}
                for packet_id in list(dict.fromkeys(job.evidence_packets +
                        ([job.result_packet] if job.result_packet else []))):
                    packet = self.packets.lookup(packet_id, access_scope=access_scope)
                    if packet.status == 'ok':
                        for source in packet.packet.sources:
                            source_values[wire(source.model_dump(exclude_none=True))] = source.model_dump(exclude_none=True)
                return {'status': job.status, 'answer': job.answer,
                    'findings': [finding.model_dump() for finding in job.findings],
                    'evidence_packets': list(job.evidence_packets),
                    'unresolved_questions': list(job.unresolved_questions),
                    'sources': list(source_values.values()),
                    'dependencies': dependencies,
                    'input_fingerprint': row['input_fingerprint'], 'focus_id': row['focus_id'],
                    'model_turns_used': frame.root_turns_used if frame is not None else 0}
        except (sqlite3.Error, OSError, TypeError, ValueError):
            return {'status': 'unavailable', 'reason': 'preparation_unavailable'}

    def cancel_preparation(self, job_id, *, access_scope):
        """Cancel an exact-scope private preparation and its descendants."""
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT j.* FROM jobs j JOIN pa1_automatic_work a ON a.job_id=j.id
                WHERE j.id=? AND j.scope=?''', (job_id, wire(dict(access_scope)))).fetchone()
            if not row:
                return False
            job = DurableJob.model_validate_json(row['record'])
            if job.status in TERMINAL:
                return False
            now = float(self.clock())
            job.attempt += 1
            job.status = 'cancelled'
            job.failure_reason = 'operator_cancelled'
            job.terminal_reason = 'operator_cancelled'
            job.unresolved_questions = ['The preparation was cancelled by its trusted controller.']
            db.execute('UPDATE jobs SET record=?,lease=0,updated=? WHERE id=?',
                (job.model_dump_json(), now, job_id))
            frames = FrameStore(db)
            frame = frames.get_frame(job_id)
            if frame is not None:
                self._cancel_descendants(db, frames, job_id, now)
                frames.record_terminal(job_id, frame.generation, 'cancelled',
                    {'status': 'cancelled', 'reason': 'operator_cancelled'}, now=now)
        self.reconcile()
        self._wake.set()
        return True

    def inquire(self, question, access_scope, mode='investigate', skill=None, hints=(),
                request_id=None, execution_question=None, foreground_timeout=30,
                startup_timeout=None):
        if startup_timeout is not None:
            startup_timeout = self._coerce_startup_timeout(startup_timeout)
        try:
            return self._inquire(question, access_scope, mode, skill, hints, request_id,
                                 execution_question, foreground_timeout, startup_timeout)
        except (sqlite3.Error, OSError):
            return {'status': 'unavailable', 'reason': 'storage_unavailable'}

    def cancel_inquiry(self, question, access_scope, mode='investigate', skill=None):
        """Cancel only the exact active inquiry identified by its public inputs.

        The lookup deliberately uses the same digest as ``inquire`` and never
        returns a durable job identifier.  Full scope and inquiry fields are
        checked again under the write transaction before applying the ordinary
        cancellation transition.
        """
        scope = dict(access_scope)
        if not scope.get('principal') or not scope.get('profile'):
            return False
        identity = self._inquiry_identity(question, scope, mode, skill)
        wake_parents = False
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            frames = FrameStore(db)
            row = db.execute('''SELECT jobs.* FROM inquiry_index
                JOIN jobs ON jobs.id=inquiry_index.job
                WHERE inquiry_index.identity=? AND jobs.inquiry=1''', (identity,)).fetchone()
            if not row:
                return False
            job = DurableJob.model_validate_json(row['record'])
            if (row['scope'] != wire(scope) or job.question != question
                    or job.mode != mode or job.skill != skill
                    or job.status in TERMINAL or frames.is_private(job.job_id)):
                return False
            job.attempt += 1
            job.status = 'cancelled'
            db.execute('UPDATE jobs SET record=?,lease=0,updated=? WHERE id=?',
                (job.model_dump_json(), self.clock(), job.job_id))
            frame = frames.get_frame(job.job_id)
            if frame is not None:
                self._cancel_descendants(db, frames, job.job_id, self.clock())
                frames.record_terminal(job.job_id, frame.generation, 'cancelled',
                    {'status': 'cancelled', 'reason': 'operator_cancelled'}, now=self.clock())
                wake_parents = frame.parent_id is not None
            self._trim_index(db, job.scope)
        self.reconcile()
        self._consume_pending_frame_acks()
        if wake_parents:
            self._wake_ready_parents()
        self._wake.set()
        return True

    def _inquiry_identity(self, question, scope, mode, skill):
        context = self.inquiry_context(scope, self.analysis_runtime_identity)
        if mode == 'skill' and self.skill_catalog_identity_provider is not None:
            context['skill_catalog_sha256'] = self.skill_catalog_identity_provider()
        return canonical_digest({'question': question, 'context': context,
            'mode': mode, 'skill': skill})

    def _inquire(self, question, access_scope, mode, skill, hints, request_id,
                 execution_question, foreground_timeout, startup_timeout=None):
        scope = dict(access_scope)
        if not scope.get('principal') or not scope.get('profile'):
            raise ValueError('trusted principal/profile scope required')
        identity = self._inquiry_identity(question, scope, mode, skill)
        # Read-only fast path: a duplicate does not validate new hints, touch order,
        # mutate TTL, or require a live dispatcher.
        with self._db() as db:
            row = db.execute('SELECT jobs.* FROM inquiry_index JOIN jobs ON jobs.id=inquiry_index.job WHERE identity=?', (identity,)).fetchone()
        freshness_snapshot = None
        if row:
            previous = DurableJob.model_validate_json(row['record'])
            if not self._inquiry_authorized(previous.model_dump(), scope):
                return {'status': 'unavailable', 'reason': 'access_unavailable'}
            if previous.status not in TERMINAL:
                unavailable = self._ensure_demand_runtime(startup_timeout)
                if unavailable:
                    return unavailable
                return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}
            if not self._cache_eligible(previous.model_dump()):
                return {'status': 'unavailable', 'reason': 'analysis_unavailable'}
            if not self._settled(previous.job_id):
                unavailable = self._ensure_demand_runtime(startup_timeout)
                if unavailable:
                    return unavailable
                return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}
            freshness = self.freshness_provider(previous.model_dump()) if self.freshness_provider else {'fresh': False}
            freshness_snapshot = {'job_id': row['id'], 'record': row['record'], 'freshness': freshness}
            if previous.status in {'completed', 'partial'} and freshness.get('fresh'):
                with self._db() as db:
                    current = db.execute('SELECT jobs.* FROM inquiry_index JOIN jobs ON jobs.id=inquiry_index.job WHERE identity=?', (identity,)).fetchone()
                    if not current or current['id'] != row['id'] or current['record'] != row['record']:
                        return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}
                    if not self._inquiry_authorized(json.loads(current['record']), scope):
                        return {'status': 'unavailable', 'reason': 'access_unavailable'}
                    observations = self._public_observations(db, current)
                return {'status': previous.status, 'job': previous.model_dump(), 'observations': observations}
            if self._historical_zero_ttl_delivery(freshness, previous, scope):
                with self._db() as db:
                    current = db.execute('SELECT jobs.* FROM inquiry_index JOIN jobs ON jobs.id=inquiry_index.job WHERE identity=?', (identity,)).fetchone()
                    if not current or current['id'] != row['id'] or current['record'] != row['record']:
                        return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}
                    current_job = json.loads(current['record'])
                    if not self._inquiry_authorized(current_job, scope):
                        return {'status': 'unavailable', 'reason': 'access_unavailable'}
                    observations = self._public_observations(db, current)
                historical = dict(previous.model_dump())
                historical['status'] = 'partial'
                note = (f"Historical command evidence from {previous.created_at} may be stale; freshness is not verified. "
                        "Ask a new question or add context for fresh observations.")[:250]
                historical['unresolved_questions'] = list(previous.unresolved_questions) + [note]
                return {'status': 'partial', 'job': historical, 'observations': observations}
            if self._freshness_unverifiable(freshness, scope):
                # Do not start a new generation when its retained evidence is
                # inherently unverifiable; an identical retry cannot repair it.
                with self._db() as db:
                    current = db.execute('SELECT jobs.* FROM inquiry_index JOIN jobs ON jobs.id=inquiry_index.job WHERE identity=?', (identity,)).fetchone()
                    if not current or current['id'] != row['id'] or current['record'] != row['record']:
                        return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}
                    if not self._inquiry_authorized(json.loads(current['record']), scope):
                        return {'status': 'unavailable', 'reason': 'access_unavailable'}
                return {'status': 'unavailable', 'reason': 'freshness_unverifiable'}
        # This is the sole fresh-start boundary. Cache hits, access checks,
        # freshness failures, and all model-free service reads return above.
        unavailable = self._ensure_demand_runtime(startup_timeout)
        if unavailable:
            return unavailable
        admitted = self.submit(question=question, access_scope=scope, mode=mode, skill=skill,
            hints=hints, request_id=request_id, _identity=identity, _execution_question=execution_question,
            _freshness_snapshot=freshness_snapshot)
        if not admitted['accepted']:
            if admitted['reason'] == 'inquiry_changed':
                return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}
            return {'status': 'busy' if admitted['reason'] == 'admission_limit' else 'unavailable',
                    'reason': admitted['reason']}
        value = self.lookup_inquiry(admitted['job_id'], access_scope=scope)
        if value['status'] != 'ok':
            return {'status': 'unavailable', 'reason': 'access_unavailable'}
        if value['job']['status'] in TERMINAL and not self._cache_eligible(value['job']):
            return {'status': 'unavailable', 'reason': 'analysis_unavailable'}
        if value['job']['status'] in {'completed', 'partial'} and self._settled(value['job']['job_id']):
            return {**value, 'status': value['job']['status']}
        if not admitted.get('immediate') or admitted.get('retry'):
            return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}
        end = time.monotonic() + max(0, min(30, foreground_timeout))
        while time.monotonic() < end:
            value = self.lookup_inquiry(admitted['job_id'], access_scope=scope)
            if value['status'] != 'ok':
                return {'status': 'unavailable', 'reason': 'access_unavailable'}
            if value['job']['status'] in TERMINAL and not self._cache_eligible(value['job']):
                return {'status': 'unavailable', 'reason': 'analysis_unavailable'}
            if value['job']['status'] in {'completed', 'partial'} and self._settled(value['job']['job_id']):
                return {**value, 'status': value['job']['status']}
            if value['job']['status'] in {'failed', 'cancelled'}:
                return {'status': 'unavailable', 'reason': 'analysis_unavailable'}
            time.sleep(min(.02, max(0, end-time.monotonic())))
        return {'status': 'thinking', 'message': 'Read-only analysis is in progress.'}

    @staticmethod
    def _cache_eligible(job):
        return (job['status'] in {'completed', 'partial'}
                and bool((job.get('answer') or '').strip()
                         or any(f['text'].strip() and f['evidence_packets'] for f in job['findings'])))

    def _freshness_unverifiable(self, freshness, access_scope):
        """Return true only for stale evidence another identical run cannot fix."""
        changes = freshness.get('changed_sources') if isinstance(freshness, dict) else None
        if not isinstance(changes, list) or not changes:
            return False

        def contains_unverifiable(item):
            if not isinstance(item, dict):
                return False
            reason = item.get('reason')
            if reason in {'unverified', 'selection_proof_missing', 'selection_manifest_missing',
                          'dependency_manifest_missing'}:
                return True
            if reason == 'volatile_observation_expired':
                ref = item.get('reference')
                packet = self.packets.lookup(ref, access_scope=access_scope).packet if isinstance(ref, str) else None
                metadata = packet.freshness if packet is not None else None
                max_age = metadata.get('max_age_seconds') if isinstance(metadata, dict) else None
                return not (isinstance(max_age, (int, float)) and not isinstance(max_age, bool) and max_age > 0)
            for key in ('dependencies', 'detail'):
                nested = item.get(key)
                if isinstance(nested, list) and any(contains_unverifiable(value) for value in nested):
                    return True
                if isinstance(nested, dict) and contains_unverifiable(nested):
                    return True
            return False

        return any(contains_unverifiable(item) for item in changes)

    def _historical_zero_ttl_delivery(self, freshness, job, access_scope):
        """Allow explicit partial delivery of only historical coarse command evidence."""
        changes = freshness.get('changed_sources') if isinstance(freshness, dict) else None
        if job.mode != 'investigate' or not isinstance(changes, list) or not changes:
            return False
        confirmed = 0
        for item in changes:
            if not isinstance(item, dict):
                return False
            if item.get('reason') == 'dependency_manifest_missing':
                continue
            if item.get('reason') != 'stale' or not isinstance(item.get('reference'), str):
                return False
            dependencies = item.get('dependencies')
            if not isinstance(dependencies, list) or not dependencies or any(
                    not isinstance(dep, dict) or dep.get('reason') != 'volatile_observation_expired'
                    for dep in dependencies):
                return False
            lookup = self.packets.lookup(item['reference'], access_scope=access_scope)
            packet = lookup.packet if lookup.status == 'ok' else None
            if packet is None or packet.tool != 'command' or packet.sources:
                return False
            payload = packet.payload
            if (not isinstance(payload, dict) or payload.get('status') != 'completed'
                    or payload.get('exit_code') != 0 or payload.get('truncated')
                    or payload.get('timed_out') or payload.get('source_reads')):
                return False
            metadata = packet.freshness
            if (not isinstance(metadata, dict) or metadata.get('volatile') is not True
                    or metadata.get('max_age_seconds') != 0):
                return False
            confirmed += 1
        return confirmed > 0

    @staticmethod
    def _inquiry_failure_class(job):
        for reason in (job.get('failure_reason'), job.get('terminal_reason')):
            if isinstance(reason, str):
                if reason in _INQUIRY_FAILURE_CLASSES:
                    return _INQUIRY_FAILURE_CLASSES[reason]
                if reason.startswith(('Extra data:', 'JSONDecodeError:', 'json.JSONDecodeError:')):
                    return 'model_output_invalid_json'
                if (reason.endswith('_mismatch')
                        and reason.startswith(('central_supervisor_', 'runtime_identity_', 'inference_'))):
                    return 'runtime_mismatch'
        return 'worker_failure'

    def inquiry_failure_diagnostics(self, limit=20):
        """Return bounded, content-free classifications for indexed negative jobs.

        This is an operator-local service method, not an observer tool. It omits
        questions, prompts, raw reasons, packets and model output by design.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError('limit must be an integer from 1 to 50')
        with self._db() as db:
            cursor = db.execute('''SELECT jobs.id,jobs.record FROM inquiry_index
                JOIN jobs ON jobs.id=inquiry_index.job
                WHERE json_extract(jobs.record,'$.status') IN ('completed','partial','failed','cancelled')
                ORDER BY jobs.updated DESC,jobs.id DESC''')
            negative_rows = []
            while len(negative_rows) < limit:
                batch = cursor.fetchmany(64)
                if not batch:
                    break
                for row in batch:
                    try:
                        job = json.loads(row['record'])
                        if (job.get('status') in TERMINAL and job.get('mode') in {'investigate', 'skill'}
                                and not self._cache_eligible(job)):
                            negative_rows.append((row['id'], job))
                            if len(negative_rows) == limit:
                                break
                    except (TypeError, ValueError, AttributeError, KeyError):
                        continue
        diagnostics = []
        for job_id, job in negative_rows:
            mode, status = job.get('mode'), job.get('status')
            diagnostics.append({'job_id': job_id, 'mode': mode, 'status': status,
                'failure_class': self._inquiry_failure_class(job)})
        return diagnostics

    def release_failed_inquiries(self, expected_terminal_reasons):
        """Remove selected, still-negative inquiries from the identity cache.

        This service-owned repair hook deliberately leaves job history, packets,
        observations and scheduler state untouched. Callers must name each job
        and the terminal reason they observed; changed, active or answer-bearing
        jobs are skipped.
        """
        if not isinstance(expected_terminal_reasons, dict):
            raise TypeError('expected_terminal_reasons must be a job-id to reason mapping')
        for job_id, reason in expected_terminal_reasons.items():
            if not isinstance(job_id, str) or not job_id or not isinstance(reason, str) or not reason:
                raise ValueError('job IDs and expected terminal reasons must be non-empty strings')

        recovered = []
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            for job_id, expected_reason in expected_terminal_reasons.items():
                rows = db.execute('''SELECT inquiry_index.identity,jobs.record
                    FROM inquiry_index JOIN jobs ON jobs.id=inquiry_index.job
                    WHERE jobs.id=?''', (job_id,)).fetchall()
                if not rows:
                    continue
                try:
                    job = json.loads(rows[0]['record'])
                    if (job.get('status') not in TERMINAL
                            or job.get('terminal_reason') != expected_reason
                            or self._cache_eligible(job)
                            or db.execute('SELECT 1 FROM execution_slots WHERE job=?', (job_id,)).fetchone()
                            or db.execute("SELECT 1 FROM outbox WHERE job=? AND materialized=0", (job_id,)).fetchone()):
                        continue
                except (AttributeError, TypeError, ValueError, KeyError):
                    continue
                db.execute('DELETE FROM inquiry_index WHERE job=?', (job_id,))
                recovered.append(job_id)
        return recovered

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
            if FrameStore(db).is_private(job_id):
                return {'status': 'not_found'}
            if not self._inquiry_authorized(json.loads(row['record']), access_scope, log=True):
                return {'status': 'forbidden'}
            observations = self._public_observations(db, row)
        return {'status': 'ok', 'job': DurableJob.model_validate_json(row['record']).model_dump(),
                'observations': observations}

    def _worker_lookup(self, job_id, *, access_scope):
        """Private exact-scope snapshot for an already claimed worker only."""
        with self._db() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row['scope'] != wire(dict(access_scope)):
                raise PermissionError('worker_scope_mismatch')
            observations = self._public_observations(db, row)
        return {'status': 'ok', 'job': DurableJob.model_validate_json(row['record']).model_dump(),
                'observations': observations}

    poll = lookup

    @staticmethod
    def inquiry_context(scope, analysis_runtime_identity=None):
        """Only caller identity is excluded; authority context remains exact."""
        context = {k: v for k, v in scope.items() if k not in {'principal', 'profile'}}
        if analysis_runtime_identity is not None:
            context['analysis_runtime_identity'] = analysis_runtime_identity
        return context

    def _inquiry_authorized(self, job, scope, *, log=False):
        required = self.inquiry_context(job['scope'])
        supplied = self.inquiry_context(scope)
        if self.inquiry_access is not None:
            permitted = self.inquiry_access(required, dict(scope), log=log)
        else:
            permitted = required == supplied
        # Material may be stale/missing and still need refresh; only an explicit
        # source-authority denial blocks reuse, never caller/profile provenance.
        material = job['hints'] + job['evidence_packets'] + ([job['result_packet']] if job.get('result_packet') else [])
        return bool(permitted) and all(self.packets.lookup(ref, access_scope=scope).status != 'forbidden'
                                       for ref in material)

    def is_inquiry(self, job_id):
        with self._db() as db:
            return (not FrameStore(db).is_private(job_id)
                    and bool(db.execute('SELECT 1 FROM jobs WHERE id=? AND inquiry=1', (job_id,)).fetchone()))

    # Inquiry and legacy observer reads share the same project knowledge.
    lookup_inquiry = lookup

    def log(self, *, access_scope, query='', limit=5, current_dependencies=None):
        if not 1 <= limit <= 50:
            raise ValueError('log limit must be 1..50')
        with self._db() as db:
            rows = db.execute('SELECT jobs.*, inquiry_index.identity FROM jobs LEFT JOIN inquiry_index ON jobs.id=inquiry_index.job ORDER BY updated DESC,jobs.id DESC').fetchall()
            private_ids = {row[0] for row in db.execute('SELECT job_id FROM pa1_private_ids')}
        candidates = []
        answered_count = 0
        for row in rows:
            job = json.loads(row['record'])
            if row['id'] in private_ids:
                continue
            if not self._cache_eligible(job):
                continue
            # Superseded/evicted inquiry generations are history, not another
            # private retrieval window. Legacy answered jobs join the one window.
            if row['inquiry'] and row['identity'] is None:
                continue
            answered_count += 1
            if answered_count > 50:
                break
            if self._inquiry_authorized(job, access_scope, log=True):
                candidates.append((job, json.loads(row['observations'])))
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
            evidence = self.packets.assemble_hints(refs, access_scope=job['scope'], current_dependencies=current_dependencies)
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
        preflight = self._dispatch_preflight(force=True)
        if preflight is False:
            return None
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            now = self.clock()
            for expired in db.execute('SELECT * FROM execution_slots WHERE lease<=?', (now,)).fetchall():
                # A live host still owns its underlying operation even when a
                # noncooperative startup or clock jump outlasts the lease.
                if not expired['cleanup_failed'] and not self._owner_alive(expired['owner_pid'], expired['owner_start']):
                    db.execute('DELETE FROM execution_slots WHERE job=? AND attempt=?', (expired['job'], expired['attempt']))
            rows = db.execute('''SELECT j.* FROM jobs j LEFT JOIN pa1_frames f ON f.job_id=j.id
                LEFT JOIN pa1_frames r ON r.job_id=f.root_id
                ORDER BY CASE WHEN coalesce(r.origin_class,f.origin_class,'public_demand')='automatic' THEN 1 ELSE 0 END,
                    j.updated,j.id''').fetchall()
            frames = FrameStore(db)
            automatic_slots = db.execute('''SELECT count(*) FROM execution_slots s
                JOIN pa1_frames f ON f.job_id=s.job
                JOIN pa1_frames r ON r.job_id=f.root_id WHERE r.origin_class='automatic' ''').fetchone()[0]
            for row in rows:
                job = DurableJob.model_validate_json(row['record'])
                if job.status in TERMINAL:
                    continue
                frame = frames.get_frame(job.job_id)
                if frame is not None:
                    if frame.state == 'waiting':
                        continue
                    if frame.state == 'suspended':
                        # A suspended parent is requeued only after a complete,
                        # durable child result set has materialized its wake.
                        pending = db.execute("""SELECT generation FROM pa1_frame_waits
                            WHERE parent_id=? ORDER BY generation DESC LIMIT 1""", (job.job_id,)).fetchone()
                        if not pending:
                            continue
                        outcome = frames.reconcile(job.job_id, int(pending['generation']))
                        if not outcome or not outcome.ready:
                            continue
                        job.status = 'queued_after_eviction'
                        db.execute('UPDATE jobs SET record=?,available=?,updated=? WHERE id=?',
                            (job.model_dump_json(), now, now, job.job_id))
                    # The release/eligibility proof gates a newly queued child
                    # before its first dispatch. Once that child has run, its
                    # ancestor wait intentionally remains open until the child
                    # becomes terminal; reapplying this gate would deadlock
                    # every resumed private child and every nested parent.
                    if (frame.parent_id is not None and frame.state == 'queued'
                            and not frames.eligible(job.job_id, now)):
                        continue
                    if frame.state in {'completed', 'partial', 'failed', 'cancelled', 'expired'}:
                        continue
                    origin = PowerPolicy(db, clock=self.clock).root_demand_origin(frame.root_id)
                    frame_class = ('automatic_root' if frame.origin_class == 'automatic' and frame.parent_id is None
                                   else 'public_root' if frame.parent_id is None else 'private_child')
                    automatic = origin.kind == 'automatic'
                    automatic_work = (db.execute('SELECT * FROM pa1_automatic_work WHERE job_id=?',
                        (frame.root_id,)).fetchone() if automatic else None)
                    if automatic and not automatic_work:
                        continue
                    automatic_permission = (trusted_operator_control().permission('dispatch_automatic')
                                            if automatic else None)
                    if automatic and automatic_slots >= 1:
                        continue
                    if not PowerPolicy(db, clock=self.clock).allow_dispatch(
                            frame_class, True, root_id=frame.root_id,
                            demand_origin=origin, permission=automatic_permission,
                            focus_id=automatic_work['focus_id'] if automatic_work else None,
                            project=automatic_work['project'] if automatic_work else None).allowed:
                        continue
                if preflight is not None and job.job_id not in preflight:
                    continue
                if self.can_execute is not None and not self.can_execute(job, bool(row['inquiry'])):
                    continue
                occupied = db.execute('SELECT 1 FROM execution_slots WHERE job=?', (job.job_id,)).fetchone()
                if occupied:
                    continue
                retry_exhausted = (job.attempt >= 3 if frame is None else frame.failed_attempts >= 3)
                if (job.deadline_epoch is not None and now >= job.deadline_epoch) or retry_exhausted:
                    job.status = 'partial' if job.findings else 'failed'
                    job.unresolved_questions = ['Analysis deadline or attempt budget exhausted.']
                    job.terminal_reason = 'attempt_or_deadline_exhausted'
                    db.execute('UPDATE jobs SET record=?,lease=0,updated=? WHERE id=?', (job.model_dump_json(), now, job.job_id))
                    self._trim_index(db, job.scope)
                    continue
                if row['available'] > now:
                    continue
                if db.execute('SELECT count(*) FROM execution_slots').fetchone()[0] >= 2:
                    continue
                job.attempt += 1; job.status = 'running'
                if frame is not None:
                    db.execute('UPDATE pa1_frames SET state=\'running\',updated=? WHERE job_id=?',
                        (now, job.job_id))
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

    def _private_wait(self, job, result):
        """Persist a typed child proposal and queued private job atomically."""
        proposal = result.get('private_wait')
        if not isinstance(proposal, dict) or set(proposal) != {'kind', 'question', 'max_turns'}:
            raise FrameError('invalid_private_wait_proposal')
        question = proposal['question']
        turns = proposal['max_turns']
        if (proposal['kind'] != 'child' or not isinstance(question, str)
                or not question.strip() or len(question.encode('utf-8')) > 512
                or not isinstance(turns, int) or isinstance(turns, bool) or turns not in (1, 2)):
            raise FrameError('invalid_private_wait_proposal')
        now = self.clock()
        child_id = 'job_' + uuid.uuid4().hex
        deadline = float(job.deadline_epoch or now)
        if deadline <= now:
            raise FrameError('parent_deadline_expired')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            _, current = self._running(db, job.job_id, job.attempt)
            frames = FrameStore(db)
            parent = frames.get_frame(job.job_id)
            if parent is None:
                frames.register_root(job.job_id, job.scope, deadline, max_turns=6, now=now)
                parent = frames.get_frame(job.job_id)
            generation = parent.generation + 1
            child = DurableJob(job_id=child_id, mode=job.mode, question=question.strip(), hints=[],
                status='queued', attempt=0, created_at=stamp(now), findings=[], evidence_packets=[],
                unresolved_questions=[], project=job.project, scope=dict(job.scope), skill=job.skill,
                deadline_epoch=deadline)
            request_hash = canonical_digest({'parent': job.job_id, 'generation': generation,
                'question': child.question, 'scope': child.scope, 'mode': child.mode, 'skill': child.skill})
            db.execute('INSERT INTO jobs(id,scope,request_id,request_hash,record,updated,inquiry) VALUES(?,?,?,?,?,?,0)',
                (child_id, wire(child.scope), None, request_hash, child.model_dump_json(), now))
            spec = ChildFrameSpec(job_id=child_id, scope=child.scope, deadline=deadline,
                question=child.question, turn_reservation=turns)
            frames.register_wait(job.job_id, generation, [spec], now=now)
            current.status = 'yielding'
            current.failure_reason = None
            db.execute('UPDATE jobs SET record=?,updated=?,lease=0,available=? WHERE id=?',
                (current.model_dump_json(), now, now, job.job_id))
        return generation

    def _private_child_context(self, db, job_id):
        """Return durable, bounded visible outcomes for a parent resume."""
        frames = FrameStore(db)
        row = db.execute('SELECT generation FROM pa1_frame_waits WHERE parent_id=? ORDER BY generation DESC LIMIT 1',
                          (job_id,)).fetchone()
        if not row:
            return (), None, None
        generation = int(row['generation'])
        outcome = frames.reconcile(job_id, generation)
        if not outcome or not outcome.ready:
            return (), generation, None
        children = []
        for child in outcome.children[:4]:
            item = {'child_id': child.job_id, 'status': child.status,
                    'result': dict(child.result), 'terminal_version': child.terminal_version}
            if len(wire(item).encode('utf-8')) > 16 * 1024:
                item['result'] = {'status': child.status,
                    'failure_reason': 'child_result_exceeded_context_bound'}
            children.append(item)
        return tuple(children), generation, outcome.wake_version

    def _wake_ready_parents(self):
        now = self.clock()
        changed = False
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            frames = FrameStore(db)
            waits = db.execute('SELECT parent_id,generation FROM pa1_frame_waits WHERE state IN (\'waiting\',\'ready\')').fetchall()
            for wait in waits:
                outcome = frames.reconcile(wait['parent_id'], int(wait['generation']))
                if not outcome or not outcome.ready:
                    continue
                row = db.execute('SELECT * FROM jobs WHERE id=?', (wait['parent_id'],)).fetchone()
                if not row:
                    continue
                parent = DurableJob.model_validate_json(row['record'])
                if parent.status in TERMINAL:
                    continue
                observations = json.loads(row['observations'])
                for child in outcome.children:
                    prior_packet = db.execute('SELECT packet_id FROM pa1_child_packets WHERE parent_id=? AND generation=? AND child_id=?',
                        (parent.job_id, int(wait['generation']), child.job_id)).fetchone()
                    if prior_packet:
                        continue
                    result = dict(child.result)
                    child_refs = list(dict.fromkeys(result.get('evidence_packets', [])))
                    if result.get('result_packet') and result['result_packet'] not in child_refs:
                        child_refs.append(result['result_packet'])
                    source_list = []
                    for ref in child_refs:
                        child_packet = db.execute('SELECT packet FROM outbox WHERE id=? AND job=?',
                            (ref, child.job_id)).fetchone()
                        if child_packet:
                            source_list.extend(json.loads(child_packet['packet']).get('sources', []))
                    payload = {'status': child.status, 'answer': result.get('answer'),
                        'findings': result.get('findings', []),
                        'failure_reason': result.get('failure_reason') or result.get('reason'),
                        'dependency_ordinal': len(observations) + 1,
                        'child_evidence_packets': child_refs,
                        'source_locators': source_list,
                        'provenance_note': 'Private child result with inherited source provenance; not independent corroboration.'}
                    ident, cleaned = self._packet(db, parent, payload, 'private_child_result', parents=child_refs)
                    parent.evidence_packets.append(ident)
                    observations.append({**cleaned, 'packet_id': ident})
                    db.execute('INSERT INTO pa1_child_packets(parent_id,generation,child_id,packet_id) VALUES(?,?,?,?)',
                        (parent.job_id, int(wait['generation']), child.job_id, ident))
                    changed = True
                if changed:
                    db.execute('UPDATE jobs SET record=?,observations=?,updated=? WHERE id=?',
                        (parent.model_dump_json(), wire(observations), now, parent.job_id))
                parent.status = 'queued_after_eviction'
                db.execute('UPDATE jobs SET record=?,updated=?,available=? WHERE id=?',
                    (parent.model_dump_json(), now, now, parent.job_id))
                changed = True
        if changed:
            self.reconcile()
        self._wake.set()

    def _cancel_descendants(self, db, frames, parent_id, now):
        """Fence child generations when an owning parent becomes terminal."""
        descendants = db.execute("""WITH RECURSIVE tree(job_id) AS (
            SELECT child_id FROM pa1_frame_children WHERE parent_id=?
            UNION ALL SELECT c.child_id FROM pa1_frame_children c JOIN tree t ON c.parent_id=t.job_id)
            SELECT job_id FROM tree""", (parent_id,)).fetchall()
        for entry in descendants:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (entry['job_id'],)).fetchone()
            frame = frames.get_frame(entry['job_id'])
            if not row or frame is None:
                continue
            child = DurableJob.model_validate_json(row['record'])
            if child.status not in TERMINAL:
                child.attempt += 1
                child.status = 'cancelled'
                child.failure_reason = 'parent_terminal'
                child.terminal_reason = 'parent_terminal'
                child.unresolved_questions = ['The parent frame ended before this child result could be used.']
                db.execute('UPDATE jobs SET record=?,updated=?,lease=0 WHERE id=?',
                    (child.model_dump_json(), now, child.job_id))
            frames.record_terminal(child.job_id, frame.generation, 'cancelled',
                {'status': 'cancelled', 'reason': 'parent_terminal',
                 'unresolved_questions': child.unresolved_questions}, now=now)

    def _packet(self, db, job, payload, tool, *, parents=()):
        cleaned, omissions = mask_payload(payload)
        ident = 'pkt_' + uuid.uuid4().hex
        # UUID encoded as words keeps aliases memorable-shaped and collision-safe.
        letters = ''.join(chr(97 + int(c, 16)) for c in ident[4:])
        sources = {}
        def collect(value):
            if isinstance(value, dict):
                if {'project', 'repository', 'path', 'content_sha256'} <= value.keys():
                    try:
                        source = SourceLocator.model_validate(value)
                        sources[wire(source.model_dump(exclude={'content_sha256'}))] = source
                    except ValueError:
                        pass
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        def collect_command_reads(value):
            """Promote the runner's exact file-read receipt to a source locator.

            ReadOnlyCommandRunner records a path and the digest of the bytes it
            returned.  That is enough for InquiryFreshness to revalidate the
            observation, but without a locator the public answer packet loses
            the source identity.  Keep this conversion deliberately narrow:
            only successful, untruncated direct-cat or bounded-sed receipts
            with a valid SHA256 and an absolute regular-file path are eligible.
            The digest is copied from the receipt, never recomputed or inferred.
            """
            if not isinstance(value, dict) or value.get('tool') != 'command':
                return
            payload = value.get('payload')
            if (not isinstance(payload, dict) or payload.get('status') != 'completed'
                    or payload.get('exit_code') != 0 or payload.get('truncated')
                    or payload.get('timed_out')):
                return
            for read in payload.get('source_reads', []):
                if not isinstance(read, dict):
                    continue
                method = read.get('method')
                ranges = read.get('line_ranges')
                valid_ranges = (method == 'direct_sed_lines'
                    and isinstance(read.get('line_count'), int)
                    and not isinstance(read.get('line_count'), bool)
                    and read['line_count'] > 0
                    and isinstance(ranges, list) and 1 <= len(ranges) <= 16
                    and all(isinstance(item, dict)
                        and isinstance(item.get('start'), int)
                        and not isinstance(item.get('start'), bool)
                        and isinstance(item.get('end'), int)
                        and not isinstance(item.get('end'), bool)
                        and 1 <= item['start'] <= item['end'] <= read['line_count']
                        for item in ranges))
                if method != 'direct_cat' and not valid_ranges:
                    continue
                digest = read.get('content_sha256')
                path = read.get('path')
                if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                        or not isinstance(path, str)):
                    continue
                try:
                    source_path = Path(path)
                    if not source_path.is_absolute():
                        continue
                    source_path = source_path.resolve(strict=True)
                    if not source_path.is_file():
                        continue
                    source = (self.source_locator_provider(job, source_path, digest)
                        if self.source_locator_provider else None)
                    if source is None:
                        continue
                    source = SourceLocator.model_validate(source)
                except (OSError, RuntimeError, ValueError):
                    continue
                sources[wire(source.model_dump(exclude={'content_sha256'}))] = source
        if job.refresh_context:
            prior = job.refresh_context.get('prior_job', {})
            for ref in prior.get('evidence_packets', []) + ([prior['result_packet']] if prior.get('result_packet') else []):
                saved = db.execute('SELECT packet FROM outbox WHERE id=?', (ref,)).fetchone()
                if saved:
                    collect(json.loads(saved['packet']).get('sources', []))
        # Derived answers preserve material source authority even when their text
        # contains no source list. Caller/profile never constrain these sources.
        for row in db.execute('SELECT packet FROM outbox WHERE job=?', (job.job_id,)):
            packet_value = json.loads(row['packet'])
            collect(packet_value.get('sources', []))
            collect_command_reads(packet_value)
        collect(cleaned)
        packet = InformationPacket(packet_id=ident, alias='job-' + letters,
            created_at=stamp(self.clock()), tool=tool, payload=cleaned,
            payload_sha256=canonical_digest(cleaned), sources=list(sources.values()), parents=list(dict.fromkeys(parents)),
            access_scope=job.scope, omissions=omissions,
            freshness=({'dependencies': {f'file:{s.repository}/{s.path}': s.content_sha256 for s in sources.values()}}
                       if tool in {'investigate', 'skill'} and sources
                       else {'volatile': True, 'max_age_seconds': 0}), pinned_by=[job.job_id])
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
                canonical_payload = {k: v for k, v in canonical[ident].items() if k != 'public_tool_call'}
                full = {**canonical_payload, **({'public_tool_call': frame['public_tool_call']} if 'public_tool_call' in frame else {})}
                omission = {'packet_id': ident, 'omissions': ['tool payload exceeded worker context budget']}
                if payload != canonical_payload and not (
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
        wake_parents = False
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                _, job = self._running(db, job_id, attempt)
            except RuntimeError:
                return False
            status = result.get('status')
            if status not in TERMINAL | {'yielding', 'queued_after_eviction'}:
                raise ValueError('invalid worker status')
            job.failure_reason = result.get('reason', job.failure_reason)
            frame_store = FrameStore(db)
            frame = frame_store.get_frame(job_id)
            cooperative_yield = (status == 'yielding' and result.get('reason') in {
                'slice_complete', 'private_wait_proposed', 'preemption_requested',
                'foreground_preemption', 'session_evicted'})
            failed_attempts = 0
            if frame is not None and status not in TERMINAL and status == 'yielding' and not cooperative_yield:
                failed_attempts = frame_store.record_failure_attempt(job_id, attempt, now=self.clock())
            exhausted_attempts = (frame is None and status not in TERMINAL and job.attempt >= 3)
            exhausted_frame_failures = (frame is not None and failed_attempts >= 3)
            if self.clock() >= (job.deadline_epoch or float('inf')) or exhausted_attempts or exhausted_frame_failures:
                status = result['status'] = ('partial' if job.answer or job.findings or job.evidence_packets else 'failed')
                result['reason'] = 'attempt_or_deadline_exhausted'
                result['failure_reason'] = job.failure_reason
            job.status = status
            job.answer = result.get('answer', job.answer)
            job.findings = [Finding.model_validate(f) for f in result.get('findings', [f.model_dump() for f in job.findings])]
            job.unresolved_questions = result.get('unresolved_questions', job.unresolved_questions)
            if status in {'completed', 'partial'} and not ((job.answer or '').strip() or job.findings or job.evidence_packets):
                status = job.status = result['status'] = 'failed'
            private_children, wait_generation, wake_version = (
                self._private_child_context(db, job_id) if frame is not None else ((), None, None))
            if frame is not None and 'protocol_feedback' in result and status not in TERMINAL:
                frame_store.save_protocol_feedback(job_id, job.scope, frame.generation, attempt,
                    result['protocol_feedback'], now=self.clock())
            consumed = result.get('private_child_results_consumed')
            accepted_child_ack = False
            if consumed is not None:
                expected = {item['child_id'] for item in private_children}
                supplied = set(consumed) if isinstance(consumed, list) and all(isinstance(x, str) for x in consumed) else set()
                if not expected or supplied != expected or wait_generation is None or wake_version is None:
                    raise ValueError('invalid_private_child_result_ack')
                if status in TERMINAL:
                    accepted_child_ack = frame_store.prepare_wake_ack(job_id, wait_generation, consumed)
                    if not accepted_child_ack:
                        raise ValueError('private_child_result_ack_not_persisted')
            if status in TERMINAL:
                job.terminal_reason = result.get('reason')
                if frame is not None and not accepted_child_ack:
                    self._cancel_descendants(db, frame_store, job_id, self.clock())
            job = DurableJob.model_validate(job.model_dump())
            if any(p not in job.evidence_packets for f in job.findings for p in f.evidence_packets):
                raise ValueError('unobserved finding evidence')
            if status in TERMINAL:
                public_result = {key: value for key, value in result.items()
                    if key not in {'private_child_results_consumed', 'private_wait',
                                   'model_turns_used', 'cumulative_model_turns_used',
                                   'remaining_model_turns', 'protocol_feedback'}}
                job.result_packet, _ = self._packet(db, job, public_result, job.mode)
                if frame is not None:
                    visible = {key: value for key, value in result.items()
                        if key in {'status', 'answer', 'findings', 'unresolved_questions', 'reason',
                                   'failure_reason', 'evidence_packets', 'result_packet'}}
                    visible['status'] = status
                    visible['result_packet'] = job.result_packet
                    visible['evidence_packets'] = list(job.evidence_packets)
                    if not accepted_child_ack:
                        frame_store.record_terminal(job_id, frame.generation,
                            'expired' if result.get('reason') in {'deadline_exhausted', 'attempt_or_deadline_exhausted'} else status,
                            visible, now=self.clock())
                        wake_parents = frame.parent_id is not None
            db.execute('UPDATE jobs SET record=?,updated=?,lease=0,available=? WHERE id=?',
                (job.model_dump_json(), self.clock(), self.clock() + (0 if result.get('reason') in {'slice_complete', 'private_wait_proposed'} else (self.retry_seconds if self.retry_seconds is not None else (5 if job.attempt == 1 else 15))), job_id))
            self._trim_index(db, job.scope)
        self.reconcile()
        if wake_parents:
            self._wake_ready_parents()
        return True

    def _trim_index(self, db, scope):
        cached = db.execute('SELECT inquiry_index.identity,jobs.record FROM inquiry_index JOIN jobs ON jobs.id=inquiry_index.job ORDER BY jobs.updated DESC,jobs.id DESC').fetchall()
        answered = [r['identity'] for r in cached if self._cache_eligible(json.loads(r['record']))]
        for identity in answered[50:]:
            db.execute('DELETE FROM inquiry_index WHERE identity=?', (identity,))

    def cancel(self, job_id, *, access_scope):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            frames = FrameStore(db)
            if frames.is_private(job_id):
                return False
            row = db.execute('SELECT * FROM jobs WHERE id=? AND scope=?', (job_id, wire(dict(access_scope)))).fetchone()
            if not row:
                return False
            job = DurableJob.model_validate_json(row['record'])
            if job.status in TERMINAL:
                return False
            job.attempt += 1; job.status = 'cancelled'
            db.execute('UPDATE jobs SET record=?,lease=0,updated=? WHERE id=?', (job.model_dump_json(), self.clock(), job_id))
            frame = frames.get_frame(job_id)
            if frame is not None:
                self._cancel_descendants(db, frames, job_id, self.clock())
                frames.record_terminal(job_id, frame.generation, 'cancelled',
                    {'status': 'cancelled', 'reason': 'operator_cancelled'}, now=self.clock())
            self._trim_index(db, job.scope)
        self.reconcile()
        self._consume_pending_frame_acks()
        return True

    def reconcile(self):
        # Serialize retention snapshots across broker processes without a DB transaction.
        with _reconcile_file_lock(self.directory):
            self._reconcile()

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
            inquiry_ids = {r[0] for r in db.execute('SELECT id FROM jobs WHERE inquiry=1')}
            indexed_ids = {r[0] for r in db.execute('SELECT job FROM inquiry_index')}
        terminal = [job for job in jobs if job.status in TERMINAL and job.job_id not in inquiry_ids]
        terminal = terminal[-self.packets.recent_terminal_limit:]
        retained = [j for j in jobs if j.status not in TERMINAL or j.job_id in indexed_ids] + terminal
        retained_packets = {}
        for job in retained:
            refs = job.hints + job.evidence_packets + ([job.result_packet] if job.result_packet else [])
            if job.refresh_context:
                prior = job.refresh_context.get('prior_job', {})
                refs += prior.get('hints', []) + prior.get('evidence_packets', [])
                if prior.get('result_packet'):
                    refs.append(prior['result_packet'])
            retained_packets[job.job_id] = (job.scope, refs)
        # Broker updated times determine recency, independent of packet clock ties.
        retained_ids = {job.job_id for job in retained}
        unpin_owners = [job.job_id for job in jobs if job.job_id not in retained_ids]
        # Historical expiry is a log omission, not a dispatcher outage. The
        # packet store validates all references and updates pins in one
        # transaction using one shared liveness/parent-closure snapshot.
        self.packets.reconcile_retention(retained_packets, unpin_owners)

    @staticmethod
    def _worker_request_bytes(request):
        return len(json.dumps(request, ensure_ascii=False).encode())

    def _checkpoint_public_calls(self, job_id):
        with self._db() as db:
            row = db.execute('SELECT frames FROM job_checkpoints WHERE job=?', (job_id,)).fetchone()
        if row is None:
            return {}
        try:
            frames = json.loads(row['frames'])
        except (TypeError, ValueError):
            return {}
        allowed_tools = SHARED_TOOLS | {'command', 'log'}
        result = {}
        for frame in frames:
            call = frame.get('public_tool_call') if isinstance(frame, dict) else None
            packet_id = frame.get('packet_id') if isinstance(frame, dict) else None
            if (isinstance(packet_id, str) and isinstance(call, dict) and set(call) == {'tool', 'arguments'}
                    and isinstance(call.get('tool'), str) and call.get('tool') in allowed_tools
                    and isinstance(call.get('arguments'), dict)):
                result[packet_id] = call
        return result

    @classmethod
    def _project_worker_request(cls, request, checkpoint_calls):
        """Fit a private worker snapshot without changing durable observations."""
        projected = dict(request)
        normalized = []
        for observation in request.get('observations', []):
            if not isinstance(observation, dict):
                normalized.append(observation)
                continue
            packet_id = observation.get('packet_id')
            if not isinstance(packet_id, str) or not packet_id:
                # Broker observations always have an identity. Keep the input
                # invalid rather than fabricate one if durable state is corrupt.
                normalized.append(observation)
                continue
            call = checkpoint_calls.get(packet_id)
            # Raw packet payloads cannot mint assistant-call history. The only
            # accepted call comes from the broker's validated checkpoint ledger.
            payload = {key: value for key, value in observation.items() if key != 'public_tool_call'}
            if call is not None:
                with_call = {**payload, 'public_tool_call': call}
                if cls._worker_request_bytes(with_call) <= WORKER_OBSERVATION_MAX_BYTES:
                    normalized.append(with_call)
                    continue
            elif cls._worker_request_bytes(payload) <= WORKER_OBSERVATION_MAX_BYTES:
                normalized.append(payload)
                continue

            marker = {'packet_id': packet_id,
                'omissions': ['tool payload exceeded worker context budget']}
            if call is not None:
                with_call = {**marker, 'public_tool_call': call}
                if cls._worker_request_bytes(with_call) <= WORKER_OBSERVATION_MAX_BYTES:
                    marker = with_call
            normalized.append(marker)

        # A trusted base that cannot fit must fail before dispatch. It includes
        # caller-independent trusted scope, hints, roots and schemas unchanged.
        # A request can be projected twice: once after adding host context and
        # again after adding the durable frame continuation fields. Preserve
        # the identities omitted by the first pass so the model sees the full
        # ordered projection and downstream evidence checks can still reject
        # omitted bodies.
        previously_omitted = projected.get('omitted_observation_packet_ids', [])
        if (not isinstance(previously_omitted, list)
                or any(not isinstance(packet_id, str) or not packet_id for packet_id in previously_omitted)):
            previously_omitted = []
        projected['observations'] = []
        projected['omitted_observation_packet_ids'] = list(previously_omitted)
        if cls._worker_request_bytes(projected) > WORKER_JOB_INPUT_MAX_BYTES:
            return None

        # Retain the newest ordered suffix that fits the complete serialized
        # input. Omitted identities remain explicit model context, but are not
        # source evidence. Recompute after each drop because that list costs bytes.
        dropped = 0
        while True:
            projected['observations'] = normalized[dropped:]
            projected['omitted_observation_packet_ids'] = previously_omitted + [
                observation['packet_id'] for observation in normalized[:dropped]
                if isinstance(observation, dict) and isinstance(observation.get('packet_id'), str)]
            if cls._worker_request_bytes(projected) <= WORKER_JOB_INPUT_MAX_BYTES:
                return projected
            if dropped >= len(normalized):
                return None
            dropped += 1

    def _execute(self, job):
        if (job.mode == 'skill' and job.skill is not None
                and job.skill not in getattr(self.worker_factory, 'skills', {})):
            # Historical durable records remain readable, but a removed skill
            # must never be rebound to another current registration.
            self.finish(job.job_id, job.attempt, {'status': 'failed',
                'reason': 'retired_skill_unavailable',
                'unresolved_questions': ['The recorded skill is no longer registered; it was not remapped.']})
            return
        session = None
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(job, heartbeat_stop), daemon=True)
        heartbeat.start()
        try:
            if self.backend:
                origin = None
                with self._db() as db:
                    frames = FrameStore(db)
                    frame = frames.get_frame(job.job_id)
                    allowed = True
                    reason = 'dispatch_policy_changed'
                    if frame is not None:
                        policy = PowerPolicy(db, clock=self.clock)
                        origin = policy.root_demand_origin(frame.root_id)
                        frame_class = ('automatic_root' if frame.origin_class == 'automatic' and frame.parent_id is None
                                       else 'public_root' if frame.parent_id is None else 'private_child')
                        automatic_work = (db.execute('SELECT * FROM pa1_automatic_work WHERE job_id=?',
                            (frame.root_id,)).fetchone() if origin.kind == 'automatic' else None)
                        automatic_permission = (trusted_operator_control().permission('dispatch_automatic')
                                                if origin.kind == 'automatic' else None)
                        decision = policy.allow_dispatch(frame_class, True, root_id=frame.root_id,
                            demand_origin=origin, permission=automatic_permission,
                            focus_id=automatic_work['focus_id'] if automatic_work else None,
                            project=automatic_work['project'] if automatic_work else None)
                        allowed, reason = decision.allowed, decision.reason
                if not allowed:
                    self.finish(job.job_id, job.attempt, {'status': 'yielding', 'reason': reason})
                    return
                # Public inquiries and explicitly enabled finite-focus work
                # use the same pinned unit. The frame policy above admits only
                # explicit public demand or currently eligible opted-in work.
                if frame is not None:
                    unavailable = self._ensure_demand_runtime()
                    if unavailable:
                        self.finish(job.job_id, job.attempt, {'status': 'yielding',
                            'reason': _demand_readiness_failure_reason(unavailable)})
                        return
                    # Startup can take time. Re-read the durable release/focus
                    # policy immediately before owner admission so a stop,
                    # cancel, or expiry during systemd start cannot rewarm a
                    # model slot.
                    still_allowed, reason = self._dispatch_gate(job.job_id, job.attempt)
                    if not still_allowed:
                        self.finish(job.job_id, job.attempt, {'status': 'yielding',
                            'reason': reason or 'dispatch_policy_changed'})
                        return
                response = self.backend.open_sessions(1, compute_profile='narrow', parallelism='default', deadline_epoch=job.deadline_epoch)
                sessions = response.get('session_ids', response.get('sessions', []))
                if response.get('status') != 'available' or not sessions:
                    self.finish(job.job_id, job.attempt, {'status': 'yielding', 'reason': response.get('reason', 'session_unavailable')})
                    return
                session = sessions[0]
                if isinstance(session, dict):
                    session = session['session_id']
                epoch_keys = ('daemon_epoch', 'supervisor_pid', 'supervisor_process_start',
                              'runtime_fingerprint')
                supervisor_epoch = {key: response[key] for key in epoch_keys if key in response}
                tracked_session = bool(supervisor_epoch)
                if (callable(getattr(self.backend, 'release_owned_observer_resources', None))
                        and not supervisor_epoch):
                    raise RuntimeError('observer_supervisor_epoch_missing')
                if tracked_session:
                    if set(supervisor_epoch) != set(epoch_keys):
                        raise RuntimeError('observer_supervisor_epoch_incomplete')
                    with self._db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        ResourceController(db, power_policy=PowerPolicy(db, clock=self.clock),
                            clock=self.clock).record_active_session(session, supervisor_epoch)
            if job.deadline_epoch is not None and self.clock() >= job.deadline_epoch:
                self.finish(job.job_id, job.attempt, {'status': 'partial', 'reason': 'deadline_exhausted',
                    'unresolved_questions': ['The analysis deadline expired during startup.']})
                return
            with self._db() as db:
                private_job = FrameStore(db).is_private(job.job_id)
            stored = (self._worker_lookup(job.job_id, access_scope=job.scope) if private_job
                      else self.lookup(job.job_id, access_scope=job.scope))
            request = {'job_id': job.job_id, 'attempt': job.attempt, 'mode': job.mode,
                'question': job.execution_question or job.question, 'scope': job.scope,
                'deadline_epoch': job.deadline_epoch, 'refresh_context': job.refresh_context,
                'log_guidance': 'Use log for the last 50 answered inquiries; lexical retrieval returns at most five records.',
                'hints': self.packets.assemble_hints(job.hints, access_scope=job.scope),
                'observations': stored['observations'][-24:], 'max_steps': 6}
            if self.inquiry_context_provider is not None:
                # Registered host configuration labels the inquiry target. Hints
                # and model arguments cannot choose repository authority/roots.
                request.update(self.inquiry_context_provider(job))
            if session:
                request['session_id'] = session
            if job.mode == 'skill' and job.skill is not None:
                request['skill'] = self.worker_factory.skills[job.skill]
            request = self._project_worker_request(request, self._checkpoint_public_calls(job.job_id))
            if request is None:
                self.finish(job.job_id, job.attempt, {'status': 'failed',
                    'reason': 'job_input_budget_exhausted',
                    'unresolved_questions': ['Trusted inquiry inputs exceed the bounded worker request size.']})
                return
            worker = self.worker_factory(self, job)
            with self._db() as db:
                frame_store = FrameStore(db)
                frame = frame_store.get_frame(job.job_id)
                private_children, wait_generation, wake_version = self._private_child_context(db, job.job_id)
                protocol_feedback = (frame_store.load_protocol_feedback(job.job_id, job.scope, frame.generation)
                    if frame is not None else [])
            if frame is not None:
                request['model_turns_used'] = frame.turns_used
                request['private_child_results'] = list(private_children)
                if protocol_feedback:
                    request['protocol_feedback'] = protocol_feedback
                policy_id = ('gather-v1' if frame.parent_id is not None
                    or not job.evidence_packets and not private_children else 'synthesis-v1')
                policy = policy_for(policy_id)
                request['trusted_turn_policy_id'] = policy_id
                requested = {'logical_context_tokens': policy.logical_context_tokens,
                    'reasoning_tokens': policy.reasoning_tokens, 'visible_tokens': policy.visible_tokens,
                    'behavior': policy.behavior, 'reasoning_mode': policy.reasoning_mode}
                with self._db() as db:
                    db.execute('''INSERT INTO pa1_frame_attempts(job,attempt,generation,policy_id,requested)
                        VALUES(?,?,?,?,?) ON CONFLICT(job,attempt) DO UPDATE SET
                        generation=excluded.generation,policy_id=excluded.policy_id,requested=excluded.requested''',
                        (job.job_id, job.attempt, frame.generation, policy_id, wire(requested)))
                request = self._project_worker_request(request, self._checkpoint_public_calls(job.job_id))
                if request is None:
                    self.finish(job.job_id, job.attempt, {'status': 'failed',
                        'reason': 'job_input_budget_exhausted',
                        'unresolved_questions': ['Private continuation context exceeds the bounded worker request size.']})
                    return
            # Slice accounting is available only to trusted, durably framed
            # work. Legacy seeded jobs retain their established single-run
            # semantics; they must not get repeated one-turn runs without a
            # cumulative broker budget.
            run_slice = getattr(worker, 'run_slice', None) if frame is not None else None
            if frame is not None:
                # The owner session may have been opened while a release or
                # automatic-window veto committed. Never call the model after
                # that canonical policy change; finally still closes the slot.
                still_allowed, reason = self._dispatch_gate(job.job_id, job.attempt)
                if not still_allowed:
                    self.finish(job.job_id, job.attempt, {'status': 'yielding',
                        'reason': reason or 'dispatch_policy_changed'})
                    return
            if callable(run_slice):
                if frame is not None:
                    with self._db() as db:
                        remaining = FrameStore(db).remaining_turns(job.job_id)
                else:
                    remaining = 1
                if remaining <= 0:
                    self.finish(job.job_id, job.attempt, {'status': 'partial', 'reason': 'model_turn_budget_exhausted',
                        'unresolved_questions': ['The bounded model-turn budget was fully reserved by the controller.']})
                    return
                response = run_slice(request, max_model_turns=1)
            else:
                # Compatibility for old verified producers during staged rollout.
                response = worker.run(request)
            if not isinstance(response, dict):
                raise ValueError('worker_result_must_be_object')
            if frame is not None:
                usage = response.get('usage') if isinstance(response.get('usage'), dict) else {}
                telemetry = {}
                budget = usage.get('budget') if isinstance(usage.get('budget'), dict) else None
                if budget is not None:
                    if set(budget) != {'requested', 'effective', 'used'}:
                        raise ValueError('worker_budget_telemetry_invalid')
                    expected_keys = {
                        'requested': {'logical_context_tokens', 'reasoning_tokens', 'visible_tokens'},
                        'effective': {'logical_context_tokens', 'reasoning_tokens', 'visible_tokens'},
                        'used': {'context_tokens', 'reasoning_tokens', 'visible_tokens'},
                    }
                    for section, allowed in expected_keys.items():
                        values = budget.get(section)
                        if (not isinstance(values, dict) or set(values) != allowed
                                or any(value is not None and (not isinstance(value, int) or isinstance(value, bool))
                                       for value in values.values())):
                            raise ValueError('worker_budget_telemetry_invalid')
                    telemetry['budget'] = budget
                reported_policy = usage.get('turn_policy_id', response.get('policy_id', policy_id))
                if reported_policy != policy_id:
                    raise ValueError('worker_turn_policy_mismatch')
                telemetry['turn_policy_id'] = policy_id
                if telemetry:
                    with self._db() as db:
                        db.execute('UPDATE pa1_frame_attempts SET telemetry=? WHERE job=? AND attempt=?',
                            (wire(telemetry), job.job_id, job.attempt))
            model_turns = response.get('model_turns_used', 0)
            if (not isinstance(model_turns, int) or isinstance(model_turns, bool)
                    or not 0 <= model_turns <= 1):
                raise ValueError('invalid_model_turn_count')
            cumulative_turns = response.get('cumulative_model_turns_used')
            if frame is not None and cumulative_turns is not None:
                if (not isinstance(cumulative_turns, int) or isinstance(cumulative_turns, bool)
                        or cumulative_turns != frame.turns_used + model_turns):
                    raise ValueError('cumulative_model_turn_count_mismatch')
            if frame is not None and model_turns:
                with self._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    fresh = FrameStore(db).get_frame(job.job_id)
                    if fresh is None or fresh.generation != frame.generation:
                        raise RuntimeError('stale_frame_generation')
                    FrameStore(db).consume_turns(job.job_id, frame.generation, model_turns,
                        attempt=job.attempt, now=self.clock())
            if response.get('reason') == 'private_wait_proposed':
                self._private_wait(job, response)
                return
            if private_children and response.get('private_child_results_consumed') is not None:
                consumed = response.get('private_child_results_consumed')
                if (not isinstance(consumed, list) or set(consumed) != {entry['child_id'] for entry in private_children}
                        or wait_generation is None or wake_version is None):
                    raise ValueError('invalid_private_child_result_ack')
            self.finish(job.job_id, job.attempt, response)
        except Exception as error:
            self.last_error = type(error).__name__
            self.finish(job.job_id, job.attempt, {'status': 'partial', 'reason': type(error).__name__,
                'unresolved_questions': ['Retry from retained observations']})
        finally:
            cleanup_failed = False
            cleanup_error_code = None
            try:
                if session:
                    try:
                        closed = self.backend.close_session(session)
                    except Exception:
                        # Keep the diagnostic bounded and private.  The raw
                        # transport exception can contain endpoint details and
                        # must not become a job or model observation.
                        cleanup_error_code = 'observer_session_close_failed'
                        raise
                    if isinstance(closed, dict) and (closed.get('released') is False
                            or closed.get('status') in {'unavailable', 'busy', 'failed'}):
                        cleanup_error_code = 'observer_session_close_unproved'
                        raise RuntimeError(cleanup_error_code)
                    if tracked_session:
                        receipt = closed.get('owned_resource_receipt') if isinstance(closed, dict) else None
                        if isinstance(receipt, dict):
                            try:
                                with self._db() as db:
                                    db.execute('BEGIN IMMEDIATE')
                                    ResourceController(db, power_policy=PowerPolicy(db, clock=self.clock),
                                        clock=self.clock).record_session(session, receipt)
                            except Exception as error:
                                cleanup_error_code = 'observer_close_receipt_persist_failed'
                                # ResourceControllerError messages are stable
                                # internal codes. Preserve only the narrow
                                # allowlist relevant to recording this receipt;
                                # never persist arbitrary exception text.
                                if (isinstance(error, ResourceControllerError)
                                        and str(error) in _CLOSE_RECEIPT_ERROR_CODES):
                                    cleanup_error_code += '_' + str(error)
                                raise
                        else:
                            # The ordinary idle close may succeed while its exact
                            # process-bound capability is unavailable. Preserve
                            # that state as unverified so global release remains
                            # pending instead of claiming device quiescence.
                            self.last_error = 'observer_close_receipt_missing'
            except Exception:
                cleanup_failed = True
                self.last_error = cleanup_error_code or 'observer_session_cleanup_failed'
                with self._db() as db:
                    db.execute('UPDATE execution_slots SET cleanup_failed=1 WHERE job=? AND attempt=?', (job.job_id, job.attempt))
            finally:
                heartbeat_stop.set(); heartbeat.join()
                if not cleanup_failed:
                    with self._db() as db:
                        db.execute('DELETE FROM execution_slots WHERE job=? AND attempt=?',
                            (job.job_id, job.attempt))
                        # Only arm children after the parent's physical session
                        # has been closed and its slot has been removed.
                        frames = FrameStore(db)
                        frame = frames.get_frame(job.job_id)
                        if frame is not None and frame.state == 'waiting':
                            wait = db.execute('SELECT generation FROM pa1_frame_waits WHERE parent_id=? ORDER BY generation DESC LIMIT 1',
                                (job.job_id,)).fetchone()
                            if wait:
                                frames.mark_parent_released(job.job_id, int(wait['generation']), now=self.clock())
                    self._consume_pending_frame_acks()
                    self._reconcile_owned_release()
                self._wake.set()

    def _drain(self):
        while not self._stop.is_set():
            try:
                preflight = self._dispatch_preflight()
                if preflight is False or preflight is None:
                    self._wake.wait(1); self._wake.clear()
                    continue
                self.reconcile()
                job = self.claim(_dispatch=True)
                if job:
                    self._execute(job)
                    continue
            except Exception as error:
                self.last_error = type(error).__name__
            self._wake.wait(1); self._wake.clear()

    def _dispatch_preflight(self, *, force=False):
        status = getattr(self.backend, 'central_status', None) if self.backend is not None else None
        with self._db() as db:
            rows = db.execute('''SELECT id FROM jobs
                WHERE json_extract(record,'$.status') NOT IN ('completed','partial','failed','cancelled')''').fetchall()
            has_slots = db.execute('SELECT 1 FROM execution_slots LIMIT 1').fetchone()
            has_pending_outbox = db.execute('SELECT 1 FROM outbox WHERE materialized=0 LIMIT 1').fetchone()
        candidates = frozenset(row['id'] for row in rows)
        has_work = bool(candidates or has_slots or has_pending_outbox)
        if not callable(status):
            return True if has_work and not force else None
        if not force and not has_work:
            return None

        with self._central_preflight_lock:
            checked = time.monotonic()
            if (not self._central_preflight_allowed
                    and checked - self._central_preflight_checked < 1.0):
                # Only a cold/unreachable socket can yield to a demand start.
                # Identity/contract failures must park durable work until the
                # runtime is repaired, even if a candidate is opt-in eligible.
                return candidates if candidates and self._central_preflight_cold else False
            try:
                value = status()
                if not isinstance(value, dict):
                    raise RuntimeError('central_supervisor_status_invalid')
                if value.get('status') not in (None, 'available'):
                    raise RuntimeError(str(value.get('reason') or 'central_supervisor_unavailable'))
            except Exception as error:
                reason = str(error).strip()[:500] or type(error).__name__
                self.last_error = reason
                self._central_preflight_error = reason
                self._central_preflight_allowed = False
                self._central_preflight_cold = reason in _COLD_SUPERVISOR_FAILURES
                self._central_preflight_checked = checked
                return candidates if candidates and self._central_preflight_cold else False
            else:
                if self._central_preflight_error and self.last_error == self._central_preflight_error:
                    self.last_error = None
                self._central_preflight_error = None
                self._central_preflight_allowed = True
                self._central_preflight_cold = False
            self._central_preflight_checked = checked
            return candidates if force and self._central_preflight_allowed else self._central_preflight_allowed
