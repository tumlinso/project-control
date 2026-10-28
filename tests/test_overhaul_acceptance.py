"""Black-box acceptance checks for the deterministic Project Control surface.

These tests intentionally use throwaway repositories and an in-memory MCP
catalog.  They do not initialize Todo, inference, scheduler, or a live server.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from project_control.as1_context import ContextHost, InformationService
from project_control.as1_packets import SQLitePacketStore
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import ProjectSnapshot
from project_control.as1_surface import register_surface
from project_control.profiles import (
    CODEX_TOOL_NAMES,
    MCPProfile,
    MUTATOR_TOOL_NAMES,
    OBSERVER_TOOL_NAMES,
    ProfiledFastMCP,
)
from project_control.workflow_tools import register_workflow_tools


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _repo(root, files):
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Acceptance Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    for path, content in files.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    return root


def _service(tmp_path, monkeypatch, *, repos=None, profile="observer", snapshots=None):
    repos = repos or {"source": _repo(tmp_path / "source", {
        "app.py": "def total(value):\n    return value + 1\n\ndef caller(value):\n    return total(value)\n",
        "test_app.py": "from app import total\n\ndef test_total():\n    assert total(1) == 2\n",
        "README.md": "See guide repository guide:setup for instructions.\n",
    })}
    monkeypatch.setenv("PROJECT_CONTROL_SOURCE_STATE_DIR", str(tmp_path / "private-cache"))
    config = ProjectControlConfig(workspaces={"demo": WorkspaceConfig(
        repositories={alias: RepositoryConfig(root=root) for alias, root in repos.items()})})
    def no_snapshot(project):
        if snapshots is not None:
            snapshots.append(project)
        raise AssertionError("source retrieval consulted the Todo snapshot provider")
    return InformationService(config, SQLitePacketStore(tmp_path / "private-packets"),
        no_snapshot, ContextHost(profile, "acceptance", frozenset({"demo"})))


def test_public_profile_catalogs_have_fixed_tool_sets_and_no_detail_selector():
    class Info:
        def call(self, *_args, **_kwargs):
            return {}

    # Registration exposes the production function schemas without opening a
    # transport or constructing an authority/runtime.
    for profile, expected in ((MCPProfile.OBSERVER, OBSERVER_TOOL_NAMES),
                              (MCPProfile.CODEX, CODEX_TOOL_NAMES),
                              (MCPProfile.MUTATOR, MUTATOR_TOOL_NAMES)):
        server = ProfiledFastMCP("acceptance", profile=profile)
        register_surface(server, SimpleNamespace(information=Info(), control=object()))
        class WorkflowStub:
            def next_task(self, **_kwargs): return {}
            def inspect_task(self, **_kwargs): return {}
            def coordinate_task(self, **_kwargs): return {}
            def finish_task(self, **_kwargs): return {}
        register_workflow_tools(server, WorkflowStub())
        tools = asyncio.run(server.list_tools())
        assert {tool.name for tool in tools} == set(expected)
        assert len(tools) == {"observer": 10, "codex": 12, "mutator": 15}[profile.value]
        assert not {"history", "impact", "investigate", "skill"} & {tool.name for tool in tools}
        for tool in tools:
            assert "detail" not in tool.inputSchema.get("properties", {})


def test_source_only_surface_returns_hashes_and_context_without_todo(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo", {
        "app.py": "def total(value):\n    return value + 1\n\ndef caller(value):\n    return total(value)\n",
        "test_app.py": "from app import total\n\ndef test_total():\n    assert total(1) == 2\n",
    })
    service = _service(tmp_path, monkeypatch, repos={"implementation": repo})

    resolved = service.call("search", project="demo",
        query={"kind": "symbol", "target": "total"})
    assert resolved["data"]["resolution"] == "resolved"
    assert resolved["data"]["matches"][0]["path"] == "app.py"
    assert resolved["data"]["matches"][0]["content_sha256"]

    packet = service.call("context", project="demo", targets=["implementation:total"],
        include=["implementation", "callers", "tests"], budget_bytes=8192)
    assert packet["status"] == "ok"
    assert packet["data"]["complete"] is True
    assert packet["sources"]
    assert all(len(source["content_sha256"]) == 64 for source in packet["sources"])
    assembled = str(packet["data"]["sections"])
    assert "def total" in assembled and "test_total" in assembled

    tree = service.call("inspect", project="demo", operation="tree")
    assert tree["data"]["repositories"]["implementation"]["complete"] is True
    read = service.call("read", project="demo", repository="implementation", paths=["app.py"])
    assert "def caller" in read["data"]["files"][0]["content"]
    assert read["sources"][0]["repository"] == "implementation"
    by_registered_root = service.call("read", project="demo", repository=str(repo), paths=["app.py"])
    assert by_registered_root["sources"][0]["repository"] == "implementation"


def test_source_foundations_inherit_project_deny_policy_and_filter_cached_rows(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo", {
        ".env": "API_TOKEN=tracked-secret\n",
        "hidden.py": "def hidden(): return 'workspace-private-marker'\n",
        "public.py": "def public(): return hidden()\n",
    })
    # Seed the shared source cache as if an earlier consumer indexed without
    # policy. Project Control must pass its existing policy into the new source
    # boundary and cannot reuse those rows in public retrieval.
    monkeypatch.setenv("PROJECT_CONTROL_SOURCE_STATE_DIR", str(tmp_path / "private-cache"))
    config = ProjectControlConfig(workspaces={"demo": WorkspaceConfig(
        repositories={"source": RepositoryConfig(root=repo)},
        deny_patterns=["hidden.py"],
    )})
    from project_source import Repository as SourceRepository, SourceIndex
    SourceIndex(SourceRepository(repo, "demo/source", tmp_path / "private-cache" / "demo" / "source")).update()
    service = InformationService(config, SQLitePacketStore(tmp_path / "private-packets"),
        lambda _project: (_ for _ in ()).throw(AssertionError("source retrieval consulted Todo")),
        ContextHost("observer", "acceptance", frozenset({"demo"})))

    index = service._source_index("demo", "source")
    assert ".env" in index.repository.deny_patterns
    assert "hidden.py" in index.repository.deny_patterns
    assert index.tree()["paths"] == ["public.py"]
    assert "tracked-secret" not in str(index.search("API_TOKEN"))
    assert "workspace-private-marker" not in str(index.context(["public.py"],
        include=("implementation", "callers", "tests")))
    with pytest.raises(FileNotFoundError):
        index.read(".env")
    with pytest.raises(FileNotFoundError):
        index.read("hidden.py")


def test_observer_bundle_local_guidance_and_budget_continuation(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo", {
        "module.py": "def target():\n    return '" + ("x" * 9000) + "'\n",
        "guide.md": "# Guide\n" + ("A useful instruction paragraph.\n" * 100),
    })
    observer = _service(tmp_path / "observer", monkeypatch, repos={"repo": repo}, profile="observer")
    local = _service(tmp_path / "local", monkeypatch, repos={"repo": repo}, profile="codex")
    rich = observer.call("context", project="demo", targets=["repo:module.py"],
        include=["implementation"], budget_bytes=20000)
    dense = local.call("context", project="demo", targets=["repo:module.py"],
        include=["implementation"], budget_bytes=20000)
    assert rich["data"]["sections"]
    assert "sections" in dense["data"], dense["data"]
    assert dense["data"]["sections"]
    assert rich["sources"] == dense["sources"]
    assert rich["data"]["complete"] is True
    assert dense["data"]["complete"] is False
    assert dense["data"]["omissions"]

    # Explicit byte limits must report truncation and a usable continuation when
    # the selected source is larger than the first response page.
    paged = observer.call("context", project="demo", targets=["repo:guide.md"],
        include=["implementation"], budget_bytes=120)
    if not paged["data"]["complete"]:
        assert paged["data"]["omissions"]
        assert paged["data"]["cursor"] or paged.get("continuation")


def test_narrow_read_is_snapshot_free_and_tracks_current_bytes(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo", {"src.py": "value = 1\n"})
    snapshot_calls = []
    service = _service(tmp_path, monkeypatch, repos={"repo": repo}, snapshots=snapshot_calls)
    first = service.call("read", project="demo", repository="repo", paths=["src.py"])
    assert first["status"] == "ok"
    assert snapshot_calls == []
    source = repo / "src.py"
    source.write_text("value = 2\n", encoding="utf-8")
    second = service.call("read", project="demo", repository="repo", paths=["src.py"])
    assert second["data"]["files"][0]["content"] == "value = 2\n"
    assert second["sources"][0]["content_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert snapshot_calls == []


def test_explicit_cross_repository_guidance_is_distinguished_from_mentions(tmp_path, monkeypatch):
    source = _repo(tmp_path / "source", {
        ".project-context.json": '{"guidance":[{"target":"guide:setup.md","name":"Build guide"}]}\n',
        "README.md": "The guide:incidental string is an ordinary mention.\n",
    })
    guide = _repo(tmp_path / "guide", {"setup.md": "# Build setup\nUse the documented build command.\n"})
    service = _service(tmp_path, monkeypatch, repos={"source": source, "guide": guide})
    response = service.call("context", project="demo", targets=["source:README.md"],
        include=["guidance"], budget_bytes=4096)
    assert response["status"] == "ok"
    guidance = response["data"]["sections"]["guidance"]
    declared = next(row for row in guidance
        if row.get("object") == "guide:setup.md" and row.get("basis") == "guidance")
    assert all(row.get("object") != "guide:incidental" for row in guidance)
    assert "Use the documented build command." in str(declared["target_context"])
    guide_locator = next(source for source in response["sources"]
        if source["repository"] == "guide" and source["path"] == "setup.md")
    assert guide_locator["content_sha256"] == hashlib.sha256((guide / "setup.md").read_bytes()).hexdigest()


def test_delta_history_mode_preserves_subject_bounded_authority_history(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo", {"src.py": "value = 1\n"})
    snapshot = ProjectSnapshot(workspace_id="demo", observed_at="2026-10-27T00:00:00Z",
        todo_revision=7, repositories={}, todo_tables={"events": [{"revision": 6,
            "timestamp": "2026-10-26T10:00:00Z", "event_type": "task.updated",
            "entity_type": "task", "entity_id": "TASK-1", "summary": "Updated task"}]})
    config = ProjectControlConfig(workspaces={"demo": WorkspaceConfig(
        repositories={"implementation": RepositoryConfig(root=repo)})})
    service = InformationService(config, SQLitePacketStore(tmp_path / "packets"),
        lambda project: snapshot, ContextHost("observer", "acceptance", frozenset({"demo"})))

    response = service.call("delta", project="demo", mode="history", subject="TASK-1",
        from_revision=5, to_revision=7)

    assert response["status"] == "partial"
    assert response["data"]["subject"] == "TASK-1"
    assert response["data"]["subject"] == "TASK-1"
    assert [event["entity_id"] for event in response["data"]["events"]] == ["TASK-1"]


def test_cpp_export_provider_is_used_by_normal_context_composition(tmp_path, monkeypatch):
    content = "namespace demo { struct PackingPlan { void freeze(); }; }\n"
    repo = _repo(tmp_path / "cpp", {"src/plan.cpp": content})
    digest = hashlib.sha256(content.encode()).hexdigest()
    export = tmp_path / "ctxpp-export.json"
    export.write_text(json.dumps({"format": "CTXPP-EXPORT/1", "root": str(repo),
        "index_integrity": True, "freshness": {"status": "fresh"}, "index_meta": {},
        "records": [
            {"record": "file", "path": "src/plan.cpp", "hash": digest, "parse_status": "ok"},
            {"record": "symbol", "file": "src/plan.cpp", "hash": digest,
             "name": "freeze", "qualified_name": "demo::PackingPlan::freeze",
             "definition": True, "line": 1, "end_line": 1, "id": "freeze-id"},
        ]}), encoding="utf-8")
    monkeypatch.setenv("PROJECT_CONTROL_CPP_EXPORTS", json.dumps({"demo/source": str(export)}))
    service = _service(tmp_path, monkeypatch, repos={"source": repo})

    result = service.call("context", project="demo", targets=[{
        "kind": "symbol", "target": "demo::PackingPlan::freeze", "repository": "source"}],
        include=["implementation"], budget_bytes=8192)

    assert result["data"]["sections"]["implementation"]
    symbol = service.call("search", project="demo", query={"kind": "symbol",
        "target": "demo::PackingPlan::freeze"}, repository="source")
    assert symbol["data"]["matches"][0]["provider"] == "cpp-context-compiler"
    assert result["sources"][0]["content_sha256"] == digest
