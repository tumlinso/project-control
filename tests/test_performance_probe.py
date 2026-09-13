from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import asyncio

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import PerformanceProbeInput, ToolStatus
from project_control.services.performance_probe import performance_probe
from project_control.snapshot import SnapshotBuilder
from project_control.app import create_mcp


class _Result:
    returncode = 0

    @staticmethod
    def json() -> dict:
        return {
            "status": "ok", "campaign": "demo", "mode": "benchmark",
            "effective_parameters": {"size": 128},
            "probe": {
                "source": {"commit": "b" * 40, "fingerprint": "c" * 64},
                "binary": {"name": "demo", "sha256": "a" * 64},
                "placement": {"gpu_uuids": ["GPU-b", "GPU-d"], "nvlink_domains": ["pair-b"]},
                "statistics": {"median": 1.2, "mad": 0.1, "samples": 5},
                "inputs": [{"parameter": "dataset", "source": "fixture", "reference": "small", "sha256": "d" * 64}],
                "quiescence": {"status": "clean", "contaminated": False},
                "artifacts": [{"id": "artifact-1", "path": "/must/not/escape"}],
            },
            "evidence_id": "perf-1", "artifact_ids": ["artifact-1"], "elapsed_ms": 12,
            "argv": ["must", "not", "escape"], "environment": {"must": "not escape"},
        }


class _Runner:
    def __init__(self, result=None) -> None:
        self.argv: list[str] | None = None
        self.env: dict[str, str] | None = None
        self.result = result or _Result()

    def run(self, argv, *, cwd, timeout, env, input_text, check):
        self.argv = list(argv)
        self.env = dict(env)
        self.cwd = cwd
        self.check = check
        self.input_text = input_text
        return self.result


class PerformanceProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Tests"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.root, check=True, capture_output=True)
        self.skills = Path(self.temporary.name) / "skills"
        controller = self.skills / "cuda" / "scripts" / "cuda_controller.py"
        controller.parent.mkdir(parents=True)
        controller.write_text("# fixture\n", encoding="utf-8")
        (self.root / "cuda-benchmarks.json").write_text('{"campaigns": []}\n', encoding="utf-8")
        self.config = ProjectControlConfig(skills_root=self.skills, workspaces={
            "demo": WorkspaceConfig(authority_repository="source", repositories={"source": RepositoryConfig(root=self.root)})
        })

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_fixed_registered_probe_compacts_result_and_preserves_worktree(self) -> None:
        snapshot = SnapshotBuilder(self.config).build("demo")
        runner = _Runner()
        result = performance_probe(
            self.config, PerformanceProbeInput(project="demo", campaign="demo", parameters={"size": 128}),
            snapshot=snapshot, snapshot_getter=lambda: SnapshotBuilder(self.config).build("demo"), runner=runner,
            skills_root=self.skills,
        )
        import sys
        self.assertEqual(runner.argv, [sys.executable, str(self.skills / "cuda" / "scripts" / "cuda_controller.py"), "probe", "--spec", "-", "--json"])
        self.assertIn('"rebuild":false', runner.input_text)
        self.assertNotIn("must", str(result.data))
        self.assertEqual(result.data["placement"]["gpu_uuids"], ["GPU-b", "GPU-d"])
        self.assertEqual(result.data["inputs"][0]["reference"], "small")
        self.assertEqual(result.data["statistics"]["median"], 1.2)
        self.assertTrue(result.data["project_worktree_unchanged"])
        self.assertFalse(runner.check)
        self.assertIn("PROJECT_CONTROL_PERFORMANCE_PROBE_STATE_DIR", runner.env)

    def test_nested_or_unbounded_parameters_are_rejected_before_adapter(self) -> None:
        with self.assertRaisesRegex(ValueError, "scalar"):
            PerformanceProbeInput(project="demo", campaign="demo", parameters={"size": {"bad": 1}})
        with self.assertRaisesRegex(ValueError, "32"):
            PerformanceProbeInput(project="demo", campaign="demo", parameters={str(i): i for i in range(33)})

    def test_read_only_observer_can_decline_explicit_rebuild(self) -> None:
        snapshot = SnapshotBuilder(self.config).build("demo")
        result = performance_probe(
            self.config, PerformanceProbeInput(project="demo", campaign="demo", rebuild=True),
            snapshot=snapshot, snapshot_getter=lambda: snapshot, runner=_Runner(),
            skills_root=self.skills, allow_rebuild=False,
        )
        self.assertEqual(result.status, ToolStatus.UNAVAILABLE)
        self.assertEqual(result.data["code"], "probe_rebuild_requires_root_profile")

    def test_contention_is_typed_unavailable_and_worktree_change_invalidates(self) -> None:
        class Unavailable:
            returncode = 1
            @staticmethod
            def json():
                return {"ok": False, "code": "probe_resource_unavailable", "reason": "occupied"}

        snapshot = SnapshotBuilder(self.config).build("demo")
        result = performance_probe(
            self.config, PerformanceProbeInput(project="demo", campaign="demo"),
            snapshot=snapshot, snapshot_getter=lambda: SnapshotBuilder(self.config).build("demo"),
            runner=_Runner(Unavailable()), skills_root=self.skills,
        )
        self.assertEqual(result.status, ToolStatus.UNAVAILABLE)
        self.assertEqual(result.data["code"], "probe_resource_unavailable")

        class MutatingRunner(_Runner):
            def run(inner_self, *args, **kwargs):
                (self.root / "README.md").write_text("changed\n", encoding="utf-8")
                return super().run(*args, **kwargs)

        result = performance_probe(
            self.config, PerformanceProbeInput(project="demo", campaign="demo"),
            snapshot=snapshot, snapshot_getter=lambda: SnapshotBuilder(self.config).build("demo"),
            runner=MutatingRunner(), skills_root=self.skills,
        )
        self.assertEqual(result.status, ToolStatus.INTERNAL_ERROR)
        self.assertFalse(result.data["valid"])
        self.assertFalse(result.data["project_worktree_unchanged"])

    def test_skills_probe_evidence_keeps_identity_statistics_without_artifact_paths(self) -> None:
        from project_control.services.performance_probe import _compact_probe_result
        compact = _compact_probe_result({"status": "ok", "probe": {
            "registry": {"id": "demo", "digest": "r" * 64, "path": "/private/registry"},
            "placement": {"gpu_uuids": ["GPU-b"], "topology": "NV2"},
            "source": {"commit": "a" * 40}, "binary": {"sha256": "b" * 64, "path": "/private/bin"},
            "inputs": [{"parameter": "dataset", "source": "fixture", "reference": "tiny", "sha256": "c" * 64, "path": "/private/data"}],
            "statistics": {"median": 1.0, "unit": "ms"}, "comparable": True,
            "artifacts": [{"id": "probe-1", "path": "/private/result.json"}],
        }})
        self.assertEqual(compact["statistics"]["median"], 1.0)
        self.assertEqual(compact["inputs"][0]["reference"], "tiny")
        self.assertEqual(compact["artifact_ids"], ["probe-1"])
        self.assertNotIn("/private", str(compact))

    def test_probe_is_explicit_observer_root_tool_not_investigator_protocol(self) -> None:
        with patch("project_control.app.todo_read_port_factory", return_value=lambda _root: None):
            tools = {tool.name: tool for tool in asyncio.run(create_mcp(self.config, profile="observer").list_tools())}
        self.assertIn("performance_probe", tools)
        self.assertFalse(tools["performance_probe"].annotations.readOnlyHint)
        from project_control.services.local_investigate import PROTOCOL
        self.assertNotIn("performance_probe", PROTOCOL)
