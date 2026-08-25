from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..security import redact
from ..subprocesses import CommandError, FixedCommandRunner


@dataclass(frozen=True)
class TodoObservation:
    revision: int | None
    project_uuid: str | None
    status: dict[str, Any]
    state: dict[str, Any]
    warnings: tuple[str, ...] = ()


class TodoReadAdapter:
    """Read todo authority only through public non-mutating CLI operations."""

    def __init__(self, root: Path, todo_script: Path, runner: FixedCommandRunner | None = None):
        self.root = root.resolve(strict=True)
        self.todo_script = todo_script.resolve(strict=True)
        self.runner = runner or FixedCommandRunner()

    def _call(self, operation: str, *arguments: str, timeout: float = 8.0) -> dict[str, Any]:
        allowed = {"status", "ready", "export", "explain", "changes"}
        if operation not in allowed:
            raise ValueError("todo operation is not read-only allowlisted")
        argv = ["python", str(self.todo_script), operation, "--repo-root", str(self.root), *arguments, "--json"]
        result = self.runner.run(argv, cwd=self.root, timeout=timeout).json()
        if not result.get("ok"):
            raise CommandError(f"todo {operation} unavailable")
        return redact(result)

    def status(self) -> dict[str, Any]:
        return self._call("status")

    def ready(self) -> dict[str, Any]:
        return self._call("ready")

    def explain(self, task_id: str) -> dict[str, Any]:
        if not task_id or task_id.startswith("-"):
            raise ValueError("invalid task ID")
        return self._call("explain", task_id)

    def changes(self, since: int) -> dict[str, Any]:
        if since < 0:
            raise ValueError("revision must be non-negative")
        return self._call("changes", "--since", str(since))

    def _exported_state(self) -> dict[str, Any]:
        export = self._call("export")
        snapshot_value = export.get("data", {}).get("snapshot")
        if not isinstance(snapshot_value, str):
            raise CommandError("todo export did not provide a snapshot")
        path = Path(snapshot_value).resolve(strict=True)
        git_common = (self.root / ".git").resolve(strict=True)
        project_state = (self.root / ".todo-orchestrator").resolve(strict=True)
        if not (path.is_relative_to(git_common) or path.is_relative_to(project_state)):
            raise CommandError("todo export escaped project authority")
        if path.stat().st_size > 8 * 1024 * 1024:
            raise CommandError("todo snapshot exceeds read limit")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise CommandError("todo snapshot is invalid")
        return redact(value)

    def observe(self) -> TodoObservation:
        status = self.status()
        data = status.get("data", {})
        warnings: list[str] = []
        try:
            state = self._exported_state()
        except (CommandError, OSError, ValueError, json.JSONDecodeError):
            state = {}
            warnings.append("todo_snapshot_unavailable")
        revision = data.get("project_revision")
        if not isinstance(revision, int):
            revision = state.get("revision") if isinstance(state.get("revision"), int) else None
        project_uuid = state.get("project_uuid") or state.get("project", {}).get("uuid")
        return TodoObservation(revision, project_uuid, data, state, tuple(warnings))

    @staticmethod
    def proposal_digest(proposal: dict[str, Any]) -> str:
        encoded = json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
