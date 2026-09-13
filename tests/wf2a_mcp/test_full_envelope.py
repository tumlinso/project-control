from __future__ import annotations

import json
import unittest

from project_control.models import RepositoryIdentity, WorktreeIdentity
from project_control.services.frontier import project_frontier
from project_control.services.overview import project_overview

try:
    from tests.test_project_model import fixture_snapshot
except ModuleNotFoundError:
    from test_project_model import fixture_snapshot


def size(value) -> int:
    return len(json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8"))


class FullEnvelopeBudgetTests(unittest.TestCase):
    def snapshot(self):
        original = fixture_snapshot()
        worktrees = {
            f"wt-{index}": WorktreeIdentity(
                id=f"wt-{index}", repository="source", branch="feature",
                head="a" * 40, dirty=False, working_tree_fingerprint="b" * 64,
                dirty_paths=[f"src/generated/{index}.py"], observed_at=original.observed_at,
            )
            for index in range(80)
        }
        return original.model_copy(update={
            "repositories": {"source": RepositoryIdentity(commit="a" * 40, dirty=False, worktrees=worktrees)},
            "warnings": ["provider_warning_" + "x" * 800],
        })

    def test_compact_overview_budgets_the_whole_serialized_envelope(self) -> None:
        result = project_overview(self.snapshot(), detail="compact", max_items=100)
        self.assertLessEqual(size(result), 1800)
        self.assertEqual(result.cursor.todo_revision, 8)
        self.assertEqual(result.project.repositories["source"].commit, "a" * 40)
        self.assertEqual(result.data["response_coverage"]["expansion_cursor"], "top_level.cursor")

    def test_frontier_budget_keeps_authority_and_cursor(self) -> None:
        result = project_frontier(self.snapshot(), max_ready=100)
        self.assertLessEqual(size(result), 12_288)
        self.assertIn("ready", result.data)
        self.assertEqual(result.cursor.todo_revision, 8)
        self.assertEqual(result.data["response_coverage"]["measurement"], "canonical_json_utf8_full_envelope")


if __name__ == "__main__":
    unittest.main()
