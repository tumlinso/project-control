from __future__ import annotations

import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task
from todo_orchestrator.reporting import no_work_frontier


class V2GraphCoordinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()

    def tearDown(self) -> None:
        self.repo.close()

    def _checkpoint_plan(self):
        return base_plan(
            [
                safe_task("PRODUCER", "include", priority=10, checkpoints=[{"id": "API-FROZEN", "title": "API frozen", "publishes_interfaces": [{"id": "api", "version": "1"}]}]),
                safe_task("CONSUMER", "src/consumer", priority=5, scope={"exclusive_paths": ["src/consumer"], "read_paths": ["include/api.txt"]}, depends_on=[{"type": "checkpoint", "checkpoint_id": "API-FROZEN"}], consumes_interfaces=[{"id": "api", "required_state": "frozen", "required_version": "1"}]),
            ],
            interfaces=[{"id": "api", "owner_task_id": "PRODUCER", "contract_paths": ["include/api.txt"]}],
        )

    def test_checkpoint_unblocks_consumer_while_producer_remains_active(self) -> None:
        (self.repo.root / "include").mkdir()
        (self.repo.root / "include" / "api.txt").write_text("v1\n", encoding="utf-8")
        self.repo.apply(self._checkpoint_plan())
        producer = self.repo.service.continue_work(task_id="PRODUCER")
        self.assertEqual(self.repo.service.explain("CONSUMER")["execution"], "blocked_dependency")
        reached = self.repo.service.checkpoint("reach", "API-FROZEN", producer["claim"]["claim_token"])
        self.assertEqual(reached["state"], "reached")
        self.assertEqual(self.repo.service.explain("PRODUCER")["execution"], "claimed")
        self.assertTrue(self.repo.service.explain("CONSUMER")["ready"])
        consumer = self.repo.service.continue_work(task_id="CONSUMER")
        self.assertNotEqual(producer["claim"]["claim_id"], consumer["claim"]["claim_id"])

    def test_checkpoint_revocation_marks_active_dependent_attention_required(self) -> None:
        (self.repo.root / "include").mkdir()
        (self.repo.root / "include" / "api.txt").write_text("v1\n", encoding="utf-8")
        self.repo.apply(self._checkpoint_plan())
        producer = self.repo.service.continue_work(task_id="PRODUCER")
        self.repo.service.checkpoint("reach", "API-FROZEN", producer["claim"]["claim_token"])
        self.repo.service.continue_work(task_id="CONSUMER")
        report = self.repo.service.checkpoint("revoke", "API-FROZEN", producer["claim"]["claim_token"])
        self.assertIn("CONSUMER", report["affected_active_tasks"])
        self.assertEqual(self.repo.service.explain("CONSUMER")["execution"], "attention_required")

    def test_plan_can_declaratively_retire_existing_checkpoint(self) -> None:
        plan = self._checkpoint_plan()
        self.repo.apply(plan)
        plan["tasks"][0]["checkpoints"][0]["state"] = "revoked"
        self.repo.apply(plan)
        with self.repo.service.db.read() as conn:
            checkpoint = conn.execute(
                "SELECT state,reached_at,revoked_at FROM checkpoints WHERE id='API-FROZEN'"
            ).fetchone()
        self.assertEqual(checkpoint["state"], "revoked")
        self.assertIsNone(checkpoint["reached_at"])
        self.assertIsNotNone(checkpoint["revoked_at"])

    def test_interface_revision_invalidates_active_consumer(self) -> None:
        (self.repo.root / "include").mkdir()
        contract = self.repo.root / "include" / "api.txt"
        contract.write_text("v1\n", encoding="utf-8")
        self.repo.apply(self._checkpoint_plan())
        producer = self.repo.service.continue_work(task_id="PRODUCER")
        self.repo.service.checkpoint("reach", "API-FROZEN", producer["claim"]["claim_token"])
        self.repo.service.continue_work(task_id="CONSUMER")
        contract.write_text("v2\n", encoding="utf-8")
        revised = self.repo.service.interface("revise", "api", "2", producer["claim"]["claim_token"])
        self.assertEqual(revised["affected_active_consumers"], ["CONSUMER"])
        self.assertEqual(self.repo.service.explain("CONSUMER")["execution"], "attention_required")

    def test_barrier_fan_in_opens_only_after_all_tasks_complete(self) -> None:
        tasks = [safe_task("A", "src/a", priority=3), safe_task("B", "src/b", priority=2)]
        tasks.append({"id": "JOIN", "kind": "integration_task", "title": "Join", "parallel_policy": "integration_exclusive", "depends_on": [{"type": "barrier", "barrier_id": "FANIN"}]})
        self.repo.apply(base_plan(tasks, barriers=[{"id": "FANIN", "mode": "all", "requirements": [{"type": "task", "id": "A", "state": "done"}, {"type": "task", "id": "B", "state": "done"}]}]))
        a = self.repo.service.continue_work(task_id="A")
        self.repo.service.complete(a["claim"]["claim_token"], "implemented")
        self.assertEqual(self.repo.service.barrier("FANIN")["state"], "closed")
        self.assertEqual(self.repo.service.explain("JOIN")["execution"], "blocked_barrier")
        b = self.repo.service.continue_work(task_id="B")
        self.repo.service.complete(b["claim"]["claim_token"], "evaluated_not_promoted")
        self.assertEqual(self.repo.service.barrier("FANIN")["state"], "open")
        self.assertTrue(self.repo.service.explain("JOIN")["ready"])

    def test_unreferenced_historical_barrier_is_not_a_current_blocker(self) -> None:
        historical = safe_task("HISTORY", "src/history", status="superseded", result="superseded")
        current = safe_task(
            "CURRENT", "src/current",
            depends_on=[{"type": "barrier", "barrier_id": "CURRENT-BARRIER"}],
        )
        self.repo.apply(base_plan(
            [historical, current],
            barriers=[
                {"id": "HISTORICAL-BARRIER", "mode": "all", "requirements": [{"type": "task", "id": "HISTORY", "state": "done"}]},
                {"id": "CURRENT-BARRIER", "mode": "all", "requirements": [{"type": "task", "id": "CURRENT", "state": "done"}]},
            ],
        ))
        with self.repo.service.db.read() as conn:
            frontier = no_work_frontier(conn)
            raw_ids = {row[0] for row in conn.execute("SELECT id FROM barriers")}
        self.assertEqual([item["id"] for item in frontier["unopened_barriers"]], ["CURRENT-BARRIER"])
        self.assertEqual(raw_ids, {"CURRENT-BARRIER", "HISTORICAL-BARRIER"})

    def test_safe_conditional_activation(self) -> None:
        conditional = safe_task("CONDITIONAL", "src/conditional", depends_on=[{"type": "decision", "decision_id": "strategy", "operator": "equals", "value": "enabled"}])
        self.repo.apply(base_plan([conditional], decisions=[{"id": "strategy", "allowed": ["enabled", "disabled"], "value": "disabled"}]))
        self.assertEqual(self.repo.service.explain("CONDITIONAL")["execution"], "inactive")
        self.repo.service.decision("set", "strategy", "enabled")
        self.assertTrue(self.repo.service.explain("CONDITIONAL")["ready"])

    def test_terminal_gate_history_keeps_barrier_open_after_source_moves(self) -> None:
        (self.repo.root / "inputs").mkdir()
        source = self.repo.root / "inputs" / "contract.txt"
        source.write_text("one\n", encoding="utf-8")
        producer = safe_task("A", "src/a", gates=[{"id": "A-GATE", "type": "file_exists", "path": "inputs/contract.txt", "input_paths": ["inputs/contract.txt"], "required": True}])
        integration = {"id": "JOIN", "kind": "integration_task", "title": "Join", "parallel_policy": "integration_exclusive", "depends_on": [{"type": "barrier", "barrier_id": "GATE-BARRIER"}]}
        self.repo.apply(base_plan([producer, integration], barriers=[{"id": "GATE-BARRIER", "mode": "all", "requirements": [{"type": "gate", "id": "A-GATE", "state": "passed"}]}]))
        claim_a = self.repo.service.continue_work(task_id="A")
        self.repo.service.gate_run("A-GATE", claim_a["claim"]["claim_token"])
        self.assertEqual(self.repo.service.barrier("GATE-BARRIER")["state"], "open")
        self.repo.service.complete(claim_a["claim"]["claim_token"], "validated")
        claim_join = self.repo.service.continue_work(task_id="JOIN")
        source.write_text("two\n", encoding="utf-8")
        reconciled = self.repo.service.reconcile()
        self.assertEqual(reconciled["invalidated_gates"], [])
        self.assertEqual(self.repo.service.barrier("GATE-BARRIER")["state"], "open")
        self.assertEqual(self.repo.service.explain("JOIN")["execution"], "claimed")
        self.assertEqual(claim_join["claim"]["task_id"], "JOIN")


if __name__ == "__main__":
    unittest.main()
