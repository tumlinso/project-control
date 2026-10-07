from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "scripts" / "pc-inference"
GPU_UUIDS = [
    "GPU-21131915-1488-23af-38dd-1743ae1f5cc8",
    "GPU-cf22c41f-5b58-77b1-3535-8fadd1ca6505",
    "GPU-d745da9c-7649-8334-41f1-483afa6f3206",
    "GPU-6c1cac7f-a360-0aef-ba98-2828bfd1db1a",
]


class SourceInferenceLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project-control"
        (self.root / "scripts").mkdir(parents=True)
        shutil.copy2(LAUNCHER, self.root / "scripts" / "pc-inference")
        self.home = Path(self.temporary.name) / "home"
        self.home.mkdir()
        self.capture = Path(self.temporary.name) / "capture.json"
        fake = self.root / "scripts" / "pc-dev"
        fake.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['PC_INFERENCE_TEST_CAPTURE']).write_text("
            "json.dumps({'argv': sys.argv[1:], 'env': dict(os.environ)}))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update({
            "HOME": str(self.home),
            "PC_INFERENCE_TEST_CAPTURE": str(self.capture),
            "PROJECT_CONTROL_RELEASE_MANIFEST": "/stale/manifest.json",
            "PROJECT_CONTROL_RELEASE_DIGEST": "stale-digest",
            "PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256": "stale-source-pin",
            "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT": "stale-todo-pin",
            "PROJECT_CONTROL_RUNTIME_PYTHON": "/stale/python",
            "PYTHONPATH": "/stale/pythonpath",
            "PYTHONHOME": "/stale/pythonhome",
            "CODING_WORKFLOW_RUNTIME_FINGERPRINT": "stale-legacy-pin",
            "CORE4_SUPERVISOR_RUNTIME_DIR": "/stale/runtime",
        })
        return environment

    def test_fixed_source_supervisor_command_preserves_state_and_policy(self) -> None:
        completed = subprocess.run(
            [str(self.root / "scripts" / "pc-inference")], env=self._environment(),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        captured = json.loads(self.capture.read_text(encoding="utf-8"))
        state = str(self.home / ".cache/project-control/as1-observer-analysis")
        self.assertEqual(captured["argv"], [
            "python", "-m", "project_control.runtime_binding", "local_worker.supervisor",
            "--serve", "--repo-root", state, "--service-state-root", state,
            "--runtime-root", f"{state}/runtime", "--observer-only",
        ])
        self.assertEqual(captured["env"]["PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR"], state)
        self.assertEqual(captured["env"]["TODO_BACKGROUND_HOST_RUNTIME_DIR"], "/tmp/codex-todo-orchestrator-1000")
        self.assertEqual(json.loads(captured["env"]["PROJECT_CONTROL_OBSERVER_GPU_UUIDS"]), GPU_UUIDS)
        for name in (
            "PYTHONPATH", "PYTHONHOME", "PROJECT_CONTROL_RELEASE_MANIFEST",
            "PROJECT_CONTROL_RELEASE_DIGEST", "PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256",
            "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT", "PROJECT_CONTROL_RUNTIME_PYTHON",
            "CODING_WORKFLOW_RUNTIME_FINGERPRINT", "CORE4_SUPERVISOR_RUNTIME_DIR",
        ):
            self.assertNotIn(name, captured["env"])

    def test_rejects_command_line_authority_overrides(self) -> None:
        completed = subprocess.run(
            [str(self.root / "scripts" / "pc-inference"), "--allowed-gpu-uuid", "GPU-other"],
            env=self._environment(), capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertFalse(self.capture.exists())


if __name__ == "__main__":
    unittest.main()
