"""One disposable ordinary-work journey through Project Control's public MCP tools."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class PCE2WorkflowJourneyTests(unittest.TestCase):
    def test_public_mcp_entry_bind_validate_repeat_and_complete(self) -> None:
        child = r'''
import asyncio
import json
import os
from pathlib import Path

from project_control.app import create_mcp
from project_control.config import ProjectControlConfig
from v2_helpers import V2Repo, base_plan, safe_task

repo = V2Repo()
try:
    root = repo.root
    os.environ["XDG_STATE_HOME"] = str(root / "xdg-state")
    target = root / "src" / "a" / "unit.py"
    target.parent.mkdir(parents=True)
    target.write_text("value = 1\n", encoding="utf-8")
    repo.apply(base_plan([safe_task("A", "src/a", priority=1)]))
    manager = create_mcp(ProjectControlConfig(), profile="codex")._tool_manager
    def call(name, arguments):
        return asyncio.run(manager.call_tool(name, arguments))
    claimed = call("next_task", {"repo_root": str(root), "task_id": "A"})
    assert claimed["status"] == "claimed", claimed
    assert claimed["recommended_next_call"] is None, claimed
    handle = claimed["workflow_handle"]
    gate = {"id": "A-EXISTS", "type": "file_exists", "path": "src/a/unit.py", "required": True}
    bound = call("coordinate_task", {"workflow_handle": handle, "action": "bind_required_gates", "payload": {"gates": [gate]}})
    assert bound.get("bound_gate_ids") == ["A-EXISTS"], bound
    revision = repo.service.db.revision()
    repeated = call("coordinate_task", {"workflow_handle": handle, "action": "bind_required_gates", "payload": {"gates": [gate]}})
    assert repeated["unchanged_gate_ids"] == ["A-EXISTS"], repeated
    assert repo.service.db.revision() == revision
    validated = call("coordinate_task", {"workflow_handle": handle, "action": "run_gates", "payload": {"required": True}})
    assert validated["status"] == "claimed", validated
    completed = call("finish_task", {"workflow_handle": handle, "action": "complete", "disposition": "implemented"})
    assert completed["status"] == "idle", completed
    print(json.dumps({"entry": claimed["status"], "complete": completed["status"]}, sort_keys=True))
finally:
    repo.close()
'''
        environment = dict(os.environ)
        # Development uses the configured Skills source. A manifest-backed
        # candidate must select its own frozen installed package unchanged.
        if not environment.get("PROJECT_CONTROL_RELEASE_MANIFEST"):
            skills_root = environment.get("PROJECT_CONTROL_SKILLS_ROOT")
            self.assertTrue(skills_root, "development journey requires PROJECT_CONTROL_SKILLS_ROOT")
            environment["PYTHONPATH"] = os.pathsep.join([
                str(Path(skills_root) / "todo-orchestrator"),
                environment.get("PYTHONPATH", ""),
            ])
        skills_root = environment.get("PROJECT_CONTROL_SKILLS_ROOT")
        self.assertTrue(skills_root, "journey fixture requires PROJECT_CONTROL_SKILLS_ROOT")
        environment["PYTHONPATH"] = os.pathsep.join([
            str(Path(skills_root) / "todo-orchestrator" / "tests"),
            environment.get("PYTHONPATH", ""),
        ])
        completed = subprocess.run(
            [sys.executable, "-c", child], cwd=Path(__file__).parents[1], env=environment,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertEqual(json.loads(completed.stdout), {"complete": "idle", "entry": "claimed"})


if __name__ == "__main__":
    unittest.main()
