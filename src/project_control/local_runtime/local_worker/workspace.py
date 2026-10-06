from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator

from .verification import require_verification


class WorkspaceError(RuntimeError):
    pass


def _runtime_source():
    from .canonical_runtime import bind
    bind(Path.cwd())
    from todo_orchestrator.runtime import capture_source_identity, normalize_source_identity
    return capture_source_identity, normalize_source_identity


def _git(root: Path, *args: str, input_data: bytes | None = None,
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.run(["git", *args], cwd=root, input=input_data, env=env,
                             capture_output=True, check=False)
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise WorkspaceError(f"git {' '.join(args)} failed: {detail[:500]}")
    return process


def normalize_scopes(values: Iterable[str]) -> list[str]:
    scopes = []
    for value in values:
        path = PurePosixPath(str(value).replace("\\", "/"))
        normalized = str(path)
        if path.is_absolute() or normalized in {"", ".", ".."} or ".." in path.parts:
            raise WorkspaceError(f"write scope is not repository-relative: {value!r}")
        if normalized == ".git" or normalized.startswith(".git/"):
            raise WorkspaceError("write scope may not include Git administrative state")
        scopes.append(normalized)
    result = sorted(set(scopes))
    if not result:
        raise WorkspaceError("at least one write scope is required")
    return result


def _inside(path: str, scope: str) -> bool:
    return path == scope or path.startswith(scope + "/")


def _content_hash(path: Path) -> str | None:
    if path.is_symlink():
        return hashlib.sha256(b"symlink\0" + os.readlink(path).encode()).hexdigest()
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return None


class WritableWorkspace:
    def __init__(self, *, primary_root: Path, path: Path, temporary_root: Path,
                 source_identity: dict[str, Any], write_scopes: list[str],
                 index_path: Path, baseline_tree: str, baseline_verification: dict[str, Any]):
        self.primary_root = primary_root
        self.path = path
        self.temporary_root = temporary_root
        self.source_identity = source_identity
        self.write_scopes = write_scopes
        self.index_path = index_path
        self.baseline_tree = baseline_tree
        self.baseline_verification = baseline_verification

    @property
    def index_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment["GIT_INDEX_FILE"] = str(self.index_path)
        return environment


def _materialize(primary: Path, expected_identity: object, write_scopes: Iterable[str],
                 baseline_commands: Iterable[object], *, read_dependencies: Iterable[str] = (),
                 approved_overlays: Iterable[str] | None = None) -> WritableWorkspace:
    capture_source_identity, normalize_source_identity = _runtime_source()
    expected = normalize_source_identity(expected_identity)
    current = capture_source_identity(primary)
    if Path(str(expected["repo_root"])).resolve() != primary:
        raise WorkspaceError("source identity belongs to a different repository")
    if current["fingerprint"] != expected["fingerprint"] or current["git_head"] != expected["git_head"]:
        raise WorkspaceError("primary source identity is stale before materialization")
    if expected["git_head"] is None:
        raise WorkspaceError("writable work requires an existing Git commit")
    scopes = normalize_scopes(list(write_scopes))
    read_values = list(read_dependencies)
    approved_values = None if approved_overlays is None else list(approved_overlays)
    reads = normalize_scopes(read_values) if read_values else []
    approved = None if approved_values is None else (normalize_scopes(approved_values) if approved_values else [])
    if approved is not None:
        denied = [item for item in approved if not any(_inside(item, root) for root in [*scopes, *reads])]
        if denied:
            raise WorkspaceError(f"approved overlays exceed write scopes and read dependencies: {denied}")
    temporary = Path(tempfile.mkdtemp(prefix="local-worker-writable-"))
    worktree = temporary / "worktree"
    try:
        _git(primary, "worktree", "add", "--detach", str(worktree), str(expected["git_head"]))
        diff_args = ["diff", "--binary", "--full-index", str(expected["git_head"])]
        if approved:
            diff_args.extend(["--", *approved])
        tracked_patch = b"" if approved == [] else _git(primary, *diff_args).stdout
        if tracked_patch:
            _git(worktree, "apply", "--binary", "--whitespace=nowarn", "-", input_data=tracked_patch)
        untracked = _git(primary, "ls-files", "--others", "--exclude-standard", "-z").stdout.split(b"\0")
        for encoded in untracked:
            if not encoded:
                continue
            relative = encoded.decode("utf-8", errors="surrogateescape")
            if approved is not None and not any(_inside(relative, item) or _inside(item, relative) for item in approved):
                continue
            source, destination = primary / relative, worktree / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                destination.symlink_to(os.readlink(source))
            elif source.is_file():
                shutil.copy2(source, destination)
        overlay_paths = expected["dirty_paths"] if approved is None else [
            relative for relative in expected["dirty_paths"]
            if any(_inside(relative, item) or _inside(item, relative) for item in approved)
        ]
        for relative in overlay_paths:
            if _content_hash(primary / relative) != _content_hash(worktree / relative):
                raise WorkspaceError(f"dirty overlay mismatch: {relative}")
        index_path = temporary / "baseline.index"
        environment = dict(os.environ)
        environment["GIT_INDEX_FILE"] = str(index_path)
        baseline = require_verification(worktree, baseline_commands, phase="baseline")
        _git(worktree, "read-tree", "HEAD", env=environment)
        _git(worktree, "add", "-A", "--", ".", env=environment)
        baseline_tree = _git(worktree, "write-tree", env=environment).stdout.decode().strip()
        return WritableWorkspace(
            primary_root=primary, path=worktree, temporary_root=temporary,
            source_identity=expected, write_scopes=scopes, index_path=index_path,
            baseline_tree=baseline_tree, baseline_verification=baseline,
        )
    except Exception:
        if worktree.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=primary,
                           capture_output=True, check=False)
        shutil.rmtree(temporary, ignore_errors=True)
        raise


@contextmanager
def materialize_writable_workspace(repo_root: str | Path, source_identity: object,
                                   write_scopes: Iterable[str], baseline_commands: Iterable[object], *,
                                   read_dependencies: Iterable[str] = (),
                                   approved_overlays: Iterable[str] | None = None) -> Iterator[WritableWorkspace]:
    primary = Path(repo_root).resolve()
    workspace = _materialize(primary, source_identity, write_scopes, baseline_commands,
                             read_dependencies=read_dependencies, approved_overlays=approved_overlays)
    try:
        yield workspace
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(workspace.path)], cwd=primary,
                       capture_output=True, check=False)
        shutil.rmtree(workspace.temporary_root, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], cwd=primary, capture_output=True, check=False)
