from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control.adapters.todo import TodoReadAdapter
from project_control.app import Runtime
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.snapshot import SnapshotBuilder
from project_control.todo_authority import (
    REQUIRED_TODO_READ_CAPABILITIES,
    TODO_READ_PORT_CONTRACT,
    resolve_todo_provider,
)


class FakeReadPort:
    def __init__(
        self,
        skills_root: Path,
        *,
        contract: str = TODO_READ_PORT_CONTRACT,
        large_export_bytes: int = 0,
    ):
        self.skills_root = skills_root
        self.contract = contract
        self.large_export_bytes = large_export_bytes
        self.calls: list[tuple[str, Path, tuple[str, ...]]] = []

    def identity(self):
        return {
            "contract": self.contract,
            "skills_root": str(self.skills_root),
            "source_identity": "todo-fixture-identity",
            "version": "fixture",
            "capabilities": list(REQUIRED_TODO_READ_CAPABILITIES),
        }

    def invoke(self, operation: str, *, repo_root: Path, arguments: tuple[str, ...] = ()):
        self.calls.append((operation, repo_root, arguments))
        project_uuid = json.loads(
            (repo_root / ".todo-orchestrator" / "project.json").read_text(encoding="utf-8")
        )["project_uuid"]
        payloads = {
            "semantic.workflow": {
                "available": True, "revision": 7, "project_uuid": project_uuid,
                "read_authority_fingerprint": "f" * 64, "active_run_id": None,
                "first_class_agents": [], "local_children": [],
            },
            "semantic.state": {
                "revision": 7, "project_uuid": project_uuid,
                "read_authority_fingerprint": "f" * 64, "tasks": [],
            },
            "status": {"project_revision": 7, "project_uuid": project_uuid},
            "export": {
                "project_revision": 7, "project": {"project_uuid": project_uuid},
                "tables": {"tasks": []}, "large_fixture": "x" * self.large_export_bytes,
            },
            "ready": {"tasks": []},
        }
        return {"ok": True, "code": "success", "data": payloads[operation]}


class TodoAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.skills_root = base / "skills"
        self.skills_root.mkdir()
        self.repo = base / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Fixture"], cwd=self.repo, check=True)
        (self.repo / "source.txt").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "source.txt"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.repo, check=True, capture_output=True)
        control = self.repo / ".todo-orchestrator"
        control.mkdir()
        (control / "project.json").write_text(json.dumps({"project_uuid": "fixture-uuid"}), encoding="utf-8")
        self.config = ProjectControlConfig(
            skills_root=self.skills_root,
            workspaces={
                "fixture": WorkspaceConfig(
                    authority_repository="source",
                    repositories={"source": RepositoryConfig(root=self.repo)},
                )
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_verified_in_process_port_is_preferred_and_drives_snapshot(self) -> None:
        port = FakeReadPort(self.skills_root)
        factory_calls: list[Path] = []

        def factory(root: Path):
            factory_calls.append(root)
            return port

        resolution = resolve_todo_provider(self.config, "fixture", read_port_factory=factory)
        self.assertTrue(resolution.compatible)
        self.assertEqual(resolution.mode, "in_process")
        self.assertIs(resolution.read_port, port)
        self.assertIsNone(resolution.todo_script)
        snapshot = SnapshotBuilder(self.config, todo_read_port_factory=factory).build("fixture")
        self.assertEqual(snapshot.todo_revision, 7)
        self.assertEqual(snapshot.project_uuid, "fixture-uuid")
        self.assertEqual(snapshot.todo_workflow["read_authority_fingerprint"], "f" * 64)
        self.assertEqual(snapshot.todo_tables, {"tasks": []})
        self.assertIn("semantic.workflow", [item[0] for item in port.calls])
        self.assertEqual(factory_calls, [self.skills_root.resolve(), self.skills_root.resolve()])

    def test_snapshot_builder_freezes_provider_after_first_resolution(self) -> None:
        port = FakeReadPort(self.skills_root)
        calls = 0

        def factory(_root: Path):
            nonlocal calls
            calls += 1
            return port

        builder = SnapshotBuilder(self.config, todo_read_port_factory=factory)
        builder.build("fixture")
        builder.build("fixture")
        self.assertEqual(calls, 1)

    def test_runtime_supplies_verified_port_and_large_reads_avoid_subprocess_capture(self) -> None:
        port = FakeReadPort(self.skills_root, large_export_bytes=9 * 1024 * 1024)
        factory = lambda _root: port
        with patch("project_control.app.todo_read_port_factory", return_value=factory) as configured:
            runtime = Runtime(self.config)
        configured.assert_called_once_with()
        self.assertIs(runtime.builder.todo_read_port_factory, factory)
        snapshot = runtime.snapshot("fixture")
        adapter = runtime.todo_adapter("fixture")
        self.assertEqual(snapshot.todo_revision, 7)
        self.assertIsNotNone(adapter)
        self.assertIs(adapter.read_port, port)
        self.assertIsNone(adapter.todo_script)
        self.assertIn("export", [item[0] for item in port.calls])

    def test_read_port_contract_mismatch_fails_closed_without_subprocess_fallback(self) -> None:
        script = self.skills_root / "todo-orchestrator" / "scripts" / "todo.py"
        script.parent.mkdir(parents=True)
        script.write_text("raise SystemExit(0)\n", encoding="utf-8")
        port = FakeReadPort(self.skills_root, contract="wrong-contract")
        resolution = resolve_todo_provider(
            self.config, "fixture", read_port_factory=lambda _root: port,
        )
        self.assertFalse(resolution.compatible)
        self.assertEqual(resolution.mode, "in_process")
        self.assertEqual(resolution.error_code, "todo_read_port_contract_mismatch")
        self.assertIsNone(resolution.todo_script)

    def test_read_port_requires_canonical_dotted_operation_capabilities(self) -> None:
        port = FakeReadPort(self.skills_root)
        identity = port.identity()
        self.assertIn("semantic.workflow", identity["capabilities"])
        self.assertNotIn("semantic_workflow", identity["capabilities"])

        resolution = resolve_todo_provider(
            self.config, "fixture", read_port_factory=lambda _root: port,
        )
        self.assertTrue(resolution.compatible)
        self.assertEqual(resolution.capabilities, tuple(REQUIRED_TODO_READ_CAPABILITIES))

        port.identity = lambda: {
            **identity,
            "capabilities": [item.replace(".", "_") for item in identity["capabilities"]],
        }
        incompatible = resolve_todo_provider(
            self.config, "fixture", read_port_factory=lambda _root: port,
        )
        self.assertFalse(incompatible.compatible)
        self.assertEqual(incompatible.error_code, "todo_read_port_schema_incompatible")
        self.assertIsNone(incompatible.todo_script)

    def test_subprocess_probe_labels_remain_cli_compatible(self) -> None:
        script = self.skills_root / "todo-orchestrator" / "scripts" / "todo.py"
        script.parent.mkdir(parents=True)
        script.write_text("raise SystemExit(0)\n", encoding="utf-8")
        cli_capabilities = (
            "semantic_state", "semantic_anchor", "semantic_delta",
            "semantic_workflow", "export",
        )
        with patch(
            "project_control.todo_authority._probe_todo_entrypoint",
            return_value=("fixture", "cli-fixture-identity", cli_capabilities, True),
        ):
            resolution = resolve_todo_provider(self.config, "fixture")
        self.assertTrue(resolution.compatible)
        self.assertEqual(resolution.mode, "subprocess")
        self.assertEqual(resolution.capabilities, cli_capabilities)
        self.assertEqual(resolution.todo_script, script.resolve())

    def test_adapter_routes_only_allowlisted_operations_through_port(self) -> None:
        port = FakeReadPort(self.skills_root)
        adapter = TodoReadAdapter(self.repo, read_port=port)
        self.assertEqual(adapter.status()["data"]["project_revision"], 7)
        self.assertEqual(adapter.semantic_workflow()["revision"], 7)
        with self.assertRaises(ValueError):
            adapter._call("claim")
        self.assertEqual([item[0] for item in port.calls], ["status", "semantic.workflow"])

    def test_legacy_root_variable_is_bounded_and_reported(self) -> None:
        config = self.config.model_copy(update={"skills_root": None})
        port = FakeReadPort(self.skills_root)
        with patch.dict(
            "os.environ",
            {"PROJECT_CONTROL_SKILLS_ROOT": "", "CODING_WORKFLOW_SKILLS_ROOT": str(self.skills_root)},
            clear=False,
        ):
            resolution = resolve_todo_provider(config, "fixture", read_port_factory=lambda _root: port)
        self.assertTrue(resolution.compatible)
        self.assertEqual(resolution.selection_source, "compatibility_environment")
        self.assertIn("coding_workflow_skills_root_deprecated", resolution.warnings)


if __name__ == "__main__":
    unittest.main()
