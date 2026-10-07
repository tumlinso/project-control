from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.todo.v2_helpers import V2Repo  # noqa: F401 - establishes package path

from todo_orchestrator.config import utc_now
from todo_orchestrator.db import Database
from todo_orchestrator.models import TodoError
from todo_orchestrator.workflow.adapters import CtxppAdapter, CudaAdapter
from todo_orchestrator.workflow.capabilities import (
    WorkflowCapabilityStore,
    capability_hash,
    child_operations,
    default_first_class_operations,
)
from todo_orchestrator.workflow.foundation import CapabilityLineage, canonical_json
from todo_orchestrator.workflow.protocol import (
    FallbackAuthorization,
    WorkflowProtocol,
    action_policy,
    fallback_authorized,
)
from todo_orchestrator.workflow.roles import allowed_actions as role_actions


class Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name) / "state.sqlite3")
        self.db.initialize({"project_uuid": "project-1", "project_name": "fixture"})
        now = utc_now()

        def seed(conn, revision):
            conn.execute(
                "INSERT INTO tasks(id,kind,title,status,created_at,updated_at,revision) VALUES(?,?,?,?,?,?,?)",
                ("TASK", "task", "Task", "in_progress", now, now, revision),
            )
            conn.execute(
                "INSERT INTO sessions(id,label,token_hash,hostname,repo_root,worktree_root,created_at,last_seen_at,state) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                ("SESSION", "agent", "session-hash", "host", "/repo", "/repo", now, now, "active"),
            )
            conn.execute(
                "INSERT INTO claims(id,task_id,session_id,token_hash,state,created_at,heartbeat_at,expires_at,baseline_revision) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                ("CLAIM", "TASK", "SESSION", "claim-hash", "active", now, now, "2099-01-01T00:00:00Z", revision),
            )
            conn.execute(
                "INSERT INTO workflow_runs(id,root_task_id,status,created_at,updated_at,revision) VALUES(?,?,?,?,?,?)",
                ("RUN", "TASK", "active", now, now, revision),
            )
            conn.execute(
                "INSERT INTO workflow_lanes(id,run_id,role,state,created_at,updated_at,revision) VALUES(?,?,?,?,?,?,?)",
                ("LANE", "RUN", "implementer", "active", now, now, revision),
            )
            conn.execute(
                "INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,state,context_version,heartbeat_at,created_at,revision) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                ("DISPATCH", "LANE", "SESSION", "CLAIM", "active", 1, now, now, revision),
            )

        self.db.mutate(
            actor_session_id=None,
            entity_type="fixture",
            entity_id="RUN",
            event_type="fixture_seeded",
            payload={},
            operation=seed,
        )
        self.capabilities = WorkflowCapabilityStore(self.db)

    def close(self) -> None:
        self.temporary.cleanup()

    def lineage(self, *, role="implementer", operations=None) -> CapabilityLineage:
        return CapabilityLineage(
            capability_class="first_class",
            project_uuid="project-1",
            repository_identity="repo-identity",
            session_id="SESSION",
            claim_id="CLAIM",
            run_id="RUN",
            lane_id="LANE",
            role=role,
            task_id="TASK",
            allowed_operations=operations if operations is not None else default_first_class_operations(role),
            incarnation=1,
        )

    def add_child(self, child_id="CHILD") -> None:
        now = utc_now()

        def operation(conn, revision):
            conn.execute(
                "INSERT INTO child_executions(id,parent_claim_id,task_id,objective,state,created_at,access_mode,authorized_scopes_json) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (child_id, "CLAIM", "TASK", "bounded", "running", now, "read", '["src/a.py"]'),
            )

        self.db.mutate(
            actor_session_id="SESSION",
            entity_type="child_execution",
            entity_id=child_id,
            event_type="fixture_child",
            payload={},
            operation=operation,
        )


