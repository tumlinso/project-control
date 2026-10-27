from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import project_control.runtime_identity as runtime_identity
from project_control.adapters.todo import NativeTodoReadPort, TodoReadAdapter


def _record(task_id: str, status: str, mapping: str, native_id: str | None, *, asserted=False):
    return SimpleNamespace(
        source_task_id=task_id,
        source_status=status,
        mapping_kind=mapping,
        native_work_item_id=native_id,
        definition_revision_id=f"definition-{native_id}" if native_id else None,
        completion_asserted=asserted,
        completion_assertion_digest="d" * 64 if asserted else None,
        source_project_id="legacy-project",
        import_id="legacy-import",
    )


def test_smoke_native_read_preserves_completion_facts_and_archival_statuses():
    model = {
        "sections": {
            "work_definitions": [
                {"work_item_id": "native-open", "title": "Open work"},
                {"work_item_id": "native-done", "title": "Completed prerequisite"},
                {"work_item_id": "native-new", "title": "New native work"},
            ],
            "plan_revisions": [{"plan_revision_id": "plan-1", "state": "accepted"}],
            "plan_membership": [{"plan_revision_id": "plan-1", "work_item_id": "native-open"}],
        }
    }
    current = [
        _record("old-open", "planned", "work_item", "native-open"),
        _record("old-done", "done", "work_item", "native-done", asserted=True),
        _record("old-closed", "closed", "archive_only", None),
        _record("old-superseded", "superseded", "archive_only", None),
    ]

    tasks = NativeTodoReadPort._legacy_projection(model, current)["tasks"]
    by_id = {item["id"]: item for item in tasks}

    assert by_id["old-open"]["title"] == "Open work"
    assert by_id["old-open"]["status"] == "planned"
    assert by_id["old-open"]["native_work_item_id"] == "native-open"
    assert by_id["old-open"]["in_current_plan"] is True
    assert by_id["old-done"]["status"] == "done"
    assert by_id["old-done"]["completion_asserted"] is True
    assert by_id["old-done"]["completion_assertion_digest"] == "d" * 64
    assert by_id["old-closed"]["status"] == "archived"
    assert by_id["old-closed"]["archived"] is True
    assert by_id["old-superseded"]["status"] == "superseded"
    assert by_id["native-new"]["status"] == "planned"
    assert by_id["native-new"]["completion_asserted"] is False


def test_native_authority_does_not_promote_linked_worktrees_to_authorities(tmp_path: Path):
    authority = tmp_path / "authority"
    adapter = TodoReadAdapter.__new__(TodoReadAdapter)
    adapter.read_port = object.__new__(NativeTodoReadPort)
    adapter.root = authority
    adapter._authority_root = lambda: authority

    assert adapter.authority_candidates() == (authority,)


def test_public_current_task_reads_are_paged_and_revision_checked(monkeypatch):
    records = [_record("old-a", "planned", "work_item", "native-a")]
    cursor = SimpleNamespace(import_id="legacy-import", source_task_id="old-a")

    class Session:
        def __init__(self):
            self.models = []
            self.pages = []
            self.revision = 7
            self.records = tuple(records)

        def read_model(self, *, expected_revision=None):
            self.models.append(expected_revision)
            if expected_revision not in (None, self.revision):
                raise AssertionError("unexpected revision")
            model = {"project_id": "p", "authority_id": "a", "semantic_revision": self.revision,
                     "sections": {"work_definitions": [], "plan_revisions": [], "plan_membership": []}}
            return SimpleNamespace(semantic_revision=self.revision, to_dict=lambda: dict(model))

        def read_legacy_current_tasks(self, *, limit, after=None):
            self.pages.append(after)
            if after is None:
                return SimpleNamespace(records=(), next_cursor=cursor)
            return SimpleNamespace(records=self.records, next_cursor=None)

    port = object.__new__(NativeTodoReadPort)
    port.runtime_identity = object()
    port.session = Session()
    port._observation = threading.local()
    monkeypatch.setattr(runtime_identity, "validate_runtime", lambda _identity: None)

    model = port._read()
    assert [item["id"] for item in model["_project_control_current_tasks"]] == ["old-a"]
    assert port.session.models == [None, 7]
    assert port.session.pages == [None, cursor]

    port.session.revision = 8
    port.session.records = (_record("old-b", "planned", "work_item", "native-b"),)
    refreshed = port._read()
    assert [item["id"] for item in refreshed["_project_control_current_tasks"]] == ["old-b"]
    assert port.session.models == [None, 7, None, 8]
    assert port.session.pages == [None, cursor, None, cursor]


def test_one_observation_reuses_one_consistent_authority_model(monkeypatch):
    class Session:
        def __init__(self):
            self.model_reads = []

        def read_model(self, *, expected_revision=None):
            self.model_reads.append(expected_revision)
            model = {"semantic_revision": 11, "project_id": "p", "authority_id": "a",
                     "sections": {"work_definitions": [], "plan_revisions": [], "plan_membership": []}}
            return SimpleNamespace(semantic_revision=11, to_dict=lambda: dict(model))

        def read_legacy_current_tasks(self, *, limit, after=None):
            return SimpleNamespace(records=(), next_cursor=None)

    port = object.__new__(NativeTodoReadPort)
    port.runtime_identity = object()
    port.session = Session()
    port._observation = threading.local()
    monkeypatch.setattr(runtime_identity, "validate_runtime", lambda _identity: None)

    port.begin_observation()
    first = port._read()
    second = port._read()
    port.end_observation()

    assert first["semantic_revision"] == second["semantic_revision"] == 11
    assert port.session.model_reads == [None, 11]
