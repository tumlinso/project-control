from __future__ import annotations

import copy
import json
import unittest

from project_control.models import AuthorityComponent, CoordinationViewInput
from project_control.services.coordination import coordination_view

from tests.test_workflow_read_model import workflow_fixture


class CoordinationViewTests(unittest.TestCase):
    def snapshot(self):
        snapshot = workflow_fixture(__import__("pathlib").Path("/tmp"))
        tables = copy.deepcopy(snapshot.todo_tables)
        tables.update({
            "workflow_messages": [{
                "id": "M-1", "run_id": "RUN-1", "author_lane_id": "z-parent", "task_id": "T-A",
                "kind": "question", "payload_json": json.dumps({"question": "Freeze v2?"}),
                "references_json": json.dumps([{"type": "interface", "id": "I-1"}]),
                "blocking": 1, "state": "open", "revision": 40, "created_at": "now",
            }, {
                "id": "M-2", "run_id": "RUN-1", "author_lane_id": "a-child", "task_id": "T-A",
                "kind": "answer", "payload_json": json.dumps({"answer": "yes"}), "references_json": "[]",
                "blocking": 0, "state": "resolved", "linked_message_id": "M-1", "revision": 41, "created_at": "later",
            }],
            "workflow_message_recipients": [
                {"message_id": "M-1", "recipient_type": "lane", "recipient_id": "a-child"},
            ],
            "workflow_message_receipts": [
                {"message_id": "M-1", "lane_id": "a-child", "received_revision": 40, "received_at": "now"},
            ],
            "workflow_context_fragments": [{
                "id": "F-1", "run_id": "RUN-1", "lane_id": "a-child", "task_id": "T-A",
                "kind": "task_brief", "owner_scope_json": "{}", "version": 2,
                "content_json": json.dumps({"objective": "implement"}), "content_hash": "fh",
                "creation_revision": 30, "created_at": "before",
            }],
            "workflow_rendezvous_arrivals": [{
                "rendezvous_id": "R-1", "lane_id": "a-child", "task_id": "T-A", "summary": "done",
                "base_source_identity": "base", "final_source_identity": "final", "artifact_json": "{}",
                "interfaces_json": "{}", "evidence_json": "[]", "warnings_json": "[]",
                "context_version": 8, "state": "valid", "arrived_at": "now", "revision": 41,
            }],
            "decisions": [{"id": "D-1", "title": "Format", "value": "json", "revision": 31}],
            "interfaces": [{"id": "I-1", "owner_task_id": "T-A", "state": "frozen", "version": "2", "content_hash": "ih", "revision": 32}],
        })
        return snapshot.model_copy(update={
            "todo_tables": tables,
            "component_authority": {"todo_workflow": AuthorityComponent(
                status="available", operation="workflow", revision=42,
                read_authority_fingerprint="wf", project_uuid="project-uuid",
                observed_at=snapshot.observed_at, source_identity="todo",
            )},
        })

    def test_authoritative_categories_and_durable_enrichment_remain_distinct(self) -> None:
        result = coordination_view(self.snapshot(), CoordinationViewInput(
            project="demo", detail="expanded", include_resolved_messages=True,
        ))
        self.assertEqual(result.data["dispatches"][0]["authority"], "todo_semantic_workflow")
        self.assertEqual(result.data["first_class_agents"][0]["lane_id"], "a-child")
        self.assertEqual(result.data["subordinate_local_children"][0]["child_execution_id"], "LC-1")
        self.assertEqual(result.data["messages"][0]["payload"]["question"], "Freeze v2?")
        self.assertEqual(result.data["messages"][1]["linked_message_id"], "M-1")
        self.assertEqual(result.data["context_fragments"][0]["content"]["objective"], "implement")
        self.assertEqual(result.data["rendezvous"][0]["arrivals"][0]["summary"], "done")
        encoded = result.model_dump_json()
        self.assertNotIn("message_receipts", encoded)
        self.assertNotIn("worktree_path", encoded)
        self.assertNotIn("hostname", encoded)
        self.assertNotIn("claim_id", encoded)

    def test_filters_continuation_and_unavailable_workflow(self) -> None:
        snapshot = self.snapshot()
        filtered = coordination_view(snapshot, CoordinationViewInput(
            project="demo", lane_id="a-child", max_items=1, include_resolved_messages=True,
        ))
        self.assertEqual({item["lane_id"] for item in filtered.data["dispatches"]}, {"a-child"})
        self.assertIsNotNone(filtered.data["continuation_cursor"])
        continued = coordination_view(snapshot, CoordinationViewInput(
            project="demo", lane_id="a-child", max_items=1, include_resolved_messages=True,
            continuation_cursor=filtered.data["continuation_cursor"],
        ))
        self.assertNotEqual(filtered.data["ranking"]["offset"], continued.data["ranking"]["offset"])
        unavailable = snapshot.model_copy(update={"todo_workflow": {}, "component_authority": {}})
        degraded = coordination_view(unavailable, CoordinationViewInput(project="demo"))
        self.assertFalse(degraded.data["available"])
        self.assertIn("todo_workflow_semantic_unavailable", degraded.warnings)

    def test_observation_does_not_change_receipts_or_cursors(self) -> None:
        snapshot = self.snapshot()
        before = copy.deepcopy(snapshot.todo_tables["workflow_message_receipts"])
        cursors = [lane["context_cursor"] for lane in snapshot.todo_workflow["runs"][0]["lanes"]]
        coordination_view(snapshot, CoordinationViewInput(project="demo", detail="expanded"))
        self.assertEqual(before, snapshot.todo_tables["workflow_message_receipts"])
        self.assertEqual(cursors, [lane["context_cursor"] for lane in snapshot.todo_workflow["runs"][0]["lanes"]])


if __name__ == "__main__":
    unittest.main()
