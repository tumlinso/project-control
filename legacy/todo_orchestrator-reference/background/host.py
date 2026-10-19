"""Lazy host-global authority for physical resources shared by project sidecars."""

from __future__ import annotations

import itertools
import json
import os
import sqlite3
import math
import secrets
import hashlib
import tempfile
import time
import uuid
from pathlib import Path

from .models import canonical_json, digest
from .resources import cpu_capacity, memory_capacity_bytes
from .store import _alive, _process_start


def _normalize_priority_class(value: str) -> str:
    # Lazy import avoids a cycle through runtime.__init__ -> facade -> host.
    from ..runtime.resources import normalize_priority_class
    return normalize_priority_class(value)


def _priority_value(value: str) -> int:
    from ..runtime.resources import priority_value
    return priority_value(value)


SCHEMA = """
CREATE TABLE IF NOT EXISTS host_resources(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, tags_json TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS host_owners(
 id TEXT PRIMARY KEY, owner_kind TEXT NOT NULL, project_root TEXT,
 job_id TEXT, attempt_id TEXT, service_id TEXT, pid INTEGER, process_start TEXT,
 state TEXT NOT NULL, preempt_requested INTEGER NOT NULL DEFAULT 0,
 priority_class TEXT NOT NULL DEFAULT 'background_cuda',
 preemptible INTEGER NOT NULL DEFAULT 1,
 residency_protected INTEGER NOT NULL DEFAULT 0,
 residency_metadata_json TEXT NOT NULL DEFAULT '{}',
 residency_release_verified INTEGER NOT NULL DEFAULT 0,
 cpu_threads INTEGER NOT NULL DEFAULT 0, ram_bytes INTEGER NOT NULL DEFAULT 0,
 acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS host_reservations(
 resource_id TEXT NOT NULL, owner_id TEXT NOT NULL, state TEXT NOT NULL,
 acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL,
 PRIMARY KEY(resource_id,owner_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_host_resource_exclusive
 ON host_reservations(resource_id) WHERE state='active';
CREATE TABLE IF NOT EXISTS host_foreground_intents(
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, resources_json TEXT NOT NULL,
 cpu_threads INTEGER NOT NULL DEFAULT 0, ram_bytes INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL, created_at REAL NOT NULL, heartbeat_at REAL NOT NULL
);
"""


def host_runtime_root() -> Path:
    override = os.environ.get("TODO_BACKGROUND_HOST_RUNTIME_DIR")
    if override:
        return Path(override).resolve()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        candidate = Path(runtime)
        try:
            if candidate.is_dir() and candidate.stat().st_uid == os.getuid():
                return candidate / "codex-todo-orchestrator"
        except OSError:
            pass
    return Path(tempfile.gettempdir()) / f"codex-todo-orchestrator-{os.getuid()}"


def background_owner_id(project_root: str | Path, job_id: str, attempt_id: str) -> str:
    project = digest(str(Path(project_root).resolve()))[:16]
    return f"background:{project}:{job_id}:{attempt_id}"


