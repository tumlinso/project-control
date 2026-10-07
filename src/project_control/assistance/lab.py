"""Operator-controlled, source-bound scratch experiments and patch candidates.

The service keeps experiment records in a private journal. A run is recorded as
an effect intent before the contained runner starts, and intent rows are never
replayed automatically. Candidate patches remain private artifacts and this
module has no canonical apply or Todo publication path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import sqlite3
import stat
import time
import uuid
from typing import Any, Callable, Mapping, Sequence

from ..adapters.git import GitReadAdapter
from ..observer_analysis import observer_analysis_state_root
from .lab_snapshot import (
    MAX_MANIFEST_BYTES, MAX_PATH_BYTES, MAX_PATH_DEPTH,
    MAX_SELECTION_METADATA_BYTES, MAX_SELECTION_PATHS,
    MAX_SOURCE_DIRECTORIES, MAX_SOURCE_FILES, Snapshot, SnapshotError,
)
from . import lab_runner


_MAX_TEXT = 8192
_MAX_PATCH_BYTES = 1024 * 1024
_CAPABILITY_SEAL = object()


class LabError(ValueError):
    """A requested experiment or candidate does not satisfy the LAB contract."""


@dataclass(frozen=True, slots=True)
class LabProject:
    """Trusted project registration supplied by the cold operator adapter."""

    project: str
    root: Path
    repository: str


@dataclass(frozen=True, slots=True)
class LabSelection:
    """One explicit operator-selected experiment and its evidence contract."""

    project: str
    source_paths: tuple[str, ...]
    hypothesis: str
    argv: tuple[str, ...]
    reference: str
    expected_measurements: str
    stop_rule: str
    base_revision: str = "HEAD"


@dataclass(frozen=True, slots=True)
class LabOperator:
    """Local operator capability minted by a configured LabService."""

    _seal: object


@dataclass(frozen=True, slots=True)
class LabGrant:
    """Immutable execution permission bound to one captured source generation."""

    experiment_id: str
    project: str
    source_root: str
    repository: str
    generation: int
    snapshot_root: str
    snapshot_head: str
    snapshot_base: str
    status_fingerprint: str
    snapshot_manifest_sha256: str
    argv: tuple[str, ...]
    created_at: float


class LabService:
    """Private journal and execution adapter for the CPU scratch laboratory.

    ``projects`` must come from trusted operator configuration. Neither a model
    proposal nor a request payload can register roots or broaden source scope.
    """

    def __init__(self, *, projects: Mapping[str, LabProject],
                 state_root: str | Path | None = None,
                 clock: Callable[[], float] = time.time):
        self.projects = dict(projects)
        for key, project in self.projects.items():
            if not isinstance(project, LabProject) or key != project.project:
                raise LabError("trusted project registration is malformed")
            root = Path(project.root).resolve(strict=True)
            if not root.is_dir() or not project.repository:
                raise LabError("trusted project root or repository identity is invalid")
        self.state_root = (Path(state_root) if state_root is not None else
                           observer_analysis_state_root(create=False) / "lab")
        self.db_path = self.state_root / "lab.sqlite3"
        self.clock = clock

    def operator(self) -> LabOperator:
        """Mint the explicit local operator capability used by cold CLI calls."""
        return LabOperator(_CAPABILITY_SEAL)

    def _require_operator(self, operator: LabOperator) -> None:
        if not isinstance(operator, LabOperator) or operator._seal is not _CAPABILITY_SEAL:
            raise PermissionError("trusted local operator capability required")

    def _open(self, *, create: bool) -> sqlite3.Connection | None:
        if create:
            if self.state_root.is_symlink():
                raise LabError("private journal directory must not be a symlink")
            self.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                self.state_root.chmod(0o700)
            except OSError as exc:
                raise LabError("private journal directory permissions could not be enforced") from exc
            self._validate_private_directory()
            if self.db_path.exists() or self.db_path.is_symlink():
                self._validate_private_database()
            else:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                try:
                    descriptor = os.open(self.db_path, flags, 0o600)
                except FileExistsError:
                    # Another selector may have initialized the private DB
                    # after our existence check. Accept only the same strict
                    # owner/mode/type contract.
                    self._validate_private_database()
                except OSError as exc:
                    raise LabError("private journal database could not be created safely") from exc
                else:
                    try:
                        os.fchmod(descriptor, 0o600)
                    except OSError as exc:
                        os.close(descriptor)
                        raise LabError("private journal database permissions could not be enforced") from exc
                    finally:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                self._validate_private_database()
            try:
                db = sqlite3.connect(self.db_path, timeout=10)
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA journal_mode=DELETE")
                db.execute("PRAGMA synchronous=FULL")
                self._initialize(db)
                self._validate_private_database()
                return db
            except BaseException:
                if "db" in locals():
                    db.close()
                raise
        if not self.db_path.exists() and not self.db_path.is_symlink():
            return None
        self._validate_private_directory()
        self._validate_private_database()
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        return db

    def _validate_private_directory(self) -> None:
        try:
            info = self.state_root.lstat()
        except OSError as exc:
            raise LabError("private journal directory is unavailable") from exc
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise LabError("private journal directory must be owned and mode 0700")

    def _validate_private_database(self) -> None:
        try:
            info = self.db_path.lstat()
        except OSError as exc:
            raise LabError("private journal database is unavailable") from exc
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise LabError("private journal database must be owned and mode 0600")

    @staticmethod
    def _initialize(db: sqlite3.Connection) -> None:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS lab_experiments(
                experiment_id TEXT PRIMARY KEY, project TEXT NOT NULL,
                source_root TEXT NOT NULL, repository TEXT NOT NULL,
                generation INTEGER NOT NULL, snapshot_root TEXT NOT NULL,
                snapshot_head TEXT NOT NULL, snapshot_base TEXT NOT NULL,
                status_fingerprint TEXT NOT NULL, snapshot_manifest_sha256 TEXT NOT NULL,
                source_manifest TEXT NOT NULL, selection TEXT NOT NULL,
                state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(project, generation));
            CREATE TABLE IF NOT EXISTS lab_attempts(
                attempt_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                generation INTEGER NOT NULL, effect_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL, outcome TEXT, intent_at REAL NOT NULL,
                started_at REAL, finished_at REAL, argv TEXT NOT NULL,
                grant_json TEXT NOT NULL, receipt_json TEXT,
                process_identity TEXT, error TEXT,
                FOREIGN KEY(experiment_id) REFERENCES lab_experiments(experiment_id));
            CREATE TABLE IF NOT EXISTS lab_candidates(
                candidate_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                generation INTEGER NOT NULL, patch_path TEXT NOT NULL,
                patch_sha256 TEXT NOT NULL, patch_bytes INTEGER NOT NULL,
                capture_base TEXT NOT NULL, capture_head TEXT NOT NULL,
                status_fingerprint TEXT NOT NULL, snapshot_manifest_sha256 TEXT NOT NULL,
                created_at REAL NOT NULL, FOREIGN KEY(experiment_id)
                REFERENCES lab_experiments(experiment_id));
            CREATE INDEX IF NOT EXISTS lab_attempts_exp
                ON lab_attempts(experiment_id, intent_at);
        """)

    def select(self, operator: LabOperator, selection: LabSelection, *,
               max_source_bytes: int = 50 * 1024 * 1024) -> LabGrant:
        """Capture selected registered source and issue a generation-bound grant."""
        self._require_operator(operator)
        if not isinstance(selection, LabSelection):
            raise LabError("typed operator selection is required")
        project = self.projects.get(selection.project)
        if project is None:
            raise PermissionError("project_not_registered")
        self._validate_selection(selection)
        db = self._open(create=True)
        assert db is not None
        experiment_id = "lab_" + uuid.uuid4().hex
        capture_dir = self.state_root / "experiments" / experiment_id / "snapshot"
        snapshot_created = False
        try:
            # Serialize generation allocation with the snapshot insert. This
            # avoids two concurrent selections reserving the same generation.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT COALESCE(MAX(generation),0)+1 FROM lab_experiments WHERE project=?",
                             (project.project,)).fetchone()
            generation = int(row[0])
            project_root = Path(project.root).resolve(strict=True)
            capture_dir.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
            try:
                snapshot = Snapshot.capture(project_root, selection.source_paths, capture_dir,
                                            max_bytes=max_source_bytes,
                                            base_revision=selection.base_revision)
            except SnapshotError as exc:
                raise LabError(str(exc)) from exc
            snapshot_created = True
            manifest_path = snapshot.destination / ".lab-snapshot.json"
            manifest_sha = _sha256_file(manifest_path)
            source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            selected = asdict(selection)
            selected["source_paths"] = list(selection.source_paths)
            selected["argv"] = list(selection.argv)
            now = float(self.clock())
            db.execute("""INSERT INTO lab_experiments(
                experiment_id,project,source_root,repository,generation,snapshot_root,
                snapshot_head,snapshot_base,status_fingerprint,snapshot_manifest_sha256,
                source_manifest,selection,state,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (experiment_id, project.project, str(project_root), project.repository,
                 generation, str(snapshot.destination), snapshot.head_commit, snapshot.base_commit,
                 snapshot.status_fingerprint, manifest_sha, _dump(source_manifest), _dump(selected),
                 "selected", now, now))
            db.commit()
            return LabGrant(experiment_id, project.project, str(project_root), project.repository,
                            generation, str(snapshot.destination), snapshot.head_commit,
                            snapshot.base_commit, snapshot.status_fingerprint, manifest_sha,
                            selection.argv, now)
        except BaseException:
            db.rollback()
            # This capture target was generated uniquely for this attempt and
            # cannot overlap a previous experiment or the registered source.
            if snapshot_created:
                shutil.rmtree(capture_dir, ignore_errors=True)
            shutil.rmtree(capture_dir.parent, ignore_errors=True)
            raise
        finally:
            db.close()

    @staticmethod
    def _validate_selection(selection: LabSelection) -> None:
        for label, value in (("hypothesis", selection.hypothesis),
                             ("reference", selection.reference),
                             ("expected_measurements", selection.expected_measurements),
                             ("stop_rule", selection.stop_rule)):
            if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > _MAX_TEXT:
                raise LabError(f"{label} must be non-empty and bounded")
        if not isinstance(selection.source_paths, tuple) or not selection.source_paths:
            raise LabError("explicit source paths are required")
        if len(selection.source_paths) > MAX_SELECTION_PATHS:
            raise LabError("source path count exceeds the limit")
        if any(not isinstance(path, str) for path in selection.source_paths):
            raise LabError("source selection metadata must contain strings")
        try:
            selection_bytes = sum(len(path.encode("utf-8")) for path in selection.source_paths)
        except UnicodeEncodeError as exc:
            raise LabError("source selection metadata must be valid UTF-8") from exc
        if selection_bytes > MAX_SELECTION_METADATA_BYTES:
            raise LabError("source selection metadata exceeds the UTF-8 byte limit")
        if (not isinstance(selection.argv, tuple) or not selection.argv
                or len(selection.argv) > 64
                or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in selection.argv)):
            raise LabError("argv must be a bounded non-empty argument vector")
        if (any(len(arg.encode("utf-8")) > 2048 for arg in selection.argv)
                or sum(len(arg.encode("utf-8")) for arg in selection.argv) > 12 * 1024):
            raise LabError("argv exceeds the runner argument byte limit")
        if not isinstance(selection.base_revision, str) or not selection.base_revision:
            raise LabError("base revision is required")

    def run(self, operator: LabOperator, grant: LabGrant) -> dict[str, Any]:
        """Execute one captured generation, recording intent before launch."""
        self._require_operator(operator)
        if not isinstance(grant, LabGrant):
            raise LabError("typed source-bound lab grant is required")
        attempt_id = "attempt_" + uuid.uuid4().hex
        effect_id = "effect_" + uuid.uuid4().hex
        attempt_root = self.state_root / "experiments" / grant.experiment_id / attempt_id
        attempt_root.mkdir(mode=0o700, parents=True, exist_ok=False)
        os.chmod(attempt_root, 0o700)
        db = self._open(create=True)
        assert db is not None
        now = float(self.clock())
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM lab_experiments WHERE experiment_id=?",
                             (grant.experiment_id,)).fetchone()
            self._check_grant(row, grant)
            if row["state"] != "selected":
                raise LabError("experiment generation is already consumed or not runnable")
            snapshot = Path(row["snapshot_root"]).resolve(strict=True)
            if snapshot != Path(grant.snapshot_root).resolve(strict=True):
                raise LabError("grant snapshot identity changed")
            self._check_manifest(row, snapshot)
            run_grant = lab_runner.ExecutionGrant()
            grant_json = _dump(asdict(run_grant))
            # Durable intent precedes runner invocation. On any interruption this
            # row is reconciled and never causes an automatic second execution.
            db.execute("""INSERT INTO lab_attempts(
                attempt_id,experiment_id,generation,effect_id,state,intent_at,argv,grant_json)
                VALUES(?,?,?,?,?,?,?,?)""",
                (attempt_id, grant.experiment_id, grant.generation, effect_id, "intent",
                 now, _dump(list(grant.argv)), grant_json))
            db.execute("UPDATE lab_experiments SET state='intent',updated_at=? WHERE experiment_id=?",
                       (now, grant.experiment_id))
            db.commit()
        except BaseException:
            db.rollback()
            db.close()
            raise
        db.close()

        started = float(self.clock())
        try:
            result = lab_runner.run_cpu(Path(grant.snapshot_root), grant.argv, run_grant,
                                        attempt_root=attempt_root, effect_id=effect_id,
                                        on_started=lambda identity: self._mark_started(
                                            attempt_id, identity))
        except lab_runner.EffectCleanupUnverified as exc:
            self._retain_cleanup_pending(attempt_id, exc.identity, str(exc))
            raise
        except BaseException as exc:
            self._finish_interrupted(attempt_id, str(exc))
            raise
        finished = float(self.clock())
        outcome = _outcome(result)
        receipt = _result_receipt(result, grant, started, finished, attempt_id)
        self._finish_attempt(attempt_id, outcome, receipt, result)
        return receipt

    def _check_grant(self, row: sqlite3.Row | None, grant: LabGrant) -> None:
        if row is None:
            raise LabError("experiment grant is unknown")
        identity = (row["project"], row["source_root"], row["repository"], row["generation"],
                    row["snapshot_root"], row["snapshot_head"], row["snapshot_base"],
                    row["status_fingerprint"], row["snapshot_manifest_sha256"])
        supplied = (grant.project, grant.source_root, grant.repository, grant.generation,
                    grant.snapshot_root, grant.snapshot_head, grant.snapshot_base,
                    grant.status_fingerprint, grant.snapshot_manifest_sha256)
        if identity != supplied or row["experiment_id"] != grant.experiment_id:
            raise LabError("grant does not match persisted experiment generation")
        selection = json.loads(row["selection"])
        if tuple(selection["argv"]) != grant.argv:
            raise LabError("grant argv differs from persisted operator selection")

    @staticmethod
    def _check_manifest(row: sqlite3.Row, snapshot: Path) -> None:
        manifest = _verify_snapshot(snapshot, row["snapshot_manifest_sha256"])
        if (manifest.get("head_commit") != row["snapshot_head"]
                or manifest.get("base_commit") != row["snapshot_base"]
                or manifest.get("status_fingerprint") != row["status_fingerprint"]):
            raise LabError("captured source manifest identity mismatch")

    def _finish_interrupted(self, attempt_id: str, error: str) -> None:
        db = self._open(create=True)
        assert db is not None
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT experiment_id,process_identity FROM lab_attempts WHERE attempt_id=?",
                             (attempt_id,)).fetchone()
            if row is not None and row["process_identity"]:
                state, error_value = "running", "cleanup_unverified:" + error[:768]
            else:
                state, error_value = "unknown", error[:1024]
            db.execute("UPDATE lab_attempts SET state=?,outcome='inconclusive',error=? WHERE attempt_id=?",
                       (state, error_value, attempt_id))
            if row:
                db.execute("UPDATE lab_experiments SET state=?,updated_at=? WHERE experiment_id=?",
                           (state, float(self.clock()), row["experiment_id"]))
            db.commit()
        finally:
            db.close()

    def _retain_cleanup_pending(self, attempt_id: str, identity: Any, error: str) -> None:
        db = self._open(create=True)
        assert db is not None
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT experiment_id FROM lab_attempts WHERE attempt_id=?",
                             (attempt_id,)).fetchone()
            if row is None:
                raise LabError("effect intent is unavailable for cleanup reconciliation")
            db.execute("UPDATE lab_attempts SET state='running',outcome='inconclusive',error=?,process_identity=? "
                       "WHERE attempt_id=?",
                       ("cleanup_unverified:" + error[:768], _dump(asdict(identity)), attempt_id))
            db.execute("UPDATE lab_experiments SET state='running',updated_at=? WHERE experiment_id=?",
                       (float(self.clock()), row["experiment_id"]))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _mark_started(self, attempt_id: str, identity: Any) -> None:
        """Durably bind the prelaunch intent to the exact owned process."""
        db = self._open(create=True)
        assert db is not None
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT experiment_id,state FROM lab_attempts WHERE attempt_id=?",
                             (attempt_id,)).fetchone()
            if row is None or row["state"] != "intent":
                raise LabError("effect intent is unavailable for process ownership")
            db.execute("UPDATE lab_attempts SET state='running',started_at=?,process_identity=? "
                       "WHERE attempt_id=?",
                       (float(self.clock()), _dump(asdict(identity)), attempt_id))
            db.execute("UPDATE lab_experiments SET state='running',updated_at=? WHERE experiment_id=?",
                       (float(self.clock()), row["experiment_id"]))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _finish_attempt(self, attempt_id: str, outcome: str,
                        receipt: Mapping[str, Any], result: Any) -> None:
        process_identity = getattr(result, "process_identity", None)
        identity_json = _dump(asdict(process_identity)) if process_identity is not None else None
        db = self._open(create=True)
        assert db is not None
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT experiment_id FROM lab_attempts WHERE attempt_id=?",
                             (attempt_id,)).fetchone()
            if row is None:
                raise LabError("attempt intent disappeared before receipt commit")
            cleanup_verified = bool(getattr(result, "cleanup_verified", False))
            if cleanup_verified:
                db.execute("UPDATE lab_attempts SET state='finished',outcome=?,started_at=?,finished_at=?,"
                           "receipt_json=?,process_identity=?,error=NULL WHERE attempt_id=?",
                           (outcome, receipt["started_at"], receipt["finished_at"], _dump(dict(receipt)),
                            identity_json, attempt_id))
                state = "finished"
            else:
                db.execute("UPDATE lab_attempts SET state='running',outcome='inconclusive',started_at=?,"
                           "receipt_json=?,process_identity=?,error='cleanup_unverified' WHERE attempt_id=?",
                           (receipt["started_at"], _dump(dict(receipt)), identity_json, attempt_id))
                state = "running"
            db.execute("UPDATE lab_experiments SET state=?,updated_at=? WHERE experiment_id=?",
                       (state, float(self.clock()), row["experiment_id"]))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def reconcile(self, operator: LabOperator) -> list[dict[str, Any]]:
        """Reconcile unfinished effects by exact process identity; never replay."""
        self._require_operator(operator)
        db = self._open(create=False)
        if db is None:
            return []
        try:
            pending = db.execute("SELECT * FROM lab_attempts WHERE state IN ('intent','running')").fetchall()
        finally:
            db.close()
        outcomes: list[dict[str, Any]] = []
        for row in pending:
            identity = json.loads(row["process_identity"]) if row["process_identity"] else None
            if identity is None:
                observed = "unknown"
            else:
                try:
                    observed = lab_runner.reconcile_process(lab_runner.OwnedProcessIdentity(**identity))
                except Exception:
                    observed = "unknown"
            cleanup_pending = bool(row["error"] and str(row["error"]).startswith("cleanup_unverified"))
            if observed != "active" and not (observed == "unknown" and cleanup_pending):
                self._reconcile_terminal(row["attempt_id"], row["experiment_id"], observed)
            outcomes.append({"attempt_id": row["attempt_id"], "effect_id": row["effect_id"],
                             "state": "active" if observed == "active" else "unknown",
                             "process_observation": observed, "replayed": False})
        return outcomes

    def _reconcile_terminal(self, attempt_id: str, experiment_id: str, observed: str) -> None:
        db = self._open(create=True)
        assert db is not None
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE lab_attempts SET state='unknown',outcome='inconclusive',error=? WHERE attempt_id=?",
                       (f"interrupted_effect_{observed}", attempt_id))
            db.execute("UPDATE lab_experiments SET state='unknown',updated_at=? WHERE experiment_id=?",
                       (float(self.clock()), experiment_id))
            db.commit()
        finally:
            db.close()

    def status(self, operator: LabOperator, experiment_id: str | None = None) -> dict[str, Any]:
        """Read private journal state and reconcile pending ownership first."""
        self._require_operator(operator)
        reconciled = self.reconcile(operator)
        db = self._open(create=False)
        if db is None:
            return {"experiments": [], "reconciled": reconciled}
        try:
            if experiment_id is None:
                rows = db.execute("SELECT * FROM lab_experiments ORDER BY created_at,experiment_id").fetchall()
            else:
                rows = db.execute("SELECT * FROM lab_experiments WHERE experiment_id=?",
                                  (experiment_id,)).fetchall()
            experiments = []
            for row in rows:
                item = dict(row)
                item["source_manifest"] = json.loads(item.pop("source_manifest"))
                item["selection"] = json.loads(item.pop("selection"))
                attempt_rows = db.execute("SELECT * FROM lab_attempts WHERE experiment_id=? ORDER BY intent_at",
                                          (row["experiment_id"],)).fetchall()
                item["attempts"] = [_attempt_public(attempt) for attempt in attempt_rows]
                experiments.append(item)
            return {"experiments": experiments, "reconciled": reconciled}
        finally:
            db.close()

    def create_candidate(self, operator: LabOperator, experiment_id: str,
                         patch_text: str) -> dict[str, Any]:
        """Persist a source-bound, unpromoted patch candidate from a finished run."""
        self._require_operator(operator)
        if not isinstance(patch_text, str) or not patch_text.startswith("diff --git "):
            raise LabError("candidate must be a unified git diff")
        patch_bytes = patch_text.encode("utf-8")
        if len(patch_bytes) > _MAX_PATCH_BYTES:
            raise LabError("candidate patch exceeds 1 MiB")
        db = self._open(create=True)
        assert db is not None
        try:
            row = db.execute("SELECT * FROM lab_experiments WHERE experiment_id=?",
                             (experiment_id,)).fetchone()
            if row is None or row["state"] != "finished":
                raise LabError("candidate requires a finished experiment")
            candidate_id = "candidate_" + uuid.uuid4().hex
            patch_path = self.state_root / "experiments" / experiment_id / f"{candidate_id}.patch"
            _write_private(patch_path, patch_bytes)
            digest = hashlib.sha256(patch_bytes).hexdigest()
            source_manifest = json.loads(row["source_manifest"])
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO lab_candidates(candidate_id,experiment_id,generation,
                patch_path,patch_sha256,patch_bytes,capture_base,capture_head,status_fingerprint,
                snapshot_manifest_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (candidate_id, experiment_id, row["generation"], str(patch_path), digest,
                 len(patch_bytes), row["snapshot_base"], row["snapshot_head"],
                 row["status_fingerprint"], row["snapshot_manifest_sha256"], float(self.clock())))
            db.commit()
            return {"candidate_id": candidate_id, "experiment_id": experiment_id,
                    "generation": row["generation"], "status": "unpromoted",
                    "patch_sha256": digest, "patch_bytes": len(patch_bytes),
                    "capture_base": row["snapshot_base"], "capture_head": row["snapshot_head"],
                    "source_manifest_sha256": row["snapshot_manifest_sha256"],
                    "captured_paths": [item["path"] for item in source_manifest["files"]],
                    "canonical_applied": False}
        finally:
            db.close()

    def verify_candidate(self, operator: LabOperator, candidate_id: str) -> dict[str, Any]:
        """Check patch integrity and exact captured/current source identity."""
        self._require_operator(operator)
        db = self._open(create=False)
        if db is None:
            raise LabError("candidate does not exist")
        try:
            row = db.execute("""SELECT c.*,e.source_root,e.repository,e.snapshot_root,e.selection
                FROM lab_candidates c JOIN lab_experiments e USING(experiment_id)
                WHERE c.candidate_id=?""", (candidate_id,)).fetchone()
            if row is None:
                raise LabError("candidate does not exist")
            patch = Path(row["patch_path"])
            try:
                patch_ok = (not patch.is_symlink() and patch.is_file()
                            and _sha256_file(patch) == row["patch_sha256"])
            except (OSError, LabError):
                patch_ok = False
            source_ok, reason = _current_source_matches(row)
            return {"candidate_id": candidate_id, "status": "verified" if patch_ok and source_ok else "stale",
                    "patch_integrity": "current" if patch_ok else "changed_or_missing",
                    "source_identity": "current" if source_ok else reason,
                    "capture_base": row["capture_base"], "capture_head": row["capture_head"],
                    "current_source_matches_capture": source_ok,
                    "canonical_applied": False, "todo_mutated": False}
        finally:
            db.close()


def _current_source_matches(row: sqlite3.Row) -> tuple[bool, str]:
    root = Path(row["source_root"])
    try:
        manifest = _verify_snapshot(Path(row["snapshot_root"]), row["snapshot_manifest_sha256"])
        if (manifest.get("base_commit") != row["capture_base"]
                or manifest.get("head_commit") != row["capture_head"]
                or manifest.get("status_fingerprint") != row["status_fingerprint"]):
            return False, "capture_identity_mismatch"
        identity = GitReadAdapter(root).identity()
        if identity.commit != row["capture_head"]:
            return False, "head_changed"
        if identity.status_fingerprint != row["status_fingerprint"]:
            return False, "source_status_changed"
        expected_files = {entry["path"]: entry for entry in manifest["files"]}
        selection = json.loads(row["selection"])
        current_paths = _enumerate_selected_source(root, selection["source_paths"])
        if current_paths != set(expected_files):
            return False, "captured_inventory_changed"
        for relative, entry in expected_files.items():
            observed = _hash_current_source_file(root, relative)
            expected = (entry["mode"], entry["size"], entry["sha256"])
            if observed != expected:
                return False, "captured_file_or_mode_changed"
        return True, "current"
    except LabError:
        return False, "captured_snapshot_changed"
    except Exception:
        return False, "source_unavailable"


def _enumerate_selected_source(root: Path, selections: Sequence[str]) -> set[str]:
    """Enumerate selected current source without following symlink directories."""
    files: set[str] = set()
    resolved_root = root.resolve(strict=True)
    for selected in selections:
        path = PurePosixPath(selected)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise LabError("registered source selection is no longer canonical")
        candidate = resolved_root.joinpath(*path.parts)
        current = resolved_root
        for part in path.parts:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise LabError("selected current source contains a symlink")
        info = candidate.lstat()
        if stat.S_ISREG(info.st_mode):
            files.add(path.as_posix())
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise LabError("selected current source is not a regular file or directory")
        for directory, dirnames, filenames in os.walk(candidate, followlinks=False):
            directory_path = Path(directory)
            for dirname in list(dirnames):
                child = directory_path / dirname
                if stat.S_ISLNK(child.lstat().st_mode):
                    raise LabError("selected current source contains a symlink directory")
            for filename in filenames:
                child = directory_path / filename
                relative = child.relative_to(resolved_root).as_posix()
                child_info = child.lstat()
                if not stat.S_ISREG(child_info.st_mode):
                    raise LabError(f"selected current source contains a special file: {relative}")
                files.add(relative)
    return files


def _hash_current_source_file(root: Path, relative: str) -> tuple[int, int, str]:
    path = PurePosixPath(relative)
    parts = path.parts
    if not parts or path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise LabError("captured source path is not canonical")
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                       | getattr(os, "O_NOFOLLOW", 0))
    current_fd = root_fd
    opened_dirs: list[int] = []
    try:
        for part in parts[:-1]:
            info = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise LabError("captured source parent is not a directory")
            child_fd = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                               | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
            opened = os.fstat(child_fd)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                os.close(child_fd)
                raise LabError("captured source parent changed during verification")
            opened_dirs.append(child_fd)
            current_fd = child_fd
        before = os.stat(parts[-1], dir_fd=current_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise LabError("captured source file is not regular")
        fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
        digest = hashlib.sha256()
        size = 0
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino, opened.st_size, stat.S_IMODE(opened.st_mode)) != (
                    before.st_dev, before.st_ino, before.st_size, stat.S_IMODE(before.st_mode)):
                raise LabError("captured source file changed during verification")
            while True:
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                size += len(block)
                digest.update(block)
            after = os.fstat(fd)
            if (after.st_dev, after.st_ino, after.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
                raise LabError("captured source file changed during verification")
        finally:
            os.close(fd)
        return stat.S_IMODE(opened.st_mode) & 0o777, size, digest.hexdigest()
    finally:
        for descriptor in reversed(opened_dirs):
            os.close(descriptor)
        os.close(root_fd)


def _outcome(result: Any) -> str:
    status = str(getattr(result, "status", "inconclusive"))
    if not bool(getattr(result, "cleanup_verified", False)) or status == "cleanup_unverified":
        return "inconclusive"
    if status == "timeout":
        return "timeout"
    if status == "command_failed":
        return "negative"
    if status not in {"completed", "ok", "success"}:
        return "inconclusive"
    return "positive" if getattr(result, "returncode", None) == 0 else "negative"


def _result_receipt(result: Any, grant: LabGrant, started: float, finished: float,
                    attempt_id: str) -> dict[str, Any]:
    artifact_path = getattr(result, "artifact_path", None)
    artifact = None
    if artifact_path:
        path = Path(artifact_path)
        if path.is_file():
            status = str(getattr(result, "status", "unknown"))
            complete = status == "ok" and bool(getattr(result, "cleanup_verified", False))
            artifact = {"path": str(path), "bytes": path.stat().st_size,
                        "sha256": _sha256_file(path),
                        "state": "complete" if complete else "partial",
                        "qualified": complete}
    return {
        "attempt_id": attempt_id, "experiment_id": grant.experiment_id,
        "generation": grant.generation, "project": grant.project,
        "repository": grant.repository, "source_root": grant.source_root,
        "snapshot": {"head_commit": grant.snapshot_head, "base_commit": grant.snapshot_base,
                     "status_fingerprint": grant.status_fingerprint,
                     "manifest_sha256": grant.snapshot_manifest_sha256},
        "argv": list(grant.argv), "resource_grant": asdict(lab_runner.ExecutionGrant()),
        "started_at": started, "finished_at": finished,
        "elapsed_ms": getattr(result, "elapsed_ms", max(0.0, (finished-started)*1000)),
        "status": getattr(result, "status", "inconclusive"),
        "outcome": _outcome(result), "returncode": getattr(result, "returncode", None),
        "cleanup_verified": bool(getattr(result, "cleanup_verified", False)),
        "stdout": _output_text(getattr(result, "stdout", "")),
        "stderr": _output_text(getattr(result, "stderr", "")),
        "containment_backend": getattr(result, "containment_backend", "unknown"),
        "artifact": artifact,
        "coverage": "operator-selected command on captured source snapshot",
        "uncertainty": ["result is limited to the selected command and captured source"],
    }


def _attempt_public(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("argv", "grant_json", "receipt_json", "process_identity"):
        value = item.pop(key, None)
        item[key.removesuffix("_json")] = json.loads(value) if value else None
    return item


def _output_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=str)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise LabError("private evidence path is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    finally:
        os.close(fd)
    return digest.hexdigest()


def _verify_snapshot(snapshot: Path, expected_manifest_sha256: str) -> dict[str, Any]:
    """Verify every captured byte, mode and path before use or candidate review."""
    if snapshot.is_symlink():
        raise LabError("captured source root is a symlink")
    root_fd: int | None = None
    try:
        root = snapshot.resolve(strict=True)
        root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                          | getattr(os, "O_NOFOLLOW", 0))
        root_info = os.fstat(root_fd)
        if not stat.S_ISDIR(root_info.st_mode) or stat.S_IMODE(root_info.st_mode) & 0o077:
            raise LabError("captured source root is not private")
        manifest_fd = os.open(".lab-snapshot.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                              dir_fd=root_fd)
        try:
            manifest_info = os.fstat(manifest_fd)
            if (not stat.S_ISREG(manifest_info.st_mode) or stat.S_IMODE(manifest_info.st_mode) != 0o600
                    or manifest_info.st_size > MAX_MANIFEST_BYTES):
                raise LabError("captured source manifest mode or size changed")
            manifest_bytes = bytearray()
            manifest_digest = hashlib.sha256()
            while True:
                block = os.read(manifest_fd, 1024 * 1024)
                if not block:
                    break
                manifest_bytes.extend(block)
                manifest_digest.update(block)
                if len(manifest_bytes) > MAX_MANIFEST_BYTES:
                    raise LabError("captured source manifest exceeds its bound")
            after_manifest = os.fstat(manifest_fd)
            if (after_manifest.st_dev, after_manifest.st_ino, after_manifest.st_size) != (
                    manifest_info.st_dev, manifest_info.st_ino, manifest_info.st_size):
                raise LabError("captured source manifest changed during verification")
            if manifest_digest.hexdigest() != expected_manifest_sha256:
                raise LabError("captured source manifest changed")
        finally:
            os.close(manifest_fd)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except LabError:
        if root_fd is not None:
            os.close(root_fd)
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        if root_fd is not None:
            os.close(root_fd)
        raise LabError("captured source manifest is unavailable or malformed") from exc

    try:
        if (not isinstance(manifest, dict)
                or manifest.get("format") != "project-control-lab-snapshot/1"
                or not isinstance(manifest.get("files"), list)
                or not manifest["files"]
                or len(manifest["files"]) > MAX_SOURCE_FILES):
            raise LabError("captured source manifest schema is invalid")
        expected: dict[str, tuple[int, int, str]] = {}
        expected_dirs: set[str] = set()
        total_bytes = 0
        for entry in manifest["files"]:
            if not isinstance(entry, dict):
                raise LabError("captured source manifest entry is invalid")
            relative, mode, size, digest = (entry.get("path"), entry.get("mode"),
                                            entry.get("size"), entry.get("sha256"))
            if (not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative
                    or isinstance(mode, bool) or not isinstance(mode, int) or mode < 0 or mode > 0o777
                    or isinstance(size, bool) or not isinstance(size, int) or size < 0
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)):
                raise LabError("captured source manifest entry fields are invalid")
            parts = PurePosixPath(relative).parts
            if (PurePosixPath(relative).is_absolute() or not parts
                    or any(part in {"", ".", ".."} for part in parts)
                    or len(parts) > MAX_PATH_DEPTH
                    or relative == ".lab-snapshot.json" or relative in expected):
                raise LabError("captured source manifest path is unsafe or duplicated")
            try:
                path_bytes = len(relative.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise LabError("captured source manifest path is not valid UTF-8") from exc
            if path_bytes > MAX_PATH_BYTES:
                raise LabError("captured source manifest path exceeds its byte limit")
            expected[relative] = (mode, size, digest)
            total_bytes += size
            for index in range(1, len(parts)):
                expected_dirs.add("/".join(parts[:index]))
                if len(expected_dirs) > MAX_SOURCE_DIRECTORIES:
                    raise LabError("captured source manifest exceeds the directory count limit")
        if total_bytes != manifest.get("total_bytes") or total_bytes > 50 * 1024 * 1024:
            raise LabError("captured source manifest byte total is invalid")

        observed: dict[str, tuple[int, int, str]] = {}
        directories: set[str] = set()
        _inventory_snapshot(root_fd, "", observed, directories)
        expected[".lab-snapshot.json"] = (0o600, len(manifest_bytes), expected_manifest_sha256)
        if directories != expected_dirs or set(observed) != set(expected):
            raise LabError("captured source inventory contains missing or unexpected paths")
        for relative, wanted in expected.items():
            if observed.get(relative) != wanted:
                raise LabError(f"captured source file changed: {relative}")
        return manifest
    finally:
        os.close(root_fd)


def _inventory_snapshot(directory_fd: int, prefix: str,
                        files: dict[str, tuple[int, int, str]],
                        directories: set[str]) -> None:
    """Walk by directory descriptor so symlinks cannot escape the snapshot."""
    with os.scandir(directory_fd) as iterator:
        for entry in iterator:
            name = entry.name
            if name in {"", ".", ".."} or "/" in name or "\\" in name or "\x00" in name:
                raise LabError("captured source contains a non-canonical path")
            relative = f"{prefix}/{name}" if prefix else name
            if (len(PurePosixPath(relative).parts) > MAX_PATH_DEPTH
                    or len(relative.encode("utf-8")) > MAX_PATH_BYTES):
                raise LabError("captured source inventory path exceeds its limit")
            before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(before.st_mode):
                if stat.S_IMODE(before.st_mode) != 0o700:
                    raise LabError(f"captured source directory mode changed: {relative}")
                child_fd = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                                   | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
                try:
                    opened = os.fstat(child_fd)
                    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                        raise LabError(f"captured source directory changed: {relative}")
                    directories.add(relative)
                    if len(directories) > MAX_SOURCE_DIRECTORIES:
                        raise LabError("captured source inventory exceeds the directory count limit")
                    _inventory_snapshot(child_fd, relative, files, directories)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(before.st_mode):
                raise LabError(f"captured source contains a symlink or special file: {relative}")
            if before.st_nlink != 1:
                raise LabError(f"captured source contains a hard-linked file: {relative}")
            fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            digest = hashlib.sha256()
            size = 0
            try:
                opened = os.fstat(fd)
                if ((opened.st_dev, opened.st_ino, opened.st_size, opened.st_nlink,
                     stat.S_IMODE(opened.st_mode)) !=
                        (before.st_dev, before.st_ino, before.st_size, before.st_nlink,
                         stat.S_IMODE(before.st_mode))):
                    raise LabError(f"captured source file changed during verification: {relative}")
                if opened.st_nlink != 1:
                    raise LabError(f"captured source contains a hard-linked file: {relative}")
                if opened.st_size > 50 * 1024 * 1024:
                    raise LabError("captured source file exceeds the capture byte limit")
                while True:
                    block = os.read(fd, 1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > 50 * 1024 * 1024:
                        raise LabError("captured source file grew during verification")
                    digest.update(block)
                after = os.fstat(fd)
                if (after.st_dev, after.st_ino, after.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
                    raise LabError(f"captured source file changed during verification: {relative}")
            finally:
                os.close(fd)
            files[relative] = (stat.S_IMODE(opened.st_mode), size, digest.hexdigest())
            if len(files) > MAX_SOURCE_FILES:
                raise LabError("captured source inventory exceeds the file count limit")


def _write_private(path: Path, contents: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
