from __future__ import annotations

import hashlib
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control.observer_analysis import SkillsObserverAnalysisProvider


class ObserverAnalysisSourceBindingTests(unittest.TestCase):
    def test_bridge_uses_verified_checkout_runtime_and_checks_operator_source_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "local_worker"
            package.mkdir()
            source = package / "supervisor.py"
            source.write_text("source-mode supervisor fixture\n", encoding="utf-8")
            state = root / "observer-state"
            identity = types.SimpleNamespace(package_root=package)

            class Client:
                def __init__(self, state_root, *, root):
                    self.state_root = state_root
                    self.runtime_root = root

            module = types.SimpleNamespace(
                __file__=str(source), SupervisorClient=Client,
            )
            with patch("project_control.observer_analysis.observer_analysis_state_root", return_value=state), \
                 patch("project_control.observer_analysis.bind_local_runtime", return_value=identity) as bind, \
                 patch("project_control.observer_analysis.importlib.import_module", return_value=module), \
                 patch.dict(os.environ, {"PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256": ""}):
                provider = SkillsObserverAnalysisProvider(repo_root=root / "untrusted-repository-override")
                client = provider._get_backend()
                bind.assert_called_once_with()
                self.assertEqual(client.state_root, state)
                self.assertEqual(provider._source_sha256, hashlib.sha256(source.read_bytes()).hexdigest())

            with patch("project_control.observer_analysis.observer_analysis_state_root", return_value=root / "mismatch-state"), \
                 patch("project_control.observer_analysis.bind_local_runtime", return_value=identity), \
                 patch("project_control.observer_analysis.importlib.import_module", return_value=module), \
                 patch.dict(os.environ, {"PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256": "0" * 64}):
                provider = SkillsObserverAnalysisProvider()
                with self.assertRaisesRegex(RuntimeError, "central_supervisor_source_mismatch"):
                    provider._get_backend()
                self.assertFalse((root / "mismatch-state").exists())


if __name__ == "__main__":
    unittest.main()
