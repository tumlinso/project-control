from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.models import TodoError
from todo_orchestrator.workflow.capabilities import WorkflowCapabilityLocator
from todo_orchestrator.workflow.protocol import WorkflowProtocol
from todo_orchestrator.workflow.service import WorkflowKernel
from todo_orchestrator.workflow.workspaces import WorkspaceService


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class FakeLocalWorker:
    def delegate(self, **kwargs):
        return {"status": "running"}

    def collect(self, **kwargs):
        return {
            "status": "candidate_available",
            "kind": "source_finding",
            "result": {"summary": "bounded subordinate finding"},
            "artifacts": [],
        }


class WorkflowDogfoodTests(unittest.TestCase):
    """One disposable end-to-end run proving the first-class/child boundary."""

    def setUp(self) -> None:
        self.repo = V2Repo()
        git(self.repo.root, "config", "user.email", "dogfood@example.invalid")
        git(self.repo.root, "config", "user.name", "Workflow Dogfood")
        (self.repo.root / "shared.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        for path in ("coord", "a2", "b2", "validate", "integrate", "contract"):
            target = self.repo.root / path
            target.mkdir(parents=True, exist_ok=True)
            (target / "state.txt").write_text(f"{path}\n", encoding="utf-8")
        git(self.repo.root, "add", ".")
        git(self.repo.root, "commit", "-qm", "dogfood base")
        self.base = git(self.repo.root, "rev-parse", "HEAD")

        tasks = [
            {"id": "ROOT", "kind": "epic", "title": "Dogfood", "objective": "Parallel protocol dogfood"},
            safe_task("COORD", "coord", priority=50),
            safe_task("A1", "shared.txt", priority=40),
            safe_task("A2", "a2", priority=30),
            safe_task("B1", "shared.txt", priority=40),
            safe_task("B2", "b2", priority=30),
            safe_task("VAL", "validate", priority=20),
            safe_task(
                "INT", "integrate", priority=10, parallel_policy="integration_exclusive",
                depends_on=[
                    {"type": "barrier", "barrier_id": "BR-ALL"},
                    {"type": "barrier", "barrier_id": "BR-Q"},
                ],
            ),
        ]
        plan = base_plan(
            tasks,
            decisions=[{"id": "FORMAT", "title": "Format", "allowed": ["json", "cbor"]}],
            barriers=[
                {"id": "BR-ALL", "title": "All", "mode": "all", "requirements": [
                    {"type": "task", "id": "A2", "state": "done"},
                    {"type": "task", "id": "B2", "state": "done"},
                ]},
                {"id": "BR-Q", "title": "Quorum", "mode": "all", "requirements": [
                    {"type": "task", "id": "A2", "state": "done"},
                ]},
            ],
        )
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN",
            "root_task_id": "ROOT",
            "charter": {
                "objective": "Prove parallel first-class lanes and subordinate local children",
                "invariants": ["lanes are serial", "children are not lanes"],
                "acceptance_conditions": ["all tasks terminal", "no child owns a todo"],
            },
            "lanes": [
                {"id": "COORD-L", "role": "coordinator", "tasks": ["COORD"]},
                {"id": "A-L", "parent_lane_id": "COORD-L", "role": "implementer", "tasks": ["A1", "A2"], "workspace": {"mode": "isolated_merge"}},
                {"id": "B-L", "parent_lane_id": "COORD-L", "role": "implementer", "tasks": ["B1", "B2"], "workspace": {"mode": "isolated_merge"}},
                {"id": "VAL-L", "parent_lane_id": "COORD-L", "role": "validator", "tasks": ["VAL"], "workspace": {"mode": "read_shared"}},
                {"id": "INT-L", "parent_lane_id": "COORD-L", "role": "integrator", "tasks": ["INT"], "workspace": {"mode": "exclusive"}},
            ],
            "rendezvous": [
                {"id": "RV-ALL", "mode": "all", "participants": ["A-L", "B-L"], "join_task_id": "INT", "barrier_id": "BR-ALL"},
                {"id": "RV-Q", "mode": "quorum", "quorum": 1, "participants": ["A-L", "B-L"], "join_task_id": "INT", "barrier_id": "BR-Q"},
            ],
        }]
        self.repo.apply(plan)
        self.managed_temp = tempfile.TemporaryDirectory()
        self.workspaces = WorkspaceService(
            self.repo.service.db,
            managed_root=Path(self.managed_temp.name),
            repository_identity_resolver=lambda root: "dogfood-repo",
        )
        for lane, mode in (("A-L", "isolated_merge"), ("B-L", "isolated_merge"), ("INT-L", "exclusive")):
            self.workspaces.create_workspace(
                repository_root=self.repo.root,
                repository_identity="dogfood-repo",
                run_id="RUN",
                lane_id=lane,
                mode=mode,
                base_commit=self.base,
                worktree_path=Path(self.managed_temp.name) / lane.lower(),
                branch=f"dogfood-{lane.lower()}",
                integration_task_id="INT",
            )
        self.locator_temp = tempfile.TemporaryDirectory()
        self.locator = WorkflowCapabilityLocator(Path(self.locator_temp.name))
        self.protocol = WorkflowProtocol(
            WorkflowKernel(locator=self.locator, local_worker_adapter=FakeLocalWorker()), self.locator
        )
        self.old_thread = os.environ.get("CODEX_THREAD_ID")

    def tearDown(self) -> None:
        if self.old_thread is None:
            os.environ.pop("CODEX_THREAD_ID", None)
        else:
            os.environ["CODEX_THREAD_ID"] = self.old_thread
        self.locator_temp.cleanup()
        self.managed_temp.cleanup()
        self.repo.close()

    def claim(self, thread: str, task: str) -> dict[str, object]:
        os.environ["CODEX_THREAD_ID"] = thread
        return self.protocol.next_task(repo_root=str(self.repo.root), task_id=task)

    def finish(self, claimed: dict[str, object]) -> dict[str, object]:
        return self.protocol.finish_task(
            workflow_handle=str(claimed["workflow_handle"]), action="complete", disposition="implemented"
        )

    def test_complete_parallel_run_with_subordinate_child(self) -> None:
        coord = self.claim("dogfood-coordinator", "COORD")
        a1 = self.claim("dogfood-a", "A1")
        b1 = self.claim("dogfood-b", "B1")
        validator = self.claim("dogfood-validator", "VAL")
        self.assertTrue(all(item["status"] == "claimed" for item in (coord, a1, b1, validator)))
        self.assertTrue(all(len(json.dumps(item, sort_keys=True).encode()) <= 8192 for item in (coord, a1, b1, validator)))
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_dispatches WHERE state='active'").fetchone()[0], 4)
        with self.assertRaises(TodoError) as serial_violation:
            self.claim("dogfood-a-second", "A2")
        self.assertEqual(serial_violation.exception.code, "workflow_lane_order_violation")

        decision = self.protocol.coordinate_task(
            workflow_handle=str(coord["workflow_handle"]), action="message",
            payload={"kind": "decision", "payload": {"decision_id": "FORMAT", "value": "json"}, "recipients": [{"type": "run", "id": "RUN"}]},
        )
        question = self.protocol.coordinate_task(
            workflow_handle=str(a1["workflow_handle"]), action="message",
            payload={"kind": "question", "payload": {"question": "Use canonical JSON?"}, "recipients": [{"type": "lane", "id": "B-L"}], "blocking": True},
        )
        synced = self.protocol.coordinate_task(workflow_handle=str(b1["workflow_handle"]), action="sync", payload={})
        self.assertIn(question["message"]["id"], synced["blocking"])
        self.protocol.coordinate_task(
            workflow_handle=str(b1["workflow_handle"]), action="answer",
            payload={"question_id": question["message"]["id"], "payload": {"answer": "yes"}},
        )
        self.assertEqual(decision["message"]["kind"], "decision")

        self.locator.forget(str(a1["workflow_handle"]))
        a1 = self.claim("dogfood-a", "A1")
        self.assertEqual(a1["status"], "resumed")

        self.finish(coord)
        self.finish(validator)
        self.finish(a1)
        self.finish(b1)
        a2 = self.claim("dogfood-a", "A2")
        b2 = self.claim("dogfood-b", "B2")
        self.assertEqual((a2["status"], b2["status"]), ("claimed", "claimed"))
        delegated = self.protocol.delegate_task(
            workflow_handle=str(a2["workflow_handle"]), delegated_objective="Inspect only the bounded A2 source", mode="readonly"
        )
        self.assertLessEqual(delegated["packet_size_bytes"], 4096)
        collected = self.protocol.collect_delegation(delegation_handle=str(delegated["delegation_handle"]))
        self.assertFalse(collected["parent_task_completed"])
        self.protocol.coordinate_task(
            workflow_handle=str(a2["workflow_handle"]), action="accept_child",
            payload={"child_execution_id": delegated["child_execution_id"]},
        )
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_lanes WHERE id=?", (delegated["child_execution_id"],)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_rendezvous_participants WHERE lane_id=?", (delegated["child_execution_id"],)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id='A2'").fetchone()[0], "in_progress")
        self.finish(a2)
        self.finish(b2)

        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM workflow_rendezvous WHERE id='RV-ALL'").fetchone()[0], "satisfied")
            self.assertEqual(conn.execute("SELECT state FROM workflow_rendezvous WHERE id='RV-Q'").fetchone()[0], "satisfied")
            # Quorum is terminal after the first valid arrival; the all-mode
            # rendezvous records both producers, for three durable arrivals.
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_rendezvous_arrivals").fetchone()[0], 3)
        integrator = self.claim("dogfood-integrator", "INT")
        self.assertEqual(integrator["status"], "claimed")
        self.finish(integrator)

        with self.repo.service.db.read() as conn:
            evidence = {
                "protocol_version": 2,
                "first_class_lanes": conn.execute("SELECT COUNT(*) FROM workflow_lanes WHERE run_id='RUN'").fetchone()[0],
                "subordinate_children": conn.execute("SELECT COUNT(*) FROM child_executions").fetchone()[0],
                "active_claims": conn.execute("SELECT COUNT(*) FROM claims WHERE state='active'").fetchone()[0],
                "active_dispatches": conn.execute("SELECT COUNT(*) FROM workflow_dispatches WHERE state='active'").fetchone()[0],
                "rendezvous_satisfied": conn.execute("SELECT COUNT(*) FROM workflow_rendezvous WHERE state='satisfied'").fetchone()[0],
                "blocking_messages": conn.execute("SELECT COUNT(*) FROM workflow_messages WHERE blocking=1 AND state!='resolved'").fetchone()[0],
                "child_is_lane": False,
                "child_is_rendezvous_participant": False,
                "child_packet_bytes": delegated["packet_size_bytes"],
                "max_capsule_bytes": max(len(json.dumps(item, sort_keys=True).encode()) for item in (coord, a1, b1, validator, a2, b2, integrator)),
            }
        self.assertEqual(evidence["active_claims"], 0)
        self.assertEqual(evidence["active_dispatches"], 0)
        self.assertEqual(evidence["blocking_messages"], 0)
        self.assertEqual(evidence["rendezvous_satisfied"], 2)


if __name__ == "__main__":
    unittest.main()
