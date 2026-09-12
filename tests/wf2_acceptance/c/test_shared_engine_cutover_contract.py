"""PC-WF2-C03 checks for explicit, directional shared-engine contracts."""
from __future__ import annotations
import json
import unittest
from pathlib import Path
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "schemas/workflow_foundation_v2"
CONTRACTS = ROOT / "docs/workflow_foundation_v2/contracts"

class SharedEngineCutoverTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((SCHEMAS / "shared-engine-cutover-v1.schema.json").read_text())
        self.contract = json.loads((CONTRACTS / "shared_engine_cutover.v1.json").read_text())
    def test_contract_is_owned_and_valid(self):
        Draft202012Validator(self.schema).validate(self.contract)
        self.assertEqual(self.contract["owner_task"], "PC-WF2-C03")
    def test_ctxpp_and_facades_are_read_only(self):
        self.assertTrue(all(self.contract["ctxpp_query"].values()))
        self.assertTrue(all(not facade["write_authority"] for facade in self.contract["facades"]))
    def test_closure_order_is_directional_not_self_referential(self):
        order = self.contract["closure_order"]
        self.assertEqual(len(order), len(set(order)))
        self.assertLess(order.index("PC-WF2-I50"), order.index("SK-WF2-X03"))
        self.assertLess(order.index("SK-WF2-I90"), order.index("PC-WF2-X04"))
        self.assertEqual(order[-1], "PC-WF2-I90")

if __name__ == "__main__": unittest.main()
