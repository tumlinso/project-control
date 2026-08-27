from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import AgentStatusInput, DeltaSince, InspectInput, PlanPreviewInput, ProjectSnapshot, RepositoryIdentity
from project_control.services.agents import agent_status
from project_control.services.delta import project_delta
from project_control.services.frontier import project_frontier
from project_control.services.inspect import inspect_subject
from project_control.services.overview import project_overview
from project_control.services.planning import plan_preview


def workflow_fixture(root: Path) -> ProjectSnapshot:
    return ProjectSnapshot(
        workspace_id="demo",
        observed_at="2026-08-27T00:00:00Z",
        todo_revision=42,
        project_uuid="project-uuid",
        repositories={"source": RepositoryIdentity(commit="abc", dirty=False)},
        todo_status={"active_claims": [{"task_id": "CLAIM-ONLY", "expires_at": "2026-08-28T00:00:00Z"}]},
        todo_tables={
            "tasks": [
                {"id": "T-ROOT", "title": "Root", "status": "in_progress"},
                {"id": "T-A", "title": "Implement", "status": "in_progress"},
                {"id": "T-B", "title": "Validate", "status": "planned"},
                {"id": "CLAIM-ONLY", "title": "Claim only", "status": "in_progress"},
            ],
            "child_executions": [{"id": "legacy-child", "task_id": "T-A", "state": "running"}],
            "events": [{"revision": 41, "event_type": "workflow_message.question", "entity_id": "M-1", "timestamp": "2026-08-27T00:00:00Z"}],
        },
        todo_workflow={
            "available": True,
            "revision": 42,
            "active_run_id": "RUN-1",
            "runs": [{
                "id": "RUN-1", "root_task_id": "T-ROOT", "status": "active", "active_charter_version": 3,
                "lanes": [
                    {
                        "id": "a-child", "parent_lane_id": "z-parent", "role": "implementer", "state": "active",
                        "workspace_mode": "isolated_merge", "context_cursor": 8,
                        "queue": [{"position": 1, "task_id": "T-A", "state": "active"}],
                        "dispatch": {"dispatch_id": "D-1", "session_id": "S-1", "claim_id": "C-1", "task_id": "T-A", "heartbeat_fresh": True, "context_version": 8, "observable": True},
                        "workspace": {"id": "W-1", "run_id": "RUN-1", "lane_id": "a-child", "base_commit": "abc", "worktree_path": str(root / "lane"), "branch": "lane-a", "mode": "isolated_merge", "state": "dirty", "integration_task_id": "T-I"},
                    },
                    {
                        "id": "z-parent", "parent_lane_id": None, "role": "coordinator", "state": "ready",
                        "workspace_mode": "exclusive", "context_cursor": 4,
                        "queue": [{"position": 1, "task_id": "T-B", "state": "queued"}],
                        "dispatch": None, "workspace": None,
                    },
                ],
            }],
            "first_class_agents": [{"run_id": "RUN-1", "lane_id": "a-child", "role": "implementer", "dispatch_id": "D-1", "session_id": "S-1", "claim_id": "C-1", "task_id": "T-A", "heartbeat_fresh": True, "context_version": 8, "observable": True}],
            "local_children": [{"child_execution_id": "LC-1", "parent_claim_id": "C-1", "parent_task_id": "T-A", "parent_lane_id": "a-child", "state": "running", "access_mode": "read_only"}],
            "blocking_messages": [{"id": "M-1", "run_id": "RUN-1", "author_lane_id": "z-parent", "task_id": "T-A", "kind": "question", "blocking": True, "state": "open", "revision": 40}],
            "unresolved_questions": [{"id": "M-1", "run_id": "RUN-1", "author_lane_id": "z-parent", "task_id": "T-A", "kind": "question", "blocking": True, "state": "open", "revision": 40}],
            "rendezvous": [{"id": "R-1", "run_id": "RUN-1", "mode": "all", "join_task_id": "T-B", "state": "open", "arrivals": [{"lane_id": "a-child", "task_id": "T-A", "state": "accepted", "context_version": 8, "revision": 41}]}],
            "patch_artifacts": [{"id": "P-1", "workspace_id": "W-1", "run_id": "RUN-1", "lane_id": "a-child", "task_id": "T-A", "kind": "commit", "artifact_ref": "refs/wfu/P-1", "content_hash": "hash", "base_commit": "abc", "state": "pending", "capability_hash": "must-not-cross"}],
            "pending_patches": [{"id": "P-1", "workspace_id": "W-1", "run_id": "RUN-1", "lane_id": "a-child", "task_id": "T-A", "kind": "commit", "artifact_ref": "refs/wfu/P-1", "content_hash": "hash", "base_commit": "abc", "state": "pending"}],
            "integration_queue": [{"id": "I-1", "run_id": "RUN-1", "integration_task_id": "T-I", "integrator_lane_id": "z-parent", "position": 1, "state": "conflict", "conflict": {"paths": ["source.py"]}}],
            "recovery_needed": [{"kind": "workspace", "id": "W-1", "reason": "dirty"}],
            "safe_parallel_groups": [["a-child", "z-parent"]],
            "raw_log": "must-not-cross",
        },
        local_worker={"status": "ok"},
    )


class WorkflowReadModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        self.config = ProjectControlConfig(workspaces={
            "demo": WorkspaceConfig(authority_repository="source", repositories={"source": RepositoryConfig(root=self.root)})
        })
        self.snapshot = workflow_fixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_agent_status_never_flattens_claims_or_children_into_first_class_agents(self) -> None:
        result = agent_status(self.snapshot, AgentStatusInput(project="demo"))
        self.assertEqual([item["lane_id"] for item in result.data["first_class_agents"]], ["a-child"])
        self.assertEqual([item["child_execution_id"] for item in result.data["subordinate_local_children"]], ["LC-1"])
        self.assertEqual(result.data["claim_observations"][0]["classification"], "claim_only_not_first_class_agent")
        self.assertNotIn("CLAIM-ONLY", json.dumps(result.data["first_class_agents"]))
        self.assertNotIn("LC-1", json.dumps(result.data["first_class_agents"]))

    def test_workflow_outputs_are_bounded_and_preserve_authoritative_categories(self) -> None:
        overview = project_overview(self.snapshot, detail="compact", max_items=10)
        frontier = project_frontier(self.snapshot, max_ready=10)
        delta = project_delta(self.snapshot, DeltaSince(todo_revision=39), {})
        self.assertEqual(overview.data["workflow"]["active_run_id"], "RUN-1")
        self.assertEqual(overview.data["workflow"]["pending_patches"][0]["id"], "P-1")
        self.assertEqual(frontier.data["parallel_group_basis"], "todo_semantic_workflow")
        self.assertEqual(frontier.data["parallel_groups"], [["a-child", "z-parent"]])
        self.assertTrue(delta.data["workflow_changed"])
        encoded = json.dumps([overview.model_dump(), frontier.model_dump(), delta.model_dump()])
        self.assertNotIn("must-not-cross", encoded)
        self.assertNotIn("raw_log", encoded)
        self.assertLessEqual(len(overview.model_dump_json().encode()), 7000)

    def test_existing_subsystem_inspection_resolves_lane_hierarchy_and_patches(self) -> None:
        lane = inspect_subject(self.config, self.snapshot, InspectInput(project="demo", kind="subsystem", target="a-child"))
        relations = {(item["relation"], item["type"], item["id"]) for item in lane.data["related"]}
        self.assertIn(("child_lane", "lane", "z-parent"), relations)
        patch = inspect_subject(self.config, self.snapshot, InspectInput(project="demo", kind="subsystem", target="P-1"))
        self.assertEqual(patch.data["subject"]["type"], "patch_artifact")

    def test_plan_context_advertises_v3_additively_without_removing_v2(self) -> None:
        result = plan_preview(self.config, self.snapshot, PlanPreviewInput(project="demo", mode="context"))
        self.assertEqual(result.data["plan_schema_version"], 2)
        self.assertEqual(result.data["accepted_plan_schema_versions"], [2, 3])
        self.assertEqual(result.data["workflow"]["active_run_id"], "RUN-1")

    def test_old_todo_workflow_view_degrades_explicitly(self) -> None:
        legacy = self.snapshot.model_copy(update={"todo_workflow": {}})
        result = agent_status(legacy, AgentStatusInput(project="demo"))
        self.assertEqual(result.data["first_class_agents"], [])
        self.assertEqual(result.data["subordinate_local_children"], [])
        self.assertTrue(result.data["legacy_child_observations"])
        self.assertIn("todo_workflow_semantic_unavailable", result.warnings)


if __name__ == "__main__":
    unittest.main()
