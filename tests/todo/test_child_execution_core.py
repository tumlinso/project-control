from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from todo_orchestrator.child_execution import (  # noqa: E402
    authenticate_child_token,
    authorize_child_execution,
    cancel_child_execution,
    child_execution_status,
    heartbeat_child_execution,
    recover_child_execution,
    report_child_result,
    sweep_expired_child_executions,
)
from todo_orchestrator.models import TodoError  # noqa: E402
from todo_orchestrator.sessions import authenticate_claim  # noqa: E402
from tests.todo.v2_helpers import V2Repo, base_plan, safe_task  # noqa: E402


class ChildExecutionCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        self.repo.apply(base_plan([safe_task("PARENT", "src/owned")]))
        self.claim = self.repo.service.continue_work(task_id="PARENT")
        self.claim_token = self.claim["claim"]["claim_token"]

    def tearDown(self) -> None:
        self.repo.close()

    def mutate(self, operation):
        value, _revision = self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="test",
            entity_id="child",
            event_type="test.child",
            payload={},
            operation=lambda conn, revision: operation(conn),
        )
        return value

    def authorize(self, **overrides):
        values = {
            "objective": "Edit the bounded child surface",
            "scopes": ["src/owned/child"],
            "gates": ["FOCUSED"],
            "max_attempts": 2,
            "lease_seconds": 300,
        }
        values.update(overrides)
        return self.mutate(lambda conn: authorize_child_execution(
            conn, self.repo.root, self.claim_token, **values,
        ))

    def test_child_token_is_restricted_and_never_completes_parent(self) -> None:
        child = self.authorize()
        self.assertTrue(child["child_token"].startswith("toch_"))
        with self.repo.service.db.read() as conn:
            attempt = authenticate_child_token(conn, child["child_token"])
            self.assertEqual(attempt["task_id"], "PARENT")
            with self.assertRaises(TodoError) as rejected:
                authenticate_claim(conn, child["child_token"])
            self.assertEqual(rejected.exception.code, "invalid_claim_token")
        complete_process, complete = self.repo.run(
            "complete", "--claim-token", child["child_token"], "--disposition", "implemented",
        )
        self.assertNotEqual(complete_process.returncode, 0)
        self.assertEqual(complete["code"], "invalid_claim_token")

        heartbeat = self.mutate(lambda conn: heartbeat_child_execution(conn, child["child_token"], lease_seconds=120))
        self.assertEqual(heartbeat["state"], "running")
        result = self.mutate(lambda conn: report_child_result(
            conn,
            child["child_token"],
            status="needs_codex",
            summary="Architecture decision required",
            changed_paths=[],
        ))
        self.assertEqual(result["state"], "needs_codex")
        self.assertFalse(result["parent_task_completed"])
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id='PARENT'").fetchone()[0], "in_progress")
            self.assertEqual(
                conn.execute("SELECT state FROM child_scope_leases WHERE child_execution_id=?", (child["child_execution_id"],)).fetchone()[0],
                "released",
            )

    def test_scope_must_be_parent_subset_and_active_child_scopes_do_not_overlap(self) -> None:
        with self.assertRaises(TodoError) as outside:
            self.authorize(scopes=["src/not-owned"])
        self.assertEqual(outside.exception.code, "child_scope_violation")

        first = self.authorize(scopes=["src/owned/child"])
        with self.assertRaises(TodoError) as overlap:
            self.authorize(scopes=["src/owned/child/nested"])
        self.assertEqual(overlap.exception.code, "child_scope_unavailable")
        canceled = self.mutate(lambda conn: cancel_child_execution(conn, self.claim_token, first["child_execution_id"]))
        self.assertEqual(canceled["state"], "canceled")
        replacement = self.authorize(scopes=["src/owned/child/nested"])
        self.assertEqual(replacement["state"], "running")
        with self.assertRaises(TodoError) as escaped:
            self.mutate(lambda conn: report_child_result(
                conn,
                replacement["child_token"],
                status="succeeded",
                changed_paths=["src/owned/child/nested/../../../outside"],
            ))
        self.assertEqual(escaped.exception.code, "child_result_scope_violation")

    def test_failed_or_expired_attempt_requires_parent_recovery(self) -> None:
        child = self.authorize(max_attempts=2)
        failed = self.mutate(lambda conn: report_child_result(
            conn, child["child_token"], status="failed", summary="retryable",
        ))
        self.assertEqual(failed["state"], "recovery_required")
        recovered = self.mutate(lambda conn: recover_child_execution(
            conn, self.claim_token, child["child_execution_id"], lease_seconds=300,
        ))
        self.assertEqual(recovered["attempt"]["attempt_number"], 2)
        with self.repo.service.db.read() as conn:
            with self.assertRaises(TodoError):
                authenticate_child_token(conn, child["child_token"])
        final = self.mutate(lambda conn: report_child_result(
            conn, recovered["child_token"], status="failed", summary="not recoverable",
        ))
        self.assertEqual(final["state"], "failed")
        with self.assertRaises(TodoError) as exhausted:
            self.mutate(lambda conn: recover_child_execution(conn, self.claim_token, child["child_execution_id"]))
        self.assertEqual(exhausted.exception.code, "child_not_recoverable")

        stale = self.authorize(scopes=["src/owned/stale"], max_attempts=2)
        self.mutate(lambda conn: conn.execute(
            "UPDATE child_attempts SET expires_at='2000-01-01T00:00:00Z' WHERE child_execution_id=? AND state='active'",
            (stale["child_execution_id"],),
        ))
        swept = self.mutate(sweep_expired_child_executions)
        self.assertEqual(swept, [stale["child_execution_id"]])
        with self.repo.service.db.read() as conn:
            status = child_execution_status(conn, self.claim_token, stale["child_execution_id"])
        self.assertEqual(status["state"], "recovery_required")

    def test_cli_authorize_status_and_result_envelopes(self) -> None:
        created_process, created = self.repo.run(
            "child", "create",
            "--claim-token", self.claim_token,
            "--objective", "CLI child",
            "--scope", "src/owned/cli",
            "--max-attempts", "1",
        )
        self.assertEqual(created_process.returncode, 0, created_process.stderr)
        self.assertTrue(created["ok"])
        child = created["data"]

        status_process, status = self.repo.run(
            "child", "status", child["child_execution_id"], "--claim-token", self.claim_token,
        )
        self.assertEqual(status_process.returncode, 0, status_process.stderr)
        self.assertEqual(status["data"]["state"], "running")

        result_process, result = self.repo.run(
            "child", "report",
            "--child-token", child["child_token"],
            "--status", "succeeded",
            "--summary", "focused gate passed",
            "--changed-path", "src/owned/cli/file.py",
        )
        self.assertEqual(result_process.returncode, 0, result_process.stderr)
        self.assertEqual(result["data"]["state"], "succeeded")
        self.assertFalse(result["data"]["parent_task_completed"])


if __name__ == "__main__":
    unittest.main()
