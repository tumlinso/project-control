from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import ProjectSnapshot, RepositoryIdentity
from project_control.services.machine_inspection import machine_inspection
from project_control.subprocesses import CommandResult


class RecordingRunner:
    def __init__(self, output: str = "") -> None:
        self.output = output
        self.calls: list[list[str]] = []

    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        return CommandResult(Path(argv[0]).name, 0, self.output, "")


class MachineInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.config = ProjectControlConfig(workspaces={"demo": WorkspaceConfig(
            repositories={"source": RepositoryConfig(root=self.root)})})
        self.snapshot = ProjectSnapshot(workspace_id="demo", observed_at="2026-01-01T00:00:00Z",
            repositories={"source": RepositoryIdentity(commit="a" * 40, dirty=False, working_tree_fingerprint="clean")})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_gpu_queries_are_fixed_read_only_and_structured(self) -> None:
        runner = RecordingRunner("0, V100, 550, 32768, 30000, 0\n")
        result = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="gpu_summary", runner=runner)
        self.assertEqual(result.data["devices"][0]["name"], "V100")
        self.assertEqual(runner.calls, [["nvidia-smi", "--query-gpu=index,name,driver_version,memory.total,memory.free,utilization.gpu", "--format=csv,noheader,nounits"]])
        self.assertNotIn("uuid", str(result.data).lower())

    def test_topology_is_bounded_without_raw_command_fields(self) -> None:
        runner = RecordingRunner("x" * 300 + "\n" + "row\n" * 40)
        result = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="gpu_topology", runner=runner)
        self.assertEqual(len(result.data["topology_rows"]), 16)
        self.assertLessEqual(len(result.data["topology_rows"][0]), 256)
        self.assertNotIn("stdout", str(result.data))
        self.assertNotIn("command", str(result.data))

    def test_filesystem_capacity_uses_only_registered_or_private_roots(self) -> None:
        result = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="filesystem_capacity")
        labels = [item["root"] for item in result.data["filesystems"]]
        self.assertEqual(labels[0], "repository:source")
        self.assertIn("project_control_runtime", labels)
        self.assertNotIn(str(self.root), str(result.data))

    def test_services_are_selected_allowlist_only(self) -> None:
        runner = RecordingRunner("LoadState=loaded\nActiveState=active\nSubState=running\n")
        result = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="services", runner=runner)
        self.assertEqual([item["service"] for item in result.data["services"]], ["project-control.service", "project-control-local-inference.service"])
        self.assertTrue(all(call[0:3] == ["systemctl", "--user", "show"] for call in runner.calls))


if __name__ == "__main__":
    unittest.main()
