from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "scripts" / "discover_skills_and_refs.py"


class DiscoveryNotesTests(unittest.TestCase):
    def test_discovery_finds_skills_and_reference_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            skill_dir = repo_root / "sample-skill"
            refs_dir = repo_root / "docs"
            skill_dir.mkdir(parents=True)
            refs_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                """---
name: sample-skill
description: Helps with planning and execution.
---
""",
                encoding="utf-8",
            )
            (refs_dir / "planning-guide.md").write_text("# Planning Guide\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--task",
                    "planning and execution",
                    "--pretty",
                ],
                capture_output=True,
                check=True,
                text=True,
            )
            data = json.loads(result.stdout)
            skill_paths = {item["path"] for item in data["skills"]}
            ref_paths = {item["path"] for item in data["reference_files"]}
            self.assertIn("sample-skill/SKILL.md", skill_paths)
            self.assertIn("docs/planning-guide.md", ref_paths)


if __name__ == "__main__":
    unittest.main()
