#!/usr/bin/env python3
"""Small, repeatable PC cache/retrieval sanity probe on disposable Git data.

Run from this checkout with:
    scripts/pc-dev python preservation/performance_sanity.py

The probe opens no service, Todo authority, model, GPU, or scheduler. It writes
only a JSON result beside this script; all cache and fixture data are temporary.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


BASE = Path(__file__).resolve().parents[1]
REPORT = Path(__file__).with_name("performance_sanity.json")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
        capture_output=True, text=True).stdout.strip()


def median(values: list[float]) -> float:
    return round(statistics.median(values), 6)


def percentile95(values: list[float]) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * .95))], 6)


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="pc-perf-sanity-") as scratch:
        temp = Path(scratch)
        # Keep all PC and source-index state private to this run.
        os.environ["XDG_CACHE_HOME"] = str(temp / "cache")
        os.environ["PROJECT_CONTROL_SOURCE_STATE_DIR"] = str(temp / "source-state")
        os.environ["PROJECT_CONTROL_AS1_STATE_DIR"] = str(temp / "packet-state")

        from project_control.adapters.cuda import CudaReadAdapter
        from project_control.adapters.git import GitReadAdapter
        from project_control.as1_context import ContextHost, InformationService
        from project_control.as1_packets import SQLitePacketStore
        from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
        from project_control.models import ProjectSnapshot
        from project_control.snapshot import SnapshotBuilder
        from project_source import SourceIndex
        import project_source

        repo = temp / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.name", "PC Perf Fixture")
        git(repo, "config", "user.email", "perf@example.invalid")
        primary = "def target(value):\n    return value + 1\n\ndef caller(value):\n    return target(value)\n"
        (repo / "app.py").write_text(primary, encoding="utf-8")
        (repo / "test_app.py").write_text(
            "from app import target\n\ndef test_target():\n    assert target(1) == 2\n", encoding="utf-8")
        for number in range(24):
            (repo / f"module_{number:02d}.py").write_text(
                f"def helper_{number}(value):\n    return value + {number}\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "deterministic context fixture")

        config = ProjectControlConfig(workspaces={"fixture": WorkspaceConfig(
            repositories={"source": RepositoryConfig(root=repo)})})
        builder = SnapshotBuilder(config)
        counters = {"git_identity": 0, "git_worktrees": 0, "cuda_status": 0}
        original_identity, original_worktrees = GitReadAdapter.identity, GitReadAdapter.worktrees
        original_cuda = CudaReadAdapter.status

        def counted_identity(adapter):
            counters["git_identity"] += 1
            return original_identity(adapter)

        def counted_worktrees(adapter):
            counters["git_worktrees"] += 1
            return original_worktrees(adapter)

        def counted_cuda(adapter, campaign=None):
            counters["cuda_status"] += 1
            return original_cuda(adapter, campaign)

        snapshot_samples = {"cache_bypassed": [], "cached": []}
        with patch.object(GitReadAdapter, "identity", counted_identity), \
             patch.object(GitReadAdapter, "worktrees", counted_worktrees), \
             patch.object(CudaReadAdapter, "status", counted_cuda):
            first = builder.build("fixture")
            for _ in range(7):
                builder.cache.clear()  # bypass result cache; retain worktree cache
                started = time.perf_counter()
                current = builder.build("fixture")
                snapshot_samples["cache_bypassed"].append(time.perf_counter() - started)
            # Last bypassed call populated the result cache for identical inputs.
            for _ in range(7):
                started = time.perf_counter()
                current = builder.build("fixture")
                snapshot_samples["cached"].append(time.perf_counter() - started)

        snapshot_same_revision = (
            first.repositories["source"].commit == current.repositories["source"].commit
            and first.todo_revision == current.todo_revision
        )

        service = InformationService(config,
            SQLitePacketStore(temp / "packet-state" / "packets"),
            lambda _project: (_ for _ in ()).throw(AssertionError("context consulted snapshot")),
            ContextHost("observer", "perf-sanity", frozenset({"fixture"})))
        update_samples: list[dict] = []
        original_update = SourceIndex.update

        def counted_update(index, paths=None):
            value = original_update(index, paths)
            update_samples.append(value)
            return value

        query = {"project": "fixture", "targets": ["source:target"],
            "include": ["implementation", "callers", "tests"], "budget_bytes": 8192}
        with patch.object(SourceIndex, "update", counted_update):
            started = time.perf_counter()
            cold = service.call("context", **query)
            cold_seconds = time.perf_counter() - started
            started = time.perf_counter()
            warm = service.call("context", **query)
            warm_seconds = time.perf_counter() - started

            # An ordinary edit should change the relevant source identity and
            # make the next packet return the edited implementation.
            edited = primary.replace("value + 1", "value + 2")
            (repo / "app.py").write_text(edited, encoding="utf-8")
            started = time.perf_counter()
            after_edit = service.call("context", **query)
            edit_seconds = time.perf_counter() - started

        def context_summary(packet):
            implementation = packet.get("data", {}).get("sections", {}).get("implementation", [])
            callers = packet.get("data", {}).get("sections", {}).get("callers", [])
            tests = packet.get("data", {}).get("sections", {}).get("tests", [])
            texts = "\n".join(str(item.get("content", "")) for item in implementation + callers + tests)
            caller_text = "\n".join(str(item.get("content", "")) for item in callers)
            locators = packet.get("sources", [])
            return {
                "status": packet.get("status"),
                "complete": packet.get("data", {}).get("complete"),
                "has_implementation": "def target" in texts,
                "has_caller_callsite": "target(value)" in caller_text,
                "has_test": "test_target" in texts,
                "source_locators": len(locators),
                "all_locators_have_sha256": bool(locators) and all(
                    len(str(item.get("content_sha256", ""))) == 64 for item in locators),
            }

        unchanged_context_repeats = (
            cold["data"].get("sections") == warm["data"].get("sections")
            and cold["sources"] == warm["sources"]
        )
        edited_digest = hashlib.sha256(edited.encode()).hexdigest()
        edit_provenance_matches = any(item.get("content_sha256") == edited_digest
            for item in after_edit.get("sources", []))
        foundation_root = next((candidate for candidate in Path(project_source.__file__).resolve().parents
            if (candidate / "packages" / "project-source").is_dir()), None)
        foundation_head = None
        foundation_status = None
        if foundation_root is not None:
            try:
                foundation_head = git(foundation_root, "rev-parse", "HEAD")
            except subprocess.CalledProcessError:
                foundation_head = "unborn-or-unavailable"
            foundation_status = subprocess.run(["git", "-C", str(foundation_root),
                "status", "--porcelain"], capture_output=True, text=True).stdout.splitlines()
        package_tree = Path(project_source.__file__).resolve().parent
        package_digest = hashlib.sha256()
        for source_file in sorted(package_tree.glob("*.py")):
            package_digest.update(source_file.name.encode())
            package_digest.update(source_file.read_bytes())
        output = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "checkout_revision": git(BASE, "rev-parse", "HEAD"),
            "checkout_tracked_changes": bool(subprocess.run(["git", "-C", str(BASE),
                "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                text=True).stdout.strip()),
            "project_source_revision": {"repository": str(foundation_root),
                "git_head": foundation_head, "git_status_lines": foundation_status,
                "project_source_module_sha256": package_digest.hexdigest()},
            "environment": {"python": sys.version.split()[0], "platform": platform.platform(),
                "machine": platform.machine(), "cpu_count": os.cpu_count(),
                "project_root": str(BASE)},
            "method": {
                "fixture": "temporary committed Git repository with 26 small Python files; no Todo authority configured",
                "snapshot_repeats": 7,
                "snapshot_cache_bypassed": "clear SnapshotBuilder result cache before each repeat; keep internal worktree cache warm",
                "snapshot_cached": "reuse the snapshot result cache for identical input in the same process",
                "timings_seconds": "wall-clock perf_counter; medians and p95 order statistic; descriptive only",
                "source_context": "one cold target-symbol context request, one identical warm request, then one request after editing target implementation",
            },
            "snapshot": {
                "samples_seconds": {key: [round(value, 6) for value in values]
                    for key, values in snapshot_samples.items()},
                "median_seconds": {key: median(values) for key, values in snapshot_samples.items()},
                "p95_seconds": {key: percentile95(values) for key, values in snapshot_samples.items()},
                "bypassed_to_cached_median_ratio": round(
                    median(snapshot_samples["cache_bypassed"]) / max(median(snapshot_samples["cached"]), 1e-9), 3),
                "provider_calls_across_all_repeats": counters,
                "same_git_and_todo_revision": snapshot_same_revision,
                "todo_provider": "not configured (fixture workspace has no authority repository)",
                "worktree_cache_note": "only SnapshotBuilder result cache was bypassed; nested worktree observations remained reusable",
            },
            "source_context": {
                "samples_seconds": {"cold": round(cold_seconds, 6), "warm": round(warm_seconds, 6),
                    "after_edit": round(edit_seconds, 6)},
                "index_update_calls": len(update_samples),
                "index_update_results": update_samples,
                "cold": context_summary(cold), "warm": context_summary(warm),
                "after_edit": context_summary(after_edit),
                "unchanged_repeat_same_sections_and_sources": unchanged_context_repeats,
                "edited_source_hash_matches_returned_locator": edit_provenance_matches,
                "requested_context_sufficient": all(
                    context_summary(packet)[key] for packet in (cold, warm, after_edit)
                    for key in ("has_implementation", "has_caller_callsite", "has_test", "all_locators_have_sha256")),
                "expected_edit_digest": edited_digest,
            },
            "limits": [
                "Synthetic small-repository sanity probe; not an end-to-end MCP latency measurement.",
                "No statistical inference, performance threshold, comprehensive benchmark, live service, Todo authority, model, GPU, or scheduler was used.",
                "Snapshot bypass retained the separate worktree-observation cache, so the result isolates snapshot memoization rather than a fully cold Git scan.",
                "Results depend on local filesystem, Python, and Git versions; this report is one environment-specific sample.",
            ],
        }
        return output


if __name__ == "__main__":
    result = run()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
