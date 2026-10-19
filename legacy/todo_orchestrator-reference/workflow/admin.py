"""Interactive owner-facing entry point for universal workflow recovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TextIO

from ..models import ExitCode, TodoError
from .foundation import canonical_json
from .recovery import RecoveryEngine, project_recovery_lock


def _require_tty(stdin: TextIO, stdout: TextIO) -> None:
    if not stdin.isatty() or not stdout.isatty():
        raise TodoError(
            "recovery_tty_required",
            "Owner recovery mutation requires interactive TTY input and output",
            ExitCode.BLOCKED,
        )


def run_owner_recovery(
    engine: RecoveryEngine,
    *,
    database_path: Path,
    reason: str,
    task_id: str | None = None,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> dict[str, object]:
    """Inspect, print, confirm, and execute one owner recovery flow."""
    _require_tty(stdin, stdout)
    with project_recovery_lock(database_path):
        plan = engine.inspect(task_id)
        stdout.write(canonical_json({"recovery_plan": plan}) + "\n")
        stdout.flush()
        if plan["blockers"]:
            raise TodoError(
                "recovery_live_work_refused",
                "Live or unproven mutable work prevents recovery",
                ExitCode.BLOCKED,
                plan,
            )
        expected = task_id or engine.project_uuid
        stdout.write(f"Type {expected} to confirm recovery: ")
        stdout.flush()
        confirmation = stdin.readline().strip()
        if confirmation != expected:
            raise TodoError(
                "recovery_confirmation_mismatch",
                "Recovery confirmation did not exactly match the project or task",
                ExitCode.BLOCKED,
            )
        result = engine.execute(plan, reason)
        stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
        stdout.flush()
        return result


def inspect_owner_recovery(engine: RecoveryEngine, task_id: str | None = None) -> dict[str, object]:
    """Non-mutating programmatic inspection for administrative smoke tests."""
    return engine.inspect(task_id)
