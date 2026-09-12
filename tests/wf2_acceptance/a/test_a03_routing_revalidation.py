"""A03 preserves a bounded, drift-checked routing receipt."""
from pathlib import Path
import unittest


class A03RoutingRevalidationTest(unittest.TestCase):
    def test_records_preflight_and_hashes(self):
        text = (Path(__file__).parents[3] / "docs/workflow_foundation_v2/baseline/PC-WF2-A03.md").read_text()
        self.assertIn("codex-routing-preflight.md", text)
        self.assertIn("80587faae7b985ff014046dfd8f8f8278b9203747d7ba973b490201d955ea21d", text)
        self.assertIn("20 checks passed, zero warnings, and zero failures", " ".join(text.split()))

    def test_records_actual_research_and_nonmutation_limit(self):
        text = (Path(__file__).parents[3] / "docs/workflow_foundation_v2/baseline/PC-WF2-A03.md").read_text()
        self.assertIn("Project Control `source_context`", text)
        self.assertIn("no source write, task claim, workflow mutation", " ".join(text.split()))
        self.assertIn("mechanical observer-only workflow-tool isolation is not claimed", " ".join(text.split()))


if __name__ == "__main__":
    unittest.main()