class HostCoordinator:
    def __init__(self, *, create: bool = True, busy_timeout_ms: int = 2500):
        self.root = host_runtime_root()
        self.database = self.root / "physical-resources.sqlite3"
        self.busy_timeout_ms = busy_timeout_ms
        if create:
            self.initialize()

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True, timeout=self.busy_timeout_ms / 1000)
        else:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                self.root.chmod(0o700)
            except OSError:
                pass
            connection = sqlite3.connect(self.database, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        if not readonly:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def initialize(self) -> None:
        connection = self.connect()
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(host_owners)")}
            if "service_id" not in columns:
                connection.execute("ALTER TABLE host_owners ADD COLUMN service_id TEXT")
            if "priority_class" not in columns:
                connection.execute("ALTER TABLE host_owners ADD COLUMN priority_class TEXT NOT NULL DEFAULT 'background_cuda'")
            if "preemptible" not in columns:
                connection.execute("ALTER TABLE host_owners ADD COLUMN preemptible INTEGER NOT NULL DEFAULT 1")
            for name, declaration in (
                ("residency_protected", "INTEGER NOT NULL DEFAULT 0"),
                ("residency_metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("residency_release_verified", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE host_owners ADD COLUMN {name} {declaration}")
            self._install_residency_guards(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _install_residency_guards(connection: sqlite3.Connection) -> None:
        # Guards execute for old frozen clients sharing this database too.
        # IGNORE preserves unrelated clients and their ordinary reservations.
        guards = {
            "owner_insert": ("host_owners", "INSERT", "EXISTS(SELECT 1 FROM host_owners WHERE id=NEW.id AND residency_protected=1 AND state='active')"),
            "owner_delete": ("host_owners", "DELETE", "OLD.residency_protected=1 AND OLD.state='active'"),
            "owner_update": ("host_owners", "UPDATE", """OLD.residency_protected=1 AND OLD.state='active' AND (
                (NEW.residency_protected=0 AND NOT(NEW.residency_release_verified=1 AND OLD.residency_release_verified=0)) OR
                (NEW.residency_protected=1 AND (NEW.state IS NOT OLD.state OR NEW.id IS NOT OLD.id OR
                 NEW.owner_kind IS NOT OLD.owner_kind OR NEW.project_root IS NOT OLD.project_root OR
                 NEW.job_id IS NOT OLD.job_id OR NEW.attempt_id IS NOT OLD.attempt_id OR
                 NEW.service_id IS NOT OLD.service_id OR NEW.acquired_at IS NOT OLD.acquired_at OR
                 NEW.cpu_threads IS NOT OLD.cpu_threads OR NEW.ram_bytes IS NOT OLD.ram_bytes OR
                 json_extract(NEW.residency_metadata_json,'$.generation') IS NOT json_extract(OLD.residency_metadata_json,'$.generation') OR
                 json_extract(NEW.residency_metadata_json,'$.residency_capability_sha256') IS NOT json_extract(OLD.residency_metadata_json,'$.residency_capability_sha256') OR
                 json_extract(NEW.residency_metadata_json,'$.origin') IS NOT json_extract(OLD.residency_metadata_json,'$.origin') OR
                 json_extract(NEW.residency_metadata_json,'$.baseline') IS NOT json_extract(OLD.residency_metadata_json,'$.baseline') OR
                 json_extract(NEW.residency_metadata_json,'$.resource_ids') IS NOT json_extract(OLD.residency_metadata_json,'$.resource_ids') OR
                 ((NEW.pid IS NOT OLD.pid OR NEW.process_start IS NOT OLD.process_start) AND NOT(
                   json_extract(OLD.residency_metadata_json,'$.phase')='reserved' AND
                   json_extract(NEW.residency_metadata_json,'$.phase')='spawned' AND
                   json_extract(NEW.residency_metadata_json,'$.model_pid')=NEW.pid AND
                   json_extract(NEW.residency_metadata_json,'$.process_start')=NEW.process_start)))))"""),
        }
        protected = "EXISTS(SELECT 1 FROM host_owners WHERE id=OLD.owner_id AND residency_protected=1 AND state='active')"
        for table, identity in (("host_reservations", "resource_id"), ("host_foreground_intents", "id")):
            guards[f"{table}_delete"] = (table, "DELETE", f"OLD.state='active' AND {protected}")
            extra = " OR NEW.resources_json IS NOT OLD.resources_json OR NEW.cpu_threads IS NOT OLD.cpu_threads OR NEW.ram_bytes IS NOT OLD.ram_bytes" if table == "host_foreground_intents" else ""
            acquired = "created_at" if table == "host_foreground_intents" else "acquired_at"
            condition = (f"OLD.state='active' AND {protected} AND (NEW.state IS NOT OLD.state OR "
                         f"NEW.owner_id IS NOT OLD.owner_id OR NEW.{identity} IS NOT OLD.{identity} OR "
                         f"NEW.{acquired} IS NOT OLD.{acquired}{extra})")
            guards[f"{table}_update"] = (table, "UPDATE", condition)
            collision = f"EXISTS(SELECT 1 FROM {table} old JOIN host_owners owner ON owner.id=old.owner_id WHERE old.{identity}=NEW.{identity} AND old.state='active' AND owner.residency_protected=1 AND owner.state='active')"
            guards[f"{table}_insert"] = (table, "INSERT", collision)
        for name, (table, operation, condition) in guards.items():
            connection.execute(f"DROP TRIGGER IF EXISTS residency_guard_{name}")
            connection.execute(f"CREATE TRIGGER residency_guard_{name} BEFORE {operation} ON {table} WHEN {condition} BEGIN SELECT RAISE(IGNORE); END")

    def _tx(self) -> sqlite3.Connection:
        connection = self.connect()
        connection.execute("BEGIN IMMEDIATE")
        return connection

    def upsert_resources(self, resources: list[dict[str, object]]) -> None:
        connection = self._tx()
        try:
            now = time.time()
            for item in resources:
                connection.execute(
                    "INSERT INTO host_resources(id,kind,tags_json,enabled,updated_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,tags_json=excluded.tags_json,enabled=excluded.enabled,updated_at=excluded.updated_at",
                    (str(item["id"]), str(item.get("kind", "accelerator")), canonical_json(item.get("tags", {})), int(item.get("enabled", True)), now),
                )
            connection.commit()
        finally:
            connection.close()

    def replace_resources(self, kind: str, resources: list[dict[str, object]]) -> None:
        """Atomically refresh one discovered resource kind and retire stale IDs."""
        connection = self._tx()
        try:
            now = time.time()
            connection.execute("UPDATE host_resources SET enabled=0,updated_at=? WHERE kind=?", (now, kind))
            for item in resources:
                item_kind = str(item.get("kind", kind))
                if item_kind != kind:
                    raise ValueError("replacement resources must share one kind")
                connection.execute(
                    "INSERT INTO host_resources(id,kind,tags_json,enabled,updated_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,tags_json=excluded.tags_json,enabled=excluded.enabled,updated_at=excluded.updated_at",
                    (str(item["id"]), kind, canonical_json(item.get("tags", {})), int(item.get("enabled", True)), now),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _sweep_locked(self, connection: sqlite3.Connection, stale_seconds: float = 30.0) -> None:
        cutoff = time.time() - stale_seconds
        for owner in connection.execute("SELECT * FROM host_owners WHERE state IN ('active','intent') AND heartbeat_at<?", (cutoff,)).fetchall():
            if owner["residency_protected"]:
                continue
            if _alive(owner["pid"], owner["process_start"]):
                continue
            self._release_locked(connection, str(owner["id"]), "stale")
        connection.execute(
            "UPDATE host_foreground_intents SET state='released',heartbeat_at=? WHERE state='active' "
            "AND owner_id IN (SELECT id FROM host_owners WHERE state NOT IN ('active','intent'))",
            (time.time(),),
        )

    def sweep_stale(self, *, stale_seconds: float = 30.0) -> int:
        """Release dead, expired owners before read-side resource selection.

        Reservation remains rechecked transactionally by ``reserve_*``.  This
        public sweep exists because bundle discovery otherwise filters ghosts
        before it can reach that transactional recheck.
        """
        connection = self._tx()
        try:
            before = connection.execute(
                "SELECT COUNT(*) FROM host_owners WHERE state IN ('active','intent')"
            ).fetchone()[0]
            self._sweep_locked(connection, stale_seconds=stale_seconds)
            after = connection.execute(
                "SELECT COUNT(*) FROM host_owners WHERE state IN ('active','intent')"
            ).fetchone()[0]
            connection.commit()
            return int(before) - int(after)
        finally:
            connection.close()

    def reconcile_current_service_owners(self, *, project_root: str | Path,
                                         pid: int, live_owner_ids: set[str]) -> list[str]:
        """Release only orphaned CORE4 service reservations of this process.

        A supervisor can lose in-memory slot state after an exceptional local
        failure while its parent remains alive.  Scope this narrowly to the
        current process identity, project, and CORE4 service namespace so no
        other process or active known slot can be disturbed.
        """
        process_start = _process_start(pid)
        if process_start is None:
            return []
        connection = self._tx()
        try:
            rows = connection.execute(
                "SELECT id FROM host_owners WHERE owner_kind='service' "
                "AND project_root=? AND pid=? AND process_start=? "
                "AND service_id LIKE 'core4-local-%' AND state IN ('active','intent')",
                (str(Path(project_root).resolve()), pid, process_start),
            ).fetchall()
            released: list[str] = []
            for row in rows:
                owner_id = str(row["id"])
                if owner_id not in live_owner_ids:
                    row = connection.execute("SELECT residency_protected FROM host_owners WHERE id=?", (owner_id,)).fetchone()
                    if row and row[0]:
                        continue
                    self._release_locked(connection, owner_id, "orphaned")
                    released.append(owner_id)
            connection.commit()
            return released
        finally:
            connection.close()

    @staticmethod
    def _request_ids(request: dict[str, object]) -> list[str]:
        return [str(item) for item in request.get("ids", [])]

    @staticmethod
    def _extra_ids(request: dict[str, object], selected: list[sqlite3.Row]) -> list[str]:
        extra = [str(item) for item in request.get("exclusive_resources", [])]
        if request.get("isolate_pcie_root"):
            extra.extend(f"interference:pcie:{json.loads(row['tags_json']).get('pcie_root')}" for row in selected if json.loads(row["tags_json"]).get("pcie_root"))
        if request.get("isolate_nvlink_domain"):
            extra.extend(f"interference:nvlink:{json.loads(row['tags_json']).get('nvlink_domain')}" for row in selected if json.loads(row["tags_json"]).get("nvlink_domain"))
        return sorted(set(extra))

    @staticmethod
    def _active_resource_ids(connection: sqlite3.Connection) -> set[str]:
        reserved = {str(row[0]) for row in connection.execute("SELECT resource_id FROM host_reservations WHERE state='active'")}
        for row in connection.execute("SELECT resources_json FROM host_foreground_intents WHERE state='active'"):
            reserved.update(str(item) for item in json.loads(row[0]))
        return reserved

    @staticmethod
    def _cpu_threads(request: dict[str, object]) -> int:
        declared = int(request.get("cpu_threads", 0) or 0)
        return declared or (max(1, int(cpu_capacity() * 0.75)) if request.get("cpu_heavy") else 0)

    @staticmethod
    def _pressure_available(connection: sqlite3.Connection, request: dict[str, object]) -> bool:
        active = connection.execute(
            "SELECT COALESCE(SUM(cpu_threads),0),COALESCE(SUM(ram_bytes),0) FROM host_owners WHERE state IN ('active','intent')"
        ).fetchone()
        cpu_limit = max(1, int(cpu_capacity() * 0.75))
        ram_limit = max(0, int(memory_capacity_bytes() * 0.80))
        return int(active[0]) + HostCoordinator._cpu_threads(request) <= cpu_limit and int(active[1]) + int(request.get("ram_bytes", 0) or 0) <= ram_limit

    def _select_locked(self, connection: sqlite3.Connection, request: dict[str, object]) -> list[str] | None:
        if not self._pressure_available(connection, request):
            return None
        kind = str(request.get("kind", "accelerator"))
        rows = connection.execute("SELECT * FROM host_resources WHERE enabled=1 AND kind=? ORDER BY id", (kind,)).fetchall()
        tags = {str(key): str(value) for key, value in dict(request.get("tags", {})).items()}
        rows = [row for row in rows if all(str(json.loads(row["tags_json"]).get(key)) == value for key, value in tags.items())]
        requested_ids = self._request_ids(request)
        count = int(request.get("count", 0) or 0)
        if requested_ids:
            selected = [row for row in rows if row["id"] in requested_ids]
            combinations = [tuple(selected)] if len(selected) == len(requested_ids) else []
        elif count:
            combinations = itertools.combinations(rows, count)
        else:
            combinations = [tuple()]
        active = self._active_resource_ids(connection)
        for combination in combinations:
            resource_ids = [str(row["id"]) for row in combination]
            expanded = sorted(set(resource_ids + self._extra_ids(request, list(combination))))
            if not active.intersection(expanded):
                return expanded
        return None

    def _candidate_resources_locked(self, connection: sqlite3.Connection,
                                    request: dict[str, object]) -> list[list[str]]:
        """Return runtime-discovered candidate bundles, including occupied ones."""
        kind = str(request.get("kind", "accelerator"))
        rows = connection.execute("SELECT * FROM host_resources WHERE enabled=1 AND kind=? ORDER BY id", (kind,)).fetchall()
        tags = {str(key): str(value) for key, value in dict(request.get("tags", {})).items()}
        rows = [row for row in rows if all(str(json.loads(row["tags_json"]).get(key)) == value for key, value in tags.items())]
        requested_ids = self._request_ids(request)
        count = int(request.get("count", 0) or 0)
        if requested_ids:
            selected = [row for row in rows if row["id"] in requested_ids]
            combinations = [tuple(selected)] if len(selected) == len(requested_ids) else []
        elif count:
            combinations = itertools.combinations(rows, count)
        else:
            combinations = [tuple()]
        return [
            sorted(set([str(row["id"]) for row in combination] + self._extra_ids(request, list(combination))))
            for combination in combinations
        ]

    @staticmethod
    def _conflicting_owners_locked(connection: sqlite3.Connection, resources: list[str]) -> list[sqlite3.Row]:
        if not resources:
            return []
        marks = ",".join("?" for _ in resources)
        return connection.execute(
            f"SELECT DISTINCT o.* FROM host_owners o JOIN host_reservations r ON r.owner_id=o.id "
            f"WHERE o.state='active' AND r.state='active' AND r.resource_id IN ({marks})",
            resources,
        ).fetchall()

    def _request_preemption_locked(self, connection: sqlite3.Connection,
                                   request: dict[str, object], priority_class: str) -> list[str]:
        """Signal one viable lower-priority bundle, preferring the fewest victims."""
        requester = _priority_value(priority_class)
        candidates: list[tuple[int, int, list[str], list[sqlite3.Row]]] = []
        for resources in self._candidate_resources_locked(connection, request):
            owners = self._conflicting_owners_locked(connection, resources)
            if owners and all(bool(row["preemptible"]) and _priority_value(str(row["priority_class"])) < requester for row in owners):
                candidates.append((len(owners), sum(_priority_value(str(row["priority_class"])) for row in owners), resources, owners))
        if not candidates:
            return []
        _, _, resources, owners = min(candidates, key=lambda item: (item[0], item[1], item[2]))
        now = time.time()
        for owner in owners:
            connection.execute("UPDATE host_owners SET preempt_requested=1,heartbeat_at=? WHERE id=?", (now, owner["id"]))
        return resources

    def _reserve_locked(self, connection: sqlite3.Connection, *, owner_id: str,
                        owner_kind: str, project_root: str | Path,
                        request: dict[str, object], pid: int,
                        priority_class: str, job_id: str | None = None,
                        attempt_id: str | None = None, service_id: str | None = None) -> tuple[str, list[str]] | None:
        priority_class = _normalize_priority_class(priority_class)
        resources = self._select_locked(connection, request)
        if resources is None:
            self._request_preemption_locked(connection, request, priority_class)
            return None
        now = time.time()
        connection.execute(
            "INSERT INTO host_owners(id,owner_kind,project_root,job_id,attempt_id,service_id,pid,process_start,state,"
            "priority_class,preemptible,cpu_threads,ram_bytes,acquired_at,heartbeat_at) "
            "VALUES(?,?,?,?,?,?,?,?,'active',?,1,?,?,?,?)",
            (owner_id, owner_kind, str(Path(project_root).resolve()), job_id, attempt_id, service_id,
             pid, _process_start(pid), priority_class, self._cpu_threads(request),
             int(request.get("ram_bytes", 0) or 0), now, now),
        )
        for resource_id in resources:
            connection.execute(
                "INSERT INTO host_reservations(resource_id,owner_id,state,acquired_at,heartbeat_at) VALUES(?,?,'active',?,?)",
                (resource_id, owner_id, now, now),
            )
        return owner_id, [item for item in resources if item.startswith("accelerator:")]

    def reserve_background(self, *, project_root: str | Path, job_id: str, attempt_id: str,
                           request: dict[str, object], pid: int) -> tuple[str, list[str]] | None:
        owner_id = background_owner_id(project_root, job_id, attempt_id)
        connection = self._tx()
        try:
            self._sweep_locked(connection)
            reserved = self._reserve_locked(
                connection, owner_id=owner_id, owner_kind="background", project_root=project_root,
                request=request, pid=pid, priority_class="background_cuda", job_id=job_id,
                attempt_id=attempt_id,
            )
            connection.commit()
            return reserved
        except sqlite3.IntegrityError:
            connection.rollback()
            return None
        finally:
            connection.close()

    def reserve_service(self, *, project_root: str | Path, service_id: str,
                        request: dict[str, object], pid: int,
                        priority_class: str = "idle_model_residency") -> tuple[str, list[str]] | None:
        connection = self._tx()
        try:
            self._sweep_locked(connection)
            owner_id = f"service:{digest(str(Path(project_root).resolve()))[:16]}:{service_id}:{uuid.uuid4()}"
            reserved = self._reserve_locked(
                connection, owner_id=owner_id, owner_kind="service", project_root=project_root,
                request=request, pid=pid, priority_class=priority_class, service_id=service_id,
            )
            connection.commit()
            return reserved
        except sqlite3.IntegrityError:
            connection.rollback()
            return None
        finally:
            connection.close()

    def set_priority(self, owner_id: str, priority_class: str) -> bool:
        priority_class = _normalize_priority_class(priority_class)
        connection = self._tx()
        try:
            row = connection.execute("SELECT state,preempt_requested FROM host_owners WHERE id=?", (owner_id,)).fetchone()
            if row is None or row["state"] != "active" or bool(row["preempt_requested"]):
                connection.commit()
                return False
            connection.execute("UPDATE host_owners SET priority_class=?,heartbeat_at=? WHERE id=?", (priority_class, time.time(), owner_id))
            connection.commit()
            return True
        finally:
            connection.close()

    def request_preemption(self, owner_id: str) -> bool:
        connection = self._tx()
        try:
            changed = connection.execute(
                "UPDATE host_owners SET preempt_requested=1,heartbeat_at=? "
                "WHERE id=? AND state='active' AND preemptible=1",
                (time.time(), owner_id),
            ).rowcount
            connection.commit()
            return bool(changed)
        finally:
            connection.close()

    def owner(self, owner_id: str) -> dict[str, object] | None:
        try:
            connection = self.connect(readonly=True)
        except sqlite3.Error:
            return None
        try:
            row = connection.execute("SELECT * FROM host_owners WHERE id=?", (owner_id,)).fetchone()
            if row is None:
                return None
            resources = [str(item[0]) for item in connection.execute(
                "SELECT resource_id FROM host_reservations WHERE owner_id=? AND state='active' ORDER BY resource_id", (owner_id,)
            )]
            return {key: row[key] for key in row.keys()} | {"resources": resources}
        finally:
            connection.close()

    def begin_foreground(self, *, project_root: str | Path, request: dict[str, object], pid: int,
                         priority_class: str = "foreground_gpu") -> tuple[str, list[str]]:
        priority_class = _normalize_priority_class(priority_class)
        connection = self._tx()
        try:
            self._sweep_locked(connection)
            resources = self._select_locked(connection, request)
            if resources is None:
                resources = self._request_preemption_locked(connection, request, priority_class)
                if not resources:
                    candidates = self._candidate_resources_locked(connection, request)
                    resources = candidates[0] if candidates else []
            owner_id = f"foreground:{uuid.uuid4()}"
            intent_id = str(uuid.uuid4())
            now = time.time()
            cpu_threads = int(request.get("cpu_threads", 0) or 0)
            ram_bytes = int(request.get("ram_bytes", 0) or 0)
            connection.execute(
                "INSERT INTO host_owners(id,owner_kind,project_root,pid,process_start,state,priority_class,preemptible,cpu_threads,ram_bytes,acquired_at,heartbeat_at) VALUES(?,?,?,?,?,'intent',?,0,?,?,?,?)",
                (owner_id, "foreground", str(Path(project_root).resolve()), pid, _process_start(pid), priority_class, cpu_threads, ram_bytes, now, now),
            )
            connection.execute(
                "INSERT INTO host_foreground_intents(id,owner_id,resources_json,cpu_threads,ram_bytes,state,created_at,heartbeat_at) VALUES(?,?,?,?,?,'active',?,?)",
                (intent_id, owner_id, canonical_json(resources), cpu_threads, ram_bytes, now, now),
            )
            active_cpu, active_ram = connection.execute(
                "SELECT COALESCE(SUM(cpu_threads),0),COALESCE(SUM(ram_bytes),0) "
                "FROM host_owners WHERE state='active'"
            ).fetchone()
            pressure_conflict = int(active_cpu) + cpu_threads > max(1, int(cpu_capacity() * 0.75)) or int(active_ram) + ram_bytes > int(memory_capacity_bytes() * 0.80)
            for owner in connection.execute("SELECT * FROM host_owners WHERE state='active'").fetchall():
                held = {str(row[0]) for row in connection.execute("SELECT resource_id FROM host_reservations WHERE owner_id=? AND state='active'", (owner["id"],))}
                if bool(owner["preemptible"]) and _priority_value(str(owner["priority_class"])) < _priority_value(priority_class) and (pressure_conflict or held.intersection(resources)):
                    connection.execute("UPDATE host_owners SET preempt_requested=1,heartbeat_at=? WHERE id=?", (now, owner["id"]))
            connection.commit()
            return owner_id, resources
        finally:
            connection.close()

    def activate_foreground(self, owner_id: str, resources: list[str]) -> bool:
        connection = self._tx()
        try:
            self._sweep_locked(connection)
            if any(connection.execute("SELECT 1 FROM host_reservations WHERE resource_id=? AND state='active'", (item,)).fetchone() for item in resources):
                connection.commit()
                return False
            now = time.time()
            for item in resources:
                connection.execute("INSERT INTO host_reservations(resource_id,owner_id,state,acquired_at,heartbeat_at) VALUES(?,?,'active',?,?)", (item, owner_id, now, now))
            connection.execute("UPDATE host_owners SET state='active',heartbeat_at=? WHERE id=?", (now, owner_id))
            connection.commit()
            return True
        except sqlite3.IntegrityError:
            connection.rollback()
            return False
        finally:
            connection.close()

    def conflicts(self, resources: list[str]) -> list[str]:
        connection = self.connect(readonly=True)
        try:
            if not resources:
                return []
            marks = ",".join("?" for _ in resources)
            return [str(row[0]) for row in connection.execute(
                f"SELECT DISTINCT owner_id FROM host_reservations WHERE state='active' AND resource_id IN ({marks}) AND owner_id IN "
                "(SELECT id FROM host_owners WHERE state='active')", resources
            )]
        finally:
            connection.close()

    def heartbeat(self, owner_id: str, pid: int | None = None) -> None:
        connection = self._tx()
        try:
            now = time.time()
            connection.execute("UPDATE host_owners SET heartbeat_at=?,pid=COALESCE(?,pid),process_start=COALESCE(?,process_start) WHERE id=?", (now, pid, _process_start(pid) if pid else None, owner_id))
            connection.execute("UPDATE host_reservations SET heartbeat_at=? WHERE owner_id=? AND state='active'", (now, owner_id))
            connection.commit()
        finally:
            connection.close()

    def preempt_requested(self, owner_id: str) -> bool:
        try:
            connection = self.connect(readonly=True)
        except sqlite3.Error:
            return False
        try:
            row = connection.execute("SELECT preempt_requested FROM host_owners WHERE id=? AND state='active'", (owner_id,)).fetchone()
            return bool(row and row[0])
        finally:
            connection.close()

    def _release_locked(self, connection: sqlite3.Connection, owner_id: str, state: str = "released") -> None:
        now = time.time()
        connection.execute("UPDATE host_reservations SET state='released',heartbeat_at=? WHERE owner_id=? AND state='active'", (now, owner_id))
        connection.execute("UPDATE host_owners SET state=?,heartbeat_at=? WHERE id=?", (state, now, owner_id))
        connection.execute("UPDATE host_foreground_intents SET state='released',heartbeat_at=? WHERE owner_id=? AND state='active'", (now, owner_id))

    def protect_residency(self, owner_id: str, *, memory_baseline: dict[str, float]) -> dict[str, object]:
        connection = self._tx()
        try:
            owner = connection.execute("SELECT * FROM host_owners WHERE id=?", (owner_id,)).fetchone()
            resources = {str(row[0]).removeprefix("accelerator:") for row in connection.execute(
                "SELECT resource_id FROM host_reservations WHERE owner_id=? AND state='active' AND resource_id LIKE 'accelerator:%'", (owner_id,))}
            if (not owner or owner['state'] != 'active' or owner['owner_kind'] != 'service' or
                    not str(owner['service_id']).startswith('core4-local-') or owner['pid'] != os.getpid() or
                    set(memory_baseline) != resources or not resources or
                    any(isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0 for value in memory_baseline.values())):
                raise ValueError('residency_protection_identity_invalid')
            if owner['residency_protected']:
                raise ValueError('residency_protection_immutable')
            capability = secrets.token_hex(32)
            origin = {'pid': os.getpid(), 'process_start': _process_start(os.getpid()),
                      'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
            resource_ids = sorted(str(row[0]) for row in connection.execute(
                "SELECT resource_id FROM host_reservations WHERE owner_id=? AND state='active'", (owner_id,)))
            generation = uuid.uuid4().hex
            metadata = {'phase': 'reserved', 'model_pid': None, 'baseline': memory_baseline,
                        'origin': origin, 'generation': generation, 'resource_ids': resource_ids,
                        'residency_capability_sha256': hashlib.sha256(capability.encode()).hexdigest()}
            connection.execute('UPDATE host_owners SET residency_protected=1,residency_metadata_json=? WHERE id=?',
                               (canonical_json(metadata), owner_id))
            connection.commit()
            return {'residency_capability': capability, 'generation': generation,
                    'origin': origin, 'resource_ids': resource_ids}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_residency_process(self, owner_id: str, *, pid: int, residency_capability: str,
                                 generation: str) -> None:
        connection = self._tx()
        try:
            owner = connection.execute('SELECT * FROM host_owners WHERE id=?', (owner_id,)).fetchone()
            start = _process_start(pid)
            if not owner or owner['state'] != 'active' or not owner['residency_protected'] or not start:
                raise ValueError('residency_process_identity_invalid')
            metadata = json.loads(owner['residency_metadata_json'])
            self._verify_residency_capability(metadata, residency_capability, generation)
            if not self._residency_origin_caller(metadata):
                raise ValueError('residency_process_owner_mismatch')
            if metadata['phase'] == 'spawned' and (metadata['model_pid'] != pid or metadata['process_start'] != start):
                raise ValueError('residency_process_identity_immutable')
            if metadata['phase'] == 'reserved' and owner['pid'] != os.getpid():
                raise ValueError('residency_process_owner_mismatch')
            metadata.update(phase='spawned', model_pid=pid, process_start=start)
            connection.execute('UPDATE host_owners SET pid=?,process_start=?,residency_metadata_json=?,heartbeat_at=? WHERE id=?',
                               (pid, start, canonical_json(metadata), time.time(), owner_id))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def release(self, owner_id: str, *, residency_quiescence: dict[str, object] | None = None,
                residency_capability: str | None = None, generation: str | None = None) -> None:
        connection = self._tx()
        try:
            owner = connection.execute('SELECT * FROM host_owners WHERE id=?', (owner_id,)).fetchone()
            if owner and owner['residency_protected']:
                metadata = json.loads(owner['residency_metadata_json'])
                self._verify_residency_capability(metadata, residency_capability, generation)
                origin = metadata['origin']
                if metadata['phase'] == 'reserved' and not self._residency_origin_caller(metadata):
                    raise ValueError('residency_release_reserved_recovery_blocked')
                if not self._residency_origin_caller(metadata) and _alive(origin['pid'], origin['process_start']):
                    raise ValueError('residency_release_owner_alive')
                self._verify_residency_release(connection, owner, residency_quiescence)
                connection.execute('UPDATE host_owners SET residency_protected=0,residency_release_verified=1 WHERE id=?', (owner_id,))
            self._release_locked(connection, owner_id)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _verify_residency_capability(metadata: dict, capability: str | None, generation: str | None) -> None:
        if (not isinstance(capability, str) or len(capability) != 64 or
                generation != metadata.get('generation') or not secrets.compare_digest(
                    hashlib.sha256(capability.encode()).hexdigest(), metadata['residency_capability_sha256'])):
            raise ValueError('residency_cleanup_capability_invalid')

    @staticmethod
    def _residency_origin_caller(metadata: dict) -> bool:
        origin = metadata['origin']
        return (origin['pid'] == os.getpid() and origin['process_start'] == _process_start(os.getpid()) and
                origin['boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip())

    @staticmethod
    def _verify_residency_release(connection: sqlite3.Connection, owner: sqlite3.Row,
                                  proof: dict[str, object] | None) -> None:
        if not isinstance(proof, dict) or proof.get('format') != 'CORE4-RESIDENCY-QUIESCENCE/1' or proof.get('owner_id') != owner['id']:
            raise ValueError('residency_release_requires_quiescence')
        observed = proof.get('observed_unix')
        if isinstance(observed, bool) or not isinstance(observed, (int, float)) or not math.isfinite(observed) or not 0 <= time.time() - observed <= 5:
            raise ValueError('residency_release_observation_stale')
        metadata = json.loads(owner['residency_metadata_json'])
        pid = metadata.get('model_pid')
        if pid is not None and (owner['pid'] != pid or owner['process_start'] != metadata.get('process_start')):
            raise ValueError('residency_release_process_identity_changed')
        if proof.get('owned_pid') != pid or (pid is not None and _alive(pid, metadata.get('process_start'))):
            raise ValueError('residency_release_model_still_alive')
        observation = proof.get('observation')
        if not isinstance(observation, dict) or observation.get('available') is not True:
            raise ValueError('residency_release_observation_unavailable')
        resources = {str(row[0]).removeprefix('accelerator:') for row in connection.execute(
            "SELECT resource_id FROM host_reservations WHERE owner_id=? AND state='active' AND resource_id LIKE 'accelerator:%'", (owner['id'],))}
        exact_resources = sorted(str(row[0]) for row in connection.execute(
            "SELECT resource_id FROM host_reservations WHERE owner_id=? AND state='active'", (owner['id'],)))
        if exact_resources != metadata['resource_ids']:
            raise ValueError('residency_release_resources_changed')
        baseline = metadata['baseline']
        rows = observation.get('devices')
        processes = observation.get('processes')
        if not isinstance(rows, list) or not isinstance(processes, list) or set(baseline) != resources or len(rows) != len(resources):
            raise ValueError('residency_release_resources_changed')
        memory = {row['uuid']: float(row['memory_used_mib']) for row in rows}
        if set(memory) != resources or any(not math.isfinite(value) or value < 0 or value > float(baseline[gpu]) + 16 for gpu, value in memory.items()):
            raise ValueError('residency_release_memory_not_quiescent')
        if any(row.get('pid') == pid for row in processes) or (metadata['phase'] == 'reserved' and processes):
            raise ValueError('residency_release_process_not_quiescent')
