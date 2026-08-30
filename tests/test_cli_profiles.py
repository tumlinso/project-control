from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control.cli import _serve_profile, main
from project_control.config import ProjectControlConfig, configured_skills_root


class ProfileCliTests(unittest.TestCase):
    def test_legacy_serve_is_observer_and_explicit_codex_is_stdio(self) -> None:
        with patch("project_control.cli._serve_profile", return_value=0) as serve:
            self.assertEqual(main(["serve"]), 0)
            serve.assert_called_once_with("observer", host=None, port=None)
        with patch("project_control.cli._serve_profile", return_value=0) as serve:
            self.assertEqual(main(["serve", "codex"]), 0)
            serve.assert_called_once_with("codex", host=None, port=None)

    def test_codex_rejects_network_options_before_importing_server(self) -> None:
        with self.assertRaisesRegex(ValueError, "stdio"):
            _serve_profile("codex", host="127.0.0.1", port=None)

    def test_migration_cli_dry_run_and_remove_modes(self) -> None:
        with patch("project_control.migration.migrate", return_value={"status": "dry_run"}) as migrate, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(["migrate-repository", "--repo", "/repo", "--dry-run"]), 0)
        migrate.assert_called_once_with(Path("/repo"), apply=False, remove=False)
        self.assertEqual(json.loads(output.getvalue()), {"status": "dry_run"})
        with patch("project_control.migration.migrate", return_value={"status": "applied"}) as migrate, \
             patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(main(["migrate-repository", "--repo", "/repo", "--remove"]), 0)
        migrate.assert_called_once_with(Path("/repo"), apply=True, remove=True)

    def test_admin_recovery_is_exposed_by_main_cli(self) -> None:
        with patch("project_control.admin.inspect_recovery", return_value={"status": "safe"}) as inspect, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(["admin", "recover", "--repo", "/repo", "--reason", "owner", "--inspect-only"]), 0)
        inspect.assert_called_once_with("/repo", None)
        self.assertEqual(json.loads(output.getvalue()), {"status": "safe"})

    def test_canonical_root_wins_and_legacy_alias_warns(self) -> None:
        config = ProjectControlConfig()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            value = configured_skills_root(config, {
                "PROJECT_CONTROL_SKILLS_ROOT": first,
                "CODING_WORKFLOW_SKILLS_ROOT": second,
            })
            self.assertEqual(value, Path(first).resolve())
            with self.assertWarns(DeprecationWarning):
                value = configured_skills_root(config, {"CODING_WORKFLOW_SKILLS_ROOT": second})
            self.assertEqual(value, Path(second).resolve())


if __name__ == "__main__":
    unittest.main()
