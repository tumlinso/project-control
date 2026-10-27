from __future__ import annotations

import json
import sys
import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task


def benchmark_task(task_id: str, path: str, gate_id: str, *, sleep: float = 0.35) -> dict[str, object]:
    code = (
        "import json,os,time; "
        f"time.sleep({sleep}); "
        "print(json.dumps({'score': 0.5, 'device': os.environ.get('CUDA_VISIBLE_DEVICES')}))"
    )
    return safe_task(
        task_id,
        path,
        gates=[{
            "id": gate_id,
            "type": "benchmark",
            "argv": [sys.executable, "-c", code],
            "resources": ["gpu:any"],
            "metric_path": "score",
            "operator": ">=",
            "threshold": 1.0,
            "evaluation_required": True,
            "required": True,
        }],
        result_policy={"allowed_dispositions": ["implemented", "evaluated_not_promoted", "failed"]},
    )


class V2ResourceGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()

    def tearDown(self) -> None:
        self.repo.close()

    @staticmethod
    def gpu_inventory(count: int):
        return [{"id": f"gpu:{i}", "metadata": {"physical_index": i}} for i in range(count)]

    def _communicate_json(self, process):
        stdout, stderr = process.communicate(timeout=15)
        self.assertTrue(stdout, f"child exited {process.returncode} without JSON; stderr={stderr!r}")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"child emitted invalid JSON: stdout={stdout!r}, stderr={stderr!r}, error={exc}")
        return process.returncode, payload, stderr

    def _apply(self, count: int = 2):
        self.repo.apply(base_plan(
            [benchmark_task("A", "src/a", "GA"), benchmark_task("B", "src/b", "GB")],
            resource_classes=[{"id": "gpu", "instances": self.gpu_inventory(count)}],
        ))

    def test_two_concurrent_gpu_any_gates_receive_distinct_devices(self) -> None:
        self._apply(2)
        a = self.repo.service.continue_work(task_id="A")
        b = self.repo.service.continue_work(task_id="B")
        processes = [
            self.repo.popen("gate", "run", "GA", "--claim-token", a["claim"]["claim_token"]),
            self.repo.popen("gate", "run", "GB", "--claim-token", b["claim"]["claim_token"]),
        ]
        results = [self._communicate_json(process) for process in processes]
        self.assertTrue(all(item[0] == 0 for item in results), results)
        devices = {item[1]["data"]["details"]["environment"]["CUDA_VISIBLE_DEVICES"] for item in results}
        self.assertEqual(devices, {"0", "1"})
        self.assertTrue(all(item[1]["data"]["status"] == "evaluated_not_promoted" for item in results))
        self.assertTrue(all(item["active"] == 0 for item in self.repo.service.resource_list()))

    def test_one_gpu_never_double_allocates(self) -> None:
        self._apply(1)
        a = self.repo.service.continue_work(task_id="A")
        b = self.repo.service.continue_work(task_id="B")
        processes = [
            self.repo.popen("gate", "run", "GA", "--claim-token", a["claim"]["claim_token"]),
            self.repo.popen("gate", "run", "GB", "--claim-token", b["claim"]["claim_token"]),
        ]
        results = [self._communicate_json(process) for process in processes]
        self.assertEqual(sum(item[0] == 0 for item in results), 1, results)
        self.assertEqual(sum(item[0] == 11 for item in results), 1, results)
        self.assertTrue(all(item["active"] == 0 for item in self.repo.service.resource_list()))

    def test_gate_failure_releases_resources_and_records_evidence(self) -> None:
        task = safe_task("FAIL", "src/fail", gates=[{"id": "FAIL-GATE", "type": "command", "argv": [sys.executable, "-c", "raise SystemExit(7)"], "resources": ["gpu:any"], "required": True}])
        self.repo.apply(base_plan([task], resource_classes=[{"id": "gpu", "instances": self.gpu_inventory(1)}]))
        claim = self.repo.service.continue_work(task_id="FAIL")
        report = self.repo.service.gate_run("FAIL-GATE", claim["claim"]["claim_token"])
        self.assertFalse(report["valid"])
        self.assertEqual(report["details"]["returncode"], 7)
        self.assertEqual(self.repo.service.resource_list()[0]["active"], 0)
        evidence = self.repo.service.gate_explain("FAIL-GATE")["evidence"]
        self.assertEqual(evidence[0]["status"], "failed")

    def test_cli_gate_failure_uses_stable_exit_code(self) -> None:
        task = safe_task("FAIL", "src/fail", gates=[{"id": "FAIL-GATE", "type": "command", "argv": [sys.executable, "-c", "raise SystemExit(7)"], "required": True}])
        self.repo.apply(base_plan([task]))
        claim = self.repo.service.continue_work(task_id="FAIL")
        process, payload = self.repo.run("gate", "run", "FAIL-GATE", "--claim-token", claim["claim"]["claim_token"])
        self.assertEqual(process.returncode, 14)
        self.assertEqual(payload["code"], "gate_failed")
        self.assertIn("evidence_id", payload["error"]["details"])

    def test_gate_timeout_releases_resources(self) -> None:
        task = safe_task("TIMEOUT", "src/timeout", gates=[{"id": "TIMEOUT-GATE", "type": "command", "argv": [sys.executable, "-c", "import time; time.sleep(2)"], "timeout": 0.1, "resources": ["gpu:any"], "required": True}])
        self.repo.apply(base_plan([task], resource_classes=[{"id": "gpu", "instances": self.gpu_inventory(1)}]))
        claim = self.repo.service.continue_work(task_id="TIMEOUT")
        report = self.repo.service.gate_run("TIMEOUT-GATE", claim["claim"]["claim_token"])
        self.assertFalse(report["valid"])
        self.assertEqual(report["details"], {"timeout": 0.1})
        self.assertEqual(self.repo.service.resource_list()[0]["active"], 0)

    def test_completion_requires_valid_required_gate(self) -> None:
        self._apply(1)
        claim = self.repo.service.continue_work(task_id="A")
        with self.assertRaisesRegex(Exception, "Required gates"):
            self.repo.service.complete(claim["claim"]["claim_token"], "evaluated_not_promoted")
        self.repo.service.gate_run("GA", claim["claim"]["claim_token"])
        completed = self.repo.service.complete(claim["claim"]["claim_token"], "evaluated_not_promoted")
        self.assertEqual(completed["status"], "done")
        self.assertEqual(completed["disposition"], "evaluated_not_promoted")


if __name__ == "__main__":
    unittest.main()
