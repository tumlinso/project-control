"""Acceptance checks for PC-WF2-C01's durable authority-boundary contract."""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "docs/workflow_foundation_v2/contracts/workflow_core_authority_boundary.v1.json"
SCHEMA = ROOT / "schemas/workflow_foundation_v2/workflow-core-authority-boundary-v1.schema.json"
LEDGER = ROOT / "planning/workflow-foundation-v2/evidence/source_ledger.json"


class WorkflowCoreAuthorityBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(CONTRACT.read_text())
        self.schema = json.loads(SCHEMA.read_text())

    def test_contract_has_the_versioned_strict_shape(self) -> None:
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(set(self.contract), set(self.schema["required"]))
        self.assertEqual(self.contract["format"], "workflow-core-authority-boundary-v1")
        self.assertEqual(self.contract["workflow_transaction_authority"]["module"], "project_control.workflow_core")

    def test_one_workflow_authority_excludes_a_second_kernel(self) -> None:
        authority = self.contract["workflow_transaction_authority"]
        self.assertTrue(authority["scope"])
        self.assertTrue(any("second Todo transaction kernel" in item for item in authority["exclusions"]))

    def test_external_authorities_remain_independent(self) -> None:
        external = {entry["name"]: entry for entry in self.contract["external_authorities"]}
        self.assertEqual(set(external), {"git", "host_resources", "compiler_artifacts"})
        self.assertIn("never SQLite-atomic", external["git"]["boundary"])

    def test_parity_move_preserves_storage_and_lifecycle(self) -> None:
        baseline = self.contract["baseline_preservation"]
        self.assertEqual(baseline["storage_layout"], "preserve_internal_todo_layout")
        self.assertEqual(baseline["lifecycle"], "preserve_baseline_workflow_lifecycle")
        self.assertTrue(baseline["prohibited_changes"])

    def test_contract_cites_the_hashed_source_ledger(self) -> None:
        digest = hashlib.sha256(LEDGER.read_bytes()).hexdigest()
        self.assertEqual(self.contract["source_ledger"]["path"], "planning/workflow-foundation-v2/evidence/source_ledger.json")
        self.assertEqual(self.contract["source_ledger"]["sha256"], digest)


if __name__ == "__main__":
    unittest.main()
