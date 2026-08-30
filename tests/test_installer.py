from __future__ import annotations

import importlib.util
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
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


class InstallerTests(unittest.TestCase):
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

            build_candidate(
                project_control_root=project,
                skills_root=skills,
                destination=destination,
                runner=runner,
            )

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
