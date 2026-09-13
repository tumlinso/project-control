from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from project_control.workflow_core.profiles import WorkProfile
from project_control.workflow_core.recovery import (
    RecoveryAuthorizationError,
    issue_recovery_authorization,
    run_authorized_recovery,
)
from project_control.workflow_core.retirement import RetirementRequest, retire_run_batch


class _Paths:
    def __init__(self, root: Path):
        self.state_dir = root
        self.db_file = root / "authority.sqlite3"


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
            issued = issue_recovery_authorization(
                service, engine, task_id="STALE", delegator_role="root", delegator_lineage="run/root", expires_seconds=60,
            )
            self.assertEqual(set(issued), {"authorization_id", "task_id", "expires_at", "authority_revision"})
            result = run_authorized_recovery(service, engine, authorization_id=str(issued["authorization_id"]), reason="root delegated")
            self.assertEqual(result["status"], "recovered")
            self.assertEqual(engine.executed, 1)
            with self.assertRaisesRegex(RecoveryAuthorizationError, "not_found"):
                run_authorized_recovery(service, engine, authorization_id=str(issued["authorization_id"]), reason="again")

    def test_authorized_recovery_refuses_changed_plan_before_execute(self):
        with TemporaryDirectory() as temporary:
            service, engine = _Service(Path(temporary)), _Engine()
            issued = issue_recovery_authorization(
                service, engine, task_id="STALE", delegator_role="parallel_head", delegator_lineage="root/head", expires_seconds=60,
            )
            engine.plan["authority_revision"] = 8
            with self.assertRaisesRegex(RecoveryAuthorizationError, "stale"):
                run_authorized_recovery(service, engine, authorization_id=str(issued["authorization_id"]), reason="root delegated")
            self.assertEqual(engine.executed, 0)


if __name__ == "__main__":
    unittest.main()
