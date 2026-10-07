"""Bridge local-worker startup to Todo's canonical runtime contract."""

from __future__ import annotations

from pathlib import Path


class CanonicalRuntimeError(RuntimeError):
    code = "runtime_identity_mismatch"


def _api():
    try:
        from todo_orchestrator import runtime_identity
        return runtime_identity
    except ModuleNotFoundError as exc:
        if exc.name in {"todo_orchestrator", "todo_orchestrator.runtime_identity"}:
            raise CanonicalRuntimeError(
                "runtime_identity_mismatch: bundled todo_orchestrator is unavailable"
            ) from exc
        raise


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
