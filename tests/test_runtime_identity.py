from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control import runtime_identity as ri


class _Distribution:
    version = "0.2.1"
    files = (Path("todo_orchestrator/__init__.py"),)

    def __init__(self, root: Path):
        self.root = root

    def locate_file(self, _entry):
        return self.root


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "site-packages" / "todo_orchestrator"
        self.root.mkdir(parents=True)
        (self.root / "__init__.py").write_text("API = True\n", encoding="utf-8")
        (self.root / "api.py").write_text("", encoding="utf-8")
        self.module = types.ModuleType("todo_orchestrator")
        self.module.__file__ = str(self.root / "__init__.py")
        self.metadata_patch = patch.object(ri.importlib.metadata, "distribution", return_value=_Distribution(self.root))
        self.metadata_patch.start()
        self.addCleanup(self.metadata_patch.stop)
        self.module_patch = patch.dict(sys.modules, {"todo_orchestrator": self.module})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)

    def test_binds_only_the_installed_standalone_distribution(self):
        with patch.object(ri, "_source_todo_lock", return_value=None):
            identity = ri.bind_runtime({})
        self.assertEqual(identity.package_root, self.root)
        self.assertEqual(identity.distribution_version, "0.2.1")
        self.assertEqual(identity.fingerprint, ri.package_fingerprint(self.root))

    def test_rejects_an_import_from_a_different_package_root(self):
        ambient = Path(self.temp.name) / "other" / "todo_orchestrator"
        ambient.mkdir(parents=True)
        (ambient / "__init__.py").write_text("API = True\n", encoding="utf-8")
        self.module.__file__ = str(ambient / "__init__.py")
        with patch.object(ri, "_source_todo_lock", return_value=None):
            with self.assertRaisesRegex(ri.RuntimeIdentityError, "not the installed standalone"):
                ri.bind_runtime({})

    def test_rejects_distribution_version_mismatch(self):
        with patch.object(ri.importlib.metadata, "distribution", return_value=types.SimpleNamespace(
            version="0.1.0", files=(Path("todo_orchestrator/__init__.py"),),
            locate_file=lambda _entry: self.root,
        )):
            with self.assertRaisesRegex(ri.RuntimeIdentityError, "Unexpected standalone"):
                ri.bind_runtime({})

    def test_release_pins_external_todo_package_and_project_control(self):
        pc_root = Path(ri.__file__).parent
        document = {
            "schema_version": 4,
            "project_control_fingerprint": ri.package_fingerprint(pc_root),
            "todo_distribution": {"name": "todo-orchestrator", "version": "0.2.1",
                                  "package_path": str(self.root),
                                  "fingerprint": ri.package_fingerprint(self.root)},
            "skills_root": str(Path(self.temp.name) / "optional-skills"),
        }
        manifest = Path(self.temp.name) / "release-manifest.json"
        manifest.write_text(json.dumps(document), encoding="utf-8")
        env = {ri.RELEASE_MANIFEST_VARIABLE: str(manifest),
               ri.RELEASE_DIGEST_VARIABLE: hashlib.sha256(manifest.read_bytes()).hexdigest()}
        identity = ri.bind_runtime(env)
        (self.root / "api.py").write_text("CHANGED = True\n", encoding="utf-8")
        with self.assertRaisesRegex(ri.RuntimeIdentityError, "identity changed"):
            ri.validate_runtime(identity)

    def test_package_symlinks_are_rejected(self):
        target = Path(self.temp.name) / "external.py"
        target.write_text("", encoding="utf-8")
        (self.root / "api.py").unlink()
        (self.root / "api.py").symlink_to(target)
        with self.assertRaisesRegex(ri.RuntimeIdentityError, "contains a symlink"):
            ri.bind_runtime({})


if __name__ == "__main__":
    unittest.main()
