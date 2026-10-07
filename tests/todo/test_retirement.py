from __future__ import annotations

import hashlib
import json
import subprocess
import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.models import TodoError
from todo_orchestrator.authority import logical_authority_fingerprint
from todo_orchestrator.runtime.source import capture_source_identity


class RetirementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        self.repo.apply(base_plan([safe_task("OLD", "src/old"), safe_task("KEEP", "src/keep"), safe_task("NEW", "src/new")]))
        def seed(conn, revision):
            # The v2 fixture supplies its own generic active compatibility
            # lane.  Retire that fixture-only queue so it does not represent a
            # foreign live owner in the supersession-specific tests below.
            conn.execute("UPDATE workflow_lane_tasks SET state='skipped',completed_at='now',revision=? WHERE lane_id='compat-v2-main'", (revision,))
            for run, root in (("OLD-RUN", "OLD"), ("NEW-RUN", "NEW")):
                conn.execute("INSERT INTO workflow_runs(id,root_task_id,status,created_at,updated_at,revision) VALUES(?,?, 'active','now','now',?)", (run, root, revision))
            conn.execute("INSERT INTO workflow_lanes(id,run_id,role,created_at,updated_at,revision) VALUES('OLD-LANE','OLD-RUN','integrator','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('OLD-LANE',0,'OLD','queued','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lanes(id,run_id,role,created_at,updated_at,revision) VALUES('NEW-LANE','NEW-RUN','integrator','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('NEW-LANE',0,'NEW','queued','now',?)", (revision,))
        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="OLD", event_type="fixture.seed", payload={}, operation=seed)

    def tearDown(self) -> None:
        self.repo.close()

    def request(self):
        with self.repo.service.db.read() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id='OLD'").fetchone()
            fingerprint = logical_authority_fingerprint(conn)
        return {
            "source_run_id": "OLD-RUN", "successor_run_id": "NEW-RUN",
            "expected_project_uuid": self.repo.service.project["project_uuid"],
            "expected_revision": self.repo.service.db.revision(), "expected_fingerprint": fingerprint,
            "expected_tasks": {"OLD": {"status": row["status"], "version": row["version"], "revision": row["revision"],
                "row_digest": hashlib.sha256(json.dumps({field: row[field] for field in ("id", "parent_id", "kind", "title", "objective", "priority", "parallel_policy", "next_action", "notes", "status", "result", "revision", "completion_revision", "completion_commit") if field in row.keys()}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}},
            "dispositions": {"OLD": "superseded"}, "reason": "replace the obsolete run",
        }

    def preserved_source_identity(self):
        (self.repo.root / ".gitignore").write_text("runtime/\nplan.json\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo.root), "add", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(self.repo.root), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"], check=True)
        (self.repo.root / "retained.txt").write_bytes(b"preserved dirty bytes\n")
        return capture_source_identity(self.repo.root)

    def test_exact_retirement_preserves_unrelated_history_and_is_idempotent(self):
        # A prepare can be repeated from separate read connections without a
        # WAL/page-layout false conflict, then the exact request applies.
        self.assertEqual(self.request()["expected_fingerprint"], self.request()["expected_fingerprint"])
        request = self.request()
        result = self.repo.service.retire_run_batch(request)
        self.assertEqual(result["status"], "retired")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id='OLD'").fetchone()[0], "superseded")
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id='KEEP'").fetchone()[0], "planned")
            self.assertEqual(conn.execute("SELECT state FROM workflow_lane_tasks WHERE task_id='OLD'").fetchone()[0], "skipped")
            self.assertEqual(conn.execute("SELECT status FROM workflow_runs WHERE id='OLD-RUN'").fetchone()[0], "cancelled")
            self.assertEqual(conn.execute("SELECT status FROM workflow_runs WHERE id='NEW-RUN'").fetchone()[0], "active")
            self.assertEqual(result["intended_run_id"], "NEW-RUN")
        revision = self.repo.service.db.revision()
        repeat = self.repo.service.retire_run_batch(request)
        self.assertEqual((repeat["status"], repeat["changed"], self.repo.service.db.revision()), ("already_retired", False, revision))

    def test_semantic_change_after_prepare_is_rejected(self):
        request = self.request()
        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="KEEP", event_type="fixture.changed", payload={},
            operation=lambda conn, revision: conn.execute("UPDATE tasks SET notes='changed',revision=? WHERE id='KEEP'", (revision,)),
        )
        with self.assertRaises(TodoError) as stale:
            self.repo.service.retire_run_batch(request)
        self.assertEqual(stale.exception.code, "retirement_authority_stale")

    def test_active_claim_and_external_consumer_are_rejected_without_partial_change(self):
        claim = self.repo.service.continue_work(task_id="OLD")
        request = self.request()
        with self.assertRaises(TodoError) as blocked:
            self.repo.service.retire_run_batch(request)
        self.assertEqual(blocked.exception.code, "retirement_not_quiescent")
        self.repo.service.release(claim["claim"]["claim_token"], "no_change_required", "fixture release")
        def consumer(conn, revision):
            conn.execute("INSERT INTO task_dependencies(task_id,type,prerequisite_task_id,condition_json) VALUES('KEEP','task','OLD','{}')")
        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="KEEP", event_type="fixture.consumer", payload={}, operation=consumer)
        request = self.request()
        with self.assertRaises(TodoError) as blocked:
            self.repo.service.retire_run_batch(request)
        self.assertEqual(blocked.exception.code, "retirement_external_consumers")

    def test_pending_checkpoint_dependency_of_external_task_refuses_retirement(self):
        def consumer(conn, revision):
            conn.execute("INSERT INTO checkpoints(id,task_id,title,state,metadata_json,revision) VALUES('OLD-READY','OLD','ready','pending','{}',?)", (revision,))
            conn.execute("INSERT INTO task_dependencies(task_id,type,checkpoint_id,condition_json) VALUES('KEEP','checkpoint','OLD-READY','{}')")
        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="KEEP", event_type="fixture.checkpoint_consumer", payload={}, operation=consumer)
        with self.assertRaises(TodoError) as blocked:
            self.repo.service.retire_run_batch(self.request())
        self.assertEqual(blocked.exception.code, "retirement_external_consumers")
        self.assertEqual(blocked.exception.details[0]["kind"], "typed_checkpoint_consumer")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id='OLD'").fetchone()[0], "planned")

    def test_omitted_unfinished_source_member_refuses_whole_run_cancellation(self):
        def add_source_member(conn, revision):
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('OLD-LANE',1,'KEEP','queued','now',?)", (revision,))
        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="KEEP", event_type="fixture.source_member", payload={}, operation=add_source_member)

        with self.assertRaises(TodoError) as blocked:
            self.repo.service.retire_run_batch(self.request())
        self.assertEqual(blocked.exception.code, "retirement_source_membership_incomplete")
        self.assertEqual(blocked.exception.details["omitted_task_ids"], ["KEEP"])
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT status FROM workflow_runs WHERE id='OLD-RUN'").fetchone()[0], "active")
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id='OLD'").fetchone()[0], "planned")

    def test_foreign_active_membership_refuses_without_mutating_foreign_queue(self):
        def add_foreign_member(conn, revision):
            conn.execute("INSERT INTO workflow_runs(id,root_task_id,status,created_at,updated_at,revision) VALUES('FOREIGN-RUN','OLD','active','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lanes(id,run_id,role,state,created_at,updated_at,revision) VALUES('FOREIGN-LANE','FOREIGN-RUN','implementer','active','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,activated_at,revision) VALUES('FOREIGN-LANE',0,'OLD','active','now','now',?)", (revision,))
        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="OLD", event_type="fixture.foreign_member", payload={}, operation=add_foreign_member)

        with self.assertRaises(TodoError) as blocked:
            self.repo.service.retire_run_batch(self.request())
        self.assertEqual(blocked.exception.code, "retirement_foreign_membership")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM workflow_lane_tasks WHERE lane_id='FOREIGN-LANE'").fetchone()[0], "active")
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id='OLD'").fetchone()[0], "planned")

    def test_foreign_attention_run_membership_is_equally_protected(self):
        def add_attention_member(conn, revision):
            conn.execute("INSERT INTO workflow_runs(id,root_task_id,status,created_at,updated_at,revision) VALUES('FOREIGN-RUN','OLD','attention_required','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lanes(id,run_id,role,state,created_at,updated_at,revision) VALUES('FOREIGN-LANE','FOREIGN-RUN','implementer','attention_required','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('FOREIGN-LANE',0,'OLD','queued','now',?)", (revision,))
        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="OLD", event_type="fixture.attention_member", payload={}, operation=add_attention_member)

        with self.assertRaises(TodoError) as blocked:
            self.repo.service.retire_run_batch(self.request())
        self.assertEqual(blocked.exception.code, "retirement_foreign_membership")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM workflow_lane_tasks WHERE lane_id='FOREIGN-LANE'").fetchone()[0], "queued")
            self.assertEqual(conn.execute("SELECT status FROM workflow_runs WHERE id='OLD-RUN'").fetchone()[0], "active")

    def test_completed_source_history_is_preserved_and_external_interface_consumer_is_concrete(self):
        def history_and_interface_consumer(conn, revision):
            conn.execute("UPDATE tasks SET status='done',result='done',revision=? WHERE id='KEEP'", (revision,))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,completed_at,revision) VALUES('OLD-LANE',1,'KEEP','completed','now','now',?)", (revision,))
            conn.execute("INSERT INTO interfaces(id,owner_task_id,state,version,contract_paths_json,revision) VALUES('OLD-IFACE','OLD','frozen','1','[]',?)", (revision,))
            conn.execute("INSERT INTO interface_consumers(interface_id,task_id,required_state,required_version) VALUES('OLD-IFACE','NEW','frozen','1')")
        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="OLD", event_type="fixture.history_interface", payload={}, operation=history_and_interface_consumer)

        with self.assertRaises(TodoError) as blocked:
            self.repo.service.retire_run_batch(self.request())
        self.assertEqual(blocked.exception.code, "retirement_external_consumers")
        self.assertEqual(blocked.exception.details[0]["kind"], "interface_consumer")
        def remove_consumer(conn, revision):
            conn.execute("DELETE FROM interface_consumers WHERE interface_id='OLD-IFACE' AND task_id='NEW'")
        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="OLD", event_type="fixture.remove_consumer", payload={}, operation=remove_consumer)
        result = self.repo.service.retire_run_batch(self.request())
        self.assertEqual(result["preserved_task_ids"], ["KEEP"])
        with self.repo.service.db.read() as conn:
            self.assertEqual(tuple(conn.execute("SELECT status,result FROM tasks WHERE id='KEEP'").fetchone()), ("done", "done"))
            self.assertEqual(conn.execute("SELECT state FROM workflow_lane_tasks WHERE lane_id='OLD-LANE' AND task_id='KEEP'").fetchone()[0], "completed")

    def test_successor_must_be_active_and_have_unfinished_execution(self):
        def empty_successor(conn, revision):
            conn.execute("UPDATE workflow_lane_tasks SET state='completed',completed_at='now',revision=? WHERE lane_id='NEW-LANE'", (revision,))
        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="NEW", event_type="fixture.empty_successor", payload={}, operation=empty_successor)
        with self.assertRaises(TodoError) as blocked:
            self.repo.service.retire_run_batch(self.request())
        self.assertEqual(blocked.exception.code, "retirement_successor_ineligible")

    def test_exact_quarantined_preserved_work_handoff_creates_successor_reference(self):
        identity = self.preserved_source_identity()
        def add_quarantined_source(conn, revision):
            conn.execute(
                "INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,worktree_path,branch,state,cleanup_eligible,created_at,updated_at) "
                "VALUES('OLD-WORKSPACE','repo-id','OLD-RUN','OLD-LANE','exclusive',?,?, 'old-branch','quarantined',0,'now','now')",
                (identity["git_head"], str(self.repo.root)),
            )
        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="OLD", event_type="fixture.preserved_workspace", payload={}, operation=add_quarantined_source)
        request = self.request()
        request["preserved_work_handoffs"] = [{
            "source_workspace_id": "OLD-WORKSPACE", "source_lane_id": "OLD-LANE",
            "successor_lane_id": "NEW-LANE", "successor_task_id": "NEW",
            "repository_identity": "repo-id", "expected_base_commit": identity["git_head"],
            "expected_head": identity["git_head"], "expected_content_fingerprint": identity["fingerprint"], "adopt_dirty": True,
        }]
        result = self.repo.service.retire_run_batch(request)
        self.assertEqual(len(result["preserved_workspace_ids"]), 1)
        with self.repo.service.db.read() as conn:
            source = conn.execute("SELECT state,cleanup_eligible,worktree_path FROM workflow_workspaces WHERE id='OLD-WORKSPACE'").fetchone()
            self.assertEqual(tuple(source), ("quarantined", 0, str(self.repo.root)))
            successor = conn.execute(
                "SELECT run_id,lane_id,mode,base_commit,worktree_path,state,cleanup_eligible,merge_result_json "
                "FROM workflow_workspaces WHERE id=?", (result["preserved_workspace_ids"][0],)
            ).fetchone()
            self.assertEqual(tuple(successor[:7]), ("NEW-RUN", "NEW-LANE", "exclusive", identity["git_head"], str(self.repo.root), "active", 0))
            self.assertEqual(json.loads(successor[7])["preserved_work_handoff"]["expected_content_fingerprint"], identity["fingerprint"])

    def test_preserved_work_handoff_refuses_source_artifact_consumption(self):
        def add_busy_source(conn, revision):
            conn.execute(
                "INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,worktree_path,state,cleanup_eligible,created_at,updated_at) "
                "VALUES('OLD-WORKSPACE','repo-id','OLD-RUN','OLD-LANE','exclusive','base-1','/preserved/worktree','quarantined',0,'now','now')"
            )
            conn.execute(
                "INSERT INTO workflow_patch_artifacts(id,workspace_id,task_id,kind,artifact_ref,content_hash,base_commit,created_at,state) "
                "VALUES('OLD-ARTIFACT','OLD-WORKSPACE','OLD','commit','head-1','content-1','base-1','now','pending')"
            )
        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="OLD", event_type="fixture.busy_preserved_workspace", payload={}, operation=add_busy_source)
        request = self.request()
        request["preserved_work_handoffs"] = [{
            "source_workspace_id": "OLD-WORKSPACE", "source_lane_id": "OLD-LANE",
            "successor_lane_id": "NEW-LANE", "successor_task_id": "NEW",
            "repository_identity": "repo-id", "expected_base_commit": "base-1",
            "expected_head": "head-1", "expected_content_fingerprint": "content-1", "adopt_dirty": True,
        }]
        with self.assertRaises(TodoError) as blocked:
            self.repo.service.retire_run_batch(request)
        self.assertEqual(blocked.exception.code, "retirement_preserved_workspace_busy")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT status FROM workflow_runs WHERE id='OLD-RUN'").fetchone()[0], "active")

    def test_preserved_work_handoff_rechecks_real_dirty_content_and_explicit_adoption(self):
        identity = self.preserved_source_identity()
        def add_quarantined_source(conn, revision):
            conn.execute(
                "INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,worktree_path,state,cleanup_eligible,created_at,updated_at) "
                "VALUES('OLD-WORKSPACE','repo-id','OLD-RUN','OLD-LANE','exclusive',?,?, 'quarantined',0,'now','now')",
                (identity["git_head"], str(self.repo.root)),
            )
        self.repo.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="OLD", event_type="fixture.measured_preserved_workspace", payload={}, operation=add_quarantined_source)

        def request(*, fingerprint: str, adopt_dirty: bool):
            value = self.request()
            value["preserved_work_handoffs"] = [{
                "source_workspace_id": "OLD-WORKSPACE", "source_lane_id": "OLD-LANE",
                "successor_lane_id": "NEW-LANE", "successor_task_id": "NEW",
                "repository_identity": "repo-id", "expected_base_commit": identity["git_head"],
                "expected_head": identity["git_head"], "expected_content_fingerprint": fingerprint,
                "adopt_dirty": adopt_dirty,
            }]
            return value

        with self.assertRaises(TodoError) as false_fingerprint:
            self.repo.service.retire_run_batch(request(fingerprint="0" * 64, adopt_dirty=True))
        self.assertEqual(false_fingerprint.exception.code, "retirement_preserved_workspace_stale")
        with self.assertRaises(TodoError) as missing_adoption:
            self.repo.service.retire_run_batch(request(fingerprint=identity["fingerprint"], adopt_dirty=False))
        self.assertEqual(missing_adoption.exception.code, "retirement_preserved_adoption_required")
        (self.repo.root / "retained.txt").write_bytes(b"changed after review\n")
        with self.assertRaises(TodoError) as changed:
            self.repo.service.retire_run_batch(request(fingerprint=identity["fingerprint"], adopt_dirty=True))
        self.assertEqual(changed.exception.code, "retirement_preserved_workspace_stale")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT status FROM workflow_runs WHERE id='OLD-RUN'").fetchone()[0], "active")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_workspaces WHERE run_id='NEW-RUN'").fetchone()[0], 0)
