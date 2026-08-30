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


if __name__ == "__main__":
    unittest.main()
