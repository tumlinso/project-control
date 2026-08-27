from __future__ import annotations

import unittest

from project_control.models import DeltaSince
from project_control.services.delta import project_delta

try:
    from .cellerator_fixture import cellerator_snapshot
except ImportError:
    from cellerator_fixture import cellerator_snapshot


class SemanticTodoAdapter:
    def semantic_anchor(self, *arguments):
        return {
            "todo_revision": 9, "timestamp": "2026-08-20T00:00:00Z",
            "entity": {"type": "task", "id": "CE-ARCH-00", "phase": "created"},
            "baseline_git_heads": ["b" * 40], "confidence": "high", "reason": "exact_task_created_event",
        }

    def semantic_delta(self, *arguments):
        return {
            "interval": {"from_revision": 9, "to_revision": 452},
            "tasks": {"completed": ["CE-ARCH-82", "CE-ARCH-92"], "superseded": ["CP-MATH-17"], "blocked": [], "reopened": []},
            "interfaces": {"frozen": ["EXECUTION-IMAGE-V2"], "revised": []},
            "checkpoints": {"reached": ["CE-ARCH-VALIDATED"], "revoked": []},
            "decisions": {"resolved": [], "changed": []},
            "validation_by_task": [{"task_id": "CE-ARCH-92", "passed": 1, "failed": 0, "invalidated": 0}],
            "coordination_summary": {"claims_started": 12, "claims_released": 12, "children_completed": 2},
            "material_events": [{"revision": 452, "event_type": "task.completed", "entity_id": "CE-ARCH-92"}],
            "raw_event_count": 403, "coalesced_event_count": 8, "heartbeat_events_omitted": 400,
        }


class GitAdapter:
    def diff_names(self, older, newer, max_items=500):
        return [
            {"status": "A", "path": "src/compute/execution_image/execution_image_v2.hpp"},
            {"status": "M", "path": "bench/architecture_evidence/ce_arch_92_v100_summary.json"},
            {"status": "A", "path": "tests/architecture/execution_image_v2_test.cpp"},
        ]


class SemanticDeltaTests(unittest.TestCase):
    def test_since_task_summarizes_whole_interval_before_budgeting(self) -> None:
        snapshot = cellerator_snapshot()
        result = project_delta(
            snapshot,
            DeltaSince(task="CE-ARCH-00"),
            {"source": GitAdapter()},
            todo_adapter=SemanticTodoAdapter(),
        )
        semantic = result.data["semantic_todo"]
        self.assertEqual(semantic["interval"], {"from_revision": 9, "to_revision": 452})
        self.assertEqual(semantic["heartbeat_events_omitted"], 400)
        self.assertNotIn("claim.pulsed", str(result.data))
        self.assertEqual(result.data["ranking"]["items_considered"], 403)
        self.assertEqual(result.data["ranking"]["items_returned"], 8)
        groups = {item["group"] for item in result.data["git_changes"][0]["path_categories"]}
        self.assertIn("src/compute/execution_image", groups)
        self.assertIn("bench/architecture_evidence", groups)

    def test_workflow_changes_are_categorized_and_pulses_coalesced(self) -> None:
        snapshot = cellerator_snapshot()
        snapshot.todo_tables["events"].extend([
            {"revision": 453, "event_type": "workflow_message.question", "entity_id": "M1", "timestamp": "2026-08-26T01:00:00Z"},
            {"revision": 454, "event_type": "workspace.created", "entity_id": "W1", "timestamp": "2026-08-26T02:00:00Z"},
            {"revision": 455, "event_type": "workflow.interface.published", "entity_id": "I1", "timestamp": "2026-08-26T03:00:00Z"},
            {"revision": 456, "event_type": "workflow_context_fragment_published", "entity_id": "CTX1", "timestamp": "2026-08-26T04:00:00Z"},
        ])
        snapshot.todo_revision = 456
        result = project_delta(snapshot, DeltaSince(todo_revision=452), {})
        self.assertEqual(set(result.data["workflow_changes"]), {"messages", "workspaces", "interfaces", "context_fragments"})
        self.assertEqual(result.data["workflow_changes"]["interfaces"][0]["category"], "architecture")
        self.assertTrue(result.data["workflow_changed"])
        self.assertNotIn("claim.pulsed", str(result.data["workflow_changes"]))
        self.assertIn("observation_preconditions", result.data)


if __name__ == "__main__":
    unittest.main()
