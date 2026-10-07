"""Finite guard tests for the staged PA1 live qualification harness."""
from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from scripts import qualify_assistance_live as live


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "planning/project-assistance-v1"
SOURCE = ROOT / "src/project_control"
FIXTURE = PACKAGE / "fixtures/repository"


class LiveQualificationPlanTests(unittest.TestCase):
    def test_inert_plan_is_explicitly_planned_and_does_not_claim_inference(self):
        plan = live.source_plan(PACKAGE, SOURCE, FIXTURE)
        self.assertEqual(plan["status"], "planned")
        self.assertIs(plan["inference_performed"], False)
        self.assertEqual(plan["case_ids"], ["E01", "E02", "E03", "E04"])
        self.assertEqual(plan["initial_budget"]["max_new_inquiries"], 12)
        self.assertEqual(plan["initial_budget"]["wall_seconds"], 900)
        self.assertEqual(plan["initial_budget"]["max_parallel_executions"], 2)
        self.assertEqual(plan["held_out_budget"]["max_new_inquiries"], 4)
        self.assertEqual(plan["held_out_budget"]["wall_seconds"], 300)
        self.assertIn("A31", plan["counterexample_case_id"])
        self.assertIn("A37", plan["continuation_case_id"])
        self.assertNotIn("qualified", plan)

    def test_initial_budget_rejects_over_cap_and_case_underfunding(self):
        with self.assertRaises(live.QualificationError):
            live._effective_budget("baseline", 4, 13, 900)
        with self.assertRaises(live.QualificationError):
            live._effective_budget("baseline", 4, 3, 900)

    def test_held_out_budget_has_separate_cap(self):
        budget = live._effective_budget("heldout", 4, 4, 300)
        self.assertEqual(budget["max_new_inquiries"], 4)
        self.assertEqual(budget["wall_seconds"], 300)
        with self.assertRaises(live.QualificationError):
            live._effective_budget("heldout", 4, 4, 301)

    def test_no_owned_receipts_means_two_slots_are_not_proven(self):
        identity = live._slot_identity({"slots": []}, [])
        self.assertIs(identity["two_distinct_slots"], False)
        self.assertIs(identity["same_model_sha256"], False)
        self.assertIs(identity["same_binary_sha256"], False)

    def test_model_controlled_reasoning_fields_are_not_execution_evidence(self):
        parsed = live._model_response({"text": '{"answer":"supported", "citations":[], '
                                      '"reasoning_tokens":987654, "trace":["claimed"]}'})
        self.assertEqual(parsed["answer"], "supported")
        self.assertNotIn("reasoning_tokens", parsed)
        self.assertNotIn("trace", parsed)

    def test_private_artifact_root_is_user_owned_and_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = live._private_artifact_root(Path(temporary) / "artifacts")
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_response_failure_is_not_a_qualified_release(self):
        report = {"status": "partial", "inference_performed": False,
                  "qualification": "model_evidence_only",
                  "gaps": ["public_as1_job_journey_not_run"]}
        self.assertNotEqual(report["status"], "qualified")
        self.assertNotEqual(report["qualification"], "production_ready")

    def test_unavailable_or_different_slot_receipts_do_not_claim_interchangeability(self):
        identity = live._slot_identity({"slots": []}, [
            {"slot_id": "missing-one"}, {"slot_id": "missing-two"}])
        self.assertIs(identity["two_distinct_slots"], False)
        self.assertEqual(identity["slots"], [])
        self.assertIs(identity["same_model_sha256"], False)

    def test_fixture_source_locator_uses_registered_repository_identity_and_path(self):
        from project_control.config import load_config
        from project_control.registry import WorkspaceRegistry

        registry = WorkspaceRegistry(load_config())
        evidence = live._source_packet({"evidence_paths": ["demo/budgets.py"]}, FIXTURE)
        locators = live._fixture_source_locators(registry, evidence, FIXTURE)
        self.assertEqual(len(locators), 1)
        self.assertEqual(locators[0]["project"], "project-control")
        self.assertEqual(locators[0]["repository"], "project-control")
        self.assertEqual(locators[0]["path"],
                         "planning/project-assistance-v1/fixtures/repository/demo/budgets.py")
        self.assertEqual(locators[0]["content_sha256"], evidence[0]["sha256"])
        self.assertNotIn("revision", locators[0])

    def test_preflight_failure_is_persistable_zero_inquiry_evidence(self):
        report = live._preflight_failure_report(
            {"cases": [], "inference_performed": False}, RuntimeError("canonical service unavailable"))
        self.assertEqual(report["status"], "preflight_failed")
        self.assertEqual(report["inquiry_count"], 0)
        self.assertFalse(report["inference_performed"])
        self.assertFalse(report["cleanup"]["isolated_job_service_started"])
        self.assertEqual(report["failure"]["reason"], "canonical service unavailable")


if __name__ == "__main__":
    unittest.main()
