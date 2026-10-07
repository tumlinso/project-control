from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control import runtime_identity


PROJECT_CONTROL_SOURCE = Path(runtime_identity.__file__).resolve().parent
TODO_SOURCE = PROJECT_CONTROL_SOURCE.parent / "todo_orchestrator"


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.project_package = root / "src" / "project_control"
        self.todo_package = root / "src" / "todo_orchestrator"
        shutil.copytree(PROJECT_CONTROL_SOURCE, self.project_package,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(TODO_SOURCE, self.todo_package,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.module = types.ModuleType("todo_orchestrator")
        self.module.__file__ = str(self.todo_package / "__init__.py")
        self.file_patch = patch.object(runtime_identity, "__file__",
                                       str(self.project_package / "runtime_identity.py"))
        self.file_patch.start()
        self.addCleanup(self.file_patch.stop)
        self.modules_patch = patch.dict(sys.modules, {"todo_orchestrator": self.module})
        self.modules_patch.start()
        self.addCleanup(self.modules_patch.stop)

    def test_binds_bundled_package_without_any_skills_root(self) -> None:
        identity = runtime_identity.bind_runtime({})

        self.assertEqual(identity.package_root, self.todo_package.resolve())
        self.assertEqual(identity.source_package_root, self.todo_package.resolve())
        self.assertEqual(identity.fingerprint, runtime_identity.package_fingerprint(self.todo_package))

    def test_missing_or_changed_content_does_not_break_core_identity(self) -> None:
        content_root = Path(self.temp.name) / "missing-content"
        identity = runtime_identity.bind_runtime({
            runtime_identity.CANONICAL_ROOT_VARIABLE: str(content_root),
        })
        self.assertEqual(identity.skills_root, content_root)

        content_root.mkdir()
        marker = content_root / "changed.md"
        marker.write_text("changed content", encoding="utf-8")
        runtime_identity.validate_runtime(identity)

    def test_canonical_observer_content_root_precedes_optional_alias(self) -> None:
        canonical = Path(self.temp.name) / "canonical"
        alias = Path(self.temp.name) / "alias"
        self.assertEqual(runtime_identity.locate_skills_root({
            runtime_identity.OBSERVER_ROOT_VARIABLE: str(canonical),
            runtime_identity.OBSERVER_ROOT_ALIAS_VARIABLE: str(alias),
        }), canonical)

    def test_import_from_a_different_package_path_is_rejected_even_if_identical(self) -> None:
        ambient = Path(self.temp.name) / "ambient" / "todo_orchestrator"
        shutil.copytree(self.todo_package, ambient)
        self.module.__file__ = str(ambient / "__init__.py")

        with self.assertRaisesRegex(runtime_identity.RuntimeIdentityError,
                                    "not the bundled Project Control package"):
            runtime_identity.bind_runtime({})

    def test_bundled_package_root_symlink_is_rejected_before_resolution(self) -> None:
        external = Path(self.temp.name) / "external-todo"
        shutil.move(self.todo_package, external)
        self.todo_package.symlink_to(external, target_is_directory=True)

        with self.assertRaisesRegex(runtime_identity.RuntimeIdentityError,
                                    "bundled Todo package root must not be a symlink"):
            runtime_identity.bind_runtime({})

    def test_python_source_symlink_is_rejected(self) -> None:
        target = Path(self.temp.name) / "external-init.py"
        target.write_bytes((self.todo_package / "__init__.py").read_bytes())
        (self.todo_package / "__init__.py").unlink()
        (self.todo_package / "__init__.py").symlink_to(target)

        with self.assertRaisesRegex(runtime_identity.RuntimeIdentityError,
                                    "runtime package contains a symlink"):
            runtime_identity.bind_runtime({})

    def test_canonical_package_fingerprint_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(runtime_identity.RuntimeIdentityError,
                                    "configured Todo source changed"):
            runtime_identity.bind_runtime({
                runtime_identity.CANONICAL_FINGERPRINT_VARIABLE: "0" * 64,
            })

    def test_manifest_digest_and_schema3_package_path_are_strict(self) -> None:
        manifest = Path(self.temp.name) / "release-manifest.json"
        data = {
            "schema_version": 3,
            "project_control_fingerprint": runtime_identity.package_fingerprint(self.project_package),
            "todo_package_root": str(self.todo_package),
            "todo_runtime_fingerprint": runtime_identity.package_fingerprint(self.todo_package),
            "skills_root": None,
        }
        manifest.write_text(json.dumps(data), encoding="utf-8")
        environment = {
            runtime_identity.RELEASE_MANIFEST_VARIABLE: str(manifest),
            runtime_identity.RELEASE_DIGEST_VARIABLE: hashlib.sha256(manifest.read_bytes()).hexdigest(),
        }
        self.assertEqual(runtime_identity.bind_runtime(environment).package_root, self.todo_package)

        data["todo_package_root"] = str(Path(self.temp.name) / "elsewhere")
        manifest.write_text(json.dumps(data), encoding="utf-8")
        environment[runtime_identity.RELEASE_DIGEST_VARIABLE] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        with self.assertRaisesRegex(runtime_identity.RuntimeIdentityError,
                                    "package path differs from release manifest"):
            runtime_identity.bind_runtime(environment)


if __name__ == "__main__":
    unittest.main()
