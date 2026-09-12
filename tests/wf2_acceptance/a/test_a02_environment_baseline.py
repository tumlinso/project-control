"""A02 keeps qualification roots external and rollback prerequisites explicit."""
from pathlib import Path
import unittest


class A02EnvironmentBaselineTest(unittest.TestCase):
    def test_baseline_declares_external_disposable_roots(self):
        text = (Path(__file__).parents[3] / "docs/workflow_foundation_v2/baseline/PC-WF2-A02.md").read_text()
        self.assertIn("fixtures/pc-wf2-a02", text)
        self.assertIn("No fixture path may be a descendant", text)
        self.assertIn("deployed launcher", text)

    def test_baseline_declares_rollback_and_reconnect(self):
        text = (Path(__file__).parents[3] / "docs/workflow_foundation_v2/baseline/PC-WF2-A02.md").read_text()
        for term in ("launcher target", "environment", "restores the disposable backup", "restarts/reconnects"):
            self.assertIn(term, text)

    def test_baseline_registers_before_milestone_use(self):
        text = (Path(__file__).parents[3] / "docs/workflow_foundation_v2/baseline/PC-WF2-A02.md").read_text()
        self.assertIn("registers the qualification environment before any WF2 milestone", " ".join(text.split()))
        self.assertIn("test_a02_environment_baseline.py", text)
        self.assertIn("unittest discover -s tests/wf2_acceptance/a -p 'test_a02*.py' -v", text)
        registry = Path(__file__).with_name("README.md").read_text()
        self.assertIn("unittest discover -s tests/wf2_acceptance/a -p 'test_a02*.py' -v", registry)


if __name__ == "__main__":
    unittest.main()
