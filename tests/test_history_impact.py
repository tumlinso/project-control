from __future__ import annotations

import copy
import unittest

from project_control.models import HistoryTraceInput, ImpactPreviewInput
from project_control.services.history import history_trace
from project_control.services.impact import impact_preview

try:
    from .cellerator_fixture import cellerator_snapshot
except ImportError:
    from cellerator_fixture import cellerator_snapshot


def rich_snapshot():
    snapshot = cellerator_snapshot()
    snapshot.todo_tables["events"].extend([
        {"revision": 453, "timestamp": "2026-08-25T15:00:00Z", "event_type": "workflow_message.question", "entity_type": "message", "entity_id": "M-1", "task_id": "CE-ARCH-92"},
        {"revision": 454, "timestamp": "2026-08-25T16:00:00Z", "event_type": "interface.revised", "entity_type": "interface", "entity_id": "EXECUTION-IMAGE-V2", "task_id": "CE-ARCH-92", "caused_by": "M-1"},
        {"revision": 455, "timestamp": "2026-08-25T17:00:00Z", "event_type": "context_fragment.invalidated", "entity_type": "context_fragment", "entity_id": "CTX-1", "task_id": "CE-ARCH-92"},
        {"revision": 456, "timestamp": "2026-08-25T18:00:00Z", "event_type": "rendezvous.arrived", "entity_type": "rendezvous", "entity_id": "R-1", "task_id": "CE-ARCH-92"},
    ])
    snapshot.todo_tables["context_fragments"] = [{
        "id": "CTX-1", "task_id": "CE-ARCH-92", "version": 2, "content_hash": "c" * 64,
        "state": "invalidated", "created_at": "2026-08-25T17:00:00Z",
    }]
    snapshot.todo_workflow = {
        "available": True,
        "revision": 456,
        "active_run_id": "RUN-1",
        "runs": [{"id": "RUN-1", "root_task_id": "CE-ARCH-00", "status": "active", "lanes": []}],
        "first_class_agents": [
            {"run_id": "RUN-1", "lane_id": "L-A", "task_id": "CE-ARCH-82", "context_version": 2, "observable": True},
            {"run_id": "RUN-1", "lane_id": "L-B", "task_id": "UNRELATED", "context_version": 1, "observable": True},
        ],
        "local_children": [{"child_execution_id": "CHILD-1", "parent_task_id": "CE-ARCH-92", "parent_lane_id": "L-A", "state": "running"}],
        "blocking_messages": [{"id": "M-1", "task_id": "CE-ARCH-92", "author_lane_id": "L-A", "created_at": "2026-08-25T15:00:00Z", "kind": "question"}],
        "unresolved_questions": [],
        "rendezvous": [{"id": "R-1", "join_task_id": "CE-ARCH-92", "arrivals": [{"lane_id": "L-A", "task_id": "CE-ARCH-92", "revision": 456}]}],
        "patch_artifacts": [], "integration_queue": [], "recovery_needed": [],
        "safe_parallel_groups": [["UNRELATED"], ["CE-ARCH-82"]],
    }
    return snapshot


class HistoryImpactTests(unittest.TestCase):
    def test_history_is_chronological_coalesced_and_does_not_invent_causation(self) -> None:
        snapshot = rich_snapshot()
        result = history_trace(snapshot, HistoryTraceInput(project="cellerator", subject="CE-ARCH-92", from_revision=450))
        material = [event for event in result.data["events"] if event["revision"] is not None]
        revisions = [event["revision"] for event in material]
        self.assertEqual(revisions, sorted(revisions))
        self.assertNotIn("claim.pulsed", {event["event_type"] for event in result.data["events"]})
        causal = next(event for event in result.data["events"] if event["event_type"] == "interface.revised")
        self.assertEqual(causal["causal_relationship"], "M-1")
        self.assertEqual(causal["causal_basis"], "explicit_recorded_reference")
        adjacent = next(event for event in result.data["events"] if event["event_type"] == "task.completed")
        self.assertIsNone(adjacent["causal_relationship"])
        self.assertGreater(result.data["coalescing"]["administrative_or_heartbeat_events_omitted"], 0)

    def test_impact_distinguishes_proven_possible_unknown_stale_and_unaffected(self) -> None:
        snapshot = rich_snapshot()
        before = copy.deepcopy(snapshot.model_dump(mode="json"))
        request = ImpactPreviewInput(
            project="cellerator",
            hypothesis="Revise Execution Image v2 and its validation context",
            target_entities=["EXECUTION-IMAGE-V2", "DOES-NOT-EXIST"],
        )
        first = impact_preview(snapshot, request)
        second = impact_preview(snapshot, request)
        self.assertEqual(first.data["proven_impacts"][0]["id"], "EXECUTION-IMAGE-V2")
        self.assertTrue(first.data["possible_impacts"])
        self.assertEqual(first.data["unknown_impacts"][0]["authority_label"], "missing_evidence")
        self.assertEqual(first.data["active_lane_context_staleness"][0]["lane_id"], "L-A")
        self.assertEqual(first.data["safe_unaffected_work"], [{
            "task_ids": ["UNRELATED"], "authority_label": "derived_relationship",
            "basis": "authoritative_safe_parallel_group_disjoint_from_affected_tasks",
        }])
        self.assertFalse(first.data["proposal_envelope"]["authority_to_apply"])
        self.assertEqual(first.data["proposal_envelope"]["deterministic_digest"], second.data["proposal_envelope"]["deterministic_digest"])
        self.assertIn("CTX-1", first.data["required_preconditions"]["context_fragments"])
        self.assertIn("EXECUTION-IMAGE-V2", first.data["required_preconditions"]["interfaces"])
        self.assertEqual(before, snapshot.model_dump(mode="json"))

    def test_history_continuation_is_bound_to_query(self) -> None:
        snapshot = rich_snapshot()
        first = history_trace(snapshot, HistoryTraceInput(project="cellerator", subject="CE-ARCH-92", max_events=2))
        cursor = first.data["pagination"]["continuation_cursor"]
        self.assertIsNotNone(cursor)
        second = history_trace(snapshot, HistoryTraceInput(project="cellerator", subject="CE-ARCH-92", max_events=2, continuation_cursor=cursor))
        self.assertEqual(second.data["pagination"]["offset"], 2)
        with self.assertRaisesRegex(ValueError, "continuation_cursor_mismatch"):
            history_trace(snapshot, HistoryTraceInput(project="cellerator", subject="different", max_events=2, continuation_cursor=cursor))


if __name__ == "__main__":
    unittest.main()
