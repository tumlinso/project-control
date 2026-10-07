from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "scripts" / "update_todos.py"


class UpdateScriptTests(unittest.TestCase):
    def test_update_accepts_stdin_payload_for_shell_sensitive_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            payload = {
                "workstream": "debug-stream",
                "objective": "Debug stream",
                "status": "in_progress",
                "execution_state": "idle",
                "owner": "codex",
                "pickup_note": "Repro hits `seriesWorkbenchRuntimeTest` before loading `*.csh5` part files.",
                "shared_assumption": [
                    "The crash repro still references `seriesWorkbenchRuntimeTest` and `*.csh5` part files."
                ],
                "assumption": [
                    "Keep the current repro text intact instead of retyping shell-sensitive fragments."
                ],
                "progress_note": [
                    "Captured the failing repro with `backticks`, `*.globs`, and `seriesWorkbenchRuntimeTest` intact."
                ],
                "next_action": [
                    "Keep the rename stream serial and leave the debug stream pickup-ready with the current repro."
                ],
            }
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--payload-file",
                    "-",
                ],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("debug-stream.md", result.stdout)

            workstream_text = (repo_root / "todos" / "debug-stream.md").read_text(encoding="utf-8")
            root_text = (repo_root / "todos.md").read_text(encoding="utf-8")
            status_text = (repo_root / "todo-status.md").read_text(encoding="utf-8")

            self.assertIn("shell-sensitive fragments", workstream_text)
            self.assertIn("`backticks`, `*.globs`", workstream_text)
            self.assertIn("`seriesWorkbenchRuntimeTest` and `*.csh5` part files", root_text)
            self.assertIn("execution: idle", status_text)
            self.assertIn("next: Repro hits `seriesWorkbenchRuntimeTest` before loading `*.csh5` part files.", status_text)


if __name__ == "__main__":
    unittest.main()
