"""Prevent legacy scripts from becoming a second authority after v2 bootstrap."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def v2_project_exists(repo_root: Path) -> bool:
    return (repo_root / ".todo-orchestrator" / "project.json").exists()


def migration_error(repo_root: Path, legacy_command: str, replacement: str) -> int:
    payload = {
        "schema_version": 2,
        "ok": False,
        "code": "legacy_command_disabled_for_v2",
        "error": {
            "message": f"{legacy_command} cannot mutate generated Markdown after v2 bootstrap.",
            "replacement": f"python -m todo_orchestrator {replacement} --repo-root {repo_root}",
        },
    }
    print(json.dumps(payload, sort_keys=True), file=sys.stderr)
    return 2
