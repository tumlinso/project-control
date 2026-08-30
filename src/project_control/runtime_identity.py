"""Fail-closed identity checks for the in-process Todo runtime.

The explicit Skills checkout is the source of truth.  The imported
``todo_orchestrator`` package may be that checkout itself or an installed copy,
but its Python sources must have the same deterministic fingerprint.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol


CANONICAL_ROOT_VARIABLE = "PROJECT_CONTROL_SKILLS_ROOT"
LEGACY_ROOT_VARIABLE = "CODING_WORKFLOW_SKILLS_ROOT"
CANONICAL_FINGERPRINT_VARIABLE = "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT"
LEGACY_FINGERPRINT_VARIABLE = "CODING_WORKFLOW_RUNTIME_FINGERPRINT"


class RuntimeIdentityError(RuntimeError):
    """The configured and imported Todo runtimes do not have one identity."""

    code = "runtime_identity_mismatch"

    def __init__(self, message: str, *, expected: str, observed: str):
        super().__init__(message)
        self.expected = expected
        self.observed = observed


class _Module(Protocol):
    __file__: str


@dataclass(frozen=True)
class RuntimeIdentity:
    skills_root: Path
    source_package_root: Path
    package_root: Path
    module_file: Path
    fingerprint: str

    def public(self) -> dict[str, str]:
        return {
            "skills_root": str(self.skills_root),
            "source_package_root": str(self.source_package_root),
            "package_root": str(self.package_root),
            "module_file": str(self.module_file),
            "fingerprint": self.fingerprint,
        }


def _resolved_setting(environment: Mapping[str, str], canonical: str, legacy: str) -> str | None:
    current = environment.get(canonical)
    old = environment.get(legacy)
    if current and old:
        current_path = Path(current).expanduser().resolve()
        old_path = Path(old).expanduser().resolve()
        if current_path != old_path:
            raise RuntimeIdentityError(
                f"{canonical} and {legacy} identify different runtimes",
                expected=str(current_path),
                observed=str(old_path),
            )
        return current
    if current:
        return current
    if old:
        warnings.warn(
            f"{legacy} is deprecated; configure {canonical}",
            DeprecationWarning,
            stacklevel=3,
        )
        return old
    return None


def locate_skills_root(environment: Mapping[str, str] = os.environ) -> Path:
    configured = _resolved_setting(environment, CANONICAL_ROOT_VARIABLE, LEGACY_ROOT_VARIABLE)
    if not configured:
        raise RuntimeIdentityError(
            f"Todo runtime root is not configured; set {CANONICAL_ROOT_VARIABLE}",
            expected=CANONICAL_ROOT_VARIABLE,
            observed="missing",
        )
    root = Path(configured).expanduser().resolve()
    package = root / "todo-orchestrator" / "todo_orchestrator"
    if not package.is_dir() or not (package / "__init__.py").is_file():
        raise RuntimeIdentityError(
            "configured Skills root does not contain todo-orchestrator",
            expected=str(package),
            observed="missing",
        )
    return root


def package_fingerprint(package_root: Path) -> str:
    """Hash the importable Python source tree without machine-specific paths."""

    digest = hashlib.sha256()
    sources = sorted(path for path in package_root.rglob("*.py") if path.is_file())
    if not sources:
        raise RuntimeIdentityError(
            "Todo runtime package contains no Python sources",
            expected=str(package_root),
            observed="empty",
        )
    for source in sources:
        digest.update(source.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _expected_fingerprint(environment: Mapping[str, str]) -> str | None:
    canonical = environment.get(CANONICAL_FINGERPRINT_VARIABLE)
    legacy = environment.get(LEGACY_FINGERPRINT_VARIABLE)
    if canonical and legacy and canonical != legacy:
        raise RuntimeIdentityError(
            "canonical and compatibility Todo fingerprints disagree",
            expected=canonical,
            observed=legacy,
        )
    return canonical or legacy


def bind_runtime(
    environment: Mapping[str, str] = os.environ,
    *,
    importer: Callable[[str], _Module] = importlib.import_module,
) -> RuntimeIdentity:
    """Import and verify Todo once without modifying ``sys.path``.

    An already imported package is allowed only when it has the same source
    fingerprint as the configured checkout.  This supports both editable and
    wheel-installed candidate environments while rejecting ambient packages.
    """

    skills_root = locate_skills_root(environment)
    source_root = (skills_root / "todo-orchestrator" / "todo_orchestrator").resolve()
    source_fingerprint = package_fingerprint(source_root)
    pinned_fingerprint = _expected_fingerprint(environment)
    if pinned_fingerprint and pinned_fingerprint != source_fingerprint:
        raise RuntimeIdentityError(
            "configured Todo source changed after candidate construction",
            expected=pinned_fingerprint,
            observed=source_fingerprint,
        )

    module = sys.modules.get("todo_orchestrator")
    if module is None:
        try:
            module = importer("todo_orchestrator")
        except (ImportError, ModuleNotFoundError) as exc:
            raise RuntimeIdentityError(
                "todo-orchestrator is not installed in the Project Control runtime",
                expected=str(source_root),
                observed="not importable",
            ) from exc
    module_value = getattr(module, "__file__", None)
    if not module_value:
        raise RuntimeIdentityError(
            "imported todo_orchestrator has no filesystem identity",
            expected=str(source_root / "__init__.py"),
            observed="missing __file__",
        )
    module_file = Path(module_value).resolve()
    package_root = module_file.parent
    observed_fingerprint = package_fingerprint(package_root)
    if observed_fingerprint != source_fingerprint:
        raise RuntimeIdentityError(
            "imported todo_orchestrator does not match the configured Skills source",
            expected=f"{source_root}:{source_fingerprint}",
            observed=f"{package_root}:{observed_fingerprint}",
        )
    return RuntimeIdentity(skills_root, source_root, package_root, module_file, source_fingerprint)


def validate_runtime(identity: RuntimeIdentity) -> None:
    """Reject source changes, imported-package changes, or module rebinding."""

    module = sys.modules.get("todo_orchestrator")
    value = getattr(module, "__file__", None) if module is not None else None
    observed_file = Path(value).resolve() if value else None
    source_fingerprint = package_fingerprint(identity.source_package_root)
    package_fingerprint_now = package_fingerprint(identity.package_root)
    if (
        observed_file != identity.module_file
        or source_fingerprint != identity.fingerprint
        or package_fingerprint_now != identity.fingerprint
    ):
        raise RuntimeIdentityError(
            "Todo runtime identity changed after initialization; restart Project Control",
            expected=f"{identity.module_file}:{identity.fingerprint}",
            observed=f"{observed_file}:{source_fingerprint}:{package_fingerprint_now}",
        )


def runtime_environment(
    identity: RuntimeIdentity,
    environment: Mapping[str, str] = os.environ,
) -> dict[str, str]:
    """Return a child-process environment carrying only canonical identity keys."""

    validate_runtime(identity)
    clean = dict(environment)
    clean.pop(LEGACY_ROOT_VARIABLE, None)
    clean.pop(LEGACY_FINGERPRINT_VARIABLE, None)
    clean[CANONICAL_ROOT_VARIABLE] = str(identity.skills_root)
    clean[CANONICAL_FINGERPRINT_VARIABLE] = identity.fingerprint
    return clean
