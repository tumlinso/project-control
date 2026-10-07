from __future__ import annotations

import json
import subprocess
import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task
from todo_orchestrator.models import TodoError


class TerminalCheckpointFinalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        subprocess.run(["git", "config", "user.email", "terminal-test@example.invalid"], cwd=self.repo.root, check=True)
        subprocess.run(["git", "config", "user.name", "Terminal Test"], cwd=self.repo.root, check=True)
        source = self.repo.root / "src" / "input.txt"
        source.parent.mkdir(parents=True)
        source.write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/input.txt"], cwd=self.repo.root, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=self.repo.root, check=True)

    def tearDown(self) -> None:
        self.repo.close()

    @staticmethod
    def _task(task_id: str, checkpoint_id: str, gate_id: str) -> dict[str, object]:
        return safe_task(
            task_id,
            f"src/{task_id.lower()}",
            checkpoints=[{"id": checkpoint_id, "title": checkpoint_id}],
            gates=[{
                "id": gate_id,
                "type": "file_exists",
                "path": "src/input.txt",
                "input_paths": ["src/input.txt"],
                "track_git_head": True,
                "checkpoint_id": checkpoint_id,
                "required": True,
            }],
        )

    @classmethod
    def _task_with_owner_gate_only(cls, task_id: str, checkpoint_id: str, gate_id: str) -> dict[str, object]:
        task = cls._task(task_id, checkpoint_id, gate_id)
        task["gates"][0].pop("checkpoint_id")
        return task

    def _complete(self, task_id: str = "T", checkpoint_id: str = "C", gate_id: str = "G", disposition: str = "implemented"):
        self.repo.apply(base_plan([self._task(task_id, checkpoint_id, gate_id)]))
        capsule = self.repo.service.continue_work(task_id=task_id)
        token = capsule["claim"]["claim_token"]
        self.repo.service.gate_run(gate_id, token)
        return self.repo.service.complete(token, disposition)

    def _move_head(self, name: str = "unrelated.txt") -> None:
        (self.repo.root / name).write_text("movement\n", encoding="utf-8")
        subprocess.run(["git", "add", name], cwd=self.repo.root, check=True)
        subprocess.run(["git", "commit", "-qm", f"move head with {name}"], cwd=self.repo.root, check=True)

    def _make_legacy_pending(self, *, gate_status: str = "passed", gate_valid: int = 1) -> None:
        with self.repo.service.db.connect() as conn:
            handoff = conn.execute(
                "SELECT id,payload_json FROM handoffs WHERE task_id='T' AND kind='complete' ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            payload = json.loads(handoff["payload_json"])
            for gate in payload["gates"]:
                if gate["id"] == "G":
                    gate["status"] = gate_status
                    gate["valid"] = bool(gate_valid)
            conn.execute("UPDATE handoffs SET payload_json=? WHERE id=?", (json.dumps(payload, sort_keys=True), handoff["id"]))
            conn.execute("DELETE FROM task_completion_gates WHERE task_id='T'")
            conn.execute(
                "UPDATE tasks SET completion_revision=NULL,completion_git_head=NULL,completion_commit=NULL WHERE id='T'"
            )
            conn.execute("UPDATE checkpoints SET state='pending',reached_at=NULL WHERE id='C'")
            conn.execute("UPDATE gates SET status=?,valid=? WHERE id='G'", (gate_status, gate_valid))
            conn.commit()

    def test_atomic_success_reaches_owned_checkpoint_before_claim_release(self) -> None:
        completed = self._complete()
        self.assertEqual(completed["reached_checkpoints"], ["C"])
        with self.repo.service.db.read() as conn:
            task = conn.execute("SELECT status,result,revision,completion_revision FROM tasks WHERE id='T'").fetchone()
            checkpoint = conn.execute("SELECT state,revision FROM checkpoints WHERE id='C'").fetchone()
            claim = conn.execute("SELECT state FROM claims WHERE task_id='T' ORDER BY created_at DESC LIMIT 1").fetchone()
            event = conn.execute("SELECT revision,payload_json FROM events WHERE entity_id='T' AND event_type='task.completed'").fetchone()
        self.assertEqual((task["status"], task["result"]), ("done", "implemented"))
        self.assertEqual(checkpoint["state"], "reached")
        self.assertEqual(claim["state"], "released")
        self.assertEqual(task["revision"], checkpoint["revision"])
        self.assertEqual(task["completion_revision"], event["revision"])
        self.assertEqual(json.loads(event["payload_json"])["reached_checkpoints"], ["C"])

    def test_later_head_movement_preserves_terminal_history_but_not_live_freshness(self) -> None:
        self._complete()
        self._move_head()
        reconciled = self.repo.service.reconcile()
        self.assertEqual(reconciled["invalidated_gates"], [])
        with self.repo.service.db.read() as conn:
            terminal_gate = conn.execute("SELECT status,valid FROM gates WHERE id='G'").fetchone()
            checkpoint = conn.execute("SELECT state FROM checkpoints WHERE id='C'").fetchone()
            completion_gate = conn.execute("SELECT status,valid,validation_git_head,completion_git_head FROM task_completion_gates WHERE gate_id='G'").fetchone()
        self.assertEqual((terminal_gate["status"], terminal_gate["valid"]), ("passed", 1))
        self.assertEqual(checkpoint["state"], "reached")
        self.assertEqual((completion_gate["status"], completion_gate["valid"]), ("passed", 1))
        self.assertTrue(completion_gate["validation_git_head"])
        self.assertTrue(completion_gate["completion_git_head"])

        active = self._task("ACTIVE", "ACTIVE-C", "ACTIVE-G")
        current_plan = base_plan([self._task("T", "C", "G"), active])
        self.repo.apply(current_plan)
        capsule = self.repo.service.continue_work(task_id="ACTIVE")
        self.repo.service.gate_run("ACTIVE-G", capsule["claim"]["claim_token"])
        self._move_head("another.txt")
        active_reconcile = self.repo.service.reconcile()
        self.assertEqual(active_reconcile["invalidated_gates"], ["ACTIVE-G"])

    def test_terminal_owner_legacy_recovery_is_tokenless_and_idempotent(self) -> None:
        self._complete()
        self._make_legacy_pending()
        self._move_head()
        with self.repo.service.db.connect() as conn:
            conn.execute("UPDATE gates SET status='invalidated',valid=0 WHERE id='G'")
            conn.commit()
        first = self.repo.service.terminal_checkpoint_finalize("T", "C")
        first_revision = first["project_revision"]
        self.assertEqual(first["status"], "finalized")
        self.assertEqual([item["checkpoint_id"] for item in first["reached"]], ["C"])
        with self.repo.service.db.read() as conn:
            task = conn.execute("SELECT status,result FROM tasks WHERE id='T'").fetchone()
            checkpoint = conn.execute("SELECT state FROM checkpoints WHERE id='C'").fetchone()
            active_claim = conn.execute("SELECT 1 FROM claims WHERE task_id='T' AND state='active'").fetchone()
            gate = conn.execute("SELECT status,valid FROM gates WHERE id='G'").fetchone()
        self.assertEqual((task["status"], task["result"]), ("done", "implemented"))
        self.assertEqual(checkpoint["state"], "reached")
        self.assertIsNone(active_claim)
        self.assertEqual((gate["status"], gate["valid"]), ("passed", 1))
        second = self.repo.service.terminal_checkpoint_finalize("T", "C")
        self.assertTrue(second["idempotent_noop"])
        self.assertEqual(second["project_revision"], first_revision)
        self.assertTrue(self.repo.service.doctor()["clean"])

    def test_completion_provenance_round_trips_through_recovery_snapshot(self) -> None:
        self._complete()
        db_path = self.repo.service.paths.db_file
        db_path.unlink()
        for suffix in ("-wal", "-shm"):
            candidate = db_path.with_name(db_path.name + suffix)
            if candidate.exists():
                candidate.unlink()
        from todo_orchestrator.service import Service

        restored = Service(self.repo.root)
        with restored.db.read() as conn:
            gate = conn.execute(
                "SELECT status,valid,completion_revision FROM task_completion_gates WHERE task_id='T' AND gate_id='G'"
            ).fetchone()
            checkpoint = conn.execute("SELECT state FROM checkpoints WHERE id='C'").fetchone()
        self.assertEqual((gate["status"], gate["valid"]), ("passed", 1))
        self.assertGreater(gate["completion_revision"], 0)
        self.assertEqual(checkpoint["state"], "reached")
        self.assertTrue(restored.terminal_checkpoint_finalize("T", "C")["idempotent_noop"])

    def test_terminal_recovery_rejections(self) -> None:
        with self.subTest("failed owner"):
            self._complete(disposition="failed")
            with self.assertRaisesRegex(TodoError, "not a successfully completed"):
                self.repo.service.terminal_checkpoint_finalize("T", "C")

        self.repo.close()
        self.setUp()
        with self.subTest("revoked checkpoint"):
            self._complete()
            self._make_legacy_pending()
            with self.repo.service.db.connect() as conn:
                conn.execute("UPDATE checkpoints SET state='revoked' WHERE id='C'")
                conn.commit()
            with self.assertRaisesRegex(TodoError, "Revoked checkpoints"):
                self.repo.service.terminal_checkpoint_finalize("T", "C")

        self.repo.close()
        self.setUp()
        with self.subTest("failed completion gate"):
            self.repo.apply(base_plan([self._task_with_owner_gate_only("T", "C", "G")]))
            capsule = self.repo.service.continue_work(task_id="T")
            self.repo.service.gate_run("G", capsule["claim"]["claim_token"])
            self.repo.service.complete(capsule["claim"]["claim_token"], "implemented")
            self._make_legacy_pending(gate_status="failed", gate_valid=0)
            with self.assertRaisesRegex(TodoError, "prerequisites were not satisfied"):
                self.repo.service.terminal_checkpoint_finalize("T", "C")

        self.repo.close()
        self.setUp()
        with self.subTest("checkpoint owned by another task"):
            self.repo.apply(base_plan([self._task("T", "C", "G"), self._task("OTHER", "OTHER-C", "OTHER-G")]))
            capsule = self.repo.service.continue_work(task_id="T")
            self.repo.service.gate_run("G", capsule["claim"]["claim_token"])
            self.repo.service.complete(capsule["claim"]["claim_token"], "implemented")
            with self.assertRaisesRegex(TodoError, "belongs to another task"):
                self.repo.service.terminal_checkpoint_finalize("T", "OTHER-C")

        self.repo.close()
        self.setUp()
        with self.subTest("unsatisfied prerequisite checkpoint gate"):
            self._complete()
            self._make_legacy_pending(gate_status="failed", gate_valid=0)
            with self.assertRaisesRegex(TodoError, "prerequisites were not satisfied"):
                self.repo.service.terminal_checkpoint_finalize("T", "C")


if __name__ == "__main__":
    unittest.main()
