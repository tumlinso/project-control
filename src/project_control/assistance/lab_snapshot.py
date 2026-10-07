"""Detached, source-bound captures for the bounded scratch laboratory.

This module only materializes selected repository bytes. It never creates a
Git worktree and never changes the registered source tree.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Sequence

from ..adapters.git import GitReadAdapter
from ..security import SecurityError, is_denied


DEFAULT_MAX_BYTES = 50 * 1024 * 1024
MAX_SOURCE_FILES = 10_000
MAX_SOURCE_DIRECTORIES = 10_000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_SELECTION_PATHS = 64
MAX_PATH_DEPTH = 64
MAX_PATH_BYTES = 4096
MAX_SELECTION_METADATA_BYTES = 64 * 1024
MANIFEST_NAME = ".lab-snapshot.json"
_DENIED_PARTS = frozenset({
    ".git", ".todo-orchestrator", ".ctxpp", "todos", ".venv", "venv",
    "__pycache__", "node_modules", "logs", "log", "output", "outputs",
    "generated", "build", "dist", "notebooks", "experiments", "experiment",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".cache", "target",
    "coverage", "htmlcov", "model_notes", "model-notes",
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".codex",
    ".password-store", ".git-credentials", ".netrc", ".npmrc", ".pypirc",
})
_DENIED_NAMES = frozenset({
    "todos.md", "todo-status.md", "state.snapshot.json", "id_rsa", "id_ed25519",
    # Keep this in step with AS1ControlService's exact private source set.
    "auth.json", "runtime-state.json",
})
_DENIED_SUFFIXES = (".pem", ".key", ".pyc", ".pyo", ".gguf")


class SnapshotError(ValueError):
    """A selected source cannot be captured consistently and safely."""


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    mode: int
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A detached tree and the identities that describe its captured bytes."""

    source_root: Path
    destination: Path
    head_commit: str
    base_commit: str
    status_fingerprint: str
    entries: tuple[ManifestEntry, ...]
    total_bytes: int

    @classmethod
    def capture(
        cls,
        source_root: Path,
        selected_paths: Sequence[str],
        destination: Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_files: int = MAX_SOURCE_FILES,
        max_dirs: int = MAX_SOURCE_DIRECTORIES,
        max_manifest_bytes: int = MAX_MANIFEST_BYTES,
        base_revision: str = "HEAD",
    ) -> "Snapshot":
        """Copy selected files to a new private directory and verify no race.

        ``selected_paths`` are repository-relative files or directories. A
        directory selection recursively includes every regular file beneath
        it; unsafe entries cause the whole capture to fail. The default base
        identity is HEAD, and callers can name another already available Git
        commit with ``base_revision``.
        """
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise SnapshotError("max_bytes must be a positive integer")
        if max_bytes > DEFAULT_MAX_BYTES:
            raise SnapshotError("source capture exceeds the 50 MiB limit")
        _validate_lowered_limit(max_files, MAX_SOURCE_FILES, "max_files")
        _validate_lowered_limit(max_dirs, MAX_SOURCE_DIRECTORIES, "max_dirs")
        _validate_lowered_limit(max_manifest_bytes, MAX_MANIFEST_BYTES, "max_manifest_bytes")
        if not isinstance(base_revision, str) or not base_revision or base_revision.startswith("-"):
            raise SnapshotError("base revision is invalid")
        if not isinstance(selected_paths, (list, tuple)) or not selected_paths:
            raise SnapshotError("at least one selected source path is required")
        if len(selected_paths) > MAX_SELECTION_PATHS:
            raise SnapshotError("source selection path count exceeds the limit")
        if any(not isinstance(value, str) for value in selected_paths):
            raise SnapshotError("selected paths must be strings")
        try:
            metadata_bytes = sum(len(value.encode("utf-8")) for value in selected_paths)
        except UnicodeEncodeError as exc:
            raise SnapshotError("selected paths must be valid UTF-8") from exc
        if metadata_bytes > MAX_SELECTION_METADATA_BYTES:
            raise SnapshotError("source selection metadata exceeds the UTF-8 byte limit")

        try:
            root = Path(source_root).resolve(strict=True)
            if not root.is_dir():
                raise SnapshotError("source root is not a directory")
            target = Path(destination).expanduser()
            if target.exists() or target.is_symlink():
                raise SnapshotError("destination already exists")
            parent = target.parent.resolve(strict=True)
            target = parent / target.name
            if target == root or root in target.parents:
                raise SnapshotError("destination must be outside the registered source")
            if target.name in {"", ".", ".."}:
                raise SnapshotError("destination path is invalid")
        except OSError as exc:
            raise SnapshotError("source or destination is unavailable") from exc

        git = GitReadAdapter(root)
        try:
            before = git.identity()
            base_commit = git.verify_revision(base_revision)
        except Exception as exc:
            raise SnapshotError("Git source identity is unavailable") from exc

        selected = tuple(sorted({_validate_relative_path(value) for value in selected_paths}))
        if not selected:
            raise SnapshotError("at least one selected source path is required")

        # Enumerate once, retaining lstat identity so both the copy and the
        # post-copy check can detect replacement, mode changes, and edits.
        planned: dict[str, os.stat_result] = {}
        captured_directories: set[str] = set()
        try:
            for relative in selected:
                candidate = _safe_source_path(root, relative)
                info = candidate.lstat()
                if stat.S_ISDIR(info.st_mode):
                    _walk_selected_directory(root, relative, planned, captured_directories,
                                             max_files=max_files, max_dirs=max_dirs)
                elif stat.S_ISREG(info.st_mode):
                    if info.st_nlink != 1:
                        raise SnapshotError("hard-linked files are not allowed in a source capture")
                    planned[relative] = info
                    if len(planned) > max_files:
                        raise SnapshotError("selected source exceeds the file count limit")
                else:
                    raise SnapshotError("selected source is not a regular file or directory")
        except SnapshotError:
            raise
        except OSError as exc:
            raise SnapshotError("selected source could not be enumerated") from exc
        if not planned:
            raise SnapshotError("selected source contains no files")

        total = sum(info.st_size for info in planned.values())
        if total > max_bytes:
            raise SnapshotError("selected source exceeds the capture byte limit")

        staging: Path | None = None
        try:
            staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.capture-", dir=parent))
            os.chmod(staging, 0o700)
            entries: list[ManifestEntry] = []
            copied_bytes = 0
            for relative in sorted(planned):
                source = _safe_source_path(root, relative)
                expected = planned[relative]
                _assert_same_stat(expected, source.lstat())
                output = _ensure_private_capture_parent(staging, relative)
                mode = stat.S_IMODE(expected.st_mode) & 0o777
                digest = hashlib.sha256()
                size = _copy_regular_file(source, output, mode, digest, max_bytes - copied_bytes)
                copied_bytes += size
                entries.append(ManifestEntry(relative, mode, size, digest.hexdigest()))

            # Check actual source bytes and Git identity again. This catches
            # external edits that race the copy, including edits to another
            # selected file after its first copy completed.
            for entry in entries:
                source = _safe_source_path(root, entry.path)
                current = source.lstat()
                _assert_same_stat(planned[entry.path], current)
                digest, size = _hash_regular_file(source)
                if size != entry.size or digest != entry.sha256:
                    raise SnapshotError("source changed during capture")
            try:
                after = git.identity()
            except Exception as exc:
                raise SnapshotError("Git source identity changed or became unavailable") from exc
            if (before.commit, before.status_fingerprint) != (after.commit, after.status_fingerprint):
                raise SnapshotError("source changed during capture")

            manifest = {
                "format": "project-control-lab-snapshot/1",
                "head_commit": before.commit,
                "base_commit": base_commit,
                "status_fingerprint": before.status_fingerprint,
                "total_bytes": copied_bytes,
                "files": [
                    {"path": item.path, "mode": item.mode, "size": item.size, "sha256": item.sha256}
                    for item in entries
                ],
            }
            manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            if len(manifest_bytes) > max_manifest_bytes:
                raise SnapshotError("snapshot manifest exceeds the serialization limit")
            manifest_path = staging / MANIFEST_NAME
            manifest_path.write_bytes(manifest_bytes)
            os.chmod(manifest_path, 0o600)
            os.replace(staging, target)
            staging = None
            return cls(root, target, before.commit, base_commit, before.status_fingerprint,
                       tuple(entries), copied_bytes)
        except SnapshotError:
            raise
        except OSError as exc:
            raise SnapshotError("detached source capture failed") from exc
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)


