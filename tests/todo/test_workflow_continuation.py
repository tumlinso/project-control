from __future__ import annotations

import os
import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.workflow.capabilities import WorkflowCapabilityLocator
from todo_orchestrator.workflow.protocol import WorkflowProtocol
from todo_orchestrator.workflow.service import WorkflowKernel, assess_continuation


class WorkflowContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        plan = base_plan([
            {"id": "ROOT", "kind": "epic", "title": "root", "objective": "root"},
            safe_task("A", "src/a", priority=20),
            safe_task("B", "src/b", priority=10),
        ])
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "ROOT", "charter": {"objective": "continuation"},
            "lanes": [
                {"id": "COORD", "role": "coordinator", "tasks": []},
                {"id": "A-LANE", "parent_lane_id": "COORD", "role": "implementer", "tasks": ["A"], "workspace": {"mode": "exclusive"}},
                {"id": "B-LANE", "parent_lane_id": "COORD", "role": "implementer", "tasks": ["B"], "workspace": {"mode": "exclusive"}},
            ],
        }]
        self.repo.apply(plan)
        self.locator = WorkflowCapabilityLocator(self.repo.root / "capabilities")
        self.protocol = WorkflowProtocol(WorkflowKernel(locator=self.locator), self.locator)
        self.old_thread = os.environ.get("CODEX_THREAD_ID")

    def tearDown(self) -> None:
        if self.old_thread is None:
            os.environ.pop("CODEX_THREAD_ID", None)
        else:
            os.environ["CODEX_THREAD_ID"] = self.old_thread
        self.repo.close()

    def test_lower_ranked_exact_lane_assesses_ready_and_claims(self) -> None:
        with self.repo.service.db.read() as conn:
            assessment = assess_continuation(
                conn, run_id="RUN", lane_id="B-LANE", task_id="B", workspace_id=None,
            )
        self.assertEqual(assessment["status"], "ready", assessment)
        self.assertEqual(assessment["blockers"], [], assessment)

        os.environ["CODEX_THREAD_ID"] = "continuation-b"
        claim = self.protocol.next_task(repo_root=str(self.repo.root), run_id="RUN", task_id="B")
        self.assertEqual(claim["status"], "claimed", claim)
        self.assertEqual((claim["lane_id"], claim["task_id"]), ("B-LANE", "B"))


if __name__ == "__main__":
    unittest.main()
