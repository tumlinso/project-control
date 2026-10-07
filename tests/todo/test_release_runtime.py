from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from project_control.runtime_identity import package_fingerprint
from todo_orchestrator import runtime_identity


class ReleaseRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_identity._reset_for_testing()

    def tearDown(self) -> None:
        runtime_identity._reset_for_testing()

    def _manifest(self, root: Path, todo_fingerprint: str) -> tuple[Path, str]:
        todo_package = Path(runtime_identity.__file__).resolve().parent
        manifest = root / "release.json"
        manifest.write_text(json.dumps({
            "schema_version": 3,
            "skills_root": str(Path.home() / ".agents" / "skills"),
            "todo_package_root": str(todo_package),
            "todo_runtime_fingerprint": todo_fingerprint,
        }), encoding="utf-8")
        return manifest, hashlib.sha256(manifest.read_bytes()).hexdigest()

    def test_release_binds_the_bundled_package(self) -> None:
        todo_package = Path(runtime_identity.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            manifest, digest = self._manifest(Path(directory), package_fingerprint(todo_package))
            with patch.dict(os.environ, {
                "PROJECT_CONTROL_RELEASE_MANIFEST": str(manifest),
                "PROJECT_CONTROL_RELEASE_DIGEST": digest,
            }, clear=True):
                identity = runtime_identity.bind_canonical_runtime()
            self.assertEqual(identity.package_root, todo_package)
            self.assertEqual(identity.fingerprint, package_fingerprint(todo_package))

    def test_release_rejects_a_todo_fingerprint_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, digest = self._manifest(Path(directory), "0" * 64)
            with patch.dict(os.environ, {
                "PROJECT_CONTROL_RELEASE_MANIFEST": str(manifest),
                "PROJECT_CONTROL_RELEASE_DIGEST": digest,
            }, clear=True), self.assertRaises(runtime_identity.RuntimeIdentityError):
                runtime_identity.bind_canonical_runtime()


if __name__ == "__main__":
    unittest.main()
