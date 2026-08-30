"""Non-destructive migration of model guidance and workflow front-door config."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any


LEGACY_START_MARKER = "<!-- coding-workflow:start -->"
LEGACY_END_MARKER = "<!-- coding-workflow:end -->"
START_MARKER = "<!-- project-control:start -->"
END_MARKER = "<!-- project-control:end -->"
RECORD_PATH = Path(".project-control/pcu-v1-migration.json")
ROUTING_SECTION = """<!-- project-control:start -->
## Project Control workflow

For substantial repository work, use `project-control`. Start with `next_task`,
use `inspect_task` for bounded current-task context, and use `coordinate_task`
for typed synchronization. Rich Project Control reads are secondary escalation
tools when bounded workflow context is insufficient.

Todo Orchestrator remains the transactional authority. First-class Codex agents
receive lanes and roles; local workers are optional bounded children of exactly
one parent claim and never become first-class participants.
<!-- project-control:end -->
"""


class MigrationError(RuntimeError):
    pass


def canonical_repo(repo: str | os.PathLike[str]) -> Path:
    result = subprocess.run(
        ["git", "-C", str(Path(repo).expanduser()), "rev-parse", "--show-toplevel"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False,
    )
    if result.returncode:
        raise MigrationError("repo is not a Git worktree")
    return Path(result.stdout.strip()).resolve()


def _span(content: str, start_marker: str, end_marker: str) -> tuple[int, int] | None:
    starts = [match.start() for match in re.finditer(re.escape(start_marker), content)]
    ends = [match.start() for match in re.finditer(re.escape(end_marker), content)]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise MigrationError(f"AGENTS.md has malformed {start_marker} section")
    finish = ends[0] + len(end_marker)
    while finish < len(content) and content[finish] == "\n" and finish < ends[0] + len(end_marker) + 2:
        finish += 1
    return starts[0], finish


def _owned_span(content: str) -> tuple[int, int] | None:
    current = _span(content, START_MARKER, END_MARKER)
    legacy = _span(content, LEGACY_START_MARKER, LEGACY_END_MARKER)
    if current and legacy:
        raise MigrationError("AGENTS.md has both project-control and coding-workflow sections")
    return current or legacy


def _insert_near_top(content: str, section: str = ROUTING_SECTION) -> str:
    heading = re.match(r"\A(#[^\n]*\n(?:\n)?)", content)
    position = heading.end() if heading else 0
    before, after = content[:position], content[position:]
    if before and not before.endswith("\n\n"):
        before += "\n"
    if after and not after.startswith("\n"):
        after = "\n" + after
    return before + section + after


def _updated_agents(content: str, *, remove: bool) -> tuple[str, str]:
    span = _owned_span(content)
    if remove:
        return (content if span is None else content[:span[0]] + content[span[1]:], "remove")
    if span is None:
        return _insert_near_top(content), "add"
    without = content[:span[0]] + content[span[1]:]
    return _insert_near_top(without), "refresh"


def _load_project(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError("project identity is not valid JSON") from exc
    if not isinstance(project, dict) or not isinstance(project.get("configuration"), dict):
        raise MigrationError("project identity has no configuration object")
    return project


def _atomic_write(path: Path, content: str) -> None:
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _apply_all(changes: list[tuple[Path, str | None]]) -> None:
    originals = [(path, path.read_text(encoding="utf-8") if path.exists() else None) for path, _ in changes]
    try:
        for path, content in changes:
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                _atomic_write(path, content)
    except Exception:
        for path, content in originals:
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                _atomic_write(path, content)
        raise


def migrate(repo: str | os.PathLike[str], *, apply: bool = False, remove: bool = False) -> dict[str, Any]:
    root = canonical_repo(repo)
    agents_path = root / "AGENTS.md"
    project_path = root / ".todo-orchestrator" / "project.json"
    record_path = root / RECORD_PATH
    agents_before = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    project = _load_project(project_path)
    current_record = record_path.read_text(encoding="utf-8") if record_path.exists() else None
    try:
        existing_record = json.loads(current_record) if current_record is not None else None
    except json.JSONDecodeError as exc:
        raise MigrationError("migration record is not valid JSON") from exc
    prior_front_door = project["configuration"].get("workflow_front_door") if project else None
    recorded_previous = existing_record.get("previous_front_door") if existing_record else prior_front_door

    agents_after, operation = _updated_agents(agents_before, remove=remove)
    if remove and existing_record:
        expected = existing_record.get("installed_front_door")
        if project and project["configuration"].get("workflow_front_door") == expected:
            previous = existing_record.get("previous_front_door")
            if previous is None:
                project["configuration"].pop("workflow_front_door", None)
            else:
                project["configuration"]["workflow_front_door"] = previous
    elif not remove and project:
        project["configuration"]["workflow_front_door"] = "project-control"

    project_after = json.dumps(project, indent=2, sort_keys=True) + "\n" if project else None
    project_before = project_path.read_text(encoding="utf-8") if project_path.exists() else None
    record_after: str | None = None
    if not remove:
        record_after = json.dumps({
            "schema_version": 1,
            "installed_front_door": "project-control",
            "previous_front_door": recorded_previous,
            "recognized_legacy_marker": bool(
                (existing_record or {}).get("recognized_legacy_marker")
                or LEGACY_START_MARKER in agents_before
            ),
        }, indent=2, sort_keys=True) + "\n"

    changes: list[tuple[Path, str | None]] = []
    if agents_after != agents_before:
        changes.append((agents_path, agents_after))
    if project_after != project_before:
        changes.append((project_path, project_after))
    if record_after != current_record:
        changes.append((record_path, record_after))
    if apply and changes:
        _apply_all(changes)
    changed = bool(changes)
    resulting_front_door = project["configuration"].get("workflow_front_door") if project else None
    return {
        "status": "applied" if apply and changed else "unchanged" if not changed else "dry_run",
        "repo": str(root),
        "operation": operation,
        "changed": changed,
        "changed_paths": [str(path.relative_to(root)) for path, _ in changes],
        "workflow_front_door": resulting_front_door,
        "recognized_markers": [name for name, marker in (("project-control", START_MARKER), ("coding-workflow", LEGACY_START_MARKER)) if marker in agents_before],
    }
