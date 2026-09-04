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


def _preconditions_fixture():
    from project_control.models import ObservationPreconditions

    return ObservationPreconditions(workspace_id="demo", observed_at="2026-08-31T00:00:00Z")


class ProfileCliTests(unittest.TestCase):
    def test_plan_compile_writes_native_plan_and_bounded_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package"
            package.mkdir()
            (package / "proposed_todos.json").write_text(json.dumps([
                {"id": "CLI-1", "title": "CLI task", "repository": "Demo", "purpose": "Exercise compile"},
            ]), encoding="utf-8")
            output_path = root / "native.json"
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(main([
                    "plan", "compile", "--package", str(package),
                    "--repository-label", "Demo", "--output", str(output_path),
                ]), 0)
            native = json.loads(output_path.read_text(encoding="utf-8"))
            summary = json.loads(output.getvalue())
            self.assertEqual(2, native["schema_version"])
            self.assertEqual(["CLI-1"], [task["id"] for task in native["tasks"]])
            self.assertEqual(1, summary["selected_task_count"])
            self.assertNotIn("native_todo_plan", summary)
            from project_control.mutation import plan_digest
            self.assertEqual(plan_digest(native), summary["plan_digest"])

    def test_plan_validate_and_apply_share_mutation_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan_file = Path(directory) / "plan.json"
            plan = {"schema_version": 2, "tasks": []}
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            with patch("project_control.cli.load_config", return_value="config"), \
                 patch("project_control.mutation.validate_native_plan", return_value={"status": "validated"}) as validate, \
                 patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(main(["plan", "validate", "--project", "demo", "--file", str(plan_file)]), 0)
            validate.assert_called_once_with("config", "demo", plan)

            snapshot = unittest.mock.Mock(observed_at="2026-08-31T00:00:00Z")
            snapshot.observation_preconditions.return_value = _preconditions_fixture()
            with patch("project_control.cli.load_config", return_value="config"), \
                 patch("project_control.mutation.build_mutation_snapshot", return_value=snapshot) as observe, \
                 patch("project_control.proposals.observation_preconditions", return_value=_preconditions_fixture()), \
                 patch("project_control.mutation.apply_proposal", return_value={"status": "applied"}) as apply, \
                 patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(main(["plan", "apply", "--project", "demo", "--file", str(plan_file)]), 0)
            observe.assert_called_once_with("config", "demo")
            self.assertEqual("config", apply.call_args.args[0])
            self.assertEqual("demo", apply.call_args.args[1])
            self.assertFalse(apply.call_args.args[2].authority_to_apply)

    def test_legacy_serve_is_observer_and_explicit_codex_is_stdio(self) -> None:
        with patch("project_control.cli._serve_profile", return_value=0) as serve:
            self.assertEqual(main(["serve"]), 0)
            serve.assert_called_once_with("observer", host=None, port=None)
        with patch("project_control.cli._serve_profile", return_value=0) as serve:
            self.assertEqual(main(["serve", "codex"]), 0)
            serve.assert_called_once_with("codex", host=None, port=None)

    def test_top_level_codex_entry_point_uses_stdio_profile(self) -> None:
        with patch("project_control.cli._serve_profile", return_value=0) as serve:
            self.assertEqual(main(["codex"]), 0)
            serve.assert_called_once_with("codex", host=None, port=None)

    def test_explicit_mutator_entry_points_use_stdio_profile(self) -> None:
        with patch("project_control.cli._serve_profile", return_value=0) as serve:
            self.assertEqual(main(["serve", "mutator"]), 0)
            serve.assert_called_once_with("mutator", host=None, port=None)
        with patch("project_control.cli._serve_profile", return_value=0) as serve:
            self.assertEqual(main(["mutator"]), 0)
            serve.assert_called_once_with("mutator", host=None, port=None)

    def test_codex_rejects_network_options_before_importing_server(self) -> None:
        with self.assertRaisesRegex(ValueError, "stdio"):
            _serve_profile("codex", host="127.0.0.1", port=None)
        with self.assertRaisesRegex(ValueError, "stdio"):
            _serve_profile("mutator", host="127.0.0.1", port=None)

    def test_mutator_dispatches_only_to_explicit_server(self) -> None:
        with patch("project_control.app.serve_mutator", return_value=0) as serve:
            self.assertEqual(_serve_profile("mutator", host=None, port=None), 0)
        serve.assert_called_once_with()

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

    def test_admin_workspace_preparation_is_exposed_by_main_cli(self) -> None:
        prepared = {"status": "prepared", "prepared": [{"lane_id": "L-A"}]}
        with patch("project_control.admin.prepare_run_workspaces", return_value=prepared) as prepare, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main([
                "admin", "prepare-run-workspaces", "--repo", "/repo", "--plan", "/plan.json",
                "--run", "RUN", "--apply", "--confirm", "PREPARE-RUN-WORKSPACES",
            ]), 0)
        prepare.assert_called_once_with(
            "/repo", "/plan.json", "RUN", apply=True, confirmation="PREPARE-RUN-WORKSPACES",
        )
        self.assertEqual(json.loads(output.getvalue()), prepared)

    def test_admin_workspace_base_reconciliation_is_exposed_by_main_cli(self) -> None:
        reconciled = {"status": "reconciled", "lane_id": "L-A"}
        with patch("project_control.admin.reconcile_workspace_base", return_value=reconciled) as reconcile, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main([
                "admin", "reconcile-workspace-base", "--repo", "/repo", "--run", "RUN",
                "--lane", "L-A", "--base", "abc", "--reason", "prior wave",
                "--apply", "--confirm", "RECONCILE-WORKSPACE-BASE",
            ]), 0)
        reconcile.assert_called_once_with(
            "/repo", "RUN", "L-A", "abc", reason="prior wave",
            apply=True, confirmation="RECONCILE-WORKSPACE-BASE",
        )
        self.assertEqual(json.loads(output.getvalue()), reconciled)

    def test_admin_workspace_cleanup_eligibility_is_exposed_by_main_cli(self) -> None:
        marked = {"status": "marked", "marked": [{"workspace_id": "W-A"}]}
        with patch(
            "project_control.admin.mark_run_workspaces_cleanup_eligible", return_value=marked
        ) as cleanup, patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main([
                "admin", "mark-run-workspaces-cleanup-eligible", "--repo", "/repo",
                "--run", "RUN", "--apply", "--confirm",
                "MARK-RUN-WORKSPACES-CLEANUP-ELIGIBLE",
            ]), 0)
        cleanup.assert_called_once_with(
            "/repo", "RUN", apply=True,
            confirmation="MARK-RUN-WORKSPACES-CLEANUP-ELIGIBLE",
        )
        self.assertEqual(json.loads(output.getvalue()), marked)

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
