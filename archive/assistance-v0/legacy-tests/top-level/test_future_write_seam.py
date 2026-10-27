from __future__ import annotations

import unittest

from project_control.models import ObservationPreconditions, WorktreePrecondition
from project_control.observer_analysis import DisabledObserverAnalysisProvider
from project_control.proposals import create_proposal, stale_preconditions, validate_proposal_preconditions
from project_control.profiles import MCPProfile, profile_policy


def _preconditions(*, revision: int = 7, head: str = "a" * 40) -> ObservationPreconditions:
    return ObservationPreconditions(
        workspace_id="demo",
        project_uuid="project-uuid",
        todo_revision=revision,
        todo_semantic_authority_fingerprint="semantic-fingerprint",
        workflow_revision=revision,
        workflow_authority_fingerprint="workflow-fingerprint",
        repository_commits={"source": head},
        worktrees={"wt-public": WorktreePrecondition(head=head, working_tree_fingerprint="dirty-fingerprint")},
        run_id="run-1",
        task_ids=["TASK-1"],
        lane_ids=["lane-1"],
        observed_at="2026-08-27T00:00:00Z",
        provider_skew={"todo_workflow": 0},
    )


class FutureWriteSeamTests(unittest.TestCase):
    def test_proposal_digest_is_deterministic_and_inert(self) -> None:
        first = create_proposal(
            intent="Consider interface revision",
            proposed_change={"interface": "v2", "consumers": ["alpha"]},
            observation_preconditions=_preconditions(),
            created_at="2026-08-27T00:00:00Z",
        )
        second = create_proposal(
            intent="Consider interface revision",
            proposed_change={"consumers": ["alpha"], "interface": "v2"},
            observation_preconditions=_preconditions(),
            created_at="2026-08-27T00:01:00Z",
        )
        self.assertEqual(first.deterministic_digest, second.deterministic_digest)
        self.assertFalse(first.authority_to_apply)
        self.assertNotIn("apply", first.model_dump())

    def test_stale_preconditions_are_field_specific(self) -> None:
        expected = _preconditions()
        current = _preconditions(revision=8, head="b" * 40)
        result = stale_preconditions(expected, current)
        self.assertTrue(result["stale"])
        fields = {item["field"] for item in result["mismatches"]}
        self.assertIn("todo_revision", fields)
        self.assertIn("repository_commits.source", fields)
        self.assertIn("worktrees.wt-public.head", fields)

    def test_timestamp_and_reported_skew_do_not_make_authority_stale(self) -> None:
        expected = _preconditions()
        current = expected.model_copy(update={
            "observed_at": "2026-08-27T01:00:00Z",
            "provider_skew": {"todo_workflow": 3},
        })
        self.assertFalse(stale_preconditions(expected, current)["stale"])

    def test_validation_never_grants_apply_authority(self) -> None:
        proposal = create_proposal(
            intent="Consider change",
            proposed_change={"task": "TASK-1"},
            observation_preconditions=_preconditions(),
        )
        result = validate_proposal_preconditions(proposal, _preconditions())
        self.assertTrue(result["proposal_digest_valid"])
        self.assertFalse(result["authority_to_apply"])
        self.assertFalse(result["stale"])

    def test_trusted_mutator_is_distinct_from_inert_proposal(self) -> None:
        proposal = create_proposal(
            intent="Apply native Todo plan",
            proposed_change={"schema_version": 2, "tasks": []},
            observation_preconditions=_preconditions(),
        )
        self.assertFalse(proposal.authority_to_apply)
        self.assertEqual("stdio", profile_policy(MCPProfile.MUTATOR).transport)
        self.assertIn("apply_plan", profile_policy(MCPProfile.MUTATOR).tool_names)
        self.assertNotIn("apply_plan", profile_policy(MCPProfile.OBSERVER).tool_names)
        self.assertNotIn("apply_plan", profile_policy(MCPProfile.CODEX).tool_names)

    def test_observer_analysis_is_explicitly_disabled(self) -> None:
        provider = DisabledObserverAnalysisProvider()
        before = {"source": "immutable"}
        result = provider.analyze(before)
        self.assertFalse(provider.available)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["mutation_authority"])
        self.assertEqual(before, {"source": "immutable"})


if __name__ == "__main__":
    unittest.main()
