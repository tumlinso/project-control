from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.semantic.workflow import workflow_state


def past() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class WorkflowSemanticReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        plan = base_plan([
            safe_task("ROOT", "coord"),
            safe_task("A", "src/a"),
            safe_task("B", "src/b"),
        ])
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "ROOT", "charter": {"objective": "observe"},
            "lanes": [
                {"id": "ROOT-LANE", "role": "coordinator", "tasks": ["ROOT"]},
                {"id": "A-LANE", "parent_lane_id": "ROOT-LANE", "role": "implementer", "tasks": ["A"]},
                {"id": "B-LANE", "parent_lane_id": "ROOT-LANE", "role": "implementer", "tasks": ["B"]},
            ],
        }]
        self.repo.apply(plan)
        self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="test",
            entity_id="RUN",
            event_type="test.semantic_setup",
            payload={},
            operation=lambda conn, revision: conn.execute(
                "UPDATE workflow_lanes SET state='closed',revision=? WHERE id='ROOT-LANE'", (revision,)
            ),
        )

    def tearDown(self) -> None:
        self.repo.close()

    def read(self) -> dict[str, object]:
        with self.repo.service.db.read() as conn:
            return workflow_state(conn, self.repo.service.project)

    def test_safe_parallel_groups_require_dependency_lock_resource_and_scope_safety(self) -> None:
        self.assertEqual(self.read()["safe_parallel_groups"], [["A-LANE", "B-LANE"]])

        def shared_lock(conn, revision):
            conn.execute("INSERT INTO named_locks(name,capacity) VALUES('shared',1)")
            conn.execute("INSERT INTO task_locks(task_id,lock_name,phase) VALUES('A','shared','claim')")
            conn.execute("INSERT INTO task_locks(task_id,lock_name,phase) VALUES('B','shared','claim')")

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="test", entity_id="shared",
            event_type="test.lock_conflict", payload={}, operation=shared_lock,
        )
        self.assertEqual(self.read()["safe_parallel_groups"], [])

    def test_active_run_prefers_a_run_with_actionable_lane_heads(self) -> None:
        def older_blocked_run(conn, revision):
            conn.execute("INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('BLOCKED-RUN','ROOT','before','before',?)", (revision,))
            conn.execute("INSERT INTO workflow_lanes(id,run_id,role,state,created_at,updated_at,revision) VALUES('BLOCKED-LANE','BLOCKED-RUN','implementer','ready','before','before',?)", (revision,))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('BLOCKED-LANE',0,'ROOT','queued','before',?)", (revision,))
            conn.execute("UPDATE tasks SET status='blocked',attention_reason='permission not granted' WHERE id='ROOT'")

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="test", entity_id="BLOCKED-RUN",
            event_type="test.blocked_run", payload={}, operation=older_blocked_run,
        )
        self.assertEqual(self.read()["active_run_id"], "RUN")

    def test_safe_parallel_groups_reject_unsatisfied_contracts_and_resource_or_scope_conflicts(self) -> None:
        def dependency(conn, revision):
            conn.execute(
                "INSERT INTO task_dependencies(task_id,type,prerequisite_task_id,condition_json) "
                "VALUES('A','task','ROOT','{}')"
            )

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="test", entity_id="A",
            event_type="test.dependency", payload={}, operation=dependency,
        )
        self.assertEqual(self.read()["safe_parallel_groups"], [])
        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="test", entity_id="ROOT",
            event_type="test.complete_root", payload={},
            operation=lambda conn, revision: conn.execute(
                "UPDATE tasks SET status='done',result='validated',completion_revision=?,revision=? WHERE id='ROOT'",
                (revision, revision),
            ),
        )
        self.assertEqual(self.read()["safe_parallel_groups"], [["A-LANE", "B-LANE"]])

        def draft_interface(conn, revision):
            conn.execute(
                "INSERT INTO interfaces(id,owner_task_id,state,version,revision) "
                "VALUES('API','ROOT','draft','1',?)", (revision,),
            )
            conn.execute(
                "INSERT INTO interface_consumers(interface_id,task_id,required_state,required_version) "
                "VALUES('API','A','frozen','1')"
            )

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="test", entity_id="API",
            event_type="test.interface", payload={}, operation=draft_interface,
        )
        self.assertEqual(self.read()["safe_parallel_groups"], [])
        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="test", entity_id="API",
            event_type="test.freeze_interface", payload={},
            operation=lambda conn, revision: conn.execute(
                "UPDATE interfaces SET state='frozen',revision=? WHERE id='API'", (revision,)
            ),
        )
        self.assertEqual(self.read()["safe_parallel_groups"], [["A-LANE", "B-LANE"]])

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="test", entity_id="B",
            event_type="test.scope_conflict", payload={},
            operation=lambda conn, revision: (
                conn.execute("DELETE FROM ownership_scopes WHERE task_id='B'"),
                conn.execute("INSERT INTO ownership_scopes(task_id,mode,path) VALUES('B','exclusive','src/a/child')"),
            ),
        )
        self.assertEqual(self.read()["safe_parallel_groups"], [])

        def resource_conflict(conn, revision):
            conn.execute("DELETE FROM ownership_scopes WHERE task_id='B'")
            conn.execute("INSERT INTO ownership_scopes(task_id,mode,path) VALUES('B','exclusive','src/b')")
            conn.execute("INSERT INTO resource_classes(id,mode) VALUES('gpu','exclusive')")
            conn.execute("INSERT INTO resource_instances(id,class_id,capacity,enabled) VALUES('gpu:0','gpu',1,1)")
            conn.execute("INSERT INTO resource_requests(id,task_id,phase,selector,amount,mode,required) VALUES('RA','A','claim','gpu:any',1,'exclusive',1)")
            conn.execute("INSERT INTO resource_requests(id,task_id,phase,selector,amount,mode,required) VALUES('RB','B','claim','gpu:any',1,'exclusive',1)")

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="test", entity_id="gpu:0",
            event_type="test.resource_conflict", payload={}, operation=resource_conflict,
        )
        self.assertEqual(self.read()["safe_parallel_groups"], [])

    def test_patch_artifacts_and_pending_patches_are_normalized(self) -> None:
        def insert_artifact(conn, revision):
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            conn.execute(
                "INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,state,"
                "integration_task_id,created_at,updated_at) VALUES('WS','repo','RUN','A-LANE','isolated_merge',"
                "'base','ready','ROOT',?,?)", (now, now),
            )
            conn.execute(
                "INSERT INTO workflow_patch_artifacts(id,workspace_id,task_id,kind,artifact_ref,content_hash,"
                "base_commit,created_at,state) VALUES('PATCH','WS','A','patch','artifacts/a.patch','hash','base',?,'pending')",
                (now,),
            )

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="test", entity_id="PATCH",
            event_type="test.patch", payload={}, operation=insert_artifact,
        )
        state = self.read()
        self.assertEqual(state["patch_artifacts"][0]["lane_id"], "A-LANE")
        self.assertEqual([item["id"] for item in state["pending_patches"]], ["PATCH"])
        self.assertNotIn("token", str(state).lower())

    def test_recovery_needed_includes_expired_authoritative_leases(self) -> None:
        claimed = self.repo.service.continue_work(task_id="A")
        claim_id = claimed["claim"]["claim_id"]

        def expire(conn, revision):
            conn.execute("UPDATE claims SET expires_at=? WHERE id=?", (past(), claim_id))
            conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=(SELECT session_id FROM claims WHERE id=?)", (past(), claim_id))

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="test", entity_id=claim_id,
            event_type="test.expire", payload={}, operation=expire,
        )
        needed = self.read()["recovery_needed"]
        kinds = {item["kind"] for item in needed}
        self.assertIn("claim", kinds)
        self.assertIn("session", kinds)

    def test_batch_wave_checkpoints_and_inflight_mutations_are_recovery_visible(self) -> None:
        def seed(conn, revision):
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            for workspace_id, lane_id, state in (("W-APPLIED", "A-LANE", "applied_pending_wave"), ("W-APPLYING", "B-LANE", "applying"), ("W-FINALIZING", "ROOT-LANE", "finalizing")):
                conn.execute(
                    "INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,state,created_at,updated_at) "
                    "VALUES(?, 'repo', 'RUN', ?, 'isolated_merge', 'base', ?, ?, ?)",
                    (workspace_id, lane_id, state, now, now),
                )
            for position, (queue_id, state) in enumerate((("Q-APPLIED", "applied_pending_wave"), ("Q-APPLYING", "applying"), ("Q-FINALIZING", "finalizing")), start=1):
                conn.execute(
                    "INSERT INTO workflow_patch_artifacts(id,workspace_id,task_id,kind,artifact_ref,content_hash,base_commit,created_at,state) "
                    "VALUES(?, 'W-APPLIED', 'A', 'patch', ?, 'hash', 'base', ?, 'queued')",
                    ("P-" + queue_id, queue_id + ".patch", now),
                )
                conn.execute(
                    "INSERT INTO workflow_integration_queue(id,run_id,patch_artifact_id,integration_task_id,integrator_lane_id,position,state,conflict_json,created_at,updated_at) "
                    "VALUES(?, 'RUN', ?, 'ROOT', 'ROOT-LANE', ?, ?, '{}', ?, ?)",
                    (queue_id, "P-" + queue_id, position, state, now, now),
                )

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="test", entity_id="batch-wave",
            event_type="test.batch_wave_recovery", payload={}, operation=seed,
        )
        needed = self.read()["recovery_needed"]
        self.assertEqual(
            {item["id"] for item in needed if item["kind"] == "workspace"},
            {"W-APPLIED", "W-APPLYING", "W-FINALIZING"},
        )
        self.assertEqual(
            {item["id"] for item in needed if item["kind"] == "integration"},
            {"Q-APPLIED", "Q-APPLYING", "Q-FINALIZING"},
        )

    def test_recovery_needed_excludes_terminal_history_and_inert_records(self) -> None:
        def historical_records(conn, revision):
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            conn.execute(
                "UPDATE tasks SET status='done',result='validated',completion_revision=?,revision=? WHERE id='A'",
                (revision, revision),
            )
            conn.execute(
                "INSERT INTO gates(id,task_id,type,config_json,required,status,valid,revision) "
                "VALUES('A-HISTORICAL','A','manual','{}',1,'invalidated',0,?)",
                (revision,),
            )
            conn.execute("UPDATE sessions SET last_seen_at=?", (past(),))
            conn.execute(
                "INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,state,"
                "created_at,updated_at) VALUES('OLD-WS','repo','RUN','A-LANE','exclusive','base','quarantined',?,?)",
                (now, now),
            )

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="test", entity_id="history",
            event_type="test.historical_recovery_records", payload={}, operation=historical_records,
        )
        needed = self.read()["recovery_needed"]
        self.assertNotIn("A-HISTORICAL", {item["id"] for item in needed})
        self.assertNotIn("OLD-WS", {item["id"] for item in needed})
        self.assertNotIn("session", {item["kind"] for item in needed})


if __name__ == "__main__":
    unittest.main()
