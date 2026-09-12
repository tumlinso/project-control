"""Acceptance inventory for the WF2 process-isolated differential harness."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

HARNESS = Path(__file__).resolve().parents[2] / "wf2_validation"
sys.path.insert(0, str(HARNESS))
from differential_harness import (  # noqa: E402
    DifferentialError,
    InventoryReport,
    RuntimeCase,
    compare_case,
    require_disposable_fixture,
    run_original_suite,
)


class WF2Acceptance(unittest.TestCase):
    def _runtime(self, root: Path, name: str, *, status: str = "claimed", event: bool = True) -> RuntimeCase:
        # This is a real Todo runtime import and derives the protocol value from
        # its installed source; the altered branches are negative controls.
        code = (
            "import json, todo_orchestrator; "
            "from todo_orchestrator.workflow.foundation import PROTOCOL_VERSION; "
            f"r={{'runtime_source': todo_orchestrator.__file__, 'protocol_version': PROTOCOL_VERSION, 'status': {status!r}}}; "
            + ("r['event']='claim'; " if event else "")
            + "print(json.dumps(r, sort_keys=True))"
        )
        return RuntimeCase((sys.executable, "-B", "-c", code), root / f"fixture-{name}", name, f"pinned-{name}")

    def test_harness_catches_a_deliberately_changed_status_and_missing_event(self):
        with tempfile.TemporaryDirectory(prefix="wf2-v01-") as raw:
            root = Path(raw)
            live = root / "registered-live-authority"
            compare_case(self._runtime(root, "old-same"), self._runtime(root, "candidate-same"),
                         registered_authority_roots=(live,))
            with self.assertRaisesRegex(DifferentialError, "semantic result mismatch"):
                compare_case(self._runtime(root, "old-changed"), self._runtime(root, "changed", status="done"),
                             registered_authority_roots=(live,))
            with self.assertRaisesRegex(DifferentialError, "missing result fields"):
                compare_case(self._runtime(root, "old-missing"), self._runtime(root, "missing", event=False),
                             registered_authority_roots=(live,))

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
        original = run_original_suite(
            (sys.executable, "-B", "-m", "unittest", "tests.test_workflow_lifecycle"), cwd=project_root
        )
        report = InventoryReport(original_test_count=original.tests_run, scenario_names=("claim", "completion", "recovery"))
        payload = json.loads(json.dumps(report.as_dict()))
        self.assertGreater(payload["original_test_count"], 0)
        self.assertEqual(payload["scenario_count"], 3)
        self.assertEqual(payload["scenario_names"], ["claim", "completion", "recovery"])
