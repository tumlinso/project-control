from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from todo_orchestrator.gates import run_gate, validate_gate_spec
from todo_orchestrator.models import TodoError
from todo_orchestrator.plan import validate_plan
from todo_orchestrator.workflow.capabilities import WorkflowCapabilityLocator
from todo_orchestrator.workflow.protocol import WorkflowProtocol
from todo_orchestrator.workflow.service import WorkflowKernel
from tests.todo.v2_helpers import V2Repo, base_plan, safe_task


def valid_gate_specs() -> list[dict[str, object]]:
    return [
        {"id": "COMMAND", "type": "command", "argv": ["true"], "required": True},
        {"id": "BENCH", "type": "benchmark", "argv": ["true"], "threshold": 1, "required": True},
        {"id": "JSON", "type": "json_predicate", "argv": ["true"], "threshold": 1, "required": True},
        {"id": "EXISTS", "type": "file_exists", "path": "src/a/unit.py", "required": True},
        {"id": "PATTERN", "type": "pattern", "path": "src/a/unit.py", "pattern": "value", "required": True},
        {"id": "TASK", "type": "task_state", "task_id": "A", "required": True},
        {"id": "CHECKPOINT", "type": "checkpoint", "checkpoint_id": "CP", "required": True},
        {"id": "INTERFACE", "type": "interface", "interface_id": "API", "required": True},
        {"id": "MANUAL", "type": "manual", "accepted": True, "required": True},
    ]


class GateReviewRepairsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        source = self.repo.root / "src/a/unit.py"
        source.parent.mkdir(parents=True)
        source.write_text("value = 1\n", encoding="utf-8")
        task = safe_task("A", "src/a", checkpoints=[{"id": "CP", "title": "CP"}])
        self.plan = base_plan(
            [task],
            interfaces=[{"id": "API", "owner_task_id": "A", "contract_paths": ["src/a/api.hh"]}],
        )

    def tearDown(self) -> None:
        self.repo.close()

    def _protocol(self) -> WorkflowProtocol:
        locator_temp = tempfile.TemporaryDirectory()
        self.addCleanup(locator_temp.cleanup)
        locator = WorkflowCapabilityLocator(Path(locator_temp.name))
        return WorkflowProtocol(WorkflowKernel(locator=locator), locator)

    def test_static_path_kind_invalidation_and_explicit_acceptance_bypass_reuse(self) -> None:
        directory = self.repo.root / "src/a/existing-directory"
        directory.mkdir()
        self.plan["tasks"][0]["gates"] = [
            {"id": "DIRECTORY", "type": "file_exists", "path": "src/a/existing-directory", "required": True},
        ]
        self.repo.apply(self.plan)
        claim = self.repo.service.continue_work(task_id="A")
        token = claim["claim"]["claim_token"]
        first, _ = run_gate(self.repo.service.db, self.repo.service.paths, self.repo.service.project, "DIRECTORY", token)
        self.assertTrue(first["valid"])
        directory.rmdir()
        rerun, _ = run_gate(self.repo.service.db, self.repo.service.paths, self.repo.service.project, "DIRECTORY", token)
        self.assertFalse(rerun["valid"])
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence WHERE gate_id='DIRECTORY'").fetchone()[0], 2)

        # A warmed static result must not skip validation of an explicit child.
        directory.mkdir()
        warmed, _ = run_gate(self.repo.service.db, self.repo.service.paths, self.repo.service.project, "DIRECTORY", token)
        self.assertTrue(warmed["valid"])
        with self.assertRaises(TodoError) as error:
            run_gate(
                self.repo.service.db, self.repo.service.paths, self.repo.service.project,
                "DIRECTORY", token, accept_child="unknown-child",
            )
        self.assertEqual(error.exception.code, "unknown_child_execution")

    def test_supported_gate_contract_is_shared_by_plan_and_live_binding(self) -> None:
        specs = valid_gate_specs()
        self.plan["tasks"][0]["gates"] = specs
        self.assertTrue(validate_plan(self.plan, self.repo.root)["valid"])
        for spec in specs:
            self.assertEqual(validate_gate_spec(spec, self.repo.root), [])

        self.repo.apply(base_plan(
            [safe_task("A", "src/a", checkpoints=[{"id": "CP", "title": "CP"}])],
            interfaces=[{"id": "API", "owner_task_id": "A", "contract_paths": ["src/a/api.hh"]}],
        ))
        protocol = self._protocol()
        claimed = protocol.next_task(repo_root=str(self.repo.root))
        # Checkpoint-owned gates remain intentionally excluded from the
        # append-only live-binding operation, but share this validation before
        # plan import.  Every live-bindable type is accepted here.
        live_specs = [spec for spec in specs if spec["type"] != "checkpoint"]
        bound = protocol.coordinate_task(
            workflow_handle=claimed["workflow_handle"], action="bind_required_gates", payload={"gates": live_specs},
        )
        self.assertEqual(bound["bound_gate_ids"], [spec["id"] for spec in live_specs])

    def test_invalid_gate_contract_is_rejected_before_plan_or_live_mutation(self) -> None:
        invalid_specs = [
            {"id": "MISSING-PATH", "type": "file_exists", "required": True},
            {"id": "MISSING-PATTERN", "type": "pattern", "path": "src/a/unit.py", "required": True},
            {"id": "BAD-REGEX", "type": "pattern", "path": "src/a/unit.py", "pattern": "[", "required": True},
            {"id": "UNKNOWN", "type": "unknown", "required": True},
            {"id": "CUDA", "type": "command", "argv": ["true"], "cuda": {"gpus": 0}, "required": True},
        ]
        for spec in invalid_specs:
            with self.subTest(spec=spec["id"]):
                plan = base_plan([safe_task("A", "src/a", gates=[spec])])
                with self.assertRaises(TodoError):
                    validate_plan(plan, self.repo.root)
                self.assertNotEqual(validate_gate_spec(spec, self.repo.root), [])

        self.repo.apply(self.plan)
        protocol = self._protocol()
        claimed = protocol.next_task(repo_root=str(self.repo.root))
        handle = claimed["workflow_handle"]
        for spec in invalid_specs:
            with self.subTest(live_spec=spec["id"]):
                before = self.repo.service.db.revision()
                with self.assertRaisesRegex(TodoError, "invalid specifications"):
                    protocol.coordinate_task(
                        workflow_handle=handle, action="bind_required_gates", payload={"gates": [spec]},
                    )
                self.assertEqual(self.repo.service.db.revision(), before)
                with self.repo.service.db.read() as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM gates WHERE id=?", (spec["id"],)).fetchone()[0], 0)