def _validate_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise SnapshotError("selected path must be repository-relative")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SnapshotError("selected path must be repository-relative")
    try:
        path_bytes = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SnapshotError("selected path must be valid UTF-8") from exc
    if len(path.parts) > MAX_PATH_DEPTH or path_bytes > MAX_PATH_BYTES:
        raise SnapshotError("selected path exceeds the depth or byte limit")
    normalized = path.as_posix()
    if normalized == MANIFEST_NAME or _is_denied(normalized):
        raise SnapshotError("selected path is private or generated")
    return normalized


def _is_denied(relative: str) -> bool:
    path = PurePosixPath(relative)
    folded_parts = {part.casefold() for part in path.parts}
    name = path.name.casefold()
    return (
        bool(folded_parts & _DENIED_PARTS)
        or name in _DENIED_NAMES
        or any(marker in name for marker in ("token", "secret", "credential", "password", "api_key", "apikey", "private_key"))
        or name == ".env"
        or name.startswith(".env.")
        or name.endswith(_DENIED_SUFFIXES)
        or ".generated." in name
        or name.endswith((".generated", ".model-note", ".model-notes"))
        or is_denied(Path(relative))
    )


def _safe_source_path(root: Path, relative: str) -> Path:
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise SnapshotError("selected source is unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise SnapshotError("symlinks are not allowed in a source capture")
        if current != root / relative and not stat.S_ISDIR(info.st_mode):
            raise SnapshotError("source path has a non-directory parent")
    return current


def _validate_lowered_limit(value: int, maximum: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise SnapshotError(f"{name} must be a positive integer no greater than {maximum}")


def _walk_selected_directory(root: Path, relative: str, planned: dict[str, os.stat_result],
                             captured_directories: set[str], *,
                             max_files: int, max_dirs: int) -> None:
    directory = _safe_source_path(root, relative)
    if relative in captured_directories:
        return
    captured_directories.add(relative)
    if len(captured_directories) > max_dirs:
        raise SnapshotError("selected source exceeds the directory count limit")
    try:
        with os.scandir(directory) as entries:
            for child in entries:
                child_relative = f"{relative}/{child.name}"
                _validate_relative_path(child_relative)
                child_path = _safe_source_path(root, child_relative)
                info = child_path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    _walk_selected_directory(root, child_relative, planned, captured_directories,
                                             max_files=max_files, max_dirs=max_dirs)
                elif stat.S_ISREG(info.st_mode):
                    if info.st_nlink != 1:
                        raise SnapshotError("hard-linked files are not allowed in a source capture")
                    planned[child_relative] = info
                    if len(planned) > max_files:
                        raise SnapshotError("selected source exceeds the file count limit")
                else:
                    raise SnapshotError("symlinks and special files are not allowed in a source capture")
    except OSError as exc:
        raise SnapshotError("selected directory could not be enumerated") from exc


def _assert_same_stat(expected: os.stat_result, actual: os.stat_result) -> None:
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_nlink", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(expected, field) != getattr(actual, field) for field in fields):
        raise SnapshotError("source changed during capture")


def _ensure_private_capture_parent(staging: Path, relative: str) -> Path:
    """Create every detached parent explicitly so pathlib's implicit parents do not inherit 0777."""
    parts = PurePosixPath(relative).parts
    parent = staging
    for part in parts[:-1]:
        parent = parent / part
        try:
            parent.mkdir(mode=0o700)
        except FileExistsError:
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise SnapshotError("captured source parent is not a private directory")
        if stat.S_IMODE(parent.lstat().st_mode) != 0o700:
            raise SnapshotError("captured source parent directory mode changed")
    return parent / parts[-1]


def _copy_regular_file(source: Path, output: Path, mode: int, digest, remaining: int) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise SnapshotError("source file could not be opened safely") from exc
    count = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            if before.st_nlink != 1:
                raise SnapshotError("hard-linked files are not allowed in a source capture")
            raise SnapshotError("source is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as source_stream, output.open("xb") as target_stream:
            while block := source_stream.read(1024 * 1024):
                count += len(block)
                if count > remaining:
                    raise SnapshotError("selected source exceeds the capture byte limit")
                digest.update(block)
                target_stream.write(block)
            target_stream.flush()
            os.fsync(target_stream.fileno())
        after = os.fstat(descriptor)
        _assert_same_stat(before, after)
        if count != before.st_size:
            raise SnapshotError("source changed during capture")
        os.chmod(output, mode)
        return count
    finally:
        os.close(descriptor)


def _hash_regular_file(source: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise SnapshotError("source file could not be reopened safely") from exc
    digest = hashlib.sha256()
    count = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            if before.st_nlink != 1:
                raise SnapshotError("hard-linked files are not allowed in a source capture")
            raise SnapshotError("source is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while block := stream.read(1024 * 1024):
                count += len(block)
                digest.update(block)
        _assert_same_stat(before, os.fstat(descriptor))
        if count != before.st_size:
            raise SnapshotError("source changed during capture")
        return digest.hexdigest(), count
    finally:
        os.close(descriptor)
