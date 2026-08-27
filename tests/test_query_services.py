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
    WorktreeIdentity,
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

    def test_large_path_range_does_not_load_whole_file(self) -> None:
        large = self.root / "src" / "large.cpp"
        with large.open("w", encoding="utf-8") as stream:
            for number in range(180000):
                stream.write(f"line-{number:06d} padding padding\n")
        self.assertGreater(large.stat().st_size, 2 * 1024 * 1024)
        result = inspect_subject(self.config, self.snapshot, InspectInput(
            project="demo", kind="path", target="src/large.cpp", repository="source",
            line_start=100000, line_end=100002,
        ))
        self.assertEqual(result.data["excerpt"].splitlines(), [
            "line-099999 padding padding", "line-100000 padding padding", "line-100001 padding padding",
        ])
        self.assertEqual(result.data["file_identity_before"], result.data["file_identity_after"])
        self.assertNotIn("source_inspection_unavailable", result.warnings)

    def test_path_inspection_ignores_unrelated_todo_warning(self) -> None:
        self.snapshot.provider_warnings = {"todo": ["todo_authority_unavailable"]}
        result = inspect_subject(self.config, self.snapshot, InspectInput(project="demo", kind="path", target="src/module.py", repository="source"))
        self.assertNotIn("todo_authority_unavailable", result.warnings)

    def test_evidence_reports_support_and_provenance(self) -> None:
        result = evidence_for(self.config, self.snapshot, EvidenceInput(project="demo", subject="T1", kinds=["gates", "worker", "git"]))
        self.assertEqual(result.data["confidence"], "high")
        self.assertIn("todo-gate:G1", result.data["provenance_ids"])
        self.assertNotIn("stdout", json.dumps(result.model_dump()))

    def test_v2_evidence_kinds_classify_current_and_stale_authority(self) -> None:
        self.snapshot.todo_tables["decisions"] = [{"id": "D1", "task_id": "T1", "state": "accepted", "summary": "Use v2"}]
        self.snapshot.todo_tables["context_fragments"] = [{"id": "CTX1", "task_id": "T1", "state": "invalidated"}]
        result = evidence_for(self.config, self.snapshot, EvidenceInput(
            project="demo", subject="T1", kinds=["decision", "context"],
        ))
        self.assertEqual(result.data["evidence_state_counts"]["current_support"], 1)
        self.assertEqual(result.data["evidence_state_counts"]["stale"], 1)
        self.assertIn("observation_preconditions", result.data)

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
        self.assertIn("local_supervisor_capacity", result.data)
        self.assertEqual(result.data["observer_jobs"], [])

    def test_agent_output_uses_stable_worktree_id_and_omits_process_authority_ids(self) -> None:
        self.snapshot.repositories["source"].worktrees = {
            "wt-public": WorktreeIdentity(
                id="wt-public", repository="source", branch="lane", head="abc", dirty=False,
                working_tree_fingerprint="clean", observed_at=self.snapshot.observed_at,
            )
        }
        self.snapshot.todo_workflow = {
            "available": True, "revision": 7, "active_run_id": "RUN",
            "runs": [{"id": "RUN", "status": "active", "lanes": [{
                "id": "LANE", "role": "implementer", "state": "active", "queue": [],
                "dispatch": {"dispatch_id": "D", "session_id": "S-private", "claim_id": "C-private", "task_id": "T1"},
                "workspace": {"id": "W", "repository": "source", "branch": "lane", "head": "abc", "worktree_path": "/private/path"},
            }]}],
            "first_class_agents": [{"run_id": "RUN", "lane_id": "LANE", "task_id": "T1", "session_id": "S-private", "claim_id": "C-private"}],
            "local_children": [], "blocking_messages": [], "unresolved_questions": [], "rendezvous": [],
            "patch_artifacts": [], "pending_patches": [], "integration_queue": [], "recovery_needed": [], "safe_parallel_groups": [],
        }
        result = agent_status(self.snapshot, AgentStatusInput(project="demo"))
        encoded = json.dumps(result.data)
        self.assertEqual(result.data["first_class_agents"][0]["worktree_id"], "wt-public")
        self.assertNotIn("S-private", encoded)
        self.assertNotIn("C-private", encoded)
        self.assertNotIn("/private/path", encoded)

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
