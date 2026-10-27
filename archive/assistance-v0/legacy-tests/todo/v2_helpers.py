from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from todo_orchestrator.projections import atomic_write_json  # noqa: E402
from todo_orchestrator.service import Service  # noqa: E402

TODO_TESTS = ROOT / "tests" / "todo"


class V2Repo:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        self.state_root = self.root / "runtime"
        self.old_override = os.environ.get("TODO_ORCHESTRATOR_STATE_DIR")
        os.environ["TODO_ORCHESTRATOR_STATE_DIR"] = str(self.state_root)
        self.service, _ = Service.bootstrap(self.root, "test-project")

    def close(self) -> None:
        if self.old_override is None:
            os.environ.pop("TODO_ORCHESTRATOR_STATE_DIR", None)
        else:
            os.environ["TODO_ORCHESTRATOR_STATE_DIR"] = self.old_override
        self.temp.cleanup()

    def apply(self, plan: dict[str, object]) -> dict[str, object]:
        path = self.root / "plan.json"
        atomic_write_json(path, plan)
        return self.service.plan_apply(str(path))

    def env(self) -> dict[str, str]:
        value = os.environ.copy()
        value["TODO_ORCHESTRATOR_STATE_DIR"] = str(self.state_root)
        value["PYTHONDONTWRITEBYTECODE"] = "1"
        return value

    def popen(self, *args: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "-m", "todo_orchestrator", *args, "--repo-root", str(self.root), "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env(),
        )

    def run(self, *args: str, check: bool = False) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        result = subprocess.run(
            [sys.executable, "-m", "todo_orchestrator", *args, "--repo-root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            env=self.env(),
            check=check,
        )
        return result, json.loads(result.stdout)


def base_plan(tasks: list[dict[str, object]], **extra) -> dict[str, object]:
    return {
        "schema_version": 2,
        "project": {"name": "test"},
        "invariants": extra.pop("invariants", []),
        "decisions": extra.pop("decisions", []),
        "locks": extra.pop("locks", []),
        "interfaces": extra.pop("interfaces", []),
        "barriers": extra.pop("barriers", []),
        "resource_classes": extra.pop("resource_classes", []),
        "tasks": tasks,
        **extra,
    }

def safe_task(task_id: str, path: str, *, priority: int = 0, **extra) -> dict[str, object]:
    return {
        "id": task_id,
        "kind": "task",
        "title": task_id,
        "objective": f"Implement {task_id}",
        "priority": priority,
        "parallel_policy": "parallel_safe",
        "scope": {"exclusive_paths": [path]},
        **extra,
    }
