from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import PlanPreviewInput
from project_control.services.planning import plan_preview

try:
    from .cellerator_fixture import cellerator_snapshot
except ImportError:
    from cellerator_fixture import cellerator_snapshot


class ObjectivePlanningTests(unittest.TestCase):
    def test_objective_context_is_graph_resolved_and_excludes_unrelated_graveyard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
            config = ProjectControlConfig(workspaces={
                "cellerator": WorkspaceConfig(authority_repository="source", repositories={"source": RepositoryConfig(root=root)})
            })
            result = plan_preview(
                config,
                cellerator_snapshot(),
                PlanPreviewInput(project="cellerator", mode="context", objective="Execution Image v2"),
            )
        self.assertEqual(result.data["resolution"]["status"], "resolved")
        task_ids = {item["id"] for item in result.data["tasks"]}
        self.assertIn("CE-ARCH-82", task_ids)
        self.assertNotIn("CP-MATH-17", task_ids)


if __name__ == "__main__":
    unittest.main()
