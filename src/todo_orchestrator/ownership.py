"""Same-worktree ownership, named-lock, and guard semantics."""

from __future__ import annotations

import json
import os
import secrets
import socket
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import utc_now
from .git_state import canonical_relative, paths_overlap
from .models import ExitCode, TodoError
from .resources import local_process_alive, process_start
from .sessions import token_hash


def scopes_for(conn: sqlite3.Connection, task_id: str, mode: str | None = None) -> list[str]:
    if mode:
        rows = conn.execute("SELECT path FROM ownership_scopes WHERE task_id=? AND mode=? ORDER BY path", (task_id, mode))
    else:
        rows = conn.execute("SELECT path FROM ownership_scopes WHERE task_id=? ORDER BY path", (task_id,))
    return [row[0] for row in rows]


def _frozen_contract_read_allowed(conn: sqlite3.Connection, reader_task_id: str, writer_task_id: str, read_path: str, writer_root: str) -> bool:
    """A registered consumer may read a frozen contract while its owner continues.

    The owner still needs an explicit interface revision to change the contract;
    all non-contract paths retain ordinary exclusive/read conflict behavior.
    """
    for row in conn.execute(
        "SELECT i.contract_paths_json,i.state,i.version,ic.required_state,ic.required_version "
        "FROM interfaces i JOIN interface_consumers ic ON ic.interface_id=i.id "
        "WHERE i.owner_task_id=? AND ic.task_id=?",
        (writer_task_id, reader_task_id),
    ):
        if row["state"] != row["required_state"] or (row["required_version"] and row["version"] != row["required_version"]):
            continue
        for contract_path in __import__("json").loads(row["contract_paths_json"]):
            if paths_overlap(read_path, contract_path) and paths_overlap(writer_root, contract_path):
                return True
    return False


def _isolated_merge_overlap_allowed(
    conn: sqlite3.Connection,
    candidate_task_id: str,
    active_task_id: str,
    active_claim_id: str,
) -> bool:
    """Prove that an overlap is isolated from the shared authoritative tree.

    A branch name or symbolic scope is never enough. Both tasks must be assigned
    to first-class isolated lanes with active managed workspaces from one base,
    and the declared integration task must have its own active exclusive
    destination workspace owned by an integrator lane.
    """
    candidate = conn.execute(
        "SELECT l.id AS lane_id,l.run_id,l.workspace_mode,w.base_commit,w.integration_task_id,w.state "
        "FROM workflow_lane_tasks lt JOIN workflow_lanes l ON l.id=lt.lane_id "
        "JOIN workflow_runs r ON r.id=l.run_id AND r.status='active' "
        "JOIN workflow_workspaces w ON w.run_id=l.run_id AND w.lane_id=l.id "
        "WHERE lt.task_id=? AND lt.state='queued' AND l.state='ready' AND l.workspace_mode='isolated_merge' "
        "AND w.mode='isolated_merge' AND w.state IN ('active','artifact_ready') "
        "ORDER BY l.run_id,l.id LIMIT 1",
        (candidate_task_id,),
    ).fetchone()
    active = conn.execute(
        "SELECT l.id AS lane_id,l.run_id,l.workspace_mode,w.base_commit,w.integration_task_id,w.state "
        "FROM workflow_dispatches d JOIN workflow_lanes l ON l.id=d.lane_id "
        "JOIN workflow_lane_tasks lt ON lt.lane_id=l.id AND lt.task_id=? AND lt.state='active' "
        "JOIN workflow_workspaces w ON w.run_id=l.run_id AND w.lane_id=l.id "
        "WHERE d.claim_id=? AND d.state='active' AND l.workspace_mode='isolated_merge' "
        "AND w.mode='isolated_merge' AND w.state IN ('active','artifact_ready') LIMIT 1",
        (active_task_id, active_claim_id),
    ).fetchone()
    if not candidate or not active:
        return False
    integration_task_id = candidate["integration_task_id"]
    if not (
        candidate["run_id"] == active["run_id"]
        and candidate["base_commit"] == active["base_commit"]
        and integration_task_id
        and integration_task_id == active["integration_task_id"]
    ):
        return False
    destination = conn.execute(
        "SELECT 1 FROM workflow_lanes l "
        "JOIN workflow_lane_tasks lt ON lt.lane_id=l.id AND lt.task_id=? "
        "JOIN workflow_workspaces w ON w.run_id=l.run_id AND w.lane_id=l.id "
        "WHERE l.run_id=? AND l.role IN ('integrator','validator') "
        "AND l.workspace_mode='exclusive' "
        "AND w.mode='exclusive' AND w.integration_task_id=? AND w.base_commit=? "
        "AND w.state IN ('active','artifact_ready') LIMIT 1",
        (integration_task_id, candidate["run_id"], integration_task_id, candidate["base_commit"]),
    ).fetchone()
    return destination is not None


