from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from project_control.runtime_identity import bind_runtime, validate_runtime, package_fingerprint, RuntimeIdentityError

class ReleaseIdentityTests(unittest.TestCase):
    def test_development_edits_do_not_rebind_release_but_installed_tamper_does(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'development/todo-orchestrator/todo_orchestrator'
            frozen=root/'release/runtime-skills/todo-orchestrator/todo_orchestrator'
            installed=root/'release/site-packages/todo_orchestrator'
            for path in (source,frozen,installed):
                path.mkdir(parents=True);(path/'__init__.py').write_text('VALUE=1\n')
            manifest=root/'release/release-manifest.json'
            manifest.write_text(json.dumps({'schema_version':2,'skills_root':str(frozen.parents[1]),'todo_runtime_fingerprint':package_fingerprint(frozen)}))
            environment={'PROJECT_CONTROL_SKILLS_ROOT':str(source.parents[1]),'PROJECT_CONTROL_RELEASE_MANIFEST':str(manifest),'PROJECT_CONTROL_RELEASE_DIGEST':hashlib.sha256(manifest.read_bytes()).hexdigest()}
            module=types.ModuleType('todo_orchestrator');module.__file__=str(installed/'__init__.py')
            with patch.dict(sys.modules,{'todo_orchestrator':module}):
                identity=bind_runtime(environment)
                (source/'__init__.py').write_text('VALUE=2\n')
                self.assertEqual(bind_runtime(environment),identity);validate_runtime(identity)
                (installed/'__init__.py').write_text('VALUE=3\n')
                with self.assertRaises(RuntimeIdentityError):validate_runtime(identity)
                with self.assertRaises(RuntimeIdentityError):bind_runtime(environment)
                (installed/'__init__.py').write_text('VALUE=1\n')
                manifest.write_text('{}')
                with self.assertRaises(RuntimeIdentityError):validate_runtime(identity)

    def test_manifest_cannot_be_selected_without_digest(self):
        with self.assertRaises(RuntimeIdentityError):bind_runtime({'PROJECT_CONTROL_RELEASE_MANIFEST':'/missing'})
