from __future__ import annotations

import json
import unittest

from project_control.models import DeltaSince, ProjectSnapshot, RepositoryIdentity
from project_control.normalize import bounded_payload
from project_control.services.delta import project_delta
from project_control.services.frontier import project_frontier
from project_control.services.overview import project_overview


def fixture_snapshot() -> ProjectSnapshot:
    return ProjectSnapshot(
        workspace_id="demo",
        display_name="Demo",
        observed_at="2026-08-25T00:00:00Z",
        todo_revision=8,
        project_uuid="uuid",
        repositories={"source": RepositoryIdentity(commit="abc", dirty=False)},
        todo_status={"ready": [{"id": "T2"}], "active_claims": [{"task_id": "T1"}]},
        todo_tables={
            "tasks": [
                {"id": "T1", "title": "Active", "status": "in_progress", "priority": 10},
                {"id": "T2", "title": "Ready", "status": "planned", "priority": 9},
                {"id": "T3", "title": "Blocked", "status": "planned", "priority": 8},
                {"id": "T0", "title": "Done", "status": "done", "priority": 11, "updated_at": "2026-08-24T00:00:00Z"},
            ],
            "task_dependencies": [{"task_id": "T3", "prerequisite_task_id": "T2"}],
            "ownership_scopes": [{"task_id": "T2", "mode": "exclusive", "path": "src"}],
            "events": [{"revision": 7, "event_type": "gate.completed", "entity_id": "G1", "timestamp": "now"}],
            "interfaces": [{"id": "I1", "state": "draft"}],
            "checkpoints": [],
            "gates": [],
        },
    )


class ProjectModelTests(unittest.TestCase):
    def test_overview_is_synthesized_and_budgeted(self) -> None:
        result = project_overview(fixture_snapshot(), detail="compact", max_items=3)
        self.assertEqual(result.tool, "project_overview")
        self.assertEqual(result.data["recommended_focus"], ["T1"])
        self.assertLessEqual(len(result.model_dump_json().encode()), 6000 + 2000)

    def test_frontier_labels_heuristic(self) -> None:
        result = project_frontier(fixture_snapshot())
        self.assertEqual(result.data["ready"][0]["id"], "T2")
        self.assertIn("heuristic", result.data["critical_path_basis"])
        self.assertEqual(result.data["blocked"][0]["immediate_blockers"], ["T2"])

    def test_delta_uses_explicit_cursor_and_returns_new_one(self) -> None:
        result = project_delta(fixture_snapshot(), DeltaSince(todo_revision=6, commits={"source": "abc"}), {})
        self.assertEqual(result.data["changes"][0]["category"], "validation")
        self.assertEqual(result.data["new_cursor"]["todo_revision"], 8)

    def test_nullable_cursor_round_trip_is_bounded(self) -> None:
        snapshot = fixture_snapshot().model_copy(update={"todo_revision": None, "todo_tables": {}})
        since = DeltaSince.model_validate(snapshot.cursor().model_dump(mode="json"))
        result = project_delta(snapshot, since, {})
        self.assertIn("todo_delta_unavailable", result.warnings)
        self.assertNotEqual(result.status.value, "internal_error")

    def test_delta_detects_same_commit_working_tree_change(self) -> None:
        snapshot = fixture_snapshot().model_copy(update={"repository_fingerprints": {"source": "new"}})
        since = DeltaSince(todo_revision=8, commits={"source": "abc"}, fingerprints={"source": "old"})
        result = project_delta(snapshot, since, {})
        self.assertTrue(result.data["git_changes"][0]["working_tree_changed"])

    def test_normalizer_enforces_budget_and_redacts(self) -> None:
        value = {"items": [{"id": str(index), "text": "x" * 100} for index in range(100)], "api_token": "secret"}
        result = bounded_payload(value, 1000)
        self.assertTrue(result["truncation"]["truncated"])
        self.assertNotIn("secret", json.dumps(result))

    def test_normalizer_bounds_nested_semantic_collections(self) -> None:
        value = {"semantic_todo": {"material_events": [{"id": str(index), "detail": "x" * 80} for index in range(100)]}}
        result = bounded_payload(value, 1200)
        self.assertLessEqual(len(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()), 1200)
        self.assertEqual(result["truncation"]["items_considered"], 100)
        self.assertLess(result["truncation"]["items_returned"], 100)


if __name__ == "__main__":
    unittest.main()
