from __future__ import annotations

import json
import unittest

from project_control.models import DeltaSince, PerformanceStatusInput, ProjectSnapshot, RepositoryIdentity
from project_control.normalize import bounded_payload
from project_control.services.delta import project_delta
from project_control.services.frontier import project_frontier
from project_control.services.overview import project_overview
from project_control.services.performance import performance_status


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

    def test_superseded_cp_math_task_cannot_enter_current_frontier(self) -> None:
        snapshot = fixture_snapshot()
        tables = dict(snapshot.todo_tables)
        tables["tasks"] = [
            *tables["tasks"],
            {"id": "CP-MATH-13", "title": "Legacy backend expansion", "status": "superseded", "priority": 999},
        ]
        tables["task_dependencies"] = [
            *tables["task_dependencies"],
            {"task_id": "CP-MATH-13", "prerequisite_task_id": "T2"},
        ]
        status = dict(snapshot.todo_status)
        status["ready"] = [*status["ready"], {"id": "CP-MATH-13"}]
        status["active_claims"] = [*status["active_claims"], {"task_id": "CP-MATH-13"}]
        result = project_frontier(snapshot.model_copy(update={"todo_tables": tables, "todo_status": status}))
        self.assertNotIn("CP-MATH-13", result.data["critical_path"])
        self.assertNotIn("CP-MATH-13", {item["id"] for item in result.data["ready"]})
        self.assertNotIn("CP-MATH-13", {item["id"] for item in result.data["blocked"]})
        self.assertNotIn("CP-MATH-13", {item["task_id"] for item in result.data["local_worker_suitability"]})
        self.assertNotIn("CP-MATH-13", {item["task_id"] for item in result.data["active_claims"]})

    def test_terminal_owner_retires_checkpoint_and_validation_attention(self) -> None:
        snapshot = fixture_snapshot()
        tables = dict(snapshot.todo_tables)
        tables["tasks"] = [
            *tables["tasks"],
            {"id": "CP-MATH-17", "title": "Legacy integration", "status": "superseded", "priority": 999},
        ]
        tables["checkpoints"] = [
            {"id": "CP-MATH-COMPLETE", "task_id": "CP-MATH-17", "state": "pending"},
        ]
        tables["gates"] = [
            {"id": "CPMATH-17-BUILD", "task_id": "CP-MATH-17", "checkpoint_id": "CP-MATH-COMPLETE", "required": 1, "valid": 0},
        ]
        result = project_overview(snapshot.model_copy(update={"todo_tables": tables}), detail="compact")
        self.assertEqual(result.data["architectural_attention"], [])
        self.assertEqual(result.data["validation_attention"], [])
        self.assertNotIn("CP-MATH-17", result.data["recommended_focus"])

    def test_terminal_checkpoint_still_surfaces_when_live_work_requires_it(self) -> None:
        snapshot = fixture_snapshot()
        tables = dict(snapshot.todo_tables)
        tables["tasks"] = [
            *tables["tasks"],
            {"id": "HISTORY", "title": "Historical producer", "status": "archived", "priority": 1},
        ]
        tables["checkpoints"] = [{"id": "OLD-CHECKPOINT", "task_id": "HISTORY", "state": "revoked"}]
        tables["gates"] = [{"id": "OLD-GATE", "task_id": "HISTORY", "checkpoint_id": "OLD-CHECKPOINT", "required": 1, "valid": 0}]
        tables["task_dependencies"] = [
            *tables["task_dependencies"],
            {"task_id": "T2", "checkpoint_id": "OLD-CHECKPOINT"},
        ]
        result = project_overview(snapshot.model_copy(update={"todo_tables": tables}), detail="compact")
        self.assertEqual([item["id"] for item in result.data["architectural_attention"]], ["OLD-CHECKPOINT"])
        self.assertEqual([item["id"] for item in result.data["validation_attention"]], ["OLD-GATE"])

    def test_live_ce_arch_dependencies_survive_historical_filtering(self) -> None:
        snapshot = fixture_snapshot()
        tables = dict(snapshot.todo_tables)
        tables["tasks"] = [
            {"id": "CE-ARCH-94", "title": "Live consumer", "status": "planned", "priority": 20},
            {"id": "CE-ARCH-93", "title": "Live producer", "status": "planned", "priority": 19},
            {"id": "CP-MATH-13", "title": "Historical work", "status": "invalidated", "priority": 999},
        ]
        tables["task_dependencies"] = [
            {"task_id": "CE-ARCH-94", "prerequisite_task_id": "CE-ARCH-93"},
        ]
        status = {"ready": [{"id": "CE-ARCH-93"}], "active_claims": []}
        result = project_frontier(snapshot.model_copy(update={"todo_tables": tables, "todo_status": status}))
        self.assertEqual(result.data["critical_path"], ["CE-ARCH-94", "CE-ARCH-93"])
        self.assertEqual(result.data["blocked"], [{"id": "CE-ARCH-94", "title": "Live consumer", "immediate_blockers": ["CE-ARCH-93"]}])

    def test_historical_task_and_performance_provenance_remain_queryable(self) -> None:
        snapshot = fixture_snapshot()
        tables = dict(snapshot.todo_tables)
        tables["tasks"] = [
            *tables["tasks"],
            {"id": "CP-MATH-13", "title": "Historical work", "status": "superseded", "notes": "preserved benchmark provenance"},
        ]
        cuda = {
            "status": "ok",
            "campaigns": [{"id": "cp-math-small-n", "task_ids": ["CP-MATH-13"]}],
            "facts": [],
            "results": [{"id": "cp-math-v100-result", "campaign_id": "cp-math-small-n", "classification": "material-regression"}],
        }
        historical = snapshot.model_copy(update={"todo_tables": tables, "cuda": cuda})
        before = historical.model_dump(mode="json")
        result = performance_status(historical, PerformanceStatusInput(project="demo"))
        self.assertEqual(result.data["regressions"], [])
        self.assertEqual([item["id"] for item in result.data["historical_measurements"]], ["cp-math-v100-result"])
        self.assertEqual(result.data["campaigns_and_facts"]["results"], cuda["results"])
        self.assertEqual(next(item for item in tables["tasks"] if item["id"] == "CP-MATH-13")["notes"], "preserved benchmark provenance")
        self.assertEqual(historical.model_dump(mode="json"), before)

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


if __name__ == "__main__":
    unittest.main()
