from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest import mock

from todo_orchestrator import runtime_identity


SKILLS_ROOT = Path.home() / ".agents" / "skills"
PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "todo_orchestrator"


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_identity._reset_for_testing()

    def tearDown(self) -> None:
        runtime_identity._reset_for_testing()

    def test_content_root_does_not_select_bundled_todo_identity(self) -> None:
        with mock.patch.dict(os.environ, {
            "PROJECT_CONTROL_SKILLS_ROOT": str(SKILLS_ROOT),
        }, clear=True):
            identity = runtime_identity.bind_canonical_runtime()
            environment = runtime_identity.controlled_subprocess_env(identity)
        self.assertEqual(identity.contract, "PCU-RUNTIME-IDENTITY/1")
        self.assertEqual(identity.skills_root, SKILLS_ROOT)
        self.assertEqual(identity.package_root, PACKAGE_ROOT.resolve())
        self.assertEqual(environment["PROJECT_CONTROL_SKILLS_ROOT"], str(SKILLS_ROOT))
        self.assertNotIn("CODING_WORKFLOW_SKILLS_ROOT", environment)
        self.assertEqual(environment["PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT"], identity.fingerprint)

    def test_legacy_skills_alias_does_not_change_package_identity(self) -> None:
        with mock.patch.dict(os.environ, {
            "CODING_WORKFLOW_SKILLS_ROOT": str(SKILLS_ROOT),
        }, clear=True):
            identity = runtime_identity.bind_canonical_runtime()
        self.assertEqual(identity.package_root, PACKAGE_ROOT.resolve())

    def test_content_root_is_not_confused_with_package_root(self) -> None:
        with mock.patch.dict(os.environ, {
            "PROJECT_CONTROL_SKILLS_ROOT": str(SKILLS_ROOT),
            "CODING_WORKFLOW_SKILLS_ROOT": str(SKILLS_ROOT.parent),
        }, clear=True):
            identity = runtime_identity.bind_canonical_runtime()
        self.assertEqual(identity.package_root, PACKAGE_ROOT.resolve())

    def test_launch_fingerprint_skew_fails_closed(self) -> None:
        with mock.patch.dict(os.environ, {
            "PROJECT_CONTROL_SKILLS_ROOT": str(SKILLS_ROOT),
            "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT": "0" * 64,
        }, clear=True), self.assertRaisesRegex(
            runtime_identity.RuntimeIdentityError, "source changed"
        ):
            runtime_identity.bind_canonical_runtime()

    def test_rebinding_is_rejected(self) -> None:
        with mock.patch.dict(os.environ, {
            "PROJECT_CONTROL_SKILLS_ROOT": str(SKILLS_ROOT),
        }, clear=True):
            identity = runtime_identity.bind_canonical_runtime()
        changed = runtime_identity.RuntimeIdentity(
            **{**identity.__dict__, "fingerprint": "f" * 64}
        )
        with self.assertRaisesRegex(runtime_identity.RuntimeIdentityError, "changed"):
            runtime_identity.validate_runtime(changed)


if __name__ == "__main__":
    unittest.main()
