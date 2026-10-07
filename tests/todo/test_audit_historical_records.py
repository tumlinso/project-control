from __future__ import annotations

import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task


class AuditHistoricalRecordsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()

    def tearDown(self) -> None:
        self.repo.close()

    def test_terminal_unconsumed_interface_is_historical(self) -> None:
        plan = base_plan([
            safe_task("OWNER", "src/owner", status="done", result="validated"),
        ])
        plan["interfaces"] = [{
            "id": "OLD-ABI",
            "owner_task_id": "OWNER",
            "state": "frozen",
            "version": "1",
            "contract_paths": ["missing/old.hh"],
            "content_hash": "historical",
        }]
        self.repo.apply(plan)
        audit = self.repo.service.audit()
        self.assertFalse(any(item.get("interface_id") == "OLD-ABI" for item in audit["discrepancies"]))

    def test_superseded_unconsumed_interface_is_historical(self) -> None:
        plan = base_plan([
            safe_task("OWNER", "src/owner", status="superseded", result="superseded"),
        ])
        plan["interfaces"] = [{
            "id": "OLD-ABI",
            "owner_task_id": "OWNER",
            "state": "frozen",
            "version": "1",
            "contract_paths": ["missing/old.hh"],
            "content_hash": "historical",
        }]
        self.repo.apply(plan)
        audit = self.repo.service.audit()
        self.assertFalse(any(item.get("interface_id") == "OLD-ABI" for item in audit["discrepancies"]))

    def test_current_consumer_keeps_interface_drift_actionable(self) -> None:
        plan = base_plan([
            safe_task("OWNER", "src/owner", status="done", result="validated"),
            safe_task(
                "CONSUMER", "src/consumer",
                consumes_interfaces=[{"id": "OLD-ABI", "required_state": "frozen", "required_version": "1"}],
            ),
        ])
        plan["interfaces"] = [{
            "id": "OLD-ABI",
            "owner_task_id": "OWNER",
            "state": "frozen",
            "version": "1",
            "contract_paths": ["missing/old.hh"],
            "content_hash": "historical",
        }]
        self.repo.apply(plan)
        audit = self.repo.service.audit()
        self.assertTrue(any(item.get("interface_id") == "OLD-ABI" for item in audit["discrepancies"]))

    def test_gate_less_terminal_task_is_advisory_not_unclean(self) -> None:
        self.repo.apply(base_plan([
            safe_task("DONE", "src/done", status="done", result="validated"),
        ]))
        audit = self.repo.service.audit()
        self.assertTrue(any(item["code"] == "code_inspection_required" for item in audit["discrepancies"]))
        self.assertTrue(audit["clean"])


if __name__ == "__main__":
    unittest.main()
