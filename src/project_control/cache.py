from __future__ import annotations

import time
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Hashable, TypeVar


T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    expires_at: float
    value: T


class RevisionCache(Generic[T]):
    def __init__(self, ttl_seconds: float = 3.0, max_items: int = 64):
        self.ttl_seconds = max(0.1, min(ttl_seconds, 5.0))
        self.max_items = max_items
        self._items: OrderedDict[Hashable, CacheEntry[T]] = OrderedDict()

    def get(self, key: Hashable) -> T | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return entry.value

    def put(self, key: Hashable, value: T) -> T:
        self._items[key] = CacheEntry(time.monotonic() + self.ttl_seconds, value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)
        return value

    def clear(self) -> None:
        self._items.clear()


def project_control_cache_root() -> Path:
    """Return the private, disposable cache root without creating it."""
    configured = os.environ.get("XDG_CACHE_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".cache"
    return base / "project-control"


def ensure_private_cache_dir(*parts: str) -> Path:
    """Create an owner-only directory below project-control's cache root."""
    if any(not part or part in {".", ".."} or "/" in part for part in parts):
        raise ValueError("invalid cache path component")
    target = project_control_cache_root().joinpath(*parts)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        target.chmod(0o700)
    except OSError:
        pass
    return target
