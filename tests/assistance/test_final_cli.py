from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from project_control.cli import (_assistance_ask, _assistance_lab_command, _parser,
                                 _scoped_lab_in_transient_unit, main)


class _Jobs:
    def __init__(self):
        self.started = False
        self.stopped = False

    def inquire(self, **_kwargs):
        return {"status": "unavailable", "reason": "test"}

    def shutdown(self, timeout=1):
        self.stopped = True
        return True


class _Composition:
    def __init__(self):
        self.jobs = _Jobs()

    def start(self):
        self.jobs.started = True

    def scope(self, project):
        return {"project": project}


class FinalAssistanceCliTests(unittest.TestCase):
    def test_parser_exposes_explicit_lifecycle_and_scoped_lab(self):
        self.assertEqual("start", _parser().parse_args(["assistance", "start"]).assistance_command)
        self.assertEqual("stop", _parser().parse_args(["assistance", "stop"]).assistance_command)
        run = _parser().parse_args(["assistance", "lab", "run", "--scope-id", "scope-1"])
        self.assertEqual("scope-1", run.scope_id)
        self.assertEqual(600, _parser().parse_args([
            "assistance", "lab", "preview", "--project", "demo", "--source", "src",
            "--goal", "check",]).wall_seconds)

    def test_one_shot_lab_still_requires_its_original_selection_fields(self):
        args = _parser().parse_args(["assistance", "lab", "run"])
        with self.assertRaisesRegex(ValueError, "lab_run_missing_required_arguments:project,source"):
            _assistance_lab_command(args)

    def test_fresh_cached_ask_does_not_start_inference(self):
        composition = _Composition()
        composition.jobs.inquire = lambda **_kwargs: {
            "status": "completed", "job": {"result_packet": "cached-ref", "evidence_packets": []}}
        composition.jobs.reconcile = lambda: None
        composition.store = SimpleNamespace(lookup=lambda reference, access_scope: SimpleNamespace(
            status="ok", packet=SimpleNamespace(payload={"answer": "cached answer"},
                packet_id="packet-1", alias="summary", sources=[])))
        with patch("project_control.assistance.demand_runtime.ensure_demand_runtime_ready") as ready, \
             patch("project_control.as1_surface.public_inquiry", side_effect=lambda value: value):
            result = _assistance_ask(composition, "question", "demo")
        ready.assert_not_called()
        self.assertTrue(composition.jobs.started)
        self.assertEqual("completed", result["status"])
        self.assertEqual("cached answer", result["answer"])

    def test_status_is_read_only_and_combines_runtime_and_operator(self):
        with patch("project_control.assistance.operator.AssistanceOperator.status",
                   return_value={"power": {"physical_state": "pending"}}), \
             patch("project_control.assistance.demand_runtime.demand_runtime_status",
                   return_value={"readiness": "inactive"}) as runtime_status, \
             patch("project_control.cli._assistance_demand_work_status",
                   return_value={"active_work": []}) as work_status, \
             patch("project_control.assistance.demand_runtime.ensure_demand_runtime_ready") as ensure, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(0, main(["assistance", "status"]))
        runtime_status.assert_called_once_with()
        work_status.assert_called_once_with()
        ensure.assert_not_called()
        value = json.loads(output.getvalue())
        self.assertEqual("pending", value["operator"]["power"]["physical_state"])
        self.assertEqual("inactive", value["runtime"]["readiness"])
        self.assertEqual([], value["work"]["active_work"])

    def test_start_waits_for_verified_runtime_receipt(self):
        with patch("project_control.assistance.demand_runtime.start_demand_runtime",
                   return_value={"status": "ready", "release_digest": "a" * 64}) as start, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(0, main(["assistance", "start"]))
        self.assertAlmostEqual(120.0, start.call_args.kwargs["deadline_epoch"] - __import__("time").time(), delta=2)
        self.assertEqual("ready", json.loads(output.getvalue())["status"])

    def test_stop_requires_owner_cancellation_and_release_proof(self):
        composition = _Composition()
        composition.jobs.coordinate_demand_stop = lambda control, timeout: {
            "active_work_cancelled": True, "owned_resources_released": True}
        with patch("project_control.cli.load_config", return_value=type("Config", (), {
                 "workspaces": {"demo": object()}})()), \
             patch("project_control.cli._assistance_composition", return_value=(None, composition)), \
             patch("project_control.assistance.demand_runtime.stop_demand_runtime",
                   side_effect=lambda coordinate_stop: {
                       "status": "stopped", **coordinate_stop()}) as stop, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(0, main(["assistance", "stop"]))
        stop.assert_called_once()
        self.assertTrue(composition.jobs.stopped)
        self.assertTrue(json.loads(output.getvalue())["active_work_cancelled"])

    def test_scoped_lab_reuses_an_existing_delegated_cgroup(self):
        with patch("project_control.assistance.lab_runner.current_cgroup_ready", return_value=True), \
             patch("project_control.cli._lab_transient_unit_active") as active:
            self.assertEqual(-1, _scoped_lab_in_transient_unit("run", "scope-1"))
        active.assert_not_called()

    def test_scoped_lab_unit_explicitly_delegates_all_containment_controllers(self):
        source_root = Path("/tmp/project-control-test-source")
        completed = subprocess.CompletedProcess([], 0, stdout='{"status":"done"}', stderr="")
        with patch("project_control.assistance.lab_runner.current_cgroup_ready", return_value=False), \
             patch("project_control.cli._lab_transient_unit_active", return_value=False), \
             patch("project_control.cli.shutil.which", return_value="/usr/bin/systemd-run"), \
             patch("project_control.cli._lab_runtime_identity",
                   return_value=(Path("/usr/bin/python3"), source_root)), \
             patch("project_control.cli.subprocess.run", return_value=completed) as run:
            self.assertEqual(0, _scoped_lab_in_transient_unit("run", "scope-1"))
        command = run.call_args.args[0]
        self.assertIn("--property=Delegate=cpu memory pids", command)
        self.assertIn("--property=CPUAccounting=yes", command)
        self.assertIn("--property=MemoryAccounting=yes", command)
        self.assertIn("--property=TasksAccounting=yes", command)
        self.assertIn("--property=CPUWeight=100", command)
        self.assertIn("--property=DelegateSubgroup=controller", command)
        self.assertIn("--property=RuntimeMaxSec=600s", command)


if __name__ == "__main__":
    unittest.main()
