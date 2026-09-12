"""Acceptance inventory for the WF2 process-isolated differential harness."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

HARNESS = Path(__file__).resolve().parents[2] / "wf2_validation"
sys.path.insert(0, str(HARNESS))
from differential_harness import (  # noqa: E402
    DifferentialError,
    InventoryReport,
    compare_case,
    compare_results,
    require_disposable_fixture,
    run_original_suite,
    runtime_cases_from_bindings,
)


class WF2Acceptance(unittest.TestCase):
    def _bindings(self) -> Path:
        raw = os.environ.get("WF2_BINDINGS")
        if not raw:
            self.fail("WF2_BINDINGS is required for pinned runtime validation")
        return Path(raw)

    def test_harness_catches_a_deliberately_changed_status_and_missing_event(self):
        with tempfile.TemporaryDirectory(prefix="wf2-v01-") as raw:
            root = Path(raw)
            live = root / "registered-live-authority"
            old, candidate = runtime_cases_from_bindings(self._bindings(), root)
            result = compare_case(old, candidate, registered_authority_roots=(live,))
            self.assertEqual(result.old["status"], "claimed")
            changed = {**result.candidate, "status": "done"}
            missing = {key: value for key, value in result.candidate.items() if key != "event"}
            with self.assertRaisesRegex(DifferentialError, "semantic result mismatch"):
                compare_results(result.old, changed)
            with self.assertRaisesRegex(DifferentialError, "missing result fields"):
                compare_results(result.old, missing)

    def test_fixtures_never_point_to_a_registered_live_db(self):
        with tempfile.TemporaryDirectory(prefix="wf2-v01-") as raw:
            root = Path(raw)
            live = root / "registered-live-authority"
            live.mkdir()
            disposable = root / "disposable"
            self.assertEqual(require_disposable_fixture(disposable, (live,)), disposable.resolve())
            with self.assertRaisesRegex(DifferentialError, "overlaps registered authority"):
                require_disposable_fixture(live / "candidate", (live,))
            with self.assertRaisesRegex(DifferentialError, "overlaps registered authority"):
                require_disposable_fixture(root, (live,))

    def test_full_original_tests_and_new_scenario_inventory_are_reported_separately(self):
        project_root = Path(__file__).resolve().parents[3]
        bindings = json.loads(self._bindings().read_text(encoding="utf-8"))
        skills = Path(bindings["repositories"]["skills"])
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(skills / "todo-orchestrator")}
        original = run_original_suite(
            (bindings["test_python"], "-B", "-m", "unittest", "tests.test_workflow_lifecycle"),
            cwd=project_root, environment=environment
        )
        report = InventoryReport(original_test_count=original.tests_run, scenario_names=("claim", "completion", "recovery"))
        payload = json.loads(json.dumps(report.as_dict()))
        self.assertGreater(payload["original_test_count"], 0)
        self.assertEqual(payload["scenario_count"], 3)
        self.assertEqual(payload["scenario_names"], ["claim", "completion", "recovery"])
