"""Trusted validation for the compact model terminal outcome."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any


class ModelOutcomeError(ValueError):
    pass


def _relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ModelOutcomeError("path must be a non-empty repository-relative string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise ModelOutcomeError(f"invalid repository-relative path: {value!r}")
    return value


def _authorized(path: str, scopes: list[str]) -> bool:
    return any(path == scope.rstrip("/") or path.startswith(scope.rstrip("/") + "/") for scope in scopes)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_model_outcome(
    value: object,
    *,
    repository_root: str | Path,
    authorized_read_paths: list[str],
    write_paths: list[str] | None = None,
    actual_changed_paths: list[str] | None = None,
    mode: str = "readonly",
    pure_test_plan: bool = False,
) -> dict[str, Any]:
    """Validate model claims against the isolated source and controller-observed diff."""
    if not isinstance(value, dict):
        raise ModelOutcomeError("model outcome must be an object")
    required = {"outcome", "summary", "claims", "changed_paths", "risk", "blocker"}
    if set(value) != required:
        raise ModelOutcomeError("model outcome has missing or unsupported fields")
    outcome = value["outcome"]
    if outcome not in {"completed", "needs_codex", "failed"}:
        raise ModelOutcomeError("unsupported outcome")
    summary = value["summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 500:
        raise ModelOutcomeError("summary must be a non-empty string of at most 500 characters")
    if value["risk"] not in {"low", "medium", "high"}:
        raise ModelOutcomeError("unsupported risk")
    blocker = value["blocker"]
    if blocker is not None and (not isinstance(blocker, str) or not blocker.strip() or len(blocker) > 500):
        raise ModelOutcomeError("blocker must be null or a non-empty bounded string")
    if outcome == "needs_codex" and blocker is None:
        raise ModelOutcomeError("needs_codex requires a concrete blocker")

    claimed_changed = value["changed_paths"]
    if not isinstance(claimed_changed, list) or any(not isinstance(item, str) for item in claimed_changed):
        raise ModelOutcomeError("changed_paths must be a string array")
    actual = sorted({_relative_path(item) for item in (actual_changed_paths or [])})
    if mode == "readonly" and (claimed_changed or actual):
        raise ModelOutcomeError("read-only work cannot change paths")
    allowed_writes = [_relative_path(item) for item in (write_paths or [])]
    if mode == "writable" and any(not _authorized(item, allowed_writes) for item in actual):
        raise ModelOutcomeError("actual Git diff escapes the authorized write scope")
    if outcome == "needs_codex" and actual:
        raise ModelOutcomeError("needs_codex cannot produce an accepted patch")

    root = Path(repository_root).resolve()
    claims = value["claims"]
    if not isinstance(claims, list) or len(claims) > 16:
        raise ModelOutcomeError("claims must be a bounded array")
    evidence_count = 0
    normalized_claims: list[dict[str, Any]] = []
    read_scopes = [_relative_path(item) for item in authorized_read_paths]
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {"statement", "evidence"}:
            raise ModelOutcomeError("claim has unsupported fields")
        statement = claim["statement"]
        evidence = claim["evidence"]
        if not isinstance(statement, str) or not statement.strip() or len(statement) > 500:
            raise ModelOutcomeError("claim statement must be a non-empty bounded string")
        if not isinstance(evidence, list) or len(evidence) > 16:
            raise ModelOutcomeError("claim evidence must be a bounded array")
        checked: list[dict[str, Any]] = []
        for item in evidence:
            if not isinstance(item, dict) or not set(item).issubset({"path", "line", "end_line", "content_sha256"}):
                raise ModelOutcomeError("evidence has unsupported fields")
            if not {"path", "line", "end_line"}.issubset(item):
                raise ModelOutcomeError("evidence is missing a required field")
            relative = _relative_path(item["path"])
            if not _authorized(relative, read_scopes):
                raise ModelOutcomeError(f"evidence path is outside authorized read scope: {relative}")
            source = (root / relative).resolve()
            if not source.is_relative_to(root) or not source.is_file():
                raise ModelOutcomeError(f"evidence path does not exist: {relative}")
            line = item["line"]
            end_line = item["end_line"]
            if isinstance(line, bool) or isinstance(end_line, bool) or not isinstance(line, int) or not isinstance(end_line, int):
                raise ModelOutcomeError("evidence line range must use integers")
            line_count = len(source.read_text(encoding="utf-8", errors="replace").splitlines())
            if line < 1 or end_line < line or end_line > line_count:
                raise ModelOutcomeError(f"invalid evidence line range for {relative}")
            supplied_hash = item.get("content_sha256")
            if supplied_hash is not None and supplied_hash != _sha256(source):
                raise ModelOutcomeError(f"evidence hash mismatch for {relative}")
            checked.append(dict(item))
            evidence_count += 1
        normalized_claims.append({"statement": statement, "evidence": checked})
    if outcome == "completed" and mode == "readonly" and not pure_test_plan and evidence_count == 0:
        raise ModelOutcomeError("read-only completion requires source evidence")
    return {
        "outcome": outcome,
        "summary": summary.strip(),
        "claims": normalized_claims,
        "changed_paths": actual if mode == "writable" else [],
        "risk": value["risk"],
        "blocker": blocker.strip() if isinstance(blocker, str) else None,
    }
