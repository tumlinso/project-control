"""Small supported CORE4 contracts with dependency-free validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


class ContractError(ValueError):
    """Raised when a supported runtime contract is malformed."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_HEAD = re.compile(r"^[0-9a-f]{40,64}$")


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{name} must be an object")
    return dict(value)


def _version(value: dict[str, Any], name: str) -> None:
    if value.get("schema_version") != 1:
        raise ContractError(f"{name}.schema_version must be 1")


def _extra(value: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ContractError(f"{name} has unknown fields: {', '.join(unknown)}")


def _strings(value: object, name: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or (nonempty and not item) for item in value):
        raise ContractError(f"{name} must be a list of strings")
    if len(set(value)) != len(value):
        raise ContractError(f"{name} must not contain duplicates")
    return list(value)


def normalize_command_spec(value: object) -> dict[str, Any]:
    result = _object(value, "command")
    allowed = {"schema_version", "argv", "cwd", "env", "timeout_seconds"}
    _extra(result, allowed, "command")
    _version(result, "command")
    argv = _strings(result.get("argv"), "command.argv", nonempty=True)
    if not argv:
        raise ContractError("command.argv must not be empty")
    cwd = result.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise ContractError("command.cwd must be a non-empty string")
    env = result.get("env", {})
    if not isinstance(env, Mapping) or any(not isinstance(key, str) or not isinstance(item, str) for key, item in env.items()):
        raise ContractError("command.env must map strings to strings")
    timeout = result.get("timeout_seconds", 3600.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ContractError("command.timeout_seconds must be positive")
    return {"schema_version": 1, "argv": argv, "cwd": cwd, "env": dict(env), "timeout_seconds": float(timeout)}


def normalize_source_identity(value: object) -> dict[str, Any]:
    result = _object(value, "source_identity")
    allowed = {"schema_version", "repo_root", "git_head", "dirty_paths", "fingerprint"}
    _extra(result, allowed, "source_identity")
    _version(result, "source_identity")
    root = result.get("repo_root")
    head = result.get("git_head")
    fingerprint = result.get("fingerprint")
    if not isinstance(root, str) or not root:
        raise ContractError("source_identity.repo_root must be a non-empty string")
    if head is not None and (not isinstance(head, str) or not _GIT_HEAD.fullmatch(head)):
        raise ContractError("source_identity.git_head must be null or a hexadecimal commit id")
    dirty = sorted(_strings(result.get("dirty_paths"), "source_identity.dirty_paths", nonempty=True))
    if not isinstance(fingerprint, str) or not _SHA256.fullmatch(fingerprint):
        raise ContractError("source_identity.fingerprint must be a lowercase SHA-256")
    return {"schema_version": 1, "repo_root": root, "git_head": head, "dirty_paths": dirty, "fingerprint": fingerprint}


def normalize_resource_request(value: object | None) -> dict[str, Any]:
    result = _object(value or {"schema_version": 1}, "resource_request")
    allowed = {
        "schema_version", "kind", "ids", "count", "tags", "exclusive_resources",
        "isolate_pcie_root", "isolate_nvlink_domain", "cpu_threads", "cpu_heavy", "ram_bytes",
    }
    _extra(result, allowed, "resource_request")
    _version(result, "resource_request")
    if "ids" in result and "count" in result:
        raise ContractError("resource_request may declare ids or count, not both")
    kind = result.get("kind", "accelerator")
    if not isinstance(kind, str) or not kind:
        raise ContractError("resource_request.kind must be a non-empty string")
    ids = _strings(result.get("ids", []), "resource_request.ids", nonempty=True)
    exclusive = _strings(result.get("exclusive_resources", []), "resource_request.exclusive_resources", nonempty=True)
    tags = result.get("tags", {})
    if not isinstance(tags, Mapping) or any(not isinstance(key, str) or not isinstance(item, str) for key, item in tags.items()):
        raise ContractError("resource_request.tags must map strings to strings")
    normalized: dict[str, Any] = {
        "schema_version": 1,
        "kind": kind,
        "ids": ids,
        "tags": dict(tags),
        "exclusive_resources": exclusive,
        "isolate_pcie_root": bool(result.get("isolate_pcie_root", False)),
        "isolate_nvlink_domain": bool(result.get("isolate_nvlink_domain", False)),
        "cpu_heavy": bool(result.get("cpu_heavy", False)),
    }
    for field in ("count", "cpu_threads", "ram_bytes"):
        item = result.get(field, 0)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ContractError(f"resource_request.{field} must be a non-negative integer")
        normalized[field] = item
    return normalized


def normalize_artifact_ref(value: object) -> dict[str, Any]:
    result = _object(value, "artifact")
    allowed = {"schema_version", "artifact_id", "job_id", "kind", "path", "content_hash", "complete"}
    _extra(result, allowed, "artifact")
    _version(result, "artifact")
    for field in ("kind", "path"):
        if not isinstance(result.get(field), str) or not result[field]:
            raise ContractError(f"artifact.{field} must be a non-empty string")
    for field in ("artifact_id", "job_id"):
        if field in result and (not isinstance(result[field], str) or not result[field]):
            raise ContractError(f"artifact.{field} must be a non-empty string")
    content_hash = result.get("content_hash")
    if content_hash is not None and (not isinstance(content_hash, str) or not _SHA256.fullmatch(content_hash)):
        raise ContractError("artifact.content_hash must be null or a lowercase SHA-256")
    if not isinstance(result.get("complete"), bool):
        raise ContractError("artifact.complete must be boolean")
    return {key: result[key] for key in ("schema_version", "artifact_id", "job_id", "kind", "path", "content_hash", "complete") if key in result}


_EVIDENCE_STATES = {
    "queued", "running", "succeeded", "failed", "preempted", "canceled", "skipped",
    "completed", "needs_codex", "no_change",
}


def normalize_evidence_summary(value: object) -> dict[str, Any]:
    result = _object(value, "evidence")
    allowed = {
        "schema_version", "job_id", "result_id", "status", "valid", "contaminated", "severity",
        "classification", "parser_version", "summary", "artifacts", "source_identity",
    }
    _extra(result, allowed, "evidence")
    _version(result, "evidence")
    if result.get("status") not in _EVIDENCE_STATES:
        raise ContractError("evidence.status is unsupported")
    for field in ("valid", "contaminated"):
        if not isinstance(result.get(field), bool):
            raise ContractError(f"evidence.{field} must be boolean")
    severity = result.get("severity")
    if isinstance(severity, bool) or not isinstance(severity, int) or severity < 0:
        raise ContractError("evidence.severity must be a non-negative integer")
    if not isinstance(result.get("summary"), Mapping):
        raise ContractError("evidence.summary must be an object")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list):
        raise ContractError("evidence.artifacts must be an array")
    normalized = {
        "schema_version": 1,
        "status": result["status"],
        "valid": result["valid"],
        "contaminated": result["contaminated"],
        "severity": severity,
        "summary": dict(result["summary"]),
        "artifacts": [normalize_artifact_ref(item) for item in artifacts],
    }
    for field in ("job_id", "result_id", "classification", "parser_version"):
        if field in result:
            item = result[field]
            if item is not None and (not isinstance(item, str) or not item):
                raise ContractError(f"evidence.{field} must be null or a non-empty string")
            normalized[field] = item
    if "source_identity" in result:
        normalized["source_identity"] = normalize_source_identity(result["source_identity"])
    return normalized
