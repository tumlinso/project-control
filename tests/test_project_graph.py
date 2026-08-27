from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.graph import ProjectGraph
from project_control.models import EvidenceInput, InspectInput
from project_control.reconcile import ProjectReconciler
from project_control.services.evidence import evidence_for
from project_control.services.inspect import inspect_subject

try:
    from .cellerator_fixture import cellerator_snapshot
except ImportError:
    from cellerator_fixture import cellerator_snapshot


class ProjectGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        self.config = ProjectControlConfig(workspaces={
            "cellerator": WorkspaceConfig(authority_repository="source", repositories={"source": RepositoryConfig(root=self.root)})
        })

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_execution_image_concept_resolves_without_literal_source_match(self) -> None:
        snapshot = cellerator_snapshot()
        result = inspect_subject(self.config, snapshot, InspectInput(project="cellerator", kind="subsystem", target="Execution Image v2"))
        self.assertEqual(result.data["resolution"]["status"], "resolved")
        self.assertEqual(result.data["subject"]["id"], "EXECUTION-IMAGE-V2")
        related = {(item["type"], item["id"]) for item in result.data["related"]}
        self.assertIn(("task", "CE-ARCH-82"), related)
        self.assertIn(("artifact", "CE-ARCH-82:tests/architecture/execution_image_v2_test.cpp"), related)

    def test_natural_language_evidence_query_resolves_current_architecture(self) -> None:
        snapshot = cellerator_snapshot()
        request = EvidenceInput(
            project="cellerator",
            subject="current material performance regressions relevant to the completed CE-ARCH architecture",
            kinds=["cuda", "gates", "worker"],
        )
        result = evidence_for(self.config, snapshot, request)
        self.assertEqual(result.data["resolution"]["status"], "resolved")
        self.assertNotIn("no_matching_evidence", result.data["caveats"])
        self.assertIn("current-ce-result", {item.get("id") for item in result.data["support"]})
        self.assertNotIn("old-regression", {item.get("id") for item in result.data["support"]})

    def test_multi_seed_contract_preserves_themes_and_is_deterministic(self) -> None:
        snapshot = cellerator_snapshot()
        graph = ProjectGraph(snapshot, ProjectReconciler(snapshot).reconcile())
        question = "current execution image interface tasks source artifacts and performance evidence"
        first = graph.seed_candidates(question, max_items=12)
        second = graph.seed_candidates(question, max_items=12)
        self.assertEqual(first, second)
        self.assertGreaterEqual(len({item["theme"] for item in first}), 3)
        self.assertIn("heuristic_relevance", {item["authority_label"] for item in first})
        expanded = graph.expand_seeds(first, max_items=20)
        self.assertTrue(all(item["authority_label"] == "derived_relationship" for item in expanded))
        self.assertTrue(all(item.get("basis") for item in expanded))


if __name__ == "__main__":
    unittest.main()
