from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pcu_candidate_runtime", ROOT / "packaging" / "candidate_runtime.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
CODEX_RICH_TOOLS = MODULE.CODEX_RICH_TOOLS
OBSERVER_TOOLS = MODULE.OBSERVER_TOOLS
WORKFLOW_TOOLS = MODULE.WORKFLOW_TOOLS


class CandidateRuntimeTests(unittest.TestCase):
    def test_exact_profile_counts_and_terminal_is_observer_only(self) -> None:
        self.assertEqual(len(OBSERVER_TOOLS), 15)
        self.assertEqual(len(WORKFLOW_TOOLS), 6)
        self.assertEqual(len(CODEX_RICH_TOOLS), 14)
        self.assertNotIn("terminal_capture", CODEX_RICH_TOOLS)
        self.assertEqual(len(WORKFLOW_TOOLS | CODEX_RICH_TOOLS), 20)


if __name__ == "__main__":
    unittest.main()
