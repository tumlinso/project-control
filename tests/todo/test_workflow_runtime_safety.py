from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.todo.v2_helpers import V2Repo  # noqa: F401 - establishes package path

from todo_orchestrator.models import TodoError
from todo_orchestrator.workflow.service import WorkflowKernel


class WorkflowRuntimeSafetyTests(unittest.TestCase):
    def test_next_task_never_bootstraps_missing_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kernel = WorkflowKernel()
            with self.assertRaises(TodoError) as raised:
                kernel.next_task(repo_root=str(root), task_id=None)
            self.assertEqual(raised.exception.code, "project_not_bootstrapped")
            self.assertFalse((root / ".todo-orchestrator").exists())

    def test_runtime_failure_is_not_reinterpreted_as_bootstrap(self) -> None:
        calls = []

        def failed(root):
            calls.append(Path(root))
            raise TodoError("runtime_identity_mismatch", "restart this process")

        kernel = WorkflowKernel(service_factory=failed)
        with self.assertRaises(TodoError) as raised:
            kernel.next_task(repo_root=".", task_id=None)
        self.assertEqual(raised.exception.code, "runtime_identity_mismatch")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
