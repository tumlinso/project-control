from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..security import redact
from ..subprocesses import (
    CommandCaptureError,
    CommandError,
    CommandTimeoutError,
    CommandUnavailableError,
    FixedCommandRunner,
)


TODO_TIMEOUTS = {
    "status": 12.0,
    "export": 45.0,
    "state": 30.0,
    "anchor": 30.0,
    "delta": 45.0,
    "workflow": 60.0,
    "plan": 30.0,
}


def _observed_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AuthorityComponentObservation:
    status: str
    operation: str
    revision: int | None
    read_authority_fingerprint: str | None
    project_uuid: str | None
    observed_at: str
    source_identity: str
    error_code: str | None
    revision_skew: int | None
    data: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "operation": self.operation,
            "revision": self.revision,
            "read_authority_fingerprint": self.read_authority_fingerprint,
            "project_uuid": self.project_uuid,
            "observed_at": self.observed_at,
            "source_identity": self.source_identity,
            "error_code": self.error_code,
            "revision_skew": self.revision_skew,
        }


@dataclass(frozen=True)
class TodoObservation:
    revision: int | None
    project_uuid: str | None
    status: dict[str, Any]
    state: dict[str, Any]
    semantic: dict[str, Any]
    workflow: dict[str, Any]
    components: dict[str, AuthorityComponentObservation]
    consistency: str = "unknown"
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
            if operation == "workflow":
                return "todo_workflow_semantic_unavailable"
            if operation in {"state", "semantic_state"}:
                return "todo_semantic_unavailable"
            return f"todo_{operation}_unavailable"
        if code in {"unsupported_command", "invalid_command", "invalid_choice", "unknown_command"}:
            return "todo_entrypoint_incompatible"
        if code in {"database_busy", "database_locked"}:
            return "todo_read_database_busy"
        if code in {"permission_denied", "read_permission_denied"}:
            return "todo_read_permission_denied"
        if code in {"project_identity_mismatch", "project_uuid_mismatch"}:
            return "todo_project_identity_mismatch"
        if isinstance(code, str) and code:
            return code if code.startswith("todo_") else f"todo_{code}"
        return f"todo_{operation}_unavailable"

    def _run_json(self, argv: list[str], *, root: Path, operation: str, timeout: float) -> dict[str, Any]:
        try:
            command = self.runner.run(
                argv, cwd=root, timeout=timeout, env=self.safe_environment(), check=False
            )
        except CommandTimeoutError as exc:
            raise TodoReadError(f"todo_{operation}_timeout") from exc
        except CommandCaptureError as exc:
            raise TodoReadError(f"todo_{operation}_output_too_large") from exc
        except CommandUnavailableError as exc:
            raise TodoReadError("todo_tool_identity_stale") from exc
        except CommandError as exc:
            raise TodoReadError(f"todo_{operation}_unavailable") from exc
        try:
            result = redact(command.json())
        except CommandError as exc:
            raise TodoReadError(f"todo_{operation}_invalid_output") from exc
        if not result.get("ok"):
            code = self._error_code(operation, result.get("code"))
            stderr = command.stderr.lower()
            if "permission denied" in stderr:
                code = "todo_read_permission_denied"
            elif "database is locked" in stderr or "database is busy" in stderr:
                code = "todo_read_database_busy"
            raise TodoReadError(code)
        return result

    def _call_at(self, root: Path, operation: str, *arguments: str, timeout: float | None = None) -> dict[str, Any]:
        allowed = {"status", "ready", "export", "explain", "changes"}
        if operation not in allowed:
            raise ValueError("todo operation is not read-only allowlisted")
        argv = [sys.executable, str(self.todo_script), operation, "--repo-root", str(root), *arguments, "--json"]
        return self._run_json(
            argv, root=root, operation=operation,
            timeout=timeout if timeout is not None else TODO_TIMEOUTS.get(operation, 20.0),
        )

    def _call(self, operation: str, *arguments: str, timeout: float | None = None) -> dict[str, Any]:
        return self._call_at(self.root, operation, *arguments, timeout=timeout)

    def _semantic_call_at(self, root: Path, action: str, *arguments: str, timeout: float | None = None) -> dict[str, Any]:
        if action not in {"state", "anchor", "delta", "workflow"}:
            raise ValueError("todo semantic operation is not read-only allowlisted")
        argv = [
            sys.executable, str(self.todo_script), "semantic", action,
            "--repo-root", str(root), *arguments, "--json",
        ]
        return self._run_json(
            argv, root=root, operation=action,
            timeout=timeout if timeout is not None else TODO_TIMEOUTS[action],
        )

    def _semantic_call(self, action: str, *arguments: str, timeout: float | None = None) -> dict[str, Any]:
        return self._semantic_call_at(self.root, action, *arguments, timeout=timeout)

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
        return self._semantic_call("delta", *arguments).get("data", {})

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

    def _authority_root(self, start: Path | None = None) -> Path:
        current = start or self.root
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

    @staticmethod
    def _git_common_dir(root: Path) -> Path | None:
        try:
            raw = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=5.0, check=True, text=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        value = Path(raw)
        return (root / value).resolve() if not value.is_absolute() else value.resolve()

    def _source_identity(self, root: Path, project_uuid: str | None) -> str:
        common = self._git_common_dir(root)
        material = f"{common or root.resolve()}\0{project_uuid or '-'}".encode()
        return "todo-authority-" + hashlib.sha256(material).hexdigest()[:16]

    def authority_candidates(self) -> tuple[Path, ...]:
        """Configured authority first, then verified same-repository worktrees with the same UUID."""
        authority = self._authority_root()
        expected_uuid = self._project_uuid(authority)
        common = self._git_common_dir(authority)
        candidates: list[Path] = [authority]
        if not common or not expected_uuid:
            return tuple(candidates)
        try:
            output = subprocess.run(
                ["git", "-C", str(authority), "worktree", "list", "--porcelain"],
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=8.0, check=True, text=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return tuple(candidates)
        roots = sorted(
            Path(line.removeprefix("worktree ")).resolve()
            for line in output.splitlines() if line.startswith("worktree ")
        )
        for root in roots:
            if root == authority or self._git_common_dir(root) != common:
                continue
            manifest = root / ".todo-orchestrator" / "project.json"
            if not manifest.is_file() or self._project_uuid(root) != expected_uuid:
                continue
            candidates.append(root)
        return tuple(candidates)

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

    def _exported_state(self, root: Path | None = None) -> dict[str, Any]:
        selected = root or self.root
        export = self._call_at(selected, "export")
        data = export.get("data", {})
        inline_state = data.get("state") if isinstance(data, dict) else None
        if isinstance(inline_state, dict):
            return redact(inline_state)
        snapshot_value = data.get("snapshot") if isinstance(data, dict) else None
        if not isinstance(snapshot_value, str):
            raise TodoReadError("todo_export_unavailable")
        path = Path(snapshot_value).resolve(strict=True)
        authority_root = self._authority_root(selected)
        expected_snapshot = (authority_root / ".todo-orchestrator" / "state.snapshot.json").resolve(strict=True)
        if path != expected_snapshot:
            raise CommandError("todo export escaped project authority")
        if path.stat().st_size > 8 * 1024 * 1024:
            raise CommandError("todo snapshot exceeds read limit")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise CommandError("todo snapshot is invalid")
        return redact(value)

    @staticmethod
    def _revision(data: dict[str, Any], operation: str) -> int | None:
        value = data.get("project_revision") if operation in {"status", "export"} else data.get("revision")
        return value if isinstance(value, int) else None

    def _component(
        self,
        operation: str,
        root: Path,
        project_uuid: str | None,
        call,
    ) -> AuthorityComponentObservation:
        observed = _observed_at()
        source = self._source_identity(root, project_uuid)
        try:
            data = call()
        except TodoReadError as exc:
            return AuthorityComponentObservation(
                "unavailable", operation, None, None, project_uuid, observed, source, exc.code, None, {},
            )
        except (CommandError, OSError, ValueError, json.JSONDecodeError):
            return AuthorityComponentObservation(
                "unavailable", operation, None, None, project_uuid, observed, source,
                f"todo_{operation}_unavailable", None, {},
            )
        fingerprint = data.get("read_authority_fingerprint")
        component_uuid = data.get("project_uuid")
        if not isinstance(component_uuid, str):
            project = data.get("project", {})
            component_uuid = project.get("project_uuid") if isinstance(project, dict) else project_uuid
        if project_uuid and component_uuid and component_uuid != project_uuid:
            return AuthorityComponentObservation(
                "unavailable", operation, self._revision(data, operation),
                fingerprint if isinstance(fingerprint, str) else None,
                component_uuid, observed, source, "todo_project_identity_mismatch", None, data,
            )
        unavailable_reason = data.get("reason") if data.get("available") is False else None
        error = None
        status = "available"
        if unavailable_reason:
            status = "unavailable"
            error = "todo_workflow_semantic_unavailable" if operation == "workflow" else f"todo_{operation}_unavailable"
        return AuthorityComponentObservation(
            status, operation, self._revision(data, operation),
            fingerprint if isinstance(fingerprint, str) else None,
            component_uuid if isinstance(component_uuid, str) else project_uuid,
            observed, source, error, None, data,
        )

    def observe(self) -> TodoObservation:
        authority = self._authority_root()
        project_uuid = self._project_uuid(authority)
        candidates = self.authority_candidates()

        workflow_component: AuthorityComponentObservation | None = None
        selected = authority
        for candidate in candidates:
            candidate_component = self._component(
                "workflow", candidate, project_uuid,
                lambda candidate=candidate: self._semantic_call_at(candidate, "workflow").get("data", {}),
            )
            if workflow_component is None:
                workflow_component = candidate_component
            if candidate_component.status == "available":
                workflow_component = candidate_component
                selected = candidate
                break
        assert workflow_component is not None

        semantic_component = self._component(
            "semantic_state", selected, project_uuid,
            lambda: self._semantic_call_at(selected, "state", "--current-only").get("data", {}),
        )
        status_component = self._component(
            "status", selected, project_uuid,
            lambda: self._call_at(selected, "status").get("data", {}),
        )
        export_component = self._component(
            "export", selected, project_uuid,
            lambda: self._exported_state(selected),
        )
        components = {
            "todo_workflow": workflow_component,
            "todo_semantic_state": semantic_component,
            "todo_status": status_component,
            "todo_export": export_component,
        }

        reference = workflow_component.revision or semantic_component.revision or status_component.revision or export_component.revision
        normalized: dict[str, AuthorityComponentObservation] = {}
        revisions: set[int] = set()
        for name, component in components.items():
            if component.revision is not None:
                revisions.add(component.revision)
            skew = component.revision - reference if reference is not None and component.revision is not None else None
            status = component.status
            error = component.error_code
            if skew and component.status == "available":
                status = "raced"
                if name == "todo_export":
                    error = "todo_export_raced"
            normalized[name] = AuthorityComponentObservation(
                status, component.operation, component.revision, component.read_authority_fingerprint,
                component.project_uuid, component.observed_at, component.source_identity, error, skew, component.data,
            )

        warnings = [item.error_code for item in normalized.values() if item.error_code]
        if len(revisions) > 1:
            warnings.append("todo_observation_skew")
        consistency = "consistent" if len(revisions) <= 1 else "skewed"
        status_data = normalized["todo_status"].data
        state_data = normalized["todo_export"].data
        semantic_data = normalized["todo_semantic_state"].data
        workflow_data = normalized["todo_workflow"].data
        return TodoObservation(
            reference,
            project_uuid,
            status_data,
            state_data,
            semantic_data,
            workflow_data,
            normalized,
            consistency,
            tuple(dict.fromkeys(str(item) for item in warnings if item)),
        )

    @staticmethod
    def proposal_digest(proposal: dict[str, Any]) -> str:
        encoded = json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
