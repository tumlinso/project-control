from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from project_control.adapters.ctxpp import CtxppReadAdapter
from project_control.adapters.cuda import CudaReadAdapter
from project_control.adapters.host import HostReadAdapter
from project_control.adapters.local_worker import LocalWorkerReadAdapter
from project_control.subprocesses import CommandError, CommandResult


class RecordingRunner:
    def __init__(self, output: str = ""):
        self.output = output
        self.calls: list[list[str]] = []

    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        return CommandResult(Path(argv[0]).name, 0, self.output, "")


class DomainAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Tests"], cwd=self.root, check=True)
        (self.root / "kernel.cu").write_text("__global__ void update_cells() {}\n", encoding="utf-8")
        subprocess.run(["git", "add", "kernel.cu"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.root, check=True, capture_output=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_ctxpp_absent_falls_back_without_scan_or_writes(self) -> None:
        before = subprocess.run(["git", "status", "--porcelain"], cwd=self.root, text=True, capture_output=True).stdout
        result = CtxppReadAdapter(self.root).inspect("update_cells")
        after = subprocess.run(["git", "status", "--porcelain"], cwd=self.root, text=True, capture_output=True).stdout
        self.assertEqual(result["status"], "partial")
        self.assertIn("semantic_context_unavailable", result["warnings"])
        self.assertFalse((self.root / ".ctxpp").exists())
        self.assertEqual(before, after)

    def test_local_worker_reads_state_without_process_probe(self) -> None:
        state = Path(self.temporary.name) / "supervisor-state.json"
        state.write_text(json.dumps({"status": "ready", "active_leases": 0, "slots": [{"leased": False, "state": "ready", "endpoint": "secret"}]}), encoding="utf-8")
        before = state.stat().st_mtime_ns
        result = LocalWorkerReadAdapter(state).status()
        self.assertEqual(result["active_leases"], 0)
        self.assertNotIn("endpoint", str(result))
        self.assertEqual(before, state.stat().st_mtime_ns)

    def test_cuda_reads_existing_artifact_without_controller(self) -> None:
        artifact = self.root / "cuda-campaigns.json"
        artifact.write_text(json.dumps({"schema_version": 1, "campaigns": [{"id": "c1", "status": "accepted", "gpu_uuid": "hidden"}]}), encoding="utf-8")
        before = artifact.stat().st_mtime_ns
        result = CudaReadAdapter(self.root).status("c1")
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("gpu_uuid", str(result))
        self.assertEqual(before, artifact.stat().st_mtime_ns)

    def test_host_uses_lightweight_query_without_uuid_or_topology(self) -> None:
        runner = RecordingRunner("0, Tesla V100, 32768, 30000, 0\n")
        result = HostReadAdapter(runner).capacity()
        self.assertEqual(result["accelerators"][0]["logical_device"], "gpu-0")
        self.assertEqual(runner.calls[0][0], "nvidia-smi")
        self.assertNotIn("uuid", " ".join(runner.calls[0]).lower())


if __name__ == "__main__":
    unittest.main()