class FakePort:
    def __init__(self, fixture: Fixture):
        self.fixture = fixture
        self.calls = []
        self.parent_completed = False
        self.delegation_fallback = False
        self.next_operations = None
        self.next_advertised_actions = None
        self.next_status = "claimed"
        self.next_context = None

    def next_task(self, *, repo_root, task_id, run_id=None):
        self.calls.append(("next_task", repo_root, task_id, run_id))
        handle, _, revision = self.fixture.capabilities.issue_first_class(
            self.fixture.lineage(operations=self.next_operations), actor_session_id="SESSION"
        )
        result = {
            "status": self.next_status,
            "run_id": "RUN",
            "lane_id": "LANE",
            "role": "implementer",
            "task_id": "TASK",
            "context_cursor": 1,
            "workflow_handle": handle,
            "revision": revision,
            "claim_token": "must-not-cross",
        }
        if self.next_advertised_actions is not None:
            result["allowed_actions"] = self.next_advertised_actions
        if self.next_context is not None:
            result["context"] = self.next_context
        return result

    def inspect_task(self, capability, *, kind, target, budget_bytes):
        self.calls.append(("inspect_task", capability.id, kind, target, budget_bytes))
        return {"status": "current", "run_id": "RUN", "lane_id": "LANE", "role": "implementer", "task_id": "TASK", "context_versions": {"task": 1}}

    def coordinate_task(self, capability, *, action, payload):
        self.calls.append(("coordinate_task", action, payload))
        return {"status": "claimed", "run_id": "RUN", "lane_id": "LANE", "role": "implementer", "task_id": "TASK", "action": action}

    def delegate_task(self, capability, *, objective, mode, source_targets=None):
        self.calls.append(("delegate_task", objective, mode, source_targets))
        if self.delegation_fallback:
            return {
                "status": "local_unavailable",
                "fallback_authorization": {
                    "specialized_skill": "local-coding-worker",
                    "permitted_operation": "continue_directly",
                    "reason": "local execution unavailable",
                    "scope": {"task_id": "TASK"},
                    "access": "mutating",
                },
            }
        now = utc_now()
        lineage = CapabilityLineage(
            capability_class="child",
            project_uuid="project-1",
            repository_identity="repo-identity",
            session_id="SESSION",
            claim_id="CLAIM",
            run_id=None,
            lane_id=None,
            role=None,
            task_id="TASK",
            allowed_operations=child_operations(),
            incarnation=1,
            parent_capability_id=capability.id,
            child_execution_id="CHILD",
        )

        def operation(conn, revision):
            conn.execute(
                "INSERT INTO child_executions(id,parent_claim_id,task_id,objective,state,created_at,access_mode,authorized_scopes_json) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("CHILD", "CLAIM", "TASK", objective, "running", now, "read", '["src/a.py"]'),
            )
            return self.fixture.capabilities.stage_child(
                conn, lineage=lineage, parent=capability, revision=revision
            )[0]

        handle, revision = self.fixture.db.mutate(
            actor_session_id="SESSION",
            entity_type="child_execution",
            entity_id="CHILD",
            event_type="child_delegated_with_capability",
            payload={},
            operation=operation,
        )
        return {
            "status": "delegated",
            "run_id": "RUN",
            "lane_id": "LANE",
            "task_id": "TASK",
            "child_execution_id": "CHILD",
            "delegation_handle": handle,
            "revision": revision,
            "child_packet": {"delegated_objective": objective},
        }

    def collect_delegation(self, capability):
        self.calls.append(("collect_delegation", capability.id))
        return {"status": "candidate_available", "result_kind": "test_result", "summary": "candidate", "parent_task_completed": True, "worker_token": "never"}

    def finish_task(self, capability, *, action, disposition, note, reason):
        self.calls.append(("finish_task", action, disposition, note, reason))
        self.parent_completed = True
        _, revision = self.fixture.db.mutate(
            actor_session_id="SESSION",
            entity_type="fixture_finish",
            entity_id="TASK",
            event_type="fixture_finished_with_capability_release",
            payload={},
            operation=lambda conn, rev: self.fixture.capabilities.stage_revoke(
                conn, capability_id=capability.id, family=True
            ),
        )
        return {"status": "finished", "terminal": True, "task_id": "TASK", "revision": revision, "gates": [{"id": "G", "valid": True}]}


class CapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_hash_only_storage_and_authoritative_revalidation(self) -> None:
        handle, authorized, _ = self.fixture.capabilities.issue_first_class(
            self.fixture.lineage(), actor_session_id="SESSION"
        )
        with self.fixture.db.read() as conn:
            row = conn.execute("SELECT token_hash FROM workflow_capabilities WHERE id=?", (authorized.id,)).fetchone()
        self.assertEqual(row["token_hash"], capability_hash(handle))
        self.assertNotEqual(row["token_hash"], handle)
        resolved = self.fixture.capabilities.resolve(
            handle, required_operation="inspect_task", expected_class="first_class"
        )
        self.assertEqual(resolved.lineage.task_id, "TASK")

        self.fixture.db.mutate(
            actor_session_id="SESSION",
            entity_type="claim",
            entity_id="CLAIM",
            event_type="fixture_release",
            payload={},
            operation=lambda conn, revision: conn.execute("UPDATE claims SET state='released' WHERE id='CLAIM'"),
        )
        with self.assertRaisesRegex(TodoError, "no longer active"):
            self.fixture.capabilities.resolve(handle, required_operation="inspect_task")

    def test_restart_reissue_retires_old_handle_and_increments_incarnation(self) -> None:
        first, first_auth, _ = self.fixture.capabilities.issue_first_class(
            self.fixture.lineage(), actor_session_id="SESSION"
        )
        self.fixture.add_child()
        child_handle, _, _ = self.fixture.capabilities.issue_child(
            CapabilityLineage(
                "child", "project-1", "repo-identity", "SESSION", "CLAIM", None, None, None,
                "TASK", child_operations(), 1, first_auth.id, "CHILD",
            ),
            parent_handle=first,
            actor_session_id="SESSION",
        )
        restarted_store = WorkflowCapabilityStore(self.fixture.db)
        second, second_auth, _ = restarted_store.issue_first_class(
            self.fixture.lineage(), actor_session_id="SESSION"
        )
        self.assertNotEqual(first, second)
        self.assertEqual(second_auth.lineage.incarnation, first_auth.lineage.incarnation + 1)
        with self.assertRaises(TodoError):
            restarted_store.resolve(first, required_operation="inspect_task")
        self.assertEqual(
            restarted_store.resolve(second, required_operation="inspect_task").id,
            second_auth.id,
        )
        self.assertEqual(
            restarted_store.resolve(
                child_handle, required_operation="collect_delegation", expected_class="child"
            ).lineage.child_execution_id,
            "CHILD",
        )

    def test_child_capability_is_distinct_and_cannot_use_first_class_paths(self) -> None:
        parent_handle, parent, _ = self.fixture.capabilities.issue_first_class(
            self.fixture.lineage(), actor_session_id="SESSION"
        )
        self.fixture.add_child()
        lineage = CapabilityLineage(
            "child", "project-1", "repo-identity", "SESSION", "CLAIM", None, None, None,
            "TASK", child_operations(), 1, parent.id, "CHILD",
        )
        child_handle, _, _ = self.fixture.capabilities.issue_child(
            lineage, parent_handle=parent_handle, actor_session_id="SESSION"
        )
        self.assertTrue(child_handle.startswith("wcc_"))
        self.fixture.capabilities.resolve(
            child_handle, required_operation="collect_delegation", expected_class="child"
        )
        for operation in ("finish_task", "inspect_task", "coordinate:message", "coordinate:arrive", "claim_task"):
            with self.assertRaises(TodoError):
                self.fixture.capabilities.resolve(child_handle, required_operation=operation)

    def test_role_operations_are_server_derived(self) -> None:
        self.assertNotIn("coordinate:fork", default_first_class_operations("implementer"))
        self.assertIn("coordinate:fork", default_first_class_operations("coordinator"))
        self.assertNotIn("delegate_task", default_first_class_operations("validator"))
        self.assertIn("coordinate:accept_child", default_first_class_operations("coordinator"))
        self.assertIn("coordinate:reject_child", default_first_class_operations("coordinator"))

    def test_public_action_policy_is_the_role_and_capability_intersection(self) -> None:
        order = ("inspect_task", "coordinate_task", "delegate_task", "finish_task")
        for role in ("coordinator", "implementer", "validator", "integrator", "specialist"):
            policy = action_policy(self.fixture.lineage(role=role))
            self.assertEqual(policy["tools"], sorted(policy["tools"], key=order.index))
            for action, schema in policy["coordinate"].items():
                if action == "publish_context" and role in {"implementer", "specialist"}:
                    self.assertIn("publish_project_context", role_actions(role))
                    self.assertIn("coordinate:publish_project_context", default_first_class_operations(role))
                    self.assertNotIn("coordinate:publish_context", default_first_class_operations(role))
                    self.assertEqual(schema["required"], ["kind", "payload"])
                    self.assertEqual(schema["optional"], ["task_id"])
                    self.assertEqual(schema["supported_kinds"], ["skill_use", "finding", "candidate_relation"])
                    self.assertEqual(set(schema), {"required", "optional", "supported_kinds"})
                else:
                    self.assertIn(action, role_actions(role))
                    self.assertIn(f"coordinate:{action}", default_first_class_operations(role))
                    if action == "publish_context":
                        self.assertEqual(set(schema), {"required", "optional", "semantic_variant"})
                        self.assertEqual(schema["required"], ["anchors", "content", "series_key"])
                        self.assertEqual(schema["semantic_variant"]["required"], ["kind", "payload"])
                        self.assertIn("coordinate:publish_project_context", default_first_class_operations(role))
                    else:
                        self.assertEqual(set(schema), {"required", "optional"})
        self.assertIn("delegate_task", action_policy(self.fixture.lineage(role="coordinator"))["tools"])
        self.assertIn("accept_child", action_policy(self.fixture.lineage(role="coordinator"))["coordinate"])
        self.assertIn("reject_child", action_policy(self.fixture.lineage(role="coordinator"))["coordinate"])
        self.assertNotIn("delegate_task", action_policy(self.fixture.lineage(role="integrator"))["tools"])


class ProtocolBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()
        self.port = FakePort(self.fixture)
        self.protocol = WorkflowProtocol(self.port, self.fixture.capabilities)
        self.claimed = self.protocol.next_task(repo_root="/repo")

    def tearDown(self) -> None:
        self.fixture.close()

    def test_v2_claim_envelope_is_bounded_and_secret_free(self) -> None:
        encoded = canonical_json(self.claimed).encode()
        self.assertEqual(self.claimed["protocol_version"], 2)
        self.assertEqual(self.claimed["status"], "claimed")
        self.assertEqual((self.claimed["run_id"], self.claimed["lane_id"], self.claimed["role"], self.claimed["task_id"]), ("RUN", "LANE", "implementer", "TASK"))
        self.assertIn("recommended_next_call", self.claimed)
        self.assertIsNone(self.claimed["recommended_next_call"])
        self.assertIn("run_gates", self.claimed["action_policy"]["coordinate"])
        self.assertLessEqual(len(encoded), 8 * 1024)
        self.assertNotIn(b"must-not-cross", encoded)

    def test_kernel_advertisement_cannot_broaden_resolved_capability(self) -> None:
        self.port.next_operations = frozenset({"inspect_task"})
        self.port.next_advertised_actions = ["inspect_task", "delegate_task", "finish_task"]
        claimed = self.protocol.next_task(repo_root="/repo")
        self.assertEqual(claimed["allowed_actions"], ["inspect_task"])

    def test_needs_context_preserves_the_claim_and_inspection_receipt(self) -> None:
        self.port.next_status = "needs_context"
        self.port.next_advertised_actions = ["inspect_task"]
        result = self.protocol.next_task(repo_root="/repo")
        self.assertEqual(result["status"], "needs_context")
        self.assertEqual(result["allowed_actions"], ["inspect_task"])

    def test_outer_entry_overflow_retains_claim_with_callable_context_receipt(self) -> None:
        self.port.next_context = {
            "fragment_manifest": [{
                "fragment_id": "CHARTER", "kind": "run_charter", "version": 1,
                "content_hash": "a" * 64, "invalidated": False,
            }],
            "large": "x" * 9000,
        }
        result = self.protocol.next_task(repo_root="/repo")
        self.assertEqual(result["status"], "needs_context")
        self.assertEqual(result["allowed_actions"], ["inspect_task"])
        call = result["context_receipt"]["next_call"]
        self.assertEqual(call["workflow_handle"], result["workflow_handle"])
        self.assertEqual({key: call[key] for key in ("kind", "target", "budget_bytes")}, {
            "kind": "context_fragment", "target": "CHARTER", "budget_bytes": 4096,
        })

    def test_inspect_and_coordinate_are_typed_and_direct(self) -> None:
        handle = self.claimed["workflow_handle"]
        inspected = self.protocol.inspect_task(workflow_handle=handle, kind="rendezvous")
        self.assertEqual(inspected["protocol_version"], 2)
        synced = self.protocol.coordinate_task(
            workflow_handle=handle, action="sync", payload={"cursor": 1}
        )
        self.assertEqual(synced["action"], "sync")
        gates = self.protocol.coordinate_task(
            workflow_handle=handle, action="run_gates", payload={"required": True}
        )
        self.assertEqual(gates["action"], "run_gates")
        with self.assertRaisesRegex(TodoError, "does not authorize"):
            self.protocol.coordinate_task(
                workflow_handle=handle, action="fork", payload={"tasks": ["TASK-2"]}
            )
        with self.assertRaisesRegex(TodoError, "not supported"):
            self.protocol.coordinate_task(workflow_handle=handle, action="exec", payload={})
        with self.assertRaisesRegex(TodoError, "schema"):
            self.protocol.coordinate_task(workflow_handle=handle, action="sync", payload={"command": "rm"})

    def test_delegation_is_subordinate_and_candidate_does_not_finish_parent(self) -> None:
        result = self.protocol.delegate_task(
            workflow_handle=self.claimed["workflow_handle"],
            delegated_objective="Run one bounded test",
        )
        self.assertEqual(result["operation_status"], "delegated")
        self.assertLessEqual(len(canonical_json(result).encode()), 4 * 1024)
        self.assertNotIn("child_packet", result)
        collected = self.protocol.collect_delegation(
            delegation_handle=result["delegation_handle"]
        )
        self.assertEqual(collected["operation_status"], "candidate_available")
        self.assertFalse(collected["parent_task_completed"])
        self.assertFalse(self.port.parent_completed)
        self.assertNotIn("worker_token", collected)
        self.assertEqual(collected["task_id"], "TASK")
        for first_class_field in ("run_id", "lane_id", "role"):
            self.assertNotIn(first_class_field, collected)

    def test_finish_is_first_class_only_and_revokes_family(self) -> None:
        handle = self.claimed["workflow_handle"]
        delegated = self.protocol.delegate_task(
            workflow_handle=handle, delegated_objective="bounded"
        )
        with self.assertRaises(TodoError):
            self.protocol.finish_task(
                workflow_handle=delegated["delegation_handle"], action="complete", disposition="implemented"
            )
        finished = self.protocol.finish_task(
            workflow_handle=handle, action="complete", disposition="implemented"
        )
        self.assertEqual(finished["status"], "idle")
        with self.assertRaises(TodoError):
            self.fixture.capabilities.resolve(handle, required_operation="inspect_task")
        with self.assertRaises(TodoError):
            self.fixture.capabilities.resolve(
                delegated["delegation_handle"], required_operation="collect_delegation"
            )

    def test_fallback_authorization_is_bounded_and_has_no_raw_token(self) -> None:
        result = fallback_authorized(FallbackAuthorization(
            "cpp-context-compiler", "slice", "bounded C++ inspection", {"paths": ["src/a.cc"]}, "read_only"
        ))
        self.assertEqual(result["status"], "fallback_authorized")
        self.assertEqual(result["fallback_authorization"]["access"], "read_only")
        self.assertNotIn("token", canonical_json(result))
        self.port.delegation_fallback = True
        delegated = self.protocol.delegate_task(
            workflow_handle=self.claimed["workflow_handle"], delegated_objective="continue"
        )
        self.assertEqual(delegated["status"], "fallback_authorized")
        self.assertEqual(
            delegated["fallback_authorization"]["specialized_skill"], "local-coding-worker"
        )


