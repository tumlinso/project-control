from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from project_control.worktrees import WorktreeCatalog, WorktreeSelectionError


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, text=True, capture_output=True).stdout


class WorktreeCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "primary"
        self.root.mkdir()
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Tests")
        git(self.root, "config", "user.email", "tests@example.invalid")
        (self.root / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(self.root, "add", "source.py")
        git(self.root, "commit", "-m", "initial")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_discovers_primary_branch_detached_and_dirty_with_stable_ids(self) -> None:
        branch = Path(self.temp.name) / "branch"
        detached = Path(self.temp.name) / "detached"
        git(self.root, "worktree", "add", "-b", "feature", str(branch))
        git(self.root, "worktree", "add", "--detach", str(detached))
        (branch / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
        before = git(self.root, "show-ref")
        first = WorktreeCatalog("source", self.root).discover()
        second = WorktreeCatalog("source", self.root).discover()
        self.assertEqual([item.worktree_id for item in first], [item.worktree_id for item in second])
        self.assertEqual(3, len(first))
        self.assertTrue(next(item for item in first if item.root == branch.resolve()).dirty)
        self.assertTrue(next(item for item in first if item.root == detached.resolve()).detached)
        self.assertEqual(before, git(self.root, "show-ref"))

    def test_selection_is_redacted_and_rejects_unknown_identity(self) -> None:
        catalog = WorktreeCatalog("source", self.root)
        selected = catalog.select()
        public = selected.public()
        self.assertNotIn(str(self.root), str(public))
        self.assertEqual("source", public["repository"])
        with self.assertRaisesRegex(WorktreeSelectionError, "unknown_or_unrelated_worktree"):
            catalog.select("wt-not-related")

    def test_dirty_content_changes_working_tree_fingerprint(self) -> None:
        path = self.root / "source.py"
        path.write_text("VALUE = 2\n", encoding="utf-8")
        first = WorktreeCatalog("source", self.root).select().selected.working_tree_fingerprint
        path.write_text("VALUE = 3\n", encoding="utf-8")
        second = WorktreeCatalog("source", self.root).select().selected.working_tree_fingerprint
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
