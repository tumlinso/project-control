import unittest
from contextlib import redirect_stderr
from io import StringIO
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

    def test_local_stdio_profiles_reject_network_options(self):
        error = StringIO()
        with redirect_stderr(error):
            self.assertEqual(main(["serve", "codex", "--port", "8768"]), 2)
        self.assertIn("local stdio profiles", error.getvalue())


if __name__ == "__main__":
    unittest.main()
