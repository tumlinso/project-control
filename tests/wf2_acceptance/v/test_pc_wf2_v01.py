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
)


class WF2Acceptance(unittest.TestCase):
    def _runtime(self, root: Path, name: str, result: dict[str, object]) -> RuntimeCase:
        program = root / f"{name}.py"
        program.write_text("import json\nprint(json.dumps(" + repr(result) + "))\n", encoding="utf-8")
        return RuntimeCase((sys.executable, str(program)), root / f"fixture-{name}", name)

    def test_harness_catches_a_deliberately_changed_status_and_missing_event(self):
        with tempfile.TemporaryDirectory(prefix="wf2-v01-") as raw:
            root = Path(raw)
            old = self._runtime(root, "old", {"status": "claimed", "event": "claim", "clock": 1})
            changed = self._runtime(root, "changed", {"status": "done", "event": "claim", "clock": 2})
            missing = self._runtime(root, "missing", {"status": "claimed", "clock": 2})
            live = root / "registered-live-authority"
            compare_case(old, self._runtime(root, "same", {"status": "claimed", "event": "claim", "clock": 2}),
                         registered_authority_roots=(live,), nondeterministic_fields=("clock",))
            with self.assertRaisesRegex(DifferentialError, "semantic result mismatch"):
                compare_case(old, changed, registered_authority_roots=(live,), nondeterministic_fields=("clock",))
            with self.assertRaisesRegex(DifferentialError, "missing result fields"):
                compare_case(old, missing, registered_authority_roots=(live,), nondeterministic_fields=("clock",))

    def test_fixtures_never_point_to_a_registered_live_db(self):
        with tempfile.TemporaryDirectory(prefix="wf2-v01-") as raw:
            root = Path(raw)
            live = root / "registered-live-authority"
            live.mkdir()
            disposable = root / "disposable"
            self.assertEqual(require_disposable_fixture(disposable, (live,)), disposable.resolve())
            with self.assertRaisesRegex(DifferentialError, "overlaps registered authority"):
                require_disposable_fixture(live / "candidate", (live,))

    def test_full_original_tests_and_new_scenario_inventory_are_reported_separately(self):
        report = InventoryReport(original_test_count=42, scenario_names=("claim", "completion", "recovery"))
        payload = json.loads(json.dumps(report.as_dict()))
        self.assertEqual(payload["original_test_count"], 42)
        self.assertEqual(payload["scenario_count"], 3)
        self.assertEqual(payload["scenario_names"], ["claim", "completion", "recovery"])
