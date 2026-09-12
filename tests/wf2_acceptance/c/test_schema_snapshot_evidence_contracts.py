"""Acceptance checks for PC-WF2-C02's versioned governance contracts."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver, ValidationError

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

    def test_strict_next_writer_task_accepts_profile_and_rejects_unknown_keys(self) -> None:
        task = self.load("workflow-task-record-v1.schema.json")
        valid = {"format": "workflow-task-record-v1", "task_id": "C02", "title": "contract", "work_profile": {"difficulty": "complex", "risk": "high", "work_type": "implementation", "context_depth": "focused"}}
        resolver = RefResolver(base_uri=SCHEMAS.as_uri() + "/", referrer=task)
        Draft202012Validator(task, resolver=resolver).validate(valid)
        with self.assertRaises(ValidationError):
            Draft202012Validator(task, resolver=resolver).validate({**valid, "unknown_task_key": True})

    def test_snapshot_references_strict_task_and_warning_records(self) -> None:
        snapshot = self.load("workflow-snapshot-v1.schema.json")
        self.assertEqual(snapshot["properties"]["tasks"]["items"]["$ref"], "workflow-task-record-v1.schema.json")
        valid = {"format": "workflow-snapshot-v1", "workflow_revision": 1, "observed_at": "2026-09-12T00:00:00Z", "tasks": [{"format": "workflow-task-record-v1", "task_id": "C02", "title": "contract"}], "warnings": [{"code": "race", "message": "source may have changed"}]}
        resolver = RefResolver(base_uri=SCHEMAS.as_uri() + "/", referrer=snapshot)
        validator = Draft202012Validator(snapshot, resolver=resolver)
        validator.validate(valid)
        invalid = {**valid, "warnings": [{"code": "race", "message": "source may have changed", "hidden": True}]}
        with self.assertRaises(ValidationError):
            validator.validate(invalid)

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
