"""Resource-aware gate execution with evidence capture and cleanup."""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .child_execution import authenticate_child_token, heartbeat_child_execution
from .config import utc_now
from .claims import pulse_claim
from .evidence import gate_input_fingerprint
from .git_state import integration_diff_args
from .git_state import canonical_relative, path_contains, paths_overlap
from .graph import reevaluate_barriers
from .models import ExitCode, TodoError
from .ownership import acquire_named_locks, release_lock, scopes_for
from .projections import atomic_write_text
from .resources import acquire_resource, release_resource, resource_environment
from .sessions import authenticate_claim


SUPPORTED_GATE_TYPES = frozenset({
    "command", "benchmark", "json_predicate", "file_exists", "pattern",
    "task_state", "checkpoint", "interface", "manual",
})
_COMMAND_GATE_TYPES = frozenset({"command", "benchmark", "json_predicate"})
_CUDA_FIELDS = frozenset({
    "gpus", "gpu_uuids", "cpu_threads", "isolate_pcie_root",
    "isolate_nvlink_domain", "toolchain", "build_argv", "binary_paths",
    "quiescence",
})


def validate_gate_spec(
    gate: object,
    repo_root: Path | None,
    *,
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    known_checkpoint_ids: set[str] | None = None,
    known_resources: set[str] | None = None,
) -> list[str]:
    """Validate the plan gate shape before either plan apply or claim binding."""
    if not isinstance(gate, dict) or not gate.get("id") or not gate.get("type"):
        return ["gate requires id and type"]
    gate_id = str(gate["id"])
    errors: list[str] = []
    gate_type = gate["type"]
    if not isinstance(gate_type, str) or gate_type not in SUPPORTED_GATE_TYPES:
        errors.append(f"gate {gate_id} has unsupported type {gate_type}")
        return errors
    if gate_type in _COMMAND_GATE_TYPES and (
        not isinstance(gate.get("argv"), list) or not gate.get("argv")
    ):
        errors.append(f"gate {gate_id} requires a non-empty argv array")
    if gate_type in {"file_exists", "pattern"} and not isinstance(gate.get("path"), str):
        errors.append(f"gate {gate_id} requires a path")
    if gate_type == "pattern":
        if not isinstance(gate.get("pattern"), str):
            errors.append(f"gate {gate_id} requires a pattern")
        else:
            try:
                re.compile(gate["pattern"])
            except re.error:
                errors.append(f"gate {gate_id} has an invalid pattern")
    for field, required_type in (
        ("task_id", "task_state"), ("checkpoint_id", "checkpoint"),
        ("interface_id", "interface"),
    ):
        if gate_type == required_type and not isinstance(gate.get(field), str):
            errors.append(f"gate {gate_id} requires {field}")
    if gate_type in {"benchmark", "json_predicate"}:
        if not isinstance(gate.get("threshold"), (int, float)) or isinstance(gate.get("threshold"), bool):
            errors.append(f"gate {gate_id} requires a numeric threshold")
        if gate.get("operator", ">=") not in {">=", ">", "<=", "<", "=="}:
            errors.append(f"gate {gate_id} has an unsupported operator")
    if gate.get("metric_file") is not None and not isinstance(gate.get("metric_file"), str):
        errors.append(f"gate {gate_id} metric_file must be a path string")
    for field in ("input_paths", "resources", "locks"):
        if field in gate and not isinstance(gate[field], list):
            errors.append(f"gate {gate_id} {field} must be an array")
    cuda = gate.get("cuda")
    if cuda is not None:
        if not isinstance(cuda, dict) or set(cuda) - _CUDA_FIELDS:
            errors.append(f"gate {gate_id} has unsupported CUDA configuration")
        else:
            if not isinstance(cuda.get("gpus", 1), int) or isinstance(cuda.get("gpus", 1), bool) or cuda.get("gpus", 1) < 1:
                errors.append(f"gate {gate_id} requires a positive CUDA GPU count")
            if "quiescence" in cuda:
                try:
                    from .cuda_gate import validate_quiescence
                    validate_quiescence(cuda["quiescence"])
                except ValueError as exc:
                    errors.append(f"gate {gate_id}: {exc}")
            if gate_type not in _COMMAND_GATE_TYPES or gate.get("expected_exit_code", 0) != 0 or gate.get("resources"):
                errors.append(f"gate {gate_id} CUDA execution requires a command, zero expected exit, and controller-owned resources")
    for field in ("cwd", "path", "metric_file"):
        if not gate.get(field):
            continue
        try:
            path = "." if field == "cwd" and gate[field] == "." else canonical_relative(repo_root, str(gate[field])) if repo_root else str(gate[field])
            if path == "." and allowed_paths is not None:
                if not gate.get("input_paths"):
                    raise TodoError("gate_cwd_unscoped", "Repository-root cwd requires explicit owned input paths")
            elif allowed_paths is not None and not any(path_contains(scope, path) for scope in allowed_paths):
                raise TodoError("gate_path_outside_claim", "Gate path is outside the active task scope")
            if path != "." and forbidden_paths and any(paths_overlap(path, forbidden) for forbidden in forbidden_paths):
                raise TodoError("gate_path_forbidden", "Gate path intersects a forbidden task scope")
        except Exception:
            errors.append(f"gate {gate_id} {field} has unsafe or unowned repository path")
    for value in gate.get("input_paths", []) if isinstance(gate.get("input_paths", []), list) else []:
        try:
            path = canonical_relative(repo_root, str(value)) if repo_root else str(value)
            if allowed_paths is not None and not any(path_contains(scope, path) for scope in allowed_paths):
                raise TodoError("gate_path_outside_claim", "Gate input is outside the active task scope")
            if forbidden_paths and any(paths_overlap(path, forbidden) for forbidden in forbidden_paths):
                raise TodoError("gate_path_forbidden", "Gate input intersects a forbidden task scope")
        except Exception:
            errors.append(f"gate {gate_id} input has unsafe or unowned repository path")
    if gate.get("checkpoint_id") and known_checkpoint_ids is not None and gate["checkpoint_id"] not in known_checkpoint_ids:
        errors.append(f"gate {gate_id} references unknown checkpoint {gate['checkpoint_id']}")
    for selector in gate.get("resources", []) if isinstance(gate.get("resources", []), list) else []:
        if known_resources is not None and str(selector) not in known_resources and not (str(selector).endswith(":any") and str(selector)[:-4] in known_resources):
            errors.append(f"gate {gate_id} references unknown resource selector {selector}")
    return errors


