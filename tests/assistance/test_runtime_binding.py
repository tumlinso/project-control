"""Source-mode tests for the trusted, single-root local runtime binding."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
import sys
import struct
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from project_control import runtime_binding


REPOSITORY = Path(__file__).resolve().parents[2]
SUPPLIER = Path("/home/tumlinson/.agents/skills")
RECEIVER = REPOSITORY / "src/project_control/local_runtime"


def _write_manifest(root: Path, *, contents: bytes = b"VERSION = 'old'\n") -> None:
    package = root / "local_worker"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("\"\"\"Fixture runtime.\"\"\"\n", encoding="utf-8")
    (package / "supervisor.py").write_bytes(contents)
    files = {
        "local_worker/__init__.py": hashlib.sha256((package / "__init__.py").read_bytes()).hexdigest(),
        "local_worker/supervisor.py": hashlib.sha256(contents).hexdigest(),
    }
    data = {
        "schema_version": 1,
        "source_root": "/fixture/skills/local-coding-worker",
        "source_commit": "a" * 40,
        "files": files,
    }
    (root / runtime_binding.RECEIVER_MANIFEST).write_text(
        json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )


class RuntimeBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        # Collection of neighboring PA1 tests imports the receiver runtime in
        # this interpreter. Model the fresh-process precondition for each
        # source binding case without changing the receiver's strict policy.
        original_runtime_modules = {
            name: module for name, module in tuple(sys.modules.items())
            if name == "local_worker" or name.startswith("local_worker.")
        }
        for name in original_runtime_modules:
            sys.modules.pop(name, None)
        original_meta_path = list(sys.meta_path)
        sys.meta_path[:] = [finder for finder in sys.meta_path
                            if not isinstance(finder, runtime_binding._ManifestFinder)]
        self.addCleanup(lambda path=original_meta_path: sys.meta_path.__setitem__(slice(None), path))

        def restore_runtime_modules() -> None:
            for name in tuple(sys.modules):
                if name == "local_worker" or name.startswith("local_worker."):
                    sys.modules.pop(name, None)
            sys.modules.update(original_runtime_modules)

        self.addCleanup(restore_runtime_modules)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Native task runners may inject release pins for the installed
        # application. These source-checkout fixtures must be independent of
        # that process environment; installed-mode cases set their pins below.
        release_variables = (
            runtime_binding.RELEASE_MANIFEST_VARIABLE,
            runtime_binding.RELEASE_DIGEST_VARIABLE,
        )
        saved_release_environment = {key: os.environ.get(key) for key in release_variables}
        for key in release_variables:
            os.environ.pop(key, None)

        def restore_release_environment() -> None:
            for key, value in saved_release_environment.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore_release_environment)
        original_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        original_meta_path = list(sys.meta_path)
        self.addCleanup(lambda: sys.meta_path.__setitem__(slice(None), original_meta_path))
        self.repo_root = Path(self.temp.name)
        (self.repo_root / "pyproject.toml").write_text("[project]\nname='binding-fixture'\n", encoding="utf-8")
        project_package = self.repo_root / "src/project_control"
        project_package.mkdir(parents=True)
        self.fixture_root = project_package / "local_runtime"
        _write_manifest(self.fixture_root)
        self.original_file = runtime_binding.__file__
        runtime_binding.__file__ = str(project_package / "runtime_binding.py")
        self.addCleanup(setattr, runtime_binding, "__file__", self.original_file)
        original_fingerprint = runtime_binding._BOUND_FINGERPRINT
        runtime_binding._BOUND_FINGERPRINT = None
        self.addCleanup(setattr, runtime_binding, "_BOUND_FINGERPRINT", original_fingerprint)
        original_finder = runtime_binding._BOUND_FINDER
        runtime_binding._BOUND_FINDER = None
        self.addCleanup(setattr, runtime_binding, "_BOUND_FINDER", original_finder)
        original_manifest_sha = runtime_binding._BOUND_MANIFEST_SHA256
        runtime_binding._BOUND_MANIFEST_SHA256 = None
        self.addCleanup(setattr, runtime_binding, "_BOUND_MANIFEST_SHA256", original_manifest_sha)

    def _install_source_shaped_binding_package(self) -> Path:
        project_package = self.repo_root / "src/project_control"
        project_package.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY / "src/project_control/__init__.py", project_package / "__init__.py")
        shutil.copy2(REPOSITORY / "src/project_control/runtime_binding.py", project_package / "runtime_binding.py")
        return project_package

    def test_source_identity_validates_receiver_manifest_without_importing_runtime(self) -> None:
        before = {name for name in sys.modules if name == "local_worker" or name.startswith("local_worker.")}
        identity = runtime_binding.local_runtime_identity()
        after = {name for name in sys.modules if name == "local_worker" or name.startswith("local_worker.")}

        self.assertEqual(identity.root, self.fixture_root.resolve())
        self.assertEqual(identity.source_root, "/fixture/skills/local-coding-worker")
        self.assertEqual(identity.source_commit, "a" * 40)
        self.assertEqual(identity.file_count, 2)
        self.assertEqual(before, after)

    def test_binding_exposes_only_the_checked_root_without_importing_runtime(self) -> None:
        foreign = str(Path(self.temp.name) / "foreign")
        Path(foreign).mkdir()
        sys.path.insert(0, foreign)
        self.addCleanup(lambda: sys.path.remove(foreign) if foreign in sys.path else None)

        identity = runtime_binding.bind_local_runtime()

        self.assertEqual(identity.root, self.fixture_root.resolve())
        self.assertEqual(Path(sys.path[0]).resolve(), self.fixture_root.resolve())
        self.assertFalse(any(name == "local_worker" or name.startswith("local_worker.") for name in sys.modules))

    def test_root_override_is_rejected_before_module_import(self) -> None:
        other = Path(self.temp.name) / "other"
        other.mkdir()
        with self.assertRaisesRegex(runtime_binding.RuntimeBindingError, "runtime_root_override_rejected"):
            runtime_binding.bind_local_runtime(root=other)
        self.assertFalse(any(name == "local_worker" or name.startswith("local_worker.") for name in sys.modules))

    def test_modified_receiver_file_is_rejected_before_binding(self) -> None:
        with (self.fixture_root / "local_worker" / "supervisor.py").open("ab") as stream:
            stream.write(b"# changed after manifest\n")

        with self.assertRaisesRegex(runtime_binding.RuntimeBindingError, "receiver_file_hash_mismatch"):
            runtime_binding.bind_local_runtime()
        self.assertFalse(any(name == "local_worker" or name.startswith("local_worker.") for name in sys.modules))

    def test_stale_or_missing_installed_release_binding_fails_closed(self) -> None:
        release = Path(self.temp.name) / "release.json"
        # This models an inherited release pin from an older installed
        # package, which lacks the receiver binding introduced by this bundle.
        release.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
        raw = release.read_bytes()
        with mock.patch.dict(os.environ, {
            runtime_binding.RELEASE_MANIFEST_VARIABLE: str(release),
            runtime_binding.RELEASE_DIGEST_VARIABLE: hashlib.sha256(raw).hexdigest(),
        }, clear=False):
            with self.assertRaisesRegex(runtime_binding.RuntimeBindingError, "runtime_release_binding_missing"):
                runtime_binding.local_runtime_identity()

        # The same source-shaped fixture is usable once the release pins are
        # isolated, as they are by setUp for every source-mode test.
        with mock.patch.dict(os.environ, {}, clear=True):
            identity = runtime_binding.local_runtime_identity()
        self.assertEqual(identity.root, self.fixture_root.resolve())
        self.assertFalse(any(name == "local_worker" or name.startswith("local_worker.") for name in sys.modules))

    def test_non_checkout_install_requires_a_digest_pinned_release_manifest(self) -> None:
        site_package = Path(self.temp.name) / "site-packages/project_control"
        runtime = site_package / "local_runtime"
        shutil.copytree(self.fixture_root, runtime)
        runtime_binding.__file__ = str(site_package / "runtime_binding.py")

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                runtime_binding.RuntimeBindingError, "installed_runtime_release_binding_required"
            ):
                runtime_binding.local_runtime_identity()

    def test_installed_release_manifest_pins_the_exact_receiver_bundle(self) -> None:
        site_package = Path(self.temp.name) / "site-packages/project_control"
        runtime = site_package / "local_runtime"
        shutil.copytree(self.fixture_root, runtime)
        manifest_raw = (runtime / runtime_binding.RECEIVER_MANIFEST).read_bytes()
        receiver_manifest = json.loads(manifest_raw)
        fingerprint = runtime_binding._files_fingerprint(receiver_manifest["files"])
        release = Path(self.temp.name) / "release.json"
        release.write_text(json.dumps({
            "schema_version": 2,
            "local_runtime_binding": {
                "path": "project_control/local_runtime",
                "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "fingerprint": fingerprint,
            },
        }), encoding="utf-8")
        release_digest = hashlib.sha256(release.read_bytes()).hexdigest()
        runtime_binding.__file__ = str(site_package / "runtime_binding.py")

        with mock.patch.dict(os.environ, {
            runtime_binding.RELEASE_MANIFEST_VARIABLE: str(release),
            runtime_binding.RELEASE_DIGEST_VARIABLE: release_digest,
        }, clear=True):
            identity = runtime_binding.local_runtime_identity()

        self.assertEqual(identity.root, runtime.resolve())
        self.assertEqual(identity.manifest_sha256, hashlib.sha256(manifest_raw).hexdigest())
        self.assertEqual(identity.fingerprint, fingerprint)

    def test_ambient_supplier_module_is_rejected_without_mixing_roots(self) -> None:
        runtime_binding.bind_local_runtime()
        name = "local_worker"
        poison = types.ModuleType(name)
        poison.__file__ = str(Path(self.temp.name) / "untrusted" / "local_worker" / "__init__.py")
        sys.modules[name] = poison
        self.addCleanup(lambda: sys.modules.pop(name, None))

        with self.assertRaisesRegex(runtime_binding.RuntimeBindingError, "ambient_local_worker_module_rejected"):
            runtime_binding.bind_local_runtime()
        self.assertEqual(Path(sys.path[0]).resolve(), self.fixture_root.resolve())

    def test_preloaded_same_root_module_without_prior_binding_fails_closed(self) -> None:
        module = types.ModuleType("local_worker")
        module.__file__ = str(self.fixture_root / "local_worker/__init__.py")
        sys.modules["local_worker"] = module
        self.addCleanup(lambda: sys.modules.pop("local_worker", None))

        with self.assertRaisesRegex(
            runtime_binding.RuntimeBindingError, "local_worker_modules_precede_verified_binding"
        ):
            runtime_binding.bind_local_runtime()

    def test_cached_modules_are_rejected_after_same_root_manifest_refresh(self) -> None:
        runtime_binding.bind_local_runtime()
        loaded = importlib.import_module("local_worker.supervisor")
        self.assertEqual(Path(loaded.__file__).resolve(), self.fixture_root / "local_worker/supervisor.py")
        self.assertEqual(loaded.VERSION, "old")
        self.addCleanup(lambda: [sys.modules.pop(name, None) for name in tuple(sys.modules)
                                 if name == "local_worker" or name.startswith("local_worker.")])

        _write_manifest(self.fixture_root, contents=b"VERSION = 'new'\n")
        self.assertEqual(loaded.VERSION, "old")
        with self.assertRaisesRegex(
            runtime_binding.RuntimeBindingError, "runtime_fingerprint_changed_restart_required"
        ):
            runtime_binding.bind_local_runtime()
        self.assertEqual(importlib.import_module("local_worker.supervisor").VERSION, "old")
        for name in tuple(sys.modules):
            if name == "local_worker" or name.startswith("local_worker."):
                sys.modules.pop(name, None)
        self.assertFalse(any(name == "local_worker" or name.startswith("local_worker.") for name in sys.modules))

        with self.assertRaisesRegex(
            runtime_binding.RuntimeBindingError, "runtime_fingerprint_changed_restart_required"
        ):
            runtime_binding.bind_local_runtime()

    def test_importing_real_receiver_supervisor_does_not_start_a_process(self) -> None:
        # Use a clean interpreter so the single local_worker namespace cannot
        # have been imported by another test first. Popen is patched only
        # inside that interpreter, after it has started.
        code = """
