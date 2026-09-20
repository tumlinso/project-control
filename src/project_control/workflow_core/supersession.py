"""Opaque, principal-bound execution of one prepared run supersession."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .recovery import RecoveryAuthorizationError, _canonical, _key, _now, _read_record, _replace, _state_dir, _write_new
from .retirement import RetirementRequest, retire_run_batch


def _check_id(authorization_id: str) -> None:
    if not authorization_id.startswith("ssa_") or "/" in authorization_id or "\\" in authorization_id:
        raise RecoveryAuthorizationError("supersession_authorization_invalid")


def issue_supersession_authorization(
    service: object, *, request: RetirementRequest | dict[str, Any], recipient_principal: str, expires_seconds: int = 1800,
) -> dict[str, str]:
    """Store the owner-prepared canonical request behind an opaque identifier."""
    if not recipient_principal or len(recipient_principal) > 160 or not 1 <= expires_seconds <= 86400:
        raise RecoveryAuthorizationError("supersession_authorization_invalid")
    payload_request = (request if isinstance(request, RetirementRequest) else RetirementRequest.model_validate(request)).model_dump(mode="json")
    if payload_request["expected_project_uuid"] != str(getattr(service, "project")["project_uuid"]):
        raise RecoveryAuthorizationError("supersession_authorization_project_mismatch")
    import secrets
    authorization_id = "ssa_" + secrets.token_urlsafe(18)
    payload = {
        "format": "project-control-supersession-authorization-v1", "operation": "supersede_run",
        "authorization_id": authorization_id, "project_uuid": payload_request["expected_project_uuid"],
        "repository_root": str(Path(getattr(getattr(service, "paths"), "repo_root")).resolve()),
        "recipient_principal": recipient_principal, "request": payload_request,
        "request_sha256": hashlib.sha256(_canonical(payload_request)).hexdigest(),
        "issued_at": _now().isoformat(), "expires_at": (_now() + timedelta(seconds=expires_seconds)).isoformat(),
    }
    root = _state_dir(service)
    _write_new(root / f"{authorization_id}.json", {"payload": payload, "signature": hmac.new(_key(root), _canonical(payload), hashlib.sha256).hexdigest()})
    return {"authorization_id": authorization_id, "expires_at": payload["expires_at"]}


def inspect_supersession_authorization(service: object, *, authorization_id: str, recipient_principal: str) -> dict[str, Any]:
    _check_id(authorization_id)
    root = _state_dir(service, create=False)
    _, record, payload, _ = _read_record(root, authorization_id)
    if payload.get("format") != "project-control-supersession-authorization-v1" or payload.get("operation") != "supersede_run":
        raise RecoveryAuthorizationError("supersession_authorization_invalid")
    if not hmac.compare_digest(str(payload.get("recipient_principal", "")), recipient_principal):
        raise RecoveryAuthorizationError("supersession_authorization_principal_mismatch")
    if (payload.get("project_uuid") != str(getattr(service, "project")["project_uuid"]) or
            payload.get("repository_root") != str(Path(getattr(getattr(service, "paths"), "repo_root")).resolve())):
        raise RecoveryAuthorizationError("supersession_authorization_target_mismatch")
    if record.get("revoked"):
        raise RecoveryAuthorizationError("supersession_authorization_revoked")
    if isinstance(record.get("completed_receipt"), dict):
        return {"payload": payload, "completed_receipt": dict(record["completed_receipt"])}
    try:
        if _now() >= datetime.fromisoformat(str(payload["expires_at"])):
            raise RecoveryAuthorizationError("supersession_authorization_expired")
    except ValueError as exc:
        raise RecoveryAuthorizationError("supersession_authorization_invalid") from exc
    return {"payload": payload}


def run_authorized_supersession(service: object, *, authorization_id: str, recipient_principal: str) -> dict[str, Any]:
    """Apply exactly the signed request through the canonical kernel receipt path."""
    _check_id(authorization_id)
    from todo_orchestrator.workflow.recovery import project_recovery_lock
    root = _state_dir(service, create=False)
    with project_recovery_lock(Path(getattr(getattr(service, "paths"), "db_file"))):
        checked = inspect_supersession_authorization(service, authorization_id=authorization_id, recipient_principal=recipient_principal)
        if "completed_receipt" in checked:
            return dict(checked["completed_receipt"])
        path, _, payload, signature = _read_record(root, authorization_id)
        receipt = retire_run_batch(service, RetirementRequest.model_validate(payload["request"]))
        _replace(path, {"payload": payload, "signature": signature, "completed_receipt": receipt})
        return receipt
