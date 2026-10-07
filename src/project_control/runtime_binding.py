"""Trusted binding for Project Control's receiver-owned local runtime.

The runtime deliberately keeps the public ``local_worker.*`` module identity.
Before that namespace is imported, this module verifies the receiver manifest,
every listed file, the package root, and (for installed candidates) the release
manifest digest.  Binding is process startup state; callers cannot supply a
repository root or policy through an observer request.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import os
import runpy
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class RuntimeBindingError(RuntimeError):
    """The receiver runtime cannot be bound to the trusted source identity."""


RECEIVER_MANIFEST = "receiver-manifest.json"
RELEASE_MANIFEST_VARIABLE = "PROJECT_CONTROL_RELEASE_MANIFEST"
RELEASE_DIGEST_VARIABLE = "PROJECT_CONTROL_RELEASE_DIGEST"
_BINDING_LOCK = threading.RLock()
_BOUND_FINGERPRINT: str | None = None
_BOUND_MANIFEST_SHA256: str | None = None
_BOUND_FINDER: "_ManifestFinder | None" = None


@dataclass(frozen=True)
class RuntimeIdentity:
    root: Path
    package_root: Path
    source_root: str
    source_commit: str
    manifest_sha256: str
    fingerprint: str
    file_count: int


class _ManifestSourceLoader(importlib.abc.Loader):
    """Compile only the source bytes whose digest is in the bound manifest."""

    def __init__(self, fullname: str, path: Path, expected_sha256: str, *, package: bool):
        self.fullname = fullname
        self.path = path
        self.expected_sha256 = expected_sha256
        self.package = package

    def create_module(self, spec: Any) -> None:
        return None

    def is_package(self, fullname: str) -> bool:
        return self.package

    def get_filename(self, fullname: str) -> str:
        if fullname != self.fullname:
            raise RuntimeBindingError("runtime_loader_name_mismatch")
        return str(self.path)

    def get_code(self, fullname: str) -> Any:
        if fullname != self.fullname:
            raise RuntimeBindingError("runtime_loader_name_mismatch")
        try:
            source = self.path.read_bytes()
        except OSError as error:
            raise RuntimeBindingError("receiver_source_missing_at_import") from error
        if hashlib.sha256(source).hexdigest() != self.expected_sha256:
            raise RuntimeBindingError("receiver_source_hash_mismatch_at_import")
        # Compile these exact bytes directly. SourceFileLoader's default
        # get_code path may trust timestamp/size bytecode caches, so it is not
        # used here and no .pyc is read or written.
        try:
            return compile(source, str(self.path), "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as error:
            raise RuntimeBindingError("receiver_source_compile_failed") from error

    def exec_module(self, module: Any) -> None:
        code = self.get_code(module.__name__)
        exec(code, module.__dict__)


class _ManifestFinder(importlib.abc.MetaPathFinder):
    """Own every import under local_worker and disallow path-finder fallback."""

    def __init__(self, identity: RuntimeIdentity, files: Mapping[str, str]):
        self.identity = identity
        self.files = dict(files)

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname != "local_worker" and not fullname.startswith("local_worker."):
            return None
        suffix = fullname.removeprefix("local_worker").lstrip(".").replace(".", "/")
        package_dir = self.identity.package_root / suffix if suffix else self.identity.package_root
        package_init = package_dir / "__init__.py"
        module_path = package_dir.with_suffix(".py") if suffix else None
        if package_init.is_file():
            source_path = package_init
            relative = source_path.relative_to(self.identity.root).as_posix()
            package = True
        elif module_path is not None and module_path.is_file():
            source_path = module_path
            relative = source_path.relative_to(self.identity.root).as_posix()
            package = False
        else:
            raise ModuleNotFoundError(f"{fullname} is not present in the verified local runtime")
        expected = self.files.get(relative)
        if expected is None:
            raise RuntimeBindingError("receiver_module_not_in_manifest")
        loader = _ManifestSourceLoader(fullname, source_path, expected, package=package)
        spec = importlib.util.spec_from_loader(fullname, loader, origin=str(source_path), is_package=package)
        if spec is None:
            raise RuntimeBindingError("receiver_module_spec_invalid")
        spec.has_location = True
        if package:
            spec.submodule_search_locations = [str(source_path.parent)]
        return spec


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files_fingerprint(files: Mapping[str, str]) -> str:
    """Hash the canonical path/digest table used by receiver manifests."""
    raw = json.dumps(dict(sorted(files.items())), sort_keys=True,
                     separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_release(expected_release_digest: str | None) -> tuple[Path, dict[str, Any]] | None:
    value = os.environ.get(RELEASE_MANIFEST_VARIABLE)
    digest = os.environ.get(RELEASE_DIGEST_VARIABLE)
    if expected_release_digest is not None:
        if digest and digest != expected_release_digest:
            raise RuntimeBindingError("runtime_release_digest_mismatch")
        digest = expected_release_digest
    if not value and not digest:
        return None
    if not value or not digest:
        raise RuntimeBindingError("runtime_release_manifest_incomplete")
    path = Path(value).expanduser().resolve()
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeBindingError("runtime_release_manifest_invalid") from error
    if (hashlib.sha256(raw).hexdigest() != digest or not isinstance(data, dict)
            or data.get("schema_version") not in {2, 3}):
        raise RuntimeBindingError("runtime_release_manifest_digest_mismatch")
    return path, data


def _source_checkout() -> bool:
    package = Path(__file__).resolve().parent
    source_root = package.parent
    repository = source_root.parent
    return source_root.name == "src" and (repository / "pyproject.toml").is_file()


def _read_receiver_manifest(root: Path) -> tuple[dict[str, str], dict[str, Any], str]:
    path = root / RECEIVER_MANIFEST
    if path.is_symlink():
        raise RuntimeBindingError("receiver_manifest_path_invalid")
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeBindingError("receiver_manifest_invalid") from error
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise RuntimeBindingError("receiver_manifest_schema_invalid")
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeBindingError("receiver_manifest_files_invalid")
    normalized: dict[str, str] = {}
    for relative, digest in files.items():
        if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
                or ".." in Path(relative).parts or relative == RECEIVER_MANIFEST
                or Path(relative).as_posix() != relative
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise RuntimeBindingError("receiver_manifest_entry_invalid")
        normalized[Path(relative).as_posix()] = digest
    if len(normalized) != len(files):
        raise RuntimeBindingError("receiver_manifest_path_duplicate")
    source_root, source_commit = data.get("source_root"), data.get("source_commit")
    if not isinstance(source_root, str) or not source_root or not isinstance(source_commit, str) or not source_commit:
        raise RuntimeBindingError("receiver_manifest_source_identity_invalid")
    return normalized, data, hashlib.sha256(raw).hexdigest()


def _verify_receiver(root: Path, *, expected_manifest_sha256: str | None = None,
                     expected_fingerprint: str | None = None) -> RuntimeIdentity:
    root = root.expanduser().resolve(strict=True)
    package_root = root / "local_worker"
    if not package_root.is_dir() or not (package_root / "__init__.py").is_file():
        raise RuntimeBindingError("receiver_package_root_invalid")
    files, manifest, manifest_sha = _read_receiver_manifest(root)
    if expected_manifest_sha256 and manifest_sha != expected_manifest_sha256:
        raise RuntimeBindingError("receiver_manifest_release_pin_mismatch")
    actual: dict[str, str] = {}
    for relative, expected in files.items():
        path = root / relative
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise RuntimeBindingError("receiver_file_missing") from error
        if root not in resolved.parents or not resolved.is_file() or path.is_symlink():
            raise RuntimeBindingError("receiver_file_path_invalid")
        observed = _sha256(resolved)
        if observed != expected:
            raise RuntimeBindingError("receiver_file_hash_mismatch")
        actual[relative] = observed
    ignored = {RECEIVER_MANIFEST, "__pycache__"}
    discovered: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeBindingError("receiver_symlink_rejected")
        if path.is_dir() and path.name == "__pycache__":
            continue
        if path.is_file() and not any(part in ignored for part in path.relative_to(root).parts) and path.suffix != ".pyc":
            discovered.add(path.relative_to(root).as_posix())
    if discovered != set(files):
        raise RuntimeBindingError("receiver_file_set_mismatch")
    fingerprint = _files_fingerprint(actual)
    if expected_fingerprint and fingerprint != expected_fingerprint:
        raise RuntimeBindingError("receiver_fingerprint_release_pin_mismatch")
    return RuntimeIdentity(root, package_root, manifest["source_root"], manifest["source_commit"],
                           manifest_sha, fingerprint, len(files))


def _source_receiver_files(root: Path) -> dict[str, str]:
    """Derive the editable source file map without trusting a checked-in map."""
    if root.is_symlink():
        raise RuntimeBindingError("receiver_root_symlink_rejected")
    try:
        root = root.expanduser().resolve(strict=True)
    except OSError as error:
        raise RuntimeBindingError("receiver_root_unavailable") from error
    package_root = root / "local_worker"
    if not package_root.is_dir() or not (package_root / "__init__.py").is_file():
        raise RuntimeBindingError("receiver_package_root_invalid")

    files: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeBindingError("receiver_symlink_rejected")
        relative_parts = path.relative_to(root).parts
        if path.is_dir() and path.name == "__pycache__":
            continue
        if not path.is_file() or path.suffix == ".pyc" or RECEIVER_MANIFEST in relative_parts:
            continue
        resolved = path.resolve(strict=True)
        if root not in resolved.parents or not resolved.is_file():
            raise RuntimeBindingError("receiver_file_path_invalid")
        files[path.relative_to(root).as_posix()] = _sha256(resolved)
    if not files:
        raise RuntimeBindingError("receiver_manifest_files_invalid")
    return files


def _source_receiver_identity(root: Path) -> RuntimeIdentity:
    """Derive the editable source inventory without trusting a checked-in map."""
    files = _source_receiver_files(root)
    root = root.expanduser().resolve(strict=True)
    package_root = root / "local_worker"

    source_root = str(Path(__file__).resolve().parents[2])
    source_commit = "working-tree"
    fingerprint = _files_fingerprint(files)
    # This is a source-only identity record generated in memory. It changes
    # whenever any imported or packaged receiver file changes, without asking
    # developers to edit the release manifest checked into the checkout.
    identity_record = json.dumps(
        {"files": dict(sorted(files.items())), "source_root": source_root,
         "source_commit": source_commit},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    manifest_sha = hashlib.sha256(identity_record).hexdigest()
    return RuntimeIdentity(root, package_root, source_root, source_commit,
                           manifest_sha, fingerprint, len(files))


def local_runtime_identity(*, root: str | Path | None = None,
                           expected_release_digest: str | None = None) -> RuntimeIdentity:
    """Validate and describe the receiver runtime without importing it."""
    package_root = Path(__file__).resolve().parent
    release = _load_release(expected_release_digest)
    release_binding: Mapping[str, Any] | None = None
    if release is not None:
        release_binding = release[1].get("local_runtime_binding")
        if not isinstance(release_binding, dict):
            raise RuntimeBindingError("runtime_release_binding_missing")
        relative = release_binding.get("path")
        if relative != "project_control/local_runtime":
            raise RuntimeBindingError("runtime_release_root_invalid")
        expected_root = package_root / "local_runtime"
        if root is not None and Path(root).expanduser().resolve() != expected_root.resolve():
            raise RuntimeBindingError("runtime_root_override_rejected")
        root = expected_root
        expected_manifest_sha = release_binding.get("manifest_sha256")
        expected_fingerprint = release_binding.get("fingerprint")
        if not isinstance(expected_manifest_sha, str) or not isinstance(expected_fingerprint, str):
            raise RuntimeBindingError("runtime_release_binding_invalid")
    else:
        if not _source_checkout():
            raise RuntimeBindingError("installed_runtime_release_binding_required")
        if root is None:
            root = package_root / "local_runtime"
        elif Path(root).expanduser().resolve() != (package_root / "local_runtime").resolve():
            raise RuntimeBindingError("runtime_root_override_rejected")
        expected_manifest_sha = None
        expected_fingerprint = None
    if release is not None:
        return _verify_receiver(Path(root), expected_manifest_sha256=expected_manifest_sha,
                                expected_fingerprint=expected_fingerprint)
    return _source_receiver_identity(Path(root))


def _reject_ambient_modules(package_root: Path, fingerprint: str) -> None:
    loaded = [(name, module) for name, module in tuple(sys.modules.items())
              if name == "local_worker" or name.startswith("local_worker.")]
    if loaded and (_BOUND_FINGERPRINT is None or _BOUND_FINGERPRINT != fingerprint):
        raise RuntimeBindingError("local_worker_modules_precede_verified_binding")
    for name, module in loaded:
        location = getattr(module, "__file__", None)
        if not location:
            raise RuntimeBindingError("ambient_local_worker_module_rejected")
        resolved = Path(location).resolve()
        if package_root != resolved.parent and package_root not in resolved.parents:
            raise RuntimeBindingError("ambient_local_worker_module_rejected")


def bind_local_runtime(*, root: str | Path | None = None,
                       expected_release_digest: str | None = None) -> RuntimeIdentity:
    """Verify then expose the sole canonical ``local_worker.*`` package.

    No runtime import occurs until all manifest and release checks pass. An
    already imported package from any other path is rejected to prevent a
    mixed-module process. Calling this repeatedly for the same root is safe.
    """
    global _BOUND_FINGERPRINT, _BOUND_MANIFEST_SHA256, _BOUND_FINDER
    with _BINDING_LOCK:
        identity = local_runtime_identity(root=root, expected_release_digest=expected_release_digest)
        if _BOUND_FINGERPRINT is not None and _BOUND_FINGERPRINT != identity.fingerprint:
            raise RuntimeBindingError("runtime_fingerprint_changed_restart_required")
        if _BOUND_MANIFEST_SHA256 is not None and _BOUND_MANIFEST_SHA256 != identity.manifest_sha256:
            raise RuntimeBindingError("runtime_manifest_changed_restart_required")
        _reject_ambient_modules(identity.package_root, identity.fingerprint)
        if identity.source_commit == "working-tree":
            files = _source_receiver_files(identity.root)
            if _files_fingerprint(files) != identity.fingerprint:
                raise RuntimeBindingError("receiver_changed_during_binding")
            manifest_sha = identity.manifest_sha256
        else:
            files, _, manifest_sha = _read_receiver_manifest(identity.root)
            if manifest_sha != identity.manifest_sha256:
                raise RuntimeBindingError("receiver_manifest_changed_during_binding")
        if _BOUND_FINDER is None:
            _BOUND_FINDER = _ManifestFinder(identity, files)
        elif (_BOUND_FINDER.identity.fingerprint != identity.fingerprint
              or _BOUND_FINDER.identity.root != identity.root):
            raise RuntimeBindingError("runtime_finder_identity_mismatch")
        other_finders = [finder for finder in sys.meta_path if isinstance(finder, _ManifestFinder)
                         and finder is not _BOUND_FINDER]
        if other_finders:
            raise RuntimeBindingError("multiple_local_runtime_finders")
        if _BOUND_FINDER not in sys.meta_path:
            sys.meta_path.insert(0, _BOUND_FINDER)
        runtime_path = str(identity.root)
        # Keep the verified receiver first for tooling that consults sys.path;
        # normal imports are intercepted by the manifest finder above.
        sys.path[:] = [value for value in sys.path if not _is_foreign_runtime_path(value, identity.root)]
        if runtime_path in sys.path:
            sys.path.remove(runtime_path)
        sys.path.insert(0, runtime_path)
        _BOUND_FINGERPRINT = identity.fingerprint
        _BOUND_MANIFEST_SHA256 = identity.manifest_sha256
        return identity


def _is_foreign_runtime_path(value: str, expected_root: Path) -> bool:
    candidate = Path(value or os.getcwd()).expanduser()
    try:
        resolved = candidate.resolve()
        if resolved == expected_root:
            return False
        return (resolved / "local_worker").is_dir()
    except OSError:
        return False


def import_local_worker_supervisor(*, root: str | Path | None = None,
                                   expected_release_digest: str | None = None) -> Any:
    """Bind the checked receiver and import its supervisor entrypoint."""
    bind_local_runtime(root=root, expected_release_digest=expected_release_digest)
    module = importlib.import_module("local_worker.supervisor")
    expected = local_runtime_identity(root=root, expected_release_digest=expected_release_digest)
    source = Path(str(getattr(module, "__file__", ""))).resolve()
    if expected.package_root != source.parent and expected.package_root not in source.parents:
        raise RuntimeBindingError("imported_runtime_source_mismatch")
    return module


def _run_verified_module(module_name: str, argv: list[str]) -> int:
    if not module_name.startswith("local_worker."):
        raise RuntimeBindingError("runtime_cli_module_invalid")
    bind_local_runtime()
    sys.argv = [module_name, *argv]
    runpy.run_module(module_name, run_name="__main__", alter_sys=True)
    return 0


def _main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise RuntimeBindingError("runtime_cli_module_missing")
    module_name, *module_args = args
    return _run_verified_module(module_name, module_args)


if __name__ == "__main__":
    # `python -m` executes this source under `__main__`; import the canonical
    # package module so the finder and its fingerprint have one registry.
    from project_control.runtime_binding import _main as canonical_main

    raise SystemExit(canonical_main())
