from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.native_workflow import NativeWorkflowHost
from project_control.profiles import MCPProfile, ProfiledFastMCP, WORKFLOW_TOOL_NAMES
from project_control.workflow_tools import register_workflow_tools


class _ReadSession:
    def __init__(self):
        self.history_calls = []
        self.current_calls = []
        self.context_calls = []
        self.closed = False

    def search_legacy_history(self, query, *, import_id=None, limit=100, after=None):
        self.history_calls.append((query, import_id, limit, after))
        return SimpleNamespace(to_dict=lambda: {"kind": "legacy-history-page", "records": [], "next_cursor": None})

    def read_legacy_current_tasks(self, *, import_id=None, limit=100, after=None):
        self.current_calls.append((import_id, limit, after))
        return SimpleNamespace(to_dict=lambda: {"kind": "legacy-current-task-page", "records": [], "next_cursor": None})

    def read_legacy_project_context(self, *, import_id=None, limit=100, after=None):
        self.context_calls.append((import_id, limit, after))
        return SimpleNamespace(to_dict=lambda: {"kind": "legacy-project-context-page", "records": [], "next_cursor": None})

    def close(self):
        self.closed = True


def test_native_paged_migration_reads_use_public_api_and_registered_repository(tmp_path: Path):
    primary, secondary = tmp_path / "primary", tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    config = ProjectControlConfig(workspaces={
        "workspace": WorkspaceConfig(
            authority_repository="primary",
            repositories={
                "primary": RepositoryConfig(root=primary),
                "secondary": RepositoryConfig(root=secondary),
            },
        ),
    })
    session = _ReadSession()
    selected_repositories = []
    host = NativeWorkflowHost(
        config,
        SimpleNamespace(profile="mutator", principal="host-principal", projects=frozenset({"workspace"})),
        session_for_host=lambda _root, **_kwargs: session,
        providers_for_project=lambda _config, _project, _root, **kwargs: selected_repositories.append(
            kwargs["repository"]
        ),
        binding_validator=lambda: None,
    )

    history_cursor = {"source_revision": 12, "occurred_at": "2026-10-19T00:00:00Z", "history_id": "h-12"}
    history = host.invoke(
        "todo_search_legacy_history", project="workspace", repository="secondary",
        query="", import_id="legacy-import", limit=17, after=history_cursor,
    )
    current_cursor = {"import_id": "legacy-import", "source_task_id": "old-17"}
    current = host.invoke(
        "todo_read_legacy_current_tasks", project="workspace", limit=18,
        import_id="legacy-import", after=current_cursor,
    )
    context_cursor = {"import_id": "legacy-import", "source_table": "project_declarations",
                      "source_id": "orientation:1"}
    context = host.invoke(
        "todo_read_legacy_project_context", project="workspace", import_id="legacy-import",
        limit=19, after=context_cursor,
    )

    assert history["status"] == current["status"] == context["status"] == "ok"
    assert session.history_calls == [("", "legacy-import", 17, history_cursor)]
    assert session.current_calls == [("legacy-import", 18, current_cursor)]
    assert session.context_calls == [("legacy-import", 19, context_cursor)]
    assert selected_repositories == ["secondary", "primary", "primary"]
    assert session.closed


class _CompactFacade:
    def next_task(self, **_kwargs): return {"status": "ok"}
    def inspect_task(self, **_kwargs): return {"status": "ok"}
    def coordinate_task(self, **_kwargs): return {"status": "ok"}
    def finish_task(self, **_kwargs): return {"status": "ok"}


def test_compact_workflow_catalog_is_profile_scoped_and_has_no_raw_todo_tools():
    for profile in (MCPProfile.OBSERVER, MCPProfile.CODER, MCPProfile.MUTATOR):
        server = ProfiledFastMCP(f"compact-{profile.value}-test", profile=profile)
        register_workflow_tools(server, _CompactFacade())
        names = {tool.name for tool in asyncio.run(server.list_tools())}
        assert not any(name.startswith("todo_") for name in names)
        if profile == MCPProfile.OBSERVER:
            assert names.isdisjoint(WORKFLOW_TOOL_NAMES)
        else:
            assert names == set(WORKFLOW_TOOL_NAMES)
            assert "next_task" in names
