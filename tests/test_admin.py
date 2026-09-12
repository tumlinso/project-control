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
    models_module = types.ModuleType("todo_orchestrator.models")
    models_module.TodoError = RuntimeError
    return {
        "todo_orchestrator": package,
        "todo_orchestrator.plan": plan_module,
        "todo_orchestrator.service": service_module,
        "todo_orchestrator.workflow": workflow,
        "todo_orchestrator.workflow.lanes": lanes_module,
        "todo_orchestrator.workflow.service": workflow_service_module,
        "todo_orchestrator.workflow.workspaces": workspaces_module,
        "todo_orchestrator.models": models_module,
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

    def test_prepare_blocked_exclusive_destination_before_producer_completion(self) -> None:
        connection = _workspace_database()
        self.addCleanup(connection.close)
        connection.execute("UPDATE workflow_lanes SET state='blocked' WHERE id='L-INTEGRATE'")
        connection.execute("UPDATE workflow_workspaces SET state='active' WHERE id='W-PRODUCER'")
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
        self.assertEqual(result["pending"][0]["integration_task_id"], "M40")
        self.assertEqual(result["pending"][0]["base_commit"], "producer-base")

    def test_prepare_never_materializes_terminal_exclusive_destination(self) -> None:
        for state in ("closed", "cancelled"):
            with self.subTest(state=state):
                connection = _workspace_database()
                self.addCleanup(connection.close)
                connection.execute("UPDATE workflow_lanes SET state=? WHERE id='L-INTEGRATE'", (state,))
                connection.execute("UPDATE workflow_workspaces SET state='active' WHERE id='W-PRODUCER'")
                connection.execute("DELETE FROM workflow_patch_artifacts")
                with tempfile.TemporaryDirectory() as directory:
                    plan, service = self._prepare_fixture(connection, Path(directory))
                    manager = Mock()
                    with patch.object(admin, "_runtime_identity"), \
                         patch.object(admin, "_git", side_effect=["", "canonical-head"]), \
                         patch.dict(sys.modules, _todo_runtime_modules(plan, service, manager)):
                        result = admin.prepare_run_workspaces("/repo", "/plan.json", "RUN")

                self.assertEqual(result["status"], "noop")
                self.assertEqual(result["pending"], [])

    def test_prepare_validator_owned_exclusive_destination(self) -> None:
        connection = _workspace_database()
        self.addCleanup(connection.close)
        connection.execute(
            "UPDATE workflow_lanes SET role='validator' WHERE id='L-INTEGRATE'"
        )
        with tempfile.TemporaryDirectory() as directory:
            plan, service = self._prepare_fixture(connection, Path(directory))
            manager = Mock()
            with patch.object(admin, "_runtime_identity"), \
                 patch.object(admin, "_git", side_effect=["", "canonical-head"]), \
                 patch.dict(sys.modules, _todo_runtime_modules(plan, service, manager)):
                result = admin.prepare_run_workspaces("/repo", "/plan.json", "RUN")

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["pending"][0]["lane_id"], "L-INTEGRATE")

    def test_scoped_prepare_keeps_selected_existing_producers_destination(self) -> None:
        connection = _workspace_database()
        self.addCleanup(connection.close)
        connection.execute(
            "UPDATE workflow_workspaces SET lane_id='L-SELECTED',state='artifact_ready' "
            "WHERE id='W-PRODUCER'"
        )
        connection.execute(
            "INSERT INTO workflow_lanes VALUES('L-SELECTED','RUN','implementer','isolated_merge','blocked')"
        )
        plan = {"runs": [{"id": "RUN", "lanes": [
            {"id": "L-SELECTED", "role": "implementer", "tasks": ["T-DONE"],
             "workspace": {"mode": "isolated_merge", "integration_task_id": "M40"}},
            {"id": "L-INTEGRATE", "role": "integrator", "tasks": ["M40"],
             "workspace": {"mode": "exclusive"}},
        ]}]}
        service = SimpleNamespace(
            db=_ReadDatabase(connection), project={"project_uuid": "project-uuid"},
            paths=SimpleNamespace(state_dir=Path("/state")),
        )
        manager = Mock()
        with patch.object(admin, "_runtime_identity"), \
             patch.object(admin, "_git", side_effect=["", "canonical-head"]), \
             patch.dict(sys.modules, _todo_runtime_modules(plan, service, manager)):
            result = admin.prepare_run_workspaces(
                "/repo", "/plan.json", "RUN", lane_id="L-SELECTED"
            )

        self.assertEqual([item["lane_id"] for item in result["pending"]], ["L-INTEGRATE"])

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
                    "tasks": ["T-NEW"],
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
            {"lane_id": "L-NEW", "task_id": "T-NEW"},
            {"lane_id": "L-UNRELATED", "task_id": "T-UNRELATED"},
        ]
        with patch.object(admin, "_runtime_identity"), \
             patch.object(admin, "_git", side_effect=["", "canonical-head"]), \
             patch.dict(sys.modules, modules):
            result = admin.prepare_run_workspaces(
                "/repo", "/plan.json", "RUN", lane_id="L-NEW"
            )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["pending"][0]["base_commit"], "producer-base")

    def test_prepare_derives_unambiguous_sealed_schedule_binding(self) -> None:
        connection = _workspace_database()
        self.addCleanup(connection.close)
        connection.execute("DELETE FROM workflow_workspaces")
        connection.execute("DELETE FROM workflow_patch_artifacts")
        connection.execute("DELETE FROM workflow_lanes")
        connection.execute(
            "INSERT INTO workflow_lanes VALUES(?,?,?,?,?)",
            ("L-A", "RUN", "implementer", "isolated_merge", "ready"),
        )
        connection.execute(
            "INSERT INTO workflow_lanes VALUES(?,?,?,?,?)",
            ("L-I", "RUN", "integrator", "exclusive", "blocked"),
        )
        connection.execute("INSERT INTO workflow_lane_tasks VALUES('L-I','I00',0,'queued')")
        plan = {"runs": [{"id": "RUN", "lanes": [{
            "id": "L-A", "role": "implementer", "tasks": ["A01", "A02"],
            "workspace": {"mode": "isolated_merge"},
        }]}]}
        service = SimpleNamespace(
            db=_ReadDatabase(connection), project={"project_uuid": "project-uuid"},
            paths=SimpleNamespace(state_dir=Path("/state")),
        )
        manager = Mock()
        modules = _todo_runtime_modules(plan, service, manager)
        modules["todo_orchestrator.workflow.lanes"].lane_candidates.return_value = [
            {"lane_id": "L-A", "task_id": "A01"}
        ]
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package"
            machine = package / "machine"
            machine.mkdir(parents=True)
            plan_file = machine / "native.todo-plan.json"
            plan_file.write_text("{}", encoding="utf-8")
            schedule_file = machine / "integration_schedule.json"
            schedule_file.write_text(
                json.dumps({"phases": [
                    {"integration_task": "I00", "required_tasks": ["A01"]},
                    {"integration_task": "I10", "required_tasks": ["A02"]},
                ]}), encoding="utf-8",
            )
            (package / "MANIFEST.sha256").write_text(
                f"{admin.hashlib.sha256(schedule_file.read_bytes()).hexdigest()}  machine/integration_schedule.json\n"
                f"{admin.hashlib.sha256(plan_file.read_bytes()).hexdigest()}  machine/native.todo-plan.json\n",
                encoding="utf-8",
            )
            with patch.object(admin, "_runtime_identity"), \
                 patch.object(admin, "_git", side_effect=["", "canonical-head"]), \
                 patch.dict(sys.modules, modules):
                result = admin.prepare_run_workspaces(
                    "/repo", plan_file, "RUN", apply=True,
                    confirmation=admin.PREPARE_WORKSPACES_CONFIRMATION,
                )

        self.assertEqual(result["pending"][0]["integration_task_id"], "I00")
        manager.return_value.create_workspace.assert_called_once_with(
            repository_root=Path("/repo"), repository_identity="repo-id", run_id="RUN",
            lane_id="L-A", mode="isolated_merge", base_commit="canonical-head",
            worktree_path=Path("/state") / "workflow-workspaces" / "l-a",
            branch="codex/l-a", integration_task_id="I00",
        )

    def test_prepare_rejects_ambiguous_sealed_schedule_binding(self) -> None:
        lane = {"id": "L-V", "tasks": ["V01", "V02"]}
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package"
            machine = package / "machine"
            machine.mkdir(parents=True)
            plan_file = machine / "native.todo-plan.json"
            plan_file.write_text("{}", encoding="utf-8")
            schedule_file = machine / "integration_schedule.json"
            schedule_file.write_text(
                json.dumps({"phases": [
                    {"integration_task": "I00", "required_tasks": ["V01"]},
                    {"integration_task": "I10", "required_tasks": ["V02"]},
                ]}), encoding="utf-8",
            )
            (package / "MANIFEST.sha256").write_text(
                f"{admin.hashlib.sha256(schedule_file.read_bytes()).hexdigest()}  machine/integration_schedule.json\n"
                f"{admin.hashlib.sha256(plan_file.read_bytes()).hexdigest()}  machine/native.todo-plan.json\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "multiple integration phases"):
                admin._scheduled_integration_task(plan_file, lane)

            self.assertEqual(
                admin._scheduled_integration_task(plan_file, lane, "V01"), "I00"
            )
            self.assertEqual(
                admin._scheduled_integration_task(plan_file, lane, "V02"), "I10"
            )
            duplicate = {"phases": [
                {"integration_task": "I00", "required_tasks": ["V01"]},
                {"integration_task": "I10", "required_tasks": ["V01"]},
            ]}
            schedule_file.write_text(json.dumps(duplicate), encoding="utf-8")
            (package / "MANIFEST.sha256").write_text(
                f"{admin.hashlib.sha256(schedule_file.read_bytes()).hexdigest()}  machine/integration_schedule.json\n"
                f"{admin.hashlib.sha256(plan_file.read_bytes()).hexdigest()}  machine/native.todo-plan.json\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "multiple integration phases"):
                admin._scheduled_integration_task(plan_file, lane, "V01")

    def test_advance_producer_wave_forwards_preview_and_apply(self) -> None:
        connection = _workspace_database()
        self.addCleanup(connection.close)
        connection.execute(
            "UPDATE workflow_workspaces SET state='integrated' WHERE id='W-PRODUCER'"
        )
        connection.execute(
            "INSERT INTO workflow_lane_tasks VALUES('L-PRODUCER','T-NEXT',0,'queued')"
        )
        with tempfile.TemporaryDirectory() as directory:
            plan = {"runs": [{"id": "RUN", "lanes": [{
                "id": "L-PRODUCER", "tasks": ["T-NEXT"]
            }]}]}
            _unused, service = self._prepare_fixture(connection, Path(directory))
            manager = Mock()
            manager.return_value.advance_producer_wave.return_value = {"revision": 12}
            with patch.object(admin, "_runtime_identity"), \
                 patch.object(admin, "_git", return_value="next-base"), \
                 patch.object(admin, "_scheduled_integration_task", return_value="M50"), \
                 patch.dict(sys.modules, _todo_runtime_modules(plan, service, manager)):
                preview = admin.advance_producer_wave(
                    "/repo", "/plan.json", "RUN", "L-PRODUCER", "candidate", "M50",
                    reason="next serial phase",
                )
                with self.assertRaisesRegex(ValueError, "differs from the sealed"):
                    admin.advance_producer_wave(
                        "/repo", "/plan.json", "RUN", "L-PRODUCER", "candidate", "M60",
                        reason="wrong phase",
                    )
                result = admin.advance_producer_wave(
                    "/repo", "/plan.json", "RUN", "L-PRODUCER", "candidate", "M50",
                    reason="next serial phase", apply=True,
                    confirmation=admin.ADVANCE_PRODUCER_WAVE_CONFIRMATION,
                )
                connection.execute(
                    "DELETE FROM workflow_lane_tasks WHERE lane_id='L-PRODUCER'"
                )
                with self.assertRaisesRegex(ValueError, "next queued producer"):
                    admin.advance_producer_wave(
                        "/repo", "/plan.json", "RUN", "L-PRODUCER", "candidate", "M50",
                        reason="stale candidate",
                    )

        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["base_commit"], "next-base")
        self.assertEqual(result["status"], "advanced")
        manager.return_value.advance_producer_wave.assert_called_once_with(
            repository_root=Path("/repo"), workspace_id="W-PRODUCER",
            base_commit="next-base", integration_task_id="M50",
            reason="next serial phase",
        )

    def test_schedule_binding_rejects_unsealed_or_malformed_sidecar(self) -> None:
        lane = {"id": "L-A", "tasks": ["A01"]}
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package"
            machine = package / "machine"
            machine.mkdir(parents=True)
            plan_file = machine / "native.todo-plan.json"
            plan_file.write_text("{}", encoding="utf-8")
            schedule_file = machine / "integration_schedule.json"
            schedule_file.write_text('{"phases": null}', encoding="utf-8")
            (package / "MANIFEST.sha256").write_text(
                f"{admin.hashlib.sha256(schedule_file.read_bytes()).hexdigest()}  machine/integration_schedule.json\n"
                f"{admin.hashlib.sha256(plan_file.read_bytes()).hexdigest()}  machine/native.todo-plan.json\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "phases list"):
                admin._scheduled_integration_task(plan_file, lane)

            schedule_file.write_text('{"phases": []}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest mismatch"):
                admin._scheduled_integration_task(plan_file, lane)

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
            "/repo", "/plan.json", "RUN", lane_id=None,
            apply=False, confirmation=None,
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

    def test_mark_run_workspaces_cleanup_eligible_cli_defaults_to_preview(self) -> None:
        preview = {"status": "ready", "pending": [{"workspace_id": "W-A"}]}
        with patch.object(
            admin, "mark_run_workspaces_cleanup_eligible", return_value=preview
        ) as cleanup, patch("sys.stdout", new_callable=io.StringIO) as output:
            result = admin.main([
                "mark-run-workspaces-cleanup-eligible", "--repo", "/repo", "--run", "RUN",
            ])
        self.assertEqual(result, 0)
        cleanup.assert_called_once_with("/repo", "RUN", apply=False, confirmation=None)
        self.assertEqual(json.loads(output.getvalue()), preview)


if __name__ == "__main__":
    unittest.main()
