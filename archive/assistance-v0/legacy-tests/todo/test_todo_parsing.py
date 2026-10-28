from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from tests.todo.scripts.todo_common import (  # noqa: E402
    ROOT_SECTION_ORDER,
    STATUS_SECTION_ORDER,
    WORKSTREAM_SECTION_ORDER,
    append_section_bullets,
    ensure_root_files,
    ensure_workstream_file,
    load_root_doc,
    load_status_doc,
    load_workstream_doc,
    parse_frontmatter,
    parse_markdown_document,
    parse_status_entries,
    render_markdown_document,
    set_section_text,
)


class TodoParsingTests(unittest.TestCase):
    def test_render_round_trip_preserves_unknown_section(self) -> None:
        original = """# Current Objective

## Summary
Initial summary

## Custom Notes
User-owned text
"""
        doc = parse_markdown_document(original)
        rendered = render_markdown_document(doc, WORKSTREAM_SECTION_ORDER)
        self.assertIn("## Custom Notes", rendered)
        self.assertIn("User-owned text", rendered)

    def test_init_creates_root_and_workstream_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            ensure_root_files(repo_root)
            ensure_workstream_file(repo_root, "build-ledger", "Build the ledger", "planned", "planner")
            self.assertTrue((repo_root / "todos.md").exists())
            self.assertTrue((repo_root / "todo-status.md").exists())
            self.assertTrue((repo_root / "todos" / "build-ledger.md").exists())
            root_doc = load_root_doc(repo_root)
            self.assertIn("Workstreams", root_doc.sections)
            status_doc = load_status_doc(repo_root)
            entries = parse_status_entries(status_doc.sections["Workstreams"])
            self.assertEqual(entries[0]["execution"], "ready")

    def test_preserves_user_written_preamble(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            ensure_workstream_file(repo_root, "resume-flow", "Resume flow", "planned", "agent-a")
            path = repo_root / "todos" / "resume-flow.md"
            path.write_text(
                """# Current Objective

This note should stay.

## Summary
Resume flow
""",
                encoding="utf-8",
            )
            doc = load_workstream_doc(repo_root, "resume-flow", "Resume flow")
            append_section_bullets(doc, "Planning Notes", ["Record first step"])
            set_section_text(doc, "Summary", "Resume flow with preserved notes")
            path.write_text(render_markdown_document(doc, WORKSTREAM_SECTION_ORDER), encoding="utf-8")
            text = path.read_text(encoding="utf-8")
            self.assertIn("This note should stay.", text)
            self.assertIn("Resume flow with preserved notes", text)

    def test_workstream_template_includes_quick_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            ensure_workstream_file(repo_root, "pickup-ready", "Pickup ready", "planned", "agent-a")
            doc = load_workstream_doc(repo_root, "pickup-ready", "Pickup ready")
            rendered = render_markdown_document(doc, WORKSTREAM_SECTION_ORDER)
            self.assertIn("## Quick Start", rendered)
            self.assertIn("Required skills", rendered)
            self.assertIn("Required references", rendered)

    def test_ensure_root_files_backfills_status_register_from_existing_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "todos").mkdir(parents=True)
            (repo_root / "todos.md").write_text(
                """# Active Objectives

## Summary
Use this file as the canonical index for substantial multi-step work.

## Workstreams
- `legacy-stream` | status: blocked | owner: legacy | file: `todos/legacy-stream.md` | objective: Legacy stream
""",
                encoding="utf-8",
            )
            ensure_root_files(repo_root)
            status_doc = load_status_doc(repo_root)
            entries = parse_status_entries(status_doc.sections["Workstreams"])
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["slug"], "legacy-stream")
            self.assertEqual(entries[0]["status"], "blocked")
            self.assertEqual(entries[0]["execution"], "idle")

    def test_workstream_files_include_frontmatter_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            ensure_workstream_file(repo_root, "alpha-stream", "Alpha stream", "in_progress", "agent-a")
            text = (repo_root / "todos" / "alpha-stream.md").read_text(encoding="utf-8")
            frontmatter = parse_frontmatter(text)
            self.assertEqual(frontmatter["slug"], "alpha-stream")
            self.assertEqual(frontmatter["status"], "in_progress")
            self.assertEqual(frontmatter["execution"], "claimed")
            self.assertEqual(frontmatter["owner"], "agent-a")
            self.assertEqual(frontmatter["objective"], "Alpha stream")
            self.assertIn("Staleness Review", (repo_root / "todo-status.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
