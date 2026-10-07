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


class ReleaseIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        site_packages = root / "lib" / "python3" / "site-packages"
        self.project_package = site_packages / "project_control"
        self.todo_package = site_packages / "todo_orchestrator"
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
        self.manifest = root / "release-manifest.json"

    def _write_release(self, **updates: object) -> dict[str, str]:
        data: dict[str, object] = {
            "schema_version": 3,
            "project_control_fingerprint": runtime_identity.package_fingerprint(self.project_package),
            "todo_package_root": str(self.todo_package),
            "todo_runtime_fingerprint": runtime_identity.package_fingerprint(self.todo_package),
            "skills_root": None,
        }
        data.update(updates)
        self.manifest.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        return {
            runtime_identity.RELEASE_MANIFEST_VARIABLE: str(self.manifest),
            runtime_identity.RELEASE_DIGEST_VARIABLE: hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
        }

    def test_release_pins_bundled_packages_but_not_optional_content(self) -> None:
        content_root = Path(self.temp.name) / "removed-content"
        environment = self._write_release(
            skills_root=str(content_root),
            tools_fingerprint="0" * 64,
            frozen_skill_resources={"malformed": "content remains provider-owned"},
        )
        identity = runtime_identity.bind_runtime(environment)
        content_root.mkdir()
        (content_root / "changed.md").write_text("new content", encoding="utf-8")
        runtime_identity.validate_runtime(identity)

    def test_installed_todo_tamper_invalidates_release_identity(self) -> None:
        environment = self._write_release()
        identity = runtime_identity.bind_runtime(environment)
        (self.todo_package / "__init__.py").write_text("VALUE = 'tampered'\n", encoding="utf-8")
        with self.assertRaises(runtime_identity.RuntimeIdentityError):
            runtime_identity.validate_runtime(identity)

    def test_release_rejects_symlinked_python_source_even_when_bytes_match(self) -> None:
        environment = self._write_release()
        target = Path(self.temp.name) / "external-init.py"
        target.write_bytes((self.todo_package / "__init__.py").read_bytes())
        (self.todo_package / "__init__.py").unlink()
        (self.todo_package / "__init__.py").symlink_to(target)

        with self.assertRaisesRegex(runtime_identity.RuntimeIdentityError,
                                    "runtime package contains a symlink"):
            runtime_identity.bind_runtime(environment)

    def test_release_manifest_digest_is_required(self) -> None:
        environment = self._write_release()
        environment[runtime_identity.RELEASE_DIGEST_VARIABLE] = "0" * 64
        with self.assertRaises(runtime_identity.RuntimeIdentityError):
            runtime_identity.bind_runtime(environment)

    def test_schema3_requires_project_control_package_fingerprint(self) -> None:
        environment = self._write_release()
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        del data["project_control_fingerprint"]
        self.manifest.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        environment[runtime_identity.RELEASE_DIGEST_VARIABLE] = hashlib.sha256(
            self.manifest.read_bytes()).hexdigest()

        with self.assertRaisesRegex(runtime_identity.RuntimeIdentityError,
                                    "Invalid release manifest"):
            runtime_identity.bind_runtime(environment)


if __name__ == "__main__":
    unittest.main()
