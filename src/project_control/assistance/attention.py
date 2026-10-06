"""Bounded, demand-only-by-default source attention beside JobService state.

This module records source-change nominations and delegates preparation through
an injected broker.  It owns no scheduler, thread, Todo queue, model or device.
All persistent state lives on the caller's private JobService SQLite handle.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import json
import math
import os
import stat
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, Mapping, Sequence


MAX_QUEUE = 8
MAX_CANDIDATES_PER_FOCUS = 4
MAX_FOCUS_RECORDS = 32
MAX_DISMISSALS = 2048
MAX_RESERVED_TURNS = 24
MAX_ACTIVE_SECONDS = 900.0
DEBOUNCE_SECONDS = 2.0
COOLDOWN_SECONDS = 60.0
ROOT_TURN_RESERVATION = 6
_DENIED_PARTS = frozenset({".git", ".venv", "venv", "__pycache__", "node_modules",
                           "logs", "log", "output", "outputs", "generated", "build",
                           "dist", "notebooks", "experiments", "experiment", ".pytest_cache",
                           ".mypy_cache", ".ruff_cache", ".tox", ".cache", "target",
                           "coverage", "htmlcov", "model_notes", "model-notes"})
_HASH = re.compile(r"^[0-9a-f]{64}$")


class AttentionError(ValueError):
    """Invalid focus, source, dispatch or notebook result."""


@dataclass(frozen=True)
class FocusConfig:
    focus_id: str
    project: str
    source_paths: tuple[str, ...]
    starts_at: float
    expires_at: float
    permit_automatic_inference: bool = False
    goal_card_id: str | None = None

    def validated(self) -> "FocusConfig":
        if any(not isinstance(v, str) or not v or len(v) > 256
               for v in (self.focus_id, self.project)):
            raise AttentionError("focus_identity_invalid")
        if (isinstance(self.starts_at, bool) or isinstance(self.expires_at, bool)
                or not isinstance(self.starts_at, (int, float))
                or not isinstance(self.expires_at, (int, float))
                or not math.isfinite(float(self.starts_at))
                or not math.isfinite(float(self.expires_at))
                or self.expires_at <= self.starts_at
                or self.expires_at - self.starts_at > 86400):
            raise AttentionError("focus_window_invalid")
        if type(self.permit_automatic_inference) is not bool:
            raise AttentionError("automatic_permission_must_be_boolean")
        if self.goal_card_id is not None and (not isinstance(self.goal_card_id, str)
                                                or not self.goal_card_id or len(self.goal_card_id) > 128):
            raise AttentionError("goal_card_invalid")
        if not isinstance(self.source_paths, (list, tuple)) or len(self.source_paths) > 64:
            raise AttentionError("source_paths_invalid")
        paths = tuple(self._relative_path(path) for path in self.source_paths)
        if self.permit_automatic_inference and not paths:
            raise AttentionError("automatic_focus_requires_source_paths")
        if len(set(paths)) != len(paths):
            raise AttentionError("source_paths_duplicate")
        return FocusConfig(self.focus_id, self.project, paths, float(self.starts_at),
                           float(self.expires_at), self.permit_automatic_inference,
                           self.goal_card_id)

    @staticmethod
    def _relative_path(value: str) -> str:
        if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
            raise AttentionError("source_path_invalid")
        path = Path(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise AttentionError("source_path_must_be_relative")
        if any(part.casefold() in _DENIED_PARTS for part in path.parts):
            raise AttentionError("generated_or_private_path_denied")
        name = path.name.casefold()
        if (name.endswith((".pyc", ".pyo", ".generated", ".model-note", ".model-notes"))
                or ".generated." in name):
            raise AttentionError("generated_or_private_path_denied")
        return path.as_posix()


@dataclass(frozen=True)
class ChangeCandidate:
    candidate_id: str
    focus_id: str
    project: str
    paths: tuple[str, ...]
    input_fingerprint: str
    changed_at: float
    stable_after: float
    status: str
    job_id: str | None = None


def _wire(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


class AttentionController:
    """Source-change detection and bounded preparation admission.

    ``trusted_roots`` maps project IDs to allowed checkout roots. ``access_scope``
    returns the already validated host scope for a project. The supplied
    fingerprinter receives a resolved regular file path and returns its SHA256.
    """

    def __init__(self, db: sqlite3.Connection, *, power_policy: Any,
                 trusted_roots: Mapping[str, str | os.PathLike[str]],
                 repository_for: Callable[[str], str],
                 access_scope: Callable[[str], Mapping[str, Any]],
                 broker: Any, notebook: Any,
                 fingerprinter: Callable[[Path], str] | None = None,
                 clock: Callable[[], float] = time.time,
                 resolve_determinants: Callable[[str], Mapping[str, str]] | None = None):
        if getattr(power_policy, "db", None) is not db:
            raise AttentionError("power_policy_database_mismatch")
        if (not callable(access_scope) or not callable(repository_for)
                or not isinstance(trusted_roots, Mapping)):
            raise AttentionError("trusted_source_registry_required")
        self.db, self.power, self.roots = db, power_policy, {
            str(project): Path(root).resolve() for project, root in trusted_roots.items()
        }
        self.repository_for, self.access_scope = repository_for, access_scope
        self.broker, self.notebook = broker, notebook
        # A caller-supplied fingerprinter is an explicit trusted host seam. The
        # production default reads by descriptor with O_NOFOLLOW at every path
        # component instead of validating a pathname and reopening it later.
        self.fingerprinter = fingerprinter
        self.clock, self.resolve_determinants = clock, resolve_determinants or (lambda _project: {})
        self.db.row_factory = sqlite3.Row
        self.initialize(db)

    @staticmethod
    def initialize(db: sqlite3.Connection) -> None:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS pa1_attention_focus(
                focus_id TEXT PRIMARY KEY, project TEXT NOT NULL, source_paths TEXT NOT NULL,
                starts_at REAL NOT NULL, expires_at REAL NOT NULL,
                permit_automatic INTEGER NOT NULL DEFAULT 0,
                goal_card_id TEXT, state TEXT NOT NULL DEFAULT 'active',
                reserved_turns INTEGER NOT NULL DEFAULT 0, active_seconds REAL NOT NULL DEFAULT 0,
                created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS pa1_attention_sources(
                focus_id TEXT NOT NULL, path TEXT NOT NULL, digest TEXT,
                observed REAL NOT NULL, PRIMARY KEY(focus_id,path));
            CREATE TABLE IF NOT EXISTS pa1_attention_determinants(
                focus_id TEXT PRIMARY KEY, value TEXT NOT NULL, observed REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS pa1_attention_candidates(
                candidate_id TEXT PRIMARY KEY, focus_id TEXT NOT NULL, project TEXT NOT NULL,
                paths TEXT NOT NULL, fingerprint TEXT NOT NULL, changed_at REAL NOT NULL,
                stable_after REAL NOT NULL, status TEXT NOT NULL, job_id TEXT,
                cooldown_until REAL, error_code TEXT, active_charge REAL NOT NULL DEFAULT 0,
                admission_deadline REAL, admission_payload TEXT,
                usage_reconciled INTEGER NOT NULL DEFAULT 0,
                updated REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS pa1_attention_queue
                ON pa1_attention_candidates(status,stable_after,changed_at);
            CREATE TABLE IF NOT EXISTS pa1_attention_dismissals(
                project TEXT NOT NULL, focus_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                reason TEXT NOT NULL, created REAL NOT NULL,
                PRIMARY KEY(project,focus_id,fingerprint));
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(pa1_attention_candidates)")}
        if "cooldown_until" not in columns:
            db.execute("ALTER TABLE pa1_attention_candidates ADD COLUMN cooldown_until REAL")
        if "error_code" not in columns:
            db.execute("ALTER TABLE pa1_attention_candidates ADD COLUMN error_code TEXT")
        if "active_charge" not in columns:
            db.execute("ALTER TABLE pa1_attention_candidates ADD COLUMN active_charge REAL NOT NULL DEFAULT 0")
        if "usage_reconciled" not in columns:
            db.execute("ALTER TABLE pa1_attention_candidates ADD COLUMN usage_reconciled INTEGER NOT NULL DEFAULT 0")
        if "admission_deadline" not in columns:
            db.execute("ALTER TABLE pa1_attention_candidates ADD COLUMN admission_deadline REAL")
        if "admission_payload" not in columns:
            db.execute("ALTER TABLE pa1_attention_candidates ADD COLUMN admission_payload TEXT")

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _sha256_beneath(root: Path, relative: str) -> str:
        """Hash one regular file through a no-follow openat walk beneath root."""
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        cloexec = getattr(os, "O_CLOEXEC", 0)
        current = os.open(os.sep, directory_flags | cloexec)
        opened: list[int] = [current]
        try:
            # Walk the already canonical trusted root from the filesystem root;
            # this also refuses a replaced/symlinked ancestor component.
            for part in root.parts[1:]:
                current = os.open(part, directory_flags | cloexec, dir_fd=current)
                opened.append(current)
            parts = Path(relative).parts
            if not parts:
                raise AttentionError("source_path_invalid")
            for part in parts[:-1]:
                current = os.open(part, directory_flags | cloexec, dir_fd=current)
                opened.append(current)
            file_fd = os.open(parts[-1], file_flags | cloexec, dir_fd=current)
            opened.append(file_fd)
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise AttentionError("source_must_be_regular_file")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(file_fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(file_fd)
            identity_before = (before.st_dev, before.st_ino, before.st_size,
                               before.st_mtime_ns, before.st_ctime_ns)
            identity_after = (after.st_dev, after.st_ino, after.st_size,
                              after.st_mtime_ns, after.st_ctime_ns)
            if identity_before != identity_after:
                raise AttentionError("source_changed_during_read")
            return digest.hexdigest()
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise AttentionError("source_symlink_denied") from exc
            raise
        finally:
            for fd in reversed(opened):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _source_file(self, project: str, relative: str) -> Path:
        root = self.roots.get(project)
        if root is None:
            raise AttentionError("project_not_in_trusted_registry")
        rel = FocusConfig._relative_path(relative)
        unresolved = root / rel
        # Refuse symlinks anywhere in the selected path. Resolving only the
        # final target would otherwise allow a trusted-looking alias to read
        # bytes outside the explicit grant.
        cursor = root
        for part in Path(rel).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise AttentionError("source_symlink_denied")
        candidate = unresolved.resolve(strict=True)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise AttentionError("source_path_escapes_trusted_root") from exc
        if not candidate.is_file():
            raise AttentionError("source_must_be_regular_file")
        return candidate

    def _digest(self, project: str, relative: str) -> str:
        root = self.roots.get(project)
        if root is None:
            raise AttentionError("project_not_in_trusted_registry")
        rel = FocusConfig._relative_path(relative)
        value = (self.fingerprinter(self._source_file(project, rel))
                 if self.fingerprinter is not None else self._sha256_beneath(root, rel))
        if not isinstance(value, str) or not _HASH.fullmatch(value):
            raise AttentionError("source_fingerprint_invalid")
        return value

    def _determinants(self, project: str) -> dict[str, str]:
        raw = self.resolve_determinants(project)
        if not isinstance(raw, Mapping) or len(raw) > 128:
            raise AttentionError("determinant_coverage_unavailable")
        values = dict(raw)
        if any(not isinstance(key, str) or not key or len(key) > 256
               or not isinstance(value, str) or not value or len(value) > 256
               for key, value in values.items()):
            raise AttentionError("determinant_manifest_invalid")
        return values

    def _repository(self, project: str) -> str:
        value = self.repository_for(project)
        if not isinstance(value, str) or not value or len(value) > 256:
            raise AttentionError("repository_identity_unavailable")
        return value

    def configure_focus(self, control: Any, config: FocusConfig) -> None:
        """Persist explicit focus metadata after checking operator authority.

        The operator adapter sets PowerPolicy's window and automatic switch
        separately, so this write cannot grant inference by itself.
        """
        config = config.validated()
        if config.project not in self.roots:
            raise AttentionError("project_not_in_trusted_registry")
        try:
            permission = control.permission("set_focus_window")
            if not self.power.check_permission(control, permission, "set_focus_window"):
                raise AttentionError("trusted_operator_control_required")
        except Exception as exc:
            if isinstance(exc, AttentionError):
                raise
            raise AttentionError("trusted_operator_control_required") from exc
        # Verify every selected source before granting the focus window.
        baseline = {path: self._digest(config.project, path) for path in config.source_paths}
        determinants = self._determinants(config.project)
        now = float(self.clock())
        with self.db:
            exists = self.db.execute("SELECT 1 FROM pa1_attention_focus WHERE focus_id=?",
                                     (config.focus_id,)).fetchone()
            count = self.db.execute("SELECT count(*) FROM pa1_attention_focus").fetchone()[0]
            if exists is None and count >= MAX_FOCUS_RECORDS:
                raise AttentionError("focus_registry_capacity")
            if exists is not None:
                self.db.execute("UPDATE pa1_attention_candidates SET status='superseded',updated=? "
                                "WHERE focus_id=? AND status IN ('pending','deferred','failed')",
                                (now, config.focus_id))
            self.db.execute("""INSERT INTO pa1_attention_focus
                (focus_id,project,source_paths,starts_at,expires_at,permit_automatic,
                 goal_card_id,state,reserved_turns,active_seconds,created,updated)
                VALUES (?,?,?,?,?,?,?,'active',0,0,?,?)
                ON CONFLICT(focus_id) DO UPDATE SET project=excluded.project,
                 source_paths=excluded.source_paths,starts_at=excluded.starts_at,
                 expires_at=excluded.expires_at,permit_automatic=excluded.permit_automatic,
                 goal_card_id=excluded.goal_card_id,state='active',updated=excluded.updated""",
                (config.focus_id, config.project, _wire(config.source_paths), config.starts_at,
                 config.expires_at, int(config.permit_automatic_inference), config.goal_card_id,
                 now, now))
            for path, digest in baseline.items():
                self.db.execute("""INSERT INTO pa1_attention_sources(focus_id,path,digest,observed)
                    VALUES(?,?,?,?) ON CONFLICT(focus_id,path) DO UPDATE SET
                    digest=excluded.digest,observed=excluded.observed""",
                    (config.focus_id, path, digest, now))
            self.db.execute("INSERT OR REPLACE INTO pa1_attention_determinants VALUES(?,?,?)",
                            (config.focus_id, _wire(determinants), now))
            self.db.execute("""DELETE FROM pa1_attention_candidates WHERE candidate_id IN (
                SELECT candidate_id FROM pa1_attention_candidates WHERE focus_id=?
                AND status IN ('completed','failed','expired','suppressed','stale','dismissed','superseded')
                ORDER BY updated DESC,candidate_id LIMIT -1 OFFSET 64)""", (config.focus_id,))

    def read_focus(self, focus_id: str) -> dict[str, Any] | None:
        """Return bounded focus metadata for the trusted operator adapter."""
        row = self.db.execute("SELECT * FROM pa1_attention_focus WHERE focus_id=?", (focus_id,)).fetchone()
        if row is None:
            return None
        return {"focus_id": row["focus_id"], "project": row["project"],
                "source_paths": json.loads(row["source_paths"]),
                "starts_at": row["starts_at"], "expires_at": row["expires_at"],
                "permit_automatic_inference": bool(row["permit_automatic"]),
                "goal_card_id": row["goal_card_id"], "state": row["state"],
                "reserved_turns": row["reserved_turns"],
                "active_seconds": row["active_seconds"]}

    def active_focuses(self, *, permit_automatic: bool | None = None) -> tuple[dict[str, Any], ...]:
        """List unexpired configured focuses for the host-owned watchdog loop."""
        if permit_automatic is not None and type(permit_automatic) is not bool:
            raise AttentionError("automatic_filter_must_be_boolean")
        now = float(self.clock())
        query = "SELECT focus_id FROM pa1_attention_focus WHERE state='active' AND starts_at<=? AND expires_at>?"
        params: tuple[Any, ...] = (now, now)
        if permit_automatic is not None:
            query += " AND permit_automatic=?"
            params += (int(permit_automatic),)
        rows = self.db.execute(query + " ORDER BY created,focus_id", params).fetchall()
        return tuple(value for row in rows if (value := self.read_focus(row[0])) is not None)

    def scan(self, focus_id: str) -> dict[str, Any]:
        """Read opted-in bytes, seed/reconcile baselines, and nominate stable changes."""
        now = float(self.clock())
        row = self.db.execute("SELECT * FROM pa1_attention_focus WHERE focus_id=?", (focus_id,)).fetchone()
        if row is None or row["state"] != "active":
            return {"status": "inactive", "nominated": 0}
        if not row["starts_at"] <= now < row["expires_at"]:
            with self.db:
                self.db.execute("UPDATE pa1_attention_focus SET state='expired',updated=? WHERE focus_id=?",
                                (now, focus_id))
            return {"status": "expired", "nominated": 0}
        paths = tuple(json.loads(row["source_paths"]))
        current: dict[str, str] = {}
        try:
            current = {path: self._digest(row["project"], path) for path in paths}
            determinants = self._determinants(row["project"])
        except (OSError, AttentionError):
            return {"status": "deferred_source_unavailable", "nominated": 0}
        changed: list[str] = []
        for path in paths:
            old = self.db.execute("SELECT digest FROM pa1_attention_sources WHERE focus_id=? AND path=?",
                                  (focus_id, path)).fetchone()
            if old is None or old[0] is None:
                # Restart/reconciliation can fill a missing baseline without
                # treating the first observed bytes as a change.
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO pa1_attention_sources VALUES(?,?,?,?)",
                                    (focus_id, path, current[path], now))
            elif old[0] != current[path]:
                changed.append(path)
        previous_determinants = self.db.execute(
            "SELECT value FROM pa1_attention_determinants WHERE focus_id=?", (focus_id,)).fetchone()
        determinants_changed = (previous_determinants is not None
                                and _wire(determinants) != previous_determinants[0])
        if not changed and not determinants_changed:
            return {"status": "unchanged", "nominated": 0}
        repository = self._repository(row["project"])
        fingerprint = hashlib.sha256(_wire({"repository": repository,
                                              "files": {p: current[p] for p in paths},
                                              "determinants": determinants}).encode()).hexdigest()
        nomination_paths = changed or list(paths)
        with self.db:
            dismissed = self.db.execute("SELECT 1 FROM pa1_attention_dismissals WHERE project=? AND focus_id=? AND fingerprint=?",
                                        (row["project"], focus_id, fingerprint)).fetchone()
            if dismissed:
                return {"status": "dismissed_unchanged_input", "nominated": 0}
            pending = self.db.execute("SELECT candidate_id FROM pa1_attention_candidates WHERE focus_id=? AND status IN ('pending','deferred') ORDER BY changed_at DESC LIMIT 1",
                                      (focus_id,)).fetchone()
            live_statuses = "('pending','deferred','admitting','dispatched')"
            total = self.db.execute(f"SELECT count(*) FROM pa1_attention_candidates WHERE status IN {live_statuses}").fetchone()[0]
            focus_count = self.db.execute(f"SELECT count(*) FROM pa1_attention_candidates WHERE focus_id=? AND status IN {live_statuses}",
                                          (focus_id,)).fetchone()[0]
            if pending:
                self.db.execute("UPDATE pa1_attention_candidates SET paths=?,fingerprint=?,changed_at=?,stable_after=?,updated=? WHERE candidate_id=?",
                                (_wire(nomination_paths), fingerprint, now, now + DEBOUNCE_SECONDS, now, pending[0]))
                for path in changed:
                    self.db.execute("UPDATE pa1_attention_sources SET digest=?,observed=? WHERE focus_id=? AND path=?",
                                    (current[path], now, focus_id, path))
                self.db.execute("INSERT OR REPLACE INTO pa1_attention_determinants VALUES(?,?,?)",
                                (focus_id, _wire(determinants), now))
                return {"status": "coalesced", "nominated": 1}
            if total >= MAX_QUEUE or focus_count >= MAX_CANDIDATES_PER_FOCUS:
                return {"status": "deferred_queue_full", "nominated": 0}
            candidate_id = hashlib.sha256(f"{focus_id}:{fingerprint}".encode()).hexdigest()[:32]
            self.db.execute("""INSERT OR IGNORE INTO pa1_attention_candidates
                (candidate_id,focus_id,project,paths,fingerprint,changed_at,stable_after,status,updated)
                VALUES(?,?,?,?,?,?,?,'pending',?)""",
                (candidate_id, focus_id, row["project"], _wire(nomination_paths), fingerprint,
                 now, now + DEBOUNCE_SECONDS, now))
            # Advance the byte baseline only after the nomination is durable.
            for path in changed:
                self.db.execute("UPDATE pa1_attention_sources SET digest=?,observed=? WHERE focus_id=? AND path=?",
                                (current[path], now, focus_id, path))
            self.db.execute("INSERT OR REPLACE INTO pa1_attention_determinants VALUES(?,?,?)",
                            (focus_id, _wire(determinants), now))
            self._prune_candidates(focus_id)
        return {"status": "nominated", "nominated": 1}

    def _prune_candidates(self, focus_id: str) -> None:
        self.db.execute("""DELETE FROM pa1_attention_candidates WHERE candidate_id IN (
            SELECT candidate_id FROM pa1_attention_candidates WHERE focus_id=?
            AND status IN ('completed','failed','expired','suppressed','stale','dismissed','superseded')
            ORDER BY updated DESC,candidate_id LIMIT -1 OFFSET 64)""", (focus_id,))

    def candidates(self) -> tuple[ChangeCandidate, ...]:
        rows = self.db.execute("SELECT * FROM pa1_attention_candidates WHERE status IN ('pending','deferred','failed','admitting','dispatched') ORDER BY changed_at LIMIT ?",
                               (MAX_QUEUE,)).fetchall()
        return tuple(ChangeCandidate(r["candidate_id"], r["focus_id"], r["project"],
                                     tuple(json.loads(r["paths"])), r["fingerprint"],
                                     r["changed_at"], r["stable_after"], r["status"], r["job_id"])
                     for r in rows)

    def dispatch_next(self, control: Any) -> dict[str, Any]:
        """Atomically reserve one candidate, then call the broker after commit."""
        if self.db.in_transaction:
            return {"status": "deferred", "reason": "uncommitted_controller_database_transaction"}
        snapshot_time = float(self.clock())
        snapshot = self.db.execute("""SELECT c.*,f.source_paths,f.starts_at,f.expires_at,
              f.permit_automatic,f.state focus_state FROM pa1_attention_candidates c
              JOIN pa1_attention_focus f USING(focus_id)
              WHERE c.status IN ('pending','deferred','failed') AND c.stable_after<=?
              AND (c.cooldown_until IS NULL OR c.cooldown_until<=?)
              ORDER BY c.changed_at LIMIT 1""", (snapshot_time, snapshot_time)).fetchone()
        if snapshot is None:
            return {"status": "empty"}
        paths = tuple(json.loads(snapshot["paths"]))
        source_paths = tuple(json.loads(snapshot["source_paths"]))
        try:
            all_digests = {path: self._digest(snapshot["project"], path) for path in source_paths}
            determinants = self._determinants(snapshot["project"])
            repository = self._repository(snapshot["project"])
        except (OSError, AttentionError):
            return self._defer(snapshot["candidate_id"], "source_unavailable")
        fingerprint = hashlib.sha256(_wire({"repository": repository,
                    "files": all_digests, "determinants": determinants}).encode()).hexdigest()
        if fingerprint != snapshot["fingerprint"]:
            return self._defer(snapshot["candidate_id"], "source_changed_during_debounce")
        scope = self.access_scope(snapshot["project"])
        if not isinstance(scope, Mapping) or scope.get("project") != snapshot["project"]:
            return self._defer(snapshot["candidate_id"], "trusted_scope_unavailable")
        prompt = ("Review the opted-in source changes for this focus. Return visible, "
                  "evidence-linked preparation with uncertainties; do not publish or "
                  "create tasks. Changed paths: " + ", ".join(paths))[:2048]
        dependencies = {**{f"file:{repository}/{path}": digest
                           for path, digest in all_digests.items()}, **determinants}
        payload = {"question": prompt, "access_scope": dict(scope),
                   "focus_id": snapshot["focus_id"], "input_fingerprint": fingerprint,
                   "expected_dependencies": dependencies}

        # BEGIN IMMEDIATE serializes competing controller processes before they
        # inspect the candidate, shared slot, or focus budget. All checks and the
        # admission intent plus budget reservation commit as one transaction.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            now = float(self.clock())
            row = self.db.execute("""SELECT c.*,f.source_paths,f.starts_at,f.expires_at,
                  f.permit_automatic,f.state focus_state,f.reserved_turns,f.active_seconds
                  FROM pa1_attention_candidates c JOIN pa1_attention_focus f USING(focus_id)
                  WHERE c.candidate_id=?""", (snapshot["candidate_id"],)).fetchone()
            reason = None
            if row is None or row["status"] not in {"pending", "deferred", "failed"}:
                reason = "candidate_claimed_elsewhere"
            elif (row["stable_after"] > now
                  or (row["cooldown_until"] is not None and row["cooldown_until"] > now)):
                reason = "candidate_not_stable"
            elif row["source_paths"] != snapshot["source_paths"] or row["fingerprint"] != fingerprint:
                reason = "focus_or_source_changed"
            elif row["focus_state"] != "active" or not row["starts_at"] <= now < row["expires_at"]:
                reason = "focus_expired"
            elif not row["permit_automatic"]:
                reason = "automatic_inference_disabled"
            elif row["reserved_turns"] + ROOT_TURN_RESERVATION > MAX_RESERVED_TURNS:
                reason = "focus_turn_budget_exhausted"
            elif row["active_seconds"] >= MAX_ACTIVE_SECONDS:
                reason = "focus_active_time_exhausted"
            elif self.db.execute("SELECT 1 FROM pa1_attention_candidates WHERE status IN ('admitting','dispatched') LIMIT 1").fetchone():
                reason = "automatic_slot_occupied"
            if reason is None:
                decision = self.power.allow_dispatch("automatic_root", True,
                    control.permission("dispatch_automatic"), focus_id=row["focus_id"],
                    project=row["project"])
                if not decision.allowed:
                    reason = decision.reason
            active_reservation = 0.0
            deadline = 0.0
            if reason is None:
                active_reservation = min(300.0, MAX_ACTIVE_SECONDS - float(row["active_seconds"]))
                deadline = min(float(row["expires_at"]), now + active_reservation)
                if deadline <= now:
                    reason = "focus_active_time_exhausted"
            if reason is None:
                changed = self.db.execute("""UPDATE pa1_attention_candidates
                    SET status='admitting',active_charge=?,admission_deadline=?,admission_payload=?,
                        updated=? WHERE candidate_id=? AND status IN ('pending','deferred','failed')""",
                    (active_reservation, deadline, _wire(payload), now, row["candidate_id"])).rowcount
                if changed != 1:
                    reason = "candidate_claimed_elsewhere"
                else:
                    budget = self.db.execute("""UPDATE pa1_attention_focus
                        SET reserved_turns=reserved_turns+?,active_seconds=active_seconds+?,updated=?
                        WHERE focus_id=? AND reserved_turns+?<=? AND active_seconds+?<=?""",
                        (ROOT_TURN_RESERVATION, active_reservation, now, row["focus_id"],
                         ROOT_TURN_RESERVATION, MAX_RESERVED_TURNS, active_reservation,
                         MAX_ACTIVE_SECONDS)).rowcount
                    if budget != 1:
                        self.db.rollback()
                        return {"status": "deferred", "reason": "focus_budget_exhausted"}
            if reason is not None:
                self.db.rollback()
                return {"status": "deferred", "reason": reason}
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

        # No SQLite transaction is held across this callback. A lost reply leaves
        # an immutable, explicitly reconcilable admission intent and its single
        # durable reservation.
        return self._call_admission(row["candidate_id"], payload, deadline, active_reservation)

    def _call_admission(self, candidate_id: str, payload: Mapping[str, Any],
                        deadline: float, active_reservation: float) -> dict[str, Any]:
        try:
            admitted = self.broker.enqueue_preparation(
                question=payload["question"], access_scope=payload["access_scope"],
                focus_id=payload["focus_id"], input_fingerprint=payload["input_fingerprint"],
                window_deadline=deadline, expected_dependencies=payload["expected_dependencies"])
        except Exception as exc:
            return {"status": "admission_unknown", "reason": type(exc).__name__}
        job_id = admitted.get("job_id") if isinstance(admitted, Mapping) else admitted
        status = admitted.get("status") if isinstance(admitted, Mapping) else "queued"
        accepted = not isinstance(admitted, Mapping) or admitted.get("accepted") is True
        if status == "deferred" or not accepted:
            now = float(self.clock())
            self.db.execute("BEGIN IMMEDIATE")
            try:
                changed = self.db.execute("UPDATE pa1_attention_candidates SET status='deferred',active_charge=0,cooldown_until=?,error_code=?,updated=? WHERE candidate_id=? AND status='admitting'",
                    (now + COOLDOWN_SECONDS,
                     str(admitted.get("reason", "broker_deferred"))[:96]
                     if isinstance(admitted, Mapping) else "broker_deferred", now, candidate_id)).rowcount
                if changed == 1:
                    self.db.execute("UPDATE pa1_attention_focus SET reserved_turns=max(0,reserved_turns-?),active_seconds=max(0,active_seconds-?),updated=? WHERE focus_id=(SELECT focus_id FROM pa1_attention_candidates WHERE candidate_id=?)",
                                    (ROOT_TURN_RESERVATION, active_reservation, now, candidate_id))
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
            return {"status": "deferred", "reason": "broker_deferred"}
        if status not in {"queued", "accepted", "dispatched", "existing"}:
            return {"status": "admission_unknown", "reason": "broker_status_unrecognized"}
        if not isinstance(job_id, str) or not job_id:
            return {"status": "admission_unknown", "reason": "broker_job_identity_missing"}
        now = float(self.clock())
        with self.db:
            changed = self.db.execute("UPDATE pa1_attention_candidates SET status='dispatched',job_id=?,updated=? WHERE candidate_id=? AND status='admitting'",
                                      (job_id, now, candidate_id)).rowcount
        if changed != 1:
            return {"status": "admission_reconciliation_required", "candidate_id": candidate_id}
        return {"status": "dispatched", "candidate_id": candidate_id, "job_id": job_id}

    def reconcile_admission(self, candidate_id: str) -> dict[str, Any]:
        """Explicitly retry only a persisted uncertain admission with exact inputs."""
        if self.db.in_transaction:
            return {"status": "deferred", "reason": "uncommitted_controller_database_transaction"}
        row = self.db.execute("SELECT * FROM pa1_attention_candidates WHERE candidate_id=? AND status='admitting'",
                              (candidate_id,)).fetchone()
        if row is None or not row["admission_payload"] or row["admission_deadline"] is None:
            return {"status": "not_reconcilable"}
        payload = json.loads(row["admission_payload"])
        if float(self.clock()) >= float(row["admission_deadline"]):
            return {"status": "expired_admission_intent"}
        return self._call_admission(candidate_id, payload, float(row["admission_deadline"]),
                                    float(row["active_charge"]))

    def _defer(self, candidate_id: str, reason: str) -> dict[str, Any]:
        current = self.db.execute("SELECT status FROM pa1_attention_candidates WHERE candidate_id=?",
                                  (candidate_id,)).fetchone()
        # Admission may have crossed the broker boundary even if its reply was
        # lost. Keep its durable reservation and exact idempotency identity until
        # the broker can reconcile it; do not accidentally enqueue a second root.
        if current is not None and current[0] in {"admitting", "dispatched"}:
            return {"status": "deferred", "reason": reason, "broker_reconciliation_pending": True}
        with self.db:
            self.db.execute("UPDATE pa1_attention_candidates SET status='deferred',error_code=?,updated=? WHERE candidate_id=?",
                             (reason[:96], float(self.clock()), candidate_id))
        return {"status": "deferred", "reason": reason}

    def _failed(self, candidate_id: str, reason: str) -> dict[str, Any]:
        now = float(self.clock())
        with self.db:
            self.db.execute("UPDATE pa1_attention_candidates SET status='failed',error_code=?,cooldown_until=?,updated=? WHERE candidate_id=?",
                             (reason[:96], now + COOLDOWN_SECONDS, now, candidate_id))
        return {"status": "failed", "reason": reason}

    def record_result(self, *, candidate_id: str, control: Any,
                      note_id: str | None = None) -> dict[str, Any]:
        """Read a broker-verified completion and store it as advisory context."""
        row = self.db.execute("SELECT * FROM pa1_attention_candidates WHERE candidate_id=? AND status='dispatched'",
                              (candidate_id,)).fetchone()
        if row is None:
            raise AttentionError("candidate_not_dispatched")
        now = float(self.clock())
        scope = self.access_scope(row["project"])
        if not isinstance(scope, Mapping) or scope.get("project") != row["project"]:
            return self._defer(candidate_id, "trusted_scope_unavailable")
        # Resolve the private job first. Pending or unknown jobs keep their slot
        # occupied; only a terminal result can be discarded and released.
        lookup = self.broker.preparation_lookup(row["job_id"], dict(scope))
        if not isinstance(lookup, Mapping) or lookup.get("status") not in {"completed", "partial"}:
            status = lookup.get("status") if isinstance(lookup, Mapping) else "unavailable"
            if status == "pending":
                return {"status": "pending"}
            return self._defer(candidate_id, "preparation_" + str(status)[:64])
        if (lookup.get("focus_id") not in (None, row["focus_id"])
                or lookup.get("input_fingerprint") not in (None, row["fingerprint"])):
            return self._failed(candidate_id, "broker_result_identity_mismatch")
        trusted_active_seconds = lookup.get("active_seconds")
        if (isinstance(trusted_active_seconds, (int, float))
                and not isinstance(trusted_active_seconds, bool)
                and math.isfinite(float(trusted_active_seconds))
                and 0 <= trusted_active_seconds <= row["active_charge"]
                and not row["usage_reconciled"]):
            with self.db:
                refund = float(row["active_charge"]) - float(trusted_active_seconds)
                self.db.execute("UPDATE pa1_attention_focus SET active_seconds=max(0,active_seconds-?),updated=? WHERE focus_id=?",
                                (refund, now, row["focus_id"]))
                self.db.execute("UPDATE pa1_attention_candidates SET usage_reconciled=1,updated=? WHERE candidate_id=?",
                                (now, candidate_id))
        focus = self.db.execute("SELECT * FROM pa1_attention_focus WHERE focus_id=?", (row["focus_id"],)).fetchone()
        if (focus is None or focus["state"] != "active" or not focus["permit_automatic"]
                or not focus["starts_at"] <= now < focus["expires_at"]):
            return self._settle(candidate_id, "suppressed", "focus_expired_before_effect", now)
        if focus["active_seconds"] >= MAX_ACTIVE_SECONDS:
            return self._settle(candidate_id, "suppressed", "focus_active_time_exhausted", now)
        decision = self.power.allow_dispatch("automatic_root", True,
                    control.permission("dispatch_automatic"),
                    focus_id=row["focus_id"], project=row["project"])
        if not decision.allowed:
            return self._settle(candidate_id, "suppressed", decision.reason, now)
        try:
            current_files = {path: self._digest(row["project"], path) for path in
                             json.loads(focus["source_paths"])}
            determinants = self._determinants(row["project"])
            repository = self._repository(row["project"])
        except Exception:
            return self._settle(candidate_id, "stale", "source_unavailable_before_effect", now)
        current_fingerprint = hashlib.sha256(_wire({"repository": repository,
                                    "files": current_files,
                                    "determinants": determinants}).encode()).hexdigest()
        if current_fingerprint != row["fingerprint"]:
            return self._settle(candidate_id, "stale", "source_changed_before_effect", now)
        evidence_packets = lookup.get("evidence_packets")
        if (not isinstance(evidence_packets, list) or not evidence_packets
                or any(not isinstance(item, str) or not item.startswith("pkt_") for item in evidence_packets)):
            return self._failed(candidate_id, "original_support_packets_required")
        answer = lookup.get("answer")
        findings = lookup.get("findings", [])
        claim = answer if isinstance(answer, str) and answer.strip() else _wire(findings)
        if len(claim.encode("utf-8")) > 4096:
            claim = claim.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
        if not claim.strip():
            return self._failed(candidate_id, "verified_visible_answer_missing")
        sources = lookup.get("sources", [])
        dependencies = lookup.get("dependencies", {})
        if not isinstance(sources, list) or not isinstance(dependencies, Mapping):
            return self._failed(candidate_id, "verified_provenance_missing")
        try:
            repository = self._repository(row["project"])
        except AttentionError:
            return self._defer(candidate_id, "repository_identity_unavailable")
        expected_dependencies = {
            **{f"file:{repository}/{path}": digest for path, digest in current_files.items()},
            **determinants,
        }
        if dict(dependencies) != expected_dependencies:
            return self._failed(candidate_id, "broker_dependency_identity_mismatch")
        if not sources:
            return self._failed(candidate_id, "verified_source_locators_missing")
        try:
            for source in sources:
                if not isinstance(source, Mapping) or source.get("project") != row["project"]:
                    raise ValueError("source_project_mismatch")
                path = FocusConfig._relative_path(source.get("path"))
                if (source.get("repository") != repository
                        or path not in current_files
                        or source.get("content_sha256") != current_files[path]):
                    raise ValueError("source_bytes_mismatch")
        except (TypeError, ValueError, AttentionError):
            return self._failed(candidate_id, "verified_source_identity_mismatch")
        uncertainty = {"unresolved_questions": lookup.get("unresolved_questions", []),
                       "status": lookup.get("status")}
        record = {
            "note_id": note_id or "attention-" + candidate_id,
            "project": row["project"], "kind": "preparation", "claim": claim,
            "reason_matters": "Advisory preparation for the explicitly selected focus.",
            "provenance": "model",
            "sources": [dict(x) for x in sources], "evidence_packets": evidence_packets,
            "dependencies": dict(dependencies), "coverage": {"focus_id": row["focus_id"],
                 "candidate_id": candidate_id, "input_fingerprint": row["fingerprint"]},
            "uncertainty": uncertainty, "next_action": None,
            "authoritative": False, "is_independent_evidence": False,
        }
        stored = self.notebook.put_note(record, access_scope=dict(scope))
        with self.db:
            self.db.execute("UPDATE pa1_attention_candidates SET status='completed',updated=? WHERE candidate_id=?",
                            (now, candidate_id))
        return {"status": "stored_advisory", "note": stored}

    def _settle(self, candidate_id: str, status: str, reason: str, now: float) -> dict[str, Any]:
        with self.db:
            self.db.execute("UPDATE pa1_attention_candidates SET status=?,error_code=?,updated=? WHERE candidate_id=?",
                            (status, reason[:96], now, candidate_id))
        return {"status": status, "reason": reason}

    def dismiss(self, *, project: str, focus_id: str, fingerprint: str,
                reason: str = "operator-dismissed") -> None:
        if not _HASH.fullmatch(fingerprint) or not isinstance(reason, str) or not reason or len(reason) > 128:
            raise AttentionError("dismissal_invalid")
        with self.db:
            exists = self.db.execute("SELECT 1 FROM pa1_attention_dismissals WHERE project=? AND focus_id=? AND fingerprint=?",
                                     (project, focus_id, fingerprint)).fetchone()
            count = self.db.execute("SELECT count(*) FROM pa1_attention_dismissals").fetchone()[0]
            if exists is None and count >= MAX_DISMISSALS:
                raise AttentionError("dismissal_registry_capacity")
            self.db.execute("INSERT OR REPLACE INTO pa1_attention_dismissals VALUES(?,?,?,?,?)",
                            (project, focus_id, fingerprint, reason, float(self.clock())))
            self.db.execute("UPDATE pa1_attention_candidates SET status='dismissed',updated=? WHERE project=? AND focus_id=? AND fingerprint=?",
                            (float(self.clock()), project, focus_id, fingerprint))

    def handoff(self, *, project: str, focus_id: str,
                user_goal_notes: Sequence[Mapping[str, Any]],
                inferred_notes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Build a proposal; trusted operator must separately approve every action."""
        user_goals = [dict(note) for note in user_goal_notes if note.get("provenance") == "user"
                      and note.get("project") == project and note.get("kind") == "goal"]
        focus = self.read_focus(focus_id)
        goal_card_id = focus.get("goal_card_id") if focus else None
        if goal_card_id:
            user_goals.sort(key=lambda note: (note.get("note_id") != goal_card_id,
                                              note.get("note_id", "")))
        inferred = []
        for note in inferred_notes:
            if (note.get("provenance") != "model" or note.get("project") != project
                    or note.get("kind") == "goal"):
                continue
            coverage = note.get("coverage", {})
            fingerprint = coverage.get("input_fingerprint") if isinstance(coverage, Mapping) else None
            if isinstance(fingerprint, str) and self.db.execute(
                    "SELECT 1 FROM pa1_attention_dismissals WHERE project=? AND focus_id=? AND fingerprint=?",
                    (project, focus_id, fingerprint)).fetchone():
                continue
            inferred.append(dict(note))
        if user_goals:
            return {"status": "user_goal_precedes_inference", "goal": user_goals[0],
                    "suggestions": inferred[:4], "requires_operator_decision": True}
        return {"status": "inferred_suggestions_only", "focus_id": focus_id,
                "suggestions": inferred[:4], "requires_operator_decision": True}
