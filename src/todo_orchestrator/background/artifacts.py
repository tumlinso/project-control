"""Private artifact paths and bounded metadata helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path


TAIL_BYTES = 16 * 1024


def attempt_directory(root: Path, job_id: str, attempt_id: str) -> Path:
    path = root / "jobs" / job_id / attempt_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def bounded_tail(path: Path, limit: int = TAIL_BYTES) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def file_digest(path: Path) -> str | None:
    try:
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
        return value.hexdigest()
    except OSError:
        return None
