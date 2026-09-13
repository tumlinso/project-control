from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import ProjectSnapshot, RepositoryIdentity
from project_control.services.machine_inspection import _SandboxedDiagnosticRunner, machine_inspection
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
        self.assertEqual([item["service"] for item in result.data["services"]], ["project-control.service"])
        self.assertTrue(all(call[0:3] == ["systemctl", "--user", "show"] for call in runner.calls))

    def test_processes_are_fixed_structured_and_bounded(self) -> None:
        with patch("project_control.services.machine_inspection._host_processes", return_value=[
            {"process": "local-llama", "rss_kib": 12345, "state": "S"}
        ]):
            result = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="processes")
        self.assertEqual(result.data["processes"][0], {"process": "local-llama", "rss_kib": 12345, "state": "S"})
        self.assertNotIn("pid", result.data["processes"][0])

    def test_home_filesystem_read_and_credential_denial_are_contained(self) -> None:
        home = Path(self.temporary.name) / "home"
        home.mkdir()
        (home / "note.txt").write_text("ordinary local evidence", encoding="utf-8")
        (home / ".ssh").mkdir()
        (home / ".ssh" / "id_rsa").write_text("secret", encoding="utf-8")
        with patch("project_control.services.machine_inspection.Path.home", return_value=home):
            readable = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="filesystem",
                filesystem={"root": "home", "operation": "read", "path": "note.txt"})
            denied = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="filesystem",
                filesystem={"root": "home", "operation": "read", "path": ".ssh/id_rsa"})
        self.assertEqual(readable.data["filesystem"]["text"], "ordinary local evidence")
        self.assertEqual(denied.data["status"], "partial")

    def test_filesystem_text_redacts_common_local_credentials(self) -> None:
        home = Path(self.temporary.name) / "home"
        home.mkdir()
        (home / "diagnostic.txt").write_text(
            'endpoint=local\n{"token":"plain_secret_value","password":"also_plain"}\n'
            '{"credential":"value containing several secret words"}\n'
            '{"api_key":"escaped \\"quote\\" and secret suffix"}\n'
            "hf=hf_abcdefghijklmnopqrstuv\n", encoding="utf-8")
        with patch("project_control.services.machine_inspection.Path.home", return_value=home):
            result = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="filesystem",
                filesystem={"root": "home", "operation": "read", "path": "diagnostic.txt"})
        self.assertIn("endpoint=local", result.data["filesystem"]["text"])
        self.assertNotIn("plain_secret_value", result.data["filesystem"]["text"])
        self.assertNotIn("also_plain", result.data["filesystem"]["text"])
        self.assertNotIn("several secret words", result.data["filesystem"]["text"])
        self.assertNotIn("secret suffix", result.data["filesystem"]["text"])
        self.assertNotIn("hf_", result.data["filesystem"]["text"])

    def test_repository_traversal_and_symlink_escape_are_rejected(self) -> None:
        (self.root / "safe").mkdir()
        (self.root / "safe" / "escape").symlink_to("/etc/passwd")
        traversal = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="filesystem",
            filesystem={"root": "repository", "repository": "source", "operation": "stat", "path": "../etc/passwd"})
        escaped = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="filesystem",
            filesystem={"root": "repository", "repository": "source", "operation": "read", "path": "safe/escape"})
        self.assertEqual(traversal.data["status"], "partial")
        self.assertEqual(escaped.data["status"], "partial")

    def test_mnt_listing_is_allowlisted_and_pathless(self) -> None:
        result = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="filesystem",
            filesystem={"root": "mnt", "operation": "list", "path": "."})
        self.assertEqual(result.data["status"], "ok")
        self.assertNotIn("/mnt", str(result.data))

    def test_filesystem_search_has_a_hard_scan_bound(self) -> None:
        home = Path(self.temporary.name) / "home"
        home.mkdir()
        for index in range(8):
            (home / f"item-{index}.txt").write_text("ordinary evidence", encoding="utf-8")
        with patch("project_control.services.machine_inspection.Path.home", return_value=home), \
             patch("project_control.services.machine_inspection._MAX_ENTRIES_SCANNED", 3):
            result = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="filesystem",
                filesystem={"root": "home", "operation": "search", "path": ".", "query": "evidence"})
        self.assertTrue(result.data["filesystem"]["scan_truncated"])
        self.assertLessEqual(len(result.data["filesystem"]["matches"]), 3)

    def test_proc_filesystem_excludes_process_magic_links(self) -> None:
        denied = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="filesystem",
            filesystem={"root": "proc", "operation": "read", "path": "self/environ"})
        allowed = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="filesystem",
            filesystem={"root": "proc", "operation": "stat", "path": "meminfo"})
        self.assertEqual(denied.data["status"], "partial")
        self.assertEqual(allowed.data["status"], "ok")

    def test_new_host_diagnostics_are_fixed_and_bounded(self) -> None:
        for diagnostic, command in (("pcie_devices", "lspci"), ("storage_block", "lsblk"),
                                    ("project_control_logs", "journalctl"), ("versions", "python3")):
            runner = RecordingRunner("row\n" * 40)
            result = machine_inspection(self.config, self.snapshot, project="demo", diagnostic=diagnostic, runner=runner)
            self.assertLess(len(str(result.model_dump(mode="json")).encode()), 24 * 1024)
            self.assertEqual(runner.calls[0][0], command)

        with patch("project_control.services.machine_inspection._host_network_state", return_value={
            "interfaces": [{"name": "eth0", "state": "up", "mtu": 1500}], "routes": [],
        }):
            network = machine_inspection(self.config, self.snapshot, project="demo", diagnostic="network_state")
        self.assertEqual(network.data["interfaces"][0]["name"], "eth0")

    def test_external_diagnostics_use_read_only_no_network_sandbox(self) -> None:
        sandbox = MagicMock()
        sandbox.executable = "/usr/bin/bwrap"
        sandbox.probe.return_value = True
        underlying = MagicMock()
        underlying.run.return_value = CommandResult("bwrap", 0, "ok", "")
        with patch("project_control.services.machine_inspection.BubblewrapSandbox", return_value=sandbox), \
             patch("project_control.services.machine_inspection.FixedCommandRunner", return_value=underlying), \
             patch("project_control.services.machine_inspection.shutil.which", return_value="/usr/bin/lspci"):
            runner = _SandboxedDiagnosticRunner()
            runner.run(["lspci", "-mm"], cwd=Path("/"), timeout=9)
        command = underlying.run.call_args.args[0]
        self.assertIn("--unshare-net", command)
        self.assertIn("--cap-drop", command)
        self.assertIn("--ro-bind", command)
        self.assertIn("--clearenv", command)
        self.assertIn("--fsize=0", command)
        self.assertEqual(underlying.run.call_args.kwargs["timeout"], 4.0)
        with self.assertRaises(Exception):
            runner.run(["sh", "-c", "id"], cwd=Path("/"))


if __name__ == "__main__":
    unittest.main()
