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


def _state_dir(service: object, *, create: bool = True) -> Path:
    root = Path(getattr(getattr(service, "paths"), "state_dir")) / "project-control-recovery-authorizations"
    if create:
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


def _replace(path: Path, value: dict[str, Any]) -> None:
    """Durably replace a private record without exposing it through a tool."""
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        _write_new(temporary, value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def plan_fingerprint(plan: dict[str, Any]) -> str:
    """Fingerprint the exact bounded fresh inspection, never a mutable token."""
    return hashlib.sha256(_canonical(plan)).hexdigest()


def _authority_revision(service: object, plan: dict[str, Any]) -> int | None:
    """Use the kernel plan revision when supplied, otherwise its live DB revision."""
    revision = plan.get("authority_revision")
    if isinstance(revision, int):
        return revision
    database = getattr(service, "db", None)
    value = getattr(database, "revision", lambda: None)()
    return value if isinstance(value, int) else None


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
    revision = _authority_revision(service, plan)
    if revision is None:
        raise RecoveryAuthorizationError("recovery_authorization_revision_unavailable")
    root = _state_dir(service)
    authorization_id = "rca_" + secrets.token_urlsafe(18)
    payload = {
        "format": "project-control-recovery-authorization-v1",
        "authorization_id": authorization_id,
        "project_uuid": str(getattr(engine, "project_uuid")),
        "repository_root": str(Path(getattr(getattr(service, "paths"), "repo_root")).resolve()),
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


def issue_maintenance_recovery_authorization(
    service: object,
    engine: object,
    *,
    task_id: str,
    recipient_principal: str,
    expires_seconds: int = 300,
) -> dict[str, str | int]:
    """Issue an exact recovery mandate for one trusted startup-bound operator.

    This is intentionally separate from the legacy root bridge.  A v2 record
    carries an explicit recipient and keeps its completed receipt privately so
    a lost reply is safe to replay by that same recipient.
    """
    if not recipient_principal or len(recipient_principal) > 160:
        raise RecoveryAuthorizationError("recovery_authorization_invalid_principal")
    delegated_plan = engine.inspect(task_id)
    if not getattr(engine, "delegated_effects_are_exact")(delegated_plan, task_id):
        raise RecoveryAuthorizationError("recovery_authorization_unsafe_target")
    continuation = getattr(engine, "delegated_continuation")(delegated_plan)
    if not isinstance(continuation, dict) or not isinstance(continuation.get("run_id"), str):
        raise RecoveryAuthorizationError("recovery_authorization_unsafe_target")
    issued = issue_root_recovery_authorization(
        service, engine, task_id=task_id, expires_seconds=expires_seconds,
    )
    root = _state_dir(service, create=False)
    path = root / f"{issued['authorization_id']}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    payload = dict(record["payload"])
    payload.update({
        "format": "project-control-recovery-authorization-v2",
        "recipient_principal": recipient_principal,
        "continuation": continuation,
    })
    _replace(path, {
        "payload": payload,
        "signature": hmac.new(_key(root), _canonical(payload), hashlib.sha256).hexdigest(),
    })
    return issued


def _read_record(root: Path, authorization_id: str) -> tuple[Path, dict[str, Any], dict[str, Any], str]:
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
    return path, record, payload, signature


def inspect_maintenance_recovery_authorization(
    service: object, engine: object, *, authorization_id: str, recipient_principal: str,
) -> dict[str, Any]:
    """Read-only target/principal check before opening a writable authority."""
    if not authorization_id.startswith("rca_") or "/" in authorization_id or "\\" in authorization_id:
        raise RecoveryAuthorizationError("recovery_authorization_invalid")
    root = _state_dir(service, create=False)
    _, record, payload, _ = _read_record(root, authorization_id)
    if payload.get("format") != "project-control-recovery-authorization-v2":
        raise RecoveryAuthorizationError("recovery_authorization_legacy_only")
    if not recipient_principal or not hmac.compare_digest(str(payload.get("recipient_principal", "")), recipient_principal):
        raise RecoveryAuthorizationError("recovery_authorization_principal_mismatch")
    if payload.get("project_uuid") != str(getattr(engine, "project_uuid")):
        raise RecoveryAuthorizationError("recovery_authorization_project_mismatch")
    if payload.get("repository_root") != str(Path(getattr(getattr(service, "paths"), "repo_root")).resolve()):
        raise RecoveryAuthorizationError("recovery_authorization_target_mismatch")
    if record.get("revoked") is True:
        raise RecoveryAuthorizationError("recovery_authorization_revoked")
    completed = record.get("completed_receipt")
    if completed is not None:
        if not isinstance(completed, dict):
            raise RecoveryAuthorizationError("recovery_authorization_invalid")
        # The receipt is replayable only for this already-verified grant.  Keep
        # its signed target alongside it so callers can produce the same exact
        # ordinary resume recommendation after a lost response.
        return {"payload": payload, "completed_receipt": dict(completed)}
    try:
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RecoveryAuthorizationError("recovery_authorization_invalid") from exc
    if _now() >= expires_at:
        raise RecoveryAuthorizationError("recovery_authorization_expired")
    return {"payload": payload}


def run_authorized_recovery(
    service: object,
    engine: object,
    *,
    authorization_id: str,
    reason: str,
    recipient_principal: str | None = None,
) -> dict[str, Any]:
    """Execute one root-issued recovery without granting general recovery access."""
    if not authorization_id.startswith("rca_") or "/" in authorization_id or "\\" in authorization_id:
        raise RecoveryAuthorizationError("recovery_authorization_invalid")
    root = _state_dir(service, create=False)
    _, _, initial_payload, _ = _read_record(root, authorization_id)
    format_version = initial_payload.get("format")
    if format_version not in {
        "project-control-recovery-authorization-v1",
        "project-control-recovery-authorization-v2",
    }:
        raise RecoveryAuthorizationError("recovery_authorization_invalid")
    if format_version.endswith("v2") and recipient_principal is None:
        raise RecoveryAuthorizationError("recovery_authorization_principal_mismatch")
    if format_version.endswith("v1") and recipient_principal is not None:
        raise RecoveryAuthorizationError("recovery_authorization_legacy_only")
    # Retain the exact same project-wide recovery exclusion used by interactive
    # owner recovery.  The kernel repeats inspection immediately before its
    # revisioned transaction, so this helper never turns an old observation
    # into a broad bypass.
    from todo_orchestrator.workflow.recovery import project_recovery_lock

    database_path = Path(getattr(getattr(service, "paths"), "db_file"))
    with project_recovery_lock(database_path):
        path, record, payload, signature = _read_record(root, authorization_id)
        if format_version.endswith("v2"):
            # The signed target and principal remain mandatory even when a
            # prior DB commit outlived this private receipt projection.
            if (payload.get("project_uuid") != str(getattr(engine, "project_uuid"))
                    or payload.get("repository_root") != str(Path(getattr(getattr(service, "paths"), "repo_root")).resolve())):
                raise RecoveryAuthorizationError("recovery_authorization_target_mismatch")
            if not hmac.compare_digest(str(payload.get("recipient_principal", "")), str(recipient_principal)):
                raise RecoveryAuthorizationError("recovery_authorization_principal_mismatch")
            if record.get("revoked") is True:
                raise RecoveryAuthorizationError("recovery_authorization_revoked")
            canonical_result = getattr(engine, "recovery_result_for_request")(authorization_id)
            if canonical_result is not None:
                if record.get("completed_receipt") != canonical_result:
                    _replace(path, {"payload": payload, "signature": signature, "completed_receipt": canonical_result})
                return canonical_result
            checked = inspect_maintenance_recovery_authorization(
                service, engine, authorization_id=authorization_id, recipient_principal=str(recipient_principal),
            )
            if "completed_receipt" in checked:
                return dict(checked["completed_receipt"])
        else:
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
        fresh = engine.inspect(task_id)
        if fresh.get("blockers") or _authority_revision(service, fresh) != payload.get("authority_revision") or plan_fingerprint(fresh) != payload.get("plan_fingerprint"):
            raise RecoveryAuthorizationError("recovery_authorization_stale")
        # Consume only after success, preserving retryability on contention
        # while preventing a second successful execution.
        result = engine.execute(
            fresh,
            reason,
            recovery_request_id=authorization_id if format_version.endswith("v2") else None,
            delegated_task_id=task_id if format_version.endswith("v2") else None,
            recovery_continuation=payload.get("continuation") if format_version.endswith("v2") else None,
        )
        if format_version.endswith("v2"):
            _replace(path, {"payload": payload, "signature": signature, "completed_receipt": dict(result)})
        else:
            path.unlink(missing_ok=True)
    return result


def revoke_maintenance_recovery_authorization(service: object, *, authorization_id: str) -> None:
    """Host lifecycle revocation for an unconsumed v2 mandate."""
    if not authorization_id.startswith("rca_") or "/" in authorization_id or "\\" in authorization_id:
        raise RecoveryAuthorizationError("recovery_authorization_invalid")
    root = _state_dir(service, create=False)
    from todo_orchestrator.workflow.recovery import project_recovery_lock
    with project_recovery_lock(Path(getattr(getattr(service, "paths"), "db_file"))):
        path, record, payload, _ = _read_record(root, authorization_id)
        if payload.get("format") != "project-control-recovery-authorization-v2":
            raise RecoveryAuthorizationError("recovery_authorization_legacy_only")
        if record.get("completed_receipt") is not None:
            raise RecoveryAuthorizationError("recovery_authorization_completed")
        _replace(path, {**record, "revoked": True})
