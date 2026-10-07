"""Internal background-runtime value types and stable private states."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PREEMPTED = "preempted"
    CANCELED = "canceled"
    SKIPPED = "skipped"


TERMINAL_STATES = {
    JobState.SUCCEEDED.value,
    JobState.FAILED.value,
    JobState.CANCELED.value,
    JobState.SKIPPED.value,
}


@dataclass(frozen=True)
class RuntimePaths:
    root: Any
    database: Any
    artifacts: Any
    wake_lock: Any


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
