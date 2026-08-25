from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import (
    AgentStatusInput,
    EvidenceInput,
    InspectInput,
    PerformanceStatusInput,
    ProjectSnapshot,
    RepositoryIdentity,
)
from project_control.services.agents import agent_status
from project_control.services.evidence import evidence_for
from project_control.services.inspect import inspect_subject
from project_control.services.performance import performance_status


class QueryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.config = ProjectControlConfig(workspaces={
            "demo": WorkspaceConfig(authority_repository="source", repositories={"source": RepositoryConfig(root=self.root)})
        })
        self.snapshot = ProjectSnapshot(
            workspace_id="demo",
            observed_at="2026-08-25T00:00:00Z",
            todo_revision=7,
            repositories={"source": RepositoryIdentity(commit="abc", dirty=False)},
            todo_status={"active_claims": [{"task_id": "T1", "expires_at": "2026-08-26T00:00:00Z"}]},
            todo_tables={
                "tasks": [{"id": "T1", "title": "Task"}],
                "gates": [{"id": "G1", "task_id": "T1", "status": "passed", "valid": 1}],
                "handoffs": [{"id": "H1", "task_id": "T1", "kind": "complete", "note": "done"}],
            },
            local_worker={"status": "unavailable"},
            cuda={"status": "unavailable", "campaigns": [], "facts": [], "results": [], "warnings": ["none"]},
            host={"status": "ok", "memory": {}},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_bounded_path_inspection_and_task_inspection(self) -> None:
        request = InspectInput(project="demo", kind="path", target="src/module.py", repository="source")
        result = inspect_subject(self.config, self.snapshot, request)
        self.assertIn("VALUE", result.data["excerpt"])
        denied = InspectInput(project="demo", kind="path", target="/etc/passwd", repository="source")
        result = inspect_subject(self.config, self.snapshot, denied)
        self.assertIn("source_inspection_unavailable", result.warnings)
        task = inspect_subject(self.config, self.snapshot, InspectInput(project="demo", kind="task", target="T1"))
        self.assertEqual(task.data["matches"][0]["id"], "T1")

    def test_path_inspection_ignores_unrelated_todo_warning(self) -> None:
        self.snapshot.provider_warnings = {"todo": ["todo_authority_unavailable"]}
        result = inspect_subject(self.config, self.snapshot, InspectInput(project="demo", kind="path", target="src/module.py", repository="source"))
        self.assertNotIn("todo_authority_unavailable", result.warnings)

    def test_evidence_reports_support_and_provenance(self) -> None:
        result = evidence_for(self.config, self.snapshot, EvidenceInput(project="demo", subject="T1", kinds=["gates", "worker", "git"]))
        self.assertEqual(result.data["confidence"], "high")
        self.assertIn("todo-gate:G1", result.data["provenance_ids"])
        self.assertNotIn("stdout", json.dumps(result.model_dump()))

    def test_nonexistent_subject_has_no_git_pseudo_support(self) -> None:
        subject = "THIS-SUBJECT-DEFINITELY-DOES-NOT-EXIST-XYZ-92841"
        result = evidence_for(self.config, self.snapshot, EvidenceInput(project="demo", subject=subject, kinds=["git"]))
        self.assertEqual(result.data["confidence"], "insufficient")
        self.assertEqual(result.data["support"], [])

    def test_git_evidence_requires_matching_repository_or_commit(self) -> None:
        result = evidence_for(self.config, self.snapshot, EvidenceInput(project="demo", subject="source", kinds=["git"]))
        self.assertEqual(result.data["confidence"], "high")
        self.assertEqual(result.data["support"][0]["kind"], "git_identity")

    def test_agents_are_observable_only(self) -> None:
        result = agent_status(self.snapshot, AgentStatusInput(project="demo"))
        self.assertTrue(result.data["observable_only"])
        self.assertNotIn("thinking", str(result.model_dump()))

    def test_performance_never_executes(self) -> None:
        result = performance_status(self.snapshot, PerformanceStatusInput(project="demo"))
        self.assertFalse(result.data["execution_performed"])
        self.assertIn("performance_evidence_unavailable", result.warnings)

    def test_performance_uses_structured_classifications(self) -> None:
        self.snapshot.cuda = {
            "status": "ok", "warnings": [], "campaigns": [{"id": "c1"}],
            "facts": [{"fact_id": "f1", "classification": "material-improvement", "compatibility": "compatible", "measurement": {"uncontaminated": True}}],
            "results": [{"id": "r1", "classification": "material-regression", "contaminated": False}],
        }
        result = performance_status(self.snapshot, PerformanceStatusInput(project="demo", campaign="c1"))
        self.assertEqual(result.data["regressions"][0]["id"], "r1")
        self.assertEqual(result.data["improvements"][0]["fact_id"], "f1")


if __name__ == "__main__":
    unittest.main()
