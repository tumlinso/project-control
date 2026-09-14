from __future__ import annotations

import json
import unittest

from project_control.models import ImpactPreviewInput, RepositoryIdentity, WorktreeIdentity
from project_control.services.frontier import project_frontier
from project_control.services.delta import project_delta
from project_control.services.overview import project_overview
from project_control.services.impact import impact_preview
from project_control.models import DeltaSince

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

    def test_ordinary_read_identity_is_compact_but_digests_complete_worktrees(self) -> None:
        snapshot = self.snapshot()
        full = snapshot.observation_preconditions()
        overview = project_overview(snapshot, detail="compact", max_items=100)
        delta = project_delta(snapshot, DeltaSince(todo_revision=7), {})
        frontier = project_frontier(snapshot, max_ready=100)
        for result in (overview, delta, frontier):
            self.assertEqual(len(result.project.repositories["source"].worktrees), 0)
            self.assertEqual(len(result.cursor.worktrees), 1)
            self.assertIsNotNone(result.cursor.identity_digest)
            self.assertNotIn("observation_preconditions", result.data)
            self.assertNotEqual(
                result.data.get("response_coverage", {}).get("reason"),
                "immutable_identity_exceeds_budget",
            )
        self.assertEqual(len(full.worktrees), 80)

    def test_frontier_budget_keeps_authority_and_cursor(self) -> None:
        result = project_frontier(self.snapshot(), max_ready=100)
        self.assertLessEqual(size(result), 12_288)
        self.assertIn("ready", result.data)
        self.assertEqual(result.cursor.todo_revision, 8)
        self.assertNotIn("response_coverage", result.data)

    def test_oversized_explicit_contract_requires_typed_expansion(self) -> None:
        snapshot = self.snapshot()
        repository = snapshot.repositories["source"]
        worktrees = dict(repository.worktrees)
        for index in range(80, 240):
            worktrees[f"wt-{index}"] = WorktreeIdentity(
                id=f"wt-{index}", repository="source", branch="feature",
                head="a" * 40, dirty=False, working_tree_fingerprint="b" * 64,
                observed_at=snapshot.observed_at,
            )
        snapshot.repositories["source"] = repository.model_copy(update={"worktrees": worktrees})
        result = impact_preview(snapshot, ImpactPreviewInput(
            project="demo", hypothesis="change interface", detail="compact",
            include_proposal_envelope=True,
        ))
        self.assertEqual(result.status.value, "partial")
        self.assertIn("response_essential_fields_require_expansion", result.warnings)
        self.assertTrue(result.data["response_coverage"]["expansion_required"])
        self.assertIn("proposal_envelope", result.data["response_coverage"]["essential_fields_omitted"])
        self.assertIn("detail=exact", result.data["response_coverage"]["expansion_route"])
        self.assertLessEqual(size(result), 16 * 1024)

        exact = impact_preview(snapshot, ImpactPreviewInput(
            project="demo", hypothesis="change interface", detail="exact",
            include_proposal_envelope=True,
        ))
        self.assertNotIn("response_essential_fields_require_expansion", exact.warnings)
        self.assertEqual(len(exact.data["proposal_envelope"]["observation_preconditions"]["worktrees"]), 240)
        self.assertLessEqual(size(exact), 512 * 1024)


if __name__ == "__main__":
    unittest.main()
