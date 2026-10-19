"""Stable v2 enums, exit codes, and shared data helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any


class ExitCode(IntEnum):
    SUCCESS = 0
    NO_ACTIONABLE_WORK = 10
    CONTENTION = 11
    BLOCKED = 12
    INVALID_TOKEN = 13
    GATE_FAILURE = 14
    VALIDATION_ERROR = 15
    CONSISTENCY_ERROR = 16


class Lifecycle(str, Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    REVIEW = "review"
    DONE = "done"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"
    STALE = "stale"
    ATTENTION_REQUIRED = "attention_required"


class ExecutionState(str, Enum):
    READY = "ready"
    CLAIMED = "claimed"
    IDLE = "idle"
    CLOSED = "closed"
    INACTIVE = "inactive"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    BLOCKED_BARRIER = "blocked_barrier"
    BLOCKED_SCOPE = "blocked_scope"
    BLOCKED_RESOURCE = "blocked_resource"
    ORPHANED = "orphaned"
    ATTENTION_REQUIRED = "attention_required"


class TodoError(RuntimeError):
    """Stable machine-facing failure."""

    def __init__(self, code: str, message: str, exit_code: ExitCode = ExitCode.VALIDATION_ERROR, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details


@dataclass(frozen=True)
class ProjectPaths:
    repo_root: Any
    control_dir: Any
    project_file: Any
    snapshot_file: Any
    state_dir: Any
    db_file: Any
    evidence_dir: Any
