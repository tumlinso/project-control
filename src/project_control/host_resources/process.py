"""Small process-generation helpers for host-owned physical reservations."""

from __future__ import annotations

import os
from pathlib import Path


def _process_start(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21]
    except (OSError, IndexError):
        return None


def _alive(pid: int | None, expected: str | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        actual = _process_start(pid)
        return not expected or not actual or expected == actual
    except OSError:
        return False

