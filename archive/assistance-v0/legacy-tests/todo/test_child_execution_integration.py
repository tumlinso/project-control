from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task  # noqa: E402


def command_gate(gate_id: str, *, required: bool = True) -> dict[str, object]:
    return {
        "id": gate_id,
        "type": "command",
        "required": required,
        "argv": [sys.executable, "-c", "raise SystemExit(0)"],
        "input_paths": ["src/owned/input.txt"],
        "timeout": 60,
    }


class ChildExecutionIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        task = safe_task("PARENT", "src/owned", gates=[command_gate("CHECK"), command_gate("OTHER", required=False)])
        self.repo.apply(base_plan([task]))
        source = self.repo.root / "src/owned/input.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("v1\n", encoding="utf-8")
        self.claim = self.repo.service.continue_work(task_id="PARENT")
        self.claim_token = self.claim["claim"]["claim_token"]

    def tearDown(self) -> None:
        self.repo.close()

    def create_child(self, scope: str, *gates: str, access: str | None = None) -> dict:
        argv = [
            "child", "create", "--claim-token", self.claim_token,
            "--objective", f"bounded work in {scope}", "--scope", scope,
            "--max-attempts", "1",
        ]
        if access:
            argv.extend(["--access", access])
        for gate in gates:
            argv.extend(["--gate", gate])
        process, envelope = self.repo.run(*argv)
        self.assertEqual(process.returncode, 0, process.stderr)
        return envelope["data"]

    def test_explicit_parent_gate_credits_only_named_ready_child(self) -> None:
        child = self.create_child("src/owned/explicit", "CHECK")
        process, candidate = self.repo.run("gate", "run", "CHECK", "--claim-token", child["child_token"])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(candidate["data"]["candidate_valid"])
        self.report(child, "succeeded", changed=True)

        process, credited = self.repo.run(
            "gate", "run", "CHECK", "--claim-token", self.claim_token,
            "--accept-child", child["child_execution_id"],
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(credited["data"]["details"]["accepted_child_execution_id"], child["child_execution_id"])
        status_process, status = self.repo.run(
            "child", "status", child["child_execution_id"], "--claim-token", self.claim_token,
        )
        self.assertEqual(status_process.returncode, 0, status_process.stderr)
        self.assertEqual(status["data"]["state"], "ready_for_acceptance")
        self.assertEqual(status["data"]["acceptance_gates"], ["CHECK"])
        process, accepted = self.repo.run(
            "child", "accept", child["child_execution_id"], "--claim-token", self.claim_token,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(accepted["data"]["state"], "accepted")

    def test_child_create_defaults_to_write_and_read_does_not_lease_scope(self) -> None:
        legacy = self.create_child("src/owned/legacy")
        self.assertEqual(legacy["access"], "write")
        read_child = self.create_child("src/owned/read", access="read")
        self.assertEqual(read_child["access"], "read")
        process, status = self.repo.run(
            "child", "status", read_child["child_execution_id"], "--claim-token", self.claim_token,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(status["data"]["scopes"][0]["state"], "authorized_read")

    def report(self, child: dict, status: str, *, changed: bool = False) -> dict:
        argv = [
            "child", "report", "--child-token", child["child_token"],
            "--status", status, "--summary", f"reported {status}",
        ]
        if changed:
            argv.extend(["--changed-path", child["scopes"][0] + "/result.py"])
        process, envelope = self.repo.run(*argv)
        self.assertEqual(process.returncode, 0, process.stderr)
        return envelope["data"]

    def capsule(self) -> dict:
        process, envelope = self.repo.run("context", "--claim-token", self.claim_token)
        self.assertEqual(process.returncode, 0, process.stderr)
        return envelope["data"]

    def test_child_gate_is_candidate_until_parent_accepts_current_source(self) -> None:
        child = self.create_child("src/owned/child", "CHECK")
        process, candidate = self.repo.run("gate", "run", "CHECK", "--claim-token", child["child_token"])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(candidate["data"]["valid"])
        self.assertTrue(candidate["data"]["candidate_valid"])
        self.assertFalse(candidate["data"]["accepted"])
        self.assertEqual(self.repo.service.gate_list("PARENT")[0]["status"], "pending")

        self.report(child, "succeeded", changed=True)
        capsule = self.capsule()
        result = capsule["child_results"][0]
        self.assertEqual(result["format"], "TODO-CHILD-RESULT/1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["acceptance"]["state"], "ready")
        self.assertEqual(result["acceptance"]["pending_gates"], ["CHECK"])
        self.assertEqual(result["evidence"][0]["artifact"]["kind"], "gate-evidence")
        self.assertEqual(result["evidence_omitted"], 0)
        command = result["acceptance"]["commands"][0]["command"]
        self.assertEqual(command["schema_version"], 1)
        self.assertEqual(command["argv"][4:8], ["gate", "run", "CHECK", "--claim-token"])
        process, blocked = self.repo.run("complete", "--claim-token", self.claim_token, "--disposition", "implemented")
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(blocked["code"], "required_gates_unsatisfied")

        released = self.repo.service.release(self.claim_token)
        self.assertEqual(released["status"], "in_progress")
        resumed = self.repo.service.continue_work(task_id="PARENT")
        self.claim_token = resumed["claim"]["claim_token"]
        self.assertEqual(resumed["child_results"][0]["acceptance"]["state"], "ready")

        process, accepted = self.repo.run("gate", "run", "CHECK", "--claim-token", self.claim_token)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(accepted["data"]["valid"])
        self.assertTrue(accepted["data"]["accepted"])
        self.assertTrue(accepted["data"]["details"]["accepted_child_evidence"])
        after = self.capsule()["child_results"][0]
        self.assertEqual(after["acceptance"]["state"], "accepted")
        self.assertEqual(after["acceptance"]["pending_gates"], [])
        process, completed = self.repo.run("complete", "--claim-token", self.claim_token, "--disposition", "implemented")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(completed["data"]["status"], "done")

    def test_stale_child_candidate_falls_back_to_normal_parent_gate_execution(self) -> None:
        child = self.create_child("src/owned/stale", "CHECK")
        process, _candidate = self.repo.run("gate", "run", "CHECK", "--claim-token", child["child_token"])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.report(child, "succeeded")
        (self.repo.root / "src/owned/input.txt").write_text("v2\n", encoding="utf-8")

        process, executed = self.repo.run("gate", "run", "CHECK", "--claim-token", self.claim_token)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(executed["data"]["valid"])
        self.assertNotIn("accepted_child_evidence", executed["data"]["details"])
        self.assertNotIn("accepted", executed["data"])
        self.assertNotIn("candidate_valid", executed["data"])
        self.assertIn("argv", executed["data"]["details"])
        self.assertEqual(self.capsule()["child_results"][0]["acceptance"]["state"], "superseded")

    def test_unauthorized_gate_is_rejected_and_compact_statuses_surface(self) -> None:
        child = self.create_child("src/owned/needs", "CHECK")
        running_capsule = self.capsule()
        self.assertNotIn("child_results", running_capsule)
        process, rejected = self.repo.run("gate", "run", "OTHER", "--claim-token", child["child_token"])
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(rejected["code"], "child_gate_unauthorized")
        self.report(child, "needs_codex")

        no_change = self.create_child("src/owned/no-change")
        self.report(no_change, "succeeded")
        failed = self.create_child("src/owned/failed")
        self.report(failed, "failed")
        statuses = {item["status"] for item in self.capsule()["child_results"]}
        self.assertEqual(statuses, {"needs_codex", "no_change", "failed"})


if __name__ == "__main__":
    unittest.main()
