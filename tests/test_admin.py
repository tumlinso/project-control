from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from project_control import admin


class _ReadDatabase:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @contextmanager
    def read(self):
        yield self.connection


def _workspace_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE workflow_runs(id TEXT PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE workflow_lanes(
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, role TEXT NOT NULL,
            workspace_mode TEXT NOT NULL, state TEXT NOT NULL
        );
        CREATE TABLE workflow_lane_tasks(
            lane_id TEXT NOT NULL, task_id TEXT NOT NULL, position INTEGER NOT NULL,
            state TEXT NOT NULL
        );
        CREATE TABLE workflow_workspaces(
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, lane_id TEXT NOT NULL,
            state TEXT NOT NULL, mode TEXT NOT NULL, integration_task_id TEXT,
            base_commit TEXT NOT NULL
        );
        CREATE TABLE workflow_patch_artifacts(
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, task_id TEXT NOT NULL,
            state TEXT NOT NULL, base_commit TEXT NOT NULL
        );
        INSERT INTO workflow_runs VALUES('RUN', 'active');
        INSERT INTO workflow_lanes VALUES(
            'L-INTEGRATE', 'RUN', 'integrator', 'exclusive', 'ready'
        );
        INSERT INTO workflow_lane_tasks VALUES('L-INTEGRATE', 'M40', 0, 'queued');
        INSERT INTO workflow_workspaces VALUES(
            'W-PRODUCER', 'RUN', 'L-PRODUCER', 'artifact_ready', 'isolated_merge',
            'M40', 'producer-base'
        );
        INSERT INTO workflow_patch_artifacts VALUES(
            'A-1', 'W-PRODUCER', 'T-PRODUCER', 'pending', 'producer-base'
        );
    """)
    return connection


def _todo_runtime_modules(plan, service, workspace_service):
    package = types.ModuleType("todo_orchestrator")
    package.__path__ = []
    workflow = types.ModuleType("todo_orchestrator.workflow")
    workflow.__path__ = []
    plan_module = types.ModuleType("todo_orchestrator.plan")
    plan_module.load_plan = Mock(return_value=plan)
    service_module = types.ModuleType("todo_orchestrator.service")
    service_module.Service = Mock(return_value=service)
    lanes_module = types.ModuleType("todo_orchestrator.workflow.lanes")
    lanes_module.lane_candidates = Mock(return_value=[])
    workflow_service_module = types.ModuleType("todo_orchestrator.workflow.service")
    workflow_service_module.repository_identity = Mock(return_value="repo-id")
    workspaces_module = types.ModuleType("todo_orchestrator.workflow.workspaces")
    workspaces_module.WorkspaceService = workspace_service
    return {
        "todo_orchestrator": package,
        "todo_orchestrator.plan": plan_module,
        "todo_orchestrator.service": service_module,
        "todo_orchestrator.workflow": workflow,
        "todo_orchestrator.workflow.lanes": lanes_module,
        "todo_orchestrator.workflow.service": workflow_service_module,
        "todo_orchestrator.workflow.workspaces": workspaces_module,
    }


class AdminCliTests(unittest.TestCase):
    def _prepare_fixture(self, connection: sqlite3.Connection, state_dir: Path):
        plan = {
            "runs": [{
                "id": "RUN",
                "lanes": [{
                    "id": "L-INTEGRATE",
                    "role": "integrator",
                    "workspace": {"mode": "exclusive"},
                }],
            }],
        }
        service = SimpleNamespace(
            db=_ReadDatabase(connection),
            project={"project_uuid": "project-uuid"},
            paths=SimpleNamespace(state_dir=state_dir),
        )
        return plan, service

    def test_prepare_run_workspaces_provisions_missing_exclusive_destination(self) -> None:
        connection = _workspace_database()
        self.addCleanup(connection.close)
        with tempfile.TemporaryDirectory() as directory:
            plan, service = self._prepare_fixture(connection, Path(directory))
            manager = Mock()
            manager.return_value.create_workspace.return_value = {"workspace_id": "W-DEST"}
            with patch.object(admin, "_runtime_identity"), \
                 patch.object(admin, "_git", side_effect=["", "canonical-head"]), \
                 patch.dict(sys.modules, _todo_runtime_modules(plan, service, manager)):
                result = admin.prepare_run_workspaces(
                    "/repo", "/plan.json", "RUN", apply=True,
                    confirmation=admin.PREPARE_WORKSPACES_CONFIRMATION,
                )

        self.assertEqual(result["status"], "prepared")
        self.assertEqual(len(result["pending"]), 1)
        self.assertEqual(result["pending"][0]["base_commit"], "producer-base")
        self.assertEqual(result["pending"][0]["integration_task_id"], "M40")
        manager.return_value.create_workspace.assert_called_once_with(
            repository_root=Path("/repo"), repository_identity="repo-id", run_id="RUN",
            lane_id="L-INTEGRATE", mode="exclusive", base_commit="producer-base",
            worktree_path=Path(directory) / "workflow-workspaces" / "l-integrate",
            branch="codex/l-integrate", integration_task_id="M40",
        )

    def test_prepare_exclusive_destination_before_first_artifact_exists(self) -> None:
        connection = _workspace_database()
        self.addCleanup(connection.close)
        connection.execute("DELETE FROM workflow_patch_artifacts")
        with tempfile.TemporaryDirectory() as directory:
            plan, service = self._prepare_fixture(connection, Path(directory))
            manager = Mock()
            with patch.object(admin, "_runtime_identity"), \
                 patch.object(admin, "_git", side_effect=["", "canonical-head"]), \
                 patch.dict(sys.modules, _todo_runtime_modules(plan, service, manager)):
                result = admin.prepare_run_workspaces("/repo", "/plan.json", "RUN")

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["pending"][0]["lane_id"], "L-INTEGRATE")
        self.assertEqual(result["pending"][0]["base_commit"], "producer-base")

    def test_prepare_run_workspaces_rejects_mixed_producer_bases(self) -> None:
        connection = _workspace_database()
        self.addCleanup(connection.close)
        connection.execute(
            "INSERT INTO workflow_workspaces VALUES(?,?,?,?,?,?,?)",
            ("W-OTHER", "RUN", "L-OTHER", "active", "isolated_merge", "M40", "other-base"),
        )
        with tempfile.TemporaryDirectory() as directory:
            plan, service = self._prepare_fixture(connection, Path(directory))
            manager = Mock()
            with patch.object(admin, "_runtime_identity"), \
                 patch.object(admin, "_git", side_effect=["", "canonical-head"]), \
                 patch.dict(sys.modules, _todo_runtime_modules(plan, service, manager)):
                with self.assertRaisesRegex(ValueError, "exact integration base"):
                    admin.prepare_run_workspaces("/repo", "/plan.json", "RUN")

    def test_prepare_run_workspaces_never_replaces_existing_destination(self) -> None:
        connection = _workspace_database()
        self.addCleanup(connection.close)
        connection.execute(
            "INSERT INTO workflow_workspaces VALUES(?,?,?,?,?,?,?)",
            ("W-DEST", "RUN", "L-INTEGRATE", "quarantined", "exclusive", "M40", "producer-base"),
        )
        with tempfile.TemporaryDirectory() as directory:
            plan, service = self._prepare_fixture(connection, Path(directory))
            manager = Mock()
            with patch.object(admin, "_runtime_identity"), \
                 patch.object(admin, "_git", side_effect=["", "canonical-head"]), \
                 patch.dict(sys.modules, _todo_runtime_modules(plan, service, manager)):
                result = admin.prepare_run_workspaces("/repo", "/plan.json", "RUN")
        self.assertEqual(result["status"], "noop")
        self.assertEqual(result["pending"], [])

    def test_prepare_missing_participant_uses_existing_integration_base(self) -> None:
        connection = _workspace_database()
        self.addCleanup(connection.close)
        connection.execute(
            "INSERT INTO workflow_lanes VALUES(?,?,?,?,?)",
            ("L-NEW", "RUN", "implementer", "isolated_merge", "ready"),
        )
        plan = {
            "runs": [{
                "id": "RUN",
                "lanes": [{
                    "id": "L-NEW",
                    "role": "implementer",
                    "workspace": {
                        "mode": "isolated_merge",
                        "integration_task_id": "M40",
                    },
                }, {
                    "id": "L-INTEGRATE",
                    "role": "integrator",
                    "workspace": {"mode": "exclusive"},
                }],
            }],
        }
        service = SimpleNamespace(
            db=_ReadDatabase(connection),
            project={"project_uuid": "project-uuid"},
            paths=SimpleNamespace(state_dir=Path("/state")),
        )
        manager = Mock()
        modules = _todo_runtime_modules(plan, service, manager)
        modules["todo_orchestrator.workflow.lanes"].lane_candidates.return_value = [
            {"lane_id": "L-NEW", "task_id": "T-NEW"}
        ]
        with patch.object(admin, "_runtime_identity"), \
             patch.object(admin, "_git", side_effect=["", "canonical-head"]), \
             patch.dict(sys.modules, modules):
            result = admin.prepare_run_workspaces("/repo", "/plan.json", "RUN")

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["pending"][0]["base_commit"], "producer-base")

    def test_inspect_only_forwards_without_recovery(self) -> None:
        with patch.object(admin, "inspect_recovery", return_value={"status": "safe"}) as inspect, \
             patch.object(admin, "recover") as recover, patch("sys.stdout", new_callable=io.StringIO) as output:
            result = admin.main(["recover", "--repo", "/repo", "--task", "T-1", "--reason", "owner", "--inspect-only"])
        self.assertEqual(result, 0)
        inspect.assert_called_once_with("/repo", "T-1")
        recover.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), {"status": "safe"})

    def test_recovery_forwards_explicit_owner_reason(self) -> None:
        with patch.object(admin, "recover") as recover:
            result = admin.main(["recover", "--repo", "/repo", "--reason", "owner approved"])
        self.assertEqual(result, 0)
        recover.assert_called_once_with("/repo", reason="owner approved", task_id=None)

    def test_prepare_run_workspaces_cli_defaults_to_preview(self) -> None:
        prepared = {"status": "ready", "pending": [{"lane_id": "L-A"}]}
        with patch.object(admin, "prepare_run_workspaces", return_value=prepared) as prepare, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            result = admin.main([
                "prepare-run-workspaces", "--repo", "/repo", "--plan", "/plan.json", "--run", "RUN",
            ])
        self.assertEqual(result, 0)
        prepare.assert_called_once_with(
            "/repo", "/plan.json", "RUN", apply=False, confirmation=None,
        )
        self.assertEqual(json.loads(output.getvalue()), prepared)

    def test_reconcile_workspace_base_cli_defaults_to_preview(self) -> None:
        preview = {"status": "ready", "lane_id": "L-A"}
        with patch.object(admin, "reconcile_workspace_base", return_value=preview) as reconcile, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            result = admin.main([
                "reconcile-workspace-base", "--repo", "/repo", "--run", "RUN",
                "--lane", "L-A", "--base", "abc", "--reason", "prior wave",
            ])
        self.assertEqual(result, 0)
        reconcile.assert_called_once_with(
            "/repo", "RUN", "L-A", "abc", reason="prior wave",
            apply=False, confirmation=None,
        )
        self.assertEqual(json.loads(output.getvalue()), preview)


if __name__ == "__main__":
    unittest.main()
