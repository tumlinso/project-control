from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class PackageMetadataTests(unittest.TestCase):
    def test_project_control_distribution_includes_todo_package(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(metadata["project"]["name"], "project-control")
        packages = metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
        self.assertEqual(
            set(packages),
            {"src/project_control", "src/todo_orchestrator"},
        )
        self.assertTrue((ROOT / "src" / "todo_orchestrator" / "__init__.py").is_file())

    def test_todo_is_not_a_separate_runtime_distribution(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertFalse(any(item.lower().startswith("todo-orchestrator") for item in metadata["project"]["dependencies"]))


if __name__ == "__main__":
    unittest.main()
