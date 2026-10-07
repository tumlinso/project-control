from __future__ import annotations

import json
import os
import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control.workflow_core import recovery as recovery_module
from project_control.workflow_core.recovery import (
    issue_maintenance_recovery_authorization,
    run_authorized_recovery,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECOVERY_FIXTURE = PROJECT_ROOT / "tests" / "todo" / "test_workflow_recovery.py"


class MaintenanceReceiptTests(unittest.TestCase):
    def test_real_authority_replays_after_receipt_projection_failure(self) -> None:
        import sys
        from tests.todo.test_workflow_recovery import WorkflowRecoveryTests
        from todo_orchestrator.git_state import scope_manifest
        from todo_orchestrator.workflow.recovery import RecoveryEngine

        fixture = WorkflowRecoveryTests("test_expired_readonly_coordinator_requeues_atomically_with_live_process")
        fixture.setUp()
        try:
            fixture.seed_expired_coordinator()
            engine = RecoveryEngine(fixture.repo.service.db, fixture.repo.root, fixture.project_uuid, process_probe=lambda *_: False)
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

    def test_public_mcp_replays_expired_committed_recovery_after_receipt_failure(self) -> None:
        """The public preflight must consult the canonical audit before expiry."""
        import sys
        import time
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from tests.todo.test_workflow_recovery import WorkflowRecoveryTests
        from todo_orchestrator.git_state import scope_manifest
        from todo_orchestrator.workflow.recovery import RecoveryEngine

        fixture = WorkflowRecoveryTests("test_expired_readonly_coordinator_requeues_atomically_with_live_process")
        fixture.setUp()
        try:
            fixture.seed_dispatch(capability=True)
            fixture.mutate(lambda conn, revision: (
                conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('LANE',0,'A','queued','now',?)", (revision,)),
                conn.execute("UPDATE claims SET state=?,released_at=?,expires_at=?,baseline_manifest_json=? WHERE id=?", ('expired_clean', '2000-01-01', '2000-01-01', json.dumps(scope_manifest(fixture.repo.root, ['src/a'])), fixture.claim_id)),
                conn.execute("UPDATE tasks SET status=? WHERE id=?", ('planned', 'A')),
                conn.execute("UPDATE lock_leases SET state=? WHERE claim_id=?", ('released', fixture.claim_id)),
            ))
            engine = RecoveryEngine(fixture.repo.service.db, fixture.repo.root, fixture.project_uuid, process_probe=lambda *_: False)
            issued = issue_maintenance_recovery_authorization(
                fixture.repo.service, engine, task_id='A', recipient_principal='receipt-fault-operator', expires_seconds=1,
            )
            unexecuted = issue_maintenance_recovery_authorization(
                fixture.repo.service, engine, task_id='A', recipient_principal='receipt-fault-operator', expires_seconds=1,
            )
            original_replace = recovery_module._replace
            failed = False
            def fail_receipt_once(path, value):
                nonlocal failed
                if value.get('completed_receipt') is not None and not failed:
                    failed = True
                    raise OSError('simulated post-commit receipt failure')
                return original_replace(path, value)
            with patch.object(recovery_module, '_replace', side_effect=fail_receipt_once):
                with self.assertRaisesRegex(OSError, 'post-commit receipt'):
                    run_authorized_recovery(
                        fixture.repo.service, engine, authorization_id=str(issued['authorization_id']),
                        reason='maintenance', recipient_principal='receipt-fault-operator',
                    )
            time.sleep(1.1)

            async def public_call(authorization_id):
                environment = dict(os.environ)
                environment['TODO_ORCHESTRATOR_STATE_DIR'] = str(fixture.repo.state_root)
                for key in ('PROJECT_CONTROL_RELEASE_MANIFEST', 'PROJECT_CONTROL_RELEASE_DIGEST',
                            'PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT', 'CODING_WORKFLOW_RUNTIME_FINGERPRINT',
                            'PROJECT_CONTROL_SKILLS_ROOT', 'PROJECT_CONTROL_OBSERVER_SKILLS_ROOT',
                            'OBSERVER_SKILLS_ROOT', 'CODING_WORKFLOW_SKILLS_ROOT'):
                    environment.pop(key, None)
                environment['PYTHONPATH'] = str(PROJECT_ROOT / 'src')
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=['-m', 'project_control.maintenance_host', 'operator', '--principal', 'receipt-fault-operator'],
                    env=environment, cwd=str(Path.cwd()),
                )
                async with stdio_client(parameters) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return await session.call_tool('maintain_execution', {
                            'repo_root': str(fixture.repo.root), 'authorization_id': str(authorization_id),
                        })
            replay_response = asyncio.run(public_call(issued['authorization_id']))
            self.assertFalse(replay_response.isError, replay_response.content)
            replay = json.loads(replay_response.content[0].text)
            self.assertEqual(replay['status'], 'maintained')
            self.assertTrue(replay['replayed'])
            expired_response = asyncio.run(public_call(unexecuted['authorization_id']))
            self.assertTrue(expired_response.isError)
            self.assertIn('recovery_authorization_expired', expired_response.content[0].text)
            with fixture.repo.service.db.read() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM workflow_recovery_audit').fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT state FROM workflow_dispatches WHERE id='DISPATCH'").fetchone()[0], 'recovered')
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
