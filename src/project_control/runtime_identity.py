"""Fail-closed identity checks for the independently installed Todo engine."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
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
TODO_DISTRIBUTION = "todo-orchestrator"
TODO_VERSION = "0.2.0"
TODO_LOCK_RELATIVE = Path("config/todo-orchestrator-runtime.json")
PROVISIONAL_TODO_VARIABLE = "PROJECT_CONTROL_ALLOW_PROVISIONAL_TODO"


class RuntimeIdentityError(RuntimeError):
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
    distribution_name: str = TODO_DISTRIBUTION
    distribution_version: str = TODO_VERSION

    def public(self) -> dict[str, str]:
        return {
            "skills_root": str(self.skills_root),
            "source_package_root": str(self.source_package_root),
            "package_root": str(self.package_root),
            "module_file": str(self.module_file),
            "fingerprint": self.fingerprint,
            "distribution_name": self.distribution_name,
            "distribution_version": self.distribution_version,
        }


def locate_skills_root(environment: Mapping[str, str] = os.environ) -> Path:
    configured = (environment.get(CANONICAL_ROOT_VARIABLE)
                  or environment.get(OBSERVER_ROOT_VARIABLE)
                  or environment.get(OBSERVER_ROOT_ALIAS_VARIABLE))
    if configured:
        return Path(configured).expanduser().resolve()
    try:
        from .config import configured_observer_skills_root, load_config
        return configured_observer_skills_root(load_config(), environment).resolve()
    except Exception:
        return (Path.home() / ".agents" / "skills").resolve()


def package_fingerprint(package_root: Path, *, allow_empty: bool = False) -> str:
    root = Path(package_root)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeIdentityError("Todo package root is unavailable or unsafe",
                                   expected=str(root), observed="symlink or missing")
    paths = tuple(root.rglob("*"))
    symlinks = [item for item in paths if item.is_symlink()]
    if symlinks:
        raise RuntimeIdentityError("Todo package contains a symlink", expected="regular package files",
                                   observed=str(symlinks[0]))
    sources = sorted(item for item in paths if item.suffix == ".py" and item.is_file())
    if not sources and not allow_empty:
        raise RuntimeIdentityError("Todo package contains no Python sources", expected=str(root), observed="empty")
    digest = hashlib.sha256()
    for source in sources:
        digest.update(source.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _release(environment: Mapping[str, str]) -> tuple[Path, str, dict] | None:
    value, digest = environment.get(RELEASE_MANIFEST_VARIABLE), environment.get(RELEASE_DIGEST_VARIABLE)
    if not value and not digest:
        return None
    if not value or not digest:
        raise RuntimeIdentityError("Release manifest and digest must be configured together",
                                   expected="manifest and digest", observed="incomplete release binding")
    path = Path(value).expanduser().resolve(strict=True)
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
        if hashlib.sha256(raw).hexdigest() != digest or not isinstance(data, dict) or data.get("schema_version") != 4:
            raise ValueError("release identity mismatch")
        todo = data.get("todo_distribution")
        if (not isinstance(todo, dict) or todo.get("name") != TODO_DISTRIBUTION
                or todo.get("version") != TODO_VERSION
                or not isinstance(todo.get("package_path"), str)
                or not isinstance(todo.get("fingerprint"), str)):
            raise ValueError("standalone Todo identity is invalid")
        pc_fingerprint = data.get("project_control_fingerprint")
        if not isinstance(pc_fingerprint, str) or len(pc_fingerprint) != 64:
            raise ValueError("Project Control fingerprint is invalid")
        observed_pc = package_fingerprint(Path(__file__).resolve().parent)
        if observed_pc != pc_fingerprint:
            raise RuntimeIdentityError("Installed Project Control changed", expected=pc_fingerprint, observed=observed_pc)
    except RuntimeIdentityError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeIdentityError("Invalid release manifest", expected=digest, observed=str(exc)) from exc
    return path, digest, data


def _source_todo_lock(environment: Mapping[str, str]) -> dict | None:
    repository = Path(__file__).resolve().parents[2]
    path = repository / TODO_LOCK_RELATIVE
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        expected = {"format", "version", "distribution", "distribution_version", "source_commit",
                    "wheel_sha256", "package_fingerprint", "provisional"}
        if (not isinstance(value, dict) or set(value) != expected
                or value.get("format") != "project-control.todo-runtime-lock"
                or value.get("version") != 1 or value.get("distribution") != TODO_DISTRIBUTION
                or value.get("distribution_version") != TODO_VERSION
                or type(value.get("provisional")) is not bool):
            raise ValueError("unsupported Todo runtime lock")
        if value["provisional"] and environment.get(PROVISIONAL_TODO_VARIABLE) != "1":
            raise ValueError("provisional Todo runtime requires explicit development opt-in")
        if not value["provisional"]:
            commit, wheel_hash, package_hash = (value["source_commit"], value["wheel_sha256"],
                                                value["package_fingerprint"])
            if (not isinstance(commit, str) or len(commit) != 40
                    or any(char not in "0123456789abcdef" for char in commit)):
                raise ValueError("Todo source commit pin is invalid")
            for pin in (wheel_hash, package_hash):
                if (not isinstance(pin, str) or len(pin) != 64
                        or any(char not in "0123456789abcdef" for char in pin)):
                    raise ValueError("Todo wheel or package pin is invalid")
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeIdentityError("Invalid standalone Todo pairing lock",
                                   expected="valid config/todo-orchestrator-runtime.json", observed=str(exc)) from exc
    return value


def bind_runtime(environment: Mapping[str, str] = os.environ, *,
                 importer: Callable[[str], _Module] = importlib.import_module) -> RuntimeIdentity:
    release = _release(environment)
    skills = release[2].get("skills_root") if release else None
    skills_root = Path(skills).expanduser().resolve() if isinstance(skills, str) and skills else locate_skills_root(environment)
    try:
        distribution = importlib.metadata.distribution(TODO_DISTRIBUTION)
        version = distribution.version
        files = distribution.files or ()
        init_entry = next((entry for entry in files if entry.as_posix() == "todo_orchestrator/__init__.py"), None)
        if init_entry is None:
            raise ValueError("distribution does not own todo_orchestrator/__init__.py")
        declared_root = Path(distribution.locate_file("todo_orchestrator"))
        if declared_root.is_symlink():
            raise ValueError("distribution package root is a symlink")
        source_root = declared_root.resolve(strict=True)
    except Exception as exc:
        raise RuntimeIdentityError("Standalone Todo distribution is not installed",
                                   expected=f"{TODO_DISTRIBUTION}=={TODO_VERSION}", observed="not importable") from exc
    if version != TODO_VERSION or source_root.is_symlink() or not (source_root / "__init__.py").is_file():
        raise RuntimeIdentityError("Unexpected standalone Todo distribution",
                                   expected=f"{TODO_DISTRIBUTION}=={TODO_VERSION}", observed=version)
    source_fingerprint = package_fingerprint(source_root)
    source_lock = _source_todo_lock(environment) if release is None else None
    if source_lock and source_lock.get("package_fingerprint") is not None and source_fingerprint != source_lock["package_fingerprint"]:
        raise RuntimeIdentityError("Installed Todo source differs from the paired runtime lock",
                                   expected=str(source_lock["package_fingerprint"]), observed=source_fingerprint)
    if release:
        declared = release[2]["todo_distribution"]
        declared_root = Path(declared["package_path"]).expanduser()
        if declared_root.is_symlink() or declared_root.resolve() != source_root:
            raise RuntimeIdentityError("Installed Todo package path differs from release manifest",
                                       expected=str(declared_root), observed=str(source_root))
        if declared["fingerprint"] != source_fingerprint:
            raise RuntimeIdentityError("Frozen Todo source changed", expected=declared["fingerprint"], observed=source_fingerprint)
    pinned = environment.get(CANONICAL_FINGERPRINT_VARIABLE)
    if pinned and pinned != source_fingerprint:
        raise RuntimeIdentityError("configured Todo source changed after candidate construction",
                                   expected=pinned, observed=source_fingerprint)
    try:
        module = sys.modules.get("todo_orchestrator") or importer("todo_orchestrator")
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeIdentityError("Standalone Todo package is not importable",
                                   expected=str(source_root), observed="not importable") from exc
    module_value = getattr(module, "__file__", None)
    if not module_value:
        raise RuntimeIdentityError("Imported Todo module has no filesystem identity",
                                   expected=str(source_root / "__init__.py"), observed="missing __file__")
    module_path = Path(module_value)
    if module_path.is_symlink():
        raise RuntimeIdentityError("Imported Todo module path is a symlink", expected=str(source_root), observed=str(module_path))
    module_file = module_path.resolve()
    package_root = module_file.parent
    observed_fingerprint = package_fingerprint(package_root)
    if package_root != source_root or module_file != source_root / "__init__.py" or observed_fingerprint != source_fingerprint:
        raise RuntimeIdentityError("Imported Todo is not the installed standalone distribution",
                                   expected=f"{source_root / '__init__.py'}:{source_fingerprint}",
                                   observed=f"{module_file}:{observed_fingerprint}")
    return RuntimeIdentity(skills_root, source_root, package_root, module_file, source_fingerprint,
                           release[0] if release else None, release[1] if release else None,
                           TODO_DISTRIBUTION, version)


def validate_runtime(identity: RuntimeIdentity) -> None:
    module = sys.modules.get("todo_orchestrator")
    value = getattr(module, "__file__", None) if module else None
    observed_file = Path(value).resolve() if value else None
    if identity.release_manifest:
        _release({RELEASE_MANIFEST_VARIABLE: str(identity.release_manifest),
                  RELEASE_DIGEST_VARIABLE: str(identity.release_digest)})
    fingerprint = package_fingerprint(identity.package_root)
    if (observed_file != identity.module_file or identity.source_package_root != identity.package_root
            or fingerprint != identity.fingerprint):
        raise RuntimeIdentityError("Todo runtime identity changed after initialization; restart Project Control",
                                   expected=f"{identity.module_file}:{identity.fingerprint}",
                                   observed=f"{observed_file}:{fingerprint}")


def runtime_environment(identity: RuntimeIdentity, environment: Mapping[str, str] = os.environ) -> dict[str, str]:
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


def runtime_diagnostics(environment: Mapping[str, str] = os.environ, *,
                        binder: Callable[[Mapping[str, str]], RuntimeIdentity] | None = None) -> dict[str, object]:
    try:
        identity = (binder or bind_runtime)(environment)
    except RuntimeIdentityError as exc:
        return {"status": "attention_required", "reason": exc.code, "cause": str(exc),
                "expected": exc.expected, "observed": exc.observed,
                "supported_action": "install_paired_candidate" if exc.observed == "not importable" else "repair_standalone_runtime"}
    return {"status": "verified", "identity": identity.public(), "supported_action": "use_configured_runtime"}
