"""Conservative Git and owned-path state inspection."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Iterable

from .models import TodoError


GENERATED_PROJECTION_FILES = {
    ".todo-orchestrator/state.snapshot.json",
    "todo-status.md",
    "todos.md",
}


def is_generated_projection(path: str) -> bool:
    return path in GENERATED_PROJECTION_FILES or path == "todos" or path.startswith("todos/")


def material_dirty_paths(repo_root: Path) -> list[str]:
    return [path for path in dirty_paths(repo_root) if not is_generated_projection(path)]


def integration_diff_args(base_commit: str, target: str | None = None) -> list[str]:
    revisions = [base_commit] if target is None else [base_commit, target]
    return [
        "diff", "--binary", *revisions, "--", ".",
        ":(exclude).todo-orchestrator/state.snapshot.json",
        ":(exclude)todo-status.md",
        ":(exclude)todos.md",
        ":(exclude)todos",
        ":(exclude)todos/**",
    ]


def git_head(repo_root: Path) -> str | None:
    result = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def dirty_paths(repo_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    paths: list[str] = []
    entries = result.stdout.decode("utf-8", errors="replace").split("\0")
    for entry in entries:
        if not entry:
            continue
        value = entry[3:] if len(entry) > 3 else entry
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        paths.append(value)
    return sorted(set(paths))


def canonical_relative(repo_root: Path, value: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise TodoError("invalid_path_scope", f"Path scope must be repository-relative without traversal: {value}")
    resolved = (repo_root / candidate).resolve()
    try:
        relative = resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise TodoError("invalid_path_scope", f"Path escapes repository: {value}") from exc
    text = relative.as_posix().strip("/")
    if not text or text == ".":
        raise TodoError("invalid_path_scope", "Repository root cannot be an ownership scope")
    return text


def path_contains(root: str, path: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def paths_overlap(a: str, b: str) -> bool:
    return path_contains(a, b) or path_contains(b, a)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scope_manifest(repo_root: Path, roots: Iterable[str]) -> dict[str, object]:
    normalized = sorted(set(roots))
    dirty = [path for path in dirty_paths(repo_root) if any(path_contains(root, path) for root in normalized)]
    files: dict[str, str] = {}
    for root in normalized:
        target = repo_root / root
        candidates = [target] if target.is_file() else sorted(target.rglob("*")) if target.exists() else []
        for path in candidates:
            if path.is_file() and ".git" not in path.parts:
                files[path.relative_to(repo_root).as_posix()] = file_hash(path)
    payload = {"roots": normalized, "dirty_paths": dirty, "files": files}
    payload["fingerprint"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload
