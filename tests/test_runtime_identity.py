from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from project_control.runtime_identity import (
    CANONICAL_FINGERPRINT_VARIABLE,
    CANONICAL_ROOT_VARIABLE,
    LEGACY_ROOT_VARIABLE,
    RuntimeIdentityError,
    bind_runtime,
    locate_skills_root,
    package_fingerprint,
    runtime_diagnostics,
    validate_runtime,
)


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "todo-orchestrator" / "todo_orchestrator"
        self.source.mkdir(parents=True)
        (self.source / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.module = types.ModuleType("todo_orchestrator")
        self.module.__file__ = str(self.source / "__init__.py")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def env(self, **extra: str) -> dict[str, str]:
        return {CANONICAL_ROOT_VARIABLE: str(self.root), **extra}

    def test_requires_explicit_root(self) -> None:
        with self.assertRaises(RuntimeIdentityError) as raised:
            locate_skills_root({})
        self.assertEqual(raised.exception.observed, "missing")

    def test_legacy_root_warns_and_resolves(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.assertEqual(locate_skills_root({LEGACY_ROOT_VARIABLE: str(self.root)}), self.root)
        self.assertEqual(len(caught), 1)
        self.assertIn(CANONICAL_ROOT_VARIABLE, str(caught[0].message))

    def test_conflicting_root_variables_fail_closed(self) -> None:
        with self.assertRaises(RuntimeIdentityError):
            locate_skills_root({
                CANONICAL_ROOT_VARIABLE: str(self.root),
                LEGACY_ROOT_VARIABLE: str(self.root / "different"),
            })

    def test_bind_does_not_mutate_sys_path(self) -> None:
        before = list(sys.path)
        with patch.dict(sys.modules, {"todo_orchestrator": self.module}):
            identity = bind_runtime(self.env())
        self.assertEqual(sys.path, before)
        self.assertEqual(identity.package_root, self.source)

    def test_installed_copy_with_equal_sources_is_accepted(self) -> None:
        installed = self.root / "venv" / "todo_orchestrator"
        installed.mkdir(parents=True)
        (installed / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.module.__file__ = str(installed / "__init__.py")
        with patch.dict(sys.modules, {"todo_orchestrator": self.module}):
            identity = bind_runtime(self.env())
        self.assertEqual(identity.package_root, installed)
        self.assertEqual(identity.fingerprint, package_fingerprint(self.source))

    def test_skewed_import_is_rejected(self) -> None:
        installed = self.root / "venv" / "todo_orchestrator"
        installed.mkdir(parents=True)
        (installed / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.module.__file__ = str(installed / "__init__.py")
        with patch.dict(sys.modules, {"todo_orchestrator": self.module}):
            with self.assertRaises(RuntimeIdentityError):
                bind_runtime(self.env())

    def test_pinned_source_change_is_rejected(self) -> None:
        wrong = "0" * 64
        with patch.dict(sys.modules, {"todo_orchestrator": self.module}):
            with self.assertRaises(RuntimeIdentityError):
                bind_runtime(self.env(**{CANONICAL_FINGERPRINT_VARIABLE: wrong}))

    def test_validate_rejects_source_mutation(self) -> None:
        with patch.dict(sys.modules, {"todo_orchestrator": self.module}):
            identity = bind_runtime(self.env())
            (self.source / "changed.py").write_text("CHANGED = True\n", encoding="utf-8")
            with self.assertRaises(RuntimeIdentityError):
                validate_runtime(identity)

    def test_runtime_diagnostics_reports_configuration_without_guessing_a_root(self) -> None:
        result = runtime_diagnostics({})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "runtime_not_configured")
        self.assertEqual(result["supported_action"], "configure_verified_runtime")
        self.assertEqual(result["required_environment"], [CANONICAL_ROOT_VARIABLE])

    def test_runtime_diagnostics_returns_verified_identity_without_environment(self) -> None:
        with patch.dict(sys.modules, {"todo_orchestrator": self.module}):
            result = runtime_diagnostics(self.env(API_SECRET="must-not-appear"))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["identity"]["skills_root"], str(self.root))
        self.assertEqual(result["supported_action"], "use_configured_runtime")
        self.assertNotIn("launch_environment", result)
        self.assertNotIn("must-not-appear", str(result))

    def test_runtime_diagnostics_preserves_a_mismatch_as_attention_required(self) -> None:
        result = runtime_diagnostics(
            self.env(**{CANONICAL_FINGERPRINT_VARIABLE: "0" * 64}),
            binder=lambda environment: bind_runtime(environment),
        )
        self.assertEqual(result["status"], "attention_required")
        self.assertEqual(result["reason"], "runtime_identity_mismatch")
        self.assertEqual(result["supported_action"], "restart_verified_runtime")
        self.assertIn("configured Todo source changed", result["cause"])


if __name__ == "__main__":
    unittest.main()
