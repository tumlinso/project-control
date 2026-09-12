#!/usr/bin/env python3
"""Discover every WF2 acceptance lane without requiring pytest packages."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path


def build_suite(root: Path) -> tuple[unittest.TestSuite, list[str]]:
    acceptance = root / "tests" / "wf2_acceptance"
    suite = unittest.TestSuite()
    lanes: list[str] = []
    for lane in sorted(path for path in acceptance.iterdir() if path.is_dir()):
        if not any(lane.glob("test_*.py")):
            continue
        suite.addTests(unittest.defaultTestLoader.discover(str(lane), pattern="test_*.py"))
        lanes.append(lane.name)
    return suite, lanes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    suite, lanes = build_suite(args.repo_root.resolve())
    if not lanes or suite.countTestCases() == 0:
        print("WF2 acceptance discovery found no tests", file=sys.stderr)
        return 5
    result = unittest.TextTestRunner(verbosity=0 if args.quiet else 2).run(suite)
    print(f"WF2 acceptance lanes={','.join(lanes)} tests={suite.countTestCases()}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
