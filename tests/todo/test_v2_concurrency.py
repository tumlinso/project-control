from __future__ import annotations

import json
import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task


class V2ConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()

    def tearDown(self) -> None:
        self.repo.close()

    def _collect(self, processes):
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            results.append((process.returncode, json.loads(stdout), stderr))
        return results

    def test_ten_processes_claim_five_unique_tasks(self) -> None:
        self.repo.apply(base_plan([safe_task(f"T{i}", f"src/t{i}") for i in range(5)]))
        results = self._collect([self.repo.popen("continue") for _ in range(10)])
        successes = [item for item in results if item[0] == 0]
        no_work = [item for item in results if item[0] == 10]
        self.assertEqual(len(successes), 5, results)
        self.assertEqual(len(no_work), 5, results)
        task_ids = [item[1]["data"]["claim"]["task_id"] for item in successes]
        claim_ids = [item[1]["data"]["claim"]["claim_id"] for item in successes]
        session_ids = [item[1]["data"]["session"]["agent_id"] for item in successes]
        self.assertEqual(len(set(task_ids)), 5)
        self.assertEqual(len(set(claim_ids)), 5)
        self.assertEqual(len(set(session_ids)), 5)
        self.assertTrue(all(item[1]["data"]["claim"]["claim_token"].startswith("toc_") for item in successes))
        self.assertTrue(all(item[1]["data"]["session"]["session_token"].startswith("tos_") for item in successes))

    def test_multiple_processes_one_task_exactly_one_wins(self) -> None:
        self.repo.apply(base_plan([safe_task("ONLY", "src/only")]))
        results = self._collect([self.repo.popen("continue") for _ in range(8)])
        self.assertEqual(sum(item[0] == 0 for item in results), 1, results)
        self.assertEqual(sum(item[0] == 10 for item in results), 7, results)

    def test_concurrent_pulses_preserve_events_and_atomic_snapshot(self) -> None:
        self.repo.apply(base_plan([safe_task("A", "src/a")]))
        claim = self.repo.service.continue_work(task_id="A")
        token = claim["claim"]["claim_token"]
        results = self._collect([self.repo.popen("pulse", "--claim-token", token) for _ in range(8)])
        self.assertTrue(all(item[0] == 0 for item in results), results)
        self.repo.service.export()
        snapshot = json.loads(self.repo.service.paths.snapshot_file.read_text(encoding="utf-8"))
        with self.repo.service.db.read() as conn:
            revisions = [row[0] for row in conn.execute("SELECT revision FROM events ORDER BY revision")]
        self.assertEqual(len(revisions), len(set(revisions)))
        self.assertEqual(snapshot["project_revision"], self.repo.service.db.revision())
        self.assertEqual(self.repo.service.db.integrity()["integrity"], "ok")
        self.assertIn(f"Project revision: `{snapshot['project_revision']}`", (self.repo.root / "todos.md").read_text(encoding="utf-8"))

    def test_overlapping_scopes_block_but_disjoint_scopes_run(self) -> None:
        self.repo.apply(base_plan([
            safe_task("A", "src/shared", priority=10),
            safe_task("B", "src/shared/child", priority=5),
            safe_task("C", "src/disjoint", priority=1),
        ]))
        first = self.repo.service.continue_work()
        self.assertEqual(first["claim"]["task_id"], "A")
        ready = {item["task_id"] for item in self.repo.service.ready()["tasks"]}
        self.assertIn("C", ready)
        self.assertNotIn("B", ready)
        second = self.repo.service.continue_work()
        self.assertEqual(second["claim"]["task_id"], "C")

    def test_serial_task_waits_for_active_parallel_work(self) -> None:
        serial = {"id": "SERIAL", "kind": "task", "title": "Serial", "parallel_policy": "serial", "priority": 1}
        self.repo.apply(base_plan([safe_task("A", "src/a", priority=10), serial]))
        claim = self.repo.service.continue_work()
        self.assertEqual(claim["claim"]["task_id"], "A")
        self.assertEqual(self.repo.service.explain("SERIAL")["execution"], "blocked_scope")

    def test_project_exclusive_task_waits_and_then_runs_alone(self) -> None:
        exclusive = {"id": "EXCLUSIVE", "kind": "integration_task", "title": "Exclusive", "parallel_policy": "project_exclusive", "priority": 1}
        self.repo.apply(base_plan([safe_task("A", "src/a", priority=10), exclusive]))
        first = self.repo.service.continue_work(task_id="A")
        self.assertEqual(self.repo.service.explain("EXCLUSIVE")["execution"], "blocked_scope")
        self.repo.service.release(first["claim"]["claim_token"])
        claimed = self.repo.service.continue_work(task_id="EXCLUSIVE")
        self.assertEqual(claimed["claim"]["task_id"], "EXCLUSIVE")


if __name__ == "__main__":
    unittest.main()
