"""Bridge local-worker startup to Project Control's canonical runtime contract."""

from __future__ import annotations

from pathlib import Path


class CanonicalRuntimeError(RuntimeError):
    code = "runtime_identity_mismatch"


def _api():
    try:
        from project_control import runtime_identity
        return runtime_identity
    except ModuleNotFoundError as exc:
        if exc.name in {"project_control", "project_control.runtime_identity"}:
            raise CanonicalRuntimeError(
                "runtime_identity_mismatch: Project Control runtime identity is unavailable"
            ) from exc
        raise


def bind(repo_root: str | Path):
    api = _api()
    try:
        identity = api.bind_runtime()
    except Exception as exc:
        if getattr(exc, "code", None) == "runtime_identity_mismatch":
            raise CanonicalRuntimeError(f"runtime_identity_mismatch: {exc}") from exc
        raise
    context = identity.public()
    context["project_root"] = str(Path(repo_root).resolve())
    return identity, context


def validate(identity) -> None:
    try:
        _api().validate_runtime(identity)
    except Exception as exc:
        if getattr(exc, "code", None) == "runtime_identity_mismatch":
            raise CanonicalRuntimeError(f"runtime_identity_mismatch: {exc}") from exc
        raise


def subprocess_environment(identity) -> dict[str, str]:
    return _api().runtime_environment(identity)


def todo_cli_path(repo_root: str | Path) -> Path:
    """Locate the already-verified standalone Todo CLI module."""
    bind(repo_root)
    try:
        import todo_orchestrator
    except ImportError as exc:
        raise CanonicalRuntimeError(
            "runtime_identity_mismatch: standalone Todo Orchestrator is unavailable"
        ) from exc
    module_file = getattr(todo_orchestrator, "__file__", None)
    if not isinstance(module_file, str) or not module_file:
        raise CanonicalRuntimeError("runtime_identity_mismatch: Todo module has no filesystem identity")
    cli = Path(module_file).resolve().with_name("__main__.py")
    if not cli.is_file():
        raise CanonicalRuntimeError("runtime_identity_mismatch: standalone Todo CLI module is missing")
    return cli
