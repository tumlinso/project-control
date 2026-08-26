from __future__ import annotations

import unittest

from project_control.services.frontier import project_frontier
from project_control.services.overview import project_overview

try:
    from .cellerator_fixture import cellerator_snapshot
except ImportError:
    from cellerator_fixture import cellerator_snapshot


class ReconciliationTests(unittest.TestCase):
    def test_completed_cellerator_program_filters_superseded_frontier_and_stale_attention(self) -> None:
        snapshot = cellerator_snapshot()
        before = snapshot.model_dump(mode="json")
        overview = project_overview(snapshot, detail="compact", max_items=10)
        frontier = project_frontier(snapshot)
        self.assertTrue(overview.data["current_project_state"][0]["complete"])
        self.assertEqual(frontier.data["critical_path"], [])
        self.assertNotIn("CP-MATH-17", {item["id"] for item in frontier.data["ready"]})
        self.assertEqual(overview.data["architectural_attention"], [])
        self.assertEqual(overview.data["validation_attention"], [])
        self.assertEqual(overview.data["historical_state_filtered"]["checkpoints"], 2)
        self.assertEqual(overview.data["historical_state_filtered"]["gates"], 2)
        self.assertIn("stale_legacy_state_filtered", overview.warnings)
        self.assertIn("CE-ARCH-92", {item["id"] for item in overview.data["recent_materially_completed"]})
        self.assertEqual(overview.data["performance_attention"], [])
        self.assertEqual(snapshot.repositories["source"].working_tree_fingerprint, snapshot.repository_fingerprints["source"])
        self.assertEqual(snapshot.model_dump(mode="json"), before)


if __name__ == "__main__":
    unittest.main()
