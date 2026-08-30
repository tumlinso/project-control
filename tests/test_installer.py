from __future__ import annotations

import importlib.util
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
            install = commands[-1]
            self.assertIn(str(project), install)
            self.assertIn(str(skills / "todo-orchestrator"), install)

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
