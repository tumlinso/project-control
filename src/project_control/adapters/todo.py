from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
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
    semantic: dict[str, Any]
    workflow: dict[str, Any]
    warnings: tuple[str, ...] = ()


class TodoReadError(CommandError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class TodoReadAdapter:
    """Read todo authority only through public non-mutating CLI operations."""

    def __init__(self, root: Path, todo_script: Path, runner: FixedCommandRunner | None = None):
        self.root = root.resolve(strict=True)
        self.todo_script = todo_script.resolve(strict=True)
        self.runner = runner or FixedCommandRunner(max_capture_bytes=8 * 1024 * 1024)

    @staticmethod
    def safe_environment() -> dict[str, str]:
        environment = {
            key: os.environ[key]
            for key in ("TODO_ORCHESTRATOR_STATE_DIR", "XDG_STATE_HOME", "HOME")
            if os.environ.get(key)
        }
        environment["TODO_ORCHESTRATOR_READ_ONLY"] = "1"
        return environment

    @staticmethod
    def _error_code(operation: str, code: object) -> str:
        if code == "project_not_bootstrapped":
            return "todo_project_not_bootstrapped"
        if code == "todo_state_unavailable":
            return "todo_state_unavailable"
        if code == "internal_error":
            return f"todo_{operation}_unavailable"
        if isinstance(code, str) and code:
            return code if code.startswith("todo_") else f"todo_{code}"
        return f"todo_{operation}_unavailable"

    def _call(self, operation: str, *arguments: str, timeout: float = 8.0) -> dict[str, Any]:
        allowed = {"status", "ready", "export", "explain", "changes"}
        if operation not in allowed:
            raise ValueError("todo operation is not read-only allowlisted")
        argv = [sys.executable, str(self.todo_script), operation, "--repo-root", str(self.root), *arguments, "--json"]
        command = self.runner.run(
            argv, cwd=self.root, timeout=timeout, env=self.safe_environment(), check=False
        )
        try:
            result = command.json()
        except CommandError as exc:
            raise TodoReadError(f"todo_{operation}_invalid_output") from exc
        result = redact(result)
        if not result.get("ok"):
            raise TodoReadError(self._error_code(operation, result.get("code")))
        return result

    def _semantic_call(self, action: str, *arguments: str, timeout: float = 12.0) -> dict[str, Any]:
        if action not in {"state", "anchor", "delta", "workflow"}:
            raise ValueError("todo semantic operation is not read-only allowlisted")
        argv = [
            sys.executable, str(self.todo_script), "semantic", action,
            "--repo-root", str(self.root), *arguments, "--json",
        ]
        command = self.runner.run(
            argv, cwd=self.root, timeout=timeout, env=self.safe_environment(), check=False
        )
        try:
            result = redact(command.json())
        except CommandError as exc:
            raise TodoReadError("todo_semantic_unavailable") from exc
        if not result.get("ok"):
            raise TodoReadError("todo_semantic_unavailable")
        return result

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

    def semantic_state(self, *arguments: str) -> dict[str, Any]:
        return self._semantic_call("state", *arguments).get("data", {})

    def semantic_anchor(self, *arguments: str) -> dict[str, Any]:
        return self._semantic_call("anchor", *arguments).get("data", {})

    def semantic_delta(self, *arguments: str) -> dict[str, Any]:
        return self._semantic_call("delta", *arguments, timeout=20.0).get("data", {})

    def semantic_workflow(self) -> dict[str, Any]:
        return self._semantic_call("workflow").get("data", {})

    def plan_read(self, operation: str, proposal_file: Path, *, timeout: float = 15.0) -> dict[str, Any]:
        if operation not in {"validate", "diff"}:
            raise ValueError("todo plan operation is not read-only allowlisted")
        command = self.runner.run(
            [sys.executable, str(self.todo_script), "plan", operation, "--file", str(proposal_file), "--repo-root", str(self.root), "--json"],
            cwd=self.root, timeout=timeout, env=self.safe_environment(), check=False,
        )
        try:
            return redact(command.json())
        except CommandError:
            return {"ok": False, "code": f"plan_{operation}_invalid_output", "data": {"valid": False}}

    def revision(self) -> int | None:
        value = self.status().get("data", {}).get("project_revision")
        return value if isinstance(value, int) else None

    def _authority_root(self) -> Path:
        current = self.root
        while True:
            if (current / ".todo-orchestrator" / "project.json").is_file():
                return current
            if current.parent == current:
                raise TodoReadError("todo_not_configured")
            current = current.parent

    def _project_uuid(self, authority_root: Path) -> str | None:
        try:
            value = json.loads((authority_root / ".todo-orchestrator" / "project.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        project_uuid = value.get("project_uuid") if isinstance(value, dict) else None
        return project_uuid if isinstance(project_uuid, str) else None

    def state_roots(self) -> tuple[Path, ...]:
        """Resolve only state roots permitted by the todo v2 location contract."""
        authority_root = self._authority_root()
        project_uuid = self._project_uuid(authority_root)
        roots: list[Path] = []
        override = os.environ.get("TODO_ORCHESTRATOR_STATE_DIR")
        if override and project_uuid:
            roots.append(Path(override).expanduser().resolve() / project_uuid)
        if project_uuid:
            try:
                raw = subprocess.run(
                    ["git", "-C", str(authority_root), "rev-parse", "--git-common-dir"],
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    timeout=5.0, check=True, text=True,
                ).stdout.strip()
                common = Path(raw)
                if not common.is_absolute():
                    common = authority_root / common
                roots.append(common.resolve() / "todo-orchestrator" / project_uuid)
            except (OSError, subprocess.SubprocessError):
                pass
            xdg = os.environ.get("XDG_STATE_HOME")
            fallback = Path(xdg).expanduser() if xdg else Path(os.environ.get("HOME", str(Path.home()))) / ".local" / "state"
            roots.append(fallback.resolve() / "todo-orchestrator" / project_uuid)
        return tuple(roots)

    def _exported_state(self) -> dict[str, Any]:
        export = self._call("export")
        data = export.get("data", {})
        inline_state = data.get("state") if isinstance(data, dict) else None
        if isinstance(inline_state, dict):
            return redact(inline_state)
        snapshot_value = data.get("snapshot") if isinstance(data, dict) else None
        if not isinstance(snapshot_value, str):
            raise TodoReadError("todo_export_unavailable")
        path = Path(snapshot_value).resolve(strict=True)
        authority_root = self._authority_root()
        expected_snapshot = (authority_root / ".todo-orchestrator" / "state.snapshot.json").resolve(strict=True)
        if path != expected_snapshot:
            raise CommandError("todo export escaped project authority")
        if path.stat().st_size > 8 * 1024 * 1024:
            raise CommandError("todo snapshot exceeds read limit")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise CommandError("todo snapshot is invalid")
        return redact(value)

    def observe(self) -> TodoObservation:
        last_status: dict[str, Any] = {}
        last_state: dict[str, Any] = {}
        last_semantic: dict[str, Any] = {}
        last_workflow: dict[str, Any] = {}
        for attempt in range(2):
            status = self.status()
            data = status.get("data", {})
            try:
                state = self._exported_state()
            except TodoReadError as exc:
                revision = data.get("project_revision")
                return TodoObservation(
                    revision if isinstance(revision, int) else None,
                    self._project_uuid(self._authority_root()),
                    data,
                    {},
                    {},
                    {},
                    (exc.code,),
                )
            except (CommandError, OSError, ValueError, json.JSONDecodeError):
                revision = data.get("project_revision")
                return TodoObservation(
                    revision if isinstance(revision, int) else None,
                    self._project_uuid(self._authority_root()),
                    data,
                    {},
                    {},
                    {},
                    ("todo_snapshot_unavailable",),
                )
            status_revision = data.get("project_revision")
            state_revision = state.get("project_revision")
            semantic_warning: tuple[str, ...] = ()
            try:
                semantic = self.semantic_state("--current-only")
            except TodoReadError:
                semantic = {}
                semantic_warning = ("todo_semantic_unavailable",)
            workflow_warning: tuple[str, ...] = ()
            try:
                workflow = self.semantic_workflow()
                if workflow.get("available") is False:
                    workflow_warning = ("todo_workflow_semantic_unavailable",)
            except TodoReadError:
                workflow = {}
                workflow_warning = ("todo_workflow_semantic_unavailable",)
            semantic_revision = semantic.get("revision") if semantic else status_revision
            workflow_revision = workflow.get("revision") if workflow else status_revision
            last_status, last_state, last_semantic, last_workflow = data, state, semantic, workflow
            if isinstance(status_revision, int) and status_revision == state_revision == semantic_revision == workflow_revision:
                project = state.get("project", {})
                project_uuid = project.get("project_uuid") if isinstance(project, dict) else None
                return TodoObservation(
                    status_revision, project_uuid, data, state, semantic, workflow,
                    tuple(dict.fromkeys([*semantic_warning, *workflow_warning])),
                )
        revision = last_status.get("project_revision")
        project = last_state.get("project", {})
        project_uuid = project.get("project_uuid") if isinstance(project, dict) else None
        return TodoObservation(
            revision if isinstance(revision, int) else None,
            project_uuid if isinstance(project_uuid, str) else None,
            last_status,
            {},
            last_semantic,
            last_workflow,
            ("todo_observation_raced",),
        )

    @staticmethod
    def proposal_digest(proposal: dict[str, Any]) -> str:
        encoded = json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
