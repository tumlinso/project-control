from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control.workflow_core import recovery as recovery_module
from project_control.workflow_core.recovery import (
    issue_maintenance_recovery_authorization,
    run_authorized_recovery,
)


SKILLS = Path(os.environ.get("PROJECT_CONTROL_SKILLS_ROOT", "/home/tumlinson/.agents/skills"))


@unittest.skipUnless((SKILLS / "todo-orchestrator/tests/test_workflow_recovery.py").is_file(), "Todo recovery fixture unavailable")
class MaintenanceReceiptTests(unittest.TestCase):
    def test_real_authority_replays_after_receipt_projection_failure(self) -> None:
        import sys
        sys.path.insert(0, str(SKILLS / "todo-orchestrator/tests"))
        from test_workflow_recovery import WorkflowRecoveryTests
        from todo_orchestrator.git_state import scope_manifest
        from todo_orchestrator.workflow.recovery import RecoveryEngine

        fixture = WorkflowRecoveryTests("test_expired_readonly_coordinator_requeues_atomically_with_live_process")
        fixture.setUp()
        try:
            fixture.seed_expired_coordinator()
            engine = RecoveryEngine(fixture.repo.service.db, fixture.repo.root, fixture.project_uuid, process_probe=lambda *_: True)
            issued = issue_maintenance_recovery_authorization(
                fixture.repo.service, engine, task_id="A", recipient_principal="operator-a",
            )
            original_replace = recovery_module._replace
            failed = False
            def fail_receipt_once(path, value):
                nonlocal failed
                if value.get("completed_receipt") is not None and not failed:
                    failed = True
                    raise OSError("simulated post-commit receipt failure")
                return original_replace(path, value)
            with patch.object(recovery_module, "_replace", side_effect=fail_receipt_once):
                with self.assertRaisesRegex(OSError, "post-commit receipt"):
                    run_authorized_recovery(
                        fixture.repo.service, engine, authorization_id=str(issued["authorization_id"]),
                        reason="maintenance", recipient_principal="operator-a",
                    )
                reopened = RecoveryEngine(fixture.repo.service.db, fixture.repo.root, fixture.project_uuid, process_probe=lambda *_: True)
                replay = run_authorized_recovery(
                    fixture.repo.service, reopened, authorization_id=str(issued["authorization_id"]),
                    reason="maintenance", recipient_principal="operator-a",
                )
            self.assertEqual(replay["actions_applied"], 1)
            with fixture.repo.service.db.read() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_recovery_audit").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT state FROM workflow_dispatches WHERE id='DISPATCH'").fetchone()[0], "recovered")
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