class SpecializedAdapterTests(unittest.TestCase):
    def test_specialized_adapters_use_fixed_shell_free_argv_and_are_lazy(self) -> None:
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout='{"status":"ok"}', stderr="")

        ctxpp = CtxppAdapter(Path("/tools/ctxpp"), runner=runner)
        cuda = CudaAdapter(Path("/tools/cuda"), runner=runner)
        self.assertEqual(calls, [])
        ctxpp.inspect(repo=Path("/repo"), target="Widget", intent="edit", budget_tokens=2000)
        cuda.execute(repo=Path("/repo"), operation="benchmark", request_ref="request-1")
        self.assertEqual(len(calls), 2)
        for argv, kwargs in calls:
            self.assertIsInstance(argv, list)
            self.assertFalse(kwargs["shell"])
            self.assertNotIn("toc_", " ".join(argv))
        self.assertIn("--request-ref", calls[1][0])

    def test_canonical_protocol_contains_no_todo_subprocess_backend(self) -> None:
        root = Path(__file__).resolve().parents[2] / "src" / "todo_orchestrator" / "workflow"
        source = (root / "protocol.py").read_text(encoding="utf-8")
        self.assertNotIn("todo.py", source)
        self.assertNotIn("shell=True", source)


if __name__ == "__main__":
    unittest.main()
