from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from project_control.migration import (
    END_MARKER,
    LEGACY_END_MARKER,
    LEGACY_START_MARKER,
    START_MARKER,
    MigrationError,
    migrate,
)


class RepositoryMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".todo-orchestrator").mkdir()
        self.project_path = self.root / ".todo-orchestrator/project.json"
        self.project_path.write_text(json.dumps({
            "project_uuid": "preserved",
            "configuration": {"workflow_front_door": "coding-workflow", "other": True},
        }, indent=2) + "\n")
        (self.root / "AGENTS.md").write_text(
            "# Guidance\n\nUser text before.\n\n" + LEGACY_START_MARKER +
            "\nlegacy owned\n" + LEGACY_END_MARKER + "\n\nUser text after.\n"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dry_run_is_non_mutating_and_recognizes_old_marker(self) -> None:
        before_agents = (self.root / "AGENTS.md").read_bytes()
        before_project = self.project_path.read_bytes()
        result = migrate(self.root)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["recognized_markers"], ["coding-workflow"])
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), before_agents)
        self.assertEqual(self.project_path.read_bytes(), before_project)
        self.assertFalse((self.root / ".project-control").exists())

    def test_apply_reapply_and_remove_are_idempotent_and_reversible(self) -> None:
        original_agents = (self.root / "AGENTS.md").read_text()
        original_project = json.loads(self.project_path.read_text())
        applied = migrate(self.root, apply=True)
        self.assertEqual(applied["status"], "applied")
        agents = (self.root / "AGENTS.md").read_text()
        self.assertIn(START_MARKER, agents)
        self.assertNotIn(LEGACY_START_MARKER, agents)
        self.assertIn("User text before.", agents)
        self.assertIn("User text after.", agents)
        project = json.loads(self.project_path.read_text())
        self.assertEqual(project["project_uuid"], "preserved")
        self.assertEqual(project["configuration"]["workflow_front_door"], "project-control")
        self.assertTrue(project["configuration"]["other"])
        self.assertEqual(migrate(self.root, apply=True)["status"], "unchanged")

        removed = migrate(self.root, apply=True, remove=True)
        self.assertEqual(removed["status"], "applied")
        self.assertEqual((self.root / "AGENTS.md").read_text(), original_agents.replace(
            LEGACY_START_MARKER + "\nlegacy owned\n" + LEGACY_END_MARKER + "\n\n", ""
        ))
        self.assertEqual(json.loads(self.project_path.read_text()), original_project)
        self.assertFalse((self.root / ".project-control/pcu-v1-migration.json").exists())
        self.assertEqual(migrate(self.root, apply=True, remove=True)["status"], "unchanged")

    def test_rejects_ambiguous_dual_markers(self) -> None:
        (self.root / "AGENTS.md").write_text(
            f"{START_MARKER}\nnew\n{END_MARKER}\n"
            f"{LEGACY_START_MARKER}\nold\n{LEGACY_END_MARKER}\n"
        )
        with self.assertRaises(MigrationError):
            migrate(self.root)


if __name__ == "__main__":
    unittest.main()
