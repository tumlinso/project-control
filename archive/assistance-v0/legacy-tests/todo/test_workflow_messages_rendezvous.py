from __future__ import annotations

import json
import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.models import TodoError
from todo_orchestrator.readiness import ready_tasks
from todo_orchestrator.workflow.messages import MessageService, _insert_message
from todo_orchestrator.workflow.rendezvous import RendezvousService


class WorkflowMessagesRendezvousTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        self.repo.apply(
            base_plan(
                [
                    safe_task("A", "src/a"),
                    safe_task("B", "src/b"),
                    safe_task("C", "src/c"),
                    safe_task("JALL", "src/jall", depends_on=[{"type": "barrier", "barrier_id": "BR-ALL"}]),
                    safe_task("JQ", "src/jq", depends_on=[{"type": "barrier", "barrier_id": "BR-Q"}]),
                    safe_task("JP", "src/jp", depends_on=[{"type": "barrier", "barrier_id": "BR-P"}]),
                ],
                decisions=[{"id": "FORMAT", "title": "Format", "allowed": ["json", "cbor"]}],
                interfaces=[{"id": "API", "owner_task_id": "A", "state": "frozen", "version": "1"}],
                barriers=[
                    {"id": "BR-ALL", "title": "All", "mode": "all", "requirements": [{"type": "task", "id": "A", "state": "done"}]},
                    {"id": "BR-Q", "title": "Quorum", "mode": "all", "requirements": [{"type": "task", "id": "A", "state": "done"}]},
                    {"id": "BR-P", "title": "Producers", "mode": "all", "requirements": [{"type": "task", "id": "A", "state": "done"}]},
                ],
            )
        )
        self._seed_run()
        self.interface_calls: list[dict[str, object]] = []

        def interface_hook(conn, payload, revision):
            interface_id = str(payload.get("interface_id", ""))
            version = str(payload.get("version", ""))
            row = conn.execute("SELECT 1 FROM interfaces WHERE id=?", (interface_id,)).fetchone()
            if not row or not version:
                raise TodoError("invalid_interface_change", "Missing interface/version")
            conn.execute(
                "UPDATE interfaces SET state='revised',version=?,revision=? WHERE id=?",
                (version, revision, interface_id),
            )
            result = {"interface_id": interface_id, "version": version, "invalidated_consumers": ["B"]}
            self.interface_calls.append(result)
            return result

        self.messages = MessageService(self.repo.service.db, interface_change_hook=interface_hook)
        self.rendezvous = RendezvousService(self.repo.service.db)

    def tearDown(self) -> None:
        self.repo.close()

    def mutate(self, operation):
        return self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="test",
            entity_id=None,
            event_type="test.seeded",
            payload={},
            operation=operation,
        )[0]

    def _seed_run(self) -> None:
        def operation(conn, revision):
            now = "2026-08-27T00:00:00Z"
            conn.execute(
                "INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('RUN','JALL',?,?,?)",
                (now, now, revision),
            )
            lanes = [
                ("ROOT", None, "coordinator"),
                ("L1", "ROOT", "implementer"),
                ("L2", "ROOT", "implementer"),
                ("VAL", "ROOT", "validator"),
            ]
            conn.executemany(
                "INSERT INTO workflow_lanes(id,run_id,parent_lane_id,role,created_at,updated_at,revision) "
                "VALUES(?,'RUN',?,?,?, ?,?)",
                [(lane, parent, role, now, now, revision) for lane, parent, role in lanes],
            )
            queue = [
                ("ROOT", 0, "JALL"), ("ROOT", 1, "JQ"), ("ROOT", 2, "JP"),
                ("L1", 0, "A"), ("L2", 0, "B"), ("VAL", 0, "C"),
            ]
            conn.executemany(
                "INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) "
                "VALUES(?,?,?,'queued',?,?)",
                [(lane, position, task, now, revision) for lane, position, task in queue],
            )

        self.mutate(operation)

    def publish(self, **overrides):
        values = {
            "capability_class": "first_class",
            "run_id": "RUN",
            "author_lane_id": "ROOT",
            "kind": "status",
            "payload": {"summary": "bounded"},
            "recipients": [{"type": "run", "id": "RUN"}],
        }
        values.update(overrides)
        return self.messages.publish(**values)

    def create_rendezvous(self, identifier: str, mode: str, join_task: str, barrier: str, participants, **extra):
        return self.rendezvous.create(
            capability_class="first_class",
            run_id="RUN",
            author_lane_id="ROOT",
            mode=mode,
            join_task_id=join_task,
            barrier_id=barrier,
            participants=participants,
            rendezvous_id=identifier,
            **extra,
        )

    def arrive(self, identifier: str, lane: str, task: str, **extra):
        self.mutate(lambda conn, revision: conn.execute(
            "UPDATE tasks SET status='done',result='implemented',revision=? WHERE id=?", (revision, task)
        ))
        values = {
            "capability_class": "first_class",
            "run_id": "RUN",
            "lane_id": lane,
            "rendezvous_id": identifier,
            "task_id": task,
            "summary": f"{task} complete",
            "base_source_identity": "base",
            "final_source_identity": f"final-{task}",
            "artifact": {"kind": "commit", "ref": f"commit-{task}"},
            "interfaces": {"produced": [], "consumed": []},
            "evidence": [{"gate": f"gate-{task}"}],
            "warnings": [],
            "context_version": 2,
        }
        values.update(extra)
        return self.rendezvous.arrive(**values)

    def test_recipient_filtering_cursors_and_idempotent_sync(self) -> None:
        direct = self.publish(recipients=[{"type": "lane", "id": "L1"}], message_id="M1")
        self.publish(recipients=[{"type": "lane", "id": "L2"}], message_id="M2")
        role = self.publish(recipients=[{"type": "role", "id": "implementer"}], message_id="M3")
        first = self.messages.sync(capability_class="first_class", run_id="RUN", lane_id="L1")
        self.assertEqual([item["id"] for item in first["messages"]], ["M1", "M3"])
        self.assertEqual(first["cursor"], role["message"]["revision"])
        before_empty = self.repo.service.db.revision()
        second = self.messages.sync(capability_class="first_class", run_id="RUN", lane_id="L1")
        self.assertEqual(second["messages"], [])
        self.assertEqual(second["cursor"], first["cursor"])
        self.assertEqual(self.repo.service.db.revision(), before_empty)
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_message_receipts WHERE lane_id='L1'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT context_cursor FROM workflow_lanes WHERE id='L1'").fetchone()[0], direct["message"]["revision"] + 2)

    def test_blocking_question_and_linked_answer_resolve_transactionally(self) -> None:
        question = self.publish(
            kind="question",
            payload={"question": "Which format?"},
            recipients=[{"type": "lane", "id": "L1"}],
            blocking=True,
            message_id="QUESTION",
        )
        synced = self.messages.sync(capability_class="first_class", run_id="RUN", lane_id="L1")
        self.assertEqual(synced["blocking"], ["QUESTION"])
        answer = self.messages.answer(
            capability_class="first_class",
            run_id="RUN",
            author_lane_id="L1",
            question_id="QUESTION",
            payload={"answer": "json"},
            message_id="ANSWER",
        )
        self.assertEqual(answer["message"]["linked_message_id"], "QUESTION")
        with self.repo.service.db.read() as conn:
            row = conn.execute("SELECT state,resolved_at FROM workflow_messages WHERE id='QUESTION'").fetchone()
            event = conn.execute("SELECT payload_json FROM events WHERE revision=?", (answer["project_revision"],)).fetchone()
        self.assertEqual(row["state"], "resolved")
        self.assertIsNotNone(row["resolved_at"])
        self.assertEqual(json.loads(event[0])["resolved_question_id"], "QUESTION")

    def test_same_revision_messages_are_not_lost_when_budget_splits_delivery(self) -> None:
        def operation(conn, revision):
            for identifier in ("SAME-A", "SAME-B"):
                _insert_message(
                    conn, revision, run_id="RUN", author_lane_id="ROOT", task_id=None,
                    kind="status", payload={"summary": identifier + ("x" * 3900)},
                    recipients=[{"type": "lane", "id": "L1"}], references=[],
                    blocking=False, linked_message_id=None, message_id=identifier,
                )
        self.mutate(operation)
        first = self.messages.sync(
            capability_class="first_class", run_id="RUN", lane_id="L1"
        )
        second = self.messages.sync(
            capability_class="first_class", run_id="RUN", lane_id="L1"
        )
        self.assertEqual([item["id"] for item in first["messages"]], ["SAME-A"])
        self.assertEqual([item["id"] for item in second["messages"]], ["SAME-B"])
        self.assertEqual(first["cursor"], second["cursor"])

    def test_decision_publication_and_interface_change_use_durable_authorities(self) -> None:
        decision = self.publish(
            kind="decision",
            payload={"decision_id": "FORMAT", "value": "json", "rationale": "bounded"},
            message_id="DECISION",
        )
        self.assertEqual(decision["durable_change"]["decision_id"], "FORMAT")
        interface = self.publish(
            kind="interface_change",
            payload={"interface_id": "API", "version": "2"},
            references=[{"type": "interface", "id": "API"}],
            message_id="INTERFACE",
        )
        self.assertEqual(interface["durable_change"]["invalidated_consumers"], ["B"])
        self.assertEqual(len(self.interface_calls), 1)
        with self.repo.service.db.read() as conn:
            self.assertEqual(json.loads(conn.execute("SELECT value_json FROM decisions WHERE id='FORMAT'").fetchone()[0]), "json")
            self.assertEqual(conn.execute("SELECT version FROM interfaces WHERE id='API'").fetchone()[0], "2")

    def test_messages_are_bounded_and_local_children_are_parent_mediated(self) -> None:
        with self.assertRaises(TodoError) as child:
            self.publish(capability_class="child")
        self.assertEqual(child.exception.code, "child_run_communication_forbidden")
        with self.assertRaises(TodoError) as oversized:
            self.publish(payload={"summary": "x" * 5000})
        self.assertEqual(oversized.exception.code, "workflow_message_too_large")
        with self.assertRaises(TodoError) as bulk:
            self.publish(payload={"stdout": "even a short raw stream is an artifact reference"})
        self.assertEqual(bulk.exception.code, "workflow_message_bulk_content_forbidden")
        with self.assertRaises(TodoError) as reference_bulk:
            self.publish(references=[{"type": "artifact", "stderr": "raw"}])
        self.assertEqual(reference_bulk.exception.code, "workflow_message_bulk_content_forbidden")
        with self.assertRaises(TodoError) as reference_secret:
            self.publish(references=[{"type": "artifact", "worker_token": "secret"}])
        self.assertEqual(reference_secret.exception.code, "workflow_message_secret_forbidden")
        with self.assertRaises(TodoError) as client_secret:
            self.publish(payload={"client_secret": "secret"})
        self.assertEqual(client_secret.exception.code, "workflow_message_secret_forbidden")
        with self.assertRaises(TodoError) as recovery_approval:
            self.publish(payload={"recovery_approval": "owner-authority"})
        self.assertEqual(recovery_approval.exception.code, "workflow_message_secret_forbidden")
        with self.assertRaises(TodoError) as approval_reference:
            self.publish(references=[{"type": "recovery", "recovery_approval": "owner-authority"}])
        self.assertEqual(approval_reference.exception.code, "workflow_message_secret_forbidden")
        with self.assertRaises(TodoError) as packet:
            self.publish(references=[{"type": "source", "packet": "broad context"}])
        self.assertEqual(packet.exception.code, "workflow_message_bulk_content_forbidden")
        near_limit = self.publish(payload={"summary": "x" * 3900}, message_id="NEAR-LIMIT")
        delivered = self.messages.sync(capability_class="first_class", run_id="RUN", lane_id="L2")
        self.assertIn(near_limit["message"]["id"], {item["id"] for item in delivered["messages"]})
        parent = self.publish(
            author_lane_id="L1",
            task_id="A",
            kind="artifact",
            payload={"summary": "accepted child candidate after parent validation"},
            references=[{"type": "child_result_candidate", "id": "candidate-1", "hash": "sha256"}],
            message_id="PARENT-PUBLISHED",
        )
        self.assertEqual(parent["message"]["author_lane_id"], "L1")
        self.assertNotIn("child_execution_id", parent["message"])

    def test_all_rendezvous_is_idempotent_and_atomically_opens_join(self) -> None:
        self.create_rendezvous(
            "RV-ALL",
            "all",
            "JALL",
            "BR-ALL",
            [{"lane_id": "L1"}, {"lane_id": "L2"}],
        )
        with self.repo.service.db.read() as conn:
            self.assertNotIn("JALL", {item["task_id"] for item in ready_tasks(conn)})
        first = self.arrive("RV-ALL", "L1", "A")
        self.assertFalse(first["condition"]["satisfied"])
        second = self.arrive("RV-ALL", "L2", "B")
        self.assertTrue(second["condition"]["satisfied"])
        duplicate = self.arrive("RV-ALL", "L2", "B")
        self.assertTrue(duplicate["duplicate"])
        with self.repo.service.db.read() as conn:
            barrier = conn.execute("SELECT state,revision FROM barriers WHERE id='BR-ALL'").fetchone()
            rendezvous = conn.execute("SELECT state,revision FROM workflow_rendezvous WHERE id='RV-ALL'").fetchone()
            ready = {item["task_id"] for item in ready_tasks(conn)}
            arrival_messages = conn.execute(
                "SELECT COUNT(*) FROM workflow_messages WHERE kind='rendezvous_arrival' AND payload_json LIKE '%RV-ALL%'"
            ).fetchone()[0]
        self.assertEqual(barrier["state"], "open")
        self.assertEqual(rendezvous["state"], "satisfied")
        self.assertEqual(barrier["revision"], rendezvous["revision"])
        self.assertIn("JALL", ready)
        self.assertEqual(arrival_messages, 2)

    def test_quorum_and_designated_producer_modes(self) -> None:
        self.create_rendezvous(
            "RV-Q",
            "quorum",
            "JQ",
            "BR-Q",
            [{"lane_id": "L1"}, {"lane_id": "L2"}, {"lane_id": "VAL"}],
            quorum=2,
        )
        self.arrive("RV-Q", "L1", "A")
        quorum = self.arrive("RV-Q", "VAL", "C")
        self.assertTrue(quorum["condition"]["satisfied"])

        self.create_rendezvous(
            "RV-P",
            "producers",
            "JP",
            "BR-P",
            [{"lane_id": "L1", "producer": True}, {"lane_id": "L2"}, {"lane_id": "VAL", "producer": True}],
            required_roles=["validator"],
        )
        nonproducer = self.arrive("RV-P", "L2", "B")
        self.assertFalse(nonproducer["condition"]["satisfied"])
        self.arrive("RV-P", "L1", "A")
        producer = self.arrive("RV-P", "VAL", "C")
        self.assertTrue(producer["condition"]["satisfied"])

    def test_invalid_participants_and_local_children_cannot_arrive(self) -> None:
        self.create_rendezvous("RV-BOUNDARY", "all", "JALL", "BR-ALL", [{"lane_id": "L1"}])
        with self.assertRaises(TodoError) as child:
            self.arrive("RV-BOUNDARY", "L1", "A", capability_class="child")
        self.assertEqual(child.exception.code, "child_run_communication_forbidden")
        with self.assertRaises(TodoError) as outsider:
            self.arrive("RV-BOUNDARY", "L2", "B")
        self.assertEqual(outsider.exception.code, "invalid_rendezvous_participant")
        with self.repo.service.db.read() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_rendezvous_arrivals)")}
            self.assertNotIn("child_execution_id", columns)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_rendezvous_arrivals WHERE rendezvous_id='RV-BOUNDARY'").fetchone()[0], 0)

    def test_incomplete_parent_task_cannot_arrive(self) -> None:
        self.create_rendezvous("RV-INCOMPLETE", "all", "JALL", "BR-ALL", [{"lane_id": "L1"}])
        with self.assertRaises(TodoError) as caught:
            self.rendezvous.arrive(
                capability_class="first_class", run_id="RUN", lane_id="L1",
                rendezvous_id="RV-INCOMPLETE", task_id="A", summary="too soon",
                base_source_identity="base", final_source_identity="final",
                artifact={"kind": "commit", "ref": "commit"}, interfaces={},
                evidence=[{"gate": "pending"}], warnings=[], context_version=1,
            )
        self.assertEqual(caught.exception.code, "rendezvous_parent_task_incomplete")

    def test_arrival_invalidation_revokes_satisfaction_without_file_mutation(self) -> None:
        self.create_rendezvous("RV-INVALID", "all", "JALL", "BR-ALL", [{"lane_id": "L1"}])
        self.arrive("RV-INVALID", "L1", "A")
        result = self.rendezvous.invalidate_arrival(
            capability_class="first_class",
            run_id="RUN",
            author_lane_id="VAL",
            rendezvous_id="RV-INVALID",
            lane_id="L1",
            reason="gate evidence revoked",
        )
        self.assertFalse(result["condition"]["satisfied"])
        revalidated = self.arrive(
            "RV-INVALID",
            "L1",
            "A",
            final_source_identity="final-A-corrected",
            artifact={"kind": "patch", "ref": "patch-A-corrected"},
        )
        self.assertTrue(revalidated["revalidated"])
        self.assertTrue(revalidated["condition"]["satisfied"])
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM barriers WHERE id='BR-ALL'").fetchone()[0], "open")
            self.assertEqual(conn.execute("SELECT state FROM workflow_rendezvous_arrivals WHERE rendezvous_id='RV-INVALID'").fetchone()[0], "valid")

    def test_terminal_join_invalidation_requires_owner_recovery(self) -> None:
        self.create_rendezvous("RV-TERMINAL", "all", "JALL", "BR-ALL", [{"lane_id": "L1"}])
        self.arrive("RV-TERMINAL", "L1", "A")
        self.mutate(lambda conn, revision: conn.execute(
            "UPDATE tasks SET status='done',result='integrated',revision=? WHERE id='JALL'", (revision,)
        ))
        with self.assertRaises(TodoError) as caught:
            self.rendezvous.invalidate_arrival(
                capability_class="first_class", run_id="RUN", author_lane_id="VAL",
                rendezvous_id="RV-TERMINAL", lane_id="L1", reason="late revocation",
            )
        self.assertEqual(caught.exception.code, "rendezvous_terminal_invalidation_requires_recovery")

    def test_arrival_invalidation_is_scoped_to_its_run(self) -> None:
        self.create_rendezvous("RV-RUN-SCOPE", "all", "JALL", "BR-ALL", [{"lane_id": "L1"}])
        self.arrive("RV-RUN-SCOPE", "L1", "A")
        def other_run(conn, revision):
            now = "2026-08-27T00:00:00Z"
            conn.execute(
                "INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('OTHER','JALL',?,?,?)",
                (now, now, revision),
            )
            conn.execute(
                "INSERT INTO workflow_lanes(id,run_id,role,created_at,updated_at,revision) "
                "VALUES('OTHER-VAL','OTHER','validator',?,?,?)",
                (now, now, revision),
            )
        self.mutate(other_run)
        with self.assertRaises(TodoError) as caught:
            self.rendezvous.invalidate_arrival(
                capability_class="first_class", run_id="OTHER", author_lane_id="OTHER-VAL",
                rendezvous_id="RV-RUN-SCOPE", lane_id="L1", reason="cross-run attempt",
            )
        self.assertEqual(caught.exception.code, "rendezvous_arrival_not_found")
        with self.repo.service.db.read() as conn:
            state = conn.execute(
                "SELECT state FROM workflow_rendezvous_arrivals WHERE rendezvous_id='RV-RUN-SCOPE' AND lane_id='L1'"
            ).fetchone()[0]
        self.assertEqual(state, "valid")

    def test_satisfied_rendezvous_does_not_clear_attention_for_closed_composite_barrier(self) -> None:
        self.create_rendezvous("RV-COMPOSITE", "all", "JALL", "BR-ALL", [{"lane_id": "L1"}])
        def add_requirement(conn, revision):
            conn.execute(
                "INSERT INTO barrier_requirements(barrier_id,type,entity_id,required_state,dispositions_json) "
                "VALUES('BR-ALL','task','C','done','[]')"
            )
            conn.execute(
                "UPDATE tasks SET status='attention_required',attention_reason='other requirement pending',revision=? WHERE id='JALL'",
                (revision,),
            )
        self.mutate(add_requirement)
        arrival = self.arrive("RV-COMPOSITE", "L1", "A")
        self.assertTrue(arrival["condition"]["satisfied"])
        self.assertFalse(arrival["condition"]["join_ready"])
        with self.repo.service.db.read() as conn:
            task = conn.execute("SELECT status,attention_reason FROM tasks WHERE id='JALL'").fetchone()
        self.assertEqual(task["status"], "attention_required")
        self.assertEqual(task["attention_reason"], "other requirement pending")


if __name__ == "__main__":
    unittest.main()
