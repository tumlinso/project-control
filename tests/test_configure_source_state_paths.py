from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control.config import ProjectControlConfig
from scripts.configure_source_state_paths import (
    discover_state_directories,
    install_drop_in,
    main,
    render_drop_in,
)
from tests.todo.v2_helpers import V2Repo


class ConfigureSourceStatePathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)

    def tearDown(self) -> None:
        self.repo.close()
        self.temp.cleanup()

    def _config(self, *roots: Path) -> ProjectControlConfig:
        return ProjectControlConfig.model_validate({
            "schema_version": 2,
            "workspaces": {
                f"workspace-{index}": {
                    "repositories": {f"repo-{index}": {"root": str(root)}}
                }
                for index, root in enumerate(roots)
            },
        })

    def test_only_existing_canonical_configured_state_directory_is_granted(self) -> None:
        unbootstrapped = self.temp_path / "unbootstrapped"
        unbootstrapped.mkdir()
        config = self._config(self.repo.root, unbootstrapped)

        directories, reports = discover_state_directories(config)
        expected = self.repo.service.paths.state_dir.resolve()
        self.assertEqual([expected], directories)
        self.assertEqual(["included", "skipped"], [report.status for report in reports])
        self.assertEqual("project_not_bootstrapped", reports[1].reason)
        scoped_directories, scoped_reports = discover_state_directories(config, project_id="workspace-0")
        self.assertEqual([expected], scoped_directories)
        self.assertEqual(["workspace-0"], [report.workspace for report in scoped_reports])
        skipped_directories, skipped_reports = discover_state_directories(config, project_id="workspace-1")
        self.assertEqual([], skipped_directories)
        self.assertEqual(["project_not_bootstrapped"], [report.reason for report in skipped_reports])
        content = render_drop_in(directories)
        self.assertIn(f'ReadWritePaths="{expected}"', content)
        self.assertNotIn(".cache/project-control", content)
        self.assertNotIn(".local/state/project-control", content)

    def test_bootstrapped_repository_without_existing_database_is_skipped(self) -> None:
        missing = self.temp_path / "missing-ledger"
        missing.mkdir()
        (missing / ".todo-orchestrator").mkdir()
        (missing / ".todo-orchestrator" / "project.json").write_text(
            json.dumps({"schema_version": 2, "project_uuid": "00000000-0000-4000-8000-000000000001"}),
            encoding="utf-8",
        )
        config = self._config(missing)

        directories, reports = discover_state_directories(config)
        self.assertEqual([], directories)
        self.assertEqual("skipped", reports[0].status)
        self.assertEqual("todo_state_missing", reports[0].reason)

    def test_malformed_project_uuid_cannot_grant_external_state_directory(self) -> None:
        state_base = self.temp_path / "state-base"
        cases = (
            ("../traversal-state", (state_base / "../traversal-state").resolve()),
            (str(self.temp_path / "absolute-state"), self.temp_path / "absolute-state"),
        )
        with patch.dict(os.environ, {"TODO_ORCHESTRATOR_STATE_DIR": str(state_base)}):
            for index, (project_uuid, external_state) in enumerate(cases):
                with self.subTest(project_uuid=project_uuid):
                    repo_root = self.temp_path / f"malformed-{index}"
                    identity_dir = repo_root / ".todo-orchestrator"
                    identity_dir.mkdir(parents=True)
                    (identity_dir / "project.json").write_text(
                        json.dumps({"schema_version": 2, "project_uuid": project_uuid}),
                        encoding="utf-8",
                    )
                    external_state.mkdir(parents=True, exist_ok=True)
                    (external_state / "state.sqlite3").write_bytes(b"existing external state")

                    with self.assertRaisesRegex(RuntimeError, "invalid project UUID"):
                        discover_state_directories(self._config(repo_root))

    def test_default_cli_is_dry_run_and_does_not_create_drop_in(self) -> None:
        config_path = self.temp_path / "config.toml"
        config_path.write_text(
            f'[workspaces.repo.repositories.main]\nroot = "{self.repo.root}"\n', encoding="utf-8"
        )
        config_path.chmod(0o600)
        target = self.temp_path / "systemd" / "project-control.service.d" / "todo.conf"
        output = io.StringIO()
        with patch.dict(os.environ, {"TODO_ORCHESTRATOR_STATE_DIR": str(self.repo.state_root)}):
            with contextlib.redirect_stdout(output):
                code = main(["--config", str(config_path), "--drop-in", str(target)])
        self.assertEqual(0, code)
        result = json.loads(output.getvalue())
        self.assertEqual("dry_run", result["mode"])
        self.assertEqual("all_registered_dry_run_default", result["selection"])
        self.assertFalse(target.exists())
        self.assertFalse(target.parent.exists())

    def test_all_projects_must_be_selected_explicitly_for_broad_dry_run(self) -> None:
        config_path = self.temp_path / "all-config.toml"
        config_path.write_text(
            f'[workspaces.project-control.repositories.main]\nroot = "{self.repo.root}"\n'
            f'[workspaces.other.repositories.other]\nroot = "{self.temp_path}"\n',
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        target = self.temp_path / "all.service.d" / "state.conf"
        output = io.StringIO()
        with patch.dict(os.environ, {"TODO_ORCHESTRATOR_STATE_DIR": str(self.repo.state_root)}):
            with contextlib.redirect_stdout(output):
                code = main([
                    "--config", str(config_path), "--drop-in", str(target), "--all-projects",
                ])
        self.assertEqual(0, code)
        result = json.loads(output.getvalue())
        self.assertEqual("all_projects_explicit", result["selection"])
        self.assertEqual([str(self.repo.service.paths.state_dir.resolve())], result["state_directories"])
        self.assertFalse(target.exists())

    def test_apply_preserves_unique_backups_across_replacements(self) -> None:
        target = self.temp_path / "drop-in.conf"
        target.write_text("old drop-in\n", encoding="utf-8")
        content = render_drop_in([self.repo.service.paths.state_dir])

        result = install_drop_in(target, content)
        self.assertTrue(result.startswith("installed_with_backup:"))
        first_backup = Path(result.removeprefix("installed_with_backup:"))
        self.assertTrue(first_backup.is_file())
        self.assertEqual("old drop-in\n", first_backup.read_text(encoding="utf-8"))
        self.assertEqual(content, target.read_text(encoding="utf-8"))

        second = install_drop_in(target, "replacement content\n")
        second_backup = Path(second.removeprefix("installed_with_backup:"))
        self.assertNotEqual(first_backup, second_backup)
        self.assertEqual(content, second_backup.read_text(encoding="utf-8"))
        self.assertEqual("replacement content\n", target.read_text(encoding="utf-8"))

    def test_apply_requires_project_filter_and_writes_only_selected_workspace(self) -> None:
        config_path = self.temp_path / "scoped-config.toml"
        config_path.write_text(
            f'[workspaces.project-control.repositories.main]\nroot = "{self.repo.root}"\n'
            f'[workspaces.other.repositories.other]\nroot = "{self.temp_path}"\n',
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        target = self.temp_path / "scoped.service.d" / "state.conf"
        output = io.StringIO()
        with patch.dict(os.environ, {"TODO_ORCHESTRATOR_STATE_DIR": str(self.repo.state_root)}):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(2, main(["--config", str(config_path), "--drop-in", str(target), "--apply"]))
            with contextlib.redirect_stdout(output):
                code = main([
                    "--config", str(config_path), "--drop-in", str(target),
                    "--project", "project-control", "--apply",
                ])
        self.assertEqual(0, code)
        result = json.loads(output.getvalue())
        self.assertEqual("project-control", result["project_filter"])
        self.assertEqual("project", result["selection"])
        self.assertEqual([str(self.repo.service.paths.state_dir.resolve())], result["state_directories"])
        self.assertEqual(result["content"], target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
