from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from tests.todo.scripts.todo_common import (  # noqa: E402
    DEFAULT_STALE_AFTER_DAYS,
    detect_resume_state,
    ensure_agents_md,
    ensure_root_files,
    ensure_workstream_file,
)


class ResumeLogicTests(unittest.TestCase):
    def test_detect_resume_state_finds_active_workstreams(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            ensure_root_files(repo_root)
            ensure_workstream_file(repo_root, "agent-alpha", "Alpha work", "in_progress", "alpha")
            ensure_workstream_file(repo_root, "agent-beta", "Beta work", "done", "beta")
            state = detect_resume_state(repo_root)
            self.assertTrue(state["has_root_todos"])
            self.assertEqual(len(state["active_workstreams"]), 1)
            self.assertEqual(state["active_workstreams"][0]["slug"], "agent-alpha")
            self.assertEqual(len(state["claimed_workstreams"]), 1)
            self.assertEqual(state["claimed_workstreams"][0]["slug"], "agent-alpha")
            self.assertEqual(state["pickup_ready_workstreams"], [])

    def test_detect_resume_state_exposes_pickup_ready_idle_streams(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            ensure_root_files(repo_root)
            ensure_workstream_file(repo_root, "agent-alpha", "Alpha work", "planned", "alpha", execution="ready")
            ensure_workstream_file(repo_root, "agent-beta", "Beta work", "in_progress", "beta", execution="idle")
            ensure_workstream_file(repo_root, "agent-gamma", "Gamma work", "in_progress", "gamma", execution="claimed")
            state = detect_resume_state(repo_root)
            ready = [entry["slug"] for entry in state["pickup_ready_workstreams"]]
            self.assertEqual(ready, ["agent-alpha", "agent-beta"])
            claimed = [entry["slug"] for entry in state["claimed_workstreams"]]
            self.assertEqual(claimed, ["agent-gamma"])
            self.assertFalse(state["cleanup_ready"])

    def test_detect_resume_state_surfaces_stale_streams(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            ensure_root_files(repo_root)
            ensure_workstream_file(repo_root, "agent-alpha", "Alpha work", "stale", "alpha", execution="closed")
            state = detect_resume_state(repo_root)
            stale = [entry["slug"] for entry in state["stale_workstreams"]]
            self.assertEqual(stale, ["agent-alpha"])
            self.assertEqual(state["pickup_ready_workstreams"], [])
            self.assertEqual(state["stale_candidate_workstreams"], [])

    def test_agents_block_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            first = ensure_agents_md(repo_root)
            second = ensure_agents_md(repo_root)
            self.assertEqual(first, second)
            text = first.read_text(encoding="utf-8")
            self.assertEqual(text.count("todo-orchestrator:start"), 1)
            self.assertIn("consult `todos.md` first", text)

    def test_default_stale_windows_are_short(self) -> None:
        self.assertEqual(DEFAULT_STALE_AFTER_DAYS["planned"], 3)
        self.assertEqual(DEFAULT_STALE_AFTER_DAYS["in_progress"], 3)
        self.assertEqual(DEFAULT_STALE_AFTER_DAYS["blocked"], 7)
        self.assertEqual(DEFAULT_STALE_AFTER_DAYS["stale"], 3)


if __name__ == "__main__":
    unittest.main()
