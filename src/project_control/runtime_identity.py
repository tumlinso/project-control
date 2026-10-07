"""Fail-closed identity checks for Project Control's bundled Todo runtime.

Todo is an ordinary sibling package in the Project Control distribution. Skills
roots identify optional content only; they never select executable code.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol


CANONICAL_ROOT_VARIABLE = "PROJECT_CONTROL_SKILLS_ROOT"
OBSERVER_ROOT_VARIABLE = "PROJECT_CONTROL_OBSERVER_SKILLS_ROOT"
OBSERVER_ROOT_ALIAS_VARIABLE = "OBSERVER_SKILLS_ROOT"
RELEASE_MANIFEST_VARIABLE = "PROJECT_CONTROL_RELEASE_MANIFEST"
RELEASE_DIGEST_VARIABLE = "PROJECT_CONTROL_RELEASE_DIGEST"
LEGACY_ROOT_VARIABLE = "CODING_WORKFLOW_SKILLS_ROOT"
CANONICAL_FINGERPRINT_VARIABLE = "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT"
LEGACY_FINGERPRINT_VARIABLE = "CODING_WORKFLOW_RUNTIME_FINGERPRINT"


def runtime_diagnostics(
    environment: Mapping[str, str] = os.environ,
    *,
    binder: Callable[[Mapping[str, str]], "RuntimeIdentity"] | None = None,
) -> dict[str, object]:
    """Describe the one supported workflow runtime without changing it.

    This is deliberately a diagnostic, not a launcher. It identifies one
    verified runtime or reports the failing layer without selecting packages,
    changing imports, or exposing ambient process environment.
    """

    bind = binder or bind_runtime
    try:
        identity = bind(environment)
    except RuntimeIdentityError as exc:
        action = "repair_bundled_runtime"
        if exc.observed == "not importable":
            action = "install_paired_candidate"
        elif exc.observed == "incomplete release binding":
            action = "configure_complete_release_binding"
        return {
            "status": "attention_required",
            "reason": exc.code,
            "cause": str(exc),
            "expected": exc.expected,
            "observed": exc.observed,
            "supported_action": action,
        }
    return {
        "status": "verified",
        "identity": identity.public(),
        "supported_action": "use_configured_runtime",
    }


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
    release_manifest: Path | None = None
    release_digest: str | None = None

    def public(self) -> dict[str, str]:
        return {
            "skills_root": str(self.skills_root),
            "source_package_root": str(self.source_package_root),
            "package_root": str(self.package_root),
            "module_file": str(self.module_file),
            "fingerprint": self.fingerprint,
        }


def locate_skills_root(environment: Mapping[str, str] = os.environ) -> Path:
    """Return the optional content root without treating it as executable code."""
    configured = (
        environment.get(CANONICAL_ROOT_VARIABLE)
        or environment.get(OBSERVER_ROOT_VARIABLE)
        or environment.get(OBSERVER_ROOT_ALIAS_VARIABLE)
    )
    if configured:
        return Path(configured).expanduser().resolve()
    try:
        from .config import configured_observer_skills_root, load_config

        return configured_observer_skills_root(load_config(), environment).resolve()
    except Exception:
        # Identity of the bundled workflow engine must remain independent of
        # optional/malformed content configuration. This is only a compatibility
        # value for older callers which display or forward a Skills root.
        return (Path.home() / ".agents" / "skills").resolve()


def package_fingerprint(package_root: Path, *, allow_empty: bool = False) -> str:
    """Hash the importable Python source tree without machine-specific paths."""

    package_root = Path(package_root)
    if package_root.is_symlink():
        raise RuntimeIdentityError(
            "runtime fingerprint root must not be a symlink",
            expected=str(package_root),
            observed="symlink",
        )
    digest = hashlib.sha256()
    if not package_root.is_dir():
        raise RuntimeIdentityError(
            "runtime fingerprint root is unavailable",
            expected=str(package_root),
            observed="missing",
        )
    paths = tuple(package_root.rglob("*"))
    symlinks = [path for path in paths if path.is_symlink()]
    if symlinks:
        raise RuntimeIdentityError(
            "runtime package contains a symlink",
            expected="all package paths remain inside the bundled package",
            observed=str(symlinks[0]),
        )
    sources = sorted(path for path in paths if path.suffix == ".py" and path.is_file())
    if not sources and not allow_empty:
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
    return canonical


def _release(environment: Mapping[str, str]) -> tuple[Path, str, dict] | None:
    value = environment.get(RELEASE_MANIFEST_VARIABLE)
    digest = environment.get(RELEASE_DIGEST_VARIABLE)
    if not value and not digest:
        return None
    if not value or not digest:
        raise RuntimeIdentityError("Release manifest and digest must be configured together", expected="manifest and digest", observed="incomplete release binding")
    path = Path(value).expanduser().resolve()
    try:
        raw = path.read_bytes()
        observed = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        version = data.get("schema_version")
        fingerprint = data.get("todo_runtime_fingerprint")
        if (observed != digest or version not in {2, 3}
                or not isinstance(fingerprint, str) or len(fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in fingerprint)):
            raise ValueError("release identity mismatch")
        if version == 3:
            package_root = data.get("todo_package_root")
            if not isinstance(package_root, str) or not Path(package_root).is_absolute():
                raise ValueError("release Todo package path is invalid")
            if Path(package_root).expanduser().is_symlink():
                raise ValueError("release Todo package root is a symlink")
        # Skills roots and their optional digests/resources are content
        # metadata. They are checked by the relevant skill provider when that
        # capability is invoked, never while binding the core workflow engine.
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise RuntimeIdentityError("Invalid release manifest", expected=digest, observed=str(exc)) from exc
    pc_fingerprint = data.get("project_control_fingerprint")
    if pc_fingerprint:
        observed = package_fingerprint(Path(__file__).parent)
        if observed != pc_fingerprint:
            raise RuntimeIdentityError("Installed Project Control changed", expected=pc_fingerprint, observed=observed)
    return path, digest, data


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

    release = _release(environment)
    # A digest-pinned installed release owns its frozen content location when
    # one is recorded. Do not let ambient user configuration redirect release
    # tools such as the CUDA controller to a live, unverified Skills tree.
    release_content_root = release[2].get("skills_root") if release else None
    skills_root = (
        Path(release_content_root).expanduser().resolve()
        if isinstance(release_content_root, str) and release_content_root
        else locate_skills_root(environment)
    )
    source_candidate = Path(__file__).resolve().parent.parent / "todo_orchestrator"
    if source_candidate.is_symlink():
        raise RuntimeIdentityError(
            "bundled Todo package root must not be a symlink",
            expected=str(source_candidate),
            observed="symlink",
        )
    source_root = source_candidate.resolve()
    if not (source_root / "__init__.py").is_file():
        raise RuntimeIdentityError(
            "bundled todo_orchestrator package is missing",
            expected=str(source_root / "__init__.py"), observed="missing",
        )
    source_fingerprint = package_fingerprint(source_root)
    if release and release[2].get("schema_version") == 3:
        declared_candidate = Path(release[2]["todo_package_root"]).expanduser()
        if declared_candidate.is_symlink():
            raise RuntimeIdentityError(
                "Installed Todo package path is a symlink",
                expected=str(source_root),
                observed=str(declared_candidate),
            )
        declared_root = declared_candidate.resolve()
        if declared_root != source_root:
            raise RuntimeIdentityError("Installed Todo package path differs from release manifest", expected=str(declared_root), observed=str(source_root))
    if release and source_fingerprint != release[2]["todo_runtime_fingerprint"]:
        raise RuntimeIdentityError("Frozen Todo source changed", expected=release[2]["todo_runtime_fingerprint"], observed=source_fingerprint)
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
            "bundled todo_orchestrator is not importable in the Project Control runtime",
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
    module_candidate = Path(module_value)
    if module_candidate.is_symlink():
        raise RuntimeIdentityError(
            "imported Todo module path is a symlink",
            expected=str(source_root / "__init__.py"),
            observed=str(module_candidate),
        )
    module_file = module_candidate.resolve()
    package_root = module_file.parent
    observed_fingerprint = package_fingerprint(package_root)
    if package_root != source_root or module_file != source_root / "__init__.py" or observed_fingerprint != source_fingerprint:
        raise RuntimeIdentityError(
            "imported todo_orchestrator is not the bundled Project Control package",
            expected=f"{source_root / '__init__.py'}:{source_fingerprint}",
            observed=f"{module_file}:{observed_fingerprint}",
        )
    return RuntimeIdentity(skills_root, source_root, package_root, module_file, source_fingerprint, release[0] if release else None, release[1] if release else None)


def validate_runtime(identity: RuntimeIdentity) -> None:
    """Reject source changes, imported-package changes, or module rebinding."""

    module = sys.modules.get("todo_orchestrator")
    value = getattr(module, "__file__", None) if module is not None else None
    observed_file = Path(value).resolve() if value else None
    if identity.release_manifest:
        _release({RELEASE_MANIFEST_VARIABLE: str(identity.release_manifest), RELEASE_DIGEST_VARIABLE: str(identity.release_digest)})
    source_fingerprint = package_fingerprint(identity.source_package_root)
    package_fingerprint_now = package_fingerprint(identity.package_root)
    if (
        observed_file != identity.module_file
        or identity.package_root != identity.source_package_root
        or identity.package_root != (Path(__file__).resolve().parent.parent / "todo_orchestrator").resolve()
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
    clean.pop(LEGACY_FINGERPRINT_VARIABLE, None)
    clean.pop(LEGACY_ROOT_VARIABLE, None)
    if identity.release_manifest:
        clean[RELEASE_MANIFEST_VARIABLE] = str(identity.release_manifest)
        clean[RELEASE_DIGEST_VARIABLE] = str(identity.release_digest)
    clean[CANONICAL_ROOT_VARIABLE] = str(identity.skills_root)
    clean[CANONICAL_FINGERPRINT_VARIABLE] = identity.fingerprint
    return clean
