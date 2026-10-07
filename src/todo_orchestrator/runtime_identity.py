"""Compatibility API for the Todo runtime identity owned by Project Control.

The workflow package is bundled beside ``project_control``.  This module keeps
Todo's historical public helpers, while all executable identity, release pin,
and rebinding checks have one implementation in Project Control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import threading
from typing import Mapping

from . import SCHEMA_VERSION


CANONICAL_ROOT_VARIABLE = "PROJECT_CONTROL_SKILLS_ROOT"
OBSERVER_ROOT_VARIABLE = "PROJECT_CONTROL_OBSERVER_SKILLS_ROOT"
OBSERVER_ROOT_ALIAS_VARIABLE = "OBSERVER_SKILLS_ROOT"
LEGACY_ROOT_VARIABLE = "CODING_WORKFLOW_SKILLS_ROOT"
RUNTIME_FINGERPRINT_VARIABLE = "CODING_WORKFLOW_RUNTIME_FINGERPRINT"
CONTRACT = "PCU-RUNTIME-IDENTITY/1"
_binding_lock = threading.Lock()
_bound_identity: RuntimeIdentity | None = None


def _same_executable(left: RuntimeIdentity, right: RuntimeIdentity) -> bool:
    """Compare executable identity while excluding optional content location."""
    return (
        left.contract == right.contract
        and left.package_root == right.package_root
        and left.package_source == right.package_source
        and left.todo_schema_version == right.todo_schema_version
        and left.fingerprint == right.fingerprint
    )


class RuntimeIdentityError(RuntimeError):
    """A configured or previously bound runtime does not match this package."""

    code = "runtime_identity_mismatch"

    def __init__(self, message: str, *, expected: object = None, observed: object = None):
        super().__init__(message)
        self.expected = expected
        self.observed = observed
        self.details = {
            key: value for key, value in (("expected", expected), ("observed", observed))
            if value is not None
        }


@dataclass(frozen=True)
class RuntimeIdentity:
    """Todo-facing view of the shared Project Control runtime binding."""

    contract: str
    skills_root: Path
    package_root: Path
    package_source: Path
    todo_schema_version: int
    fingerprint: str
    _project_control_identity: object = field(repr=False, compare=False)

    def public(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "skills_root": str(self.skills_root),
            "package_root": str(self.package_root),
            "package_source": str(self.package_source),
            "todo_schema_version": self.todo_schema_version,
            "fingerprint": self.fingerprint,
        }


def _pc_api():
    try:
        from project_control import runtime_identity as api
    except ImportError as exc:
        raise RuntimeIdentityError(
            "Project Control runtime identity is not installed with Todo",
            expected="project_control.runtime_identity",
            observed=str(exc),
        ) from exc
    return api


def _content_root(environment: Mapping[str, str], explicit_root: str | Path | None = None) -> Path:
    if explicit_root is not None:
        return Path(explicit_root).expanduser().resolve()
    value = (
        environment.get(CANONICAL_ROOT_VARIABLE)
        or environment.get(OBSERVER_ROOT_VARIABLE)
        or environment.get(OBSERVER_ROOT_ALIAS_VARIABLE)
    )
    if value:
        return Path(value).expanduser().resolve()
    return _pc_api().locate_skills_root(environment)


def locate_skills_root(explicit_root: str | Path | None = None) -> Path:
    """Return an optional-content location for compatibility callers.

    The value never selects or validates the executable Todo package.
    """
    return _content_root(os.environ, explicit_root)


def bind_canonical_runtime(skills_root: str | Path | None = None) -> RuntimeIdentity:
    """Bind to the bundled Todo package through Project Control's verifier."""
    api = _pc_api()
    environment = dict(os.environ)
    if skills_root is not None:
        selected = Path(skills_root).expanduser().resolve()
        environment[CANONICAL_ROOT_VARIABLE] = str(selected)
        environment[OBSERVER_ROOT_VARIABLE] = str(selected)
    try:
        identity = api.bind_runtime(environment)
    except api.RuntimeIdentityError as exc:
        raise RuntimeIdentityError(
            str(exc), expected=exc.expected, observed=exc.observed,
        ) from exc
    candidate = RuntimeIdentity(
        contract=CONTRACT,
        skills_root=identity.skills_root,
        package_root=identity.package_root,
        package_source=identity.module_file,
        todo_schema_version=SCHEMA_VERSION,
        fingerprint=identity.fingerprint,
        _project_control_identity=identity,
    )
    global _bound_identity
    with _binding_lock:
        if _bound_identity is not None and not _same_executable(_bound_identity, candidate):
            raise RuntimeIdentityError(
                "runtime rebinding is forbidden; restart the process",
                expected=_bound_identity.public(), observed=candidate.public(),
            )
        _bound_identity = candidate
    return candidate


def validate_runtime(identity: RuntimeIdentity) -> None:
    """Revalidate source bytes, release binding, and the shared process bind."""
    if not isinstance(identity, RuntimeIdentity):
        raise RuntimeIdentityError("unrecognized runtime identity object")
    with _binding_lock:
        bound = _bound_identity
    if bound is None or not _same_executable(bound, identity):
        raise RuntimeIdentityError(
            "runtime identity changed after initialization; restart the process",
            expected=bound.public() if bound is not None else "bound runtime",
            observed=identity.public(),
        )
    api = _pc_api()
    try:
        api.validate_runtime(identity._project_control_identity)
    except api.RuntimeIdentityError as exc:
        raise RuntimeIdentityError(
            str(exc), expected=exc.expected, observed=exc.observed,
        ) from exc


def controlled_subprocess_env(identity: RuntimeIdentity) -> dict[str, str]:
    """Return a child environment pinned to the same bundled workflow package."""
    validate_runtime(identity)
    api = _pc_api()
    try:
        return api.runtime_environment(identity._project_control_identity)
    except api.RuntimeIdentityError as exc:
        raise RuntimeIdentityError(
            str(exc), expected=exc.expected, observed=exc.observed,
        ) from exc


def project_runtime_context(
    repo_root: str | Path, identity: RuntimeIdentity | None = None,
) -> dict[str, object]:
    """Describe Todo authority state while keeping package identity internal."""
    runtime = identity or bind_canonical_runtime()
    validate_runtime(runtime)
    try:
        from .config import project_paths, read_project

        paths = project_paths(repo_root)
        project = read_project(paths.repo_root)
    except Exception:
        raise
    return {
        **runtime.public(),
        "project_uuid": str(project["project_uuid"]),
        "repo_root": str(paths.repo_root),
        "db_path": str(paths.db_file),
    }


def _reset_for_testing() -> None:
    """Compatibility reset; the authoritative binding lives in Project Control."""
    # Runtime state is owned centrally. Production code never resets it.
    global _bound_identity
    with _binding_lock:
        _bound_identity = None
    try:
        from project_control.workflow_binding import reset_runtime_for_testing

        reset_runtime_for_testing()
    except ImportError:
        pass


__all__ = [
    "CANONICAL_ROOT_VARIABLE", "CONTRACT", "LEGACY_ROOT_VARIABLE",
    "OBSERVER_ROOT_ALIAS_VARIABLE", "OBSERVER_ROOT_VARIABLE",
    "RUNTIME_FINGERPRINT_VARIABLE", "RuntimeIdentity",
    "RuntimeIdentityError", "bind_canonical_runtime", "controlled_subprocess_env",
    "locate_skills_root", "project_runtime_context", "validate_runtime",
]
