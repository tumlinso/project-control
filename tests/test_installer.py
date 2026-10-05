from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import sys
import tempfile
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
build_candidate = MODULE.build_candidate
candidate_manifest_digest = MODULE.candidate_manifest_digest


def completed(command, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)


def native_resources(skills):
    integrations = skills / "integrations"
    integrations.mkdir(parents=True, exist_ok=True)
    (integrations / "native-skill-catalog.json").write_bytes(b'{"entries": []}\n')
    (integrations / "native-skill-routing.md").write_bytes(b"# Native routing\n")


class InstallerTests(unittest.TestCase):
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

    def test_candidate_preserves_native_catalog_routes_and_pins_resources(self) -> None:
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
            native_resources(skills)
            originals = {}
            entries = []
            for name in ("todo-orchestrator", "cuda", "cpp-context-compiler", "local-coding-worker"):
                entry = f"{name}/SKILL.md"
                route = f"{name}/references/nested/router.md"
                for relative in (entry, route):
                    source = skills / relative
                    source.parent.mkdir(parents=True, exist_ok=True)
                    originals[relative] = f"# Authored {relative}\n".encode()
                    source.write_bytes(originals[relative])
                entries.append({"name": name, "entry": entry, "status": "accessible",
                                "routes": [{"path": "references/nested/router.md", "status": "accessible"}]})
            (skills / "todo-orchestrator/pyproject.toml").write_text("")
            package = skills / "todo-orchestrator/todo_orchestrator"
            package.mkdir()
            (package / "__init__.py").write_text("# frozen kernel\n")
            catalog = "integrations/native-skill-catalog.json"
            routing = "integrations/native-skill-routing.md"
            (skills / catalog).write_bytes(json.dumps({"entries": entries}).encode() + b"\n")
            for relative in (catalog, routing):
                originals[relative] = (skills / relative).read_bytes()

            def runner(command):
                if command[0] == "git":
                    return completed(command, stdout="exact-source-commit\n")
                if "venv" in command:
                    Path(command[-1], "bin").mkdir(parents=True)
                return completed(command)

            build_candidate(project_control_root=project, skills_root=skills, destination=destination, runner=runner)
            for relative, expected in originals.items():
                self.assertEqual((destination / "runtime-skills" / relative).read_bytes(), expected)
            manifest = destination / "release-manifest.json"
            data = json.loads(manifest.read_bytes())
            self.assertEqual(data["todo_commit"], "exact-source-commit")
            self.assertEqual(data["frozen_skill_resources"], {
                relative: hashlib.sha256(originals[relative]).hexdigest()
                for relative in (catalog, routing)
            })
            environment = {
                RELEASE_MANIFEST_VARIABLE: str(manifest),
                RELEASE_DIGEST_VARIABLE: hashlib.sha256(manifest.read_bytes()).hexdigest(),
            }
            self.assertIsNotNone(_release(environment))
            for relative in (catalog, routing):
                with self.subTest(resource=relative):
                    frozen = destination / "runtime-skills" / relative
                    frozen.write_bytes(originals[relative] + b"changed")
                    with self.assertRaisesRegex(RuntimeIdentityError, "Frozen Skills resource changed"):
                        _release(environment)
                    frozen.write_bytes(originals[relative])
                    frozen.unlink()
                    frozen.symlink_to(skills / relative)
                    with self.assertRaisesRegex(RuntimeIdentityError, "Frozen Skills resource changed"):
                        _release(environment)
                    frozen.unlink()
                    frozen.write_bytes(originals[relative])
            pins = data["frozen_skill_resources"]
            for invalid in (None, {"../native-skill-catalog.json": "a" * 64},
                            {**pins, catalog: "invalid"}):
                with self.subTest(invalid_pins=invalid):
                    data["frozen_skill_resources"] = invalid
                    manifest.write_text(json.dumps(data))
                    environment[RELEASE_DIGEST_VARIABLE] = hashlib.sha256(manifest.read_bytes()).hexdigest()
                    with self.assertRaisesRegex(RuntimeIdentityError, "Invalid frozen Skills resource"):
                        _release(environment)
            del data["frozen_skill_resources"]
            manifest.write_text(json.dumps(data))
            environment[RELEASE_DIGEST_VARIABLE] = hashlib.sha256(manifest.read_bytes()).hexdigest()
            self.assertIsNotNone(_release(environment))  # Existing manifests remain compatible.

    def test_missing_native_resource_refuses_candidate_publication(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            skills = root / "skills"
            native_resources(skills)
            (skills / "integrations/native-skill-routing.md").unlink()
            with self.assertRaisesRegex(InstallError, "required native Skills resource is missing"):
                MODULE._freeze_skills(skills, root / "staging", root / "candidate")

    def test_candidate_binds_frozen_local_analysis_without_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            project, skills, destination = root / "project", root / "skills", root / "candidate"
            project.mkdir(); (project / "pyproject.toml").write_text("")
            (skills / "todo-orchestrator").mkdir(parents=True); (skills / "todo-orchestrator" / "pyproject.toml").write_text("")
            (skills / "local-coding-worker" / "local_worker").mkdir(parents=True)
            (skills / "local-coding-worker" / "local_worker" / "supervisor.py").write_text("# frozen\n")
            native_resources(skills)

            def runner(command):
                if command[0] == "git": return completed(command, stdout="hash\n")
                if "venv" in command:
                    staging = Path(command[-1]); (staging / "bin").mkdir(parents=True)
                    (staging / "lib/python3.13/site-packages").mkdir(parents=True)
                    return completed(command)
                if "pip" in command: return completed(command)
                return completed(command)
            build_candidate(project_control_root=project, skills_root=skills, destination=destination, runner=runner)
            pth = next((destination / "lib/python3.13/site-packages").glob("project_control_observer_analysis.pth"))
            self.assertEqual(pth.read_text().strip(), str(destination / "runtime-skills/local-coding-worker"))
            release = json.loads((destination / "release-manifest.json").read_text())
            self.assertEqual(release["observer_analysis_binding"]["path"], str(destination / "runtime-skills/local-coding-worker"))
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

            native_resources(skills)

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
            install = next(command for command in commands if "pip" in command)
            self.assertIn(str(project), install)
            self.assertIn(str(skills / "todo-orchestrator"), install)

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

            native_resources(skills)

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
                        f"#!{sys.executable}\nimport sys\nraise SystemExit(0)\n",
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
                frozen_root = str(destination / "runtime-skills")
                self.assertIn(f"export PROJECT_CONTROL_SKILLS_ROOT='{frozen_root}'", launcher)
                self.assertIn(f"export PROJECT_CONTROL_OBSERVER_SKILLS_ROOT='{frozen_root}'", launcher)
                self.assertEqual(os.environ["PROJECT_CONTROL_SKILLS_ROOT"], "/unsealed/custom-skills")
                self.assertEqual(os.environ["PROJECT_CONTROL_OBSERVER_SKILLS_ROOT"], "/unsealed/custom-observer-skills")

            self.assertEqual(
                executed,
                [
                    (str(destination / "bin" / "project-control"), "--help"),
                    (str(destination / "bin" / "python"), "-m", "project_control", "--help"),
                ],
            )
            console = (destination / "bin" / "project-control").read_bytes()
            for staging in staging_paths:
                self.assertNotIn(os.fsencode(staging), console)
            self.assertIn(os.fsencode(destination / "bin" / "python"), console)
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

            native_resources(skills)

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

            native_resources(skills)

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
