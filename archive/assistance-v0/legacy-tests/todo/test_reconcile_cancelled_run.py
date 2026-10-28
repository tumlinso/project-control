from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.todo.test_workflow_workspaces import WorkflowWorkspaceTests, git
from todo_orchestrator.config import utc_now
from todo_orchestrator.models import TodoError
from todo_orchestrator.sessions import create_session
from todo_orchestrator.workflow.reconcile_cancelled_run import reconcile_cancelled_run_workspaces
from todo_orchestrator.workflow.service import repository_identity


class CancelledRunWorkspaceReconciliationTests(unittest.TestCase):
    setUp = WorkflowWorkspaceTests.setUp
    tearDown = WorkflowWorkspaceTests.tearDown

    def change(self, sql: str, values: tuple[object, ...] = ()) -> None:
        self.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="RUN",
            event_type="fixture", payload={},
            operation=lambda conn, revision: (conn.execute(sql, values), {})[1],
        )

    def _fixture(self, *, merge_all: bool = True) -> dict[str, str]:
        git(self.repo, "branch", "-M", "main")
        result: dict[str, str] = {}
        identity = repository_identity(self.repo, "project-1")
        self.service.repository_identity_resolver = lambda root: repository_identity(root, "project-1")
        for lane, branch in (("PRODUCER", "nf1-producer"), ("PRODUCER2", "nf1-producer2")):
            workspace = self.service.create_workspace(
                repository_root=self.repo, repository_identity=identity, run_id="RUN",
                lane_id=lane, mode="isolated_merge", base_commit=self.base,
                worktree_path=self.managed / lane.lower(), branch=branch, integration_task_id="INTEGRATE",
            )
            path = Path(str(workspace["worktree_path"]))
            source = path / f"{lane.lower()}.txt"
            source.write_text(f"{lane} source\n", encoding="utf-8")
            git(path, "add", source.name)
            git(path, "commit", "-qm", f"{lane} source")
            head = git(path, "rev-parse", "HEAD")
            result[str(workspace["workspace_id"])] = head
            if merge_all or lane == "PRODUCER":
                git(self.repo, "merge", "--no-ff", "--no-edit", head)

        # A dirty canonical checkout is allowed; its HEAD and main ref remain
        # the accepted commit, and this untracked file is left untouched.
        (self.repo / "canonical-user-file.txt").write_text("preserve\n", encoding="utf-8")
        with self.db.read() as conn:
            task_ids = [str(row[0]) for row in conn.execute(
                "SELECT DISTINCT lt.task_id FROM workflow_lane_tasks lt JOIN workflow_lanes l ON l.id=lt.lane_id WHERE l.run_id='RUN'"
            )]
        marks = ",".join("?" for _ in task_ids)
        now = utc_now()
        self.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="fixture_cancelled", payload={},
            operation=lambda conn, revision: (
                conn.execute("UPDATE workflow_runs SET status='cancelled',updated_at=?,completed_at=?,revision=? WHERE id='RUN'", (now, now, revision)),
                conn.execute("UPDATE workflow_lane_tasks SET state='completed',completed_at=?,revision=? WHERE lane_id IN (SELECT id FROM workflow_lanes WHERE run_id='RUN')", (now, revision)),
                conn.execute("UPDATE tasks SET status='done',result='done',updated_at=?,revision=? WHERE id IN (" + marks + ")", (now, revision, *task_ids)),
                conn.execute("UPDATE workflow_lanes SET state=CASE WHEN id='READER' THEN 'cancelled' ELSE 'ready' END,updated_at=?,revision=? WHERE run_id='RUN'", (now, revision)),
                {},
            )[-1],
        )
        return result

    def request(self, heads: dict[str, str], *, revision: int | None = None) -> dict[str, object]:
        return {
            "run_id": "RUN",
            "expected_revision": self.db.revision() if revision is None else revision,
            "accepted_main_commit": git(self.repo, "rev-parse", "refs/heads/main"),
            "workspaces": heads,
            "reason": "verify already merged cancelled-run workspaces",
        }

    def reconcile(self, request: dict[str, object], *, apply: bool = False) -> dict[str, object]:
        project_uuid = "project-1"
        return reconcile_cancelled_run_workspaces(
            self.db, repository_root=self.repo, managed_root=self.managed,
            repository_identity=repository_identity(self.repo, project_uuid), project_uuid=project_uuid,
            request=request, apply=apply,
        )

    def assert_code(self, code: str, action) -> None:
        with self.assertRaises(TodoError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_preview_then_apply_closes_lanes_and_preserves_task_rows(self):
        heads = self._fixture()
        self.change("UPDATE workflow_workspaces SET merge_result_json=? WHERE id=?", (json.dumps({"old": "receipt"}), next(iter(heads))))
        with self.db.read() as conn:
            tasks_before = [dict(row) for row in conn.execute("SELECT * FROM tasks ORDER BY id")]
        revision_before = self.db.revision()
        request = self.request(heads)
        preview = self.reconcile(request)
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(self.db.revision(), revision_before)
        self.assertEqual(len(preview["workspaces"]), 2)
        self.assertEqual({row["new_state"] for row in preview["lane_state_changes"]}, {"closed"})
        result = self.reconcile(request, apply=True)
        self.assertEqual(result["status"], "reconciled")
        self.assertEqual(result["revision"], revision_before + 1)
        with self.db.read() as conn:
            tasks_after = [dict(row) for row in conn.execute("SELECT * FROM tasks ORDER BY id")]
        self.assertEqual(tasks_after, tasks_before)
        with self.db.read() as conn:
            states = {str(row["id"]): str(row["state"]) for row in conn.execute("SELECT id,state FROM workflow_lanes WHERE run_id='RUN'")}
            workspaces = {str(row["id"]): row for row in conn.execute("SELECT * FROM workflow_workspaces WHERE run_id='RUN'")}
            event = dict(conn.execute("SELECT * FROM events ORDER BY revision DESC LIMIT 1").fetchone())
        self.assertTrue(all(state == "closed" for lane, state in states.items() if lane != "READER"))
        self.assertEqual(states["READER"], "cancelled")
        self.assertTrue(all(row["state"] == "integrated" for row in workspaces.values()))
        preserved = json.loads(workspaces[next(iter(heads))]["merge_result_json"])
        self.assertEqual(preserved["old"], "receipt")
        self.assertEqual(preserved["cancelled_run_reconciliation"]["proof"]["kind"], "source_included")
        self.assertEqual(event["event_type"], "workflow.cancelled_run_reconciliation.applied")
        event_payload = json.loads(event["payload_json"])
        self.assertEqual(len(event_payload["lane_state_changes"]), len(states) - 1)
        self.assertTrue((self.repo / "canonical-user-file.txt").exists())

    def test_rejects_non_cancelled_run(self):
        heads = self._fixture()
        self.change("UPDATE workflow_runs SET status='active' WHERE id='RUN'")
        self.assert_code("cancelled_run_reconciliation_run_not_cancelled", lambda: self.reconcile(self.request(heads)))

    def test_rejects_nonterminal_membership(self):
        heads = self._fixture()
        self.change("UPDATE workflow_lane_tasks SET state='queued' WHERE lane_id='PRODUCER'")
        self.assert_code("cancelled_run_reconciliation_membership_active", lambda: self.reconcile(self.request(heads)))

    def test_rejects_stale_revision(self):
        heads = self._fixture()
        request = self.request(heads)
        self.change("UPDATE workflow_lanes SET context_cursor=context_cursor+1 WHERE id='COORD'")
        self.assert_code("cancelled_run_reconciliation_stale_revision", lambda: self.reconcile(request))

    def test_rejects_dirty_selected_worktree_and_rechecks_at_apply(self):
        heads = self._fixture()
        request = self.request(heads)
        workspace_id = next(iter(heads))
        with self.db.read() as conn:
            target = Path(str(conn.execute("SELECT worktree_path FROM workflow_workspaces WHERE id=?", (workspace_id,)).fetchone()[0]))
        self.reconcile(request)
        (target / "unknown.txt").write_text("preserve\n", encoding="utf-8")
        self.assert_code("cancelled_run_reconciliation_worktree_dirty", lambda: self.reconcile(request, apply=True))
        self.assertTrue((target / "unknown.txt").exists())

    def test_rejects_unmerged_head(self):
        heads = self._fixture(merge_all=False)
        self.assert_code("cancelled_run_reconciliation_head_not_merged", lambda: self.reconcile(self.request(heads)))

    def test_rejects_branch_authority_mismatch(self):
        heads = self._fixture()
        self.change("UPDATE workflow_workspaces SET branch='unexpected' WHERE id=?", (next(iter(heads)),))
        self.assert_code("cancelled_run_reconciliation_branch_mismatch", lambda: self.reconcile(self.request(heads)))

    def test_rejects_active_claim(self):
        heads = self._fixture()
        def add_claim(conn, revision):
            session, _ = create_session(conn, self.repo, {"command": "test"})
            now = utc_now()
            conn.execute(
                "INSERT INTO claims(id,task_id,session_id,token_hash,state,created_at,heartbeat_at,expires_at,baseline_revision) "
                "VALUES('ACTIVE','IMPL',?,'claim-hash','active',?,?,?,?)",
                (session["agent_id"], now, now, now, revision),
            )
            return {}
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="fixture_owner", payload={}, operation=add_claim)
        self.assert_code("cancelled_run_reconciliation_work_active", lambda: self.reconcile(self.request(heads)))

    def test_rejects_unresolved_patch_artifact(self):
        heads = self._fixture()
        workspace_id, head = next(iter(heads.items()))
        self.change(
            "INSERT INTO workflow_patch_artifacts(id,workspace_id,task_id,kind,artifact_ref,content_hash,base_commit,created_at,state) "
            "VALUES('PENDING',?,'IMPL','commit',?,?,?,?,'pending')",
            (workspace_id, head, "a" * 64, self.base, utc_now()),
        )
        self.assert_code("cancelled_run_reconciliation_work_active", lambda: self.reconcile(self.request(heads)))

    def test_rejects_active_named_lock_lease(self):
        heads = self._fixture()
        def add_lock(conn, revision):
            session, _ = create_session(conn, self.repo, {"command": "test"})
            now = utc_now()
            conn.execute("INSERT INTO named_locks(name,capacity) VALUES('nf1-lock',1)")
            conn.execute(
                "INSERT INTO claims(id,task_id,session_id,token_hash,state,created_at,heartbeat_at,expires_at,baseline_revision) "
                "VALUES('RELEASED','IMPL',?,'released-hash','released',?,?,?,?)",
                (session["agent_id"], now, now, now, revision),
            )
            conn.execute(
                "INSERT INTO lock_leases(id,lock_name,claim_id,session_id,token_hash,state,acquired_at,heartbeat_at,expires_at) "
                "VALUES('LOCK-1','nf1-lock','RELEASED',?,'lease-hash','active',?,?,?)",
                (session["agent_id"], now, now, now),
            )
            return {}
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="fixture_lock", payload={}, operation=add_lock)
        self.assert_code("cancelled_run_reconciliation_work_active", lambda: self.reconcile(self.request(heads)))

    def test_rejects_active_child_scope_lease(self):
        heads = self._fixture()
        def add_child_scope(conn, revision):
            session, _ = create_session(conn, self.repo, {"command": "test"})
            now = utc_now()
            conn.execute(
                "INSERT INTO claims(id,task_id,session_id,token_hash,state,created_at,heartbeat_at,expires_at,baseline_revision) "
                "VALUES('PARENT','IMPL',?,'parent-hash','released',?,?,?,?)",
                (session["agent_id"], now, now, now, revision),
            )
            conn.execute(
                "INSERT INTO child_executions(id,parent_claim_id,task_id,objective,state,created_at) "
                "VALUES('CHILD','PARENT','IMPL','finished child with stale scope','failed',?)", (now,),
            )
            conn.execute("INSERT INTO child_scope_leases(child_execution_id,path,state,acquired_at) VALUES('CHILD','src/owned','active',?)", (now,))
            return {}
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="fixture_child_scope", payload={}, operation=add_child_scope)
        self.assert_code("cancelled_run_reconciliation_work_active", lambda: self.reconcile(self.request(heads)))

    def test_rejects_branch_ref_moved_away_from_explicit_head(self):
        heads = self._fixture()
        workspace_id = next(iter(heads))
        with self.db.read() as conn:
            branch = str(conn.execute("SELECT branch FROM workflow_workspaces WHERE id=?", (workspace_id,)).fetchone()[0])
        git(self.repo, "update-ref", f"refs/heads/{branch}", self.base)
        self.assert_code("cancelled_run_reconciliation_branch_mismatch", lambda: self.reconcile(self.request(heads)))

    def test_ignored_build_output_is_allowed_and_preserved(self):
        heads = self._fixture()
        workspace_id = next(iter(heads))
        with self.db.read() as conn:
            target = Path(str(conn.execute("SELECT worktree_path FROM workflow_workspaces WHERE id=?", (workspace_id,)).fetchone()[0]))
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.write_text(exclude.read_text(encoding="utf-8") + "\nnf1-ignored-build/\n", encoding="utf-8")
        ignored = target / "nf1-ignored-build" / "run.log"
        ignored.parent.mkdir()
        ignored.write_text("preserve ignored build output\n", encoding="utf-8")
        request = self.request(heads)
        self.assertEqual(self.reconcile(request)["status"], "ready")
        self.reconcile(request, apply=True)
        self.assertTrue(ignored.exists())

    def test_rejects_unsupported_nonterminal_workspace_state(self):
        heads = self._fixture()
        self.change("UPDATE workflow_workspaces SET state='queued' WHERE id=?", (next(iter(heads)),))
        self.assert_code(
            "cancelled_run_reconciliation_workspace_state_unsupported",
            lambda: self.reconcile(self.request(heads)),
        )

    def test_rejects_tracked_worktree_change(self):
        heads = self._fixture()
        workspace_id = next(iter(heads))
        with self.db.read() as conn:
            target = Path(str(conn.execute("SELECT worktree_path FROM workflow_workspaces WHERE id=?", (workspace_id,)).fetchone()[0]))
        (target / "shared.txt").write_text("tracked local change\n", encoding="utf-8")
        self.assert_code("cancelled_run_reconciliation_worktree_dirty", lambda: self.reconcile(self.request(heads)))
