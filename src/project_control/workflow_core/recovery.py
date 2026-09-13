"""One-use, root-issued authorization for a narrowly delegated recovery.

The authorization is a private on-host record.  The delegated executor receives
only its opaque identifier; the original owner capability and any signing
material are never returned through the model-facing protocol.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class RecoveryAuthorizationError(ValueError):
    pass


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state_dir(service: object) -> Path:
    root = Path(getattr(getattr(service, "paths"), "state_dir")) / "project-control-recovery-authorizations"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root


def _key(root: Path) -> bytes:
    path = root / ".signing-key"
    if path.exists():
        return path.read_bytes()
    key = secrets.token_bytes(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, key)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return key


def _write_new(path: Path, value: dict[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, _canonical(value))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def plan_fingerprint(plan: dict[str, Any]) -> str:
    """Fingerprint the exact bounded fresh inspection, never a mutable token."""
    return hashlib.sha256(_canonical(plan)).hexdigest()


def issue_root_recovery_authorization(
    service: object,
    engine: object,
    *,
    task_id: str,
    expires_seconds: int = 300,
) -> dict[str, str | int]:
    """Issue one opaque, exact-target authorization after a fresh safe inspect.

    Callers are expected to be root/parallel-head lifecycle code.  The record
    binds the observed UUID, revision and full plan fingerprint; execution
    repeats the inspection under the canonical recovery lock.
    """
    # This is deliberately an internal root/head lifecycle call. It has no
    # CLI/MCP issuer and accepts no caller-declared role or lineage. Same-user
    # arbitrary Python is outside the model-facing authority boundary; the
    # executor receives only this opaque exact token.
    if not task_id or expires_seconds < 1 or expires_seconds > 900:
        raise RecoveryAuthorizationError("recovery_authorization_invalid")
    plan = engine.inspect(task_id)
    if plan.get("blockers") or plan.get("status") not in {"recovery_needed", "already_recovered"}:
        raise RecoveryAuthorizationError("recovery_authorization_unsafe_target")
    revision = plan.get("authority_revision")
    if not isinstance(revision, int):
        raise RecoveryAuthorizationError("recovery_authorization_revision_unavailable")
    root = _state_dir(service)
    authorization_id = "rca_" + secrets.token_urlsafe(18)
    payload = {
        "format": "project-control-recovery-authorization-v1",
        "authorization_id": authorization_id,
        "project_uuid": str(getattr(engine, "project_uuid")),
        "task_id": task_id,
        "authority_revision": revision,
        "plan_fingerprint": plan_fingerprint(plan),
        "issuer": "root_lifecycle_gateway",
        "issued_at": _now().isoformat(),
        "expires_at": (_now() + timedelta(seconds=expires_seconds)).isoformat(),
    }
    record = {"payload": payload, "signature": hmac.new(_key(root), _canonical(payload), hashlib.sha256).hexdigest()}
    _write_new(root / f"{authorization_id}.json", record)
    return {"authorization_id": authorization_id, "task_id": task_id, "expires_at": payload["expires_at"], "authority_revision": revision}


def run_authorized_recovery(service: object, engine: object, *, authorization_id: str, reason: str) -> dict[str, Any]:
    """Execute one root-issued recovery without granting general recovery access."""
    if not authorization_id.startswith("rca_") or "/" in authorization_id or "\\" in authorization_id:
        raise RecoveryAuthorizationError("recovery_authorization_invalid")
    root = _state_dir(service)
    path = root / f"{authorization_id}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        payload = record["payload"]
        signature = record["signature"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RecoveryAuthorizationError("recovery_authorization_not_found") from exc
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise RecoveryAuthorizationError("recovery_authorization_invalid")
    expected = hmac.new(_key(root), _canonical(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise RecoveryAuthorizationError("recovery_authorization_invalid")
    if payload.get("project_uuid") != str(getattr(engine, "project_uuid")):
        raise RecoveryAuthorizationError("recovery_authorization_project_mismatch")
    try:
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RecoveryAuthorizationError("recovery_authorization_invalid") from exc
    if _now() >= expires_at:
        raise RecoveryAuthorizationError("recovery_authorization_expired")
    task_id = payload.get("task_id")
    if not isinstance(task_id, str):
        raise RecoveryAuthorizationError("recovery_authorization_invalid")
    # Retain the exact same project-wide recovery exclusion used by interactive
    # owner recovery.  The kernel repeats inspection immediately before its
    # revisioned transaction, so this helper never turns an old observation
    # into a broad bypass.
    from todo_orchestrator.workflow.recovery import project_recovery_lock

    database_path = Path(getattr(getattr(service, "paths"), "db_file"))
    with project_recovery_lock(database_path):
        fresh = engine.inspect(task_id)
        if fresh.get("blockers") or fresh.get("authority_revision") != payload.get("authority_revision") or plan_fingerprint(fresh) != payload.get("plan_fingerprint"):
            raise RecoveryAuthorizationError("recovery_authorization_stale")
        # Consume only after success, preserving retryability on contention
        # while preventing a second successful execution.
        result = engine.execute(fresh, reason)
    path.unlink(missing_ok=True)
    return result
