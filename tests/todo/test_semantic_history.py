from __future__ import annotations

import hashlib
import subprocess
import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task


class SemanticHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        self.repo.apply(base_plan([
            safe_task("CE-ARCH-00", "docs/architecture"),
            safe_task(
                "CE-ARCH-92", "evidence/ce-arch",
                gates=[{"id": "CE-ARCH-92-GATE", "type": "manual", "accepted": True}],
            ),
        ]))
        self.start_revision = self.repo.service.db.revision()
        for _ in range(350):
            self.repo.service.db.mutate(
                actor_session_id=None, entity_type="claim", entity_id="CE-ARCH-00",
                event_type="claim.pulsed", payload={}, operation=lambda conn, revision: None,
            )
        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="interface", entity_id="EXECUTION-IMAGE-V2",
            event_type="interface.freeze", payload={"version": "2"}, operation=lambda conn, revision: None,
        )
        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="gate", entity_id="CE-ARCH-92-GATE",
            event_type="gate.completed", payload={"status": "passed", "valid": True}, operation=lambda conn, revision: None,
        )
        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="task", entity_id="CE-ARCH-92",
            event_type="task.completed", payload={"disposition": "validated"}, operation=lambda conn, revision: None,
        )

    def tearDown(self) -> None:
        self.repo.close()

    def _run(self, action: str, *args: str) -> dict[str, object]:
        process, payload = self.repo.run("semantic", action, *args)
        self.assertEqual(process.returncode, 0, payload)
        return payload["data"]

    def test_task_anchor_resolves_without_manufacturing_a_git_head(self) -> None:
        anchor = self._run("anchor", "--task", "CE-ARCH-00", "--phase", "created")
        self.assertEqual(anchor["entity"], {"id": "CE-ARCH-00", "phase": "created", "type": "task"})
        self.assertEqual(anchor["baseline_git_heads"], [])
        self.assertIn(anchor["confidence"], {"high", "medium"})

    def test_semantic_delta_coalesces_heartbeats_after_reading_whole_interval(self) -> None:
        data = self._run("delta", "--since-revision", str(self.start_revision))
        self.assertGreaterEqual(data["raw_event_count"], 353)
        self.assertEqual(data["heartbeat_events_omitted"], 350)
        self.assertLess(data["coalesced_event_count"], 10)
        self.assertEqual(data["tasks"]["completed"], ["CE-ARCH-92"])
        self.assertEqual(data["interfaces"]["frozen"], ["EXECUTION-IMAGE-V2"])
        self.assertEqual(data["validation_by_task"], [{
            "task_id": "CE-ARCH-92", "passed": 1, "failed": 0, "invalidated": 0,
        }])
        self.assertNotIn("claim.pulsed", {item["event_type"] for item in data["material_events"]})
        self.assertEqual(data["interval"]["to_revision"], self.repo.service.db.revision())

    def test_anchor_and_delta_commands_are_read_only(self) -> None:
        snapshot = self.repo.root / ".todo-orchestrator" / "state.snapshot.json"
        before_snapshot = snapshot.read_bytes()
        before_git = subprocess.run(
            ["git", "-C", str(self.repo.root), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True, text=True, check=True,
        ).stdout
        with self.repo.service.db.read() as conn:
            before = (self.repo.service.db.revision(), hashlib.sha256(conn.serialize()).hexdigest())
        self._run("anchor", "--revision", str(self.start_revision))
        self._run("delta", "--since-task", "CE-ARCH-00")
        with self.repo.service.db.read() as conn:
            after = (self.repo.service.db.revision(), hashlib.sha256(conn.serialize()).hexdigest())
        after_git = subprocess.run(
            ["git", "-C", str(self.repo.root), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertEqual(after, before)
        self.assertEqual(snapshot.read_bytes(), before_snapshot)
        self.assertEqual(after_git, before_git)


if __name__ == "__main__":
    unittest.main()
