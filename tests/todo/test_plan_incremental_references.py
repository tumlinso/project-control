from __future__ import annotations

import json
import subprocess
import sys
import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.claims import claim_best
from todo_orchestrator.models import TodoError
from todo_orchestrator.sessions import create_session


class IncrementalPlanReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        initial = base_plan(
            [
                safe_task("TASK-A", "src/a"),
                safe_task(
                    "TASK-B",
                    "src/b",
                    invariants=["INV-EXISTING"],
                    depends_on=[{"type": "task", "task_id": "TASK-A"}],
                    gates=[{"id": "GATE-B", "type": "manual"}],
                ),
            ],
            invariants=[{"id": "INV-EXISTING", "rule": "Keep the existing contract."}],
        )
        self.repo.apply(initial)

    def tearDown(self) -> None:
        self.repo.close()

    def _active_claim_for_a(self) -> str:
        result, _ = self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="claim",
            entity_id="TASK-A",
            event_type="test.incremental_claim",
            payload={},
            operation=lambda conn, revision: self._claim_in_transaction(conn, revision),
        )
        return str(result["claim_id"])

    def _claim_in_transaction(self, conn, revision):
        session, _ = create_session(conn, self.repo.root, {"test": True})
        claim, _ = claim_best(
            conn,
            self.repo.root,
            session["agent_id"],
            revision,
            7200,
            requested_task_id="TASK-A",
        )
        return {"session_id": session["agent_id"], "claim_id": claim["claim_id"]}

    def test_incremental_diff_and_apply_resolve_retained_references_from_authority(self) -> None:
        self.repo.service.db.mutate(
            actor_session_id=None,
            entity_type="fixture",
            entity_id="TASK-B",
            event_type="test.incremental_result",
            payload={},
            operation=lambda conn, revision: conn.execute(
                "UPDATE tasks SET result='preserve this result' WHERE id='TASK-B'"
            ),
        )
        claim_id = self._active_claim_for_a()
        with self.repo.service.db.read() as conn:
            before_claim = dict(conn.execute("SELECT id,task_id,state FROM claims WHERE id=?", (claim_id,)).fetchone())
            before_counts = {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("tasks", "invariants", "task_invariants", "task_dependencies", "gates", "claims")
            }

        # This fragment intentionally refers to existing database definitions
        # omitted from its payload. `plan_validate` stays standalone/strict;
        # diff/apply resolve the references against the current authority.
        fragment = base_plan([
            {
                "id": "TASK-B",
                "title": "Updated task B",
                "invariants": ["INV-EXISTING"],
                "depends_on": [{"type": "task", "task_id": "TASK-A"}],
                "gates": [{"id": "GATE-B", "type": "manual"}],
            }
        ])
        path = self.repo.root / "incremental.json"
        path.write_text(json.dumps(fragment), encoding="utf-8")
        diff = self.repo.service.plan_diff(str(path))
        self.assertEqual(["TASK-B"], diff["update"])
        applied = self.repo.service.plan_apply(str(path))
        self.assertEqual(1, applied["tasks_upserted"])

        with self.repo.service.db.read() as conn:
            task = dict(conn.execute("SELECT title,result FROM tasks WHERE id='TASK-B'").fetchone())
            invariant = conn.execute(
                "SELECT invariant_id FROM task_invariants WHERE task_id='TASK-B'"
            ).fetchall()
            dependency = conn.execute(
                "SELECT type,prerequisite_task_id FROM task_dependencies WHERE task_id='TASK-B'"
            ).fetchall()
            gate = conn.execute("SELECT id,type FROM gates WHERE task_id='TASK-B'").fetchall()
            after_claim = dict(conn.execute("SELECT id,task_id,state FROM claims WHERE id=?", (claim_id,)).fetchone())
            after_counts = {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("tasks", "invariants", "task_invariants", "task_dependencies", "gates", "claims")
            }
        self.assertEqual({"title": "Updated task B", "result": "preserve this result"}, task)
        self.assertEqual(["INV-EXISTING"], [row[0] for row in invariant])
        self.assertEqual([("task", "TASK-A")], [(row["type"], row["prerequisite_task_id"]) for row in dependency])
        self.assertEqual([("GATE-B", "manual")], [(row["id"], row["type"]) for row in gate])
        self.assertEqual(before_claim, after_claim)
        self.assertEqual(before_counts, after_counts)

    def test_incremental_paths_reject_unknown_references_and_do_not_mutate(self) -> None:
        for task_patch, expected_fragment in (
            ({"id": "TASK-B", "title": "Bad invariant", "invariants": ["INV-MISSING"]}, "invariant INV-MISSING"),
            ({"id": "TASK-B", "title": "Bad dependency", "depends_on": [{"type": "task", "task_id": "TASK-MISSING"}]}, "task dependency TASK-MISSING"),
        ):
            with self.subTest(expected_fragment=expected_fragment):
                path = self.repo.root / "invalid-incremental.json"
                path.write_text(json.dumps(base_plan([task_patch])), encoding="utf-8")
                before = self.repo.service.db.revision()
                with self.assertRaises(TodoError) as diff_error:
                    self.repo.service.plan_diff(str(path))
                self.assertEqual("plan_validation_failed", diff_error.exception.code)
                self.assertTrue(any(expected_fragment in item for item in diff_error.exception.details["errors"]))
                with self.assertRaises(TodoError) as apply_error:
                    self.repo.service.plan_apply(str(path))
                self.assertEqual("plan_validation_failed", apply_error.exception.code)
                self.assertEqual(before, self.repo.service.db.revision())

    def test_standalone_validation_remains_strict_for_external_references(self) -> None:
        path = self.repo.root / "standalone-fragment.json"
        path.write_text(
            json.dumps(base_plan([{"id": "TASK-B", "title": "External ref", "invariants": ["INV-EXISTING"]}])),
            encoding="utf-8",
        )
        with self.assertRaises(TodoError) as caught:
            self.repo.service.plan_validate(str(path))
        self.assertEqual("plan_validation_failed", caught.exception.code)
        self.assertTrue(any("unknown invariant INV-EXISTING" in item for item in caught.exception.details["errors"]))

    def test_incremental_apply_still_rejects_cycle_through_existing_tasks(self) -> None:
        path = self.repo.root / "cycle-incremental.json"
        path.write_text(
            json.dumps(base_plan([{
                "id": "TASK-A",
                "title": "Would create a cycle",
                "depends_on": [{"type": "task", "task_id": "TASK-B"}],
            }])),
            encoding="utf-8",
        )
        before = self.repo.service.db.revision()
        with self.assertRaises(TodoError) as caught:
            self.repo.service.plan_apply(str(path))
        self.assertEqual("cyclic_graph", caught.exception.code)
        self.assertEqual(before, self.repo.service.db.revision())

    def _bounded_plan_call(self, method: str, path) -> subprocess.CompletedProcess[str]:
        code = (
            "import json,sys\n"
            "from todo_orchestrator.service import Service\n"
            "from todo_orchestrator.models import TodoError\n"
            "service=Service(sys.argv[1])\n"
            "try:\n"
            " result=getattr(service, sys.argv[2])(sys.argv[3])\n"
            " print('ok')\n"
            "except TodoError as exc:\n"
            " print(json.dumps(exc.details)); sys.exit(3)\n"
        )
        return subprocess.run(
            [sys.executable, "-c", code, str(self.repo.root), method, str(path)],
            capture_output=True, text=True, timeout=10, check=False,
        )

    def test_incremental_parent_outside_fragment_is_already_satisfied(self) -> None:
        initial = base_plan([
            {"id": "PARENT", "title": "Parent"},
            {"id": "CHILD", "parent_id": "PARENT", "title": "Child"},
        ])
        self.repo.apply(initial)
        fragment = base_plan([{"id": "CHILD", "title": "Updated child"}])
        path = self.repo.root / "external-parent.json"
        path.write_text(json.dumps(fragment), encoding="utf-8")

        diff = self._bounded_plan_call("plan_diff", path)
        apply = self._bounded_plan_call("plan_apply", path)

        self.assertEqual(0, diff.returncode, diff.stderr)
        self.assertEqual(0, apply.returncode, apply.stderr)
        with self.repo.service.db.read() as conn:
            row = conn.execute("SELECT title,parent_id FROM tasks WHERE id='CHILD'").fetchone()
        self.assertEqual(("Updated child", "PARENT"), tuple(row))

    def test_incremental_unknown_parent_is_rejected_without_hanging_or_mutating(self) -> None:
        fragment = base_plan([{
            "id": "TASK-B", "title": "Bad parent", "parent_id": "TASK-MISSING",
        }])
        path = self.repo.root / "unknown-parent.json"
        path.write_text(json.dumps(fragment), encoding="utf-8")
        before = self.repo.service.db.revision()

        diff = self._bounded_plan_call("plan_diff", path)
        apply = self._bounded_plan_call("plan_apply", path)

        for result in (diff, apply):
            self.assertNotEqual(0, result.returncode)
            self.assertIn("unknown parent TASK-MISSING", result.stdout)
        self.assertEqual(before, self.repo.service.db.revision())


if __name__ == "__main__":
    unittest.main()
