from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from project_control.app import Runtime
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import (
    ArchitectureContextInput, DeltaSince, EvidenceInput, ImpactPreviewInput,
    PlanPreviewInput, ProjectSnapshot, RepositoryIdentity, ToolEnvelope, envelope,
)
from project_control.services.architecture import architecture_context
from project_control.services.delta import project_delta
from project_control.services.evidence import evidence_for
from project_control.services.impact import impact_preview
from project_control.services.planning import plan_preview

try:
    from .cellerator_fixture import cellerator_snapshot
except ImportError:
    from cellerator_fixture import cellerator_snapshot


class EmptyDeltaAdapter:
    def semantic_anchor(self, *arguments):
        return {
            "todo_revision": 452, "baseline_git_heads": ["a" * 40],
            "authority_path": "/home/example/private/state.sqlite3",
        }

    def semantic_delta(self, *arguments):
        return {
            "interval": {"from_revision": 452, "to_revision": 452},
            "tasks": {"completed": [], "blocked": [], "reopened": []},
            "checkpoints": {"reached": [], "revoked": []},
            "raw_event_count": 0, "coalesced_event_count": 0,
        }


class V2CleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Tests"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.root, check=True, capture_output=True)
        self.config = ProjectControlConfig(workspaces={
            "demo": WorkspaceConfig(authority_repository="source", repositories={"source": RepositoryConfig(root=self.root)})
        })

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_zero_length_delta_has_no_readiness_change_and_redacts_anchor_path(self) -> None:
        result = project_delta(cellerator_snapshot(), DeltaSince(todo_revision=452), {}, todo_adapter=EmptyDeltaAdapter())
        self.assertFalse(result.data["readiness_changed"])
        self.assertEqual(result.data["semantic_todo"]["interval"], {"from_revision": 452, "to_revision": 452})
        self.assertNotIn("/home/", json.dumps(result.data))

    def test_pending_downstream_gate_is_unvalidated_not_a_contradiction(self) -> None:
        snapshot = ProjectSnapshot(
            workspace_id="demo", observed_at="2026-08-27T00:00:00Z", todo_revision=4,
            repositories={"source": RepositoryIdentity(commit="a" * 40, dirty=False)},
            todo_semantic={
                "revision": 4,
                "tasks": [{"id": "T-DOWN", "title": "Downstream", "effective_state": "ready", "current_relevance": "current"}],
                "gates": [{"id": "G-PENDING", "task_id": "T-DOWN", "raw_status": "pending", "raw_valid": False,
                           "effective_state": "current_pending", "relevance": "current_attention"}],
                "checkpoints": [], "programs": [], "contradictions": [],
            },
            todo_tables={
                "tasks": [{"id": "T-DOWN", "title": "Downstream", "status": "planned"}],
                "interfaces": [{"id": "IFACE-1", "state": "frozen", "version": 1}],
                "interface_consumers": [{"interface_id": "IFACE-1", "task_id": "T-DOWN"}],
                "gates": [{"id": "G-PENDING", "task_id": "T-DOWN", "status": "pending", "valid": 0}],
            },
        )
        result = evidence_for(self.config, snapshot, EvidenceInput(project="demo", subject="IFACE-1", kinds=["gates"]))
        self.assertEqual(result.data["contradictions"], [])
        self.assertEqual(result.data["unmeasured_or_unvalidated"][0]["id"], "G-PENDING")
        self.assertEqual(result.data["evidence_state_counts"]["unvalidated"], 1)

    def test_current_retrieval_precedes_history_and_expanded_impact_retains_it(self) -> None:
        snapshot = cellerator_snapshot()
        stale = {
            "id": "OLD-EXEC", "title": "Execution Image v2 historical interface", "objective": "old source interface",
            "effective_state": "superseded", "current_relevance": "superseded", "terminal": True,
            "priority": 999, "reason_codes": ["superseded"], "dependencies": [],
        }
        snapshot.todo_semantic["tasks"].append(stale)
        snapshot.todo_tables["tasks"].append({"id": "OLD-EXEC", "title": stale["title"], "status": "superseded"})
        snapshot.todo_tables["interface_consumers"].append({"interface_id": "EXECUTION-IMAGE-V2", "task_id": "OLD-EXEC"})
        architecture = architecture_context(snapshot, ArchitectureContextInput(
            project="cellerator", question="execution image interface source", scope="all", detail="standard",
        ))
        seeds = [seed for cluster in architecture.data["clusters"] for seed in cluster["seeds"]]
        self.assertLess(
            next(index for index, item in enumerate(seeds) if item["id"] == "EXECUTION-IMAGE-V2"),
            next(index for index, item in enumerate(seeds) if item["id"] == "OLD-EXEC"),
        )
        standard = impact_preview(snapshot, ImpactPreviewInput(
            project="cellerator", hypothesis="execution image interface", target_entities=["EXECUTION-IMAGE-V2"], detail="standard",
        ))
        expanded = impact_preview(snapshot, ImpactPreviewInput(
            project="cellerator", hypothesis="execution image interface", target_entities=["EXECUTION-IMAGE-V2"], detail="expanded",
        ))
        self.assertNotIn("OLD-EXEC", json.dumps(standard.data["possible_impacts"]))
        self.assertIn("OLD-EXEC", json.dumps(expanded.data["possible_impacts"]))

    def test_plan_context_uses_multiple_current_objective_seeds(self) -> None:
        result = plan_preview(self.config, cellerator_snapshot(), PlanPreviewInput(
            project="cellerator", mode="context",
            objective="current execution image interface tasks source artifacts and performance evidence",
        ))
        self.assertEqual(result.data["resolution"]["status"], "resolved")
        self.assertEqual(result.data["resolution"]["retrieval_mode"], "multi_seed")
        self.assertGreaterEqual(len({item["theme"] for item in result.data["resolution"]["subjects"]}), 3)

    def test_runtime_redacts_absolute_paths_from_all_normal_output(self) -> None:
        snapshot = ProjectSnapshot(workspace_id="demo", observed_at="2026-08-27T00:00:00Z", repositories={})
        runtime = Runtime(self.config)
        result = runtime.invoke("evidence", "demo", lambda: envelope(
            "evidence", snapshot,
            {"evidence_path": "/home/example/private/evidence.json", "anchor": {"payload": "at /tmp/private/anchor.json"}},
        ))
        encoded = json.dumps(result)
        self.assertNotIn("/home/", encoded)
        self.assertNotIn("/tmp/", encoded)
        self.assertIn("[REDACTED_PATH]", encoded)

    def test_ordinary_context_compacts_large_task_notes(self) -> None:
        snapshot = cellerator_snapshot()
        huge = "historical detail " * 10000
        task = next(item for item in snapshot.todo_semantic["tasks"] if item["id"] == "CE-ARCH-82")
        task["notes"] = huge
        raw = next(item for item in snapshot.todo_tables["tasks"] if item["id"] == "CE-ARCH-82")
        raw["notes"] = huge
        result = plan_preview(self.config, snapshot, PlanPreviewInput(
            project="cellerator", mode="context", objective="Execution Image v2 interface",
        ))
        encoded = json.dumps(result.data)
        self.assertNotIn(huge, encoded)
        self.assertLess(len(encoded.encode()), 10000)


if __name__ == "__main__":
    unittest.main()
