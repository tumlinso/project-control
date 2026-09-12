import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class I00IntegrationTests(unittest.TestCase):
    def test_required_wf2_suites_are_under_configured_test_root(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('testpaths = ["tests"]', pyproject)
        required = {
            "a": "test_a03_routing_revalidation.py",
            "c": "test_workflow_core_authority_boundary.py",
            "v": "test_pc_wf2_v01.py",
            "i": "test_i00_integration.py",
        }
        for lane, module in required.items():
            self.assertTrue((ROOT / "tests" / "wf2_acceptance" / lane / module).is_file())
        self.assertTrue((ROOT / "scripts" / "run_wf2_acceptance.py").is_file())

    def test_authority_interface_has_declared_owner_and_contract(self) -> None:
        plan = json.loads(
            (ROOT / "planning" / "workflow-foundation-v2" / "machine" /
             "project-control-workflow-foundation-v2.todo-plan.json").read_text(encoding="utf-8")
        )
        interface = next(item for item in plan["interfaces"] if item["id"] == "PC-WF2-IF-AUTHORITY")
        self.assertEqual(interface, {
            "contract_paths": ["docs/workflow_foundation_v2/contracts/authority.md"],
            "id": "PC-WF2-IF-AUTHORITY",
            "owner_task_id": "PC-WF2-C03",
            "state": "draft",
            "version": "1",
        })
        contract = ROOT / interface["contract_paths"][0]
        self.assertTrue(contract.is_file())
        self.assertIn("Owner: `PC-WF2-C03`", contract.read_text(encoding="utf-8"))

    def test_i00_receipt_preserves_the_release_cutover_boundary(self) -> None:
        receipt = (ROOT / "docs" / "workflow_foundation_v2" / "integration" /
                   "I00_BASELINE_INTEGRATION.md").read_text(encoding="utf-8")
        normalized = " ".join(receipt.split())
        self.assertIn("does not swap a launcher", normalized)
        self.assertIn(".venv-codex-live-links-70b863a/bin/python", receipt)
        self.assertIn("production release paths remain unchanged", normalized)


if __name__ == "__main__":
    unittest.main()
