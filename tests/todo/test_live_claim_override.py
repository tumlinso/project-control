from __future__ import annotations

import json
import io
import threading
import unittest
from unittest import mock

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.config import utc_now
from todo_orchestrator.background.store import BackgroundStore
from todo_orchestrator.cli import build_parser
from todo_orchestrator.models import TodoError


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class LiveClaimOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        task = safe_task(
            "A",
            "src/a",
            claim_locks=["task-lock"],
            gates=[{
                "id": "A-GATE", "type": "command", "argv": ["python", "-c", "pass"],
                "required": True,
            }],
        )
        self.repo.apply(base_plan([task], locks=[{"name": "task-lock"}]))

    def tearDown(self) -> None:
        self.repo.close()

    def claim(self, *, facade: bool = True) -> dict[str, object]:
        return self.repo.service.continue_work(
            task_id="A",
            owner_system="coding-workflow" if facade else None,
            owner_instance_id="fi_prior" if facade else None,
        )

    def approve(self, reason: str = "lost opaque handle") -> dict[str, object]:
        return self.repo.service.live_recovery_approve("A", reason, 300)

    def test_manual_approval_is_required_and_non_facade_claims_are_refused(self) -> None:
        self.claim()
        with self.assertRaises(TodoError) as missing:
            self.repo.service.live_recovery_override("A", "toa_model_flag", "fi_new")
        self.assertEqual(missing.exception.code, "override_requires_permission")
        result, envelope = self.repo.run(
            "recover", "live-approve", "A", "--reason", "noninteractive model attempt"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(envelope["code"], "manual_approval_terminal_required")

        other = V2Repo()
        self.addCleanup(other.close)
        other.apply(base_plan([safe_task("A", "src/a")]))
        other.service.continue_work(task_id="A")
        report = other.service.live_recovery_inspect("A")
        self.assertFalse(report["eligible"])
        self.assertIn("claim_owner_not_verifiable_facade", report["blockers"])
        with self.assertRaises(TodoError) as refused:
            other.service.live_recovery_approve("A", "not facade owned", 300)
        self.assertEqual(refused.exception.code, "live_override_blocked")
        self.assertIn("not owned by a compatible Project Control workflow facade", refused.exception.message)

    def test_ineligible_manual_approval_fails_before_confirmation_prompt(self) -> None:
        args = build_parser().parse_args([
            "recover", "live-approve", "A", "--reason", "lost handle",
            "--repo-root", str(self.repo.root),
        ])
        report = {
            "task_id": "A", "project_revision": 7, "claim_fingerprint": "a" * 64,
            "eligible": False, "blockers": ["claim_owner_not_verifiable_facade"],
        }
        with (
            mock.patch("todo_orchestrator.commands.coordination.Service") as service_type,
            mock.patch("sys.stdin", TtyStringIO()),
            mock.patch("sys.stdout", TtyStringIO()),
            mock.patch("builtins.input") as confirmation,
        ):
            service_type.return_value.live_recovery_inspect.return_value = report
            with self.assertRaises(TodoError) as refused:
                args.handler(args)
        self.assertEqual(refused.exception.code, "live_override_blocked")
        self.assertIn("not owned by coding-workflow", refused.exception.message)
        confirmation.assert_not_called()
        service_type.return_value.live_recovery_approve.assert_not_called()

    def test_success_is_single_use_preserves_semantics_and_finishes(self) -> None:
        original = self.claim()
        original_token = original["claim"]["claim_token"]
        approval = self.approve()
        token = approval["approval_token"]
        recovered = self.repo.service.live_recovery_override("A", token, "fi_new")
        self.assertNotEqual(recovered["claim"]["claim_token"], original_token)
        self.assertEqual(recovered["task"]["objective"], "Implement A")
        self.assertEqual(recovered["scope"]["exclusive_paths"], ["src/a"])
        self.assertEqual(recovered["gates"][0]["id"], "A-GATE")
        self.assertEqual(recovered["claim"]["owner_instance_id"], "fi_new")
        self.assertEqual(recovered["claim"]["owner_system"], "project-control")
        with self.repo.service.db.read() as conn:
            states = [row[0] for row in conn.execute(
                "SELECT state FROM claims WHERE task_id='A' ORDER BY created_at"
            )]
            self.assertEqual(states, ["overridden", "active"])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM lock_leases WHERE state='active'"
            ).fetchone()[0], 1)
            audit = dict(conn.execute("SELECT * FROM live_recovery_audit").fetchone())
            self.assertEqual(audit["reason"], "lost opaque handle")
            self.assertEqual(audit["prior_instance_id"], "fi_prior")
            self.assertEqual(audit["new_instance_id"], "fi_new")
            self.assertNotIn("token", json.dumps(audit))
        with self.assertRaises(TodoError) as consumed:
            self.repo.service.live_recovery_override("A", token, "fi_other")
        self.assertEqual(consumed.exception.code, "approval_consumed")
        self.repo.service.gate_run("A-GATE", recovered["claim"]["claim_token"])
        completed = self.repo.service.complete(
            recovered["claim"]["claim_token"], "implemented", "recovered"
        )
        self.assertEqual(completed["status"], "done")

    def test_approval_is_revision_claim_user_and_time_bound(self) -> None:
        claimed = self.claim()
        approval = self.approve()
        self.repo.service.pulse(claimed["claim"]["claim_token"])
        with self.assertRaises(TodoError) as stale_revision:
            self.repo.service.live_recovery_override("A", approval["approval_token"], "fi_new")
        self.assertEqual(stale_revision.exception.code, "stale_approval")

        self.repo.service.release(claimed["claim"]["claim_token"], "planned", "retry")
        self.claim()
        expired = self.approve()
        with mock.patch("todo_orchestrator.claims.utc_now", return_value="9999-01-01T00:00:00Z"):
            with self.assertRaises(TodoError) as stale_time:
                self.repo.service.live_recovery_override("A", expired["approval_token"], "fi_new")
        self.assertEqual(stale_time.exception.code, "stale_approval")

        # A token created for this repository/project is absent in another authority.
        foreign = V2Repo()
        self.addCleanup(foreign.close)
        foreign.apply(base_plan([safe_task("A", "src/a")]))
        foreign.service.continue_work(
            task_id="A", owner_system="coding-workflow", owner_instance_id="fi_foreign"
        )
        with self.assertRaises(TodoError) as mismatch:
            foreign.service.live_recovery_override("A", expired["approval_token"], "fi_new")
        self.assertEqual(mismatch.exception.code, "override_requires_permission")

        fresh = V2Repo()
        self.addCleanup(fresh.close)
        fresh.apply(base_plan([safe_task("A", "src/a")]))
        fresh.service.continue_work(
            task_id="A", owner_system="coding-workflow", owner_instance_id="fi_prior"
        )
        bound = fresh.service.live_recovery_approve("A", "uid bound", 300)
        with mock.patch("todo_orchestrator.claims.os.getuid", return_value=999999):
            with self.assertRaises(TodoError) as wrong_user:
                fresh.service.live_recovery_override("A", bound["approval_token"], "fi_new")
        self.assertEqual(wrong_user.exception.code, "approval_mismatch")

    def test_attached_child_resource_gate_and_acceptance_block_recovery(self) -> None:
        cases = (
            ("child", "active_child_execution"),
            ("resource", "active_resource_lease"),
            ("gate", "active_gate_execution"),
            ("acceptance", "active_child_execution"),
            ("background", "active_background_or_cuda_campaign"),
        )
        for kind, expected in cases:
            with self.subTest(kind=kind):
                repo = V2Repo()
                self.addCleanup(repo.close)
                repo.apply(base_plan([safe_task("A", "src/a", gates=[{
                    "id": "A-GATE", "type": "command", "argv": ["python", "-c", "pass"],
                    "required": True,
                }])]))
                claimed = repo.service.continue_work(
                    task_id="A", owner_system="coding-workflow", owner_instance_id="fi_prior"
                )
                claim_id = claimed["claim"]["claim_id"]

                def attach(conn, revision):
                    if kind in {"child", "acceptance"}:
                        state = "running" if kind == "child" else "ready_for_acceptance"
                        conn.execute(
                            "INSERT INTO child_executions(id,parent_claim_id,task_id,objective,state,created_at) "
                            "VALUES(?,?,?,?,?,?)",
                            (f"child-{kind}", claim_id, "A", "bounded", state, utc_now()),
                        )
                    elif kind == "resource":
                        conn.execute("INSERT INTO resource_classes(id) VALUES('gpu')")
                        conn.execute("INSERT INTO resource_instances(id,class_id) VALUES('gpu0','gpu')")
                        conn.execute(
                            "INSERT INTO resource_leases(id,instance_id,claim_id,session_id,token_hash,state,hostname,acquired_at,heartbeat_at,expires_at) "
                            "SELECT 'lease','gpu0',c.id,c.session_id,'hash','active','host',?,?,? FROM claims c WHERE c.id=?",
                            (utc_now(), utc_now(), "9999-01-01T00:00:00Z", claim_id),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO events(revision,timestamp,entity_type,entity_id,event_type,payload_json) "
                            "VALUES(?,?,?,?,?,?)",
                            (revision, utc_now(), "gate", "A-GATE", "gate.started", "{}"),
                        )
                    return {"task_id": "A"}

                if kind == "background":
                    BackgroundStore(repo.root).enqueue({
                        "kind": "cuda-benchmark", "argv": ["true"], "cwd": str(repo.root),
                        "task_id": "A", "dedup_key": f"background-{kind}",
                    })
                elif kind == "gate":
                    # Use the service transaction's own event as the latest gate event.
                    repo.service.db.mutate(
                        actor_session_id=None, entity_type="gate", entity_id="A-GATE",
                        event_type="gate.started", payload={}, operation=lambda conn, revision: {"task_id": "A"},
                    )
                else:
                    repo.service.db.mutate(
                        actor_session_id=None, entity_type="test", entity_id=kind,
                        event_type=f"test.{kind}", payload={}, operation=attach,
                    )
                report = repo.service.live_recovery_inspect("A")
                self.assertFalse(report["eligible"])
                self.assertIn(expected, report["blockers"])

    def test_concurrent_consumers_create_exactly_one_replacement(self) -> None:
        self.claim()
        approval = self.approve()
        token = approval["approval_token"]
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def attempt(instance: str) -> None:
            service = type(self.repo.service)(self.repo.root)
            barrier.wait(timeout=5)
            try:
                service.live_recovery_override("A", token, instance)
                outcomes.append("success")
            except TodoError as error:
                outcomes.append(error.code)

        threads = [threading.Thread(target=attempt, args=(f"fi_{index}",)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(outcomes.count("success"), 1)
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(set(outcomes) <= {"success", "approval_consumed", "stale_approval"})
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM claims WHERE task_id='A' AND state='active'"
            ).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