def sweep_lock_leases(conn: sqlite3.Connection) -> list[str]:
    now = utc_now()
    released: list[str] = []
    for lease in conn.execute("SELECT * FROM lock_leases WHERE state='active' AND expires_at<=?", (now,)):
        if lease["hostname"] == socket.gethostname() and local_process_alive(lease["pid"], lease["process_start"]):
            continue
        conn.execute("UPDATE lock_leases SET state='expired' WHERE id=?", (lease["id"],))
        released.append(lease["id"])
    return released


def _different_active_workflow_lanes(
    conn: sqlite3.Connection,
    candidate_task_id: str,
    active_task_id: str,
) -> bool:
    """Return whether workflow-v3 lane order already separates two tasks.

    ``parallel_policy=serial`` predates first-class workflow lanes and remains
    project-wide for unassigned/legacy work. In a schema-v3 run, seriality is
    the lane queue contract: distinct lanes may proceed together after the
    ordinary scope, lock, and resource checks below pass.
    """
    rows = conn.execute(
        "SELECT lt.task_id,l.id AS lane_id,l.run_id FROM workflow_lane_tasks lt "
        "JOIN workflow_lanes l ON l.id=lt.lane_id "
        "JOIN workflow_runs r ON r.id=l.run_id AND r.status='active' "
        "WHERE lt.task_id IN (?,?) AND lt.state IN ('queued','active') "
        "ORDER BY lt.task_id,l.run_id,l.id",
        (candidate_task_id, active_task_id),
    ).fetchall()
    candidate = [row for row in rows if row["task_id"] == candidate_task_id]
    active = [row for row in rows if row["task_id"] == active_task_id]
    return any(
        left["run_id"] == right["run_id"] and left["lane_id"] != right["lane_id"]
        for left in candidate
        for right in active
    )


def ownership_conflicts(conn: sqlite3.Connection, task_id: str) -> list[dict[str, str]]:
    task = conn.execute("SELECT parallel_policy FROM tasks WHERE id=?", (task_id,)).fetchone()
    active = conn.execute(
        "SELECT c.id AS claim_id,c.task_id,t.parallel_policy FROM claims c JOIN tasks t ON t.id=c.task_id WHERE c.state='active' AND c.task_id<>?",
        (task_id,),
    ).fetchall()
    if not task:
        return [{"type": "missing_task", "task_id": task_id}]
    if task[0] in {"project_exclusive", "integration_exclusive"} and active:
        return [{"type": "parallel_policy", "task_id": row["task_id"]} for row in active]
    if task[0] == "serial":
        blocked = [
            row for row in active
            if not _different_active_workflow_lanes(conn, task_id, str(row["task_id"]))
        ]
        if blocked:
            return [{"type": "parallel_policy", "task_id": row["task_id"]} for row in blocked]
    blocking_active = [
        row for row in active
        if row["parallel_policy"] in {"project_exclusive", "integration_exclusive"}
        or (
            row["parallel_policy"] == "serial"
            and not _different_active_workflow_lanes(conn, task_id, str(row["task_id"]))
        )
    ]
    if blocking_active:
        return [
            {"type": "active_parallel_policy", "task_id": row["task_id"]}
            for row in blocking_active
        ]
    ours = scopes_for(conn, task_id, "exclusive")
    reads = scopes_for(conn, task_id, "read")
    if task[0] == "parallel_safe" and not ours and not conn.execute("SELECT 1 FROM task_locks WHERE task_id=?", (task_id,)).fetchone():
        return [{"type": "missing_scope", "task_id": task_id}]
    conflicts: list[dict[str, str]] = []
    for row in active:
        theirs = scopes_for(conn, row["task_id"], "exclusive")
        isolated = _isolated_merge_overlap_allowed(conn, task_id, row["task_id"], row["claim_id"])
        for our_path in ours:
            for their_path in theirs:
                if paths_overlap(our_path, their_path) and not isolated:
                    conflicts.append({"type": "path", "path": our_path, "other_path": their_path, "task_id": row["task_id"]})
        for read_path in reads:
            for their_path in theirs:
                if paths_overlap(read_path, their_path) and not isolated and not _frozen_contract_read_allowed(conn, task_id, row["task_id"], read_path, their_path):
                    conflicts.append({"type": "path", "path": read_path, "other_path": their_path, "task_id": row["task_id"]})
    return conflicts


