from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "scripts" / "cleanup_todos.py"
INIT_SCRIPT = ROOT / "scripts" / "init_todos.py"
UPDATE_SCRIPT = ROOT / "scripts" / "update_todos.py"


class CleanupScriptTests(unittest.TestCase):
    def test_cleanup_refuses_when_any_workstream_is_unfinished(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            subprocess.run(
                [
                    sys.executable,
                    str(INIT_SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--workstream",
                    "alpha",
                    "--objective",
                    "Alpha work",
                    "--status",
                    "in_progress",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(repo_root), "--dry-run"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Cleanup blocked", result.stdout)

    def test_cleanup_apply_deletes_completed_workstreams_and_compacts_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            subprocess.run(
                [
                    sys.executable,
                    str(INIT_SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--workstream",
                    "alpha",
                    "--objective",
                    "Alpha work",
                    "--status",
                    "done",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(UPDATE_SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--workstream",
                    "alpha",
                    "--status",
                    "done",
                    "--execution-state",
                    "closed",
                    "--progress-note",
                    "Alpha is complete",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(repo_root), "--apply"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("Cleanup complete.", result.stdout)
            self.assertFalse((repo_root / "todos" / "alpha.md").exists())
            todos_text = (repo_root / "todos.md").read_text(encoding="utf-8")
            self.assertIn("_No active workstreams yet._", todos_text)
            self.assertIn("Ran `todo-cleanup`", todos_text)
            status_text = (repo_root / "todo-status.md").read_text(encoding="utf-8")
            self.assertIn("_No tracked workstreams yet._", status_text)
            self.assertIn("Safe to call `todo-cleanup`: yes", status_text)

    def test_cleanup_reports_stale_workstreams_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            subprocess.run(
                [
                    sys.executable,
                    str(INIT_SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--workstream",
                    "alpha",
                    "--objective",
                    "Alpha work",
                    "--status",
                    "stale",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(INIT_SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--workstream",
                    "beta",
                    "--objective",
                    "Beta work",
                    "--status",
                    "superseded",
                    "--superseded-by",
                    "gamma",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(repo_root), "--dry-run"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Stale workstreams pending review", result.stdout)
            self.assertIn("Cleanup-eligible workstreams", result.stdout)
            self.assertIn("beta", result.stdout)

    def test_partial_cleanup_dry_run_reports_targets_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            ensure_args = [
                (
                    "alpha",
                    "Alpha work",
                    "in_progress",
                    None,
                ),
                (
                    "beta",
                    "Beta work",
                    "done",
                    "Beta is complete",
                ),
                (
                    "gamma",
                    "Gamma work",
                    "stale",
                    "Gamma is stale",
                ),
            ]
            for slug, objective, status, next_action in ensure_args:
                command = [
                    sys.executable,
                    str(INIT_SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--workstream",
                    slug,
                    "--objective",
                    objective,
                    "--status",
                    status,
                ]
                subprocess.run(command, check=True, capture_output=True, text=True)
                if next_action:
                    subprocess.run(
                        [
                            sys.executable,
                            str(UPDATE_SCRIPT),
                            "--repo-root",
                            str(repo_root),
                            "--workstream",
                            slug,
                            "--status",
                            status,
                            "--pickup-note",
                            next_action,
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(repo_root), "--partial", "--dry-run"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("Partial cleanup mode.", result.stdout)
            self.assertIn("Cleanup targets:", result.stdout)
            self.assertIn("beta", result.stdout)
            self.assertIn("Active workstreams kept:", result.stdout)
            self.assertIn("alpha", result.stdout)
            self.assertIn("Stale workstreams kept:", result.stdout)
            self.assertIn("gamma", result.stdout)

    def test_partial_cleanup_apply_removes_done_and_keeps_survivors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            subprocess.run(
                [
                    sys.executable,
                    str(INIT_SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--workstream",
                    "alpha",
                    "--objective",
                    "Alpha work",
                    "--status",
                    "done",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(INIT_SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--workstream",
                    "beta",
                    "--objective",
                    "Beta work",
                    "--status",
                    "in_progress",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--partial",
                    "--apply",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertFalse((repo_root / "todos" / "alpha.md").exists())
            self.assertTrue((repo_root / "todos" / "beta.md").exists())
            todos_text = (repo_root / "todos.md").read_text(encoding="utf-8")
            self.assertIn("`beta` | status: in_progress", todos_text)
            self.assertNotIn("`alpha` | status: done", todos_text)
            self.assertIn("Ran `todo-cleanup --partial` and cleared workstreams: alpha.", todos_text)
            status_text = (repo_root / "todo-status.md").read_text(encoding="utf-8")
            self.assertIn("`beta` | status: in_progress", status_text)
            self.assertNotIn("`alpha` | status: done", status_text)
            self.assertIn("Safe to call `todo-cleanup`: no, active workstreams: beta.", status_text)
            self.assertNotIn("Partial cleanup is available via `todo-cleanup --partial`", status_text)

    def test_partial_cleanup_can_remove_stale_when_explicitly_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            subprocess.run(
                [
                    sys.executable,
                    str(INIT_SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--workstream",
                    "alpha",
                    "--objective",
                    "Alpha work",
                    "--status",
                    "stale",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--partial",
                    "--scope",
                    "stale",
                    "--apply",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertFalse((repo_root / "todos" / "alpha.md").exists())
            todos_text = (repo_root / "todos.md").read_text(encoding="utf-8")
            self.assertIn("_No active workstreams yet._", todos_text)
            status_text = (repo_root / "todo-status.md").read_text(encoding="utf-8")
            self.assertIn("_No tracked workstreams yet._", status_text)

    def test_partial_cleanup_rejects_invalid_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--partial",
                    "--scope",
                    "in_progress",
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsupported cleanup scope token", result.stderr)


if __name__ == "__main__":
    unittest.main()
