"""Project discovery, identity, and runtime-state path resolution."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import SCHEMA_VERSION
from .models import ProjectPaths, TodoError


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def find_repo_root(start: str | Path = ".") -> Path:
    current = Path(start).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".todo-orchestrator" / "project.json").exists():
            return candidate
    result = subprocess.run(
        ["git", "-C", str(current), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return Path(result.stdout.strip()).resolve()
    return current


def read_project(repo_root: Path) -> dict[str, object]:
    path = repo_root / ".todo-orchestrator" / "project.json"
    if not path.exists():
        raise TodoError("project_not_bootstrapped", f"No v2 project identity at {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TodoError("invalid_project_identity", str(exc)) from exc
    if int(data.get("schema_version", 0)) != SCHEMA_VERSION or not data.get("project_uuid"):
        raise TodoError("invalid_project_identity", "project.json is missing the v2 schema version or UUID")
    return data


def _git_common_dir(repo_root: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    value = Path(result.stdout.strip())
    return (repo_root / value).resolve() if not value.is_absolute() else value.resolve()


def project_paths(repo_root: str | Path = ".", *, require_identity: bool = True) -> ProjectPaths:
    root = find_repo_root(repo_root)
    control = root / ".todo-orchestrator"
    project_file = control / "project.json"
    if require_identity:
        project = read_project(root)
        project_uuid = str(project["project_uuid"])
    else:
        project_uuid = "uninitialized"
    override = os.environ.get("TODO_ORCHESTRATOR_STATE_DIR")
    if override:
        state_dir = Path(override).expanduser().resolve() / project_uuid
    else:
        common = _git_common_dir(root)
        if common is not None:
            state_dir = common / "todo-orchestrator" / project_uuid
        else:
            xdg = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
            state_dir = xdg / "todo-orchestrator" / project_uuid
    return ProjectPaths(
        repo_root=root,
        control_dir=control,
        project_file=project_file,
        snapshot_file=control / "state.snapshot.json",
        state_dir=state_dir,
        db_file=state_dir / "state.sqlite3",
        evidence_dir=state_dir / "evidence",
    )


def create_project_identity(repo_root: str | Path, name: str | None = None) -> tuple[ProjectPaths, dict[str, object]]:
    root = find_repo_root(repo_root)
    control = root / ".todo-orchestrator"
    path = control / "project.json"
    if path.exists():
        project = read_project(root)
        return project_paths(root), project
    project = {
        "schema_version": SCHEMA_VERSION,
        "project_uuid": str(uuid.uuid4()),
        "project_name": name or root.name,
        "created_at": utc_now(),
        "configuration": {
            "claim_lease_seconds": 7200,
            "resource_lease_seconds": 300,
            "busy_timeout_ms": 5000,
            "context_budget_bytes": 12000,
            "legacy_review_days": {"planned": 3, "in_progress": 3, "blocked": 7, "stale": 3},
        },
    }
    control.mkdir(parents=True, exist_ok=True)
    from .projections import atomic_write_json

    atomic_write_json(path, project)
    return project_paths(root), project
