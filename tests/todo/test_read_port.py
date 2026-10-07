from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.todo.v2_helpers import ROOT, V2Repo, base_plan, safe_task

from todo_orchestrator.read_port import (
    READ_PORT_CAPABILITIES,
    READ_PORT_CONTRACT,
    TodoReadPort,
    create_todo_read_port,
)


class TodoReadPortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        self.repo.apply(base_plan([safe_task("READ-1", "src/read")]))
        self.port = create_todo_read_port(ROOT)

    def tearDown(self) -> None:
        self.repo.close()

    def _authority(self) -> tuple[int, str]:
        with self.repo.service.db.read() as conn:
            revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
            return revision, hashlib.sha256(conn.serialize()).hexdigest()

    def test_identity_is_stable_explicit_and_source_bound(self) -> None:
        identity = self.port.identity()
        self.assertEqual(identity["contract"], READ_PORT_CONTRACT)
        self.assertEqual(identity["skills_root"], str(ROOT.resolve()))
        self.assertTrue(str(identity["source_identity"]).startswith("todo-orchestrator:"))
        self.assertEqual(identity["capabilities"], list(READ_PORT_CAPABILITIES))
        self.assertEqual(identity, self.port.identity())

    def test_read_port_does_not_require_todo_beneath_the_content_root(self) -> None:
        self.assertEqual(TodoReadPort(self.repo.root).identity()["skills_root"], str(self.repo.root.resolve()))

    def test_normalized_reads_match_existing_services(self) -> None:
        status = self.port.invoke("status", repo_root=self.repo.root)
        ready = self.port.invoke("ready", repo_root=self.repo.root)
        state = self.port.invoke(
            "semantic.state", repo_root=self.repo.root, arguments=("--current-only",)
        )
        explain = self.port.invoke("explain", repo_root=self.repo.root, arguments=("READ-1",))
        for payload in (status, ready, state, explain):
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["code"], "success")
        self.assertEqual(status["data"], self.repo.service.status())
        self.assertEqual(ready["data"], self.repo.service.ready())
        self.assertEqual(explain["data"], self.repo.service.explain("READ-1"))
        self.assertTrue(state["data"]["read_authority_fingerprint"])

    def test_every_capability_is_non_mutating(self) -> None:
        plan = self.repo.root / "plan.json"
        calls = {
            "status": (),
            "ready": (),
            "export": (),
            "explain": ("READ-1",),
            "changes": ("--since", "0"),
            "semantic.state": (),
            "semantic.anchor": ("--task", "READ-1"),
            "semantic.delta": ("--since-revision", "0"),
            "semantic.workflow": (),
            "plan.validate": ("--file", str(plan)),
            "plan.diff": ("--file", str(plan)),
        }
        snapshot = self.repo.root / ".todo-orchestrator" / "state.snapshot.json"
        projection = self.repo.root / "todos.md"
        before_project_files = (snapshot.read_bytes(), projection.read_bytes())
        before_git = subprocess.run(
            ["git", "-C", str(self.repo.root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True, capture_output=True, text=True,
        ).stdout
        before = self._authority()
        for operation, arguments in calls.items():
            with self.subTest(operation=operation):
                payload = self.port.invoke(operation, repo_root=self.repo.root, arguments=arguments)
                self.assertTrue(payload["ok"], payload)
        self.assertEqual(self._authority(), before)
        self.assertEqual((snapshot.read_bytes(), projection.read_bytes()), before_project_files)
        after_git = subprocess.run(
            ["git", "-C", str(self.repo.root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True, capture_output=True, text=True,
        ).stdout
        self.assertEqual(after_git, before_git)

    def test_mutating_or_malformed_calls_fail_before_dispatch(self) -> None:
        before = self._authority()
        denied = self.port.invoke("continue", repo_root=self.repo.root)
        malformed = self.port.invoke("changes", repo_root=self.repo.root, arguments=("--claim-token", "secret"))
        self.assertEqual(denied["code"], "read_port_operation_denied")
        self.assertEqual(malformed["code"], "read_port_invalid_arguments")
        self.assertEqual(self._authority(), before)

    def test_sqlite_open_failure_is_bounded_without_path_leakage(self) -> None:
        before = self._authority()
        with patch.object(
            TodoReadPort,
            "_dispatch",
            side_effect=sqlite3.OperationalError("unable to open database file /private/authority.sqlite3"),
        ):
            payload = self.port.invoke("status", repo_root=self.repo.root)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "database_open_failed")
        self.assertEqual(payload["error"]["message"], "Todo SQLite read failed")
        self.assertEqual(payload["error"]["details"], {"kind": "sqlite_operational"})
        self.assertNotIn("/private", str(payload))
        self.assertEqual(self._authority(), before)


if __name__ == "__main__":
    unittest.main()
