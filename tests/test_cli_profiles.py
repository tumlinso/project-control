import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch

from project_control.cli import _parser, main


class ProfileCliTests(unittest.TestCase):
    def test_public_profiles_are_observer_codex_and_mutator(self):
        profile = next(action for action in _parser()._subparsers._group_actions[0].choices["serve"]._actions
                       if action.dest == "profile")
        self.assertEqual(profile.choices, ("observer", "codex", "mutator"))

    def test_observer_is_the_default_serve_profile(self):
        with patch("project_control.app.serve", return_value=0) as serve:
            self.assertEqual(main(["serve"]), 0)
        serve.assert_called_once_with(host=None, port=None)

    def test_codex_and_mutator_are_explicit_stdio_entrypoints(self):
        with patch("project_control.app.serve_codex", return_value=0) as codex:
            self.assertEqual(main(["codex"]), 0)
        codex.assert_called_once_with()
        with patch("project_control.app.serve_mutator", return_value=0) as mutator:
            self.assertEqual(main(["mutator"]), 0)
        mutator.assert_called_once_with()

    def test_workspace_cli_explicitly_selects_and_preserves_gitlink_policy(self):
        from project_control.config import ProjectControlConfig

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = ProjectControlConfig()
            output = StringIO()
            from contextlib import redirect_stdout
            with (patch("project_control.cli.load_config", return_value=config),
                  patch("project_control.cli.save_config") as save,
                  redirect_stdout(output)):
                self.assertEqual(main(["workspace", "add", "demo", "source", str(root),
                                       "--gitlink-policy", "opaque_clean"]), 0)
                self.assertEqual(main(["workspace", "add", "demo", "source", str(root)]), 0)
            self.assertEqual(save.call_count, 2)
            self.assertEqual(config.workspaces["demo"].repositories["source"].gitlink_policy,
                             "opaque_clean")

    def test_local_stdio_profiles_reject_network_options(self):
        error = StringIO()
        with redirect_stderr(error):
            self.assertEqual(main(["serve", "codex", "--port", "8768"]), 2)
        self.assertIn("local stdio profiles", error.getvalue())


if __name__ == "__main__":
    unittest.main()
