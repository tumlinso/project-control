from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import PerformanceStatusInput
from project_control.services.performance import performance_status
from project_control.services.overview import project_overview

try:
    from .cellerator_fixture import cellerator_snapshot
except ImportError:
    from cellerator_fixture import cellerator_snapshot


class CurrentPerformanceTests(unittest.TestCase):
    def test_current_architecture_evidence_precedes_historical_campaigns(self) -> None:
        snapshot = cellerator_snapshot()
        result = performance_status(snapshot, PerformanceStatusInput(project="cellerator"))
        self.assertEqual(result.data["latest_current_compatible_measurements"][0]["id"], "current-ce-result")
        self.assertEqual(result.data["current_material_regressions"], [])
        self.assertEqual(result.data["stale_or_superseded_evidence_counts"]["evidence"], 1)
        self.assertNotIn("campaigns_and_facts", result.data)
        self.assertFalse(result.data["execution_performed"])

    def test_source_incompatible_result_is_historical_and_current_regression_reaches_overview(self) -> None:
        snapshot = cellerator_snapshot()
        snapshot.cuda["results"][1]["source"] = {"commit": "b" * 40}
        incompatible = performance_status(snapshot, PerformanceStatusInput(project="cellerator"))
        self.assertEqual(incompatible.data["latest_current_compatible_measurements"], [])

        snapshot.cuda["results"][1].pop("source")
        snapshot.cuda["results"][1]["classification"] = "material-regression"
        overview = project_overview(snapshot)
        self.assertEqual(overview.data["performance_attention"][0]["id"], "current-ce-result")

    def test_performance_links_current_source_and_authoritative_lane(self) -> None:
        snapshot = cellerator_snapshot()
        snapshot.cuda["results"][1]["source"] = {"commit": "a" * 40}
        snapshot.todo_workflow = {
            "available": True, "revision": 452, "active_run_id": "RUN-1", "runs": [],
            "first_class_agents": [{"run_id": "RUN-1", "lane_id": "L-1", "task_id": "CE-ARCH-92", "context_version": 7}],
            "local_children": [], "blocking_messages": [], "unresolved_questions": [], "rendezvous": [],
            "patch_artifacts": [], "pending_patches": [], "integration_queue": [], "recovery_needed": [], "safe_parallel_groups": [],
        }
        result = performance_status(snapshot, PerformanceStatusInput(project="cellerator"))
        context = result.data["source_and_workflow_context"]["current-ce-result"]
        self.assertEqual(context["source_commit"], "a" * 40)
        self.assertEqual(context["workflow_links"][0]["lane_id"], "L-1")
        self.assertFalse(result.data["execution_performed"])

    def test_registered_ce_arch_92_schema_is_read_from_committed_git_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "bench/architecture_evidence/ce_arch_92_v100_summary.json"
            target.parent.mkdir(parents=True)
            shutil.copyfile(Path(__file__).parent / "fixtures/cellerator/ce_arch_92_v100_summary.json", target)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
            subprocess.run(["git", "add", target.relative_to(root)], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, check=True, capture_output=True)
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            snapshot = cellerator_snapshot()
            snapshot.repositories["source"].commit = commit
            config = ProjectControlConfig(workspaces={
                "cellerator": WorkspaceConfig(authority_repository="source", repositories={"source": RepositoryConfig(root=root)})
            })
            result = performance_status(snapshot, PerformanceStatusInput(project="cellerator"), config)
            evidence = result.data["current_architectural_evidence"][0]
            self.assertEqual(evidence["schema"], "CE-ARCH-92-SUMMARY/1")
            self.assertEqual(evidence["record_count"], 36)
            self.assertEqual(evidence["commit"], commit)


if __name__ == "__main__":
    unittest.main()
