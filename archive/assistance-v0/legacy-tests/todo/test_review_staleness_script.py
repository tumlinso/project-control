from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
INIT_SCRIPT = ROOT / "scripts" / "init_todos.py"
REVIEW_SCRIPT = ROOT / "scripts" / "review_staleness.py"


class ReviewStalenessScriptTests(unittest.TestCase):
    def test_apply_marks_old_stream_as_stale_and_refreshes_status_review(self) -> None:
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

            workstream_path = repo_root / "todos" / "alpha.md"
            text = workstream_path.read_text(encoding="utf-8")
            text = re.sub(r'last_heartbeat_at: ".*?"', 'last_heartbeat_at: "2000-01-01T00:00:00Z"', text)
            text = re.sub(r'last_reviewed_at: ".*?"', 'last_reviewed_at: "2000-01-01T00:00:00Z"', text)
            workstream_path.write_text(text, encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(REVIEW_SCRIPT), "--repo-root", str(repo_root), "--apply"],
                check=True,
                capture_output=True,
                text=True,
            )

            refreshed = workstream_path.read_text(encoding="utf-8")
            status_text = (repo_root / "todo-status.md").read_text(encoding="utf-8")
            root_text = (repo_root / "todos.md").read_text(encoding="utf-8")

            self.assertIn("Updated stale statuses: 1", result.stdout)
            self.assertIn('status: "stale"', refreshed)
            self.assertIn("Stale candidates", status_text)
            self.assertIn("status: stale", root_text)


if __name__ == "__main__":
    unittest.main()
