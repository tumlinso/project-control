import hashlib
import json
import subprocess

from project_control.as1_context import ContextHost, InformationService
from project_control.as1_packets import SQLitePacketStore
from project_control.as1_control import ControlService
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import ProjectSnapshot
from project_control.project_metadata import ProjectMetadataStore


def test_observer_reads_orientation_and_registered_relation_without_todo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    readme = repo / "README.md"
    readme.write_text("Registered project architecture and guide.\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c",
        "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True)
    digest = hashlib.sha256(readme.read_bytes()).hexdigest()
    guide_repo = tmp_path / "guide"
    guide_repo.mkdir()
    (guide_repo / "setup.md").write_text("Use the registered cross-project guide.\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(guide_repo)], check=True)
    subprocess.run(["git", "-C", str(guide_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(guide_repo), "-c", "user.name=Fixture", "-c",
        "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True)
    config = ProjectControlConfig(workspaces={
        "demo": WorkspaceConfig(repositories={"source": RepositoryConfig(root=repo)}),
        "guide-project": WorkspaceConfig(repositories={"docs": RepositoryConfig(root=guide_repo)}),
    })
    state = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    store = ProjectMetadataStore(state / "project-control")
    mutator = ControlService(config, ContextHost("mutator", "fixture-mutator", frozenset({"demo"})),
        metadata_store=store)
    anchor = {"project": "demo", "repository": "source", "path": "README.md",
        "content_sha256": digest}
    mutator.amend_project({"format": "pc-project-amendment/1", "project": "demo",
        "action": "update_orientation", "intent": "fixture", "expected_revision": 0,
        "operation_id": "orientation", "payload": {"id": "purpose",
            "fields": {"purpose": "A registered observer description"}, "anchors": [anchor],
            "field_anchors": {"purpose": [anchor]}}})
    scope = mutator._metadata_scope("demo")
    store.amend({"format": "pc-project-amendment/1", "project": "demo",
        "action": "register_relation", "intent": "fixture", "expected_revision": 1,
        "operation_id": "relation", "payload": {"id": "guide-link", "relation": "guidance",
            "source": {"project_uuid": "demo-uuid", "repository": "source", "kind": "path", "id": "README.md"},
            "target": {"project_uuid": "guide-uuid", "repository": "docs", "kind": "path", "id": "setup.md"},
            "anchors": [anchor]}}, scope_digest=scope, validate_sources=lambda _: [],
        provider_allowlist={}, project_identities={"demo-uuid"})

    def source_identity_only(project):
        return ProjectSnapshot(workspace_id=project, project_uuid={
            "demo": "demo-uuid", "guide-project": "guide-uuid"}[project],
            observed_at="2026-10-27T00:00:00Z", repositories={})

    observer = InformationService(config, SQLitePacketStore(tmp_path / "packets"), source_identity_only,
        ContextHost("observer", "fixture-observer", frozenset({"demo", "guide-project"})))
    assert observer.snapshot_provider("guide-project").project_uuid == "guide-uuid"
    assert observer._project_for_uuid("guide-uuid", "docs") == "guide-project"
    context, error = observer._context("demo")
    assert error == "semantic_context_extension_unavailable"
    assert context["orientation"][0]["payload"]["fields"]["purpose"] == "A registered observer description"
    assert context["metadata_relations"][0]["payload"]["target"]["id"] == "setup.md"
    overview = observer.call("overview", project="demo")
    assert overview["data"]["orientation"][0]["payload"]["fields"]["purpose"] == \
        "A registered observer description"
    assert overview["data"]["registered_relations"][0]["payload"]["target"]["id"] == "setup.md"

    packet = observer.call("context", project="demo", targets=["source:README.md"],
        include=["guidance"], budget_bytes=4096)
    relation = next(row for row in packet["data"]["sections"]["guidance"]
        if row.get("relation_id") == "guide-link")
    assert relation["basis"] == "registered_relation"
    assert "Use the registered cross-project guide." in str(relation.get("target_context", relation)), relation
    assert any(source["project"] == "guide-project" and source["path"] == "setup.md"
        for source in packet["sources"])

    restricted = InformationService(config, SQLitePacketStore(tmp_path / "restricted-packets"),
        source_identity_only, ContextHost("observer", "restricted", frozenset({"demo"})))
    denied = restricted.call("context", project="demo", targets=["source:README.md"],
        include=["guidance"], budget_bytes=4096)
    denied_relation = next(row for row in denied["data"]["sections"]["guidance"]
        if row.get("relation_id") == "guide-link")
    assert denied_relation["target_status"] == "unavailable"
    assert denied["status"] == "partial"
