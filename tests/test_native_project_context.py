from __future__ import annotations

import hashlib
from pathlib import Path

from project_control.as1_context import ContextHost
from project_control.as1_control import ControlService
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig


class _Workflow:
    def __init__(self, root: Path):
        self.root = root
        self.context_cursors = []

    @staticmethod
    def _record(source_id, row, payload, *, revision=4):
        return {
            "import_id": "legacy-import",
            "source_system": "project-control.todo-v2",
            "source_project_id": "legacy-project",
            "source_schema_version": 2,
            "source_authority_revision": 12,
            "source_digest": "a" * 64,
            "source_project_name": "old-workspace",
            "source_repository_id": "legacy-repo",
            "import_semantic_revision": 4,
            "imported_at": "2026-10-19T00:00:00Z",
            "source_table": "project_declarations",
            "source_id": source_id,
            "source_entity_id": None,
            "row_revision": revision,
            "row": row,
            "payload": payload,
        }

    def invoke(self, tool, **arguments):
        if tool == "todo_read_authority":
            return {"status": "ok", "data": {
                "project_id": "native-project", "authority_id": "native-authority",
                "semantic_revision": 9, "accepted_plan_revision_id": "plan-1",
                "sections": {
                    "declaration_facts": [], "plan_revisions": [], "plan_membership": [],
                    "work_definitions": [], "context_findings": [{"finding_id": "finding-1"}],
                },
                "page": {"complete": True, "next_page_token": None},
            }}
        if tool == "todo_read_legacy_project_context":
            cursor = arguments.get("after")
            self.context_cursors.append(cursor)
            if cursor is None:
                records = [self._record("identity:1", {
                    "kind": "identity", "id": "package", "version": 1, "retired": 0,
                    "payload_json": "{}", "revision": 2,
                }, {})]
                records.append(self._record("orientation:1", {
                    "kind": "orientation", "id": "purpose", "version": 1, "retired": 0,
                    "payload_json": "{}", "revision": 3,
                }, {"fields": {"purpose": "Old purpose"}, "anchors": [{
                    "project": "legacy-project", "repository": "legacy-repo", "path": "README.md",
                    "content_sha256": hashlib.sha256((self.root / "README.md").read_bytes()).hexdigest(),
                }]}))
                next_cursor = {"import_id": "legacy-import", "source_table": "project_declarations",
                               "source_id": "orientation:1"}
            else:
                records = [self._record("orientation:2", {
                    "kind": "orientation", "id": "purpose", "version": 2, "retired": 0,
                    "payload_json": "{}", "revision": 5,
                }, {"fields": {"purpose": "Current purpose"}, "anchors": [{
                    "project": "legacy-project", "repository": "legacy-repo", "path": "README.md",
                    "content_sha256": hashlib.sha256((self.root / "README.md").read_bytes()).hexdigest(),
                }]}, revision=5)]
                next_cursor = None
            return {"status": "ok", "data": {"kind": "legacy-project-context-page",
                                                   "payload": {"records": records, "next_cursor": next_cursor}}}
        raise AssertionError(tool)


def test_project_context_preserves_paged_legacy_orientation_and_native_findings(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("source-backed purpose\n", encoding="utf-8")
    config = ProjectControlConfig(workspaces={
        "workspace": WorkspaceConfig(
            authority_repository="primary",
            repositories={"primary": RepositoryConfig(root=root)},
        ),
    })
    service = ControlService(config, ContextHost("observer", "observer", frozenset({"workspace"})))
    workflow = _Workflow(root)
    service.workflow = workflow

    context = service.project_context("workspace")

    assert context["status"] == "ok"
    assert context["project_uuid"] == "native-project"
    assert len(context["orientation"]) == 1
    orientation = context["orientation"][0]
    assert orientation["version"] == 2
    assert orientation["payload"]["fields"]["purpose"] == "Current purpose"
    assert orientation["field_freshness"]["purpose"]["status"] == "fresh"
    assert orientation["field_freshness"]["purpose"]["sources"][0]["source"]["project"] == "workspace"
    assert orientation["field_freshness"]["purpose"]["sources"][0]["source"]["repository"] == "primary"
    assert {item["id"] for item in context["declarations"]} == {"package"}
    assert context["context_findings"] == [{"finding_id": "finding-1", "repository_alias": "primary"}]
    assert workflow.context_cursors == [None, {
        "import_id": "legacy-import", "source_table": "project_declarations", "source_id": "orientation:1",
    }]
