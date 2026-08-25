from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..adapters.git import GitReadAdapter
from ..adapters.todo import TodoReadAdapter
from ..config import ProjectControlConfig, ensure_private_directory
from ..models import PlanPreviewInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..registry import WorkspaceRegistry
from ..snapshot import resolve_skills_root


class MutationDetected(RuntimeError):
    pass


def _app_temp_directory() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "project-control" / "tmp"
    ensure_private_directory(base)
    return base


def _manifest(root: Path) -> tuple[tuple[str, int, int], ...]:
    result = []
    for directory, directories, files in os.walk(root):
        directories[:] = [name for name in directories if name not in {".git", ".todo-orchestrator", ".ctxpp", "__pycache__", "node_modules"}]
        base = Path(directory)
        for name in files:
            path = base / name
            relative = path.relative_to(root).as_posix()
            if relative in {"todos.md", "todo-status.md"} or relative.startswith("todos/"):
                continue
            stat = path.stat()
            result.append((relative, stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(result))


def _identities(registry: WorkspaceRegistry, project: str) -> dict[str, tuple[str, str, tuple[tuple[str, int, int], ...]]]:
    workspace = registry.workspace(project)
    values = {}
    for alias in workspace.repositories:
        root = registry.repository(project, alias).root
        identity = GitReadAdapter(root).identity()
        values[alias] = (identity.commit, identity.status_fingerprint, _manifest(root))
    return values


def _todo_revision(root: Path, todo_script: Path) -> int | None:
    return TodoReadAdapter(root, todo_script).revision()


def _planning_context(snapshot: ProjectSnapshot) -> dict[str, Any]:
    tasks = snapshot.todo_tables.get("tasks", [])
    prefixes = sorted({str(item.get("id", "")).split("-", 1)[0] for item in tasks if "-" in str(item.get("id", ""))})
    return {
        "task_prefixes": prefixes,
        "ready": snapshot.todo_status.get("ready", []),
        "active_claims": snapshot.todo_status.get("active_claims", []),
        "existing_tasks": [{key: item.get(key) for key in ("id", "title", "status", "priority")} for item in tasks],
        "scope_conflicts": snapshot.todo_tables.get("ownership_scopes", []),
        "interfaces": snapshot.todo_tables.get("interfaces", []),
        "dependencies": snapshot.todo_tables.get("task_dependencies", []),
        "gates": snapshot.todo_tables.get("gates", []),
        "invariants": snapshot.todo_tables.get("invariants", []),
        "plan_schema_version": 2,
        "base_revision": snapshot.todo_revision,
        "base_commits": {key: value.commit for key, value in snapshot.repositories.items()},
    }


def plan_preview(config: ProjectControlConfig, snapshot: ProjectSnapshot, request: PlanPreviewInput) -> ToolEnvelope:
    if request.mode == "context":
        budget = 10000 if request.detail == "standard" else 6000
        return envelope("plan_preview", snapshot, bounded_payload({"mode": "context", **_planning_context(snapshot)}, budget), warnings=snapshot.warnings_for("todo"))

    registry = WorkspaceRegistry(config)
    workspace = registry.workspace(request.project)
    if not workspace.authority_repository:
        return envelope("plan_preview", snapshot, {"mode": request.mode, "valid": False}, warnings=["todo_authority_unavailable"])
    skills_root = resolve_skills_root(config, request.project)
    if skills_root is None:
        return envelope("plan_preview", snapshot, {"mode": request.mode, "valid": False}, warnings=["skills_root_unavailable"])
    todo_script = skills_root / "todo-orchestrator" / "scripts" / "todo.py"
    root = registry.repository(request.project, workspace.authority_repository).root
    proposal = request.proposal or {}
    encoded = json.dumps(proposal, sort_keys=True, indent=2).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    before_identity = _identities(registry, request.project)
    before_revision = _todo_revision(root, todo_script)
    todo_adapter = TodoReadAdapter(root, todo_script)
    temporary_path: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix="proposal-", suffix=".json", dir=_app_temp_directory())
        temporary_path = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        validate_json = todo_adapter.plan_read("validate", temporary_path)
        diff_json = todo_adapter.plan_read("diff", temporary_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    after_revision = _todo_revision(root, todo_script)
    after_identity = _identities(registry, request.project)
    if before_revision != after_revision or before_identity != after_identity:
        raise MutationDetected("plan preview changed registered project authority")
    diff_data = diff_json.get("data", {}) if isinstance(diff_json, dict) else {}
    valid = bool(validate_json.get("ok") and validate_json.get("data", {}).get("valid"))
    result: dict[str, Any] = {
        "mode": request.mode,
        "valid": valid,
        "would_add": diff_data.get("add", []),
        "would_modify": diff_data.get("update", []),
        "dependency_errors": validate_json.get("data", {}).get("dependency_errors", []),
        "scope_conflicts": validate_json.get("data", {}).get("scope_conflicts", []),
        "interface_errors": validate_json.get("data", {}).get("interface_errors", []),
        "warnings": validate_json.get("data", {}).get("warnings", []),
        "plan_digest": digest,
        "base_revision": before_revision,
        "base_commits": {alias: identity[0] for alias, identity in before_identity.items()},
        "mutation_guard": "unchanged",
    }
    if request.mode == "handoff" and valid:
        result["handoff"] = {
            "handoff_version": 1,
            "base_todo_revision": before_revision,
            "base_commits": result["base_commits"],
            "proposal_sha256": digest,
            "objective": request.objective or "",
            "proposal": proposal,
            "codex_instructions": [
                "Revalidate against the current revision before applying.",
                "Apply through coding-workflow/todo authority.",
                "Do not proceed if base identities changed materially.",
            ],
        }
    warnings = snapshot.warnings_for("todo")
    if not valid:
        warnings.append("proposal_invalid")
    return envelope("plan_preview", snapshot, bounded_payload(result, 32000), warnings=warnings)