import subprocess
from unittest.mock import patch
from project_control.runtime_binding import import_local_worker_supervisor
with patch('subprocess.Popen') as process_start:
    module = import_local_worker_supervisor()
    assert module.__name__ == 'local_worker.supervisor'
    assert not process_start.called
"""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPOSITORY / "src")
        completed = subprocess.run(
            [sys.executable, "-c", code], cwd=REPOSITORY, env=env,
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_runtime_domain_tool_paths_are_explicit_and_do_not_depend_on_layout(self) -> None:
        code = """
import os
from unittest.mock import patch
from project_control.runtime_binding import import_local_worker_supervisor
import_local_worker_supervisor()
from local_worker.controller import IntegrationController
from local_worker.controller import IntegrationError
with patch.dict(os.environ, {'PROJECT_CONTROL_SKILLS_ROOT': '/configured/skills'}, clear=True):
    defaulted = IntegrationController()
    assert str(defaulted.todo_cli) == '/configured/skills/todo-orchestrator/scripts/todo.py'
    assert str(defaulted.ctxpp_cli) == '/configured/skills/cpp-context-compiler/scripts/ctxpp'
    assert str(defaulted.cuda_cli) == '/configured/skills/cuda/scripts/cuda_controller.py'
with patch.dict(os.environ, {
    'LCW_TODO_CLI': '/tools/todo', 'LCW_CTXPP_CLI': '/tools/ctxpp',
    'LCW_WORKER_CLI': '/tools/worker', 'LCW_CUDA_CLI': '/tools/cuda',
}, clear=True):
    explicit = IntegrationController()
    assert str(explicit.todo_cli) == '/tools/todo'
    assert str(explicit.ctxpp_cli) == '/tools/ctxpp'
    assert str(explicit.worker_cli) == '/tools/worker'
    assert str(explicit.cuda_cli) == '/tools/cuda'
