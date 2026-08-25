from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import PlanPreviewInput, ProjectSnapshot, RepositoryIdentity
from project_control.services.planning import plan_preview


TODO = Path("/home/tumlinson/.agents/skills/todo-orchestrator/scripts/todo.py")
SKILLS = Path("/home/tumlinson/.agents/skills")


def run(argv: list[str], root: Path) -> str:
    return subprocess.run(argv, cwd=root, check=True, text=True, capture_output=True).stdout


class PlanPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.cache = Path(self.temporary.name) / "cache"
        self.root.mkdir()
        run(["git", "init", "-b", "main"], self.root)
        run(["git", "config", "user.email", "tests@example.invalid"], self.root)
        run(["git", "config", "user.name", "Tests"], self.root)
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        (self.root / ".gitignore").write_text(".todo-orchestrator/\ntodos/\ntodos.md\ntodo-status.md\n", encoding="utf-8")
        run(["git", "add", "README.md", ".gitignore"], self.root)
        run(["git", "commit", "-m", "fixture"], self.root)
        run(["python", str(TODO), "bootstrap", "--repo-root", ".", "--name", "Fixture", "--json"], self.root)
        self.config = ProjectControlConfig(skills_root=SKILLS, workspaces={
            "demo": WorkspaceConfig(authority_repository="source", repositories={"source": RepositoryConfig(root=self.root)})
        })
        status = json.loads(run(["python", str(TODO), "status", "--repo-root", ".", "--json"], self.root))
        commit = run(["git", "rev-parse", "HEAD"], self.root).strip()
        self.snapshot = ProjectSnapshot(
            workspace_id="demo", observed_at="2026-08-25T00:00:00Z",
            todo_revision=status["data"]["project_revision"],
            repositories={"source": RepositoryIdentity(commit=commit, dirty=False)},
            todo_status=status["data"], todo_tables={},
        )
        self.proposal = {"schema_version": 2, "project": {"name": "Fixture"}, "invariants": [], "locks": [], "interfaces": [], "tasks": []}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def identities(self):
        return (
            run(["git", "rev-parse", "HEAD"], self.root),
            run(["git", "status", "--porcelain=v2"], self.root),
            json.loads(run(["python", str(TODO), "status", "--repo-root", ".", "--json"], self.root))["data"]["project_revision"],
        )

    def test_context_is_prospective(self) -> None:
        result = plan_preview(self.config, self.snapshot, PlanPreviewInput(project="demo", mode="context"))
        self.assertEqual(result.data["plan_schema_version"], 2)

    def test_validate_and_handoff_do_not_mutate(self) -> None:
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)}):
            before = self.identities()
            validated = plan_preview(self.config, self.snapshot, PlanPreviewInput(project="demo", mode="validate", proposal=self.proposal))
            after_validate = self.identities()
            handed = plan_preview(self.config, self.snapshot, PlanPreviewInput(project="demo", mode="handoff", proposal=self.proposal, objective="Test"))
            after_handoff = self.identities()
        self.assertEqual(before, after_validate)
        self.assertEqual(before, after_handoff)
        self.assertTrue(validated.data["valid"])
        self.assertEqual(validated.data["mutation_guard"], "unchanged")
        self.assertEqual(handed.data["handoff"]["handoff_version"], 1)
        self.assertFalse(any(self.cache.rglob("proposal-*.json")))

    def test_proposal_limit_is_enforced(self) -> None:
        with self.assertRaises(ValueError):
            PlanPreviewInput(project="demo", mode="validate", proposal={"blob": "x" * (257 * 1024)})


if __name__ == "__main__":
    unittest.main()
