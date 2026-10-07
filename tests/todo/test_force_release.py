from __future__ import annotations

import io
import json
import os
import socket
import unittest
from unittest import mock

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.background.store import BackgroundStore
from todo_orchestrator.cli import build_parser
from todo_orchestrator.config import utc_now
from todo_orchestrator.models import TodoError
from todo_orchestrator.resources import process_start


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class ForceReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        task = safe_task(
            "A",
            "src/a",
            claim_locks=["task-lock"],
            resource_requests=[{
                "id": "A-SLOT", "phase": "claim", "selector": "slot:any",
                "amount": 1, "required": True,
            }],
            gates=[{
                "id": "A-GATE", "type": "command", "argv": ["python", "-c", "pass"],
                "required": True,
            }],
        )
        self.repo.apply(base_plan(
            [task, safe_task("B", "src/b")],
            locks=[{"name": "task-lock"}],
            resource_classes=[{"id": "slot", "instances": [{"id": "slot:0"}]}],
        ))

    def tearDown(self) -> None:
        self.repo.close()

    def claim(self) -> dict[str, object]:
        return self.repo.service.continue_work(task_id="A")

    def approve(
        self,
        reason: str = "lost raw claim token",
        *,
        acknowledge_dirty: bool = False,
    ) -> dict[str, object]:
        return self.repo.service.force_release_approve(
            "A", reason, 300, acknowledge_dirty,
        )

    def test_success_releases_bookkeeping_and_task_is_claimable_again(self) -> None:
        original = self.claim()
        original_token = original["claim"]["claim_token"]
        original_fingerprint = original["claim"]["claim_fingerprint"]
        report = self.repo.service.force_release_inspect("A")
        self.assertTrue(report["eligible"])
        self.assertIsNone(report["owner_system"])
        self.assertEqual(report["claim_fingerprint"], original_fingerprint)

        approval = self.approve("owner clearing accidental bookkeeping claim")
        secret = approval["approval_token"]
        released = self.repo.service.force_release("A", secret)
        self.assertEqual(released["claim_state"], "force_released")
        self.assertEqual(released["status"], "planned")
        with self.repo.service.db.read() as conn:
            claim = dict(conn.execute("SELECT * FROM claims WHERE task_id='A'").fetchone())
            self.assertEqual(claim["state"], "force_released")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM lock_leases WHERE claim_id=? AND state='active'",
                (claim["id"],),
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM resource_leases WHERE claim_id=? AND state='active'",
                (claim["id"],),
            ).fetchone()[0], 0)
            audit = dict(conn.execute("SELECT * FROM live_recovery_audit").fetchone())
            self.assertEqual(audit["reason"], "owner clearing accidental bookkeeping claim")
            self.assertEqual(audit["prior_claim_fingerprint"], original_fingerprint)
            self.assertEqual(audit["disposition"], "owner_force_released")
            persisted = json.dumps({
                "audit": audit,
                "events": [dict(row) for row in conn.execute("SELECT * FROM events")],
            }, sort_keys=True)
            self.assertNotIn(secret, persisted)

        with self.assertRaises(TodoError) as old:
            self.repo.service.pulse(original_token)
        self.assertEqual(old.exception.code, "invalid_claim_token")
        with self.assertRaises(TodoError) as consumed:
            self.repo.service.force_release("A", secret)
        self.assertEqual(consumed.exception.code, "approval_consumed")
        reclaimed = self.repo.service.continue_work(task_id="A")
        self.assertEqual(reclaimed["task"]["id"], "A")
        self.assertNotEqual(reclaimed["claim"]["claim_token"], original_token)

    def test_force_release_retires_first_class_dispatch_and_requeues_lane(self) -> None:
        original = self.claim()

        def seed_workflow(conn, revision):
            conn.execute("INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('RUN','A','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lanes(id,run_id,role,state,created_at,updated_at,revision) VALUES('LANE','RUN','implementer','active','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,activated_at,revision) VALUES('LANE',0,'A','active','now','now',?)", (revision,))
            conn.execute(
                "INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,state,context_version,heartbeat_at,created_at,revision) "
                "VALUES('DISPATCH','LANE',?,?,'active',1,'now','now',?)",
                (original["session"]["agent_id"], original["claim"]["claim_id"], revision),
            )
            conn.execute(
                "INSERT INTO workflow_capabilities(id,token_hash,capability_class,project_uuid,repository_identity,session_id,claim_id,run_id,lane_id,role,task_id,allowed_operations_json,state,created_at,expires_at) "
                "VALUES('CAP','hash','first_class',?,'repo',?,?,'RUN','LANE','implementer','A','[]','active','now','2999-01-01T00:00:00Z')",
                (self.repo.service.project["project_uuid"], original["session"]["agent_id"], original["claim"]["claim_id"]),
            )

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="A",
            event_type="fixture.workflow", payload={}, operation=seed_workflow,
        )
        approval = self.approve("halted first-class worker")
        released = self.repo.service.force_release("A", approval["approval_token"])
        self.assertEqual(released["retired_dispatch_ids"], ["DISPATCH"])
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM workflow_dispatches WHERE id='DISPATCH'").fetchone()[0], "released")
            self.assertEqual(conn.execute("SELECT state FROM workflow_lanes WHERE id='LANE'").fetchone()[0], "ready")
            self.assertEqual(conn.execute("SELECT state FROM workflow_lane_tasks WHERE lane_id='LANE'").fetchone()[0], "queued")
            self.assertEqual(conn.execute("SELECT state FROM workflow_capabilities WHERE id='CAP'").fetchone()[0], "retired")

    def test_cli_inspect_and_force_release_use_environment_capability(self) -> None:
        self.claim()
        process, inspected = self.repo.run("recover", "force-release-inspect", "A")
        self.assertEqual(process.returncode, 0)
        self.assertTrue(inspected["data"]["eligible"])
        approval = self.approve("CLI environment capability")
        with mock.patch.dict(
            os.environ,
            {"TODO_FORCE_RELEASE_APPROVAL": approval["approval_token"]},
        ):
            result, released = self.repo.run("recover", "force-release", "A")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(released["data"]["claim_state"], "force_released")
        self.assertNotIn(approval["approval_token"], " ".join(result.args))

    def test_dogfood_lost_non_facade_token_manual_owner_flow(self) -> None:
        original = self.claim()
        lost_token = original["claim"]["claim_token"]
        args = build_parser().parse_args([
            "recover", "force-release-approve", "A",
            "--reason", "disposable dogfood lost-token claim",
            "--repo-root", str(self.repo.root),
        ])
        with (
            mock.patch("sys.stdin", TtyStringIO()),
            mock.patch("sys.stdout", TtyStringIO()),
            mock.patch("sys.stderr", TtyStringIO()),
            mock.patch("builtins.input", return_value="A"),
        ):
            approval = args.handler(args)
        with mock.patch.dict(
            os.environ,
            {"TODO_FORCE_RELEASE_APPROVAL": approval["approval_token"]},
        ):
            result, released = self.repo.run("recover", "force-release", "A")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(released["data"]["status"], "planned")
        with self.assertRaises(TodoError):
            self.repo.service.pulse(lost_token)
        reclaimed = self.repo.service.continue_work(task_id="A")
        self.assertEqual(reclaimed["task"]["id"], "A")

    def test_interactive_approval_requires_ttys_exact_task_and_has_no_yes(self) -> None:
        self.claim()
        result, envelope = self.repo.run(
            "recover", "force-release-approve", "A", "--reason", "noninteractive model attempt"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(envelope["code"], "manual_approval_terminal_required")

        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([
                "recover", "force-release-approve", "A", "--reason", "bypass",
                "--yes", "--repo-root", str(self.repo.root),
            ])
        args = parser.parse_args([
            "recover", "force-release-approve", "A", "--reason", "lost token",
            "--repo-root", str(self.repo.root),
        ])
        stdin = TtyStringIO()
        stdout = TtyStringIO()
        stderr = TtyStringIO()
        with (
            mock.patch("sys.stdin", stdin),
            mock.patch("sys.stdout", stdout),
            mock.patch("sys.stderr", stderr),
            mock.patch("builtins.input", return_value="WRONG"),
        ):
            with self.assertRaises(TodoError) as canceled:
                args.handler(args)
        self.assertEqual(canceled.exception.code, "manual_approval_canceled")
        self.assertIn("Project UUID:", stderr.getvalue())
        self.assertIn("Consequences:", stderr.getvalue())

        with (
            mock.patch("sys.stdin", TtyStringIO()),
            mock.patch("sys.stdout", TtyStringIO()),
            mock.patch("sys.stderr", TtyStringIO()),
            mock.patch("builtins.input", return_value="A"),
        ):
            approved = args.handler(args)
        self.assertTrue(approved["approval_token"].startswith("tof_"))

    def test_token_is_repository_project_task_uid_revision_fingerprint_and_time_bound(self) -> None:
        self.claim()
        forged = "tof_model_supplied_fake"
        with self.assertRaises(TodoError) as fake:
            self.repo.service.force_release("A", forged)
        self.assertEqual(fake.exception.code, "force_release_requires_permission")

        approval = self.approve()
        with self.assertRaises(TodoError) as wrong_task:
            self.repo.service.force_release("B", approval["approval_token"])
        self.assertEqual(wrong_task.exception.code, "approval_mismatch")
        original_uuid = self.repo.service.project["project_uuid"]
        self.repo.service.project["project_uuid"] = "wrong-project"
        try:
            with self.assertRaises(TodoError) as wrong_project:
                self.repo.service.force_release("A", approval["approval_token"])
            self.assertEqual(wrong_project.exception.code, "approval_mismatch")
        finally:
            self.repo.service.project["project_uuid"] = original_uuid
        with mock.patch("todo_orchestrator.claims.os.getuid", return_value=999999):
            with self.assertRaises(TodoError) as wrong_uid:
                self.repo.service.force_release("A", approval["approval_token"])
        self.assertEqual(wrong_uid.exception.code, "approval_mismatch")

        foreign = V2Repo()
        self.addCleanup(foreign.close)
        foreign.apply(base_plan([safe_task("A", "src/a")]))
        foreign.service.continue_work(task_id="A")
        with self.assertRaises(TodoError) as wrong_repo:
            foreign.service.force_release("A", approval["approval_token"])
        self.assertEqual(wrong_repo.exception.code, "force_release_requires_permission")

        self.repo.service.pulse(self.repo.service.continue_work(task_id="B")["claim"]["claim_token"])
        with self.assertRaises(TodoError) as changed_revision:
            self.repo.service.force_release("A", approval["approval_token"])
        self.assertEqual(changed_revision.exception.code, "stale_approval")

        revision_repo = V2Repo()
        self.addCleanup(revision_repo.close)
        revision_repo.apply(base_plan([safe_task("A", "src/a")]))
        revision_repo.service.continue_work(task_id="A")
        expires = revision_repo.service.force_release_approve("A", "expires", 300)
        with mock.patch("todo_orchestrator.claims.utc_now", return_value="9999-01-01T00:00:00Z"):
            with self.assertRaises(TodoError) as expired:
                revision_repo.service.force_release("A", expires["approval_token"])
        self.assertEqual(expired.exception.code, "stale_approval")

        fingerprint_repo = V2Repo()
        self.addCleanup(fingerprint_repo.close)
        fingerprint_repo.apply(base_plan([safe_task("A", "src/a")]))
        fingerprint_repo.service.continue_work(task_id="A")
        changed = fingerprint_repo.service.force_release_approve("A", "fingerprint", 300)
        conn = fingerprint_repo.service.db.connect()
        try:
            conn.execute("UPDATE claims SET heartbeat_at='2099-01-01T00:00:00Z' WHERE task_id='A'")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(TodoError) as stale_fingerprint:
            fingerprint_repo.service.force_release("A", changed["approval_token"])
        self.assertEqual(stale_fingerprint.exception.code, "stale_approval")

    def test_pulse_after_approval_is_stale(self) -> None:
        claim = self.claim()
        approval = self.approve()
        self.repo.service.pulse(claim["claim"]["claim_token"])
        with self.assertRaises(TodoError) as stale:
            self.repo.service.force_release("A", approval["approval_token"])
        self.assertEqual(stale.exception.code, "stale_approval")

    def test_dirty_scope_requires_explicit_acknowledgement_and_is_preserved(self) -> None:
        original = self.claim()
        path = self.repo.root / "src" / "a"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("owner work must survive\n", encoding="utf-8")

        blocked = self.repo.service.force_release_inspect("A")
        self.assertFalse(blocked["eligible"])
        self.assertEqual(blocked["blockers"], ["owned_scope_changed"])
        with self.assertRaises(TodoError) as missing_ack:
            self.approve("preserve owner work")
        self.assertEqual(missing_ack.exception.code, "force_release_blocked")

        acknowledged = self.repo.service.force_release_inspect("A", True)
        self.assertTrue(acknowledged["eligible"])
        self.assertTrue(acknowledged["scope_changed"])
        args = build_parser().parse_args([
            "recover", "force-release-approve", "A",
            "--reason", "preserve owner work", "--acknowledge-dirty",
            "--repo-root", str(self.repo.root),
        ])
        stderr = TtyStringIO()
        with (
            mock.patch("sys.stdin", TtyStringIO()),
            mock.patch("sys.stdout", TtyStringIO()),
            mock.patch("sys.stderr", stderr),
            mock.patch("builtins.input", return_value="A"),
        ):
            approval = args.handler(args)
        self.assertIn("Dirty scope acknowledged: yes", stderr.getvalue())
        self.assertIn("preserve every repository file exactly as-is", stderr.getvalue())
        self.assertTrue(approval["approval_context"]["acknowledge_dirty"])
        released = self.repo.service.force_release("A", approval["approval_token"])
        self.assertTrue(released["acknowledged_dirty_scope"])
        self.assertEqual(path.read_text(encoding="utf-8"), "owner work must survive\n")
        with self.repo.service.db.read() as conn:
            audit = dict(conn.execute("SELECT * FROM live_recovery_audit").fetchone())
            context = json.loads(audit["context_json"])
            self.assertTrue(context["acknowledge_dirty"])
            self.assertEqual(
                context["current_scope_fingerprint"],
                acknowledged["current_scope_fingerprint"],
            )
        with self.assertRaises(TodoError):
            self.repo.service.pulse(original["claim"]["claim_token"])

    def test_dirty_scope_change_after_approval_makes_capability_stale(self) -> None:
        self.claim()
        path = self.repo.root / "src" / "a"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("first\n", encoding="utf-8")
        approval = self.approve("first manifest", acknowledge_dirty=True)
        path.write_text("second\n", encoding="utf-8")
        with self.assertRaises(TodoError) as stale:
            self.repo.service.force_release("A", approval["approval_token"])
        self.assertEqual(stale.exception.code, "stale_approval")

    def test_approval_projection_refresh_does_not_self_invalidate(self) -> None:
        repo = V2Repo()
        self.addCleanup(repo.close)
        task = safe_task("A", "src/a")
        task["scope"]["exclusive_paths"] = [
            ".todo-orchestrator", "src/a", "todo-status.md", "todos.md",
        ]
        repo.apply(base_plan([task]))
        repo.service.continue_work(task_id="A")
        path = repo.root / "src" / "a"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("preserve\n", encoding="utf-8")
        approval = repo.service.force_release_approve(
            "A", "managed projections will refresh", 300, True,
        )
        released = repo.service.force_release("A", approval["approval_token"])
        self.assertTrue(released["acknowledged_dirty_scope"])
        self.assertEqual(path.read_text(encoding="utf-8"), "preserve\n")

    def test_dirty_scope_and_attached_execution_blockers(self) -> None:
        cases = (
            ("child", "active_child_execution"),
            ("acceptance", "active_child_execution"),
            ("gate", "active_gate_execution"),
            ("background", "active_background_or_cuda_campaign"),
            ("external", "active_external_resource_process"),
            ("dirty", "owned_scope_changed"),
        )
        for kind, expected in cases:
            with self.subTest(kind=kind):
                repo = V2Repo()
                self.addCleanup(repo.close)
                repo.apply(base_plan([safe_task("A", "src/a", gates=[{
                    "id": "A-GATE", "type": "command", "argv": ["python", "-c", "pass"],
                    "required": True,
                }])]))
                claimed = repo.service.continue_work(task_id="A")
                claim_id = claimed["claim"]["claim_id"]
                if kind in {"child", "acceptance"}:
                    state = "running" if kind == "child" else "ready_for_acceptance"
                    repo.service.db.mutate(
                        actor_session_id=None, entity_type="test", entity_id=kind,
                        event_type=f"test.{kind}", payload={},
                        operation=lambda conn, revision, state=state: conn.execute(
                            "INSERT INTO child_executions(id,parent_claim_id,task_id,objective,state,created_at) "
                            "VALUES(?,?,?,?,?,?)",
                            (f"child-{kind}", claim_id, "A", "bounded", state, utc_now()),
                        ),
                    )
                elif kind == "gate":
                    repo.service.db.mutate(
                        actor_session_id=None, entity_type="gate", entity_id="A-GATE",
                        event_type="gate.started", payload={}, operation=lambda conn, revision: True,
                    )
                elif kind == "background":
                    BackgroundStore(repo.root).enqueue({
                        "kind": "cuda-benchmark", "argv": ["true"], "cwd": str(repo.root),
                        "task_id": "A", "dedup_key": "force-release-background",
                    })
                elif kind == "external":
                    def attach(conn, revision):
                        conn.execute("INSERT INTO resource_classes(id) VALUES('gpu')")
                        conn.execute("INSERT INTO resource_instances(id,class_id) VALUES('gpu0','gpu')")
                        conn.execute(
                            "INSERT INTO resource_leases(id,instance_id,claim_id,session_id,token_hash,state,hostname,pid,process_start,command_json,acquired_at,heartbeat_at,expires_at) "
                            "SELECT 'lease','gpu0',c.id,c.session_id,'hash','active',?,?,?,?,?,?,? FROM claims c WHERE c.id=?",
                            (socket.gethostname(), os.getpid(), process_start(), '["mutator"]', utc_now(), utc_now(), "9999-01-01T00:00:00Z", claim_id),
                        )
                        return True
                    repo.service.db.mutate(
                        actor_session_id=None, entity_type="test", entity_id=kind,
                        event_type="test.external", payload={}, operation=attach,
                    )
                else:
                    path = repo.root / "src" / "a"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("changed\n", encoding="utf-8")
                report = repo.service.force_release_inspect("A")
                self.assertFalse(report["eligible"])
                self.assertIn(expected, report["blockers"])
                with self.assertRaises(TodoError) as refused:
                    repo.service.force_release_approve("A", "unsafe", 300)
                self.assertEqual(refused.exception.code, "force_release_blocked")

    def test_expired_claim_remains_ordinary_recovery_and_live_override_still_works(self) -> None:
        claim = self.claim()
        conn = self.repo.service.db.connect()
        try:
            conn.execute("UPDATE claims SET expires_at='2000-01-01T00:00:00Z' WHERE task_id='A'")
            conn.commit()
        finally:
            conn.close()
        self.repo.service.status()
        self.repo.service.continue_work(task_id="B")
        with self.assertRaises(TodoError):
            self.repo.service.force_release_inspect("A")
        recovered = self.repo.service.recover("release", "A")
        self.assertTrue(recovered["clean"])
        with self.assertRaises(TodoError):
            self.repo.service.pulse(claim["claim"]["claim_token"])

        facade = V2Repo()
        self.addCleanup(facade.close)
        facade.apply(base_plan([safe_task("A", "src/a")]))
        facade.service.continue_work(
            task_id="A", owner_system="coding-workflow", owner_instance_id="fi_prior"
        )
        approval = facade.service.live_recovery_approve("A", "lost facade handle", 300)
        result = facade.service.live_recovery_override("A", approval["approval_token"], "fi_new")
        self.assertEqual(result["claim"]["owner_instance_id"], "fi_new")


if __name__ == "__main__":
    unittest.main()
