from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import EvidenceInput, ProjectSnapshot, RepositoryIdentity, SourceContextInput, SourceTarget
from project_control.services.source_context import source_context
from project_control.services.evidence import evidence_for
from project_control.source_index import SourceLexicalIndex


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, text=True, capture_output=True).stdout


def manifest(root: Path) -> dict[str, str]:
    ignored = {".git", ".todo-orchestrator", ".ctxpp"}
    return {
        item.relative_to(root).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in root.rglob("*") if item.is_file() and not any(part in ignored for part in item.parts)
    }


class SourceContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp.name) / "cache"
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Tests")
        git(self.root, "config", "user.email", "tests@example.invalid")
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "docs").mkdir()
        (self.root / "src" / "module.py").write_text("def calculate_total(value):\n    return value + 1\n", encoding="utf-8")
        (self.root / "tests" / "test_module.py").write_text("from src.module import calculate_total\n", encoding="utf-8")
        (self.root / "docs" / "architecture.md").write_text(
            "calculate_total is the public calculation boundary.\n" + "".join(f"line needle {number}\n" for number in range(100)),
            encoding="utf-8",
        )
        (self.root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
        (self.root / "large.md").write_text("".join(f"line {number}\n" for number in range(300_000)), encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "fixture")
        head = git(self.root, "rev-parse", "HEAD").strip()
        self.config = ProjectControlConfig(workspaces={
            "demo": WorkspaceConfig(authority_repository="source", repositories={"source": RepositoryConfig(root=self.root)})
        })
        self.snapshot = ProjectSnapshot(
            workspace_id="demo", observed_at="2026-08-27T00:00:00Z", todo_revision=1,
            repositories={"source": RepositoryIdentity(commit=head, dirty=False)},
        )
        self.env = patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def call(self, targets, **kwargs):
        return source_context(self.config, self.snapshot, SourceContextInput(
            project="demo", repository="source", targets=targets, **kwargs,
        ))

    def test_multi_target_range_reads_large_file_and_relations(self) -> None:
        before = manifest(self.root)
        result = self.call([
            SourceTarget(kind="path", value="large.md", line_start=299_990, line_end=300_000),
            SourceTarget(kind="path", value="src/module.py"),
        ], requested_relations=["tests", "documentation", "recent_changes"], budget_bytes=64 * 1024)
        self.assertEqual("line 299989", result.data["targets"][0]["excerpt"].splitlines()[0])
        self.assertIn("calculate_total", result.data["targets"][1]["excerpt"])
        self.assertTrue(result.data["targets"][1]["tests"])
        self.assertTrue(result.data["targets"][1]["documentation"])
        self.assertEqual(before, manifest(self.root))

    def test_small_full_envelope_budget_preserves_requested_source_content(self) -> None:
        result = self.call(
            [SourceTarget(kind="path", value="src/module.py", line_start=1, line_end=2)],
            budget_bytes=4096,
        )
        encoded = result.model_dump_json().encode("utf-8")
        self.assertLessEqual(len(encoded), 4096)
        self.assertIn("calculate_total", result.data["targets"][0]["excerpt"])
        self.assertIn("identity_digest", result.cursor.model_dump(mode="json"))
        self.assertNotIn("preconditions", result.data)

    def test_symbol_falls_back_without_writing_ctxpp(self) -> None:
        result = self.call([SourceTarget(kind="symbol", value="calculate_total")])
        target = result.data["targets"][0]
        self.assertEqual("bounded_git_grep", target["source"])
        self.assertIn("semantic_context_unavailable", target["warnings"])
        self.assertFalse((self.root / ".ctxpp").exists())

    def test_context_note_descendant_anchor_is_non_authoritative_and_staleness_is_visible(self) -> None:
        self.snapshot.todo_tables["workflow_context_fragments"] = [{
            "id": "NOTE-1", "kind": "context_note", "series_key": "cuda-layout", "version": 1,
            "state": "current", "content_json": json.dumps({
                "authority": "non_authoritative_context",
                "anchors": [{"kind": "directory", "value": "src"}],
                "classification": "implementation_insight",
                "content": {"summary": "layout requires aligned blocks"},
                "source_identity": {"repository": "source", "commit": "0" * 40},
            }),
        }, {
            "id": "NOTE-OTHER", "kind": "context_note", "content_json": json.dumps({
                "anchors": [{"kind": "path", "value": "docs"}], "content": {"summary": "unrelated"},
            }),
        }]
        result = self.call([SourceTarget(kind="path", value="src/module.py")], requested_relations=["context_notes"])
        notes = result.data["targets"][0]["context_notes"]
        self.assertEqual([note["id"] for note in notes], ["NOTE-1"])
        self.assertEqual(notes[0]["authority"], "non_authoritative_context")
        self.assertEqual(notes[0]["source_freshness"], "potentially_stale")

    def test_evidence_summary_keeps_note_finding_and_marks_stale_source(self) -> None:
        self.snapshot.todo_tables["context_fragments"] = [{
            "id": "NOTE-E", "kind": "context_note", "content_json": json.dumps({
                "anchors": [{"kind": "task", "value": "T1"}], "content": {"summary": "preserve block order"},
                "source_identity": {"repository": "source", "commit": "f" * 40},
            }),
        }]
        result = evidence_for(self.config, self.snapshot, EvidenceInput(project="demo", subject="T1", kinds=["context"]))
        self.assertEqual(result.data["stale_or_historical"][0]["content"]["summary"], "preserve block order")
        self.assertEqual(result.data["stale_or_historical"][0]["authority_label"], "non_authoritative_context")
        self.assertNotIn("source_identity", result.data["stale_or_historical"][0])

    def test_relation_contract_is_explicit_when_semantic_edges_are_unavailable(self) -> None:
        self.snapshot.todo_tables = {
            "tasks": [{"id": "T", "title": "Own calculate_total"}],
            "interfaces": [{"id": "I", "name": "calculate_total"}],
        }
        self.snapshot.cuda = {"facts": [{"id": "P", "subject": "calculate_total"}], "results": []}
        result = self.call(
            [SourceTarget(kind="symbol", value="calculate_total")],
            requested_relations=["definitions", "references", "callers", "callees", "task_ownership", "interfaces", "performance_evidence"],
        )
        target = result.data["targets"][0]
        self.assertTrue(target["definitions"])
        self.assertTrue(target["references"])
        self.assertEqual("unavailable", target["callers"]["status"])
        self.assertEqual("T", target["task_ownership"][0]["id"])
        self.assertEqual("I", target["interfaces"][0]["id"])
        self.assertEqual("P", target["performance_evidence"][0]["id"])

    def test_racy_file_read_retries_once_then_reports_race(self) -> None:
        with patch("project_control.services.source_context._file_identity", side_effect=[
            (1, 1, 1, "1"), (1, 1, 2, "2"), (1, 1, 2, "2"), (1, 1, 3, "3"),
        ]):
            result = self.call([SourceTarget(kind="path", value="src/module.py")])
        self.assertIn("racy_source_read", result.warnings)
        self.assertEqual("raced", result.data["targets"][0]["status"])

    def test_private_lexical_index_is_outside_repository_and_redacts_secrets(self) -> None:
        (self.root / "docs" / "notes.md").write_text("needle bearer abcdefghijklmnop\n", encoding="utf-8")
        git(self.root, "add", "docs/notes.md")
        result = self.call([SourceTarget(kind="text", value="needle")])
        matches = result.data["targets"][0]["matches"]
        self.assertTrue(matches)
        self.assertIn("[REDACTED]", matches[0]["excerpt"])
        self.assertTrue(any(self.cache.rglob("index.sqlite3")))
        self.assertFalse(any(self.root.rglob("index.sqlite3")))

    def test_ordinary_search_prefers_current_source_and_subsystem_exposes_shape(self) -> None:
        (self.root / "archive").mkdir()
        (self.root / "archive" / "wf2-plan.md").write_text("calculate_total planning history\n", encoding="utf-8")
        git(self.root, "add", "archive/wf2-plan.md")
        git(self.root, "commit", "-m", "archive fixture")
        result = self.call([
            SourceTarget(kind="text", value="calculate_total"),
            SourceTarget(kind="subsystem", value="calculate_total"),
        ])
        text, subsystem = result.data["targets"]
        self.assertEqual("src/module.py", text["matches"][0]["path"])
        self.assertEqual("src/module.py", subsystem["structure"]["files"][0]["path"])
        self.assertIn("src", [item["path"] for item in subsystem["structure"]["directories"]])

    def test_commit_selector_ignores_dirty_worktree(self) -> None:
        head = git(self.root, "rev-parse", "HEAD").strip()
        (self.root / "src" / "module.py").write_text("DIRTY = True\n", encoding="utf-8")
        result = self.call([SourceTarget(kind="path", value="src/module.py")], source_selector=head)
        self.assertIn("calculate_total", result.data["targets"][0]["excerpt"])
        self.assertNotIn("DIRTY", result.data["targets"][0]["excerpt"])
        self.assertEqual("immutable_commit", result.data["source_freshness"])

    def test_commit_selector_keeps_searches_and_relations_on_git_objects(self) -> None:
        head = git(self.root, "rev-parse", "HEAD").strip()
        (self.root / "src" / "module.py").write_text("DIRTY_SYMBOL = True\n", encoding="utf-8")
        (self.root / "tests" / "test_module.py").write_text("DIRTY_SYMBOL\n", encoding="utf-8")
        (self.root / "docs" / "architecture.md").write_text("DIRTY_SYMBOL\n", encoding="utf-8")
        (self.root / "pyproject.toml").write_text("DIRTY_SYMBOL\n", encoding="utf-8")
        result = self.call(
            [
                SourceTarget(kind="symbol", value="calculate_total"),
                SourceTarget(kind="text", value="calculate_total"),
                SourceTarget(kind="subsystem", value="calculate_total"),
                SourceTarget(kind="path", value="src/module.py"),
            ],
            source_selector=head,
            requested_relations=["tests", "documentation", "build_config_references"],
        )
        symbol, text, subsystem, path = result.data["targets"]
        for item in (symbol, text, subsystem):
            self.assertEqual("bounded_git_grep", item["source"])
            self.assertEqual("immutable_commit", item["freshness"])
            self.assertTrue(item["matches"])
            self.assertNotIn("DIRTY_SYMBOL", json.dumps(item))
        self.assertIn("calculate_total", path["excerpt"])
        self.assertNotIn("DIRTY_SYMBOL", json.dumps(path["tests"]))
        self.assertNotIn("DIRTY_SYMBOL", json.dumps(path["documentation"]))
        self.assertNotIn("DIRTY_SYMBOL", json.dumps(path["build_config_references"]))

    def test_working_tree_reads_explicit_live_link_without_copying(self) -> None:
        live = Path(self.temp.name) / "live-config.toml"
        live.write_text("model = 'first'\n", encoding="utf-8")
        (self.root / "live-config.toml").symlink_to(live)
        git(self.root, "add", "live-config.toml")
        git(self.root, "commit", "-m", "add live link")
        self.config.workspaces["demo"].repositories["source"].live_links = {
            "live-config.toml": live,
        }
        first = self.call([SourceTarget(kind="path", value="live-config.toml")])
        self.assertIn("first", first.data["targets"][0]["excerpt"])
        live.write_text("model = 'second'\n", encoding="utf-8")
        second = self.call([SourceTarget(kind="path", value="live-config.toml")])
        self.assertIn("second", second.data["targets"][0]["excerpt"])

    def test_workflow_workspace_maps_to_redacted_worktree_identity(self) -> None:
        self.snapshot.todo_workflow = {"runs": [{
            "id": "RUN", "lanes": [{
                "id": "LANE", "queue": [{"task_id": "TASK"}],
                "workspace": {"id": "WS", "worktree_path": str(self.root), "mode": "isolated_merge"},
            }],
        }]}
        result = self.call([SourceTarget(kind="path", value="src/module.py")])
        self.assertEqual("LANE", result.data["workflow_mapping"][0]["lane_id"])
        self.assertNotIn(str(self.root), json.dumps(result.model_dump(mode="json")))

    def test_denied_absolute_and_unknown_worktree_are_rejected_without_paths(self) -> None:
        result = self.call([SourceTarget(kind="path", value="/etc/passwd")])
        self.assertIn("source_target_unavailable", result.warnings)
        self.assertNotIn(str(self.root), json.dumps(result.model_dump(mode="json")))
        bad = self.call([SourceTarget(kind="path", value="src/module.py")], worktree_id="wt-unrelated")
        self.assertIn("unknown_or_unrelated_worktree", bad.warnings)

    def test_lexical_continuation_is_deterministic_and_stale_cursor_is_rejected(self) -> None:
        index = SourceLexicalIndex("repo", "identity")
        index.build(self.root, ["docs/architecture.md"], [])
        self.assertEqual(index.search("calculation"), index.search("calculation"))
        first = self.call([SourceTarget(kind="text", value="line")], budget_bytes=1024)
        cursor = first.data["continuation_cursor"]
        self.assertIsNotNone(cursor)
        second = self.call([SourceTarget(kind="text", value="line")], budget_bytes=1024, continuation_cursor=cursor)
        self.assertNotEqual(first.data["targets"][0]["matches"], second.data["targets"][0]["matches"])
        with self.assertRaisesRegex(ValueError, "invalid_or_stale"):
            self.call([SourceTarget(kind="text", value="different")], continuation_cursor=cursor)


if __name__ == "__main__":
    unittest.main()
