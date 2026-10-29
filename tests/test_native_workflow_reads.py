from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.native_workflow import NativeWorkflowHost, _authority_page
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


def test_pending_review_selector_filters_before_authority_page_budget_and_binds_cursor():
    target_attempt = {"attempt_id": "attempt-target", "run_id": "run-target"}
    unrelated_attempt = {"attempt_id": "attempt-other", "run_id": "run-other"}
    model = {
        "project_id": "project-1", "authority_id": "authority-1", "semantic_revision": 9,
        "sections": {
            # More unrelated authority records than the compact review reader's
            # 32-page budget; they must not consume pages for an explicit review.
            "audit_events": [{"event_id": f"event-{i}"} for i in range(7000)],
            "attempts": [target_attempt, unrelated_attempt],
            "outcome_candidates": [
                {"candidate_id": "candidate-target", "attempt_id": "attempt-target",
                 "work_item_id": "task-target", "state": "pending", "reported_at": "1"},
                {"candidate_id": "candidate-other-task", "attempt_id": "attempt-target",
                 "work_item_id": "task-other", "state": "pending", "reported_at": "0"},
                {"candidate_id": "candidate-other-run", "attempt_id": "attempt-other",
                 "work_item_id": "task-target", "state": "pending", "reported_at": "0"},
                {"candidate_id": "candidate-resolved", "attempt_id": "attempt-target",
                 "work_item_id": "task-target", "state": "accepted", "reported_at": "0"},
            ],
        },
    }
    session = SimpleNamespace(read_model=lambda *, expected_revision: SimpleNamespace(to_dict=lambda: deepcopy(model)))

    page = _authority_page(session, expected_revision=9, limit=200, page_token=None,
                           review_selector={"task_id": "task-target", "run_id": "run-target"})

    assert page["review_selector"] == {"task_id": "task-target", "run_id": "run-target"}
    assert page["page"]["complete"] is True
    assert page["sections"]["outcome_candidates"] == [model["sections"]["outcome_candidates"][0]]
    assert page["sections"]["attempts"] == [target_attempt]
    assert "audit_events" not in page["sections"]
    assert page["section_counts"] == {"attempts": 1, "outcome_candidates": 1}


def test_pending_review_cursor_cannot_be_reused_without_its_selector():
    model = {
        "project_id": "project-1", "authority_id": "authority-1", "semantic_revision": 9,
        "sections": {
            "attempts": [{"attempt_id": "attempt-target", "run_id": "run-target"}],
            "outcome_candidates": [{"candidate_id": f"candidate-{i}",
                                     "attempt_id": "attempt-target", "work_item_id": "task-target",
                                     "state": "pending", "reported_at": str(i)} for i in range(201)],
        },
    }
    session = SimpleNamespace(read_model=lambda *, expected_revision: SimpleNamespace(to_dict=lambda: deepcopy(model)))
    first = _authority_page(session, expected_revision=9, limit=1, page_token=None,
                            review_selector={"task_id": "task-target", "run_id": "run-target"})
    assert first["page"]["next_page_token"]

    with pytest.raises(ValueError, match="page_token_scope_mismatch"):
        _authority_page(session, expected_revision=9, limit=1,
                        page_token=first["page"]["next_page_token"], review_selector=None)


def test_unscoped_pending_review_projection_ignores_unrelated_rows_without_changing_generic_reads():
    model = {
        "project_id": "project-1", "authority_id": "authority-1", "semantic_revision": 9,
        "sections": {
            "attempts": [{"attempt_id": "attempt-1", "run_id": "run-1"}],
            "audit_events": [{"event_id": f"event-{i}"} for i in range(7000)],
            "outcome_candidates": [
                {"candidate_id": "candidate-pending", "attempt_id": "attempt-1",
                 "work_item_id": "task-1", "state": "pending"},
                {"candidate_id": "candidate-resolved", "attempt_id": "attempt-1",
                 "work_item_id": "task-2", "state": "accepted"},
            ],
        },
    }
    session = SimpleNamespace(read_model=lambda *, expected_revision: SimpleNamespace(to_dict=lambda: deepcopy(model)))

    review_page = _authority_page(
        session, expected_revision=9, limit=200, page_token=None,
        review_selector={"task_id": None, "run_id": None},
    )
    generic_page = _authority_page(
        session, expected_revision=9, limit=200, page_token=None,
    )

    assert review_page["page"]["complete"] is True
    assert [row["candidate_id"] for row in review_page["sections"]["outcome_candidates"]] == [
        "candidate-pending",
    ]
    assert "audit_events" not in review_page["sections"]
    assert generic_page["page"]["complete"] is False
    assert len(generic_page["sections"]["audit_events"]) > 0
    assert generic_page["sections"]["outcome_candidates"] == []


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
