from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_control.config import ProgramConfig, ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import AuthorityComponent, ProgramContextInput, ProjectSnapshot, RepositoryIdentity, WorktreeIdentity
from project_control.services.program import program_context


class _Builder:
    def __init__(self, values: dict[str, ProjectSnapshot]):
        self.values = values

    def build(self, workspace_id: str) -> ProjectSnapshot:
        return self.values[workspace_id]


def _snapshot(workspace_id: str, root: Path, *, observed_at: str, revision: int) -> ProjectSnapshot:
    return ProjectSnapshot(
        workspace_id=workspace_id,
        display_name=workspace_id.title(),
        observed_at=observed_at,
        todo_revision=revision,
        project_uuid=f"uuid-{workspace_id}",
        repositories={
            "source": RepositoryIdentity(commit=f"{revision:040x}", dirty=False, working_tree_fingerprint=f"fp-{workspace_id}")
        },
        todo_tables={
            "tasks": [{"id": f"{workspace_id}-1", "title": "Freeze shared interface contract", "status": "planned"}],
            "interfaces": [{"id": f"if-{workspace_id}", "name": "Shared interface", "state": "frozen"}],
            "context_fragments": [{"id": f"ctx-{workspace_id}", "version": 2, "content_hash": f"hash-{workspace_id}", "state": "current"}],
        },
        todo_workflow={
            "available": True,
            "revision": revision,
            "read_authority_fingerprint": f"wf-{workspace_id}",
            "active_run_id": f"run-{workspace_id}",
            "runs": [{"id": f"run-{workspace_id}", "status": "active"}],
            "first_class_agents": [{"run_id": f"run-{workspace_id}", "lane_id": "lane", "task_id": f"{workspace_id}-1"}],
            "local_children": [{"parent_task_id": f"{workspace_id}-1", "state": "running"}],
        },
        component_authority={
            "todo_semantic_state": AuthorityComponent(
                status="available", operation="semantic_state", revision=revision,
                read_authority_fingerprint=f"state-{workspace_id}", project_uuid=f"uuid-{workspace_id}",
                observed_at=observed_at, source_identity="todo:compatible",
            ),
            "todo_workflow": AuthorityComponent(
                status="available", operation="semantic_workflow", revision=revision,
                read_authority_fingerprint=f"wf-{workspace_id}", project_uuid=f"uuid-{workspace_id}",
                observed_at=observed_at, source_identity="todo:compatible",
            ),
        },
    )


class ProgramContextTests(unittest.TestCase):
    def test_configured_program_is_query_grouping_with_per_project_skew(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            roots = {}
            workspaces = {}
            snapshots = {}
            for index, workspace_id in enumerate(("alpha", "beta")):
                root = base / workspace_id
                root.mkdir()
                roots[workspace_id] = root
                workspaces[workspace_id] = WorkspaceConfig(
                    authority_repository="source",
                    repositories={"source": RepositoryConfig(root=root)},
                )
                snapshots[workspace_id] = _snapshot(
                    workspace_id, root,
                    observed_at=f"2026-08-27T00:00:0{index}Z",
                    revision=10 + index,
                )
            config = ProjectControlConfig(
                schema_version=2,
                workspaces=workspaces,
                programs={"stack": ProgramConfig(display_name="Stack", workspaces=["alpha", "beta"])},
            )
            result = program_context(
                config,
                ProgramContextInput(program_id="stack", question="shared interface contract", max_items=20),
                builder=_Builder(snapshots),
            )
            self.assertEqual(result["status"], "ok")
            data = result["data"]
            self.assertEqual(data["program"]["membership_semantics"], "query_grouping_only")
            self.assertFalse(data["program"]["architectural_authority"])
            self.assertEqual(data["cross_project_synthesis"]["observation_atomicity"], "independent_not_global")
            self.assertEqual(data["cross_project_synthesis"]["observation_skew_seconds"], 1.0)
            self.assertNotIn("observation_preconditions", data)
            alpha_identity = data["projects"][0]["observation_identity"]
            self.assertEqual(alpha_identity["repositories"]["source"]["commit"], f"{10:040x}")
            self.assertTrue(alpha_identity["identity_digest"])
            self.assertTrue(data["projects"][0]["revalidation"]["required_for_mutation"])
            self.assertIn("cross_project_observations_not_atomic", result["warnings"])
            self.assertNotIn(str(base), json.dumps(result))
            self.assertEqual(len(data["projects"][0]["coordination"]["first_class_agents"]), 1)
            self.assertEqual(len(data["projects"][0]["coordination"]["subordinate_local_children"]), 1)

    def test_compact_program_omits_irrelevant_worktrees_and_budgets_full_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = ProjectControlConfig(workspaces={"alpha": WorkspaceConfig(repositories={"source": RepositoryConfig(root=root)})})
            snapshot = _snapshot("alpha", root, observed_at="2026-08-27T00:00:00Z", revision=1)
            snapshot.repositories["source"].current_worktree_id = "wt-0"
            snapshot.repositories["source"].worktrees = {
                f"wt-{index}": WorktreeIdentity(id=f"wt-{index}", repository="source", head=f"{index:040x}", dirty=False,
                    working_tree_fingerprint=f"fp-{index}", observed_at=snapshot.observed_at)
                for index in range(48)
            }
            result = program_context(config, ProgramContextInput(workspaces=["alpha"], question="interface", detail="compact"), builder=_Builder({"alpha": snapshot}))
            encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.assertLessEqual(len(encoded), 16 * 1024)
            self.assertIn("identity_digest", result["data"]["projects"][0]["observation_identity"])
            self.assertNotIn("wt-47", encoded.decode("utf-8"))

    def test_explicit_workspace_list_does_not_require_program(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = ProjectControlConfig(
                workspaces={"alpha": WorkspaceConfig(repositories={"source": RepositoryConfig(root=root)})}
            )
            snapshot = _snapshot("alpha", root, observed_at="2026-08-27T00:00:00Z", revision=1)
            result = program_context(
                config,
                ProgramContextInput(workspaces=["alpha"], question="interface"),
                builder=_Builder({"alpha": snapshot}),
            )
            self.assertEqual(result["data"]["program"]["selection"], "explicit_workspace_list")
            self.assertFalse(result["data"]["program"]["architectural_authority"])

    def test_program_rejects_unknown_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "unknown workspaces"):
                ProjectControlConfig(
                    workspaces={"alpha": WorkspaceConfig(repositories={"source": RepositoryConfig(root=root)})},
                    programs={"bad": ProgramConfig(workspaces=["missing"])},
                )


if __name__ == "__main__":
    unittest.main()
