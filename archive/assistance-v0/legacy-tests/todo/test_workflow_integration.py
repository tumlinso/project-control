from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.config import utc_now
from todo_orchestrator.models import TodoError
from todo_orchestrator.semantic import SemanticReader
from todo_orchestrator.service import Service
from todo_orchestrator.workflow.capabilities import WorkflowCapabilityLocator
from todo_orchestrator.workflow.context_fragments import ContextFragmentStore, FragmentOwner
from todo_orchestrator.workflow.protocol import WorkflowProtocol
from todo_orchestrator.workflow.protocol import _validate_action
from todo_orchestrator.workflow.service import WorkflowKernel
from todo_orchestrator.workflow.workspaces import WorkspaceService


class FakeLocalWorker:
    def delegate(self, **kwargs):
        return {"status": "running"}

    def collect(self, **kwargs):
        return {
            "status": "candidate_available",
            "kind": "source_finding",
            "result": {"summary": "bounded finding"},
            "artifacts": [],
        }


class UnavailableLocalWorker:
    def delegate(self, **kwargs):
        return {"status": "local_unavailable"}


class WorkflowKernelIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repo = V2Repo()
        (self.repo.root / "src" / "a").mkdir(parents=True)
        (self.repo.root / "src" / "a" / "unit.py").write_text("value = 1\n", encoding="utf-8")
        self.repo.apply(base_plan([safe_task("A", "src/a")]))
        self.locator_temp = tempfile.TemporaryDirectory()
        self.locator = WorkflowCapabilityLocator(Path(self.locator_temp.name))
        self.kernel = WorkflowKernel(locator=self.locator, local_worker_adapter=FakeLocalWorker())
        self.protocol = WorkflowProtocol(self.kernel, self.locator)

    def tearDown(self):
        self.repo.close()
        self.locator_temp.cleanup()

    def test_claim_sync_complete_are_one_in_process_semantic_path(self):
        claimed = self.protocol.next_task(repo_root=str(self.repo.root))
        self.assertEqual((claimed["status"], claimed["run_id"], claimed["lane_id"]), ("claimed", "compat-v2", "compat-v2-main"))
        self.assertLess(len(str(claimed).encode()), 8192)
        self.assertNotIn("claim_token", str(claimed))
        self.assertEqual(claimed["context"]["task_brief"]["objective"], "Implement A")
        self.assertIn("exclusive_paths", claimed["context"]["task_brief"]["scope"])
        handle = claimed["workflow_handle"]
        synced = self.protocol.coordinate_task(workflow_handle=handle, action="sync", payload={})
        self.assertEqual(synced["messages"], [])
        finished = self.protocol.finish_task(workflow_handle=handle, action="complete", disposition="implemented")
        self.assertEqual(finished["status"], "idle")
        with self.assertRaises(TodoError):
            self.locator.resolve(handle, required_operation="inspect_task")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id='A'").fetchone()[0], "done")
            self.assertEqual(conn.execute("SELECT state FROM workflow_lanes WHERE id='compat-v2-main'").fetchone()[0], "closed")

    def test_claim_owned_gate_binding_is_append_only_and_runnable(self):
        claimed = self.protocol.next_task(repo_root=str(self.repo.root))
        handle = claimed["workflow_handle"]
        spec = {"id": "A-EXISTS", "type": "file_exists", "path": "src/a/unit.py", "required": True}
        bound = self.protocol.coordinate_task(
            workflow_handle=handle, action="bind_required_gates", payload={"gates": [spec]},
        )
        self.assertEqual(bound["bound_gate_ids"], ["A-EXISTS"])
        repeated = self.protocol.coordinate_task(
            workflow_handle=handle, action="bind_required_gates", payload={"gates": [spec]},
        )
        self.assertEqual(repeated["unchanged_gate_ids"], ["A-EXISTS"])
        with self.assertRaisesRegex(TodoError, "cannot be replaced"):
            self.protocol.coordinate_task(
                workflow_handle=handle, action="bind_required_gates",
                payload={"gates": [{**spec, "path": "src/a/missing.py"}]},
            )
        gates = self.protocol.coordinate_task(workflow_handle=handle, action="run_gates", payload={"required": True})
        self.assertEqual(gates["status"], "claimed")
        self.assertEqual(gates["gates"][0]["gate_id"], "A-EXISTS")
        finished = self.protocol.finish_task(workflow_handle=handle, action="complete", disposition="implemented")
        self.assertEqual(finished["status"], "idle")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence WHERE gate_id='A-EXISTS'").fetchone()[0], 1)

    def test_stale_foreign_locator_hint_is_never_opened_by_capability_use(self):
        claimed = self.protocol.next_task(repo_root=str(self.repo.root))
        foreign = tempfile.TemporaryDirectory()
        self.addCleanup(foreign.cleanup)
        foreign_root = Path(foreign.name).resolve()
        (self.locator.root / "stale-foreign-hint").write_text(str(foreign_root), encoding="utf-8")
        opened: list[Path] = []
        original = self.kernel.service_factory

        def recording(root):
            opened.append(Path(root).resolve())
            return original(root)

        self.kernel.service_factory = recording
        synced = self.protocol.coordinate_task(
            workflow_handle=claimed["workflow_handle"], action="sync", payload={},
        )
        self.assertEqual(synced["status"], "claimed")
        self.assertNotIn(foreign_root, opened)

    def test_bound_static_gate_rechecks_its_implicit_path_and_repeat_binding_is_a_true_noop(self):
        claimed = self.protocol.next_task(repo_root=str(self.repo.root))
        handle = claimed["workflow_handle"]
        spec = {"id": "A-EXISTS", "type": "file_exists", "path": "src/a/unit.py", "required": True}
        self.protocol.coordinate_task(workflow_handle=handle, action="bind_required_gates", payload={"gates": [spec]})
        self.protocol.coordinate_task(workflow_handle=handle, action="run_gates", payload={"required": True})
        self.assertNotIn("gate_inputs_changed", [item["code"] for item in self.repo.service.audit()["discrepancies"]])
        revision = self.repo.service.db.revision()
        repeated = self.protocol.coordinate_task(workflow_handle=handle, action="bind_required_gates", payload={"gates": [spec]})
        self.assertEqual(repeated["unchanged_gate_ids"], ["A-EXISTS"])
        self.assertEqual(self.repo.service.db.revision(), revision)
        (self.repo.root / "src" / "a" / "unit.py").unlink()
        self.assertIn("gate_inputs_changed", [item["code"] for item in self.repo.service.audit()["discrepancies"]])
        rerun = self.protocol.coordinate_task(workflow_handle=handle, action="run_gates", payload={"required": True})
        self.assertEqual(rerun["status"], "blocked")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence WHERE gate_id='A-EXISTS'").fetchone()[0], 2)

    def test_live_gate_binding_rejects_broader_or_optional_scope(self):
        self.repo.apply(base_plan([safe_task("A", "src/a", scope={"exclusive_paths": ["src/a"], "forbidden_paths": ["src/a/private"]})]))
        claimed = self.protocol.next_task(repo_root=str(self.repo.root))
        handle = claimed["workflow_handle"]
        with self.assertRaisesRegex(TodoError, "invalid specifications"):
            self.protocol.coordinate_task(workflow_handle=handle, action="bind_required_gates", payload={"gates": [{"id": "BROAD", "type": "file_exists", "path": "src", "required": True}]})
        with self.assertRaisesRegex(TodoError, "required=true"):
            self.protocol.coordinate_task(workflow_handle=handle, action="bind_required_gates", payload={"gates": [{"id": "OPTIONAL", "type": "file_exists", "path": "src/a/unit.py", "required": False}]})

    def test_fresh_claim_receives_repaired_scope_and_consumed_interfaces(self):
        repaired = safe_task(
            "A",
            "src/a",
            scope={
                "exclusive_paths": ["src/a"],
                "read_paths": ["include/repaired_api.hh"],
            },
            consumes_interfaces=[{
                "id": "REPAIRED-API",
                "required_state": "frozen",
                "required_version": "1",
            }],
        )
        repaired_plan = base_plan(
            [repaired],
            interfaces=[{
                "id": "REPAIRED-API",
                "owner_task_id": "A",
                "state": "frozen",
                "version": "1",
                "contract_paths": ["include/repaired_api.hh"],
                "content_hash": "fixture-contract-hash",
            }],
        )
        repaired_plan["schema_version"] = 3
        repaired_plan["runs"] = [{
            "id": "compat-v2",
            "root_task_id": "A",
            "charter": {
                "objective": "test",
                "boundaries": ["Compatibility run normalized from plan schema v2"],
                "invariants": [],
                "acceptance_conditions": [],
                "glossary": {"lane": "single serial compatibility lane"},
            },
            "lanes": [{
                "id": "compat-v2-main",
                "role": "implementer",
                "tasks": ["A"],
                "workspace": {"mode": "exclusive"},
            }],
            "rendezvous": [],
        }]
        self.repo.apply(repaired_plan)

        claimed = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        brief = claimed["context"]["task_brief"]
        self.assertEqual(brief["version"], 2)
        self.assertEqual(brief["scope"]["read_paths"], ["include/repaired_api.hh"])
        self.assertEqual(brief["consumes_interfaces"], [{
            "id": "REPAIRED-API",
            "required_state": "frozen",
            "required_version": "1",
        }])
        with self.repo.service.db.read() as conn:
            fragments = conn.execute(
                "SELECT version,invalidated_at,superseded_by FROM workflow_context_fragments "
                "WHERE task_id='A' AND kind='task_brief' ORDER BY version"
            ).fetchall()
        self.assertEqual([row["version"] for row in fragments], [1, 2])
        self.assertIsNotNone(fragments[0]["invalidated_at"])
        self.assertIsNotNone(fragments[0]["superseded_by"])
        self.assertIsNone(fragments[1]["invalidated_at"])

        self.repo.apply(repaired_plan)
        with self.repo.service.db.read() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM workflow_context_fragments "
                "WHERE task_id='A' AND kind='task_brief'"
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_lost_locator_is_resumed_by_next_task_without_raw_token(self):
        claimed = self.protocol.next_task(repo_root=str(self.repo.root))
        self.locator.forget(claimed["workflow_handle"])
        resumed = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        self.assertEqual(resumed["status"], "resumed")
        self.assertNotEqual(resumed["workflow_handle"], claimed["workflow_handle"])

    def test_authorized_context_publication_is_atomic_and_implementer_is_denied(self):
        self.repo.close()
        self.repo = V2Repo()
        plan = base_plan([safe_task("COORD", "coord"), safe_task("IMPL", "src/impl")])
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "COORD", "charter": {"objective": "context publication"},
            "lanes": [
                {"id": "COORD-L", "role": "coordinator", "tasks": ["COORD"]},
                {"id": "IMPL-L", "parent_lane_id": "COORD-L", "role": "implementer", "tasks": ["IMPL"]},
            ],
        }]
        self.repo.apply(plan)
        coordinator = self.protocol.next_task(repo_root=str(self.repo.root), task_id="COORD")
        published = self.protocol.coordinate_task(
            workflow_handle=coordinator["workflow_handle"], action="publish_context",
            payload={
                "series_key": "layout-finding",
                "content": {"summary": "alignment is observable"},
                "anchors": [{"kind": "path", "value": "src"}, {"kind": "task", "value": "COORD"}],
                "classification": "finding",
                "source_identity": {"commit": "fixture-base"},
            },
        )
        note = published["context_note"]
        self.assertEqual(note["authority"], "non_authoritative")
        expanded = self.protocol.inspect_task(
            workflow_handle=coordinator["workflow_handle"], kind="context_fragment",
            target=note["fragment_id"], budget_bytes=2048,
        )
        self.assertEqual(expanded["content"]["source_identity"]["commit"], "fixture-base")
        revised = self.protocol.coordinate_task(
            workflow_handle=coordinator["workflow_handle"], action="publish_context",
            payload={
                "series_key": "layout-finding", "content": {"summary": "revised alignment"},
                "anchors": [{"kind": "path", "value": "src"}, {"kind": "task", "value": "COORD"}],
                "invalidate_fragment_ids": [note["fragment_id"]],
            },
        )
        with self.repo.service.db.read() as conn:
            prior = conn.execute("SELECT invalidated_at,superseded_by FROM workflow_context_fragments WHERE id=?", (note["fragment_id"],)).fetchone()
        self.assertIsNotNone(prior["invalidated_at"])
        self.assertEqual(prior["superseded_by"], revised["context_note"]["fragment_id"])
        implementer_locator = WorkflowCapabilityLocator(Path(self.locator_temp.name) / "implementer")
        implementer_protocol = WorkflowProtocol(WorkflowKernel(locator=implementer_locator), implementer_locator)
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "context-note-implementer"}):
            implementer = implementer_protocol.next_task(repo_root=str(self.repo.root), task_id="IMPL")
        visible_note = implementer_protocol.inspect_task(
            workflow_handle=implementer["workflow_handle"], kind="context_fragment",
            target=revised["context_note"]["fragment_id"], budget_bytes=2048,
        )
        self.assertEqual(visible_note["fragment"]["fragment_id"], revised["context_note"]["fragment_id"])
        with self.assertRaises(TodoError) as private_fragment:
            implementer_protocol.inspect_task(
                workflow_handle=implementer["workflow_handle"], kind="context_fragment",
                target=coordinator["context"]["task_brief"]["fragment_id"], budget_bytes=2048,
            )
        self.assertEqual(private_fragment.exception.code, "context_fragment_forbidden")
        with self.assertRaises(TodoError) as denied:
            implementer_protocol.coordinate_task(
                workflow_handle=implementer["workflow_handle"], action="publish_context",
                payload={"series_key": "nope", "content": {"summary": "no authority"}, "anchors": [{"kind": "task", "value": "IMPL"}]},
            )
        self.assertEqual(denied.exception.code, "capability_operation_forbidden")

    def test_publish_context_cannot_invalidate_generated_or_unrelated_notes(self):
        self.repo.close()
        self.repo = V2Repo()
        plan = base_plan([safe_task("COORD", "coord"), safe_task("INTEGRATE", "src/owned")])
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "COORD", "charter": {"objective": "context authority"},
            "lanes": [
                {"id": "COORD-L", "role": "coordinator", "tasks": ["COORD"]},
                {"id": "INT-L", "parent_lane_id": "COORD-L", "role": "integrator", "tasks": ["INTEGRATE"]},
            ],
        }]
        self.repo.apply(plan)
        coordinator = self.protocol.next_task(repo_root=str(self.repo.root), task_id="COORD")
        generated_id = coordinator["context"]["task_brief"]["fragment_id"]
        revision = self.repo.service.db.revision()
        with self.assertRaises(TodoError) as generated_denied:
            self.protocol.coordinate_task(
                workflow_handle=coordinator["workflow_handle"], action="publish_context",
                payload={
                    "series_key": "attempted-generated-retirement",
                    "content": {"summary": "must not retire a generated brief"},
                    "anchors": [{"kind": "task", "value": "COORD"}],
                    "invalidate_fragment_ids": [generated_id],
                },
            )
        self.assertEqual(generated_denied.exception.code, "workflow_context_invalidation_forbidden")
        self.assertEqual(self.repo.service.db.revision(), revision)
        with self.repo.service.db.read() as conn:
            self.assertIsNone(conn.execute(
                "SELECT invalidated_at FROM workflow_context_fragments WHERE id=?", (generated_id,)
            ).fetchone()["invalidated_at"])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM workflow_context_fragments WHERE kind='context_note'"
            ).fetchone()[0], 0)

        unrelated = self.protocol.coordinate_task(
            workflow_handle=coordinator["workflow_handle"], action="publish_context",
            payload={
                "series_key": "docs-finding", "content": {"summary": "docs only"},
                "anchors": [{"kind": "path", "value": "docs"}],
            },
        )["context_note"]
        integrator_locator = WorkflowCapabilityLocator(Path(self.locator_temp.name) / "integrator")
        integrator_protocol = WorkflowProtocol(WorkflowKernel(locator=integrator_locator), integrator_locator)
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "context-note-integrator"}):
            integrator = integrator_protocol.next_task(repo_root=str(self.repo.root), task_id="INTEGRATE")
        revision = self.repo.service.db.revision()
        with self.assertRaises(TodoError) as unrelated_denied:
            integrator_protocol.coordinate_task(
                workflow_handle=integrator["workflow_handle"], action="publish_context",
                payload={
                    "series_key": "owned-finding", "content": {"summary": "owned only"},
                    "anchors": [{"kind": "path", "value": "src/owned"}],
                    "invalidate_fragment_ids": [unrelated["fragment_id"]],
                },
            )
        self.assertEqual(unrelated_denied.exception.code, "workflow_context_scope_forbidden")
        self.assertEqual(self.repo.service.db.revision(), revision)
        with self.repo.service.db.read() as conn:
            row = conn.execute(
                "SELECT invalidated_at FROM workflow_context_fragments WHERE id=?", (unrelated["fragment_id"],)
            ).fetchone()
            self.assertIsNone(row["invalidated_at"])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM workflow_context_fragments WHERE kind='context_note'"
            ).fetchone()[0], 1)

    def _queued_integration_fixture(self) -> tuple[dict[str, object], Path, Path]:
        self.repo.close()
        self.repo = V2Repo()
        subprocess.run(["git", "-C", str(self.repo.root), "config", "user.email", "workflow@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo.root), "config", "user.name", "Workflow Tests"], check=True)
        (self.repo.root / "shared.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo.root), "add", "shared.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo.root), "commit", "-qm", "base"], check=True)
        base = subprocess.check_output(["git", "-C", str(self.repo.root), "rev-parse", "HEAD"], text=True).strip()
        counter_fd, counter_name = tempfile.mkstemp()
        os.close(counter_fd)
        counter = Path(counter_name)
        counter.write_text("0", encoding="utf-8")
        self.addCleanup(counter.unlink)
        plan = base_plan([
            {"id": "ROOT", "kind": "epic", "title": "root", "objective": "root"},
            safe_task("P", "shared.txt"),
            safe_task("INT", "shared.txt", parallel_policy="integration_exclusive", gates=[{
                "id": "COUNT", "type": "command", "required": True, "cwd": ".", "input_paths": ["shared.txt"],
                "argv": [sys.executable, "-c", "from pathlib import Path; p=Path(__import__('os').environ['COUNT_FILE']); p.write_text(str(int(p.read_text() or '0') + 1))"],
                "env": {"COUNT_FILE": str(counter)},
            }]),
        ])
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "ROOT", "charter": {"objective": "gate scheduling"},
            "lanes": [
                {"id": "ROOT-L", "role": "coordinator", "tasks": ["ROOT"]},
                {"id": "P-L", "parent_lane_id": "ROOT-L", "role": "implementer", "tasks": ["P"], "workspace": {"mode": "isolated_merge"}},
                {"id": "I-L", "parent_lane_id": "ROOT-L", "role": "integrator", "tasks": ["INT"], "workspace": {"mode": "exclusive"}},
            ],
        }]
        self.repo.apply(plan)
        managed = tempfile.TemporaryDirectory()
        self.addCleanup(managed.cleanup)
        workspaces = WorkspaceService(self.repo.service.db, managed_root=Path(managed.name), repository_identity_resolver=lambda root: "integration-test")
        producer = workspaces.create_workspace(repository_root=self.repo.root, repository_identity="integration-test", run_id="RUN", lane_id="P-L", mode="isolated_merge", base_commit=base, worktree_path=Path(managed.name) / "producer", branch="producer", integration_task_id="INT")
        destination = workspaces.create_workspace(repository_root=self.repo.root, repository_identity="integration-test", run_id="RUN", lane_id="I-L", mode="exclusive", base_commit=base, worktree_path=Path(managed.name) / "integrator", branch="integrator", integration_task_id="INT")
        producer_root = Path(str(producer["worktree_path"]))
        (producer_root / "shared.txt").write_text("producer change\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(producer_root), "add", "shared.txt"], check=True)
        subprocess.run(["git", "-C", str(producer_root), "commit", "-qm", "producer"], check=True)
        head = subprocess.check_output(["git", "-C", str(producer_root), "rev-parse", "HEAD"], text=True).strip()
        artifact = workspaces.publish_artifact(workspace_id=str(producer["workspace_id"]), task_id="P", kind="commit", artifact_ref=head)
        workspaces.enqueue_artifact(artifact_id=str(artifact["artifact_id"]), integrator_lane_id="I-L", integration_task_id="INT")
        claimed = self.protocol.next_task(repo_root=str(self.repo.root), task_id="INT")
        return claimed, Path(str(destination["worktree_path"])), counter

    def test_combined_integration_validates_command_once_and_rechecks_changed_source(self):
        claimed, destination, counter = self._queued_integration_fixture()
        combined = self.protocol.coordinate_task(workflow_handle=str(claimed["workflow_handle"]), action="run_gates", payload={"required": True})
        self.assertEqual(counter.read_text(), "1")
        self.assertEqual(combined["validation"]["effect"], "integrate_and_validate")
        self.assertEqual(combined["validation"]["validated_in_integration_gate_ids"], ["COUNT"])
        (destination / "shared.txt").write_text("changed after integration\n", encoding="utf-8")
        validate = self.protocol.coordinate_task(workflow_handle=str(claimed["workflow_handle"]), action="run_gates", payload={"required": True, "effect": "validate"})
        self.assertEqual(counter.read_text(), "2")
        self.assertEqual(validate["validation"]["effect"], "validate")
        self.assertEqual(validate["integration"], [])

    def test_validate_effect_does_not_apply_the_integration_queue(self):
        claimed, _, counter = self._queued_integration_fixture()
        validate = self.protocol.coordinate_task(workflow_handle=str(claimed["workflow_handle"]), action="run_gates", payload={"required": True, "effect": "validate"})
        self.assertEqual(counter.read_text(), "1")
        self.assertEqual(validate["validation"]["effect"], "validate")
        self.assertEqual(validate["integration"], [])
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM workflow_integration_queue").fetchone()[0], "queued")

    def test_declared_integration_wave_refuses_legacy_serial_gate_lifecycle(self):
        self.repo.close()
        self.repo = V2Repo()
        plan = base_plan([safe_task("ROOT", "coord"), safe_task("I", "integration")])
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "ROOT", "charter": {"objective": "wave refusal"},
            "lanes": [
                {"id": "ROOT-LANE", "role": "coordinator", "tasks": ["ROOT"]},
                {"id": "I-LANE", "parent_lane_id": "ROOT-LANE", "role": "integrator", "tasks": ["I"],
                 "workspace": {"mode": "read_shared"}},
            ],
        }]
        self.repo.apply(plan)

        def seed(conn, revision):
            now = "2026-09-12T00:00:00Z"
            conn.execute(
                "INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,state,created_at,updated_at) "
                "VALUES('I-WORKSPACE','repo','RUN','I-LANE','read_shared','base','applied_pending_wave',?,?)",
                (now, now),
            )
            conn.execute(
                "INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,state,created_at,updated_at) "
                "VALUES('P-WORKSPACE','repo','RUN','ROOT-LANE','isolated_merge','base','queued',?,?)",
                (now, now),
            )
            conn.execute(
                "INSERT INTO workflow_patch_artifacts(id,workspace_id,task_id,kind,artifact_ref,content_hash,base_commit,created_at,state) "
                "VALUES('PATCH','P-WORKSPACE','ROOT','commit','deadbeef','hash','base',?,'queued')",
                (now,),
            )
            conn.execute(
                "INSERT INTO workflow_integration_queue(id,run_id,patch_artifact_id,integration_task_id,integrator_lane_id,position,state,merge_result_json,conflict_json,created_at,updated_at) "
                "VALUES('QUEUE','RUN','PATCH','I','I-LANE',1,'applied_pending_wave','{\"wave_id\":\"WAVE\",\"wave_members\":[\"QUEUE\"]}','{}',?,?)",
                (now, now),
            )

        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="WAVE",
            event_type="fixture.declared_wave", payload={}, operation=seed,
        )
        claimed = self.protocol.next_task(repo_root=str(self.repo.root), task_id="I")
        self.assertEqual(claimed["status"], "claimed")
        with self.assertRaises(TodoError) as denied:
            self.protocol.coordinate_task(
                workflow_handle=claimed["workflow_handle"], action="run_gates", payload={"required": True},
            )
        self.assertEqual(denied.exception.code, "integration_wave_root_lifecycle_required")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM workflow_integration_queue WHERE id='QUEUE'").fetchone()[0], "applied_pending_wave")

    def test_child_candidate_is_parent_mediated_and_never_completes_parent(self):
        claimed = self.protocol.next_task(repo_root=str(self.repo.root))
        delegated = self.protocol.delegate_task(
            workflow_handle=claimed["workflow_handle"], delegated_objective="Inspect one bounded source file", mode="readonly"
        )
        self.assertEqual(delegated["status"], "claimed")
        self.assertLessEqual(delegated["packet_size_bytes"], 4096)
        collected = self.protocol.collect_delegation(delegation_handle=delegated["delegation_handle"])
        self.assertFalse(collected["parent_task_completed"])
        accepted = self.protocol.coordinate_task(
            workflow_handle=claimed["workflow_handle"], action="accept_child",
            payload={"child_execution_id": delegated["child_execution_id"]},
        )
        self.assertEqual(accepted["state"], "accepted")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id='A'").fetchone()[0], "in_progress")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_lanes WHERE id=?", (delegated["child_execution_id"],)).fetchone()[0], 0)

    def test_coordinator_can_dispose_only_its_own_child_and_failure_releases_writable_lease(self):
        self.repo.close()
        self.repo = V2Repo()
        (self.repo.root / "src" / "a").mkdir(parents=True)
        (self.repo.root / "src" / "a" / "unit.py").write_text("value = 1\n", encoding="utf-8")
        plan = base_plan([
            safe_task("A", "src/a", forbidden_mutations=["src/a/private"], references=["docs/task.md"]),
            safe_task("B", "src/b"),
        ])
        plan["schema_version"] = 3
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "A",
            "charter": {"objective": "bounded", "boundaries": ["preserve scope"], "invariants": ["parent decides"]},
            "lanes": [{"id": "COORD", "role": "coordinator", "tasks": ["A"]}],
        }]
        self.repo.apply(plan)
        store = ContextFragmentStore(self.repo.service.db)
        store.publish(
            actor_session_id=None,
            owner=FragmentOwner("RUN", "COORD", "A"),
            kind="source_packet_ref",
            content={"references": [{"packet_id": "receipt-a", "content_hash": "abc", "paths": ["src/a/unit.py"]}]},
        )
        coordinator = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A", run_id="RUN")
        with patch("todo_orchestrator.workflow.service.compose_child_packet", wraps=__import__(
            "todo_orchestrator.workflow.service", fromlist=["compose_child_packet"]
        ).compose_child_packet) as packet:
            delegated = self.protocol.delegate_task(
                workflow_handle=coordinator["workflow_handle"], delegated_objective="inspect exact unit",
                mode="writable", source_targets=["src/a/unit.py"],
            )
        packet_args = packet.call_args.kwargs
        self.assertEqual(packet_args["child_authorized_paths"], ["src/a/unit.py"])
        self.assertEqual(packet_args["source_packet_refs"], [{"packet_id": "receipt-a", "content_hash": "abc", "paths": ["src/a/unit.py"]}])
        self.assertIn('run boundaries: ["preserve scope"]', packet_args["parent_constraints"])
        self.assertIn('task references: ["docs/task.md"]', packet_args["parent_constraints"])
        self.protocol.collect_delegation(delegation_handle=delegated["delegation_handle"])
        accepted = self.protocol.coordinate_task(
            workflow_handle=coordinator["workflow_handle"], action="accept_child",
            payload={"child_execution_id": delegated["child_execution_id"]},
        )
        self.assertEqual(accepted["state"], "accepted")
        rejected_child = self.protocol.delegate_task(
            workflow_handle=coordinator["workflow_handle"], delegated_objective="inspect exact unit again",
            mode="writable", source_targets=["src/a/unit.py"],
        )
        rejected = self.protocol.coordinate_task(
            workflow_handle=coordinator["workflow_handle"], action="reject_child",
            payload={"child_execution_id": rejected_child["child_execution_id"], "reason": "not needed"},
        )
        self.assertEqual(rejected["state"], "rejected")
        now = utc_now()
        def seed_foreign(conn, revision):
            conn.execute(
                "INSERT INTO claims(id,task_id,session_id,token_hash,state,created_at,heartbeat_at,expires_at,baseline_revision) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                ("FOREIGN-CLAIM", "B", conn.execute("SELECT session_id FROM claims WHERE task_id=? AND state='active'", ("A",)).fetchone()[0],
                 "foreign", "active", now, now, "2099-01-01T00:00:00Z", revision),
            )
            conn.execute(
                "INSERT INTO child_executions(id,parent_claim_id,task_id,objective,state,created_at,access_mode,authorized_scopes_json) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("foreign-child", "FOREIGN-CLAIM", "B", "foreign", "running", now, "read", '["src/b"]'),
            )
        self.repo.service.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="foreign-child",
            event_type="fixture.foreign_child", payload={}, operation=seed_foreign,
        )
        with self.assertRaises(TodoError) as foreign:
            self.protocol.coordinate_task(
                workflow_handle=coordinator["workflow_handle"], action="reject_child",
                payload={"child_execution_id": "foreign-child", "reason": "not mine"},
            )
        self.assertEqual(foreign.exception.code, "child_parent_mismatch")
        self.kernel.local_worker_adapter = UnavailableLocalWorker()
        unavailable = self.protocol.delegate_task(
            workflow_handle=coordinator["workflow_handle"], delegated_objective="must clean up",
            mode="writable", source_targets=["src/a/unit.py"],
        )
        self.assertEqual(unavailable["operation_status"], "local_unavailable")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM child_scope_leases WHERE state='active'").fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM child_executions WHERE parent_claim_id != 'FOREIGN-CLAIM' AND state='rejected'"
            ).fetchone()[0], 2)

    def test_ce_geo_shaped_claim_delegation_preserves_all_read_surfaces(self):
        def assert_observable() -> None:
            with patch.dict(os.environ, {"TODO_ORCHESTRATOR_READ_ONLY": "1"}):
                service = Service(self.repo.root)
                semantic = SemanticReader(self.repo.root)
                revisions = (
                    service.status()["project_revision"], service.export()["project_revision"],
                    semantic.state()["revision"], semantic.workflow()["revision"],
                )
                self.assertEqual(len(set(revisions)), 1)
                self.assertTrue(semantic.workflow()["available"])

        assert_observable()
        claimed = self.protocol.next_task(repo_root=str(self.repo.root))
        assert_observable()
        delegated = self.protocol.delegate_task(
            workflow_handle=claimed["workflow_handle"], delegated_objective="bounded preflight", mode="readonly"
        )
        assert_observable()
        self.protocol.collect_delegation(delegation_handle=delegated["delegation_handle"])
        assert_observable()

    def test_arrival_schema_requires_full_provenance(self):
        with self.assertRaises(TodoError) as error:
            _validate_action("arrive", {"rendezvous_id": "R", "summary": "done"})
        self.assertEqual(error.exception.code, "invalid_coordination_payload")
        _validate_action("arrive", {
            "rendezvous_id": "R", "summary": "done", "base_source_identity": "base",
            "final_source_identity": "final", "artifact": {"kind": "commit", "ref": "abc"},
            "evidence": [{"type": "gate", "id": "G"}], "context_version": 1,
        })

    def test_terminal_interface_is_validated_before_artifact_publication(self):
        self.repo.close()
        self.repo = V2Repo()
        task = safe_task(
            "A",
            "src/a",
            checkpoints=[{
                "id": "A-FROZEN",
                "title": "A interface frozen",
                "publishes_interfaces": [{"id": "A-API", "version": "1"}],
            }],
        )
        self.repo.apply(base_plan(
            [task],
            interfaces=[{
                "id": "A-API",
                "owner_task_id": "A",
                "contract_paths": ["include/a_api.hh"],
            }],
        ))
        claimed = self.protocol.next_task(repo_root=str(self.repo.root), task_id="A")
        with self.assertRaises(TodoError) as caught:
            self.protocol.finish_task(
                workflow_handle=claimed["workflow_handle"],
                action="complete",
                disposition="implemented",
            )
        self.assertEqual(caught.exception.code, "interface_artifact_missing")
        with self.repo.service.db.read() as conn:
            self.assertEqual(
                conn.execute("SELECT status FROM tasks WHERE id='A'").fetchone()[0],
                "in_progress",
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_patch_artifacts").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
