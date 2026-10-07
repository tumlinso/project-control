"""Admission and evidence checks for the opt-in installed LAB trial."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock


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

    def test_source_plan_routes_every_step_through_checked_in_launcher(self):
        launcher = Path(__file__).resolve().parents[2] / "scripts/pc-dev"
        args = argparse.Namespace(project_control=launcher,
            command_prefix=[str(launcher), "run"], runtime="source",
            project="registered", goal="Check the selected implementation",
            wall_seconds=600, max_experiments=2, source=["src/a.py"],
            tool=[], gpu_uuid=[], toolchain_root=None)
        plan = qualification.command_plan(args)
        self.assertTrue(all(command[:2] == [str(launcher), "run"] for command in plan))
        self.assertEqual([command[4] for command in plan], ["preview", "authorize", "run", "status"])
        self.assertFalse(any("/.local/bin/project-control" in command[0] for command in plan))

    def test_dry_run_and_execute_live_conflict_is_rejected_before_invoke(self):
        with mock.patch.object(qualification, "_invoke") as invoke:
            with self.assertRaises(SystemExit) as raised:
                qualification.main([
                    "--runtime", "source", "--dry-run", "--execute-live",
                    "--project", "registered", "--source", "src/a.py",
                    "--goal", "Inspect source", "--artifact-root", "/tmp/never-created"])
        self.assertEqual(raised.exception.code, 2)
        invoke.assert_not_called()

    def test_source_live_wiring_is_hermetic_and_records_stable_identity(self):
        launcher = Path(__file__).resolve().parents[2] / "scripts/pc-dev"
        labels = []

        def fake_invoke(argv, *, timeout, root, label, env=None):
            labels.append(label)
            self.assertEqual(argv[:2], [str(launcher), "run"])
            self.assertFalse(env.get("PROJECT_CONTROL_RELEASE_MANIFEST"))
            self.assertFalse(env.get("PROJECT_CONTROL_RELEASE_DIGEST"))
            if label == "preview":
                return {"session_id": "lab_session_" + "a" * 32,
                        "authorized": False, "inference_started": False}
            if label == "authorize":
                return {"session_id": "lab_session_" + "a" * 32,
                        "state": "authorized", "deadline": time.time() + 90}
            if label == "status":
                return {"sessions": [{"session_id": "lab_session_" + "a" * 32,
                    "state": "completed", "effects": [{"state": "completed",
                        "receipt": {"cleanup_verified": True}}]}]}
            return {"state": "completed"}

        output_stream = io.StringIO()
        with tempfile.TemporaryDirectory(prefix="pc-source-qualification-") as temporary:
            artifact_root = Path(temporary) / "qualification"
            with mock.patch.object(qualification, "_invoke", side_effect=fake_invoke), \
                    contextlib.redirect_stdout(output_stream):
                result = qualification.main([
                    "--runtime", "source", "--execute-live", "--project", "registered",
                    "--source", "src/a.py", "--goal", "Inspect source",
                    "--artifact-root", str(artifact_root)])
            self.assertEqual(result, 0)
            self.assertEqual(labels, ["preview", "authorize", "run", "status"])
            receipt = json.loads((artifact_root / "qualification-receipt.json").read_text())
            self.assertTrue(receipt["mechanical_trial_passed"])
            self.assertTrue(receipt["runtime_identity_stable"])
            self.assertEqual(receipt["runtime_identity_before"], receipt["runtime_identity_after"])

    def test_source_identity_change_fails_without_replaying_effect_and_attempts_cancel(self):
        launcher = Path(__file__).resolve().parents[2] / "scripts/pc-dev"
        baseline = {"runtime": "source", "git": {"head": "head", "dirty": True},
            "interpreter": {"executable": "/venv/python", "resolved": "/venv/python",
                            "prefix": "/venv"},
            "cli_command": [str(launcher), "run"],
            "project_control": {"path": "/checkout/src/project_control/__init__.py",
                                 "fingerprint": "a" * 64},
            "todo": {"path": "/checkout/src/todo_orchestrator/__init__.py",
                     "fingerprint": "b" * 64},
            "receiver": {"root": "/checkout/src/project_control/local_runtime",
                         "fingerprint": "c" * 64, "file_count": 1},
            "demand_runtime": {"mode": "source", "source_root": "/checkout",
                "project_control_fingerprint": "e" * 64,
                "receiver_fingerprint": "c" * 64, "todo_runtime_fingerprint": "d" * 64,
                "skills_root": "/skills"}}
        calls = [0]
        labels = []

        def changing_provenance(*args, **kwargs):
            calls[0] += 1
            observed = json.loads(json.dumps(baseline))
            if calls[0] >= 7:
                observed["project_control"]["fingerprint"] = "f" * 64
            return observed

        def fake_invoke(argv, *, timeout, root, label, env=None):
            labels.append(label)
            if label == "preview":
                return {"session_id": "lab_session_" + "a" * 32,
                        "authorized": False, "inference_started": False}
            if label == "authorize":
                return {"session_id": "lab_session_" + "a" * 32,
                        "state": "authorized", "deadline": time.time() + 90}
            return {"state": "complete" if label == "run" else "cancelled"}

        output_stream = io.StringIO()
        with tempfile.TemporaryDirectory(prefix="pc-source-drift-") as temporary:
            artifact_root = Path(temporary) / "qualification"
            with mock.patch.object(qualification, "runtime_provenance",
                                   side_effect=changing_provenance), \
                    mock.patch.object(qualification, "_invoke", side_effect=fake_invoke), \
                    contextlib.redirect_stdout(output_stream):
                result = qualification.main([
                    "--runtime", "source", "--execute-live", "--project", "registered",
                    "--source", "src/a.py", "--goal", "Inspect source",
                    "--artifact-root", str(artifact_root)])
            self.assertEqual(result, 1)
            self.assertEqual(labels, ["preview", "authorize", "run", "cancel"])
            receipt = json.loads((artifact_root / "qualification-receipt.json").read_text())
            self.assertEqual(receipt["reason"], "qualification_runtime_changed")
            self.assertFalse(receipt["automatic_effect_replay"])
            self.assertFalse(receipt["runtime_identity_stable"])

    def test_source_dry_run_reports_current_identity_and_never_invokes(self):
        launcher = Path(__file__).resolve().parents[2] / "scripts/pc-dev"
        output_stream = io.StringIO()
        with mock.patch.object(qualification, "_invoke") as invoke, \
                contextlib.redirect_stdout(output_stream):
            result = qualification.main([
                "--runtime", "source", "--dry-run", "--project", "registered",
                "--source", "src/a.py", "--goal", "Inspect source"])
        self.assertEqual(result, 0)
        invoke.assert_not_called()
        output = json.loads(output_stream.getvalue())
        self.assertEqual(output["runtime_provenance"]["runtime"], "source")
        self.assertEqual(output["runtime_provenance"]["cli_command"],
                         [str(launcher), "run"])
        self.assertFalse(output["inference_started"])
        self.assertFalse(output["gpu_work_started"])

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