def acquire_task_locks(conn: sqlite3.Connection, task_id: str, claim_id: str, session_id: str, lease_seconds: int) -> list[dict[str, str]]:
    sweep_lock_leases(conn)
    names = [row[0] for row in conn.execute("SELECT lock_name FROM task_locks WHERE task_id=? AND phase='claim' ORDER BY lock_name", (task_id,))]
    acquired: list[dict[str, str]] = []
    now = datetime.now(timezone.utc)
    for name in names:
        active = conn.execute("SELECT 1 FROM lock_leases WHERE lock_name=? AND state='active'", (name,)).fetchone()
        if active:
            raise TodoError("lock_unavailable", f"Named lock {name} is already leased", ExitCode.CONTENTION)
        raw = "tol_" + secrets.token_urlsafe(24)
        lease_id = str(uuid.uuid4())
        expires = (now + timedelta(seconds=lease_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        conn.execute(
            "INSERT INTO lock_leases(id,lock_name,claim_id,session_id,token_hash,state,acquired_at,heartbeat_at,expires_at,hostname,pid,process_start,command_json) VALUES(?,?,?,?,?,'active',?,?,?,?,?,?,?)",
            (lease_id, name, claim_id, session_id, token_hash(raw), utc_now(), utc_now(), expires, socket.gethostname(), os.getpid(), process_start(), "[]"),
        )
        acquired.append({"lease_id": lease_id, "name": name, "token": raw, "expires_at": expires})
    return acquired


def acquire_named_locks(
    conn: sqlite3.Connection,
    names: list[str],
    *,
    claim_id: str | None,
    session_id: str,
    lease_seconds: int,
    command: list[str] | None = None,
) -> list[dict[str, str]]:
    sweep_lock_leases(conn)
    acquired: list[dict[str, str]] = []
    now = datetime.now(timezone.utc)
    for name in sorted(set(names)):
        conn.execute("INSERT OR IGNORE INTO named_locks(name,capacity,metadata_json) VALUES(?,1,'{}')", (name,))
        if conn.execute("SELECT 1 FROM lock_leases WHERE lock_name=? AND state='active'", (name,)).fetchone():
            raise TodoError("lock_unavailable", f"Named lock {name} is already leased", ExitCode.CONTENTION)
        raw = "tol_" + secrets.token_urlsafe(24)
        lease_id = str(uuid.uuid4())
        expires = (now + timedelta(seconds=lease_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        conn.execute(
            "INSERT INTO lock_leases(id,lock_name,claim_id,session_id,token_hash,state,acquired_at,heartbeat_at,expires_at,hostname,pid,process_start,command_json) VALUES(?,?,?,?,?,'active',?,?,?,?,?,?,?)",
            (lease_id, name, claim_id, session_id, token_hash(raw), utc_now(), utc_now(), expires, socket.gethostname(), os.getpid(), process_start(), json.dumps(command or [])),
        )
        acquired.append({"lease_id": lease_id, "name": name, "token": raw, "expires_at": expires})
    return acquired


def release_lock(conn: sqlite3.Connection, lease_token: str) -> dict[str, str]:
    row = conn.execute("SELECT * FROM lock_leases WHERE token_hash=? AND state='active'", (token_hash(lease_token),)).fetchone()
    if not row:
        raise TodoError("invalid_lock_token", "Named-lock token is invalid or inactive", ExitCode.INVALID_TOKEN)
    conn.execute("UPDATE lock_leases SET state='released',expires_at=? WHERE id=?", (utc_now(), row["id"]))
    return {"lease_id": row["id"], "name": row["lock_name"], "session_id": row["session_id"], "state": "released"}


def release_claim_locks(conn: sqlite3.Connection, claim_id: str) -> None:
    conn.execute("UPDATE lock_leases SET state='released',expires_at=? WHERE claim_id=? AND state='active'", (utc_now(), claim_id))


def guard_paths(conn: sqlite3.Connection, repo_root: Path, claim_id: str, paths: list[str]) -> dict[str, object]:
    claim = conn.execute("SELECT task_id FROM claims WHERE id=? AND state='active'", (claim_id,)).fetchone()
    if not claim:
        raise TodoError("claim_not_active", "The claim is not active", ExitCode.INVALID_TOKEN)
    normalized = [canonical_relative(repo_root, value) for value in paths]
    owned = scopes_for(conn, claim["task_id"], "exclusive")
    allowed = [path for path in normalized if any(path == root or path.startswith(root + "/") for root in owned)]
    denied = sorted(set(normalized) - set(allowed))
    if denied:
        raise TodoError("scope_violation", "Paths are outside the active claim's exclusive scope", ExitCode.BLOCKED, {"denied": denied})
    delegated: list[dict[str, str]] = []
    for lease in conn.execute(
        "SELECT l.child_execution_id,l.path FROM child_scope_leases l "
        "JOIN child_executions c ON c.id=l.child_execution_id "
        "WHERE c.parent_claim_id=? AND l.state='active'",
        (claim_id,),
    ):
        for path in allowed:
            if paths_overlap(path, lease["path"]):
                delegated.append({
                    "path": path,
                    "child_path": lease["path"],
                    "child_execution_id": lease["child_execution_id"],
                })
    if delegated:
        raise TodoError(
            "active_child_scope",
            "Cancel, supersede, reject, stale, or accept the delegated child before editing its paths",
            ExitCode.BLOCKED,
            {"conflicts": delegated},
        )
    return {"allowed": allowed, "task_id": claim["task_id"]}
