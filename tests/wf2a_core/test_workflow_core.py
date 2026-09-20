from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import io
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from project_control.workflow_core.profiles import WorkProfile
from project_control.workflow_core.recovery import (
    RecoveryAuthorizationError,
    issue_maintenance_recovery_authorization,
    issue_root_recovery_authorization,
    revoke_maintenance_recovery_authorization,
    run_authorized_recovery,
)
from project_control.workflow_core.retirement import RetirementRequest, retire_run_batch


class _Paths:
    def __init__(self, root: Path):
        self.state_dir = root
        self.db_file = root / "authority.sqlite3"
        self.repo_root = root


class _Service:
    def __init__(self, root: Path):
        self.paths = _Paths(root)


class _Engine:
    project_uuid = "project-uuid"

    def __init__(self):
        self.plan = {
            "project_uuid": self.project_uuid, "task_id": "STALE", "authority_revision": 7,
            "status": "recovery_needed", "actions": [{"kind": "release_claim", "id": "c"}],
            "blockers": [], "warnings": [], "file_policy": "preserve_all_no_repository_mutation",
        }
        self.executed = 0

    def inspect(self, task_id: str):
        if task_id != self.plan["task_id"]:
            raise AssertionError(task_id)
        return dict(self.plan)

    def execute(self, plan, reason):
        self.executed += 1
        self.assert_plan = plan
        return {"status": "recovered", "reason": reason}


class _RetirementService:
    def retire_run_batch(self, request):
        return {"status": "retired", "request": request}


