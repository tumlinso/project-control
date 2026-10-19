"""Source-grounded, content-sensitive identity capture."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from .contracts import ContractError, normalize_source_identity


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)


def _dirty_paths(status: bytes) -> list[str]:
    entries = status.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        state = entry[:2].decode("ascii", errors="replace")
        path = entry[3:].decode("utf-8", errors="surrogateescape")
        if "R" in state or "C" in state:
            # Porcelain v1 -z reports the destination first, then the source.
            # The destination is the retained pathname whose material bytes
            # must be part of a content-sensitive source identity.
            if index < len(entries) and entries[index]:
                index += 1
        paths.append(path)
    return sorted(set(paths))


def _path_hash(root: Path, relative: str) -> str | None:
    path = root / relative
    try:
        if path.is_symlink():
            payload = b"symlink\0" + os.readlink(path).encode("utf-8", errors="surrogateescape")
        elif path.is_file():
            payload = path.read_bytes()
        else:
            return None
    except OSError:
        return None
    return hashlib.sha256(payload).hexdigest()


def capture_source_identity(repo_root: str | Path) -> dict[str, object]:
    requested = Path(repo_root).resolve()
    top = _git(requested, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise ContractError(f"source identity requires a Git worktree: {requested}")
    root = Path(top.stdout.decode("utf-8").strip()).resolve()
    head_result = _git(root, "rev-parse", "--verify", "HEAD")
    head = head_result.stdout.decode("ascii").strip() if head_result.returncode == 0 else None
    status_result = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if status_result.returncode != 0:
        raise ContractError(f"unable to inspect Git status for {root}")
    paths = _dirty_paths(status_result.stdout)
    payload = {
        "git_head": head,
        "status_sha256": hashlib.sha256(status_result.stdout).hexdigest(),
        "files": [{"path": path, "sha256": _path_hash(root, path)} for path in paths],
    }
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return normalize_source_identity({
        "schema_version": 1,
        "repo_root": str(root),
        "git_head": head,
        "dirty_paths": paths,
        "fingerprint": fingerprint,
    })
