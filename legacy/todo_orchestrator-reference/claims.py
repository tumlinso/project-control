"""Atomic claims, leases, orphan quarantine, release, and recovery."""

from __future__ import annotations

import json
import hashlib
import os
import pwd
import secrets
import socket
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import utc_now
from .git_state import git_head, scope_manifest
from .models import ExitCode, TodoError
from .ownership import acquire_task_locks, release_claim_locks, scopes_for
from .readiness import ready_tasks
from .resources import acquire_resource, local_process_alive
from .sessions import authenticate_claim, create_session, token_hash


CANONICAL_WORKFLOW_OWNER = "project-control"
COMPATIBLE_WORKFLOW_OWNERS = frozenset({CANONICAL_WORKFLOW_OWNER, "coding-workflow"})


def compatible_workflow_owner(value: object) -> bool:
    """Return whether an owner is a canonical or migration-window workflow facade."""

    return isinstance(value, str) and value in COMPATIBLE_WORKFLOW_OWNERS


def _iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def claim_fingerprint(claim: sqlite3.Row | dict[str, object]) -> str:
    value = dict(claim)
    payload = {
        key: value.get(key)
        for key in (
            "id", "task_id", "session_id", "token_hash", "state", "created_at",
            "heartbeat_at", "expires_at", "baseline_revision", "owner_system",
            "owner_instance_id",
        )
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sweep_expired(conn: sqlite3.Connection, repo_root: Path, *, claim_id: str | None = None) -> list[dict[str, object]]:
    now = utc_now()
    swept: list[dict[str, object]] = []
    query = "SELECT * FROM claims WHERE state='active' AND expires_at<=?"
    args = (now,)
    if claim_id is not None:
        query += " AND id=?"
        args += (claim_id,)
    for claim in conn.execute(query + " ORDER BY expires_at", args).fetchall():
        roots = scopes_for(conn, claim["task_id"], "exclusive")
        baseline = json.loads(claim["baseline_manifest_json"] or "{}")
        current = scope_manifest(repo_root, roots)
        clean = baseline.get("fingerprint") == current.get("fingerprint")
        release_claim_locks(conn, claim["id"])
        live_resources: list[str] = []
        for lease in conn.execute("SELECT * FROM resource_leases WHERE claim_id=? AND state='active'", (claim["id"],)):
            locally_alive = lease["hostname"] == socket.gethostname() and local_process_alive(lease["pid"], lease["process_start"])
            if locally_alive:
                live_resources.append(lease["id"])
            else:
                conn.execute("UPDATE resource_leases SET state='released',released_at=? WHERE id=?", (now, lease["id"]))
        if clean and not live_resources:
            conn.execute("UPDATE claims SET state='expired_clean',released_at=? WHERE id=?", (now, claim["id"]))
            conn.execute("UPDATE tasks SET status='planned',attention_reason=NULL,updated_at=? WHERE id=? AND status='in_progress'", (now, claim["task_id"]))
            state = "released_clean"
        else:
            reason = "owned paths changed after baseline" if not clean else "expired claim still has a demonstrably live local resource process"
            conn.execute("UPDATE claims SET state='orphaned',orphan_reason=? WHERE id=?", (reason, claim["id"]))
            conn.execute("UPDATE tasks SET status='attention_required',attention_reason=?,updated_at=? WHERE id=?", (reason, now, claim["task_id"]))
            state = "orphaned_dirty" if not clean else "orphaned_live_resource"
        swept.append({"claim_id": claim["id"], "task_id": claim["task_id"], "state": state, "current_manifest": current, "live_resource_leases": live_resources})
    return swept


def claim_best(
    conn: sqlite3.Connection,
    repo_root: Path,
    session_id: str,
    project_revision: int,
    lease_seconds: int,
    requested_task_id: str | None = None,
    *,
    reconcile_expired: bool = True,
    owner_system: str | None = None,
    owner_instance_id: str | None = None,
) -> tuple[dict[str, object], str]:
    if reconcile_expired:
        sweep_expired(conn, repo_root)
    candidates = ready_tasks(conn)
    if requested_task_id:
        candidates = [item for item in candidates if item["task_id"] == requested_task_id]
    if not candidates:
        raise TodoError("no_actionable_work", "No safe task is currently claimable", ExitCode.NO_ACTIONABLE_WORK)
    candidate = candidates[0]
    task_id = str(candidate["task_id"])
    claim_id = str(uuid.uuid4())
    raw_token = "toc_" + secrets.token_urlsafe(36)
    roots = scopes_for(conn, task_id, "exclusive")
    baseline = scope_manifest(repo_root, roots)
    now = utc_now()
    expires = _iso_after(lease_seconds)
    try:
        conn.execute(
            "INSERT INTO claims(id,task_id,session_id,token_hash,state,created_at,heartbeat_at,expires_at,baseline_head,baseline_manifest_json,baseline_revision,owner_system,owner_instance_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (claim_id, task_id, session_id, token_hash(raw_token), "active", now, now, expires, git_head(repo_root), json.dumps(baseline, sort_keys=True), project_revision, owner_system, owner_instance_id),
        )
    except sqlite3.IntegrityError as exc:
        raise TodoError("claim_contention", f"Task {task_id} was claimed concurrently", ExitCode.CONTENTION) from exc
    conn.execute("UPDATE tasks SET status='in_progress',updated_at=?,version=version+1,revision=? WHERE id=?", (now, project_revision, task_id))
    locks = acquire_task_locks(conn, task_id, claim_id, session_id, lease_seconds)
    resources = []
    for request in conn.execute(
        "SELECT * FROM resource_requests WHERE task_id=? AND phase='claim' AND required=1 ORDER BY selector,id",
        (task_id,),
    ):
        for _ in range(int(request["amount"])):
            lease, lease_token = acquire_resource(
                conn,
                selector=request["selector"],
                session_id=session_id,
                claim_id=claim_id,
                request_id=request["id"],
                lease_seconds=lease_seconds,
            )
            lease["lease_token"] = lease_token
            resources.append(lease)
    result = {
        "claim_id": claim_id,
        "task_id": task_id,
        "expires_at": expires,
        "baseline_head": git_head(repo_root),
        "baseline_manifest": baseline,
        "locks": locks,
        "resources": resources,
        "selection": candidate,
        "owner_system": owner_system,
        "owner_instance_id": owner_instance_id,
    }
    result["claim_fingerprint"] = claim_fingerprint({
        "id": claim_id, "task_id": task_id, "session_id": session_id,
        "token_hash": token_hash(raw_token), "state": "active", "created_at": now,
        "heartbeat_at": now, "expires_at": expires, "baseline_revision": project_revision,
        "owner_system": owner_system, "owner_instance_id": owner_instance_id,
    })
    return result, raw_token


def pulse_claim(conn: sqlite3.Connection, claim_token: str, lease_seconds: int) -> dict[str, object]:
    claim = authenticate_claim(conn, claim_token)
    return _pulse_claim_row(conn, claim, lease_seconds)


def renew_claim_for_session(conn: sqlite3.Connection, claim_id: str, session_id: str, lease_seconds: int) -> dict[str, object]:
    """Renew a kernel-authenticated active seat inside its resume transaction.

    Unlike owner recovery, resumption cannot revive an expired or foreign claim.
    The caller has resolved the first-class session and its active dispatch.
    """
    claim = conn.execute("SELECT * FROM claims WHERE id=? AND session_id=? AND state='active' AND expires_at>?",
                         (claim_id, session_id, utc_now())).fetchone()
    if claim is None:
        raise TodoError("claim_not_resumable", "Session does not own an unexpired active claim", ExitCode.INVALID_TOKEN)
    return _pulse_claim_row(conn, claim, lease_seconds)


def _pulse_claim_row(conn: sqlite3.Connection, claim: sqlite3.Row, lease_seconds: int) -> dict[str, object]:
    now = utc_now()
    expires = _iso_after(lease_seconds)
    conn.execute("UPDATE claims SET heartbeat_at=?,expires_at=? WHERE id=?", (now, expires, claim["id"]))
    conn.execute("UPDATE lock_leases SET heartbeat_at=?,expires_at=? WHERE claim_id=? AND state='active'", (now, expires, claim["id"]))
    conn.execute("UPDATE resource_leases SET heartbeat_at=?,expires_at=? WHERE claim_id=? AND state='active'", (now, expires, claim["id"]))
    return {"claim_id": claim["id"], "task_id": claim["task_id"], "session_id": claim["session_id"], "expires_at": expires}


def release_claim(conn: sqlite3.Connection, claim_token: str, *, next_status: str = "in_progress", reason: str | None = None) -> dict[str, object]:
    claim = authenticate_claim(conn, claim_token)
    return release_claim_id(conn, str(claim["id"]), next_status=next_status, reason=reason)


def release_claim_id(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    next_status: str = "in_progress",
    reason: str | None = None,
) -> dict[str, object]:
    """Release a claim already authorized by an in-process workflow capability."""
    claim = conn.execute("SELECT * FROM claims WHERE id=? AND state='active'", (claim_id,)).fetchone()
    if not claim:
        raise TodoError("invalid_claim_authority", "Authorized claim is no longer active", ExitCode.INVALID_TOKEN)
    now = utc_now()
    release_claim_locks(conn, claim["id"])
    conn.execute("UPDATE resource_leases SET state='released',released_at=? WHERE claim_id=? AND state='active'", (now, claim["id"]))
    conn.execute("UPDATE claims SET state='released',released_at=? WHERE id=?", (now, claim["id"]))
    conn.execute("UPDATE tasks SET status=?,attention_reason=?,updated_at=?,version=version+1 WHERE id=?", (next_status, reason, now, claim["task_id"]))
    return {"claim_id": claim["id"], "task_id": claim["task_id"], "session_id": claim["session_id"], "status": next_status}


def inspect_recovery(conn: sqlite3.Connection, repo_root: Path, task_id: str) -> dict[str, object]:
    claim = conn.execute("SELECT * FROM claims WHERE task_id=? AND state='orphaned' ORDER BY created_at DESC LIMIT 1", (task_id,)).fetchone()
    if not claim:
        raise TodoError("no_orphaned_claim", f"Task {task_id} has no orphaned claim")
    roots = scopes_for(conn, task_id, "exclusive")
    baseline = json.loads(claim["baseline_manifest_json"] or "{}")
    current = scope_manifest(repo_root, roots)
    return {"task_id": task_id, "claim_id": claim["id"], "baseline": baseline, "current": current, "clean": baseline.get("fingerprint") == current.get("fingerprint")}


def recover_release(conn: sqlite3.Connection, repo_root: Path, task_id: str, acknowledge_dirty: bool = False) -> dict[str, object]:
    report = inspect_recovery(conn, repo_root, task_id)
    if not report["clean"] and not acknowledge_dirty:
        raise TodoError("dirty_orphan_quarantined", "Owned paths changed; inspect or adopt before release", ExitCode.CONSISTENCY_ERROR, report)
    conn.execute("UPDATE claims SET state='recovered_released',released_at=? WHERE id=?", (utc_now(), report["claim_id"]))
    conn.execute("UPDATE tasks SET status='planned',attention_reason=NULL,updated_at=? WHERE id=?", (utc_now(), task_id))
    return report


def recover_adopt(conn: sqlite3.Connection, repo_root: Path, task_id: str, session_id: str, revision: int, lease_seconds: int) -> tuple[dict[str, object], str]:
    report = inspect_recovery(conn, repo_root, task_id)
    conn.execute("UPDATE claims SET state='adopted',released_at=? WHERE id=?", (utc_now(), report["claim_id"]))
    conn.execute("UPDATE tasks SET status='planned',attention_reason=NULL WHERE id=?", (task_id,))
    claim, token = claim_best(conn, repo_root, session_id, revision, lease_seconds, requested_task_id=task_id)
    claim["adopted_from"] = report["claim_id"]
    return claim, token


def _live_override_blockers(conn: sqlite3.Connection, repo_root: Path, claim: sqlite3.Row) -> list[str]:
    claim_id = str(claim["id"])
    task_id = str(claim["task_id"])
    blockers: set[str] = set()
    active_children = conn.execute(
        "SELECT 1 FROM child_executions WHERE parent_claim_id=? "
        "AND state IN ('authorized','running','recovery_required','ready_for_acceptance','succeeded') LIMIT 1",
        (claim_id,),
    ).fetchone()
    if active_children:
        blockers.add("active_child_execution")
    if conn.execute(
        "SELECT 1 FROM child_attempts a JOIN child_executions c ON c.id=a.child_execution_id "
        "WHERE c.parent_claim_id=? AND a.state='active' LIMIT 1",
        (claim_id,),
    ).fetchone():
        blockers.add("active_child_attempt")
    if conn.execute(
        "SELECT 1 FROM child_scope_leases l JOIN child_executions c ON c.id=l.child_execution_id "
        "WHERE c.parent_claim_id=? AND l.state='active' LIMIT 1",
        (claim_id,),
    ).fetchone():
        blockers.add("active_child_scope")
    if conn.execute(
        "SELECT 1 FROM resource_leases WHERE claim_id=? AND state='active' LIMIT 1",
        (claim_id,),
    ).fetchone():
        blockers.add("active_resource_lease")
    if conn.execute(
        "SELECT 1 FROM lock_leases l LEFT JOIN task_locks t "
        "ON t.task_id=? AND t.lock_name=l.lock_name AND t.phase='claim' "
        "WHERE l.claim_id=? AND l.state='active' "
        "AND (t.lock_name IS NULL OR (l.command_json IS NOT NULL AND l.command_json!='[]')) LIMIT 1",
        (task_id, claim_id),
    ).fetchone():
        blockers.add("active_auxiliary_lock")
    if conn.execute(
        "SELECT 1 FROM gates g JOIN events e ON e.entity_type='gate' AND e.entity_id=g.id "
        "WHERE g.task_id=? AND e.revision=(SELECT MAX(e2.revision) FROM events e2 "
        "WHERE e2.entity_type='gate' AND e2.entity_id=g.id) "
        "AND e.event_type IN ('gate.started','gate.heartbeat') LIMIT 1",
        (task_id,),
    ).fetchone():
        blockers.add("active_gate_execution")
    try:
        from .background.store import BackgroundStore, runtime_paths

        database = runtime_paths(repo_root).database
        if database.exists():
            store = BackgroundStore(repo_root, create=False)
            background = store.connect(readonly=True)
            try:
                if background.execute(
                    "SELECT 1 FROM background_jobs WHERE task_id=? "
                    "AND state IN ('queued','running','preempted') LIMIT 1",
                    (task_id,),
                ).fetchone():
                    blockers.add("active_background_or_cuda_campaign")
            finally:
                background.close()
    except (OSError, sqlite3.Error):
        blockers.add("background_state_unverifiable")
    return sorted(blockers)


def _force_release_blockers(conn: sqlite3.Connection, repo_root: Path, claim: sqlite3.Row) -> list[str]:
    """Return activity that cannot be safely retired with claim bookkeeping."""
    claim_id = str(claim["id"])
    blockers = set(_live_override_blockers(conn, repo_root, claim))
    blockers.discard("claim_owner_not_verifiable_facade")
    blockers.discard("active_resource_lease")
    blockers.discard("active_auxiliary_lock")

    for table in ("resource_leases", "lock_leases"):
        for lease in conn.execute(
            f"SELECT * FROM {table} WHERE claim_id=? AND state='active'",
            (claim_id,),
        ):
            command = json.loads(lease["command_json"] or "[]")
            locally_running = (
                bool(command)
                and lease["hostname"] == socket.gethostname()
                and local_process_alive(lease["pid"], lease["process_start"])
            )
            if locally_running:
                blockers.add("active_external_resource_process")
    return sorted(blockers)


def _material_scope_manifest(manifest: dict[str, object]) -> dict[str, object]:
    """Exclude projections that owner-recovery transactions refresh themselves."""
    def generated(path: str) -> bool:
        return path in {
            ".todo-orchestrator/state.snapshot.json", "todo-status.md", "todos.md",
        } or path.startswith("todos/")

    payload = {
        "roots": list(manifest.get("roots") or []),
        "dirty_paths": [
            path for path in manifest.get("dirty_paths") or [] if not generated(str(path))
        ],
        "files": {
            path: digest for path, digest in dict(manifest.get("files") or {}).items()
            if not generated(str(path))
        },
    }
    payload["fingerprint"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
    return payload


def inspect_live_override(
    conn: sqlite3.Connection,
    repo_root: Path,
    project_uuid: str,
    task_id: str,
    project_revision: int,
) -> dict[str, object]:
    claims = conn.execute(
        "SELECT * FROM claims WHERE task_id=? AND state='active' ORDER BY created_at",
        (task_id,),
    ).fetchall()
    if len(claims) != 1:
        raise TodoError(
            "live_claim_cardinality_invalid",
            "Manual live recovery requires exactly one active claim",
            ExitCode.BLOCKED,
            {"active_claim_count": len(claims)},
        )
    claim = claims[0]
    blockers = _live_override_blockers(conn, repo_root, claim)
    if claim["expires_at"] <= utc_now():
        blockers.append("claim_lease_not_live")
    if not compatible_workflow_owner(claim["owner_system"]) or not claim["owner_instance_id"]:
        blockers.append("claim_owner_not_verifiable_facade")
    return {
        "repo_root": str(repo_root.resolve()),
        "project_uuid": project_uuid,
        "project_revision": project_revision,
        "task_id": task_id,
        "claim_fingerprint": claim_fingerprint(claim),
        "owner_system": claim["owner_system"],
        "prior_instance_id": claim["owner_instance_id"],
        "eligible": not blockers,
        "blockers": sorted(set(blockers)),
    }


def inspect_force_release(
    conn: sqlite3.Connection,
    repo_root: Path,
    project_uuid: str,
    task_id: str,
    project_revision: int,
    acknowledge_dirty: bool = False,
) -> dict[str, object]:
    claims = conn.execute(
        "SELECT * FROM claims WHERE task_id=? AND state='active' ORDER BY created_at",
        (task_id,),
    ).fetchall()
    if len(claims) != 1:
        raise TodoError(
            "live_claim_cardinality_invalid",
            "Owner force release requires exactly one active claim",
            ExitCode.BLOCKED,
            {"active_claim_count": len(claims)},
        )
    claim = claims[0]
    blockers = _force_release_blockers(conn, repo_root, claim)
    baseline = json.loads(claim["baseline_manifest_json"] or "{}")
    current = scope_manifest(repo_root, scopes_for(conn, task_id, "exclusive"))
    baseline_material = _material_scope_manifest(baseline)
    current_material = _material_scope_manifest(current)
    scope_changed = baseline.get("fingerprint") != current.get("fingerprint")
    if scope_changed and not acknowledge_dirty:
        blockers.append("owned_scope_changed")
    if claim["expires_at"] <= utc_now():
        blockers.append("claim_lease_not_live")
    return {
        "repo_root": str(repo_root.resolve()),
        "project_uuid": project_uuid,
        "project_revision": project_revision,
        "task_id": task_id,
        "claim_id": claim["id"],
        "claim_fingerprint": claim_fingerprint(claim),
        "owner_system": claim["owner_system"],
        "owner_instance_id": claim["owner_instance_id"],
        "lease_expires_at": claim["expires_at"],
        "scope_changed": scope_changed,
        "acknowledge_dirty": bool(acknowledge_dirty),
        "baseline_scope_fingerprint": baseline.get("fingerprint"),
        "current_scope_fingerprint": current.get("fingerprint"),
        "baseline_material_scope_fingerprint": baseline_material.get("fingerprint"),
        "current_material_scope_fingerprint": current_material.get("fingerprint"),
        "baseline_dirty_paths": list(baseline.get("dirty_paths") or []),
        "current_dirty_paths": list(current.get("dirty_paths") or []),
        "eligible": not blockers,
        "blockers": sorted(set(blockers)),
    }


def _approval_identity() -> tuple[int, str]:
    uid = os.getuid()
    try:
        username = pwd.getpwuid(uid).pw_name
    except KeyError:
        username = "unknown"
    return uid, f"uid:{uid}:{username}"


def _create_live_recovery_approval(
    conn: sqlite3.Connection,
    repo_root: Path,
    project_uuid: str,
    task_id: str,
    revision: int,
    reason: str,
    ttl_seconds: int,
    report: dict[str, object],
    *,
    token_prefix: str,
    approval_kind: str,
    context: dict[str, object] | None = None,
) -> tuple[dict[str, object], str]:
    bounded_reason = reason.strip()[:1000]
    if not bounded_reason:
        raise TodoError("recovery_reason_required", "Manual recovery requires an explicit reason", ExitCode.BLOCKED)
    ttl = max(30, min(int(ttl_seconds), 900))
    approval_id = str(uuid.uuid4())
    approval_token = token_prefix + secrets.token_urlsafe(36)
    uid, approver = _approval_identity()
    now = utc_now()
    expires = _iso_after(ttl)
    approval_context = context or {}
    conn.execute(
        "INSERT INTO live_recovery_approvals(id,token_hash,repo_root,project_uuid,task_id,claim_fingerprint,project_revision,requester_uid,approver_identity,reason,prior_instance_id,created_at,expires_at,state,approval_kind,context_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)",
        (
            approval_id, token_hash(approval_token), str(repo_root.resolve()), project_uuid,
            task_id, report["claim_fingerprint"], revision, uid, approver,
            bounded_reason, report.get("prior_instance_id") or report.get("owner_instance_id") or "",
            now, expires, approval_kind, json.dumps(approval_context, sort_keys=True),
        ),
    )
    return {
        "status": "approved", "task_id": task_id,
        "claim_fingerprint": report["claim_fingerprint"],
        "project_revision": revision, "requester_uid": uid,
        "approver_identity": approver, "reason": bounded_reason,
        "approval_kind": approval_kind, "approval_context": approval_context,
        "created_at": now, "expires_at": expires,
    }, approval_token


def approve_live_override(
    conn: sqlite3.Connection,
    repo_root: Path,
    project_uuid: str,
    task_id: str,
    revision: int,
    reason: str,
    ttl_seconds: int,
) -> tuple[dict[str, object], str]:
    report = inspect_live_override(conn, repo_root, project_uuid, task_id, revision)
    if not report["eligible"]:
        blockers = set(report.get("blockers") or [])
        message = (
            "Live claim is not owned by a compatible Project Control workflow facade and cannot be manually overridden"
            if "claim_owner_not_verifiable_facade" in blockers
            else "Live claim is not eligible for manual Project Control workflow recovery"
        )
        raise TodoError("live_override_blocked", message, ExitCode.BLOCKED, report)
    return _create_live_recovery_approval(
        conn, repo_root, project_uuid, task_id, revision, reason, ttl_seconds,
        report, token_prefix="toa_", approval_kind="live_claim_override",
    )


def approve_force_release(
    conn: sqlite3.Connection,
    repo_root: Path,
    project_uuid: str,
    task_id: str,
    revision: int,
    reason: str,
    ttl_seconds: int,
    acknowledge_dirty: bool = False,
) -> tuple[dict[str, object], str]:
    report = inspect_force_release(
        conn, repo_root, project_uuid, task_id, revision,
        acknowledge_dirty=acknowledge_dirty,
    )
    if not report["eligible"]:
        raise TodoError(
            "force_release_blocked",
            "Live claim is not eligible for owner force release",
            ExitCode.BLOCKED,
            report,
        )
    return _create_live_recovery_approval(
        conn, repo_root, project_uuid, task_id, revision, reason, ttl_seconds,
        report, token_prefix="tof_", approval_kind="live_claim_force_release",
        context={
            "acknowledge_dirty": bool(acknowledge_dirty),
            "scope_changed": bool(report["scope_changed"]),
            "baseline_scope_fingerprint": report["baseline_scope_fingerprint"],
            "current_scope_fingerprint": report["current_scope_fingerprint"],
            "baseline_material_scope_fingerprint": report["baseline_material_scope_fingerprint"],
            "current_material_scope_fingerprint": report["current_material_scope_fingerprint"],
            "baseline_dirty_paths": report["baseline_dirty_paths"],
            "current_dirty_paths": report["current_dirty_paths"],
        },
    )


def override_live_claim(
    conn: sqlite3.Connection,
    repo_root: Path,
    project_uuid: str,
    task_id: str,
    revision: int,
    approval_token: str,
    new_instance_id: str,
    lease_seconds: int,
) -> tuple[dict[str, object], dict[str, str]]:
    if not approval_token.startswith("toa_"):
        raise TodoError("override_requires_permission", "A valid manual approval is required", ExitCode.BLOCKED)
    approval = conn.execute(
        "SELECT * FROM live_recovery_approvals WHERE token_hash=?",
        (token_hash(approval_token),),
    ).fetchone()
    if not approval:
        raise TodoError("override_requires_permission", "A valid manual approval is required", ExitCode.BLOCKED)
    if approval["approval_kind"] != "live_claim_override":
        raise TodoError("approval_mismatch", "Manual recovery approval has the wrong purpose", ExitCode.BLOCKED)
    if approval["state"] != "pending":
        raise TodoError("approval_consumed", "Manual recovery approval was already consumed", ExitCode.CONTENTION)
    if approval["expires_at"] <= utc_now():
        raise TodoError("stale_approval", "Manual recovery approval expired", ExitCode.BLOCKED)
    expected = {
        "repo_root": str(repo_root.resolve()), "project_uuid": project_uuid,
        "task_id": task_id, "requester_uid": os.getuid(),
    }
    if any(approval[key] != value for key, value in expected.items()):
        raise TodoError("approval_mismatch", "Manual recovery approval does not match this request", ExitCode.BLOCKED)
    if int(approval["project_revision"]) != revision - 1:
        raise TodoError("stale_approval", "Project revision changed after manual approval", ExitCode.BLOCKED)
    report = inspect_live_override(conn, repo_root, project_uuid, task_id, revision - 1)
    if not report["eligible"]:
        raise TodoError("live_override_blocked", "Attached work blocks manual recovery", ExitCode.BLOCKED, report)
    if report["claim_fingerprint"] != approval["claim_fingerprint"]:
        raise TodoError("stale_approval", "Active claim changed after manual approval", ExitCode.BLOCKED)
    if report["prior_instance_id"] != approval["prior_instance_id"]:
        raise TodoError("approval_mismatch", "Facade owner instance does not match approval", ExitCode.BLOCKED)
    if not isinstance(new_instance_id, str) or not new_instance_id.startswith("fi_"):
        raise TodoError("invalid_facade_instance", "A fresh facade instance identity is required", ExitCode.BLOCKED)

    old_claim = conn.execute(
        "SELECT * FROM claims WHERE task_id=? AND state='active'",
        (task_id,),
    ).fetchone()
    session, session_token = create_session(
        conn,
        repo_root,
        {"command": "recover live-override", "owner_system": CANONICAL_WORKFLOW_OWNER, "owner_instance_id": new_instance_id},
    )
    now = utc_now()
    expires = _iso_after(lease_seconds)
    new_claim_id = str(uuid.uuid4())
    claim_token = "toc_" + secrets.token_urlsafe(36)
    conn.execute(
        "UPDATE claims SET state='overridden',released_at=? WHERE id=? AND state='active'",
        (now, old_claim["id"]),
    )
    conn.execute(
        "INSERT INTO claims(id,task_id,session_id,token_hash,state,created_at,heartbeat_at,expires_at,baseline_head,baseline_manifest_json,baseline_revision,owner_system,owner_instance_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            new_claim_id, task_id, session["agent_id"], token_hash(claim_token), "active",
            now, now, expires, old_claim["baseline_head"], old_claim["baseline_manifest_json"],
            old_claim["baseline_revision"], CANONICAL_WORKFLOW_OWNER, new_instance_id,
        ),
    )
    conn.execute(
        "UPDATE lock_leases SET claim_id=?,session_id=?,heartbeat_at=?,expires_at=? "
        "WHERE claim_id=? AND state='active'",
        (new_claim_id, session["agent_id"], now, expires, old_claim["id"]),
    )
    conn.execute(
        "UPDATE tasks SET status='in_progress',updated_at=?,version=version+1,revision=? WHERE id=?",
        (now, revision, task_id),
    )
    conn.execute(
        "UPDATE live_recovery_approvals SET state='consumed',consumed_at=? WHERE id=? AND state='pending'",
        (now, approval["id"]),
    )
    new_claim = conn.execute("SELECT * FROM claims WHERE id=?", (new_claim_id,)).fetchone()
    new_fingerprint = claim_fingerprint(new_claim)
    audit_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO live_recovery_audit(id,task_id,prior_claim_fingerprint,new_claim_fingerprint,approver_identity,requester_uid,reason,approved_at,consumed_at,prior_instance_id,new_instance_id,disposition,project_revision) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            audit_id, task_id, approval["claim_fingerprint"], new_fingerprint,
            approval["approver_identity"], approval["requester_uid"], approval["reason"],
            approval["created_at"], now, approval["prior_instance_id"], new_instance_id,
            "live_claim_replaced", revision,
        ),
    )
    locks = [dict(row) for row in conn.execute(
        "SELECT id AS lease_id,lock_name AS name,expires_at FROM lock_leases "
        "WHERE claim_id=? AND state='active' ORDER BY lock_name",
        (new_claim_id,),
    )]
    claim = {
        "claim_id": new_claim_id, "task_id": task_id, "expires_at": expires,
        "baseline_head": old_claim["baseline_head"],
        "baseline_manifest": json.loads(old_claim["baseline_manifest_json"] or "{}"),
        "locks": locks, "resources": [], "claim_fingerprint": new_fingerprint,
        "retired_claim_fingerprint": approval["claim_fingerprint"],
        "owner_system": CANONICAL_WORKFLOW_OWNER, "owner_instance_id": new_instance_id,
    }
    return claim, {"claim_token": claim_token, "session_token": session_token, "session": session}


def force_release_live_claim(
    conn: sqlite3.Connection,
    repo_root: Path,
    project_uuid: str,
    task_id: str,
    revision: int,
    approval_token: str,
) -> dict[str, object]:
    if not approval_token.startswith("tof_"):
        raise TodoError("force_release_requires_permission", "A valid owner force-release approval is required", ExitCode.BLOCKED)
    approval = conn.execute(
        "SELECT * FROM live_recovery_approvals WHERE token_hash=?",
        (token_hash(approval_token),),
    ).fetchone()
    if not approval:
        raise TodoError("force_release_requires_permission", "A valid owner force-release approval is required", ExitCode.BLOCKED)
    if approval["approval_kind"] != "live_claim_force_release":
        raise TodoError("approval_mismatch", "Owner approval has the wrong purpose", ExitCode.BLOCKED)
    if approval["state"] != "pending":
        raise TodoError("approval_consumed", "Owner force-release approval was already consumed", ExitCode.CONTENTION)
    if approval["expires_at"] <= utc_now():
        raise TodoError("stale_approval", "Owner force-release approval expired", ExitCode.BLOCKED)
    expected = {
        "repo_root": str(repo_root.resolve()), "project_uuid": project_uuid,
        "task_id": task_id, "requester_uid": os.getuid(),
    }
    if any(approval[key] != value for key, value in expected.items()):
        raise TodoError("approval_mismatch", "Owner force-release approval does not match this request", ExitCode.BLOCKED)
    if int(approval["project_revision"]) != revision - 1:
        raise TodoError("stale_approval", "Project revision changed after owner approval", ExitCode.BLOCKED)
    context = json.loads(approval["context_json"] or "{}")
    acknowledge_dirty = bool(context.get("acknowledge_dirty"))
    report = inspect_force_release(
        conn, repo_root, project_uuid, task_id, revision - 1,
        acknowledge_dirty=acknowledge_dirty,
    )
    if not report["eligible"]:
        raise TodoError("force_release_blocked", "Attached work or unacknowledged dirty scope blocks owner force release", ExitCode.BLOCKED, report)
    if report["claim_fingerprint"] != approval["claim_fingerprint"]:
        raise TodoError("stale_approval", "Active claim changed after owner approval", ExitCode.BLOCKED)
    if report["current_material_scope_fingerprint"] != context.get("current_material_scope_fingerprint"):
        raise TodoError("stale_approval", "Owned scope changed after owner approval", ExitCode.BLOCKED)

    claim = conn.execute(
        "SELECT * FROM claims WHERE task_id=? AND state='active'",
        (task_id,),
    ).fetchone()
    now = utc_now()
    dispatches = conn.execute(
        "SELECT id,lane_id FROM workflow_dispatches WHERE claim_id=? AND state='active' ORDER BY id",
        (claim["id"],),
    ).fetchall()
    release_claim_locks(conn, claim["id"])
    conn.execute(
        "UPDATE resource_leases SET state='released',released_at=? WHERE claim_id=? AND state='active'",
        (now, claim["id"]),
    )
    conn.execute(
        "UPDATE claims SET state='force_released',released_at=? WHERE id=? AND state='active'",
        (now, claim["id"]),
    )
    conn.execute(
        "UPDATE tasks SET status='planned',attention_reason=NULL,updated_at=?,version=version+1,revision=? WHERE id=?",
        (now, revision, task_id),
    )
    for dispatch in dispatches:
        conn.execute(
            "UPDATE workflow_dispatches SET state='released',released_at=?,revision=? WHERE id=? AND state='active'",
            (now, revision, dispatch["id"]),
        )
        conn.execute(
            "UPDATE workflow_lane_tasks SET state='queued',activated_at=NULL,revision=? "
            "WHERE lane_id=? AND task_id=? AND state='active'",
            (revision, dispatch["lane_id"], task_id),
        )
        conn.execute(
            "UPDATE workflow_lanes SET state='ready',updated_at=?,revision=? WHERE id=?",
            (now, revision, dispatch["lane_id"]),
        )
    conn.execute(
        "UPDATE workflow_capabilities SET state='retired',revoked_at=? WHERE claim_id=? AND state='active'",
        (now, claim["id"]),
    )
    conn.execute(
        "UPDATE live_recovery_approvals SET state='consumed',consumed_at=? WHERE id=? AND state='pending'",
        (now, approval["id"]),
    )
    conn.execute(
        "INSERT INTO live_recovery_audit(id,task_id,prior_claim_fingerprint,new_claim_fingerprint,approver_identity,requester_uid,reason,approved_at,consumed_at,prior_instance_id,new_instance_id,disposition,project_revision,context_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            str(uuid.uuid4()), task_id, approval["claim_fingerprint"], "",
            approval["approver_identity"], approval["requester_uid"], approval["reason"],
            approval["created_at"], now, approval["prior_instance_id"], "",
            "owner_force_released", revision, json.dumps(context, sort_keys=True),
        ),
    )
    return {
        "task_id": task_id,
        "claim_id": claim["id"],
        "session_id": claim["session_id"],
        "status": "planned",
        "claim_state": "force_released",
        "retired_dispatch_ids": [str(item["id"]) for item in dispatches],
        "retired_claim_fingerprint": approval["claim_fingerprint"],
        "acknowledged_dirty_scope": acknowledge_dirty and bool(report["scope_changed"]),
        "scope_context": context,
        "reason": approval["reason"],
    }
