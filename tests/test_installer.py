from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pcu_installer", ROOT / "packaging" / "installer.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
AtomicCutover = MODULE.AtomicCutover
CutoverStep = MODULE.CutoverStep
InstallError = MODULE.InstallError
candidate_manifest_digest = MODULE.candidate_manifest_digest


def _test_todo_wheel(project: Path) -> Path:
    wheel = project.parent / "todo_orchestrator-0.2.1-py3-none-any.whl"
    if not wheel.exists():
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("todo_orchestrator/__init__.py", "API_VERSION = 1\n")
            archive.writestr("todo_orchestrator-0.2.1.dist-info/METADATA",
                             "Metadata-Version: 2.1\nName: todo-orchestrator\nVersion: 0.2.1\n\n")
            archive.writestr("todo_orchestrator-0.2.1.dist-info/WHEEL",
                             "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
    return wheel


def build_candidate(*, project_control_root, todo_wheel=None, **kwargs):
    project = Path(project_control_root)
    wheel = todo_wheel or _test_todo_wheel(project)
    config = project / "config" / "todo-orchestrator-runtime.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    if not config.exists():
        config.write_text(json.dumps({"format": "project-control.todo-runtime-lock", "version": 1,
                                      "distribution": "todo-orchestrator", "distribution_version": "0.2.1",
                                      "source_commit": None, "wheel_sha256": None,
                                      "package_fingerprint": None, "provisional": True}), encoding="utf-8")
    runner = kwargs.get("runner")
    if runner is not None:
        def install_fixture(command):
            result = runner(command)
            if "pip" in command:
                if "--python" in command:
                    python = Path(command[command.index("--python") + 1])
                    venv = python.parent.parent
                else:
                    venv = Path(command[0]).parents[1]
                package = venv / "lib/python3.13/site-packages/todo_orchestrator"
                package.mkdir(parents=True, exist_ok=True)
                (package / "__init__.py").write_text("API_VERSION = 1\n", encoding="utf-8")
                dist = venv / "lib/python3.13/site-packages/todo_orchestrator-0.2.1.dist-info"
                dist.mkdir(parents=True, exist_ok=True)
                (dist / "METADATA").write_text("Name: todo-orchestrator\nVersion: 0.2.1\n", encoding="utf-8")
            return result
        kwargs["runner"] = install_fixture
    with mock.patch.dict(os.environ, {"PROJECT_CONTROL_ALLOW_PROVISIONAL_TODO": "1"}):
        return MODULE.build_candidate(project_control_root=project_control_root,
                                      todo_wheel=wheel, **kwargs)


def completed(command, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)


def fake_project_control_wheel(temporary):
    """Create the receiver resource a successful fake pip install would expose."""
    runtime = temporary / "lib/python3.13/site-packages/project_control/local_runtime"
    package = runtime / "local_worker"
    package.mkdir(parents=True, exist_ok=True)
    files = {
        "local_worker/__init__.py": b'"""Canonical receiver package."""\n',
        "local_worker/supervisor.py": b"# receiver fixture\n",
        "config/production-profile.toml": b"[profile]\n",
    }
    for relative, data in files.items():
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest = {
        "schema_version": 1,
        "source_root": "/fixture/project-control/local_runtime",
        "source_commit": "a" * 40,
        "files": {relative: hashlib.sha256(data).hexdigest() for relative, data in files.items()},
    }
    (runtime / "receiver-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    return runtime


class InstallerTests(unittest.TestCase):
    def test_candidate_requires_an_explicit_standalone_wheel(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "project"
            project.mkdir()
            (project / "pyproject.toml").write_text("[project]\nname='fixture'\n")
            with self.assertRaisesRegex(InstallError, "wheel is missing or unsafe"):
                MODULE.build_candidate(project_control_root=project,
                                       todo_wheel=Path(root) / "missing.whl",
                                       destination=Path(root) / "candidate")

    @unittest.skipUnless(shutil.which("uv"), "uv is required for offline candidate builds")
    def test_offline_candidate_uses_explicit_cache_and_records_source_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, skills, cache, destination = (root / name for name in
                ("project", "skills", "cache", "release"))
            project.mkdir(); skills.mkdir(); cache.mkdir()
            (project / "pyproject.toml").write_text("[project]\nname='fixture'\n")
            (project / "current.py").write_text("# current working tree\n")
            todo = skills / "todo-orchestrator"
            todo.mkdir(); (todo / "pyproject.toml").write_text("[project]\nname='todo'\n")
            (todo / "current.py").write_text("# current Todo tree\n")
            commands = []

            def runner(command):
                commands.append(tuple(command))
                if command[0] == "git":
                    if "ls-files" in command:
                        source_root = Path(command[2])
                        relative = ["pyproject.toml", "current.py"]
                        return completed(command, stdout="\0".join(relative) + "\0")
                    return completed(command, stdout="fixture-commit")
                if "venv" in command:
                    staging = Path(command[-1])
                    (staging / "bin").mkdir(exist_ok=True)
                    (staging / "lib/python3.13/site-packages").mkdir(parents=True)
                    return completed(command)
                if "pip" in command:
                    python = Path(command[command.index("--python") + 1])
                    fake_project_control_wheel(python.parents[1])
                    return completed(command)
                return completed(command)

            build_candidate(project_control_root=project, skills_root=skills,
                            destination=destination, offline=True, uv_cache_dir=cache, runner=runner)
            install = next(command for command in commands if "pip" in command)
            self.assertIn("--offline", install)
            self.assertIn(str(cache.resolve()), install)
            release = json.loads((destination / "release-manifest.json").read_text())
            self.assertEqual(release["schema_version"], 4)
            inventory = destination / release["source_working_tree_inventory"]["path"]
            self.assertEqual(hashlib.sha256(inventory.read_bytes()).hexdigest(),
                             release["source_working_tree_inventory"]["sha256"])
            data = json.loads(inventory.read_text())
            self.assertEqual(data["project_control"]["files"]["current.py"]["sha256"],
                             hashlib.sha256(b"# current working tree\n").hexdigest())
            self.assertEqual(data["todo_distribution"]["version"], "0.2.1")
            self.assertEqual(data["todo_distribution"]["wheel_sha256"],
                             MODULE._sha256_file(_test_todo_wheel(project)))

    def test_rollback_inventory_accepts_named_list_and_mapping(self) -> None:
        records = [
            {"name": "project-control", "enabled": True,
             "transport": {"type": "stdio", "command": "/qualified/project-control-release"}},
            {"name": "coding-workflow", "enabled": False, "transport": {"type": "stdio"}},
            {"name": "unrelated", "enabled": True, "transport": {"type": "http"}},
        ]
        for value in (records, {record["name"]: record for record in records}):
            with self.subTest(shape=type(value).__name__):
                def runner(command):
                    if command[:3] == ("codex", "mcp", "list"):
                        return completed(command, stdout=json.dumps(value))
                    if "cat" in command:
                        return completed(command, stdout="[Service]\nExecStart=/preserved/launcher\n")
                    return completed(command, stdout="LoadState=loaded\nActiveState=active\nExecStart=/preserved/launcher\n")
                inventory = MODULE.capture_rollback_inventory(runner=runner)
                self.assertEqual(inventory.project_control_registration, records[0])
                self.assertEqual(inventory.coding_workflow_registration, records[1])
                self.assertEqual(inventory.service_properties["ActiveState"], "active")
                self.assertEqual(inventory.service_unit_sha256, hashlib.sha256(
                    b"[Service]\nExecStart=/preserved/launcher\n").hexdigest())
        self.assertEqual(MODULE._json_object('{"project-control":{"enabled":true}}'),
                         {"project-control": {"enabled": True}})
        self.assertEqual(MODULE._json_object('[]'), {})

    def test_rollback_inventory_rejects_ambiguous_or_malformed_registrations(self) -> None:
        values = ['not-json', 'null', '1', '[null]', '[{}]', '[{"name":1}]',
                  '[{"name":" "}]', '[{"name":"project-control"},{"name":"project-control"}]',
                  '{"project-control":null}', '{"project-control":{"name":"other"}}',
                  '{"project-control":{},"project-control":{}}']
        for value in values:
            with self.subTest(value=value):
                calls = []
                def runner(command):
                    calls.append(command)
                    return completed(command, stdout=value)
                with self.assertRaises(InstallError):
                    MODULE.capture_rollback_inventory(runner=runner)
                self.assertEqual(calls, [("codex", "mcp", "list", "--json")])

    def test_candidate_omits_worker_skill_and_legacy_catalog_pins(self) -> None:
        from project_control.runtime_identity import (
            RELEASE_DIGEST_VARIABLE, RELEASE_MANIFEST_VARIABLE,
            RuntimeIdentityError, _release,
        )

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            project, skills, destination = root / "project", root / "skills", root / "candidate"
            project.mkdir()
            (project / "pyproject.toml").write_text("")
            (project / "src").mkdir()
            (project / "src/project_control").symlink_to(ROOT / "src/project_control", target_is_directory=True)
            originals = {}
            for name in ("todo-orchestrator", "cuda", "cpp-context-compiler", "local-coding-worker"):
                entry = f"{name}/SKILL.md"
                route = f"{name}/references/nested/router.md"
                for relative in (entry, route):
                    source = skills / relative
                    source.parent.mkdir(parents=True, exist_ok=True)
                    originals[relative] = f"# Authored {relative}\n".encode()
                    source.write_bytes(originals[relative])
            (skills / "todo-orchestrator/pyproject.toml").write_text("")
            package = skills / "todo-orchestrator/todo_orchestrator"
            package.mkdir()
            (package / "__init__.py").write_text("# frozen kernel\n")
            worker_runtime = skills / "local-coding-worker/local_worker"
            worker_runtime.mkdir(parents=True)
            (worker_runtime / "supervisor.py").write_text("# retired skill runtime\n")
            (skills / "integrations").mkdir()
            (skills / "integrations/native-skill-catalog.json").write_text('{"entries": []}\n')
            (skills / "integrations/native-skill-routing.md").write_text("# Legacy external routing\n")

            def runner(command):
                if command[0] == "git":
                    return completed(command, stdout="exact-source-commit\n")
                if "venv" in command:
                    Path(command[-1], "bin").mkdir(parents=True)
                return completed(command)

            build_candidate(project_control_root=project, skills_root=skills, destination=destination, runner=runner)
            snapshot = destination / "runtime-skills"
            for relative, expected in originals.items():
                if relative.startswith(("local-coding-worker/", "todo-orchestrator/")):
                    self.assertFalse((snapshot / relative).exists())
                else:
                    self.assertEqual((snapshot / relative).read_bytes(), expected)
            self.assertFalse((snapshot / "local-coding-worker").exists())
            self.assertFalse((snapshot / "integrations/native-skill-catalog.json").exists())
            self.assertFalse((snapshot / "integrations/native-skill-routing.md").exists())
            manifest = destination / "release-manifest.json"
            data = json.loads(manifest.read_bytes())
            self.assertEqual(data["todo_wheel_sha256"], MODULE._sha256_file(_test_todo_wheel(project)))
            self.assertNotIn("frozen_skill_resources", data)
            environment = {
                RELEASE_MANIFEST_VARIABLE: str(manifest),
                RELEASE_DIGEST_VARIABLE: hashlib.sha256(manifest.read_bytes()).hexdigest(),
            }
            self.assertIsNotNone(_release(environment))

            self.assertNotIn("frozen_skill_resources", data)

    def test_candidate_does_not_require_legacy_native_catalog_resources(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            skills = root / "skills"
            todo = skills / "todo-orchestrator/todo_orchestrator"
            todo.mkdir(parents=True)
            (todo / "__init__.py").write_text("# todo\n")
            record = MODULE._freeze_skills(skills, root / "staging", root / "candidate")
            self.assertNotIn("frozen_skill_resources", record)

    def test_candidate_binds_frozen_local_analysis_without_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            project, skills, destination = root / "project", root / "skills", root / "candidate"
            project.mkdir(); (project / "pyproject.toml").write_text("")
            (skills / "todo-orchestrator").mkdir(parents=True); (skills / "todo-orchestrator" / "pyproject.toml").write_text("")
            (skills / "local-coding-worker" / "local_worker").mkdir(parents=True)
            (skills / "local-coding-worker" / "local_worker" / "supervisor.py").write_text("# frozen\n")

            def runner(command):
                if command[0] == "git": return completed(command, stdout="hash\n")
                if "venv" in command:
                    staging = Path(command[-1]); (staging / "bin").mkdir(parents=True)
                    (staging / "lib/python3.13/site-packages").mkdir(parents=True)
                    return completed(command)
                if "pip" in command:
                    fake_project_control_wheel(Path(command[0]).parents[1])
                    return completed(command)
                return completed(command)
            build_candidate(project_control_root=project, skills_root=skills, destination=destination, runner=runner)
            self.assertFalse(list((destination / "lib/python3.13/site-packages").glob("project_control_observer_analysis.pth")))
            release = json.loads((destination / "release-manifest.json").read_text())
            installed_runtime = destination / "lib/python3.13/site-packages/project_control/local_runtime"
            receiver_manifest = (installed_runtime / "receiver-manifest.json").read_bytes()
            binding = release["local_runtime_binding"]
            self.assertEqual(binding["path"], "project_control/local_runtime")
            self.assertEqual(binding["manifest_sha256"], hashlib.sha256(receiver_manifest).hexdigest())
            self.assertEqual(binding["fingerprint"], hashlib.sha256(json.dumps(
                dict(sorted(json.loads(receiver_manifest)["files"].items())), sort_keys=True,
                separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest())
    def test_candidate_is_published_only_after_both_packages_install(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            project = root / "project"
            skills = root / "skills"
            destination = root / "candidate"
            project.mkdir()
            (project / "pyproject.toml").write_text("", encoding="utf-8")
            (skills / "todo-orchestrator").mkdir(parents=True)
            (skills / "todo-orchestrator" / "pyproject.toml").write_text("", encoding="utf-8")
            commands = []

            def runner(command):
                commands.append(tuple(command))
                if command[:3] == ("git", "-C", str(project)) or command[:3] == ["git", "-C", str(project)]:
                    return completed(command, stdout="pc\n")
                if len(command) > 2 and command[0:2] == ("git", "-C"):
                    return completed(command, stdout="todo\n")
                if "venv" in command:
                    Path(command[-1], "bin").mkdir(parents=True)
                return completed(command)

            identity = build_candidate(
                project_control_root=project,
                skills_root=skills,
                destination=destination,
                runner=runner,
            )
            self.assertTrue((destination / "pcu-candidate.json").is_file())
            self.assertEqual(identity.project_control_commit, "pc")
            installs = [command for command in commands if "pip" in command]
            self.assertEqual(len(installs), 2)
            self.assertIn(str(project), installs[0])
            self.assertIn(".whl", installs[1][-1])

    def test_promoted_console_and_module_entrypoints_use_final_path(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            project = root / "project"
            skills = root / "skills"
            destination = root / "candidate final"
            project.mkdir()
            (project / "pyproject.toml").write_text("", encoding="utf-8")
            (skills / "todo-orchestrator").mkdir(parents=True)
            (skills / "todo-orchestrator" / "pyproject.toml").write_text("", encoding="utf-8")
            staging_paths = []
            executed = []

            def runner(command):
                command = tuple(command)
                if command[:2] == ("git", "-C"):
                    return completed(command, stdout="hash\n")
                if "venv" in command:
                    staging = Path(command[-1])
                    staging_paths.append(staging)
                    bin_dir = staging / "bin"
                    bin_dir.mkdir(parents=True)
                    python = bin_dir / "python"
                    python.write_text(
                        f"#!{sys.executable}\n"
                        "import json, os, sys\n"
                        "capture = os.environ.get('PCU_LAUNCH_CAPTURE')\n"
                        "if capture:\n"
                        "    keys = ('PROJECT_CONTROL_SKILLS_ROOT', 'PROJECT_CONTROL_OBSERVER_SKILLS_ROOT', "
                        "'PROJECT_CONTROL_RELEASE_MANIFEST', 'PROJECT_CONTROL_RELEASE_DIGEST', "
                        "'PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR', 'TODO_BACKGROUND_HOST_RUNTIME_DIR', "
                        "'PROJECT_CONTROL_OBSERVER_GPU_UUIDS', 'CUDA_VISIBLE_DEVICES', 'PYTHONPATH', "
                        "'PYTHONHOME', 'PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT', "
                        "'PROJECT_CONTROL_RUNTIME_PYTHON', 'CODING_WORKFLOW_RUNTIME_PYTHON', "
                        "'CORE4_SUPERVISOR_RUNTIME_DIR')\n"
                        "    with open(capture, 'w', encoding='utf-8') as stream:\n"
                        "        json.dump({'argv': sys.argv[1:], 'environment': {key: os.environ.get(key) for key in keys}}, stream)\n"
                        "raise SystemExit(0)\n",
                        encoding="utf-8",
                    )
                    python.chmod(0o755)
                    console = bin_dir / "project-control"
                    console.write_text(
                        f"#!{python}\nimport sys\nraise SystemExit(0)\n",
                        encoding="utf-8",
                    )
                    console.chmod(0o755)
                    return completed(command)
                if "pip" in command:
                    return completed(command)
                executed.append(command)
                return subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )

            with mock.patch.dict(os.environ, {
                "PROJECT_CONTROL_SKILLS_ROOT": "/unsealed/custom-skills",
                "PROJECT_CONTROL_OBSERVER_SKILLS_ROOT": "/unsealed/custom-observer-skills",
            }):
                build_candidate(
                    project_control_root=project,
                    skills_root=skills,
                    destination=destination,
                    runner=runner,
                )
                launcher = (destination / "bin" / "project-control-release").read_text(encoding="utf-8")
                inference_launcher = destination / "bin" / "project-control-inference"
                inference_text = inference_launcher.read_text(encoding="utf-8")
                frozen_root = str(destination / "runtime-skills")
                self.assertIn(f"export PROJECT_CONTROL_SKILLS_ROOT='{frozen_root}'", launcher)
                self.assertNotIn("export PROJECT_CONTROL_OBSERVER_SKILLS_ROOT=", launcher)
                for release_line in MODULE._candidate_release_environment(
                    destination, hashlib.sha256((destination / "release-manifest.json").read_bytes()).hexdigest()
                ).splitlines():
                    self.assertIn(release_line, inference_text)
                self.assertEqual(os.environ["PROJECT_CONTROL_SKILLS_ROOT"], "/unsealed/custom-skills")
                self.assertEqual(os.environ["PROJECT_CONTROL_OBSERVER_SKILLS_ROOT"], "/unsealed/custom-observer-skills")

            self.assertEqual(
                executed,
                [
                    (str(destination / "bin" / "project-control"), "--help"),
                    (str(destination / "bin" / "python"), "-m", "project_control", "--help"),
                    ("/bin/sh", "-n", str(destination / "bin" / "project-control-release")),
                    ("/bin/sh", "-n", str(destination / "bin" / "project-control-inference")),
                ],
            )
            state_root = root / "operator state"
            host_runtime = root / "host runtime"
            gpu_uuids = '["GPU-preserved-operator-uuid"]'
            capture = root / "inference-launch.json"
            launch_environment = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(root / "home"),
                "PCU_LAUNCH_CAPTURE": str(capture),
                "PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR": str(state_root),
                "TODO_BACKGROUND_HOST_RUNTIME_DIR": str(host_runtime),
                "PROJECT_CONTROL_OBSERVER_GPU_UUIDS": gpu_uuids,
                "PROJECT_CONTROL_OBSERVER_SKILLS_ROOT": str(root / "operator-domain-skills"),
                "CUDA_VISIBLE_DEVICES": "2,4",
                "PYTHONPATH": "/unsealed/pythonpath",
                "PYTHONHOME": "/unsealed/pythonhome",
                "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT": "stale-fingerprint",
                "PROJECT_CONTROL_RUNTIME_PYTHON": "/unsealed/python",
                "CODING_WORKFLOW_RUNTIME_PYTHON": "/unsealed/coding-workflow-python",
                "CORE4_SUPERVISOR_RUNTIME_DIR": "/unsealed/supervisor-runtime",
                "PROJECT_CONTROL_RELEASE_MANIFEST": "/unsealed/release-manifest.json",
                "PROJECT_CONTROL_RELEASE_DIGEST": "0" * 64,
            }
            launched = subprocess.run(
                [str(destination / "bin" / "project-control-inference")],
                env=launch_environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual((launched.returncode, launched.stderr), (0, ""))
            captured = json.loads(capture.read_text(encoding="utf-8"))
            release_digest = hashlib.sha256((destination / "release-manifest.json").read_bytes()).hexdigest()
            self.assertEqual(captured["argv"], [
                "-m", "project_control.runtime_binding", "local_worker.supervisor", "--serve",
                "--repo-root", str(state_root), "--service-state-root", str(state_root),
                "--runtime-root", str(state_root / "runtime"), "--observer-only",
            ])
            self.assertEqual(captured["environment"], {
                "PROJECT_CONTROL_SKILLS_ROOT": str(destination / "runtime-skills"),
                "PROJECT_CONTROL_OBSERVER_SKILLS_ROOT": str(root / "operator-domain-skills"),
                "PROJECT_CONTROL_RELEASE_MANIFEST": str(destination / "release-manifest.json"),
                "PROJECT_CONTROL_RELEASE_DIGEST": release_digest,
                "PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR": str(state_root),
                "TODO_BACKGROUND_HOST_RUNTIME_DIR": str(host_runtime),
                "PROJECT_CONTROL_OBSERVER_GPU_UUIDS": gpu_uuids,
                "CUDA_VISIBLE_DEVICES": "2,4",
                "PYTHONPATH": None,
                "PYTHONHOME": None,
                "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT": None,
                "PROJECT_CONTROL_RUNTIME_PYTHON": None,
                "CODING_WORKFLOW_RUNTIME_PYTHON": None,
                "CORE4_SUPERVISOR_RUNTIME_DIR": None,
            })
            source_gpu_line = next(
                line for line in (ROOT / "scripts" / "pc-inference").read_text(encoding="utf-8").splitlines()
                if line.startswith("export PROJECT_CONTROL_OBSERVER_GPU_UUIDS=")
            )
            self.assertEqual(source_gpu_line.split("=", 1)[1].strip("'"), MODULE.DEFAULT_OBSERVER_GPU_UUIDS)
            fallback_capture = root / "inference-launch-default-gpus.json"
            fallback_environment = dict(launch_environment)
            fallback_environment.pop("PROJECT_CONTROL_OBSERVER_GPU_UUIDS")
            fallback_environment["PCU_LAUNCH_CAPTURE"] = str(fallback_capture)
            fallback_launch = subprocess.run(
                [str(destination / "bin" / "project-control-inference")],
                env=fallback_environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual((fallback_launch.returncode, fallback_launch.stderr), (0, ""))
            fallback_captured = json.loads(fallback_capture.read_text(encoding="utf-8"))
            self.assertEqual(
                fallback_captured["environment"]["PROJECT_CONTROL_OBSERVER_GPU_UUIDS"],
                MODULE.DEFAULT_OBSERVER_GPU_UUIDS,
            )
            capture.unlink()
            fallback_capture.unlink()
            rejected = subprocess.run(
                [str(destination / "bin" / "project-control-inference"), "--repo-root", "/other"],
                env=launch_environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("operator arguments are not accepted", rejected.stderr)
            self.assertFalse(capture.exists())
            console = (destination / "bin" / "project-control").read_bytes()
            inference_bytes = (destination / "bin" / "project-control-inference").read_bytes()
            for staging in staging_paths:
                self.assertNotIn(os.fsencode(staging), console)
                self.assertNotIn(os.fsencode(staging), inference_bytes)
            self.assertIn(os.fsencode(destination / "bin" / "python"), console)
            self.assertIn(os.fsencode(destination / "bin" / "python"), inference_bytes)
            expected_digest = hashlib.sha256(
                (destination / "pcu-candidate.json").read_bytes()
            ).hexdigest()
            self.assertEqual(candidate_manifest_digest(destination), expected_digest)

    def test_failed_candidate_leaves_no_destination(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            project = root / "project"
            skills = root / "skills"
            destination = root / "candidate"
            project.mkdir()
            (project / "pyproject.toml").write_text("", encoding="utf-8")
            (skills / "todo-orchestrator").mkdir(parents=True)
            (skills / "todo-orchestrator" / "pyproject.toml").write_text("", encoding="utf-8")

            def runner(command):
                if command[0] == "git":
                    return completed(command, stdout="hash\n")
                if "venv" in command:
                    Path(command[-1], "bin").mkdir(parents=True)
                    return completed(command)
                return completed(command, code=1, stderr="install failed")

            with self.assertRaises(InstallError):
                build_candidate(project_control_root=project, skills_root=skills, destination=destination, runner=runner)
            self.assertFalse(destination.exists())

    def test_failed_promoted_entrypoint_removes_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            project = root / "project"
            skills = root / "skills"
            destination = root / "candidate"
            project.mkdir()
            (project / "pyproject.toml").write_text("", encoding="utf-8")
            (skills / "todo-orchestrator").mkdir(parents=True)
            (skills / "todo-orchestrator" / "pyproject.toml").write_text("", encoding="utf-8")

            def runner(command):
                command = tuple(command)
                if command[:2] == ("git", "-C"):
                    return completed(command, stdout="hash\n")
                if "venv" in command:
                    bin_dir = Path(command[-1]) / "bin"
                    bin_dir.mkdir(parents=True)
                    python = bin_dir / "python"
                    python.write_text(
                        f"#!{sys.executable}\nraise SystemExit(0)\n",
                        encoding="utf-8",
                    )
                    python.chmod(0o755)
                    console = bin_dir / "project-control"
                    console.write_text(f"#!{python}\nraise SystemExit(0)\n", encoding="utf-8")
                    console.chmod(0o755)
                    return completed(command)
                if "pip" in command:
                    return completed(command)
                return completed(command, code=127, stderr="entry point unavailable")

            with self.assertRaisesRegex(InstallError, "promoted candidate entry point failed"):
                build_candidate(
                    project_control_root=project,
                    skills_root=skills,
                    destination=destination,
                    runner=runner,
                )
            self.assertFalse(destination.exists())

    def test_cutover_requires_authority_and_rolls_back_in_reverse(self) -> None:
        calls = []
        steps = (
            CutoverStep(("apply-1",), ("undo-1",)),
            CutoverStep(("apply-2",), ("undo-2",)),
        )

        def runner(command):
            calls.append(tuple(command))
            return completed(command, code=1 if command[0] == "apply-2" else 0)

        cutover = AtomicCutover(steps, runner=runner)
        with self.assertRaisesRegex(InstallError, "explicit authority"):
            cutover.execute(authority_to_install=False)
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(InstallError, "cutover step failed"):
            cutover.execute(authority_to_install=True)
        self.assertEqual(calls, [("apply-1",), ("apply-2",), ("undo-1",)])


if __name__ == "__main__":
    unittest.main()
