from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from project_control.adapters.git import GitReadAdapter
from project_control.adapters.todo import TodoReadAdapter, TodoReadError
from project_control.subprocesses import CommandResult, FixedCommandRunner


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


class SequenceRunner:
    def __init__(self, payloads: list[tuple[int, dict]]) -> None:
        self.payloads = list(payloads)
        self.calls: list[tuple[list[str], dict]] = []

    def run(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        returncode, payload = self.payloads.pop(0)
        return CommandResult(Path(argv[0]).name, returncode, json.dumps(payload), "ignored stderr")


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

    def test_nested_registration_resolves_parent_export_authority(self) -> None:
        nested = self.root / "component"
        nested.mkdir()
        observation = TodoReadAdapter(nested, TODO).observe()
        self.assertIsInstance(observation.revision, int)
        self.assertTrue(observation.state.get("tables", {}).get("tasks") is not None)

    def test_state_roots_follow_git_common_dir_indirection(self) -> None:
        worktree = Path(self.temporary.name) / "worktree"
        run(["git", "worktree", "add", "-b", "fixture-worktree", str(worktree)], self.root)
        control = worktree / ".todo-orchestrator"
        control.mkdir()
        (control / "project.json").write_bytes((self.root / ".todo-orchestrator" / "project.json").read_bytes())
        roots = TodoReadAdapter(worktree, TODO).state_roots()
        common = (self.root / ".git").resolve()
        self.assertTrue(any(path.is_relative_to(common / "todo-orchestrator") for path in roots))

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

    def test_git_grep_no_match_is_clean(self) -> None:
        self.assertEqual(GitReadAdapter(self.root).grep("DEFINITELY_NOT_PRESENT"), [])

    def test_todo_uses_current_interpreter_and_safe_state_environment(self) -> None:
        runner = SequenceRunner([(0, {"ok": True, "code": "success", "data": {"project_revision": 1}})])
        adapter = TodoReadAdapter(self.root, TODO, runner=runner)
        with patch.dict(os.environ, {"TODO_ORCHESTRATOR_STATE_DIR": "/tmp/todo-state", "UNSAFE_TEST_VALUE": "no"}, clear=False):
            self.assertEqual(adapter.safe_environment()["TODO_ORCHESTRATOR_STATE_DIR"], "/tmp/todo-state")
            self.assertNotIn("UNSAFE_TEST_VALUE", adapter.safe_environment())
            self.assertEqual(adapter.safe_environment()["TODO_ORCHESTRATOR_READ_ONLY"], "1")
            adapter.status()
        self.assertEqual(runner.calls[0][0][0], sys.executable)
        self.assertFalse(runner.calls[0][1]["check"])
        self.assertEqual(Path(adapter.todo_script), TODO.resolve())

    def test_structured_nonzero_error_is_parsed(self) -> None:
        runner = SequenceRunner([(16, {"ok": False, "code": "project_not_bootstrapped", "error": {"message": "hidden"}})])
        with self.assertRaises(TodoReadError) as caught:
            TodoReadAdapter(self.root, TODO, runner=runner).status()
        self.assertEqual(caught.exception.code, "todo_project_not_bootstrapped")

    def test_observation_enforces_revision_consistency_with_one_retry(self) -> None:
        project = {"project_uuid": "fixture-uuid"}
        def status(revision):
            return (0, {"ok": True, "code": "success", "data": {"project_revision": revision}})
        def export(revision):
            return (0, {"ok": True, "code": "success", "data": {"project_revision": revision, "state": {"project_revision": revision, "project": project, "tables": {}}}})
        def semantic(revision):
            return (0, {"ok": True, "code": "success", "data": {"revision": revision, "tasks": []}})
        runner = SequenceRunner([status(1), export(2), semantic(2), status(3), export(4), semantic(4)])
        observation = TodoReadAdapter(self.root, TODO, runner=runner).observe()
        self.assertEqual(len(runner.calls), 6)
        self.assertEqual(observation.warnings, ("todo_observation_raced",))
        self.assertEqual(observation.state, {})

    def test_observation_preserves_raw_fallback_when_semantic_command_is_unavailable(self) -> None:
        project = {"project_uuid": "fixture-uuid"}
        runner = SequenceRunner([
            (0, {"ok": True, "code": "success", "data": {"project_revision": 5}}),
            (0, {"ok": True, "code": "success", "data": {"state": {"project_revision": 5, "project": project, "tables": {}}}}),
            (2, {"ok": False, "code": "internal_error", "error": {"message": "unsupported"}}),
        ])
        observation = TodoReadAdapter(self.root, TODO, runner=runner).observe()
        self.assertEqual(observation.revision, 5)
        self.assertEqual(observation.semantic, {})
        self.assertEqual(observation.warnings, ("todo_semantic_unavailable",))

    def test_revision_read_does_not_export(self) -> None:
        runner = SequenceRunner([(0, {"ok": True, "code": "success", "data": {"project_revision": 9}})])
        adapter = TodoReadAdapter(self.root, TODO, runner=runner)
        self.assertEqual(adapter.revision(), 9)
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("status", runner.calls[0][0])
        self.assertNotIn("export", runner.calls[0][0])

    def test_runner_never_uses_shell_and_bounds_output(self) -> None:
        runner = FixedCommandRunner(max_capture_bytes=3)
        with self.assertRaises(Exception):
            runner.run(["python", "-c", "print('long')"], cwd=self.root)


if __name__ == "__main__":
    unittest.main()
