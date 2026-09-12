"""Acceptance checks for PC-WF2-C02's versioned governance contracts."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "schemas/workflow_foundation_v2"


class SchemaSnapshotEvidenceContractTests(unittest.TestCase):
    def load(self, name: str) -> dict:
        return json.loads((SCHEMAS / name).read_text())

    def test_work_profile_is_strict_and_provider_neutral(self) -> None:
        schema = self.load("work-profile-v1.schema.json")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), {"difficulty", "risk", "work_type", "context_depth"})
        self.assertNotIn("model", schema["properties"])

    def test_snapshot_has_independent_revision_and_observation_time(self) -> None:
        schema = self.load("workflow-snapshot-v1.schema.json")
        self.assertEqual(schema["properties"]["workflow_revision"]["type"], "integer")
        self.assertEqual(schema["properties"]["observed_at"]["format"], "date-time")
        self.assertIn("warnings", schema["required"])

    def test_observation_reference_carries_warnings_but_no_capability(self) -> None:
        schema = self.load("observation-reference-v1.schema.json")
        required = set(schema["required"])
        self.assertTrue({"workspace", "permission_domain", "workflow_revision", "source_identity", "fields", "expires_at", "warnings"} <= required)
        self.assertNotIn("capability", schema["properties"])

    def test_execution_attempt_is_separate_from_task_profile(self) -> None:
        schema = self.load("execution-attempt-v1.schema.json")
        self.assertIn("observed_model", schema["properties"])
        self.assertIn("usage_evidence", schema["required"])
        self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
