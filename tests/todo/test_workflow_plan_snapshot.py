from __future__ import annotations

import copy
import json
import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.projections import build_snapshot
from todo_orchestrator.models import TodoError
from todo_orchestrator.readiness import explain_task, ready_tasks


class WorkflowPlanSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.repo = V2Repo()

    def tearDown(self):
        self.repo.close()

    def _workspace_plan(self):
        plan = base_plan([safe_task("A", "src/a")])
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "A", "charter": {"objective": "bounded"},
            "lanes": [
                {"id": "ROOT", "role": "coordinator", "tasks": []},
                {"id": "I", "parent_lane_id": "ROOT", "role": "integrator",
                 "tasks": ["A"], "workspace": {"mode": "isolated_merge"}},
            ],
        }]
        self.repo.apply(plan)
        updated = copy.deepcopy(plan)
        updated["runs"][0]["lanes"][1]["workspace"]["mode"] = "exclusive"
        return plan, updated

    def _fixture(self, operation):
        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="I",
            event_type="fixture.workspace_mode", payload={}, operation=operation,
        )

    def test_v3_plan_updates_idle_unprovisioned_lane_and_versions_context(self):
        _, updated = self._workspace_plan()
        with self.repo.service.db.read() as conn:
            before = conn.execute("SELECT revision FROM workflow_lanes WHERE id='I'").fetchone()[0]
        self.repo.apply(updated)
        with self.repo.service.db.read() as conn:
            lane = conn.execute("SELECT * FROM workflow_lanes WHERE id='I'").fetchone()
            briefs = conn.execute("SELECT version,content_json FROM workflow_context_fragments WHERE lane_id='I' AND kind='lane_brief' ORDER BY version").fetchall()
            self.assertEqual("exclusive", lane["workspace_mode"])
            self.assertGreater(lane["revision"], before)
            self.assertEqual(["isolated_merge", "exclusive"], [json.loads(row["content_json"])["workspace_mode"] for row in briefs])
            self.assertEqual([1, 2], [row["version"] for row in briefs])
        self.repo.apply(updated)
        with self.repo.service.db.read() as conn:
            self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM workflow_context_fragments WHERE lane_id='I' AND kind='lane_brief'").fetchone()[0])
            self.assertEqual(("A", "queued"), tuple(conn.execute("SELECT task_id,state FROM workflow_lane_tasks WHERE lane_id='I'").fetchone()))

    def test_generated_briefs_preserve_architectural_intent_without_procedural_rewrite(self):
        plan = base_plan([safe_task("A", "src/a")])
        plan["schema_version"] = 3
        plan["tasks"][0].update({"motivation": "avoid repeated archaeology", "rationale": "root retains judgment", "delegated_choices": ["local decomposition"], "references": ["design/intent.md"]})
        plan["runs"] = [{"id": "RUN", "root_task_id": "A", "charter": {"objective": "bounded"}, "lanes": [
            {"id": "ROOT", "role": "coordinator", "tasks": [], "motivation": "integrate intent", "uncertainties": ["source shape may differ"]},
            {"id": "I", "parent_lane_id": "ROOT", "role": "integrator", "tasks": ["A"], "desired_end_state": "durable outcome", "delegated_choices": ["implementation details"]},
        ]}]
        self.repo.apply(plan)
        with self.repo.service.db.read() as conn:
            brief = json.loads(conn.execute("SELECT content_json FROM workflow_context_fragments WHERE kind='task_brief' AND task_id='A'").fetchone()[0])
        self.assertEqual(brief["motivation"], "avoid repeated archaeology")
        self.assertEqual(brief["delegated_choices"], ["local decomposition"])

    def test_v3_mode_update_refuses_closed_or_active_lane_and_rolls_back(self):
        _, updated = self._workspace_plan()
        for state in ("closed", "cancelled", "active", "active_lane"):
            with self.subTest(state=state):
                def seed(conn, revision):
                    conn.execute("UPDATE workflow_lanes SET state=? WHERE id='I'", ("active" if state == "active_lane" else state if state != "active" else "ready",))
                    conn.execute("UPDATE workflow_lane_tasks SET state=? WHERE lane_id='I'", ("active" if state == "active" else "queued",))
                self._fixture(seed)
                with self.assertRaises(TodoError) as denied:
                    self.repo.apply(updated)
                self.assertEqual("workflow_lane_workspace_in_use" if state in {"active", "active_lane"} else "workflow_lane_closed", denied.exception.code)
                with self.repo.service.db.read() as conn:
                    self.assertEqual("isolated_merge", conn.execute("SELECT workspace_mode FROM workflow_lanes WHERE id='I'").fetchone()[0])
                    self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM workflow_context_fragments WHERE lane_id='I' AND kind='lane_brief'").fetchone()[0])

    def test_v3_mode_update_refuses_any_existing_workspace(self):
        _, updated = self._workspace_plan()
        self._fixture(lambda conn, revision: conn.execute(
            "INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,state,created_at,updated_at) VALUES('W','repo','RUN','I','isolated_merge','base','cleaned','now','now')"
        ).rowcount)
        with self.assertRaises(TodoError) as denied:
            self.repo.apply(updated)
        self.assertEqual("workflow_lane_workspace_in_use", denied.exception.code)

    def test_v3_mode_update_preserves_identity_refusals(self):
        _, updated = self._workspace_plan()
        for key, value in (("role", "implementer"), ("parent_lane_id", None)):
            with self.subTest(key=key):
                invalid = copy.deepcopy(updated)
                invalid["runs"][0]["lanes"][1][key] = value
                with self.assertRaises(TodoError):
                    self.repo.apply(invalid)
                with self.repo.service.db.read() as conn:
                    self.assertEqual(("RUN", "ROOT", "integrator", "isolated_merge"), tuple(conn.execute("SELECT run_id,parent_lane_id,role,workspace_mode FROM workflow_lanes WHERE id='I'").fetchone()))

    def test_v2_plan_normalizes_to_one_serial_compatibility_lane(self):
        self.repo.apply(base_plan([safe_task("A", "src/a"), safe_task("B", "src/b")]))
        with self.repo.service.db.read() as conn:
            run = conn.execute("SELECT id,status FROM workflow_runs").fetchall()
            queue = conn.execute("SELECT lane_id,position,task_id FROM workflow_lane_tasks ORDER BY position").fetchall()
        self.assertEqual([tuple(row) for row in run], [("compat-v2", "active")])
        self.assertEqual([tuple(row) for row in queue], [("compat-v2-main", 0, "A"), ("compat-v2-main", 1, "B")])
        with self.repo.service.db.read() as conn:
            fragments = conn.execute(
                "SELECT kind,lane_id,task_id,owner_scope_json FROM workflow_context_fragments ORDER BY kind,task_id"
            ).fetchall()
        self.assertEqual({row["kind"] for row in fragments}, {"run_charter", "lane_brief", "task_brief"})
        self.assertTrue(all(row["owner_scope_json"] for row in fragments))

    def test_plan_reapply_never_downgrades_a_frozen_interface(self):
        contract = self.repo.root / "contracts" / "api.txt"
        contract.parent.mkdir(parents=True)
        contract.write_text("v1\n", encoding="utf-8")
        plan = base_plan(
            [safe_task("A", "contracts")],
            interfaces=[{"id": "API", "owner_task_id": "A", "contract_paths": ["contracts/api.txt"]}],
        )
        self.repo.apply(plan)
        capsule = self.repo.service.continue_work(task_id="A")
        self.repo.service.interface("freeze", "API", "1", capsule["claim"]["claim_token"])
        self.repo.apply(plan)
        status = self.repo.service.interface("status", "API")
        self.assertEqual((status["state"], status["version"]), ("frozen", "1"))
        self.assertTrue(status["content_hash"])

    def test_plan_reapply_accepts_unchanged_tasks_in_a_closed_lane(self):
        plan = base_plan([safe_task("A", "src/a")])
        self.repo.apply(plan)

        def close_lane(conn, revision):
            conn.execute("UPDATE workflow_lanes SET state='closed',revision=? WHERE id='compat-v2-main'", (revision,))
            conn.execute("UPDATE workflow_lane_tasks SET state='completed',revision=? WHERE lane_id='compat-v2-main'", (revision,))

        self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="fixture",
            entity_id="compat-v2-main",
            event_type="fixture.lane.closed",
            payload={},
            operation=close_lane,
        )
        self.repo.apply(plan)
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM workflow_lanes WHERE id='compat-v2-main'").fetchone()[0], "closed")
            self.assertEqual(conn.execute("SELECT state FROM workflow_lane_tasks WHERE lane_id='compat-v2-main'").fetchone()[0], "completed")

    def test_v3_plan_reapply_reconciles_idle_lane_order_and_preserves_omitted_tasks(self):
        tasks = [safe_task("A", "src/a"), safe_task("B", "src/b"), safe_task("C", "src/c")]
        plan = base_plan(tasks)
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "A",
            "charter": {"objective": "bounded"},
            "lanes": [{"id": "ROOT", "role": "coordinator", "tasks": ["A", "B", "C"]}],
        }]
        self.repo.apply(plan)

        reordered = base_plan(tasks)
        reordered["schema_version"] = 3
        reordered["runs"] = [{
            "id": "RUN", "root_task_id": "A",
            "charter": {"objective": "bounded"},
            "lanes": [{"id": "ROOT", "role": "coordinator", "tasks": ["C", "A"]}],
        }]
        self.repo.apply(reordered)

        with self.repo.service.db.read() as conn:
            queue = conn.execute(
                "SELECT position,task_id,state FROM workflow_lane_tasks WHERE lane_id='ROOT' ORDER BY position"
            ).fetchall()
        self.assertEqual(
            [(0, "C", "queued"), (1, "A", "queued"), (2, "B", "queued")],
            [tuple(row) for row in queue],
        )

    def test_v3_plan_reapply_refuses_to_reorder_active_lane(self):
        tasks = [safe_task("A", "src/a"), safe_task("B", "src/b")]
        plan = base_plan(tasks)
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "A",
            "charter": {"objective": "bounded"},
            "lanes": [{"id": "ROOT", "role": "coordinator", "tasks": ["A", "B"]}],
        }]
        self.repo.apply(plan)

        def activate_lane(conn, revision):
            conn.execute(
                "UPDATE workflow_lane_tasks SET state='active',revision=? WHERE lane_id='ROOT' AND task_id='A'",
                (revision,),
            )

        self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="fixture",
            entity_id="ROOT",
            event_type="fixture.lane.active",
            payload={},
            operation=activate_lane,
        )
        reordered = base_plan(tasks)
        reordered["schema_version"] = 3
        reordered["runs"] = [{
            "id": "RUN", "root_task_id": "A",
            "charter": {"objective": "bounded"},
            "lanes": [{"id": "ROOT", "role": "coordinator", "tasks": ["B", "A"]}],
        }]
        with self.assertRaises(TodoError) as caught:
            self.repo.apply(reordered)
        self.assertEqual("workflow_lane_order_in_use", caught.exception.code)

        with self.repo.service.db.read() as conn:
            queue = conn.execute(
                "SELECT position,task_id,state FROM workflow_lane_tasks WHERE lane_id='ROOT' ORDER BY position"
            ).fetchall()
        self.assertEqual([(0, "A", "active"), (1, "B", "queued")], [tuple(row) for row in queue])

    def test_aggregate_epic_unlocks_only_after_all_direct_children_complete(self):
        tasks = [
            {**safe_task("ROOT", "root"), "kind": "epic"},
            {**safe_task("GROUP", "group"), "kind": "workstream", "parent_id": "ROOT"},
            {**safe_task("LEAF", "leaf"), "parent_id": "GROUP"},
        ]
        plan = base_plan(tasks)
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "ROOT",
            "charter": {"objective": "bounded"},
            "lanes": [{"id": "ROOT-L", "role": "coordinator", "tasks": ["GROUP", "ROOT"]}],
        }]
        self.repo.apply(plan)
        with self.repo.service.db.read() as conn:
            self.assertFalse(explain_task(conn, "GROUP")["ready"])
            self.assertFalse(explain_task(conn, "ROOT")["ready"])

        def complete_leaf(conn, revision):
            conn.execute("UPDATE tasks SET status='done',revision=? WHERE id='LEAF'", (revision,))

        self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="fixture",
            entity_id="LEAF",
            event_type="fixture.task.completed",
            payload={},
            operation=complete_leaf,
        )
        with self.repo.service.db.read() as conn:
            self.assertTrue(explain_task(conn, "GROUP")["ready"])
            self.assertFalse(explain_task(conn, "ROOT")["ready"])
            self.assertIn("GROUP", {item["task_id"] for item in ready_tasks(conn)})

        def complete_group(conn, revision):
            conn.execute("UPDATE tasks SET status='done',revision=? WHERE id='GROUP'", (revision,))

        self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="fixture",
            entity_id="GROUP",
            event_type="fixture.task.completed",
            payload={},
            operation=complete_group,
        )
        with self.repo.service.db.read() as conn:
            self.assertTrue(explain_task(conn, "ROOT")["ready"])
            self.assertIn("ROOT", {item["task_id"] for item in ready_tasks(conn)})

    def test_explicit_v3_fragments_include_owner_scope(self):
        plan = base_plan([safe_task("A", "src/a")])
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "A",
            "charter": {"objective": "bounded"},
            "lanes": [{"id": "ROOT", "role": "coordinator", "tasks": ["A"]}],
            "context_fragments": [{"kind": "decision_ledger", "content": {"decisions": []}}],
        }]
        self.repo.apply(plan)
        with self.repo.service.db.read() as conn:
            rows = conn.execute("SELECT kind,owner_scope_json FROM workflow_context_fragments WHERE run_id='RUN'").fetchall()
        self.assertIn("decision_ledger", {row["kind"] for row in rows})
        self.assertTrue(all(row["owner_scope_json"] for row in rows))

    def test_workflow_snapshot_is_deterministic_and_excludes_volatile_authority(self):
        self.repo.apply(base_plan([safe_task("A", "src/a")]))
        with self.repo.service.db.read() as conn:
            first = build_snapshot(conn, self.repo.service.project)
            second = build_snapshot(conn, self.repo.service.project)
        self.assertEqual(first, second)
        self.assertEqual(first["snapshot_version"], 3)
        names = set(first["tables"])
        self.assertIn("workflow_runs", names)
        self.assertIn("workflow_lanes", names)
        self.assertNotIn("workflow_capabilities", names)
        self.assertNotIn("workflow_dispatches", names)
        self.assertNotIn("workflow_child_result_candidates", names)


if __name__ == "__main__":
    unittest.main()
