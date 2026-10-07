from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.front_door import require_mutation_route
from todo_orchestrator.models import TodoError
from todo_orchestrator.projections import atomic_write_json
from todo_orchestrator.service import Service
from todo_orchestrator.workflow.capabilities import WorkflowCapabilityLocator
from todo_orchestrator.workflow.context_fragments import ContextFragmentStore, FragmentOwner
from todo_orchestrator.workflow.protocol import WorkflowProtocol
from todo_orchestrator.workflow.service import WorkflowKernel
from todo_orchestrator.workflow.service import repository_identity
from todo_orchestrator.workflow.workspaces import WorkspaceService


class WorkflowFrontDoorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        self.repo.apply(base_plan([safe_task("A", "src/a")]))

    def tearDown(self) -> None:
        self.repo.close()

    def migrate_identity(self, front_door: str = "project-control") -> None:
        identity_path = self.repo.root / ".todo-orchestrator" / "project.json"
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        identity["configuration"]["workflow_front_door"] = front_door
        atomic_write_json(identity_path, identity)

    def test_legacy_project_keeps_direct_automated_compatibility(self) -> None:
        claimed = self.repo.service.continue_work(task_id="A")
        self.assertEqual(claimed["claim"]["task_id"], "A")

    def test_migrated_project_blocks_direct_automated_mutation_but_not_reads(self) -> None:
        self.migrate_identity()
        service = Service(self.repo.root)
        self.assertEqual(service.status()["project_revision"], service.db.revision())
        with self.assertRaises(TodoError) as caught:
            service.continue_work(task_id="A")
        self.assertEqual(caught.exception.code, "workflow_front_door_required")
        self.assertEqual(caught.exception.details["workflow_front_door"], "project-control")
        self.assertTrue(caught.exception.details["read_only_commands_available"])

    def test_cli_returns_stable_front_door_result(self) -> None:
        self.migrate_identity()
        status_process, status = self.repo.run("status")
        self.assertEqual(status_process.returncode, 0)
        self.assertTrue(status["ok"])
        claim_process, claim = self.repo.run("continue", "--task-id", "A")
        self.assertNotEqual(claim_process.returncode, 0)
        self.assertFalse(claim["ok"])
        self.assertEqual(claim["code"], "workflow_front_door_required")
        self.assertNotIn("token", json.dumps(claim).lower())

    def test_explicit_test_and_self_debug_modes_remain_available(self) -> None:
        self.migrate_identity()
        for mode in ("test", "self_debug"):
            require_mutation_route(
                {"configuration": {"workflow_front_door": "project-control"}},
                operation="plan.applied",
                mutation_mode=mode,
                interactive=False,
            )
        claimed = Service(self.repo.root, mutation_mode="test").continue_work(task_id="A")
        self.assertEqual(claimed["claim"]["task_id"], "A")

    def test_interactive_owner_maintenance_has_no_approval_token(self) -> None:
        project = {"configuration": {"workflow_front_door": "project-control"}}
        require_mutation_route(project, operation="plan.applied", interactive=True)
        with self.assertRaises(TodoError) as caught:
            require_mutation_route(project, operation="plan.applied", interactive=False)
        self.assertEqual(caught.exception.code, "workflow_front_door_required")
        self.assertNotIn("token", json.dumps(caught.exception.details).lower())

    def test_canonical_in_process_workflow_is_permitted(self) -> None:
        self.migrate_identity()
        locator_dir = tempfile.TemporaryDirectory()
        try:
            locator = WorkflowCapabilityLocator(Path(locator_dir.name))
            protocol = WorkflowProtocol(WorkflowKernel(locator=locator), locator)
            claimed = protocol.next_task(repo_root=str(self.repo.root), task_id="A")
            self.assertEqual(claimed["status"], "claimed")
            self.assertEqual(claimed["task_id"], "A")
            with self.repo.service.db.read() as conn:
                claim = conn.execute("SELECT owner_system FROM claims WHERE state='active'").fetchone()
                self.assertEqual(claim["owner_system"], "project-control")
        finally:
            locator_dir.cleanup()

    def test_oversized_charter_has_a_public_paged_retrieval_journey(self) -> None:
        self.migrate_identity()
        charter, _ = ContextFragmentStore(self.repo.service.db).publish(
            actor_session_id=None, owner=FragmentOwner("compat-v2"), kind="run_charter",
            content={"objective": "required constraint " + "x" * 14000},
        )
        locator_dir = tempfile.TemporaryDirectory()
        try:
            locator = WorkflowCapabilityLocator(Path(locator_dir.name))
            protocol = WorkflowProtocol(WorkflowKernel(locator=locator), locator)
            claimed = protocol.next_task(repo_root=str(self.repo.root), task_id="A")
            self.assertEqual(claimed["status"], "needs_context")
            synced = protocol.coordinate_task(
                workflow_handle=claimed["workflow_handle"], action="sync",
                payload={"cursor": 0, "known_fragments": {charter.id: 0}},
            )
            self.assertEqual(synced["context_delta"]["requested_cursor"], 0)
            self.assertEqual(synced["context_delta"]["cursor"], synced["cursor"])
            self.assertIn(charter.id, [item["fragment_id"] for item in synced["context_delta"]["changed_fragments"]])
            call = claimed["context_receipt"]["next_call"]
            pages: list[str] = []
            while call is not None:
                inspected = protocol.inspect_task(**call)
                self.assertLessEqual(len(json.dumps(inspected, separators=(",", ":")).encode()), call["budget_bytes"])
                pages.append(inspected["page"]["content"])
                next_target = inspected["page"]["next_target"]
                call = {**call, "target": next_target} if next_target else None
            self.assertIn("required constraint", "".join(pages))
        finally:
            locator_dir.cleanup()

    def test_managed_lane_returns_root_workspace_preparation_action(self) -> None:
        self.migrate_identity()
        self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="fixture",
            entity_id="compat-v2-main",
            event_type="fixture.workspace_contract",
            payload={},
            operation=lambda conn, revision: conn.execute(
                "UPDATE workflow_lanes SET workspace_mode='isolated_merge',revision=? "
                "WHERE id='compat-v2-main'",
                (revision,),
            ),
        )
        locator_dir = tempfile.TemporaryDirectory()
        try:
            locator = WorkflowCapabilityLocator(Path(locator_dir.name))
            protocol = WorkflowProtocol(WorkflowKernel(locator=locator), locator)
            result = protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        finally:
            locator_dir.cleanup()
        self.assertEqual(result["status"], "root_preparation_required")
        self.assertEqual(result["warnings"], ["workflow_workspace_required"])
        self.assertEqual(result["root_action"], {
            "operation": "prepare_run_workspaces",
            "authority": "root_only",
            "run_id": "compat-v2",
            "lane_id": "compat-v2-main",
            "task_id": "A",
            "workspace_mode": "isolated_merge",
        })
        self.assertEqual(result["allowed_actions"], [])
        with self.repo.service.db.read() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM claims WHERE state='active'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM workflow_dispatches WHERE state='active'").fetchone())

    def test_legacy_front_door_alias_remains_accepted_during_migration(self) -> None:
        self.migrate_identity("coding-workflow")
        with self.assertRaises(TodoError) as caught:
            Service(self.repo.root).continue_work(task_id="A")
        self.assertEqual(caught.exception.code, "workflow_front_door_required")
        self.assertEqual(caught.exception.details["workflow_front_door"], "coding-workflow")
        self.assertEqual(caught.exception.details["canonical_workflow_front_door"], "project-control")

    def test_isolated_completion_queues_and_integrator_run_gates_consumes_commit(self) -> None:
        self.repo.close()
        self.repo = V2Repo()
        task = safe_task("A", "src/a", gates=[{
            "id": "A-GATE",
            "type": "command",
            "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('src/a/result.txt').read_text() == 'producer\\n'"],
            "input_paths": ["src/a/result.txt"],
            "required": True,
        }])
        self.repo.apply(base_plan([task]))
        (self.repo.root / "src" / "a").mkdir(parents=True)
        subprocess.run(["git", "-C", str(self.repo.root), "config", "user.email", "workflow@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo.root), "config", "user.name", "Workflow Test"], check=True)
        subprocess.run(["git", "-C", str(self.repo.root), "add", ".todo-orchestrator", "plan.json", "src"], check=True)
        subprocess.run(["git", "-C", str(self.repo.root), "commit", "-qm", "base"], check=True)
        base = subprocess.check_output(["git", "-C", str(self.repo.root), "rev-parse", "HEAD"], text=True).strip()

        def seed(conn, revision):
            now = "2026-08-30T00:00:00Z"
            conn.execute(
                "INSERT INTO tasks(id,kind,title,status,created_at,updated_at,revision) VALUES('INTEGRATE','integration_task','Integrate','planned',?,?,?)",
                (now, now, revision),
            )
            conn.execute("UPDATE workflow_lanes SET workspace_mode='isolated_merge' WHERE id='compat-v2-main'")
            conn.execute(
                "INSERT INTO workflow_lanes(id,run_id,parent_lane_id,role,state,workspace_mode,created_at,updated_at,revision) "
                "VALUES('INTEGRATOR','compat-v2','compat-v2-main','integrator','ready','exclusive',?,?,?)",
                (now, now, revision),
            )
            conn.execute(
                "INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) "
                "VALUES('INTEGRATOR',0,'INTEGRATE','queued',?,?)",
                (now, revision),
            )
            conn.execute(
                "INSERT INTO gates(id,task_id,type,config_json,required,status,valid,revision) "
                "VALUES('INTEGRATE-GATE','INTEGRATE','command',?,1,'pending',0,?)",
                (json.dumps({
                    "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('src/a/result.txt').read_text() == 'producer\\n'"],
                    "input_paths": ["src/a/result.txt"],
                }, sort_keys=True), revision),
            )
            return {"task_id": "A"}

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="workspace",
            event_type="fixture.workspace", payload={}, operation=seed,
        )
        identity = repository_identity(self.repo.root, str(self.repo.service.project["project_uuid"]))
        manager = WorkspaceService(
            self.repo.service.db,
            managed_root=self.repo.service.paths.state_dir / "workflow-workspaces",
            repository_identity_resolver=lambda root: repository_identity(
                root, str(self.repo.service.project["project_uuid"])
            ),
        )
        manager.create_workspace(
            repository_root=self.repo.root, repository_identity=identity, run_id="compat-v2",
            lane_id="INTEGRATOR", mode="exclusive", base_commit=base,
            worktree_path=self.repo.service.paths.state_dir / "workflow-workspaces" / "destination",
            branch="test-integration", integration_task_id="INTEGRATE",
        )
        producer = manager.create_workspace(
            repository_root=self.repo.root, repository_identity=identity, run_id="compat-v2",
            lane_id="compat-v2-main", mode="isolated_merge", base_commit=base,
            worktree_path=self.repo.service.paths.state_dir / "workflow-workspaces" / "producer",
            branch="test-producer", integration_task_id="INTEGRATE",
        )
        producer_root = Path(str(producer["worktree_path"]))
        (producer_root / "src" / "a").mkdir(parents=True, exist_ok=True)
        (producer_root / "src" / "a" / "result.txt").write_text("producer\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(producer_root), "add", "src/a/result.txt"], check=True)
        subprocess.run(["git", "-C", str(producer_root), "commit", "-qm", "producer"], check=True)
        producer_head = subprocess.check_output(["git", "-C", str(producer_root), "rev-parse", "HEAD"], text=True).strip()

        locator_dir = tempfile.TemporaryDirectory()
        self.addCleanup(locator_dir.cleanup)
        locator = WorkflowCapabilityLocator(Path(locator_dir.name))
        protocol = WorkflowProtocol(WorkflowKernel(locator=locator), locator)
        claimed = protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        claimed_capability = locator.resolve(
            claimed["workflow_handle"], required_operation="finish_task"
        )
        finished = protocol.finish_task(
            workflow_handle=claimed["workflow_handle"], action="complete", disposition="implemented"
        )
        self.assertEqual(finished["artifact"]["artifact_ref"], producer_head)
        self.assertEqual(finished["integration_task_id"], "INTEGRATE")
        with self.repo.service.db.read() as conn:
            evidence = conn.execute("SELECT metadata_json FROM evidence WHERE gate_id='A-GATE'").fetchone()
            metadata = json.loads(evidence["metadata_json"])
            self.assertEqual(Path(metadata["workspace_path"]), producer_root.resolve())
            queued = conn.execute(
                "SELECT q.state,a.artifact_ref FROM workflow_integration_queue q "
                "JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id"
            ).fetchone()
            self.assertEqual((queued["state"], queued["artifact_ref"]), ("queued", producer_head))

        # A completion precondition can legitimately require one more commit
        # after an artifact was queued. Refresh only a clean descendant and
        # preserve the original artifact as superseded history.
        (producer_root / "src" / "a" / "followup.txt").write_text("published interface\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(producer_root), "add", "src/a/followup.txt"], check=True)
        subprocess.run(["git", "-C", str(producer_root), "commit", "-qm", "publish interface"], check=True)
        refreshed_head = subprocess.check_output(
            ["git", "-C", str(producer_root), "rev-parse", "HEAD"], text=True
        ).strip()
        with self.repo.service.db.read() as conn:
            workspace = dict(conn.execute(
                "SELECT * FROM workflow_workspaces WHERE lane_id='compat-v2-main'"
            ).fetchone())
        refreshed = WorkflowKernel._publish_and_queue_workspace_artifact(
            self.repo.service, claimed_capability.lineage, workspace
        )
        self.assertEqual(refreshed["artifact"]["artifact_ref"], refreshed_head)
        with self.repo.service.db.read() as conn:
            artifacts = conn.execute(
                "SELECT state,artifact_ref FROM workflow_patch_artifacts"
            ).fetchall()
            self.assertEqual(
                {row["artifact_ref"]: row["state"] for row in artifacts},
                {producer_head: "superseded", refreshed_head: "queued"},
            )

        integrator = protocol.next_task(repo_root=str(self.repo.root), task_id="INTEGRATE")
        self.assertEqual(integrator["role"], "integrator")
        destination = self.repo.service.paths.state_dir / "workflow-workspaces" / "destination"
        dirty = destination / "dirty.txt"
        dirty.write_text("preserve\n", encoding="utf-8")
        with self.assertRaises(TodoError) as caught:
            protocol.coordinate_task(
                workflow_handle=integrator["workflow_handle"], action="run_gates", payload={"required": True}
            )
        self.assertEqual(caught.exception.code, "integration_workspace_dirty")
        dirty.unlink()
        integrated = protocol.coordinate_task(
            workflow_handle=integrator["workflow_handle"], action="run_gates", payload={"required": True}
        )
        self.assertEqual((integrated["status"], integrated["operation_status"]), ("claimed", "passed"))
        self.assertEqual(len(integrated["integration"]), 1)
        self.assertEqual(integrated["integration"][0]["state"], "integrated")
        self.assertEqual((destination / "src" / "a" / "result.txt").read_text(), "producer\n")
        self.assertEqual((destination / "src" / "a" / "followup.txt").read_text(), "published interface\n")
        self.assertEqual(subprocess.check_output(["git", "-C", str(destination), "status", "--porcelain"], text=True), "")
        handed_off_integrator = protocol.finish_task(
            workflow_handle=integrator["workflow_handle"], action="handoff", reason="resume integrated workspace"
        )
        self.assertEqual(handed_off_integrator["status"], "idle")
        resumed_integrator = protocol.next_task(repo_root=str(self.repo.root), task_id="INTEGRATE")
        with self.repo.service.db.read() as conn:
            rebound = conn.execute(
                "SELECT workspace_id FROM workflow_dispatches WHERE lane_id='INTEGRATOR' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(rebound["workspace_id"])
        finished_integrator = protocol.finish_task(
            workflow_handle=resumed_integrator["workflow_handle"], action="complete", disposition="implemented"
        )
        self.assertEqual(finished_integrator["status"], "idle")


if __name__ == "__main__":
    unittest.main()
