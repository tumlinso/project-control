#!/usr/bin/env python3
"""Print or install a narrow Project Control Todo-state systemd drop-in.

The utility only discovers already bootstrapped Todo projects registered in
Project Control. It never initializes a project, creates a Todo ledger, or
restarts a systemd unit. Use ``scripts/pc-dev python`` to run it from checkout
source.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from project_control.config import ProjectControlConfig, config_path, load_config
from todo_orchestrator.config import project_paths, read_project
from todo_orchestrator.models import TodoError


DEFAULT_DROP_IN = Path.home() / ".config" / "systemd" / "user" / "project-control.service.d" / "90-todo-state-paths.conf"


@dataclass(frozen=True)
class RepositoryState:
    workspace: str
    repository: str
    repo_root: str
    state_dir: str | None
    status: str
    reason: str | None = None


def discover_state_directories(
    config: ProjectControlConfig,
    *,
    project_id: str | None = None,
) -> tuple[list[Path], list[RepositoryState]]:
    """Resolve existing canonical Todo state dirs for configured bootstrapped repos."""

    if project_id is not None and project_id not in config.workspaces:
        raise ValueError(f"Unknown configured workspace: {project_id}")
    directories: set[Path] = set()
    reports: list[RepositoryState] = []
    seen_roots: set[Path] = set()
    selected = (
        {project_id: config.workspaces[project_id]}
        if project_id is not None
        else config.workspaces
    )
    for workspace_id, workspace in sorted(selected.items()):
        for repository_id, repository in sorted(workspace.repositories.items()):
            repo_root = repository.root.resolve()
            if repo_root in seen_roots:
                continue
            seen_roots.add(repo_root)
            try:
                project_uuid = read_project(repo_root).get("project_uuid")
                if not isinstance(project_uuid, str) or str(uuid.UUID(project_uuid)) != project_uuid:
                    raise ValueError("project_uuid is not a canonical UUID")
                paths = project_paths(repo_root, require_identity=True)
            except TodoError as exc:
                if exc.code == "project_not_bootstrapped":
                    reports.append(RepositoryState(
                        workspace_id, repository_id, str(repo_root), None, "skipped", "project_not_bootstrapped"
                    ))
                    continue
                raise RuntimeError(f"Cannot resolve Todo state for {repo_root}: {exc.code}: {exc.message}") from exc
            except (ValueError, TypeError, AttributeError) as exc:
                raise RuntimeError(f"Cannot resolve Todo state for {repo_root}: invalid project UUID") from exc

            try:
                state_dir = paths.state_dir.resolve(strict=True)
                db_file = paths.db_file.resolve(strict=True)
            except OSError:
                reports.append(RepositoryState(
                    workspace_id, repository_id, str(repo_root), str(paths.state_dir), "skipped", "todo_state_missing"
                ))
                continue
            if not state_dir.is_dir() or db_file.parent != state_dir or not db_file.is_file():
                reports.append(RepositoryState(
                    workspace_id, repository_id, str(repo_root), str(state_dir), "skipped", "todo_state_missing_or_noncanonical"
                ))
                continue
            directories.add(state_dir)
            reports.append(RepositoryState(
                workspace_id, repository_id, str(repo_root), str(state_dir), "included"
            ))
    return sorted(directories, key=lambda path: str(path)), reports


def _systemd_quote(path: Path) -> str:
    value = str(path)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_drop_in(directories: Iterable[Path]) -> str:
    paths = sorted({Path(path).resolve() for path in directories}, key=str)
    if not paths:
        return ""
    lines = [
        "# Managed by project-control/scripts/configure_source_state_paths.py",
        "# Grants SQLite WAL bookkeeping only in existing canonical Todo state directories.",
        "[Service]",
    ]
    lines.extend(f"ReadWritePaths={_systemd_quote(path)}" for path in paths)
    return "\n".join(lines) + "\n"


def install_drop_in(target: Path, content: str) -> str:
    """Atomically install the drop-in, preserving one exact backup if replaced."""

    if not content.strip():
        return "no_existing_todo_state"
    target = target.expanduser()
    if target.is_symlink():
        raise RuntimeError(f"Refusing to replace symlink drop-in: {target}")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if target.is_file() and target.read_text(encoding="utf-8") == content:
        return "already_current"

    backup: Path | None = None
    original_mode = 0o600
    if target.exists():
        if not target.is_file():
            raise RuntimeError(f"Drop-in target exists and is not a regular file: {target}")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = target.with_name(f"{target.name}.bak-{timestamp}")
        suffix = 1
        while backup.exists():
            backup = target.with_name(f"{target.name}.bak-{timestamp}-{suffix}")
            suffix += 1
        original_mode = stat.S_IMODE(target.stat().st_mode)
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, original_mode)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(target.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                backup.unlink()
            except OSError:
                pass
            raise

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as stream:
            temporary_name = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, original_mode)
        os.replace(temporary_name, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if temporary_name:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass
        raise
    return f"installed_with_backup:{backup}" if backup else "installed_new"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None, help="Project Control TOML config (default: configured XDG path)")
    parser.add_argument("--drop-in", type=Path, default=DEFAULT_DROP_IN, help=f"user systemd drop-in path (default: {DEFAULT_DROP_IN})")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--project", help="limit discovery and any write to one configured workspace ID")
    selection.add_argument("--all-projects", action="store_true", help="explicitly select every registered workspace")
    parser.add_argument("--apply", action="store_true", help="write the selected drop-in (requires --project or --all-projects); does not restart systemd")
    args = parser.parse_args(argv)

    try:
        if args.apply and not (args.project or args.all_projects):
            raise ValueError("--apply requires an explicit --project or --all-projects selector")
        config = load_config(args.config or config_path())
        directories, reports = discover_state_directories(config, project_id=args.project)
        content = render_drop_in(directories)
        result: dict[str, object] = {
            "status": "ready" if directories else "no_existing_todo_state",
            "mode": "apply" if args.apply else "dry_run",
            "drop_in": str(args.drop_in.expanduser()),
            "project_filter": args.project,
            "selection": (
                "project" if args.project
                else "all_projects_explicit" if args.all_projects
                else "all_registered_dry_run_default"
            ),
            "state_directories": [str(path) for path in directories],
            "repositories": [asdict(report) for report in reports],
            "content": content,
        }
        if args.apply:
            result["write_result"] = install_drop_in(args.drop_in, content)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