class WorkflowCoreTests(unittest.TestCase):
    def test_prepare_retirement_cli_derives_kernel_request_from_real_fixture(self):
        from project_control import admin
        from project_control.cli import main
        from todo_orchestrator.service import Service
        from todo_orchestrator.projections import atomic_write_json
        with TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            state = root / "runtime"
            with patch.dict(os.environ, {"TODO_ORCHESTRATOR_STATE_DIR": str(state)}, clear=False):
                service, _ = Service.bootstrap(root, "fixture")
                plan = {"schema_version": 2, "project": {"name": "fixture"}, "invariants": [], "decisions": [], "locks": [], "interfaces": [], "barriers": [], "resource_classes": [], "tasks": [
                    {"id": "OLD", "kind": "task", "title": "old", "objective": "old", "priority": 0, "parallel_policy": "parallel_safe", "scope": {"exclusive_paths": ["old"]}},
                    {"id": "NEW", "kind": "task", "title": "new", "objective": "new", "priority": 0, "parallel_policy": "parallel_safe", "scope": {"exclusive_paths": ["new"]}},
                ]}
                plan_file = root / "plan.json"; atomic_write_json(plan_file, plan); service.plan_apply(str(plan_file))
                def seed(conn, revision):
                    for run, task in (("OLD-RUN", "OLD"), ("NEW-RUN", "NEW")):
                        conn.execute("INSERT INTO workflow_runs(id,root_task_id,status,created_at,updated_at,revision) VALUES(?,?, 'active','now','now',?)", (run, task, revision))
                    conn.execute("INSERT INTO workflow_lanes(id,run_id,role,created_at,updated_at,revision) VALUES('OLD-LANE','OLD-RUN','integrator','now','now',?)", (revision,))
                    conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('OLD-LANE',0,'OLD','queued','now',?)", (revision,))
                service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="OLD", event_type="fixture.seed", payload={}, operation=seed)
                intent = root / "intent.json"; output = root / "request.json"
                intent.write_text(json.dumps({"source_run_id": "OLD-RUN", "successor_run_id": "NEW-RUN", "task_ids": ["OLD"], "dispositions": {"OLD": "superseded"}, "reason": "fixture replacement"}))
                with patch.object(admin, "_runtime_identity"), patch("sys.argv", ["project-control", "admin", "prepare-retire-run-batch", "--repo", str(root), "--intent", str(intent), "--output", str(output)]), patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    self.assertEqual(main(), 0)
                result = json.loads(stdout.getvalue())
                self.assertEqual(result["status"], "prepared")
                self.assertEqual(json.loads(output.read_text())["expected_tasks"]["OLD"]["status"], "planned")

    def test_model_facing_cli_cannot_issue_recovery(self):
        from project_control.cli import main
        with patch("sys.argv", ["project-control", "admin", "authorize-delegated-recovery", "--help"]):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)

    def test_work_profile_is_strict_and_does_not_mix_execution(self):
        profile = WorkProfile(difficulty="hard", risk="high", work_type="implementation", context_depth="deep")
        self.assertEqual(profile.work_type, "implementation")
        with self.assertRaises(ValidationError):
            WorkProfile(difficulty="hard", risk="high", work_type="implementation", context_depth="deep", model="x")

    def test_retirement_is_kernel_only_and_typed(self):
        request = RetirementRequest(
            source_run_id="OLD", successor_run_id="NEW", expected_project_uuid="p", expected_revision=4,
            expected_fingerprint="f", expected_tasks={"OLD-1": {"status": "planned", "version": 4}},
            dispositions={"OLD-1": "superseded"}, reason="replacement ready",
        )
        self.assertEqual(retire_run_batch(_RetirementService(), request)["status"], "retired")
        with self.assertRaisesRegex(RuntimeError, "kernel_unavailable"):
            retire_run_batch(object(), request)

    def test_authorized_recovery_is_exact_single_use_and_opaque(self):
        with TemporaryDirectory() as temporary:
            service, engine = _Service(Path(temporary)), _Engine()
            issued = issue_root_recovery_authorization(service, engine, task_id="STALE", expires_seconds=60)
            self.assertEqual(set(issued), {"authorization_id", "task_id", "expires_at", "authority_revision"})
            result = run_authorized_recovery(service, engine, authorization_id=str(issued["authorization_id"]), reason="root delegated")
            self.assertEqual(result["status"], "recovered")
            self.assertEqual(engine.executed, 1)
            with self.assertRaisesRegex(RecoveryAuthorizationError, "not_found"):
                run_authorized_recovery(service, engine, authorization_id=str(issued["authorization_id"]), reason="again")

    def test_authorized_recovery_refuses_changed_plan_before_execute(self):
        with TemporaryDirectory() as temporary:
            service, engine = _Service(Path(temporary)), _Engine()
            issued = issue_root_recovery_authorization(service, engine, task_id="STALE", expires_seconds=60)
            engine.plan["authority_revision"] = 8
            with self.assertRaisesRegex(RecoveryAuthorizationError, "stale"):
                run_authorized_recovery(service, engine, authorization_id=str(issued["authorization_id"]), reason="root delegated")
            self.assertEqual(engine.executed, 0)

    def test_maintenance_authorization_binds_principal_and_replays_receipt(self):
        with TemporaryDirectory() as temporary:
            service, engine = _Service(Path(temporary)), _Engine()
            issued = issue_maintenance_recovery_authorization(
                service, engine, task_id="STALE", recipient_principal="operator-a", expires_seconds=60,
            )
            with self.assertRaisesRegex(RecoveryAuthorizationError, "principal_mismatch"):
                run_authorized_recovery(
                    service, engine, authorization_id=str(issued["authorization_id"]),
                    reason="maintenance", recipient_principal="operator-b",
                )
            first = run_authorized_recovery(
                service, engine, authorization_id=str(issued["authorization_id"]),
                reason="maintenance", recipient_principal="operator-a",
            )
            replay = run_authorized_recovery(
                service, engine, authorization_id=str(issued["authorization_id"]),
                reason="maintenance", recipient_principal="operator-a",
            )
            self.assertEqual(first, replay)
            self.assertEqual(engine.executed, 1)

    def test_maintenance_authorization_can_be_revoked_before_execution(self):
        with TemporaryDirectory() as temporary:
            service, engine = _Service(Path(temporary)), _Engine()
            issued = issue_maintenance_recovery_authorization(
                service, engine, task_id="STALE", recipient_principal="operator-a", expires_seconds=60,
            )
            revoke_maintenance_recovery_authorization(service, authorization_id=str(issued["authorization_id"]))
            with self.assertRaisesRegex(RecoveryAuthorizationError, "revoked"):
                run_authorized_recovery(
                    service, engine, authorization_id=str(issued["authorization_id"]),
                    reason="maintenance", recipient_principal="operator-a",
                )

    def test_unknown_authorization_does_not_create_private_state_directory(self):
        with TemporaryDirectory() as temporary:
            service, engine = _Service(Path(temporary)), _Engine()
            with self.assertRaisesRegex(RecoveryAuthorizationError, "not_found"):
                run_authorized_recovery(
                    service, engine, authorization_id="rca_missing", reason="maintenance",
                    recipient_principal="operator-a",
                )
            self.assertFalse((Path(temporary) / "project-control-recovery-authorizations").exists())


if __name__ == "__main__":
    unittest.main()