with patch.dict(os.environ, {}, clear=True):
    try:
        IntegrationController()
    except IntegrationError as error:
        assert 'PROJECT_CONTROL_SKILLS_ROOT' in str(error)
    else:
        raise AssertionError('unconfigured domain roots must fail closed')
"""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPOSITORY / "src")
        completed = subprocess.run(
            [sys.executable, "-c", code], cwd=REPOSITORY, env=env,
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_supplier_and_receiver_policy_imports_match_on_the_same_fixture(self) -> None:
        code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from local_worker.policy import DelegationPolicy
evidence = {
    'format': 'CORE4-HOST-BAKEOFF/1', 'status': 'completed',
    'selection': {'configuration_id': 'fixture-profile'},
    'summary': {'phase_b_survivors': 1},
}
policy = DelegationPolicy.from_bakeoff(evidence)
print(json.dumps({
    'record': policy.as_record(),
    'explain': policy.decision('explain', backend='real'),
    'fake_review': policy.decision('review', backend='fake'),
}, sort_keys=True, separators=(',', ':')))
"""

        def run(import_root: Path) -> str:
            completed = subprocess.run(
                [sys.executable, "-c", code, str(import_root)], cwd=REPOSITORY,
                capture_output=True, text=True, timeout=15, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return completed.stdout.strip()

        supplier_runtime = SUPPLIER / "local-coding-worker"
        receiver_runtime = RECEIVER
        supplier_output = run(supplier_runtime)
        receiver_output = run(receiver_runtime)
        self.assertEqual(receiver_output, supplier_output)
        self.assertEqual(json.loads(receiver_output)["explain"]["max_workers"], 1)

    def test_verified_loader_ignores_stale_same_size_timestamp_pyc(self) -> None:
        source = self.fixture_root / "local_worker/supervisor.py"
        old_source = b"VERSION = 'A'\n"
        new_source = b"VERSION = 'B'\n"
        self.assertEqual(len(old_source), len(new_source))
        source.write_bytes(old_source)
        fixed_mtime = 1_700_000_000
        os.utime(source, (fixed_mtime, fixed_mtime))
        _write_manifest(self.fixture_root, contents=old_source)
        os.utime(source, (fixed_mtime, fixed_mtime))
        py_compile.compile(str(source), doraise=True)
        pyc_path = Path(importlib.util.cache_from_source(str(source)))
        pyc_before = hashlib.sha256(pyc_path.read_bytes()).hexdigest()
        pyc_mtime, pyc_size = struct.unpack("<II", pyc_path.read_bytes()[8:16])
        old_stat = source.stat()
        self.assertEqual(pyc_mtime, int(old_stat.st_mtime))
        self.assertEqual(pyc_size, old_stat.st_size)

        source.write_bytes(new_source)
        os.utime(source, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        new_stat = source.stat()
        self.assertEqual(new_stat.st_size, old_stat.st_size)
        self.assertEqual(new_stat.st_mtime_ns, old_stat.st_mtime_ns)
        _write_manifest(self.fixture_root, contents=new_source)
        os.utime(source, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        self.assertEqual(source.stat().st_mtime_ns, old_stat.st_mtime_ns)
        expected_source_sha = hashlib.sha256(new_source).hexdigest()

        # Prove the fixture would ordinarily load A from its valid timestamp
        # cache. This is isolated from the verified import in the next process.
        default_import = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import local_worker.supervisor as m; print(m.VERSION)",
             str(self.fixture_root)], cwd=self.repo_root, capture_output=True, text=True,
            timeout=15, check=False,
        )
        self.assertEqual(default_import.returncode, 0, default_import.stderr)
        self.assertEqual(default_import.stdout.strip(), "A")

        self._install_source_shaped_binding_package()
        bound_code = """
import hashlib
from pathlib import Path
from project_control.runtime_binding import bind_local_runtime, import_local_worker_supervisor
identity = bind_local_runtime()
module = import_local_worker_supervisor()
source = identity.package_root / 'supervisor.py'
assert module.__name__ == 'local_worker.supervisor'
assert Path(module.__file__).resolve() == source.resolve()
assert module.VERSION == 'B'
assert module.__loader__.__class__.__name__ == '_ManifestSourceLoader'
assert hashlib.sha256(source.read_bytes()).hexdigest() == identity_manifest_sha
print(module.VERSION)
""".replace("identity_manifest_sha", repr(expected_source_sha))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.repo_root / "src")
        env.pop(runtime_binding.RELEASE_MANIFEST_VARIABLE, None)
        env.pop(runtime_binding.RELEASE_DIGEST_VARIABLE, None)
        verified_import = subprocess.run(
            [sys.executable, "-c", bound_code], cwd=self.repo_root, env=env,
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(verified_import.returncode, 0, verified_import.stderr)
        self.assertEqual(verified_import.stdout.strip(), "B")
        self.assertEqual(hashlib.sha256(pyc_path.read_bytes()).hexdigest(), pyc_before)

    def test_supervisor_child_bootstrap_forwards_args_without_starting_service(self) -> None:
        spy_dir = Path(self.temp.name) / "spy"
        spy_dir.mkdir()
        marker = Path(self.temp.name) / "unexpected-process-start"
        workspace = Path(self.temp.name) / "workspace"
        shadow_package = workspace / "project_control"
        shadow_package.mkdir(parents=True)
        shadow_marker = Path(self.temp.name) / "untrusted-bootstrap-executed"
        (shadow_package / "__init__.py").write_text("\"\"\"Untrusted CWD shadow fixture.\"\"\"\n", encoding="utf-8")
        (shadow_package / "runtime_binding.py").write_text(
            "import os\nfrom pathlib import Path\n"
            "Path(os.environ['PA1_SHADOW_MARKER']).write_text('executed')\n"
            "raise SystemExit(85)\n",
            encoding="utf-8",
        )
        (spy_dir / "sitecustomize.py").write_text(
            "import os, subprocess\n"
            "def blocked_popen(*args, **kwargs):\n"
            "    open(os.environ['PA1_POPEN_MARKER'], 'w').write('called')\n"
            "    raise RuntimeError('unexpected child process start')\n"
            "subprocess.Popen = blocked_popen\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join((str(spy_dir), str(REPOSITORY / "src")))
        env["PA1_POPEN_MARKER"] = str(marker)
        env["PA1_SHADOW_MARKER"] = str(shadow_marker)
        env.pop(runtime_binding.RELEASE_MANIFEST_VARIABLE, None)
        env.pop(runtime_binding.RELEASE_DIGEST_VARIABLE, None)

        result = subprocess.run(
            [sys.executable, "-m", "project_control.runtime_binding", "local_worker.supervisor",
             "--repo-root", str(workspace), "--unexpected-bootstrap-arg"],
            # The executable package parent is the cwd, so an untrusted
            # workspace package cannot shadow `project_control` before binding.
            cwd=REPOSITORY / "src", env=env, capture_output=True, text=True, timeout=30, check=False,
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(marker.exists(), result.stderr)
        self.assertFalse(shadow_marker.exists(), result.stderr)
        self.assertIn("--unexpected-bootstrap-arg", result.stderr)

        # Exercise the production launch path itself. Keep this in a clean
        # interpreter so the manifest-backed finder owns local_worker.*, and
        # replace Popen before ensure_running can start a child process.
        launch_code = """
import json, os, sys
from pathlib import Path
from unittest.mock import patch
from project_control.runtime_binding import import_local_worker_supervisor
supervisor = import_local_worker_supervisor()
client = object.__new__(supervisor.SupervisorClient)
client.repo_root = Path(sys.argv[1])
client.root = Path(sys.argv[2])
client.socket_path = client.root / 'supervisor.sock'
client.runtime_identity = object()
def unavailable(*args, **kwargs):
    raise supervisor.SupervisorError('fixture supervisor unavailable')
client._request = unavailable
client._recover_stale = lambda: None
captured = {}
def fake_popen(*args, **kwargs):
    captured['args'] = args
    captured['kwargs'] = kwargs
    return object()
environment = {'PYTHONPATH': os.pathsep.join(('/ambient/supplier', '/other/path')),
               'LCW_FIXTURE_FLAG': 'preserved'}
expected_cwd = Path(supervisor.__file__).resolve().parents[3]
with patch.object(supervisor, 'subprocess_environment', return_value=environment.copy()), \\
     patch.object(supervisor.subprocess, 'Popen', side_effect=fake_popen), \\
     patch.object(supervisor, '_private_directory', side_effect=lambda path: Path(path).mkdir(parents=True, exist_ok=True)), \\
     patch.object(supervisor, 'state_root', return_value=Path(sys.argv[3])), \\
     patch.object(supervisor.time, 'monotonic', side_effect=[0, 11]):
    try:
        client.ensure_running()
    except supervisor.SupervisorError as error:
        assert str(error) == 'persistent model supervisor did not start'
    else:
        raise AssertionError('fixture launch should time out without a child')
assert len(captured) == 2, captured
kwargs = captured['kwargs']
assert captured['args'][0] == [
    sys.executable, '-m', 'project_control.runtime_binding', 'local_worker.supervisor',
    '--serve', '--repo-root', str(client.repo_root),
]
assert kwargs['cwd'] == expected_cwd, (kwargs['cwd'], expected_cwd)
assert kwargs['env']['PYTHONPATH'].split(os.pathsep) == [
    str(expected_cwd), '/ambient/supplier', '/other/path',
]
assert kwargs['env']['LCW_FIXTURE_FLAG'] == 'preserved'
assert not isinstance(captured['args'][0], str)
print(json.dumps({'cwd': str(kwargs['cwd']), 'args': captured['args'][0],
                  'pythonpath': kwargs['env']['PYTHONPATH']}))
"""
        launch_env = dict(os.environ)
        launch_env["PYTHONPATH"] = str(REPOSITORY / "src")
        launch_env.pop(runtime_binding.RELEASE_MANIFEST_VARIABLE, None)
        launch_env.pop(runtime_binding.RELEASE_DIGEST_VARIABLE, None)
        launch_check = subprocess.run(
            [sys.executable, "-c", launch_code, str(workspace),
             str(Path(self.temp.name) / "launch-runtime"),
             str(Path(self.temp.name) / "launch-state")],
            cwd=REPOSITORY / "src", env=launch_env,
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(launch_check.returncode, 0, launch_check.stderr)
        self.assertIn('"cwd":', launch_check.stdout)
        self.assertFalse(marker.exists(), launch_check.stdout)
        self.assertFalse(shadow_marker.exists(), launch_check.stdout)

    def test_receiver_manifest_preserves_transferred_supplier_files_and_layout(self) -> None:
        transfer_path = REPOSITORY / "docs/pa1/runtime-transfer.json"
        transfer = json.loads(transfer_path.read_text(encoding="utf-8"))
        receiver_manifest = json.loads((RECEIVER / "receiver-manifest.json").read_text(encoding="utf-8"))
        rollback_bundle = SUPPLIER / "docs/pa1/rollback/operator-bundle"
        rollback_manifest = json.loads((rollback_bundle / "operator-bundle-manifest.json").read_text(encoding="utf-8"))
        rollback_provenance_path = rollback_bundle / "rollback/provenance.json"
        rollback_provenance_raw = rollback_provenance_path.read_bytes()
        rollback_provenance = json.loads(rollback_provenance_raw)
        rollback_archive = rollback_bundle / "rollback" / rollback_provenance["archive_path"]
        rollback_archive_raw = rollback_archive.read_bytes()
        transition_receipt_path = rollback_bundle / "tests/transition-test-receipt.json"
        transition_receipt_raw = transition_receipt_path.read_bytes()
        transition_receipt = json.loads(transition_receipt_raw)
        runtime_transition_path = rollback_bundle / "runtime-transition.json"
        runtime_transition_raw = runtime_transition_path.read_bytes()
        runtime_transition = json.loads(runtime_transition_raw)
        # The receiver manifest identifies the Project Control checkout that
        # hosts the executable runtime. The transfer record and checksummed
        # rollback archive identify the original Skills source; the current
        # catalog path contains retirement navigation markers for some files.
        self.assertRegex(receiver_manifest["source_commit"],
                         r"^[0-9a-f]{40}\+PC-PA1-[A-Z0-9-]+-reviewed-peer-rpc$")
        self.assertEqual(transfer["source_commit"], "95818340006dd50ef233d7c67ddca2da8eb08bc4")
        self.assertEqual(receiver_manifest["source_inventory_path"], transfer["source_inventory_path"])
        self.assertEqual(receiver_manifest["source_inventory_sha256"], transfer["source_inventory_sha256"])
        self.assertEqual(rollback_provenance["source_commit"], transfer["source_commit"])
        self.assertEqual(rollback_provenance["inventory_sha256"], transfer["source_inventory_sha256"])
        self.assertEqual(runtime_transition["authority_exclusion_source_inventory_sha256"],
                         transfer["source_inventory_sha256"])
        self.assertEqual(hashlib.sha256(rollback_archive_raw).hexdigest(), rollback_provenance["archive_sha256"])
        self.assertEqual(hashlib.sha256(rollback_archive_raw).hexdigest(),
                         rollback_manifest["files"]["rollback/local-coding-worker-portable.tar.gz"]["sha256"])
        self.assertEqual(hashlib.sha256(rollback_provenance_raw).hexdigest(),
                         rollback_manifest["files"]["rollback/provenance.json"]["sha256"])
        self.assertEqual(hashlib.sha256(transition_receipt_raw).hexdigest(),
                         rollback_manifest["files"]["tests/transition-test-receipt.json"]["sha256"])
        self.assertEqual(hashlib.sha256(runtime_transition_raw).hexdigest(),
                         rollback_manifest["files"]["runtime-transition.json"]["sha256"])
        self.assertEqual(transition_receipt["evidence"]["rollback_archive"]["sha256"],
                         rollback_provenance["archive_sha256"])
        self.assertEqual(transition_receipt["evidence"]["rollback_provenance"]["sha256"],
                         hashlib.sha256(rollback_provenance_raw).hexdigest())
        self.assertEqual(transition_receipt["status"], "passed_staged_candidate")
        self.assertEqual(transition_receipt["result"]["failures"], 0)
        self.assertEqual(transition_receipt["result"]["errors"], 0)
        self.assertEqual(len(receiver_manifest["files"]), 42)
        self.assertEqual(len(transfer["transferred_files"]), 41)
        self.assertEqual(transfer["source_inventory_sha256"], hashlib.sha256(
            (SUPPLIER / "docs/pa1/runtime-handoff.json").read_bytes()
        ).hexdigest())

        archived_file_hashes = {item["path"]: item for item in rollback_provenance["archived_files"]}
        active_retirement_hashes = runtime_transition["candidate_hashes"]
        source_contents: dict[str, bytes] = {}
        current_receiver_differences: set[str] = set()

        transformed_paths: set[str] = set()
        with tarfile.open(rollback_archive, "r:gz") as archive:
            archive_members = {member.name: member for member in archive.getmembers() if member.isfile()}
            for entry in transfer["transferred_files"]:
                supplier_path = entry["supplier_path"]
                active_file = SUPPLIER / supplier_path
                expected_source_sha = entry["source_sha256"]
                if active_file.is_file():
                    active_sha = hashlib.sha256(active_file.read_bytes()).hexdigest()
                    if active_sha != expected_source_sha:
                        # The catalog path now holds only retirement markers;
                        # verify their bytes against the transition candidate
                        # record before falling back to the preserved source.
                        self.assertEqual(active_retirement_hashes.get(supplier_path), active_sha, supplier_path)

                archived_record = archived_file_hashes.get(supplier_path)
                self.assertIsNotNone(archived_record, supplier_path)
                self.assertEqual(archived_record["sha256"], expected_source_sha, supplier_path)
                archive_path = "local-coding-worker/" + supplier_path
                member = archive_members.get(archive_path)
                self.assertIsNotNone(member, archive_path)
                archived_source = archive.extractfile(member).read()
                self.assertEqual(len(archived_source), archived_record["bytes"], supplier_path)
                self.assertEqual(hashlib.sha256(archived_source).hexdigest(), expected_source_sha, supplier_path)
                source_contents[supplier_path] = archived_source

                receiver_file = RECEIVER / entry["receiver_path"]
                receiver_sha = hashlib.sha256(receiver_file.read_bytes()).hexdigest()
                self.assertEqual(receiver_sha, receiver_manifest["files"][entry["receiver_path"]],
                                 entry["receiver_path"])
                if receiver_sha != expected_source_sha:
                    current_receiver_differences.add(entry["receiver_path"])
                if entry["source_sha256"] != entry["receiver_sha256"]:
                    self.assertTrue(entry.get("transformation"), entry["receiver_path"])
                    transformed_paths.add(entry["receiver_path"])
                else:
                    self.assertIsNone(entry.get("transformation"), entry["receiver_path"])

        self.assertEqual(transformed_paths, {
            "local_worker/controller.py", "local_worker/supervisor.py", "scripts/worker_core.py",
        })
        # These three current receiver files changed after the original
        # transfer record. Their current bytes are checked by the receiver
        # manifest above; keep their names explicit so they cannot be mistaken
        # for original-source transfer transformations.
        post_transfer_receiver_changes = {
            "local_worker/observer_runtime.py",
            "local_worker/residency.py",
            "local_worker/servers/llama_cpp.py",
            "scripts/local_worker.py",
        }
        self.assertEqual(current_receiver_differences, transformed_paths | post_transfer_receiver_changes)

        controller = (RECEIVER / "local_worker/controller.py").read_text(encoding="utf-8")
        supervisor = (RECEIVER / "local_worker/supervisor.py").read_text(encoding="utf-8")
        worker_core = (RECEIVER / "scripts/worker_core.py").read_text(encoding="utf-8")
        self.assertIn("PROJECT_CONTROL_SKILLS_ROOT", controller)
        self.assertIn("LCW_", controller)
        self.assertIn('"-m", "project_control.runtime_binding", "local_worker.supervisor"', supervisor)
        self.assertIn("PROJECT_CONTROL_SKILLS_ROOT", worker_core)
        self.assertIn("LCW_", worker_core)

        for relative in (
            "config/host-profile.example.toml", "config/production-profile.toml",
            "schemas/delegation-spec-v1.schema.json", "schemas/delegation-spec-v2.schema.json",
            "schemas/harness-policy-v2.schema.json", "schemas/model-cache-v1.schema.json",
            "schemas/model-outcome-v1.schema.json", "schemas/model-service-profile-v2.schema.json",
            "schemas/worker-result-v1.schema.json", "schemas/worker-result-v2.schema.json",
        ):
            self.assertTrue((RECEIVER / relative).is_file(), relative)
            supplier_relative = "local-coding-worker/" + relative
            self.assertEqual(
                hashlib.sha256(source_contents[supplier_relative]).hexdigest(),
                hashlib.sha256((RECEIVER / relative).read_bytes()).hexdigest(),
                relative,
            )

        excluded_ids = {item["id"] for item in transfer["authority_exclusions"]}
        self.assertEqual(excluded_ids, {"todo-orchestrator", "cuda", "cpp-context-compiler"})
        receiver_paths = set(receiver_manifest["files"])
        self.assertFalse(any(path.startswith(("todo-orchestrator/", "cuda/", "cpp-context-compiler/")) for path in receiver_paths))

    def test_recorded_public_mcp_schemas_keep_the_current_profile_counts(self) -> None:
        baseline = json.loads((REPOSITORY / "docs/pa1/baseline-tests.json").read_text(encoding="utf-8"))
        profiles = baseline["public_mcp_discovery"]["profiles"]
        self.assertEqual({name: item["tool_count"] for name, item in profiles.items()}, {
            "observer": 11, "codex": 12, "mutator": 16,
        })
        for profile in profiles.values():
            self.assertEqual(len(profile["tools"]), profile["tool_count"])
            self.assertTrue(all(len(digest) == 64 for digest in profile["tools"].values()))


if __name__ == "__main__":
    unittest.main()