def bind_required_gates(db, repo_root: Path, claim_id: str, gates: list[object], *, actor_session_id: str) -> tuple[dict[str, object], int]:
    """Append exact task-owned required gates without rewriting a live plan."""
    def exact_existing(conn) -> dict[str, object] | None:
        claim = conn.execute("SELECT task_id,state FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not claim or claim["state"] != "active" or not gates:
            return None
        task_id = str(claim["task_id"])
        unchanged: list[str] = []
        for gate in gates:
            if not isinstance(gate, dict) or not gate.get("id") or gate.get("checkpoint_id") or gate.get("required", True) is not True:
                return None
            config = {key: value for key, value in gate.items() if key not in {"id", "type", "required", "checkpoint_id"}}
            existing = conn.execute("SELECT task_id,checkpoint_id,type,config_json,required FROM gates WHERE id=?", (str(gate["id"]),)).fetchone()
            if not existing or not (str(existing["task_id"]) == task_id and existing["checkpoint_id"] is None and existing["type"] == gate.get("type") and json.loads(existing["config_json"]) == config and bool(existing["required"]) == bool(gate.get("required", True))):
                return None
            unchanged.append(str(gate["id"]))
        return {"task_id": task_id, "bound_gate_ids": [], "unchanged_gate_ids": unchanged}

    with db.read() as conn:
        unchanged = exact_existing(conn)
    if unchanged is not None:
        return unchanged, db.revision()

    def operation(conn, revision):
        claim = conn.execute("SELECT task_id,state FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not claim or claim["state"] != "active":
            raise TodoError("invalid_claim_authority", "Gate binding requires an active claim")
        task_id = str(claim["task_id"])
        scopes = [*scopes_for(conn, task_id, "exclusive"), *scopes_for(conn, task_id, "read")]
        forbidden = scopes_for(conn, task_id, "forbidden")
        checkpoint_ids = {str(row[0]) for row in conn.execute("SELECT id FROM checkpoints WHERE task_id=?", (task_id,))}
        resource_ids = {str(row[0]) for row in conn.execute("SELECT id FROM resource_instances UNION SELECT id FROM resource_classes")}
        ids = [str(item.get("id", "")) for item in gates if isinstance(item, dict)]
        if not gates or len(ids) != len(gates) or not all(ids) or len(ids) != len(set(ids)):
            raise TodoError("invalid_gate_binding", "Gate binding requires unique complete gate specifications")
        if any(not isinstance(gate, dict) or gate.get("required", True) is not True for gate in gates):
            raise TodoError("invalid_gate_binding", "Live gate binding requires required=true")
        errors = [error for gate in gates for error in validate_gate_spec(gate, repo_root, allowed_paths=scopes, forbidden_paths=forbidden, known_checkpoint_ids=checkpoint_ids, known_resources=resource_ids)]
        if errors:
            raise TodoError("invalid_gate_binding", "Gate binding rejected invalid specifications", details={"errors": errors})
        bound, unchanged = [], []
        for gate in gates:
            assert isinstance(gate, dict)
            gate_id = str(gate["id"])
            config = {key: value for key, value in gate.items() if key not in {"id", "type", "required", "checkpoint_id"}}
            existing = conn.execute("SELECT task_id,checkpoint_id,type,config_json,required FROM gates WHERE id=?", (gate_id,)).fetchone()
            if existing:
                same = (str(existing["task_id"]) == task_id and existing["checkpoint_id"] is None and existing["type"] == gate["type"] and json.loads(existing["config_json"]) == config and bool(existing["required"]) == bool(gate.get("required", True)))
                if not same:
                    raise TodoError("gate_binding_conflict", "Existing gate cannot be replaced or weakened", details={"gate_id": gate_id})
                unchanged.append(gate_id)
                continue
            if gate.get("checkpoint_id"):
                raise TodoError("gate_binding_checkpoint_forbidden", "Live claim binding cannot alter checkpoint gates")
            conn.execute("INSERT INTO gates(id,task_id,checkpoint_id,type,config_json,required,status,valid,revision) VALUES(?,?,?,?,?,?, 'pending',0,?)", (gate_id, task_id, None, gate["type"], json.dumps(config, sort_keys=True), int(gate.get("required", True)), revision))
            bound.append(gate_id)
        return {"task_id": task_id, "bound_gate_ids": bound, "unchanged_gate_ids": unchanged}
    return db.mutate(actor_session_id=actor_session_id, entity_type="gate_binding", entity_id=claim_id, event_type="workflow.gates.bound", payload={"gate_count": len(gates)}, operation=operation)


def _child_candidate(conn, gate_id: str, fingerprint: str, target_child_id: str | None = None) -> dict[str, object] | None:
    """Return the newest successful, reported child candidate for current inputs."""
    rows = conn.execute(
        "SELECT e.* FROM evidence e WHERE e.gate_id=? AND e.status IN ('passed','evaluated_not_promoted') "
        "ORDER BY e.created_at DESC",
        (gate_id,),
    )
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            continue
        child_id = metadata.get("child_execution_id")
        if not child_id or (target_child_id is not None and child_id != target_child_id):
            continue
        child = conn.execute(
            "SELECT state,gates_json,candidate_gates_json FROM child_executions WHERE id=?",
            (child_id,),
        ).fetchone()
        if metadata.get("input_fingerprint") != fingerprint:
            if child and child["state"] == "ready_for_acceptance":
                now = utc_now()
                conn.execute("UPDATE child_executions SET state='stale',completed_at=? WHERE id=?", (now, child_id))
                conn.execute(
                    "UPDATE child_scope_leases SET state='released',released_at=? "
                    "WHERE child_execution_id=? AND state='active'",
                    (now, child_id),
                )
            continue
        allowed = json.loads(child["candidate_gates_json"] or child["gates_json"] or "[]") if child else []
        if not child or child["state"] not in {"succeeded", "ready_for_acceptance"} or gate_id not in allowed:
            continue
        accepted = False
        for acceptance in conn.execute("SELECT metadata_json FROM evidence WHERE gate_id=? ORDER BY created_at DESC", (gate_id,)):
            try:
                accepted_metadata = json.loads(acceptance["metadata_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if accepted_metadata.get("accepted_evidence_id") == row["id"]:
                accepted = True
                break
        if not accepted:
            return {"evidence": dict(row), "metadata": metadata, "child_execution_id": child_id}
    return None


def list_gates(conn, task_id: str | None = None) -> list[dict[str, object]]:
    if task_id:
        rows = conn.execute("SELECT * FROM gates WHERE task_id=? ORDER BY id", (task_id,))
    else:
        rows = conn.execute("SELECT * FROM gates ORDER BY id")
    result = []
    for row in rows:
        item = dict(row)
        item["config"] = json.loads(item.pop("config_json"))
        result.append(item)
    return result


def explain_gate(conn, gate_id: str) -> dict[str, object]:
    row = conn.execute("SELECT * FROM gates WHERE id=?", (gate_id,)).fetchone()
    if not row:
        raise TodoError("gate_not_found", f"Unknown gate {gate_id}")
    evidence = [dict(item) for item in conn.execute("SELECT * FROM evidence WHERE gate_id=? ORDER BY created_at DESC", (gate_id,))]
    item = dict(row)
    item["config"] = json.loads(item.pop("config_json"))
    item["evidence"] = evidence
    return item


def _json_value(value: object, path: str) -> object:
    current = value
    for part in path.split(".") if path else []:
        if isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(path)
    return current


def _compare(actual: float, operator: str, expected: float) -> bool:
    return {
        ">=": actual >= expected,
        ">": actual > expected,
        "<=": actual <= expected,
        "<": actual < expected,
        "==": actual == expected,
    }.get(operator, False)


def _evaluate_static(repo_root: Path, gate_type: str, config: dict[str, object], conn) -> tuple[str, bool, dict[str, object]]:
    if gate_type == "file_exists":
        exists = (repo_root / str(config["path"])).exists()
        return ("passed" if exists else "failed", exists, {"path": config["path"], "exists": exists})
    if gate_type == "pattern":
        path = repo_root / str(config["path"])
        found = bool(path.is_file() and re.search(str(config["pattern"]), path.read_text(encoding="utf-8"), re.MULTILINE))
        return ("passed" if found else "failed", found, {"path": str(config["path"]), "found": found})
    if gate_type == "task_state":
        row = conn.execute("SELECT status,result FROM tasks WHERE id=?", (config["task_id"],)).fetchone()
        ok = bool(row and row["status"] == config.get("status", "done") and (not config.get("result") or row["result"] == config["result"]))
        return ("passed" if ok else "failed", ok, dict(row) if row else {})
    if gate_type == "checkpoint":
        row = conn.execute("SELECT state FROM checkpoints WHERE id=?", (config["checkpoint_id"],)).fetchone()
        ok = bool(row and row[0] == config.get("state", "reached"))
        return ("passed" if ok else "failed", ok, {"state": row[0] if row else "missing"})
    if gate_type == "interface":
        row = conn.execute("SELECT state,version,content_hash FROM interfaces WHERE id=?", (config["interface_id"],)).fetchone()
        ok = bool(row and row["state"] == config.get("state", "frozen") and (not config.get("version") or row["version"] == config["version"]))
        return ("passed" if ok else "failed", ok, dict(row) if row else {})
    if gate_type == "manual":
        ok = bool(config.get("accepted"))
        return ("passed" if ok else "failed", ok, {"accepted": ok, "note": config.get("note")})
    raise TodoError("unsupported_gate_type", f"Gate type {gate_type} is not executable")


def _reusable_static_evidence(conn, gate, fingerprint: str, *, workspace_base_commit: str | None) -> dict[str, object] | None:
    """Reuse only deterministic static evidence with its complete input identity.

    Commands, GPU/resource gates, and manual acceptance deliberately execute
    again: their runtime observation lacks a complete reusable contract.
    """
    if workspace_base_commit is not None or gate["type"] not in {"file_exists", "pattern"}:
        return None
    config = json.loads(gate["config_json"])
    if config.get("cuda") is not None or config.get("resources") or config.get("locks"):
        return None
    if not gate["valid"] or gate["status"] != "passed" or gate["input_fingerprint"] != fingerprint:
        return None
    row = conn.execute(
        "SELECT id,status,metadata_json FROM evidence WHERE gate_id=? AND status='passed' ORDER BY revision DESC,id DESC LIMIT 1",
        (gate["id"],),
    ).fetchone()
    if not row:
        return None
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        return None
    if metadata.get("input_fingerprint") != fingerprint:
        return None
    return {"evidence_id": str(row["id"]), "status": "passed", "valid": True}


def run_gate(
    db, paths, project: dict[str, object], gate_id: str, claim_token: str | None,
    accept_child: str | None = None,
    *,
    authorized_claim_id: str | None = None,
    execution_root: Path | None = None,
    workspace_base_commit: str | None = None,
) -> tuple[dict[str, object], int]:
    configuration = project.get("configuration", {})
    resource_seconds = int(configuration.get("resource_lease_seconds", 300))
    claim_seconds = int(configuration.get("claim_lease_seconds", 7200))
    acquired: dict[str, object] = {}
    raw_tokens: dict[str, str] = {}
    if claim_token and authorized_claim_id:
        raise TodoError("ambiguous_gate_authority", "Gate execution accepts one claim authority")
    gate_root = Path(execution_root or paths.repo_root).resolve()
    if execution_root is not None and not workspace_base_commit:
        raise TodoError("workspace_gate_base_required", "Managed-workspace gates require the frozen workspace base commit")

    def acquire(conn, revision):
        gate = conn.execute("SELECT * FROM gates WHERE id=?", (gate_id,)).fetchone()
        if not gate:
            raise TodoError("gate_not_found", f"Unknown gate {gate_id}")
        config = json.loads(gate["config_json"])
        child = None
        if authorized_claim_id:
            claim = conn.execute("SELECT * FROM claims WHERE id=? AND state='active'", (authorized_claim_id,)).fetchone()
            if not claim:
                raise TodoError("invalid_claim_authority", "Workflow capability claim is no longer active", ExitCode.INVALID_TOKEN)
            if claim["task_id"] != gate["task_id"]:
                raise TodoError("claim_task_mismatch", "Gate does not belong to the capability claim", ExitCode.INVALID_TOKEN)
            claim_expires = (datetime.now(timezone.utc) + timedelta(seconds=claim_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            conn.execute(
                "UPDATE claims SET heartbeat_at=?,expires_at=? WHERE id=?",
                (utc_now(), claim_expires, authorized_claim_id),
            )
            child = None
        elif gate["task_id"] and str(claim_token or "").startswith("toch_"):
            attempt = authenticate_child_token(conn, claim_token)
            execution = conn.execute(
                "SELECT id,gates_json FROM child_executions WHERE id=?",
                (attempt["child_execution_id"],),
            ).fetchone()
            allowed_gates = json.loads(execution["gates_json"] or "[]") if execution else []
            if gate_id not in allowed_gates:
                raise TodoError(
                    "child_gate_unauthorized",
                    f"Child execution is not authorized to run gate {gate_id}",
                    ExitCode.BLOCKED,
                    {"allowed_gates": allowed_gates},
                )
            if attempt["task_id"] != gate["task_id"]:
                raise TodoError("child_gate_task_mismatch", "Child gate does not belong to the parent task", ExitCode.INVALID_TOKEN)
            claim = conn.execute("SELECT * FROM claims WHERE id=?", (attempt["parent_claim_id"],)).fetchone()
            lease_seconds = max(resource_seconds, int(float(config.get("timeout", 3600))) + 30)
            heartbeat_child_execution(conn, claim_token, lease_seconds=lease_seconds)
            child = {
                "child_execution_id": attempt["child_execution_id"],
                "attempt_id": attempt["id"],
                "attempt_number": attempt["attempt_number"],
                "lease_seconds": lease_seconds,
            }
        else:
            if gate["task_id"]:
                pulse_claim(conn, claim_token, claim_seconds)
            claim = authenticate_claim(conn, claim_token) if gate["task_id"] else None
        if claim and claim["task_id"] != gate["task_id"]:
            raise TodoError("claim_task_mismatch", "Gate does not belong to the claimed task", ExitCode.INVALID_TOKEN)
        session_id = claim["session_id"] if claim else config.get("session_id")
        if not session_id:
            raise TodoError("gate_session_required", "Gate execution requires an active claim")
        fingerprint, inputs = gate_input_fingerprint(conn, gate_root, config, gate_type=str(gate["type"]))
        # A child candidate or explicit acceptance has lineage and durable
        # acceptance effects.  Do not let static evidence reuse skip either.
        reused = None if child or accept_child is not None else _reusable_static_evidence(
            conn, gate, fingerprint, workspace_base_commit=workspace_base_commit,
        )
        if reused:
            acquired.update(gate=dict(gate), config=config, fingerprint=fingerprint, inputs=inputs, reused=reused)
            return {"gate_id": gate_id, "reused": True, **reused}
        workspace_source_identity = None
        if workspace_base_commit:
            source = subprocess.run(
                ["git", "-C", str(gate_root), *integration_diff_args(workspace_base_commit)],
                capture_output=True,
                check=False,
            )
            if source.returncode != 0:
                raise TodoError("workspace_gate_source_unavailable", "Cannot establish managed-workspace gate source identity")
            workspace_source_identity = hashlib.sha256(source.stdout).hexdigest()
        if accept_child and child:
            raise TodoError("child_acceptance_token_invalid", "Child acceptance requires the parent claim token", ExitCode.INVALID_TOKEN)
        if accept_child:
            selected = conn.execute(
                "SELECT parent_claim_id,task_id,state,gates_json,candidate_gates_json FROM child_executions WHERE id=?",
                (accept_child,),
            ).fetchone()
            if not selected:
                raise TodoError("unknown_child_execution", f"Child execution {accept_child} does not exist", ExitCode.BLOCKED)
            if selected["parent_claim_id"] != claim["id"]:
                raise TodoError("child_parent_mismatch", "Child execution is not owned by this claim", ExitCode.INVALID_TOKEN)
            if selected["task_id"] != gate["task_id"]:
                raise TodoError("child_gate_task_mismatch", "Child gate does not belong to the parent task", ExitCode.INVALID_TOKEN)
            if selected["state"] != "ready_for_acceptance":
                raise TodoError("child_not_ready", "Child result is not ready for acceptance", ExitCode.BLOCKED)
            allowed = json.loads(selected["candidate_gates_json"] or selected["gates_json"] or "[]")
            if gate_id not in allowed:
                raise TodoError("child_gate_unauthorized", f"Child execution is not authorized for gate {gate_id}", ExitCode.BLOCKED)
        accept_candidate = _child_candidate(conn, gate_id, fingerprint, accept_child) if claim and not child else None
        if accept_child and not accept_candidate:
            raise TodoError(
                "child_gate_evidence_unavailable",
                "The named child has no valid gate evidence for the current source",
                ExitCode.BLOCKED,
            )
        resources = []
        selectors = sorted(str(item) for item in config.get("resources", []))
        argv = [str(item) for item in config.get("argv", [])]
        for selector in [] if accept_candidate else selectors:
            lease, token = acquire_resource(
                conn,
                selector=selector,
                session_id=session_id,
                claim_id=claim["id"] if claim else None,
                request_id=None,
                lease_seconds=resource_seconds,
                command=argv,
            )
            resources.append(lease)
            raw_tokens[f"resource:{lease['lease_id']}"] = token
        locks = acquire_named_locks(
            conn,
            [] if accept_candidate else [str(item) for item in config.get("locks", [])],
            claim_id=claim["id"] if claim else None,
            session_id=session_id,
            lease_seconds=resource_seconds,
            command=argv,
        )
        for lock in locks:
            raw_tokens[f"lock:{lock['lease_id']}"] = lock["token"]
        acquired.update(
            gate=dict(gate), config=config, claim=dict(claim) if claim else None, child=child,
            accept_candidate=accept_candidate, resources=resources, locks=locks,
            fingerprint=fingerprint, inputs=inputs,
            explicit_accept_child=accept_child,
            workspace_path=str(gate_root) if execution_root is not None else None,
            workspace_source_identity=workspace_source_identity,
        )
        return {"gate_id": gate_id, "resources": resources, "locks": [{k: v for k, v in item.items() if k != "token"} for item in locks]}

    _, acquire_revision = db.mutate(
        actor_session_id=None,
        entity_type="gate",
        entity_id=gate_id,
        event_type="gate.started",
        payload={"gate_id": gate_id},
        operation=acquire,
    )
    if acquired.get("reused"):
        return {
            "gate_id": gate_id,
            "status": "passed",
            "valid": True,
            "evidence_id": acquired["reused"]["evidence_id"],
            "reused": True,
        }, acquire_revision

    stop = threading.Event()

    def heartbeat() -> None:
        if stop.wait(max(1.0, resource_seconds / 3.0)):
            return
        while not stop.is_set():
            def refresh(conn, revision):
                del revision
                now = utc_now()
                expires = (datetime.now(timezone.utc) + timedelta(seconds=resource_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                for lease in acquired.get("resources", []):
                    conn.execute("UPDATE resource_leases SET heartbeat_at=?,expires_at=? WHERE id=? AND state='active'", (now, expires, lease["lease_id"]))
                for lease in acquired.get("locks", []):
                    conn.execute("UPDATE lock_leases SET heartbeat_at=?,expires_at=? WHERE id=? AND state='active'", (now, expires, lease["lease_id"]))
                child = acquired.get("child")
                if child:
                    child_expires = (datetime.now(timezone.utc) + timedelta(seconds=int(child["lease_seconds"]))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    conn.execute("UPDATE child_attempts SET heartbeat_at=?,expires_at=? WHERE id=? AND state='active'", (now, child_expires, child["attempt_id"]))
                    conn.execute("UPDATE child_executions SET heartbeat_at=? WHERE id=?", (now, child["child_execution_id"]))
                return True
            try:
                db.mutate(actor_session_id=None, entity_type="gate", entity_id=gate_id, event_type="gate.heartbeat", payload={}, operation=refresh)
            except Exception:
                pass
            stop.wait(max(1.0, resource_seconds / 3.0))

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    gate = acquired["gate"]
    config = acquired["config"]
    gate_type = gate["type"]
    stdout = ""
    stderr = ""
    returncode: int | None = None
    status = "failed"
    valid = False
    details: dict[str, Any] = {}
    try:
        if acquired.get("accept_candidate"):
            candidate = acquired["accept_candidate"]
            status, valid = "passed", True
            details = {
                "accepted_child_evidence": True,
                "accepted_evidence_id": candidate["evidence"]["id"],
                "accepted_child_execution_id": candidate["child_execution_id"],
                "input_fingerprint": acquired["fingerprint"],
            }
        elif gate_type in {"command", "benchmark", "json_predicate"}:
            argv = config.get("argv")
            if not isinstance(argv, list) or not argv:
                raise TodoError("invalid_gate_command", "Command gates require a non-empty argv array")
            environment = os.environ.copy()
            environment.update({str(k): str(v) for k, v in config.get("env", {}).items()})
            environment.update(resource_environment(acquired["resources"]))
            if config.get("cuda") is not None:
                from .cuda_gate import run as run_cuda_gate
                result = run_cuda_gate([str(item) for item in argv],
                    gate_root / str(config.get("cwd", ".")), environment, config["cuda"], float(config.get("timeout", 3600)))
            else:
                result = subprocess.run(
                    [str(item) for item in argv],
                    cwd=gate_root / str(config.get("cwd", ".")),
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=float(config.get("timeout", 3600)),
                    check=False,
                )
            stdout, stderr, returncode = result.stdout, result.stderr, result.returncode
            expected = int(config.get("expected_exit_code", 0))
            executed = returncode == expected
            status = "passed" if executed else "failed"
            valid = executed
            details = {"argv": argv, "returncode": returncode, "expected_exit_code": expected, "environment": resource_environment(acquired["resources"])}
            if executed and gate_type in {"benchmark", "json_predicate"}:
                source: object
                metric_file = config.get("metric_file")
                source = json.loads((gate_root / str(metric_file)).read_text(encoding="utf-8")) if metric_file else json.loads(stdout)
                actual = _json_value(source, str(config.get("metric_path", "")))
                passed = _compare(float(actual), str(config.get("operator", ">=")), float(config["threshold"]))
                details.update(actual=actual, threshold=config["threshold"], operator=config.get("operator", ">="))
                if passed:
                    status, valid = "passed", True
                elif gate_type == "benchmark" and bool(config.get("evaluation_required", True)):
                    status, valid = "evaluated_not_promoted", True
                else:
                    status, valid = "failed", False
        else:
            with db.read() as read_conn:
                status, valid, details = _evaluate_static(gate_root, gate_type, config, read_conn)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        status, valid, details = "failed", False, {"timeout": config.get("timeout", 3600)}
    except KeyboardInterrupt:
        status, valid, details = "failed", False, {"interrupted": True}
    except Exception as exc:
        status, valid, details = "failed", False, {"error": str(exc)}
    finally:
        stop.set()
        thread.join(timeout=2)

    evidence_id = str(uuid.uuid4())
    evidence_dir = paths.evidence_dir / evidence_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(evidence_dir / "stdout.txt", stdout)
    atomic_write_text(evidence_dir / "stderr.txt", stderr)
    metadata = {
        **details,
        "input_fingerprint": acquired["fingerprint"],
        "inputs": acquired["inputs"],
        "resources": acquired["resources"],
        "locks": [{k: v for k, v in item.items() if k != "token"} for item in acquired["locks"]],
        "started_revision": acquire_revision,
    }
    if acquired.get("workspace_path"):
        metadata["workspace_path"] = acquired["workspace_path"]
        metadata["source_identity"] = acquired["workspace_source_identity"]
    if acquired.get("child"):
        metadata.update(acquired["child"])
        metadata["candidate_evidence"] = True

    def finish(conn, revision):
        for key, token in raw_tokens.items():
            if key.startswith("resource:"):
                release_resource(conn, token)
            else:
                release_lock(conn, token)
        if not acquired.get("child"):
            conn.execute(
                "UPDATE gates SET status=?,valid=?,input_fingerprint=?,last_run_at=?,revision=? WHERE id=?",
                (status, int(valid), acquired["fingerprint"], utc_now(), revision, gate_id),
            )
        evidence_kind = "child_gate_candidate" if acquired.get("child") else ("child_acceptance" if acquired.get("accept_candidate") else gate_type)
        conn.execute(
            "INSERT INTO evidence(id,gate_id,claim_id,kind,status,path,metadata_json,created_at,revision) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                evidence_id,
                gate_id,
                acquired["claim"]["id"] if acquired["claim"] else None,
                evidence_kind,
                status,
                str(evidence_dir),
                json.dumps(metadata, sort_keys=True),
                utc_now(),
                revision,
            ),
        )
        if acquired.get("accept_candidate") and valid:
            candidate = acquired["accept_candidate"]
            child = conn.execute(
                "SELECT candidate_gates_json,gates_json,acceptance_gates_json FROM child_executions WHERE id=?",
                (candidate["child_execution_id"],),
            ).fetchone()
            if child:
                accepted_gates = set(json.loads(child["acceptance_gates_json"] or "[]"))
                accepted_gates.add(gate_id)
                required_gates = set(json.loads(child["candidate_gates_json"] or child["gates_json"] or "[]"))
                all_credited = required_gates <= accepted_gates
                state = "ready_for_acceptance" if acquired.get("explicit_accept_child") else (
                    "accepted" if all_credited else "ready_for_acceptance"
                )
                conn.execute(
                    "UPDATE child_executions SET acceptance_gates_json=?,state=?,completed_at=? WHERE id=?",
                    (json.dumps(sorted(accepted_gates)), state, utc_now(), candidate["child_execution_id"]),
                )
                if state == "accepted":
                    conn.execute(
                        "UPDATE child_scope_leases SET state='released',released_at=? "
                        "WHERE child_execution_id=? AND state='active'",
                        (utc_now(), candidate["child_execution_id"]),
                    )
        barriers = [] if acquired.get("child") else reevaluate_barriers(conn, revision)
        report = {
            "gate_id": gate_id,
            "task_id": gate["task_id"],
            "status": status,
            "valid": bool(valid),
            "evidence_id": evidence_id,
            "evidence_path": str(evidence_dir),
            "details": details,
            "barrier_changes": barriers,
        }
        if acquired.get("child"):
            report.update(candidate_valid=bool(valid), accepted=False)
        elif acquired.get("accept_candidate"):
            report["accepted"] = bool(valid)
        return report

    report, revision = db.mutate(
        actor_session_id=acquired["claim"]["session_id"] if acquired["claim"] else None,
        entity_type="gate",
        entity_id=gate_id,
        event_type="gate.completed",
        payload={"status": status, "valid": valid, "evidence_id": evidence_id},
        operation=finish,
    )
    return report, revision
