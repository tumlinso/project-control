from __future__ import annotations

import io
import json
import os
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.models import TodoError
from todo_orchestrator.workflow.admin import inspect_owner_recovery, run_owner_recovery
from todo_orchestrator.workflow.recovery import RecoveryEngine, project_recovery_lock


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class WorkflowRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        self.repo.apply(base_plan([
            safe_task("A", "src/a", priority=10),
            safe_task("T", "src/t", checkpoints=[{"id": "T-DONE", "title": "T done"}]),
        ]))
        self.capsule = self.repo.service.continue_work(task_id="A")
        self.claim_id = self.capsule["claim"]["claim_id"]
        self.session_id = self.capsule["session"]["agent_id"]
        self.project_uuid = str(self.repo.service.project["project_uuid"])

    def tearDown(self) -> None:
        self.repo.close()

    def mutate(self, operation):
        return self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="recovery_fixture",
            entity_id="A",
            event_type="recovery_fixture.seeded",
            payload={},
            operation=operation,
        )[0]

    def engine(self, probe=None) -> RecoveryEngine:
        return RecoveryEngine(
            self.repo.service.db,
            self.repo.root,
            self.project_uuid,
            process_probe=probe or (lambda hostname, pid, started: False),
            actor_identity="test-owner",
        )

    def seed_dispatch(self, *, pid: int = 999999, workspace: bool = False, capability: bool = False) -> None:
        def seed(conn, revision):
            conn.execute("INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('RUN','A','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lanes(id,run_id,role,created_at,updated_at,revision) VALUES('LANE','RUN','implementer','now','now',?)", (revision,))
            workspace_id = None
            if workspace:
                workspace_id = "WS"
                conn.execute(
                    "INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,worktree_path,branch,state,created_at,updated_at) "
                    "VALUES('WS','repo','RUN','LANE','isolated_merge','base',?,'branch','dirty','now','now')",
                    (str(self.repo.root),),
                )
            conn.execute(
                "INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,workspace_id,context_version,heartbeat_at,hostname,pid,created_at,revision) "
                "VALUES('DISPATCH','LANE',?,?,?,1,'2000-01-01T00:00:00Z',?,?,'now',?)",
                (self.session_id, self.claim_id, workspace_id, socket.gethostname(), pid, revision),
            )
            if capability:
                conn.execute(
                    "INSERT INTO workflow_capabilities(id,token_hash,capability_class,project_uuid,repository_identity,session_id,claim_id,run_id,lane_id,role,task_id,allowed_operations_json,state,created_at,expires_at) "
                    "VALUES('CAP','hash-only','first_class',?,'repo',?,?,'RUN','LANE','implementer','A','[\"sync\"]','active','now','2999-01-01T00:00:00Z')",
                    (self.project_uuid, self.session_id, self.claim_id),
                )
        self.mutate(seed)

    def seed_expired_coordinator(self, *, own_lock: bool = False):
        from todo_orchestrator.git_state import scope_manifest
        self.seed_dispatch(pid=os.getpid(), capability=True)
        def seed(conn, revision):
            conn.execute("DELETE FROM ownership_scopes WHERE task_id='A'")
            conn.execute("UPDATE workflow_lanes SET role='coordinator',workspace_mode='read_shared',state='active' WHERE id='LANE'")
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('LANE',0,'A','active','now',?)", (revision,))
            conn.execute("UPDATE claims SET state='expired_clean',released_at='2000-01-01T00:00:00Z',expires_at='2000-01-01T00:00:00Z',baseline_manifest_json=? WHERE id=?", (json.dumps(scope_manifest(self.repo.root, [])), self.claim_id))
            conn.execute("UPDATE tasks SET status='planned' WHERE id='A'")
            conn.execute("UPDATE workflow_capabilities SET role='coordinator' WHERE id='CAP'")
            conn.execute("UPDATE lock_leases SET state='released' WHERE claim_id=?", (self.claim_id,))
            if own_lock:
                conn.execute("INSERT INTO named_locks(name,capacity,metadata_json) VALUES('coordinator-seat',1,'{}')")
                conn.execute(
                    "INSERT INTO lock_leases(id,lock_name,claim_id,session_id,token_hash,state,acquired_at,heartbeat_at,expires_at) "
                    "VALUES('COORD-LOCK','coordinator-seat',? ,?,'coordinator-lock','active','now','now','2999-01-01T00:00:00Z')",
                    (self.claim_id, self.session_id),
                )
        self.mutate(seed)

    def test_expired_readonly_coordinator_requeues_atomically_with_live_process(self):
        self.seed_expired_coordinator()
        engine = self.engine(lambda *args: True)
        plan = engine.inspect('A')
        self.assertEqual(plan['blockers'], [])
        self.assertEqual([a['kind'] for a in plan['actions']], ['requeue_expired_coordinator'])
        engine.execute(plan, 'recover expired read-only seat')
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM claims WHERE id=?", (self.claim_id,)).fetchone()[0], 'recovered_released')
            self.assertEqual(conn.execute("SELECT state FROM workflow_dispatches WHERE id='DISPATCH'").fetchone()[0], 'recovered')
            self.assertEqual(conn.execute("SELECT state FROM workflow_lane_tasks WHERE task_id='A'").fetchone()[0], 'queued')
            self.assertEqual(conn.execute("SELECT state FROM workflow_capabilities WHERE id='CAP'").fetchone()[0], 'revoked')
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_recovery_audit").fetchone()[0], 1)
        self.assertEqual(engine.inspect('A')['status'], 'already_recovered')

    def test_unobservable_coordinator_recovers_its_own_live_lock_atomically(self):
        self.seed_expired_coordinator(own_lock=True)
        engine = self.engine(lambda *args: None)
        plan = engine.inspect('A')
        self.assertEqual(plan['blockers'], [])
        engine.execute(plan, 'owner confirmed stale coordinator recovery')
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM claims WHERE id=?", (self.claim_id,)).fetchone()[0], 'recovered_released')
            self.assertEqual(conn.execute("SELECT state FROM workflow_capabilities WHERE id='CAP'").fetchone()[0], 'revoked')
            self.assertEqual(conn.execute("SELECT state FROM lock_leases WHERE id='COORD-LOCK'").fetchone()[0], 'released')

    def test_blocked_task_requeues_only_after_its_declared_dependency_is_resolved(self):
        other = V2Repo()
        try:
            other.apply(base_plan([
                safe_task('UPSTREAM', 'src/upstream'),
                safe_task('BLOCKED', 'src/blocked', depends_on=[{'type': 'task', 'task_id': 'UPSTREAM'}]),
            ]))

            def seed(conn, revision):
                conn.execute("INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('RUN','BLOCKED','now','now',?)", (revision,))
                conn.execute("INSERT INTO workflow_lanes(id,run_id,role,state,created_at,updated_at,revision) VALUES('LANE','RUN','implementer','attention_required','now','now',?)", (revision,))
                conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('LANE',0,'BLOCKED','queued','now',?)", (revision,))
                conn.execute("UPDATE tasks SET status='blocked',attention_reason='external prerequisite pending' WHERE id='BLOCKED'")

            other.service.db.mutate(actor_session_id=None, entity_type='fixture', entity_id='BLOCKED', event_type='fixture', payload={}, operation=seed)
            engine = RecoveryEngine(other.service.db, other.root, str(other.service.project['project_uuid']), process_probe=lambda *_: False)
            plan = engine.inspect('BLOCKED')
            self.assertEqual(plan['status'], 'refused')
            self.assertEqual(plan['blockers'][0]['state'], 'external_dependency_unresolved')
            other.service.db.mutate(
                actor_session_id=None, entity_type='fixture', entity_id='UPSTREAM', event_type='fixture', payload={},
                operation=lambda conn, revision: conn.execute("UPDATE tasks SET status='done',result='implemented' WHERE id='UPSTREAM'"),
            )
            self.assertEqual([action['kind'] for action in engine.inspect('BLOCKED')['actions']], ['requeue_blocked_task'])
        finally:
            other.close()

    def test_expired_coordinator_revokes_retired_family_preserves_other_claim(self):
        other = self.repo.service.continue_work(task_id='T')
        self.seed_expired_coordinator()
        def seed(conn, revision):
            conn.execute("INSERT INTO workflow_lanes(id,run_id,parent_lane_id,role,state,created_at,updated_at,revision) VALUES('OTHER-LANE','RUN','LANE','implementer','active','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('OTHER-LANE',0,'T','active','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,context_version,heartbeat_at,created_at,revision) VALUES('OTHER-DISPATCH','OTHER-LANE',?,?,1,'now','now',?)", (other['session']['agent_id'], other['claim']['claim_id'], revision))
            cap = dict(conn.execute("SELECT * FROM workflow_capabilities WHERE id='CAP'").fetchone())
            other_cap = {**cap, 'id': 'OTHER-CAP', 'token_hash': 'other-cap-hash', 'claim_id': other['claim']['claim_id'], 'session_id': other['session']['agent_id'], 'lane_id': 'OTHER-LANE', 'task_id': 'T', 'role': 'implementer'}
            conn.execute('INSERT INTO workflow_capabilities (' + ','.join(other_cap) + ') VALUES (' + ','.join('?' for _ in other_cap) + ')', tuple(other_cap.values()))
            cap.update(id='OLD-CAP', token_hash='old-hash', state='retired')
            conn.execute('INSERT INTO workflow_capabilities (' + ','.join(cap) + ') VALUES (' + ','.join('?' for _ in cap) + ')', tuple(cap.values()))
        self.mutate(seed)
        with self.repo.service.db.read() as conn:
            before = dict(conn.execute("SELECT * FROM claims WHERE id=?", (other['claim']['claim_id'],)).fetchone())
        engine = self.engine(lambda *args: True)
        engine.execute(engine.inspect('A'), 'expired coordinator only')
        with self.repo.service.db.read() as conn:
            self.assertEqual(dict(conn.execute("SELECT * FROM claims WHERE id=?", (other['claim']['claim_id'],)).fetchone()), before)
            self.assertEqual(conn.execute("SELECT state FROM workflow_capabilities WHERE id='OLD-CAP'").fetchone()[0], 'revoked')
            self.assertEqual(conn.execute("SELECT state FROM workflow_capabilities WHERE id='OTHER-CAP'").fetchone()[0], 'active')
            self.assertEqual(conn.execute("SELECT state FROM workflow_dispatches WHERE id='OTHER-DISPATCH'").fetchone()[0], 'active')
            self.assertEqual(conn.execute("SELECT state FROM workflow_lane_tasks WHERE lane_id='OTHER-LANE'").fetchone()[0], 'active')


    def test_expired_coordinator_refuses_running_gate_and_writable_scope(self):
        self.seed_expired_coordinator()
        engine = self.engine(lambda *args: True)
        def seed(conn, revision):
            conn.execute("INSERT INTO gates(id,task_id,type,status) VALUES('G','T','command','running')")
        self.mutate(seed)
        self.assertIn('active_gates', [b['state'] for b in engine.inspect('A')['blockers']])
        def replace(conn, revision):
            conn.execute("UPDATE gates SET status='passed' WHERE id='G'")
            conn.execute("INSERT INTO ownership_scopes(task_id,path,mode) VALUES('A','src/a','exclusive')")
        self.mutate(replace)
        self.assertIn('writable_scope', [b['state'] for b in engine.inspect('A')['blockers']])

    def test_expired_coordinator_refuses_child_even_if_process_stopped(self):
        self.seed_expired_coordinator()
        self.seed_child(expires_at='2000-01-01T00:00:00Z')
        plan = self.engine().inspect('A')
        self.assertIn('child_operation', [b['state'] for b in plan['blockers']])

    def test_expired_coordinator_never_releases_unrelated_stale_lock(self):
        self.seed_expired_coordinator()
        def seed(conn, revision):
            conn.execute("INSERT INTO named_locks(name,capacity,metadata_json) VALUES('other-lock',1,'{}')")
            conn.execute("INSERT INTO lock_leases(id,lock_name,session_id,token_hash,state,acquired_at,heartbeat_at,expires_at) VALUES('OTHER','other-lock',?,'other-hash','active','now','now','2000-01-01T00:00:00Z')", (self.session_id,))
        self.mutate(seed)
        plan = self.engine().inspect('A')
        self.assertIn('active_lock_leases', [b['state'] for b in plan['blockers']])
        self.assertEqual(plan['actions'], [])

    def test_expired_coordinator_refuses_dirty_read_shared_workspace(self):
        self.seed_expired_coordinator()
        def seed(conn, revision):
            conn.execute("INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,worktree_path,state,created_at,updated_at) VALUES('WS','repo','RUN','LANE','read_shared','base',?,'active','now','now')", (str(self.repo.root),))
            conn.execute("UPDATE workflow_dispatches SET workspace_id='WS' WHERE id='DISPATCH'")
        self.mutate(seed)
        # Fixture root contains untracked plan/runtime files, deliberately dirty.
        plan = self.engine(lambda *args: True).inspect('A')
        self.assertIn('dirty_or_unavailable_workspace', [b['state'] for b in plan['blockers']])

    def test_unswept_expired_coordinator_preview_and_atomic_expiration(self):
        other = self.repo.service.continue_work(task_id='T')
        self.seed_expired_coordinator()
        def seed(conn, revision):
            conn.execute("UPDATE claims SET state='active',released_at=NULL WHERE id=?", (self.claim_id,))
            conn.execute("UPDATE tasks SET status='in_progress' WHERE id='A'")
            conn.execute("UPDATE claims SET expires_at='2000-01-01T00:00:00Z' WHERE id=?", (other['claim']['claim_id'],))
        self.mutate(seed)
        engine = self.engine(lambda *args: True)
        with self.repo.service.db.read() as conn:
            other_before = dict(conn.execute("SELECT * FROM claims WHERE id=?", (other['claim']['claim_id'],)).fetchone())
        plan = engine.inspect('A')
        self.assertEqual(plan['blockers'], [])
        self.assertEqual(plan['actions'][0]['kind'], 'expire_and_requeue_coordinator')
        self.assertEqual(plan['actions'][0]['claim_transition'], {'from': 'active', 'to': 'expired_clean', 'expires_at': '2000-01-01T00:00:00Z'})
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM claims WHERE id=?", (self.claim_id,)).fetchone()[0], 'active')
        engine.execute(plan, 'selected unswept read-only coordinator')
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM claims WHERE id=?", (self.claim_id,)).fetchone()[0], 'recovered_released')
            self.assertEqual(conn.execute("SELECT state FROM workflow_lane_tasks WHERE task_id='A'").fetchone()[0], 'queued')
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id='A'").fetchone()[0], 'planned')
            self.assertEqual(conn.execute("SELECT state FROM workflow_capabilities WHERE id='CAP'").fetchone()[0], 'revoked')
            self.assertEqual(dict(conn.execute("SELECT * FROM claims WHERE id=?", (other['claim']['claim_id'],)).fetchone()), other_before)

    def test_unswept_coordinator_refuses_unexpired_and_dirty_variants(self):
        self.seed_expired_coordinator()
        def seed(conn, revision):
            conn.execute("UPDATE claims SET state='active',released_at=NULL,expires_at='2999-01-01T00:00:00Z' WHERE id=?", (self.claim_id,))
            conn.execute("UPDATE tasks SET status='in_progress' WHERE id='A'")
        self.mutate(seed)
        engine = self.engine(lambda *args: True)
        self.assertEqual(engine.inspect('A')['status'], 'refused')
        def dirty(conn, revision):
            conn.execute("UPDATE claims SET expires_at='2000-01-01T00:00:00Z',baseline_manifest_json='{}' WHERE id=?", (self.claim_id,))
        self.mutate(dirty)
        plan = engine.inspect('A')
        self.assertIn('scope_changed', [b['state'] for b in plan['blockers']])
        self.assertEqual(plan['actions'], [])

    def test_expired_coordinator_is_not_global_recovery_exception(self):
        self.seed_expired_coordinator()
        self.assertEqual(self.engine(lambda *args: True).inspect()['status'], 'refused')

    def test_expired_coordinator_refuses_unsafe_variants(self):
        self.seed_expired_coordinator()
        engine = self.engine(lambda *args: True)
        variants = [
            ("UPDATE claims SET state='active' WHERE id=?", (self.claim_id,)),
            ("UPDATE workflow_lanes SET workspace_mode='exclusive' WHERE id='LANE'", ()),
            ("UPDATE claims SET baseline_manifest_json='{}' WHERE id=?", (self.claim_id,)),
            ("UPDATE tasks SET status='done' WHERE id='A'", ()),
            ("UPDATE workflow_lane_tasks SET state='queued' WHERE task_id='A'", ()),
        ]
        for sql, args in variants:
            with self.subTest(sql=sql):
                with self.repo.service.db.read() as conn:
                    conn.execute(sql, args)
                    plan = engine._expired_coordinator_plan(conn, 'A', datetime.now(timezone.utc))
                    if plan is None:
                        # Active claim must retain the ordinary live refusal.
                        pass
                    else:
                        self.assertEqual(plan['status'], 'refused')
                    conn.rollback()
        self.mutate(lambda conn, rev: conn.execute("UPDATE claims SET state='active' WHERE id=?", (self.claim_id,)))
        self.assertEqual(engine.inspect('A')['status'], 'refused')

    def test_expired_coordinator_transaction_refuses_revision_race(self):
        self.seed_expired_coordinator()
        engine = self.engine(lambda *args: True)
        plan = engine.inspect('A')
        original = self.repo.service.db.mutate
        from unittest.mock import patch
        def race(**kwargs):
            self.mutate(lambda conn, rev: conn.execute("UPDATE tasks SET title='concurrent unrelated change' WHERE id='T'"))
            return original(**kwargs)
        # Inject a committed writer after execute's fresh inspection.
        def intercepted(**kwargs):
            with patch.object(type(self.repo.service.db), 'mutate', original):
                return race(**kwargs)
        with patch.object(type(self.repo.service.db), 'mutate', lambda db, **kwargs: intercepted(**kwargs)):
            with self.assertRaises(TodoError) as caught:
                engine.execute(plan, 'race check')
        self.assertEqual(caught.exception.code, 'recovery_plan_stale')
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM workflow_dispatches WHERE id='DISPATCH'").fetchone()[0], 'active')
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_recovery_audit").fetchone()[0], 0)

    def test_stopped_first_class_worker_clean_scope_retires_lineage_and_is_idempotent(self) -> None:
        self.seed_dispatch(capability=True)
        engine = RecoveryEngine(
            self.repo.service.db, self.repo.root, self.project_uuid,
            process_probe=lambda *_: False, child_process_probe=lambda _: False,
            actor_identity="test-owner",
        )
        plan = engine.inspect("A")
        self.assertEqual(plan["status"], "recovery_needed")
        self.assertEqual(plan["blockers"], [])
        kinds = {item["kind"] for item in plan["actions"]}
        self.assertTrue({"retire_dispatch", "release_claim", "retire_capability"} <= kinds)
        result = engine.execute(plan, "stopped owner toc_secret-value")
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(result["resume"], "next_task")
        self.assertFalse(result["files_mutated"])
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM workflow_dispatches WHERE id='DISPATCH'").fetchone()[0], "recovered")
            self.assertEqual(conn.execute("SELECT state FROM claims WHERE id=?", (self.claim_id,)).fetchone()[0], "recovered_released")
            self.assertEqual(conn.execute("SELECT state FROM workflow_capabilities WHERE id='CAP'").fetchone()[0], "retired")
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id='A'").fetchone()[0], "planned")
            audit = conn.execute("SELECT reason FROM workflow_recovery_audit").fetchone()
            event = conn.execute("SELECT payload_json FROM events WHERE event_type='workflow_recovery.completed'").fetchone()
        self.assertEqual(audit["reason"], "stopped owner [redacted]")
        self.assertNotIn("token", event[0].lower())
        second = engine.execute(engine.inspect("A"), "repeat")
        self.assertTrue(second["idempotent_noop"])
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_recovery_audit").fetchone()[0], 1)

    def test_lost_capability_index_does_not_prevent_resume(self) -> None:
        self.seed_dispatch(capability=False)
        result = self.engine().execute(self.engine().inspect("A"), "lost locator and handle")
        self.assertEqual(result["resume"], "next_task")
        self.assertEqual(result["status"], "recovered")

    def test_global_recovery_clears_attention_left_by_released_lane_claim(self) -> None:
        self.seed_dispatch()

        def release_with_attention(conn, revision):
            conn.execute("UPDATE claims SET state='released',released_at='now' WHERE id=?", (self.claim_id,))
            conn.execute("UPDATE workflow_dispatches SET state='released',released_at='now' WHERE id='DISPATCH'")
            conn.execute("UPDATE workflow_lanes SET state='ready' WHERE id='LANE'")
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('LANE',0,'A','queued','now',?)", (revision,))
            conn.execute("UPDATE tasks SET status='in_progress',attention_reason='historical release note' WHERE id='A'")

        self.mutate(release_with_attention)
        engine = self.engine()
        plan = engine.inspect()
        self.assertIn(
            {"kind": "clear_released_attention", "task_id": "A"},
            plan["actions"],
        )
        engine.execute(plan, "clear released lane residue")
        with self.repo.service.db.read() as conn:
            task = conn.execute("SELECT status,attention_reason FROM tasks WHERE id='A'").fetchone()
        self.assertEqual(tuple(task), ("in_progress", None))

    def test_dirty_scope_and_workspace_are_quarantined_without_file_loss(self) -> None:
        self.seed_dispatch(workspace=True)
        source = self.repo.root / "src" / "a" / "dirty.txt"
        source.parent.mkdir(parents=True)
        source.write_text("preserve me\n", encoding="utf-8")
        before = source.read_bytes()
        plan = self.engine().inspect("A")
        self.assertTrue(any(item.get("dirty") for item in plan["actions"] if item["kind"] == "release_claim"))
        result = self.engine().execute(plan, "paused dirty worker")
        self.assertEqual(source.read_bytes(), before)
        self.assertIn("A", result["dirty_tasks"])
        with self.repo.service.db.read() as conn:
            task = conn.execute("SELECT status,attention_reason FROM tasks WHERE id='A'").fetchone()
            handoff = conn.execute("SELECT kind,payload_json FROM handoffs WHERE task_id='A' ORDER BY revision DESC LIMIT 1").fetchone()
            workspace = conn.execute("SELECT state,cleanup_eligible FROM workflow_workspaces WHERE id='WS'").fetchone()
        self.assertEqual(task["status"], "attention_required")
        self.assertEqual(handoff["kind"], "recovery_quarantine")
        self.assertIn("fingerprint", json.loads(handoff["payload_json"]))
        self.assertEqual(tuple(workspace), ("quarantined", 0))

    def test_live_or_unobservable_first_class_process_refuses_recovery(self) -> None:
        for observed in (True, None):
            with self.subTest(observed=observed):
                other = V2Repo()
                try:
                    other.apply(base_plan([safe_task("A", "src/a")]))
                    capsule = other.service.continue_work(task_id="A")
                    project_uuid = str(other.service.project["project_uuid"])
                    def seed(conn, revision):
                        conn.execute("INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('RUN','A','now','now',?)", (revision,))
                        conn.execute("INSERT INTO workflow_lanes(id,run_id,role,created_at,updated_at,revision) VALUES('LANE','RUN','implementer','now','now',?)", (revision,))
                        conn.execute(
                            "INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,context_version,heartbeat_at,hostname,pid,created_at,revision) VALUES('D','LANE',?,?,1,'2000-01-01T00:00:00Z','remote',7,'now',?)",
                            (capsule["session"]["agent_id"], capsule["claim"]["claim_id"], revision),
                        )
                    other.service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="A", event_type="fixture", payload={}, operation=seed)
                    engine = RecoveryEngine(other.service.db, other.root, project_uuid, process_probe=lambda h, p, s, value=observed: value)
                    plan = engine.inspect("A")
                    self.assertEqual(plan["status"], "refused")
                    with self.assertRaises(TodoError) as error:
                        engine.execute(plan, "must refuse")
                    self.assertEqual(error.exception.code, "recovery_live_work_refused")
                finally:
                    other.close()

    def seed_child(self, *, expires_at: str, state: str = "running", candidate: bool = True) -> None:
        def seed(conn, revision):
            conn.execute(
                "INSERT INTO child_executions(id,parent_claim_id,task_id,objective,state,max_attempts,attempt_count,created_at,access_mode,authorized_scopes_json) "
                "VALUES('CHILD',?,'A','bounded',?,1,1,'now','write','[\"src/a\"]')",
                (self.claim_id, state),
            )
            conn.execute("INSERT INTO child_scope_leases(child_execution_id,path,state,acquired_at) VALUES('CHILD','src/a','active','now')")
            conn.execute(
                "INSERT INTO child_attempts(id,child_execution_id,attempt_number,token_hash,state,created_at,heartbeat_at,expires_at) VALUES('ATTEMPT','CHILD',1,'child-hash','active','now','now',?)",
                (expires_at,),
            )
            if candidate:
                conn.execute(
                    "INSERT INTO workflow_child_result_candidates(id,child_execution_id,parent_claim_id,kind,payload_json,artifact_refs_json,state,created_at) "
                    "VALUES('CANDIDATE','CHILD',?,'candidate_patch','{\"summary\":\"kept\"}','[\"patch:1\"]','collected','now')",
                    (self.claim_id,),
                )
        self.mutate(seed)

    def test_active_local_child_refuses_scope_reclamation(self) -> None:
        self.seed_dispatch()
        self.seed_child(expires_at="2999-01-01T00:00:00Z")
        plan = self.engine().inspect("A")
        self.assertTrue(any(item["kind"] == "local_child" for item in plan["blockers"]))
        with self.assertRaises(TodoError):
            self.engine().execute(plan, "child is active")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM child_scope_leases WHERE child_execution_id='CHILD'").fetchone()[0], "active")

    def test_dead_local_child_is_terminalized_artifacts_preserved_and_parent_resumes(self) -> None:
        self.seed_dispatch()
        self.seed_child(expires_at="2000-01-01T00:00:00Z")
        engine = RecoveryEngine(
            self.repo.service.db, self.repo.root, self.project_uuid,
            process_probe=lambda *_: False, child_process_probe=lambda _: False,
            actor_identity="test-owner",
        )
        plan = engine.inspect("A")
        child_action = next(item for item in plan["actions"] if item["kind"] == "terminalize_dead_child")
        self.assertEqual(child_action["preserved_candidate_ids"], ["CANDIDATE"])
        result = engine.execute(plan, "dead subordinate")
        self.assertEqual(result["resume"], "next_task")
        with self.repo.service.db.read() as conn:
            execution = conn.execute("SELECT state FROM child_executions WHERE id='CHILD'").fetchone()[0]
            attempt = conn.execute("SELECT state FROM child_attempts WHERE id='ATTEMPT'").fetchone()[0]
            lease = conn.execute("SELECT state FROM child_scope_leases WHERE child_execution_id='CHILD'").fetchone()[0]
            candidate = conn.execute("SELECT state,payload_json,artifact_refs_json FROM workflow_child_result_candidates WHERE id='CANDIDATE'").fetchone()
            parent_lane_membership = conn.execute("SELECT COUNT(*) FROM workflow_lane_tasks WHERE task_id='A'").fetchone()[0]
            child_lane_membership = conn.execute(
                "SELECT COUNT(*) FROM workflow_lane_tasks lt JOIN child_executions c ON c.id=lt.task_id WHERE c.id='CHILD'"
            ).fetchone()[0]
        self.assertEqual((execution, attempt, lease), ("failed", "failed", "released"))
        self.assertEqual(candidate["state"], "collected")
        self.assertIn("patch:1", candidate["artifact_refs_json"])
        self.assertEqual(parent_lane_membership, 1)  # v2 plans normalize to one serial compatibility lane.
        self.assertEqual(child_lane_membership, 0)

    def test_supervisor_status_overrides_child_lease_age_safely(self) -> None:
        self.seed_dispatch()
        self.seed_child(expires_at="2999-01-01T00:00:00Z")
        stopped = RecoveryEngine(
            self.repo.service.db, self.repo.root, self.project_uuid,
            process_probe=lambda h, p, s: False,
            child_process_probe=lambda child: False,
        )
        self.assertTrue(any(item["kind"] == "terminalize_dead_child" for item in stopped.inspect("A")["actions"]))
        live = RecoveryEngine(
            self.repo.service.db, self.repo.root, self.project_uuid,
            process_probe=lambda h, p, s: False,
            child_process_probe=lambda child: True,
        )
        self.assertTrue(any(item.get("state") == "demonstrably_live" for item in live.inspect("A")["blockers"]))

    def test_running_gate_and_background_resource_refuse_recovery(self) -> None:
        self.seed_dispatch()
        def seed(conn, revision):
            conn.execute("UPDATE gates SET status='running' WHERE task_id='A'")
            conn.execute("INSERT INTO gates(id,task_id,type,status,valid) VALUES('ACTIVE-GATE','A','command','running',0)")
            conn.execute("INSERT INTO resource_classes(id,mode,metadata_json) VALUES('gpu','exclusive','{}')")
            conn.execute("INSERT INTO resource_instances(id,class_id,capacity,hostname,metadata_json) VALUES('gpu:0','gpu',1,?,'{}')", (socket.gethostname(),))
            conn.execute(
                "INSERT INTO resource_leases(id,instance_id,session_id,token_hash,state,hostname,pid,acquired_at,heartbeat_at,expires_at,command_json) "
                "VALUES('GPU-LEASE','gpu:0',?,'resource-hash','active',?,?,'now','now','2999-01-01T00:00:00Z','[\"cuda-campaign\"]')",
                (self.session_id, socket.gethostname(), os.getpid()),
            )
        self.mutate(seed)
        plan = self.engine(lambda h, p, s: p == os.getpid()).inspect("A")
        kinds = {item["kind"] for item in plan["blockers"]}
        self.assertTrue({"gate", "resource"} <= kinds)

    def test_stale_lock_and_resource_are_released(self) -> None:
        self.seed_dispatch()
        def seed(conn, revision):
            conn.execute("INSERT INTO named_locks(name,capacity,metadata_json) VALUES('recovery-fixture',1,'{}')")
            conn.execute(
                "INSERT INTO lock_leases(id,lock_name,session_id,token_hash,state,acquired_at,heartbeat_at,expires_at,hostname,pid) "
                "VALUES('LOCK','recovery-fixture',?,'lock-hash','active','now','now','2000-01-01T00:00:00Z',?,999999)",
                (self.session_id, socket.gethostname()),
            )
            conn.execute("INSERT INTO resource_classes(id,mode,metadata_json) VALUES('cpu','exclusive','{}')")
            conn.execute("INSERT INTO resource_instances(id,class_id,capacity,hostname,metadata_json) VALUES('cpu:0','cpu',1,?,'{}')", (socket.gethostname(),))
            conn.execute(
                "INSERT INTO resource_leases(id,instance_id,session_id,token_hash,state,hostname,pid,acquired_at,heartbeat_at,expires_at) "
                "VALUES('RESOURCE','cpu:0',?,'resource-hash','active',?,999999,'now','now','2000-01-01T00:00:00Z')",
                (self.session_id, socket.gethostname()),
            )
        self.mutate(seed)
        engine = self.engine()
        plan = engine.inspect()
        kinds = {item["kind"] for item in plan["actions"]}
        self.assertTrue({"release_lock", "release_resource"} <= kinds)
        engine.execute(plan, "stale leases")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM lock_leases WHERE id='LOCK'").fetchone()[0], "recovered")
            self.assertEqual(conn.execute("SELECT state FROM resource_leases WHERE id='RESOURCE'").fetchone()[0], "recovered")

    def test_delegated_recovery_releases_owned_resource_and_leaves_unrelated_live_resource(self) -> None:
        self.seed_dispatch()
        def seed(conn, revision):
            conn.execute("INSERT INTO resource_classes(id,mode,metadata_json) VALUES('cpu','exclusive','{}')")
            conn.execute("INSERT INTO resource_instances(id,class_id,capacity,hostname,metadata_json) VALUES('cpu:0','cpu',1,?,'{}')", (socket.gethostname(),))
            conn.execute("INSERT INTO resource_instances(id,class_id,capacity,hostname,metadata_json) VALUES('cpu:1','cpu',1,?,'{}')", (socket.gethostname(),))
            conn.execute("INSERT INTO resource_leases(id,instance_id,session_id,token_hash,state,hostname,pid,acquired_at,heartbeat_at,expires_at) VALUES('OTHER','cpu:0',?,'x','active',?,?,'now','2999-01-01T00:00:00Z','2999-01-01T00:00:00Z')", (self.session_id, socket.gethostname(), os.getpid()))
            conn.execute("INSERT INTO resource_leases(id,instance_id,claim_id,session_id,token_hash,state,hostname,pid,acquired_at,heartbeat_at,expires_at) VALUES('OWNED','cpu:1',?,?,'x','active',?,999999,'now','2000-01-01T00:00:00Z','2000-01-01T00:00:00Z')", (self.claim_id, self.session_id, socket.gethostname()))
        self.mutate(seed)
        plan = self.engine().inspect('A')
        self.assertFalse(any(a['kind'] == 'release_resource' and a['id'] == 'OTHER' for a in plan['actions']))
        self.assertTrue(any(a['kind'] == 'release_resource' and a['id'] == 'OWNED' and a['task_id'] == 'A' for a in plan['actions']))
        self.assertTrue(self.engine().delegated_effects_are_exact(plan, 'A'))
        self.engine().execute(plan, 'target only', delegated_task_id='A')
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM resource_leases WHERE id='OTHER'").fetchone()[0], 'active')
            self.assertEqual(conn.execute("SELECT state FROM resource_leases WHERE id='OWNED'").fetchone()[0], 'recovered')

    def test_task_recovery_does_not_quarantine_live_producer_workspace_for_its_integrator(self) -> None:
        """An integration consumer relation is never workspace ownership."""
        producer = self.repo.service.continue_work(task_id='T')

        def seed(conn, revision):
            conn.execute("INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('INTEGRATOR-RUN','A','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('PRODUCER-RUN','T','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lanes(id,run_id,role,state,created_at,updated_at,revision) VALUES('INTEGRATOR-LANE','INTEGRATOR-RUN','integrator','active','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lanes(id,run_id,role,state,created_at,updated_at,revision) VALUES('PRODUCER-LANE','PRODUCER-RUN','implementer','active','now','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('INTEGRATOR-LANE',0,'A','active','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('PRODUCER-LANE',0,'T','active','now',?)", (revision,))
            conn.execute(
                "INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,worktree_path,branch,state,integration_task_id,created_at,updated_at) "
                "VALUES('PRODUCER-WS','repo','PRODUCER-RUN','PRODUCER-LANE','isolated_merge','base',?,'producer','active','A','now','now')",
                (str(self.repo.root),),
            )
            conn.execute(
                "INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,context_version,heartbeat_at,hostname,pid,created_at,revision) "
                "VALUES('INTEGRATOR-DISPATCH','INTEGRATOR-LANE',?,?,1,'2000-01-01T00:00:00Z',?,999999,'now',?)",
                (self.session_id, self.claim_id, socket.gethostname(), revision),
            )
            conn.execute(
                "INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,workspace_id,context_version,heartbeat_at,hostname,pid,created_at,revision) "
                "VALUES('PRODUCER-DISPATCH','PRODUCER-LANE',?,?,?,1,'now',?,?,'now',?)",
                (producer['session']['agent_id'], producer['claim']['claim_id'], 'PRODUCER-WS', socket.gethostname(), os.getpid(), revision),
            )
            conn.execute("INSERT INTO resource_classes(id,mode,metadata_json) VALUES('producer-cpu','exclusive','{}')")
            conn.execute("INSERT INTO resource_instances(id,class_id,capacity,hostname,metadata_json) VALUES('producer-cpu:0','producer-cpu',1,?,'{}')", (socket.gethostname(),))
            conn.execute(
                "INSERT INTO resource_leases(id,instance_id,claim_id,session_id,token_hash,state,hostname,pid,acquired_at,heartbeat_at,expires_at) "
                "VALUES('PRODUCER-LEASE','producer-cpu:0',?,?,'producer','active',?,?,'now','now','2999-01-01T00:00:00Z')",
                (producer['claim']['claim_id'], producer['session']['agent_id'], socket.gethostname(), os.getpid()),
            )
        self.mutate(seed)

        engine = self.engine(lambda _host, pid, _started: pid == os.getpid())
        plan = engine.inspect('A')
        self.assertFalse(any(action.get('workspace_id') == 'PRODUCER-WS' for action in plan['actions']))
        self.assertTrue(engine.delegated_effects_are_exact(plan, 'A'))
        engine.execute(plan, 'recover stopped integrator only', delegated_task_id='A')
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM workflow_workspaces WHERE id='PRODUCER-WS'").fetchone()[0], 'active')
            self.assertEqual(conn.execute("SELECT state FROM claims WHERE id=?", (producer['claim']['claim_id'],)).fetchone()[0], 'active')
            self.assertEqual(conn.execute("SELECT state FROM workflow_dispatches WHERE id='PRODUCER-DISPATCH'").fetchone()[0], 'active')
            self.assertEqual(conn.execute("SELECT state FROM resource_leases WHERE id='PRODUCER-LEASE'").fetchone()[0], 'active')

    def test_general_recovery_rejects_writer_after_final_inspection(self) -> None:
        self.seed_dispatch()
        engine = self.engine()
        plan = engine.inspect('A')
        original_inspect = RecoveryEngine.inspect
        injected = False
        def raced(instance, task_id):
            nonlocal injected
            fresh = original_inspect(instance, task_id)
            if not injected:
                injected = True
                self.mutate(lambda conn, revision: conn.execute("UPDATE tasks SET title=title WHERE id='T'"))
            return fresh
        from unittest.mock import patch
        with patch.object(RecoveryEngine, 'inspect', raced):
            with self.assertRaises(TodoError) as error:
                engine.execute(plan, 'race')
        self.assertEqual(error.exception.code, 'recovery_plan_stale')
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM workflow_dispatches WHERE id='DISPATCH'").fetchone()[0], 'active')

    def test_terminal_checkpoint_finalization_is_automatic_and_idempotent(self) -> None:
        token = self.repo.service.continue_work(task_id="T")["claim"]["claim_token"]
        self.repo.service.complete(token, "implemented")
        with self.repo.service.db.connect() as conn:
            conn.execute("UPDATE checkpoints SET state='pending',reached_at=NULL WHERE id='T-DONE'")
            conn.commit()
        engine = self.engine()
        plan = engine.inspect("T")
        self.assertTrue(any(item["kind"] == "finalize_terminal_checkpoints" for item in plan["actions"]))
        engine.execute(plan, "terminal reconciliation")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM checkpoints WHERE id='T-DONE'").fetchone()[0], "reached")
        self.assertTrue(engine.execute(engine.inspect("T"), "repeat")["idempotent_noop"])

    def test_interactive_single_confirmation_flow_has_no_approval_token(self) -> None:
        self.seed_dispatch()
        engine = self.engine()
        stdout = TtyBuffer()
        with self.assertRaises(TodoError) as mismatch:
            run_owner_recovery(
                engine, database_path=self.repo.service.paths.db_file, reason="owner request", task_id="A",
                stdin=TtyBuffer("wrong\n"), stdout=stdout,
            )
        self.assertEqual(mismatch.exception.code, "recovery_confirmation_mismatch")
        result = run_owner_recovery(
            engine, database_path=self.repo.service.paths.db_file, reason="owner request", task_id="A",
            stdin=TtyBuffer("A\n"), stdout=TtyBuffer(),
        )
        self.assertEqual(result["status"], "recovered")
        self.assertNotIn("approval_token", result)

    def test_tty_required_and_inspection_remains_read_only(self) -> None:
        self.seed_dispatch()
        before = self.repo.service.db.revision()
        plan = inspect_owner_recovery(self.engine(), "A")
        self.assertEqual(self.repo.service.db.revision(), before)
        self.assertEqual(plan["file_policy"], "preserve_all_no_repository_mutation")
        with self.assertRaises(TodoError) as error:
            run_owner_recovery(
                self.engine(), database_path=self.repo.service.paths.db_file, reason="noninteractive", task_id="A",
                stdin=io.StringIO("A\n"), stdout=io.StringIO(),
            )
        self.assertEqual(error.exception.code, "recovery_tty_required")

    def test_project_recovery_lock_serializes_owner_operations(self) -> None:
        path = self.repo.service.paths.db_file
        with project_recovery_lock(path):
            with self.assertRaises(TodoError) as error:
                with project_recovery_lock(path):
                    pass
        self.assertEqual(error.exception.code, "recovery_lock_busy")


if __name__ == "__main__":
    unittest.main()
