"""Private, durable continuation and dependency state for PA1 observer frames.

This module deliberately accepts the JobService SQLite connection.  It never
opens a database, starts a transaction, dispatches work, or invokes a worker;
callers can therefore commit frame intent atomically with the job/checkpoint
and outbox rows that make that intent real.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from typing import Any, Mapping, Sequence


MAX_ROOT_DEPTH = 2
MAX_ROOT_CREDITS = 4
MAX_PRIVATE_FRAMES = 32
DEFAULT_MODEL_TURN_LIMIT = 6
_TERMINAL = frozenset({"completed", "failed", "cancelled", "expired", "partial"})
_ORIGIN_CLASSES = frozenset({"public_demand", "automatic"})
_PROTOCOL_ERRORS = frozenset({"invalid_json_object", "final_response_not_object",
    "invalid_private_wait_proposal", "malformed_tool_envelope", "invalid_command_arguments"})


class FrameError(ValueError):
    """A proposed private continuation violates a persisted frame bound."""


@dataclass(frozen=True)
class ChildFrameSpec:
    """Typed intent for one broker-allocated private child job."""

    job_id: str
    scope: Mapping[str, Any]
    deadline: float
    question: str = ""
    turn_reservation: int = 1


@dataclass(frozen=True)
class ChildOutcome:
    job_id: str
    status: str
    result: Mapping[str, Any]
    terminal_version: int


@dataclass(frozen=True)
class WaitOutcome:
    parent_id: str
    generation: int
    ready: bool
    children: tuple[ChildOutcome, ...]
    wake_version: int | None = None


@dataclass(frozen=True)
class FrameRecord:
    job_id: str
    root_id: str
    parent_id: str | None
    generation: int
    depth: int
    scope: Mapping[str, Any]
    deadline: float
    turn_limit: int
    turns_used: int
    failed_attempts: int
    turns_reserved: int
    root_turns_used: int
    root_turns_reserved: int
    credit_limit: int
    credit_used: int
    state: str
    origin_class: str
    automatic_read_scope: Mapping[str, Any] | None


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _scope_intersection(parent: Mapping[str, Any], requested: Mapping[str, Any]) -> dict[str, Any]:
    """Intersect same-shaped scope trees; reject ambiguous or expanding forms."""
    if set(parent) != set(requested):
        raise FrameError("child scope must preserve every parent scope dimension")
    result: dict[str, Any] = {}
    for key in sorted(parent):
        left, right = parent[key], requested[key]
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            result[key] = _scope_intersection(left, right)
        elif isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
            selected = [item for item in left if item in right]
            if not selected:
                raise FrameError(f"child scope is disjoint at {key}")
            result[key] = selected
        elif left == right:
            result[key] = left
        else:
            # Scalar dimensions are exact capabilities, never wildcard grants.
            raise FrameError(f"child scope attempts to change {key}")
    return result


def normalize_automatic_read_scope(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and canonicalize the private automatic file-read capability."""
    if value is None:
        value = {"version": 1, "files": []}
    if not isinstance(value, Mapping) or set(value) != {"version", "files"} or value.get("version") != 1:
        raise FrameError("automatic read scope must use the version 1 file seal")
    files = value.get("files")
    if not isinstance(files, (list, tuple)):
        raise FrameError("automatic read scope files must be a list")
    normalized: dict[tuple[str, str], str] = {}
    for entry in files:
        if not isinstance(entry, Mapping) or set(entry) != {"repository", "path", "sha256"}:
            raise FrameError("automatic read scope entries need repository, path, and sha256")
        repository, path, digest = entry.get("repository"), entry.get("path"), entry.get("sha256")
        if (not isinstance(repository, str) or not repository or len(repository) > 256
                or "/" in repository or "\\" in repository or any(c.isspace() for c in repository)
                or not isinstance(path, str) or not path or len(path.encode("utf-8")) > 4096
                or path.startswith("/") or "\\" in path or "\x00" in path
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise FrameError("automatic read scope contains a non-canonical repository path")
        if any(part in {"", ".", ".."} for part in path.split("/")) or re.match(r"^[A-Za-z]:", path):
            raise FrameError("automatic read scope contains a non-canonical repository path")
        key = (repository, path)
        if key in normalized and normalized[key] != digest:
            raise FrameError("automatic read scope has conflicting file digests")
        normalized[key] = digest
    ordered = [{"repository": repository, "path": path, "sha256": normalized[(repository, path)]}
        for repository, path in sorted(normalized)]
    return {"version": 1, "files": ordered}


def automatic_read_scope_from_dependencies(dependencies: Mapping[str, Any]) -> dict[str, Any]:
    """Seal only selected file dependencies; other determinant keys grant no reads."""
    if not isinstance(dependencies, Mapping):
        raise FrameError("automatic dependencies must be a mapping")
    files = []
    for key, digest in dependencies.items():
        if not isinstance(key, str) or not key.startswith("file:"):
            continue
        locator = key[len("file:"):]
        if "/" not in locator or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise FrameError("automatic file dependency is malformed")
        repository, path = locator.split("/", 1)
        files.append({"repository": repository, "path": path, "sha256": digest})
    return normalize_automatic_read_scope({"version": 1, "files": files})


def _visible_result(result: Mapping[str, Any]) -> str:
    """Validate compact public-visible output; hidden reasoning is never stored."""
    if not isinstance(result, Mapping):
        raise FrameError("terminal result must be a mapping")
    forbidden = {"reasoning", "chain_of_thought", "hidden_reasoning", "analysis"}
    if forbidden.intersection(str(k).lower() for k in result):
        raise FrameError("terminal result may contain visible output only")
    try:
        wire = _dump(dict(result))
    except (TypeError, ValueError) as exc:
        raise FrameError("terminal result must be JSON serializable") from exc
    if len(wire.encode("utf-8")) > 64 * 1024:
        raise FrameError("terminal result exceeds the private result bound")
    return wire


class FrameStore:
    """Private continuation metadata stored beside canonical JobService rows."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.row_factory = sqlite3.Row

    @staticmethod
    def initialize(db: sqlite3.Connection) -> None:
        """Create additive private tables on the caller's JobService database."""
        db.executescript("""
            CREATE TABLE IF NOT EXISTS pa1_frames(
                job_id TEXT PRIMARY KEY, root_id TEXT NOT NULL, parent_id TEXT,
                generation INTEGER NOT NULL DEFAULT 0, depth INTEGER NOT NULL,
                scope TEXT NOT NULL, deadline REAL NOT NULL,
                turn_limit INTEGER NOT NULL, turns_used INTEGER NOT NULL DEFAULT 0,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                turns_reserved INTEGER NOT NULL DEFAULT 0, root_turns_reserved INTEGER NOT NULL DEFAULT 0,
                credit_limit INTEGER NOT NULL DEFAULT 0, credit_used INTEGER NOT NULL DEFAULT 0,
                origin_class TEXT NOT NULL DEFAULT 'public_demand',
                read_scope_seal TEXT,
                state TEXT NOT NULL DEFAULT 'queued', terminal_version INTEGER NOT NULL DEFAULT 0,
                terminal_status TEXT, terminal_result TEXT, updated REAL NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS pa1_frames_root ON pa1_frames(root_id,depth);
            CREATE TABLE IF NOT EXISTS pa1_private_ids(job_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS pa1_frame_waits(
                parent_id TEXT NOT NULL, generation INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'waiting', release_verified INTEGER NOT NULL DEFAULT 0,
                wake_version INTEGER, wake_consumed INTEGER NOT NULL DEFAULT 0, pending_ack TEXT,
                created REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(parent_id,generation),
                FOREIGN KEY(parent_id) REFERENCES pa1_frames(job_id));
            CREATE TABLE IF NOT EXISTS pa1_frame_children(
                parent_id TEXT NOT NULL, generation INTEGER NOT NULL, child_id TEXT NOT NULL UNIQUE,
                ordinal INTEGER NOT NULL, PRIMARY KEY(parent_id,generation,child_id),
                FOREIGN KEY(parent_id,generation) REFERENCES pa1_frame_waits(parent_id,generation));
            CREATE TABLE IF NOT EXISTS pa1_protocol_feedback(
                job_id TEXT PRIMARY KEY, scope TEXT NOT NULL, generation INTEGER NOT NULL,
                payload TEXT NOT NULL, updated REAL NOT NULL);
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(pa1_frames)")}
        if "failed_attempts" not in columns:
            db.execute("ALTER TABLE pa1_frames ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0")
        if "origin_class" not in columns:
            # Before automatic roots existed, all admitted roots represented
            # explicit public demand. Preserve that meaning during migration.
            db.execute("ALTER TABLE pa1_frames ADD COLUMN origin_class TEXT NOT NULL DEFAULT 'public_demand'")
        if "read_scope_seal" not in columns:
            db.execute("ALTER TABLE pa1_frames ADD COLUMN read_scope_seal TEXT")
        wait_columns = {row[1] for row in db.execute("PRAGMA table_info(pa1_frame_waits)")}
        if "pending_ack" not in wait_columns:
            db.execute("ALTER TABLE pa1_frame_waits ADD COLUMN pending_ack TEXT")

    def register_root(self, job_id: str, scope: Mapping[str, Any], deadline: float,
                      *, max_turns: int = DEFAULT_MODEL_TURN_LIMIT,
                      origin_class: str = "public_demand", automatic_read_scope: Mapping[str, Any] | None = None,
                      now: float = 0.0) -> None:
        if not job_id or not isinstance(scope, Mapping) or not scope:
            raise FrameError("root job id and trusted scope are required")
        if not isinstance(origin_class, str) or origin_class not in _ORIGIN_CLASSES:
            raise FrameError("root origin class must be public_demand or automatic")
        if origin_class == "automatic":
            read_scope_wire = _dump(normalize_automatic_read_scope(automatic_read_scope))
        elif automatic_read_scope is not None:
            raise FrameError("public demand roots cannot carry an automatic read scope")
        else:
            read_scope_wire = None
        if deadline <= now or not 1 <= max_turns <= DEFAULT_MODEL_TURN_LIMIT:
            raise FrameError("root deadline or model-turn budget is invalid")
        wire = _dump(dict(scope))
        old = self.db.execute("SELECT * FROM pa1_frames WHERE job_id=?", (job_id,)).fetchone()
        if old:
            if (old["parent_id"] is None and old["scope"] == wire and old["deadline"] == deadline
                    and old["origin_class"] == origin_class
                    and old["read_scope_seal"] == read_scope_wire):
                return
            raise FrameError("job is already registered with another frame identity")
        self.db.execute("""INSERT INTO pa1_frames(job_id,root_id,parent_id,generation,depth,scope,
            deadline,turn_limit,credit_limit,origin_class,read_scope_seal,updated)
            VALUES(?,?,NULL,0,0,?,?,?,?,?,?,?)""",
            (job_id, job_id, wire, float(deadline), int(max_turns), MAX_ROOT_CREDITS,
             origin_class, read_scope_wire, float(now)))

    def register_wait(self, parent_id: str, generation: int,
                      child_specs: Sequence[ChildFrameSpec], *, now: float = 0.0) -> WaitOutcome:
        """Persist one bounded wait and preallocated children inside caller tx.

        Existing identical registrations are idempotent.  A new generation is
        accepted only after the parent's prior wait has been consumed.
        """
        if not isinstance(generation, int) or generation < 1 or not child_specs:
            raise FrameError("wait generation and at least one child are required")
        parent = self.db.execute("SELECT * FROM pa1_frames WHERE job_id=?", (parent_id,)).fetchone()
        if not parent or parent["state"] in _TERMINAL:
            raise FrameError("parent frame is unavailable")
        existing = self.db.execute("SELECT state FROM pa1_frame_waits WHERE parent_id=? AND generation=?",
                                   (parent_id, generation)).fetchone()
        if existing:
            ids = tuple(r[0] for r in self.db.execute(
                "SELECT child_id FROM pa1_frame_children WHERE parent_id=? AND generation=? ORDER BY ordinal",
                (parent_id, generation)))
            if ids != tuple(spec.job_id for spec in child_specs):
                raise FrameError("wait generation was already registered with different children")
            return self.reconcile(parent_id, generation) or WaitOutcome(parent_id, generation, False, ())
        if generation <= parent["generation"]:
            raise FrameError("stale parent generation")
        previous = self.db.execute("SELECT state,wake_consumed FROM pa1_frame_waits WHERE parent_id=? ORDER BY generation DESC LIMIT 1",
                                   (parent_id,)).fetchone()
        if previous and (previous["state"] != "ready" or not previous["wake_consumed"]):
            raise FrameError("prior parent wait has not been consumed")
        if parent["depth"] >= MAX_ROOT_DEPTH:
            raise FrameError("descendant depth limit reached")
        specs = tuple(child_specs)
        if len(specs) > MAX_ROOT_CREDITS:
            raise FrameError("one wait exceeds the descendant credit limit")
        if len({spec.job_id for spec in specs}) != len(specs) or parent_id in {s.job_id for s in specs}:
            raise FrameError("child identities must be unique and cannot form a self-cycle")
        root = self.db.execute("SELECT * FROM pa1_frames WHERE job_id=?", (parent["root_id"],)).fetchone()
        if not root:
            raise FrameError("root frame metadata is missing")
        if parent["origin_class"] == "automatic":
            if (root["origin_class"] != "automatic" or parent["read_scope_seal"] is None
                    or parent["read_scope_seal"] != root["read_scope_seal"]):
                raise FrameError("automatic descendant read scope does not match its root seal")
        elif parent["read_scope_seal"] is not None or root["read_scope_seal"] is not None:
            raise FrameError("public demand frames cannot carry an automatic read scope")
        if root["credit_used"] + len(specs) > root["credit_limit"]:
            raise FrameError("root descendant credits exhausted")
        private_count = self.db.execute("SELECT count(*) FROM pa1_frames WHERE parent_id IS NOT NULL AND state NOT IN ('completed','failed','cancelled','expired','partial')").fetchone()[0]
        if private_count + len(specs) > MAX_PRIVATE_FRAMES:
            raise FrameError("global private frame capacity reached")
        parent_scope = json.loads(parent["scope"])
        children: list[tuple[ChildFrameSpec, str]] = []
        for spec in specs:
            if not spec.job_id or self.db.execute("SELECT 1 FROM pa1_frames WHERE job_id=?", (spec.job_id,)).fetchone():
                raise FrameError("child identity is already in the frame graph")
            if not isinstance(spec.scope, Mapping):
                raise FrameError("child scope must be a typed mapping")
            effective_scope = _scope_intersection(parent_scope, spec.scope)
            if spec.deadline <= now or spec.deadline > parent["deadline"]:
                raise FrameError("child deadline must be live and no later than its parent")
            if not 1 <= spec.turn_reservation <= 2:
                raise FrameError("child turn reservation must be one or two turns")
            if not isinstance(spec.question, str) or len(spec.question.encode("utf-8")) > 8192:
                raise FrameError("child request is not bounded text")
            children.append((spec, _dump(effective_scope)))
        # A wait reserves one turn for its parent to incorporate results. Child
        # turns and that resume turn share the root's cumulative six-turn budget.
        old_parent_reservation = int(parent["turns_reserved"])
        needed = 1 + sum(spec.turn_reservation for spec, _ in children)
        root_reserved = int(root["root_turns_reserved"])
        parent_local_room = int(parent["turn_limit"]) - int(parent["turns_used"]) - old_parent_reservation
        root_room = int(root["turn_limit"]) - int(root["turns_used"]) - root_reserved + old_parent_reservation
        if parent_local_room < 1 or root_room < needed:
            raise FrameError("root model-turn budget cannot reserve children and parent resume")
        self.db.execute("INSERT INTO pa1_frame_waits(parent_id,generation,created) VALUES(?,?,?)",
                        (parent_id, generation, float(now)))
        for ordinal, (spec, scope_wire) in enumerate(children):
            self.db.execute("""INSERT INTO pa1_frames(job_id,root_id,parent_id,generation,depth,scope,
            deadline,turn_limit,turns_reserved,origin_class,read_scope_seal,updated)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (spec.job_id, parent["root_id"], parent_id, 0, parent["depth"] + 1, scope_wire,
                 float(spec.deadline), DEFAULT_MODEL_TURN_LIMIT, int(spec.turn_reservation),
                 root["origin_class"], root["read_scope_seal"], float(now)))
            self.db.execute("INSERT INTO pa1_private_ids(job_id) VALUES(?)", (spec.job_id,))
            self.db.execute("INSERT INTO pa1_frame_children(parent_id,generation,child_id,ordinal) VALUES(?,?,?,?)",
                            (parent_id, generation, spec.job_id, ordinal))
        self.db.execute("""UPDATE pa1_frames SET generation=?,state='waiting',turns_reserved=?,
            credit_used=credit_used+?,updated=? WHERE job_id=?""",
                        (generation, 1, len(children), float(now), parent_id))
        self.db.execute("UPDATE pa1_frames SET root_turns_reserved=root_turns_reserved+?,updated=? WHERE job_id=?",
                        (needed - old_parent_reservation, float(now), parent["root_id"]))
        self.db.execute("UPDATE pa1_frames SET credit_used=credit_used+?,updated=? WHERE job_id=?",
                        (len(children), float(now), parent["root_id"]))
        return WaitOutcome(parent_id, generation, False, ())

    def is_private(self, job_id: str) -> bool:
        return (self.db.execute("SELECT 1 FROM pa1_private_ids WHERE job_id=?", (job_id,)).fetchone() is not None
                or self.db.execute("SELECT 1 FROM pa1_frames WHERE job_id=? AND origin_class='automatic'",
                                   (job_id,)).fetchone() is not None)

    def record_failure_attempt(self, job_id: str, attempt: int, *, now: float = 0.0) -> int:
        """Count retryable execution failures separately from cooperative slices."""
        row = self.db.execute("SELECT failed_attempts FROM pa1_frames WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise FrameError("frame metadata is missing")
        self.db.execute("UPDATE pa1_frames SET failed_attempts=failed_attempts+1,updated=? WHERE job_id=?",
                        (float(now), job_id))
        return int(row["failed_attempts"]) + 1

    @staticmethod
    def _validate_protocol_feedback(value: object) -> str:
        """Bound controller-generated protocol repair hints; never store model prose."""
        if not isinstance(value, list) or len(value) > 4:
            raise FrameError("protocol feedback must be a list of at most four items")
        normalized = []
        for item in value:
            if not isinstance(item, Mapping) or set(item) != {"role", "content"} or item.get("role") != "user":
                raise FrameError("protocol feedback has an invalid message envelope")
            content = item.get("content")
            if not isinstance(content, str) or len(content.encode("utf-8")) > 2048:
                raise FrameError("protocol feedback content exceeds its bound")
            try:
                body = json.loads(content)
            except (TypeError, ValueError) as exc:
                raise FrameError("protocol feedback must be a typed JSON object") from exc
            if (not isinstance(body, Mapping) or set(body) - {"protocol_error", "detail", "instruction"}
                    or not isinstance(body.get("protocol_error"), str)
                    or body["protocol_error"] not in _PROTOCOL_ERRORS
                    or not isinstance(body.get("instruction"), str)
                    or not body["instruction"] or len(body["instruction"].encode("utf-8")) > 1536
                    or ("detail" in body and (not isinstance(body["detail"], str)
                        or len(body["detail"].encode("utf-8")) > 256))):
                raise FrameError("protocol feedback fields are not an allowed operational hint")
            normalized.append({"role": "user", "content": _dump(dict(body))})
        payload = _dump(normalized)
        if len(payload.encode("utf-8")) > 4096:
            raise FrameError("protocol feedback exceeds its total bound")
        return payload

    def load_protocol_feedback(self, job_id: str, scope: Mapping[str, Any], generation: int) -> list[dict[str, str]]:
        row = self.db.execute("SELECT * FROM pa1_protocol_feedback WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return []
        if row["scope"] != _dump(dict(scope)) or int(row["generation"]) != generation:
            raise FrameError("protocol feedback scope or generation mismatch")
        return json.loads(row["payload"])

    def save_protocol_feedback(self, job_id: str, scope: Mapping[str, Any], generation: int,
                               attempt: int, value: object, *, now: float = 0.0) -> None:
        frame = self.get_frame(job_id)
        slot = self.db.execute("SELECT attempt FROM execution_slots WHERE job=?", (job_id,)).fetchone()
        row = self.db.execute("SELECT scope FROM jobs WHERE id=?", (job_id,)).fetchone()
        if (frame is None or frame.generation != generation or not slot or int(slot["attempt"]) != attempt
                or row is None or row["scope"] != _dump(dict(scope))):
            raise FrameError("protocol feedback requires its exact active job generation")
        payload = self._validate_protocol_feedback(value)
        if payload == "[]":
            self.db.execute("DELETE FROM pa1_protocol_feedback WHERE job_id=?", (job_id,))
        else:
            self.db.execute("""INSERT INTO pa1_protocol_feedback(job_id,scope,generation,payload,updated)
                VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET scope=excluded.scope,
                generation=excluded.generation,payload=excluded.payload,updated=excluded.updated""",
                (job_id, _dump(dict(scope)), generation, payload, float(now)))

    def get_frame(self, job_id: str) -> FrameRecord | None:
        row = self.db.execute("SELECT * FROM pa1_frames WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return None
        return FrameRecord(row["job_id"], row["root_id"], row["parent_id"], int(row["generation"]),
                           int(row["depth"]), json.loads(row["scope"]), float(row["deadline"]),
                           int(row["turn_limit"]), int(row["turns_used"]), int(row["failed_attempts"]),
                           int(row["turns_reserved"]),
                           int(self.db.execute("SELECT turns_used FROM pa1_frames WHERE job_id=?", (row["root_id"],)).fetchone()[0]),
                           int(row["root_turns_reserved"] if row["job_id"] == row["root_id"] else self.db.execute("SELECT root_turns_reserved FROM pa1_frames WHERE job_id=?", (row["root_id"],)).fetchone()[0]),
                           int(row["credit_limit"]), int(row["credit_used"]), row["state"], row["origin_class"],
                           json.loads(row["read_scope_seal"]) if row["read_scope_seal"] else None)

    def automatic_read_scope(self, job_id: str) -> dict[str, Any] | None:
        """Return the persisted automatic seal for a root or descendant."""
        frame = self.get_frame(job_id)
        if frame is None or frame.origin_class != "automatic":
            return None
        root = self.get_frame(frame.root_id)
        if (root is None or root.parent_id is not None or root.origin_class != "automatic"
                or frame.automatic_read_scope is None
                or frame.automatic_read_scope != root.automatic_read_scope):
            raise FrameError("automatic read scope is missing or inconsistent")
        return normalize_automatic_read_scope(frame.automatic_read_scope)

    def remaining_turns(self, job_id: str) -> int:
        frame = self.get_frame(job_id)
        if frame is None:
            raise FrameError("frame metadata is missing")
        if frame.turns_reserved:
            return frame.turns_reserved
        return frame.turn_limit - frame.root_turns_used - frame.root_turns_reserved

    def eligible(self, job_id: str, now: float) -> bool:
        row = self.db.execute("""SELECT f.*,w.release_verified,w.state AS wait_state
            FROM pa1_frames f JOIN pa1_frame_waits w ON w.parent_id=f.parent_id
              AND w.generation=(SELECT max(generation) FROM pa1_frame_waits WHERE parent_id=f.parent_id)
            WHERE f.job_id=? AND f.parent_id IS NOT NULL""", (job_id,)).fetchone()
        if row and row["parent_id"]:
            owner = self.db.execute("SELECT state FROM pa1_frames WHERE job_id=?", (row["parent_id"],)).fetchone()
            parent_state = owner["state"] if owner else None
        else:
            parent_state = None
        return bool(row and parent_state == "suspended" and row["wait_state"] == "waiting" and row["release_verified"]
                    and float(now) < row["deadline"] and row["state"] == "queued")

    def consume_turns(self, job_id: str, generation: int, count: int = 1, *, attempt: int,
                      now: float = 0.0) -> int:
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise FrameError("model turn reservation must be positive")
        row = self.db.execute("SELECT * FROM pa1_frames WHERE job_id=?", (job_id,)).fetchone()
        if not row or int(row["generation"]) != generation or row["state"] in _TERMINAL:
            # A terminal frame can still have a model turn completing under its
            # already-owned execution slot. It is charged below without changing
            # state or releasing/reusing any additional reservation.
            if not row or int(row["generation"]) != generation:
                raise FrameError("stale frame generation")
        slot = self.db.execute("SELECT attempt FROM execution_slots WHERE job=?", (job_id,)).fetchone()
        if not slot or int(slot["attempt"]) != attempt:
            raise FrameError("model turns require the matching owned execution attempt")
        terminal = row["state"] in _TERMINAL
        if not terminal and row["state"] != "running":
            raise FrameError("frame must be running before model-turn accounting")
        # This is post-execution accounting: even a result arriving after its
        # logical deadline must be charged while its exact physical slot remains
        # owned. Admission prevents late starts; this path never revives output.
        root = self.db.execute("SELECT * FROM pa1_frames WHERE job_id=?", (row["root_id"],)).fetchone()
        used = int(row["turns_used"])
        root_used = int(root["turns_used"])
        root_reserved = int(root["root_turns_reserved"])
        if terminal:
            consumed_reserved = 0
        elif row["parent_id"] is not None:
            if int(row["turns_reserved"]) < count or used + count > int(row["turn_limit"]):
                raise FrameError("frame turn reservation exhausted")
            consumed_reserved = count
        else:
            own_reserved = int(row["turns_reserved"])
            unreserved_room = int(root["turn_limit"]) - root_used - root_reserved
            if count > own_reserved + unreserved_room or used + count > int(row["turn_limit"]):
                raise FrameError("cumulative root model-turn budget exhausted")
            consumed_reserved = min(count, own_reserved)
        if terminal:
            if job_id == row["root_id"]:
                self.db.execute("UPDATE pa1_frames SET turns_used=turns_used+?,updated=? WHERE job_id=? AND generation=?",
                                (count, float(now), job_id, generation))
            else:
                self.db.execute("UPDATE pa1_frames SET turns_used=turns_used+?,updated=? WHERE job_id=? AND generation=?",
                                (count, float(now), job_id, generation))
                self.db.execute("UPDATE pa1_frames SET turns_used=turns_used+? WHERE job_id=?",
                                (count, row["root_id"]))
        elif job_id == row["root_id"]:
            self.db.execute("""UPDATE pa1_frames SET turns_used=turns_used+?,turns_reserved=turns_reserved-?,
                root_turns_reserved=root_turns_reserved-?,updated=? WHERE job_id=? AND generation=?""",
                (count, consumed_reserved, consumed_reserved, float(now), job_id, generation))
        else:
            self.db.execute("UPDATE pa1_frames SET turns_used=turns_used+?,turns_reserved=turns_reserved-?,updated=? WHERE job_id=? AND generation=?",
                            (count, consumed_reserved, float(now), job_id, generation))
            self.db.execute("UPDATE pa1_frames SET turns_used=turns_used+?,root_turns_reserved=root_turns_reserved-? WHERE job_id=?",
                            (count, consumed_reserved, row["root_id"]))
        return used + count

    def record_terminal(self, job_id: str, generation: int, status: str,
                        result: Mapping[str, Any], *, now: float = 0.0) -> bool:
        if status not in _TERMINAL:
            raise FrameError("terminal status is not recognized")
        row = self.db.execute("SELECT * FROM pa1_frames WHERE job_id=?", (job_id,)).fetchone()
        if not row or int(row["generation"]) != generation or row["state"] in _TERMINAL:
            return False
        if now >= row["deadline"] and status not in {"expired", "cancelled"}:
            return False
        wire = _visible_result(result)
        version = int(row["terminal_version"]) + 1
        reserved = int(row["turns_reserved"])
        self.db.execute("""UPDATE pa1_frames SET state=?,terminal_version=?,terminal_status=?,
            terminal_result=?,turns_reserved=0,updated=? WHERE job_id=? AND generation=?""",
            (status, version, status, wire, float(now), job_id, generation))
        self.db.execute("DELETE FROM pa1_protocol_feedback WHERE job_id=?", (job_id,))
        if reserved:
            self.db.execute("UPDATE pa1_frames SET root_turns_reserved=root_turns_reserved-? WHERE job_id=?",
                            (reserved, row["root_id"]))
        return True

    def mark_parent_released(self, parent_id: str, generation: int, *, now: float = 0.0) -> bool:
        """Arm children only after JobService confirms execution-slot removal."""
        wait = self.db.execute("SELECT * FROM pa1_frame_waits WHERE parent_id=? AND generation=?",
                               (parent_id, generation)).fetchone()
        if not wait:
            return False
        parent = self.db.execute("SELECT generation,state FROM pa1_frames WHERE job_id=?", (parent_id,)).fetchone()
        if (not parent or int(parent["generation"]) != generation
                or parent["state"] != "waiting" or wait["state"] not in {"waiting", "ready"}):
            return False
        if self.db.execute("SELECT 1 FROM execution_slots WHERE job=? LIMIT 1", (parent_id,)).fetchone():
            return False
        self.db.execute("UPDATE pa1_frame_waits SET release_verified=1 WHERE parent_id=? AND generation=?",
                        (parent_id, generation))
        self.db.execute("UPDATE pa1_frames SET state='suspended',updated=? WHERE job_id=? AND generation=?",
                        (float(now), parent_id, generation))
        return True

    def reconcile(self, parent_id: str, generation: int) -> WaitOutcome | None:
        """Materialize a durable wake once all children have terminal versions."""
        wait = self.db.execute("SELECT * FROM pa1_frame_waits WHERE parent_id=? AND generation=?",
                               (parent_id, generation)).fetchone()
        if not wait:
            return None
        if wait["state"] == "retired":
            return WaitOutcome(parent_id, generation, False, (), wait["wake_version"])
        if wait["wake_consumed"]:
            return WaitOutcome(parent_id, generation, True, (), int(wait["wake_version"]))
        rows = self.db.execute("""SELECT f.job_id,f.terminal_status,f.terminal_result,f.terminal_version
            FROM pa1_frame_children c JOIN pa1_frames f ON f.job_id=c.child_id
            WHERE c.parent_id=? AND c.generation=? ORDER BY c.ordinal""", (parent_id, generation)).fetchall()
        if not rows or any(row["terminal_version"] < 1 for row in rows):
            return WaitOutcome(parent_id, generation, False, ())
        if wait["wake_version"] is None:
            version = int(wait["generation"])
            self.db.execute("UPDATE pa1_frame_waits SET state='ready',wake_version=? WHERE parent_id=? AND generation=? AND wake_version IS NULL",
                            (version, parent_id, generation))
        else:
            version = int(wait["wake_version"])
        outcomes = tuple(ChildOutcome(row["job_id"], row["terminal_status"],
                                      json.loads(row["terminal_result"] or "{}"),
                                      int(row["terminal_version"])) for row in rows)
        return WaitOutcome(parent_id, generation, True, outcomes, version)

    def prepare_wake_ack(self, parent_id: str, generation: int,
                         child_ids: Sequence[str]) -> bool:
        """Persist the exact validated child set before parent-slot cleanup.

        The broker calls this in the same transaction as its parent result and
        checkpoint update.  A cleanup pass consumes this acknowledgement only
        after the parent's execution slot has been physically removed.
        """
        outcome = self.reconcile(parent_id, generation)
        if not outcome or not outcome.ready or outcome.wake_version is None:
            return False
        expected = tuple(sorted(child.job_id for child in outcome.children))
        supplied = tuple(child_ids)
        if len(set(supplied)) != len(supplied) or set(supplied) != set(expected):
            raise FrameError("wake acknowledgement must list exactly the stored child IDs")
        ack = _dump(list(expected))
        wait = self.db.execute("SELECT pending_ack FROM pa1_frame_waits WHERE parent_id=? AND generation=?",
                               (parent_id, generation)).fetchone()
        if not wait:
            return False
        if wait["pending_ack"] is not None:
            if wait["pending_ack"] != ack:
                raise FrameError("wake acknowledgement is already bound to another child set")
            return True
        self.db.execute("UPDATE pa1_frame_waits SET pending_ack=? WHERE parent_id=? AND generation=? AND pending_ack IS NULL",
                        (ack, parent_id, generation))
        return self.db.execute("SELECT changes()").fetchone()[0] == 1

    def pending_wake_ack(self, parent_id: str, generation: int) -> tuple[str, ...] | None:
        row = self.db.execute("SELECT pending_ack FROM pa1_frame_waits WHERE parent_id=? AND generation=?",
                              (parent_id, generation)).fetchone()
        if not row or row["pending_ack"] is None:
            return None
        try:
            values = json.loads(row["pending_ack"])
        except (TypeError, ValueError):
            return None
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            return None
        return tuple(values)

    def consume_wake(self, parent_id: str, generation: int, wake_version: int) -> WaitOutcome | None:
        """Consume an acknowledged wake once, after verified parent cleanup.

        Call this in the same caller-owned transaction as checkpointing the
        returned outcomes. A rollback preserves both the acknowledgement and
        child result rows for safe retry after restart.
        """
        outcome = self.reconcile(parent_id, generation)
        if not outcome or not outcome.ready or outcome.wake_version != wake_version:
            return None
        row = self.db.execute("SELECT wake_consumed,pending_ack FROM pa1_frame_waits WHERE parent_id=? AND generation=?",
                              (parent_id, generation)).fetchone()
        wait = self.db.execute("SELECT release_verified FROM pa1_frame_waits WHERE parent_id=? AND generation=?",
                               (parent_id, generation)).fetchone()
        parent = self.db.execute("SELECT state,generation FROM pa1_frames WHERE job_id=?", (parent_id,)).fetchone()
        expected_ack = tuple(sorted(child.job_id for child in outcome.children))
        try:
            acknowledged = tuple(json.loads(row["pending_ack"])) if row and row["pending_ack"] is not None else None
        except (TypeError, ValueError):
            acknowledged = None
        if (not parent or int(parent["generation"]) != generation
                or row["wake_consumed"] or not wait["release_verified"] or acknowledged != expected_ack):
            return None
        if self.db.execute("SELECT 1 FROM execution_slots WHERE job=? LIMIT 1", (parent_id,)).fetchone():
            return None
        if any(self.db.execute("SELECT 1 FROM execution_slots WHERE job=? LIMIT 1", (child.job_id,)).fetchone()
               for child in outcome.children):
            return None
        changed = self.db.execute("""UPDATE pa1_frame_waits SET wake_consumed=1,pending_ack=NULL
            WHERE parent_id=? AND generation=? AND wake_consumed=0 AND pending_ack=?""",
            (parent_id, generation, row["pending_ack"])).rowcount
        if changed != 1:
            return None
        if parent["state"] not in _TERMINAL:
            changed = self.db.execute("""UPDATE pa1_frames SET state='running'
                WHERE job_id=? AND generation=? AND state NOT IN ('completed','failed','cancelled','expired','partial')""",
                (parent_id, generation)).rowcount
            if changed != 1:
                raise FrameError("parent frame changed while consuming wake")
        # The broker must checkpoint the returned visible outcomes in this same
        # transaction. Retain only tiny privacy tombstones after that handoff.
        self.db.executemany("DELETE FROM pa1_frames WHERE job_id=?", ((child.job_id,) for child in outcome.children))
        return outcome

    def retire_wait(self, parent_id: str, generation: int) -> bool:
        """Discard unconsumed child payloads after terminal parent cleanup.

        Use for failed/cancelled parents that cannot provide a result-consumed
        acknowledgement. Child IDs remain private tombstones and provenance
        edges remain durable; live child state must already be terminal.
        """
        parent = self.db.execute("SELECT generation,state FROM pa1_frames WHERE job_id=?", (parent_id,)).fetchone()
        wait = self.db.execute("SELECT * FROM pa1_frame_waits WHERE parent_id=? AND generation=?",
                               (parent_id, generation)).fetchone()
        if not parent or not wait or int(parent["generation"]) != generation or parent["state"] not in _TERMINAL:
            return False
        if self.db.execute("SELECT 1 FROM execution_slots WHERE job=? LIMIT 1", (parent_id,)).fetchone():
            return False
        if wait["wake_consumed"] or wait["state"] == "retired":
            return True
        ids = tuple(row[0] for row in self.db.execute(
            "SELECT child_id FROM pa1_frame_children WHERE parent_id=? AND generation=?",
            (parent_id, generation)))
        if not ids:
            return False
        for child_id in ids:
            if self.db.execute("SELECT 1 FROM execution_slots WHERE job=? LIMIT 1", (child_id,)).fetchone():
                return False
            child = self.db.execute("SELECT terminal_version FROM pa1_frames WHERE job_id=?", (child_id,)).fetchone()
            # A missing row means an earlier acknowledged consume already
            # discarded its payload, which is safe to retire idempotently.
            if child is not None and child["terminal_version"] < 1:
                return False
        self.db.execute("UPDATE pa1_frame_waits SET state='retired',wake_consumed=1,pending_ack=NULL WHERE parent_id=? AND generation=?",
                        (parent_id, generation))
        self.db.executemany("DELETE FROM pa1_frames WHERE job_id=?", ((child_id,) for child_id in ids))
        return True
