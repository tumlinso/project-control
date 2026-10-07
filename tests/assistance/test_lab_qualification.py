"""Admission and evidence checks for the opt-in installed LAB trial."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import unittest


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/qualify_assistance_scoped_lab.py"
_SPEC = importlib.util.spec_from_file_location("scoped_lab_qualification", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
qualification = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(qualification)


class ScopedLabQualificationTests(unittest.TestCase):
    def test_plan_is_installed_cli_only_and_explicit_devices_are_retained(self):
        args = argparse.Namespace(project_control=Path("/installed/project-control"),
            project="registered", goal="Check the selected implementation",
            wall_seconds=600, max_experiments=2, source=["src/a.py"],
            tool=["python3"], gpu_uuid=["GPU-a", "GPU-b"],
            toolchain_root=Path("/approved/toolkit"))
        plan = qualification.command_plan(args)
        self.assertTrue(all(command[0] == "/installed/project-control" for command in plan))
        self.assertEqual(plan[0].count("--gpu-uuid"), 2)
        self.assertIn("/approved/toolkit", plan[0])
        self.assertEqual([command[3] for command in plan], ["preview", "authorize", "run", "status"])

    def test_model_claim_is_not_mechanical_evidence(self):
        result = qualification.assess_status({"sessions": [{"session_id": "selected",
            "state": "completed", "effects": [], "summary": "Everything passed"}]}, "selected")
        self.assertFalse(result["mechanical_trial_passed"])
        self.assertFalse(result["answer_quality_accepted"])

    def test_every_effect_must_have_terminal_cleanup_receipt(self):
        status = {"sessions": [{"session_id": "selected", "state": "budget_exhausted",
            "effects": [{"state": "completed", "receipt": {"cleanup_verified": True}},
                        {"state": "running", "receipt": {"cleanup_verified": True}}]}]}
        self.assertFalse(qualification.assess_status(status, "selected")["mechanical_trial_passed"])
        status["sessions"][0]["effects"][1]["state"] = "completed"
        result = qualification.assess_status(status, "selected")
        self.assertTrue(result["mechanical_trial_passed"])
        self.assertFalse(result["answer_quality_accepted"])

    def test_ambiguous_status_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            qualification.assess_status({"sessions": [{"session_id": "a"}, {"session_id": "a"}]}, "a")


if __name__ == "__main__":
    unittest.main()
