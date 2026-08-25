from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from project_control.adapters.git import GitReadAdapter
from project_control.adapters.todo import TodoReadAdapter
from project_control.subprocesses import FixedCommandRunner


TODO = Path("/home/tumlinson/.agents/skills/todo-orchestrator/scripts/todo.py")


def run(argv: list[str], cwd: Path) -> str:
    return subprocess.run(argv, cwd=cwd, check=True, text=True, capture_output=True).stdout


def project_manifest(root: Path) -> dict[str, str]:
    ignored = {".git", ".todo-orchestrator", "todos"}
    values = {}
    for path in root.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        if path.name in {"todos.md", "todo-status.md"}:
            continue
        values[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


@unittest.skipUnless(TODO.is_file(), "local todo-orchestrator integration unavailable")
class AdapterContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        run(["git", "init", "-b", "main"], self.root)
        run(["git", "config", "user.email", "tests@example.invalid"], self.root)
        run(["git", "config", "user.name", "Project Control Tests"], self.root)
        (self.root / "source.txt").write_text("one\n", encoding="utf-8")
        run(["git", "add", "source.txt"], self.root)
        run(["git", "commit", "-m", "fixture"], self.root)
        run(["python", str(TODO), "bootstrap", "--repo-root", ".", "--name", "Fixture", "--json"], self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def identity(self) -> tuple[str, str, dict[str, str], int]:
        head = run(["git", "rev-parse", "HEAD"], self.root).strip()
        status = run(["git", "status", "--porcelain=v2"], self.root)
        manifest = project_manifest(self.root)
        raw = run(["python", str(TODO), "status", "--repo-root", ".", "--json"], self.root)
        revision = json.loads(raw)["data"]["project_revision"]
        return head, status, manifest, revision

    def test_public_todo_reads_do_not_mutate_authority(self) -> None:
        adapter = TodoReadAdapter(self.root, TODO)
        before = self.identity()
        status = adapter.status()
        adapter.ready()
        adapter.changes(0)
        observation = adapter.observe()
        after = self.identity()
        self.assertEqual(before, after)
        self.assertEqual(observation.revision, status["data"]["project_revision"])

    def test_explain_is_read_only(self) -> None:
        # No task exists, but the public missing-task path must not mutate.
        adapter = TodoReadAdapter(self.root, TODO)
        before = self.identity()
        result = adapter.explain("MISSING")
        self.assertIn(result.get("code"), {"success", "not_found"})
        self.assertEqual(before, self.identity())

    def test_git_adapter_uses_bounded_read_operations(self) -> None:
        adapter = GitReadAdapter(self.root)
        before = self.identity()
        identity = adapter.identity()
        self.assertFalse(identity.dirty)
        self.assertEqual(adapter.recent_commits(1)[0]["subject"], "fixture")
        self.assertEqual(adapter.show_text("HEAD", "source.txt"), "one\n")
        self.assertEqual(before, self.identity())
        with self.assertRaises(ValueError):
            adapter._git("fetch")

    def test_runner_never_uses_shell_and_bounds_output(self) -> None:
        runner = FixedCommandRunner(max_capture_bytes=3)
        with self.assertRaises(Exception):
            runner.run(["python", "-c", "print('long')"], cwd=self.root)


if __name__ == "__main__":
    unittest.main()
