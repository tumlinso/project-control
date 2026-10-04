from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control.app import create_mcp
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.snapshot import SnapshotBuilder


TODO = Path("/home/tumlinson/.agents/skills/todo-orchestrator/scripts/todo.py")
SKILLS = Path("/home/tumlinson/.agents/skills")
TOOLS = ('overview', 'delta', 'frontier', 'search', 'evidence', 'history', 'impact', 'read')


def run(argv: list[str], root: Path) -> str:
    return subprocess.run(argv, cwd=root, check=True, text=True, capture_output=True).stdout


def manifest(root: Path) -> dict[str, tuple[int, str]]:
    ignored = {".git", ".todo-orchestrator", ".ctxpp", "todos", "__pycache__"}
    result = {}
    for path in root.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        if relative in {"todos.md", "todo-status.md"}:
            continue
        payload = path.read_bytes()
        result[relative] = (len(payload), hashlib.sha256(payload).hexdigest())
    return result


@unittest.skipUnless(TODO.is_file(), "local todo-orchestrator integration unavailable")
class ReadOnlyAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "disposable"
        self.root.mkdir()
        run(["git", "init", "-b", "main"], self.root)
        run(["git", "config", "user.email", "tests@example.invalid"], self.root)
        run(["git", "config", "user.name", "Tests"], self.root)
        (self.root / "README.md").write_text("# disposable\n", encoding="utf-8")
        (self.root / "source.cc").write_text("int safe_symbol() { return 1; }\n", encoding="utf-8")
        (self.root / ".gitignore").write_text(".todo-orchestrator/\ntodos/\ntodos.md\ntodo-status.md\n", encoding="utf-8")
        run(["git", "add", "."], self.root)
        run(["git", "commit", "-m", "fixture"], self.root)
        run(["python", str(TODO), "bootstrap", "--repo-root", ".", "--name", "Disposable", "--json"], self.root)
        self.proposal = {
            "schema_version": 2,
            "project": {"name": "Disposable"},
            "invariants": [], "locks": [], "interfaces": [],
            "tasks": [{
                "id": "D-01", "kind": "workstream", "title": "Disposable task",
                "objective": "Prove read-only observation.", "priority": 1,
                "parallel_policy": "serial", "scope": {"exclusive_paths": ["source.cc"]},
            }],
        }
        plan = Path(self.temporary.name) / "plan.json"
        plan.write_text(json.dumps(self.proposal), encoding="utf-8")
        run(["python", str(TODO), "plan", "validate", "--file", str(plan), "--repo-root", ".", "--json"], self.root)
        run(["python", str(TODO), "plan", "apply", "--file", str(plan), "--repo-root", ".", "--json"], self.root)
        self.config = ProjectControlConfig(skills_root=SKILLS, workspaces={
            "disposable": WorkspaceConfig(authority_repository="source", repositories={"source": RepositoryConfig(root=self.root)})
        })
        # This disposable audit deliberately exercises the compatibility CLI
        # read provider; the gate parent retains its deployed runtime binding.
        with patch('project_control.app.todo_read_port_factory', return_value=None):
            self.mcp = create_mcp(self.config)


    def tearDown(self) -> None:
        self.mcp._project_control_surface.close()
        self.temporary.cleanup()

    def sentinel(self):
        status = json.loads(run(["python", str(TODO), "status", "--repo-root", ".", "--json"], self.root))
        workers = subprocess.run(["pgrep", "-af", "local_worker.*supervisor"], text=True, capture_output=True).stdout
        cuda_files = {
            path.relative_to(self.root).as_posix(): path.stat().st_mtime_ns
            for path in self.root.rglob("*cuda*") if path.is_file()
        }
        return {
            "head": run(["git", "rev-parse", "HEAD"], self.root),
            "status": run(["git", "status", "--porcelain=v2"], self.root),
            "todo_revision": status["data"]["project_revision"],
            "files": manifest(self.root),
            "workers": workers,
            "cuda": cuda_files,
        }

    async def call_all(self):
        revision = json.loads(run(["python", str(TODO), "status", "--repo-root", ".", "--json"], self.root))["data"]["project_revision"]
        commit = run(["git", "rev-parse", "HEAD"], self.root).strip()
        calls = {
            'overview': {'project': 'disposable', 'detail': 'standard'},
            'delta': {'project': 'disposable', 'since': {'todo_revision': revision, 'commits': {'source': commit}}},
            'frontier': {'project': 'disposable'},
            'search': {'project': 'disposable', 'query': {'kind': 'task', 'target': 'D-01'}},
            'evidence': {'project': 'disposable', 'subject': 'D-01'},
            'history': {'project': 'disposable', 'subject': 'D-01'},
            'impact': {'project': 'disposable', 'targets': [{'repository': 'source', 'path': 'source.cc'}]},
            'read': {'project': 'disposable', 'paths': ['source.cc']},
        }
        return {name: await self.mcp.call_tool(name, arguments) for name, arguments in calls.items()}

    def test_every_tool_preserves_all_authoritative_sentinels(self) -> None:
        before = self.sentinel()
        results = asyncio.run(self.call_all())
        after = self.sentinel()
        self.assertEqual(before, after)
        self.assertEqual(set(results), set(TOOLS))
        self.assertFalse((self.root / ".ctxpp").exists())
        serialized = json.dumps(results, default=lambda value: value.model_dump(mode="json") if hasattr(value, "model_dump") else str(value))
        for forbidden in ("toc_", "tos_", "tol_", "gpu_uuid", "raw_log", "stdout", "stderr"):
            self.assertNotIn(forbidden, serialized.lower())

    def test_snapshot_populates_semantic_fingerprint_precondition(self) -> None:
        snapshot = SnapshotBuilder(self.config).build("disposable")
        semantic = snapshot.component_authority["todo_semantic_state"]
        self.assertIsNotNone(semantic.read_authority_fingerprint)
        self.assertEqual(
            snapshot.observation_preconditions().todo_semantic_authority_fingerprint,
            semantic.read_authority_fingerprint,
        )
        self.assertEqual(snapshot.cursor().todo_semantic_fingerprint, semantic.read_authority_fingerprint)

    def test_concurrent_overviews_are_bounded_and_non_mutating(self) -> None:
        before = self.sentinel()

        async def calls():
            return await asyncio.gather(*[
                self.mcp.call_tool("overview", {"project": "disposable", "detail": "compact"})
                for _ in range(16)
            ])

        results = asyncio.run(calls())
        self.assertEqual(before, self.sentinel())
        for result in results:
            payload = json.dumps(result, default=lambda value: value.model_dump(mode="json") if hasattr(value, "model_dump") else str(value)).encode()
            self.assertLessEqual(len(payload), 6000)

    def test_arbitrary_secret_binary_oversized_and_symlink_paths_are_rejected(self) -> None:
        (self.root / ".env").write_text("TOKEN=hidden", encoding="utf-8")
        (self.root / "binary.dat").write_bytes(b"a\x00b")
        (self.root / "oversized.txt").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        outside = Path(self.temporary.name) / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        (self.root / "escape.txt").symlink_to(outside)

        async def inspect_path(path: str):
            return await self.mcp.call_tool("read", {"project": "disposable", "paths": [path], "repository": "source"})

        for target in ("/etc/passwd", ".env", "binary.dat", "oversized.txt", "escape.txt"):
            result = asyncio.run(inspect_path(target))
            payload = json.dumps(result, default=str)
            self.assertNotIn("outside", payload)
            self.assertNotIn("TOKEN=hidden", payload)
            structured = result[1] if isinstance(result, tuple) else result
            self.assertEqual(structured['status'], 'partial')
            if target == 'oversized.txt':
                self.assertIn('continuation', structured['data'])
                self.assertGreater(structured['data']['needed_bytes'], 2 * 1024 * 1024)
                self.assertFalse(structured['coverage']['complete'])
            else:
                self.assertEqual(structured['data']['files'][0]['status'], 'error')
                self.assertNotIn('content', structured['data']['files'][0])


if __name__ == "__main__":
    unittest.main()
