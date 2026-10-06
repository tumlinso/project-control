"""Bridge local-worker startup to Todo's canonical runtime contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


class CanonicalRuntimeError(RuntimeError):
    code = "runtime_identity_mismatch"


def _api():
    try:
        from todo_orchestrator import runtime_identity
        return runtime_identity
    except ModuleNotFoundError as exc:
        if exc.name not in {"todo_orchestrator", "todo_orchestrator.runtime_identity"}:
            raise
        canonical = os.environ.get("PROJECT_CONTROL_SKILLS_ROOT")
        legacy = os.environ.get("CODING_WORKFLOW_SKILLS_ROOT")
        if canonical and legacy and Path(canonical).expanduser().resolve() != Path(legacy).expanduser().resolve():
            raise CanonicalRuntimeError(
                "runtime_identity_mismatch: canonical and legacy Skills roots differ"
            )
        configured = canonical or legacy
        if not configured:
            data_home = Path(os.environ.get(
                "XDG_DATA_HOME", Path.home() / ".local/share"
            )).expanduser()
            locator = data_home / "coding-workflow-mcp/skills-root.json"
            try:
                configured = str(json.loads(locator.read_text(encoding="utf-8"))["skills_root"])
            except (OSError, KeyError, TypeError, json.JSONDecodeError) as locator_error:
                raise CanonicalRuntimeError(
                    "runtime_identity_mismatch: set PROJECT_CONTROL_SKILLS_ROOT"
                ) from locator_error
        package_parent = Path(configured).expanduser().resolve() / "todo-orchestrator"
        original = list(sys.path)
        try:
            sys.path.insert(0, str(package_parent))
            from todo_orchestrator import runtime_identity
            return runtime_identity
        finally:
            sys.path[:] = original


def bind(repo_root: str | Path):
    api = _api()
    try:
        identity = api.bind_canonical_runtime()
    except Exception as exc:
        if getattr(exc, "code", None) == "runtime_identity_mismatch":
            raise CanonicalRuntimeError(f"runtime_identity_mismatch: {exc}") from exc
        raise
    try:
        context = api.project_runtime_context(repo_root, identity)
    except Exception as exc:
        if getattr(exc, "code", None) != "project_not_bootstrapped":
            raise
        context = identity.public()
    return identity, context


def validate(identity) -> None:
    try:
        _api().validate_runtime(identity)
    except Exception as exc:
        if getattr(exc, "code", None) == "runtime_identity_mismatch":
            raise CanonicalRuntimeError(f"runtime_identity_mismatch: {exc}") from exc
        raise


def subprocess_environment(identity) -> dict[str, str]:
    return _api().controlled_subprocess_env(identity)
