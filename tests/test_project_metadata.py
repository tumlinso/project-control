import hashlib
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from project_control.as1_context import ContextHost
from project_control.as1_control import ControlService
from project_control.config import (ProjectControlConfig, RepositoryConfig, WorkspaceConfig,
                                    load_config, save_config)
from project_control.profiles import (MCPProfile, MUTATOR_TOOL_NAMES, OBSERVER_TOOL_NAMES,
                                      CODEX_TOOL_NAMES, ProfiledFastMCP)
from project_control.as1_surface import register_surface
from project_control.project_metadata import ProjectMetadataStore


def _request(action, payload, *, operation="operation-0001", revision=0, mode="apply"):
    return {"format": "pc-project-amendment/1", "project": "demo", "action": action,
            "intent": "preserve Project Control project metadata", "expected_revision": revision,
            "operation_id": operation, "payload": payload, "mode": mode}


def test_metadata_amendment_roundtrip_cas_freshness_and_profile_gate(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "README.md"
    source.write_text("Registered repository orientation.\n")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    config = ProjectControlConfig(workspaces={
        "demo": WorkspaceConfig(authority_repository="source", repositories={
            "source": RepositoryConfig(root=root),
        }),
    })
    store = ProjectMetadataStore(tmp_path / "pc-state")
    host = ContextHost("mutator", "fixture-mutator", frozenset({"demo"}))
    control = ControlService(config, host, metadata_store=store)
    anchor = {"project": "demo", "repository": "source", "path": "README.md",
              "content_sha256": digest}

    identity = _request("register_identity", {"id": "api", "repository": "source", "version": "1"},
                        mode="preview")
    preview = control.amend_project(identity)
    assert preview["status"] == "preview" and preview["expected_revision"] == 0
    identity["mode"] = "apply"
    applied = control.amend_project(identity)
    assert applied["status"] == "applied" and applied["revision"] == 1
    assert control.amend_project(identity)["status"] == "replayed"
    assert store.read_context("demo", control._metadata_scope("demo"))["revision"] == 1

    orientation = _request("update_orientation", {
        "id": "purpose", "fields": {"purpose": "Preserve registered API behavior"},
        "anchors": [anchor], "field_anchors": {"purpose": [anchor]},
    }, operation="operation-orientation", revision=1)
    result = control.amend_project(orientation)
    assert result["status"] == "applied" and result["invalidations"]
    context = control.project_context("demo")
    assert context["metadata_revision"] == 2
    assert context["orientation"][0]["payload"]["fields"]["purpose"] == "Preserve registered API behavior"
    assert context["orientation"][0]["field_freshness"]["purpose"]["status"] == "fresh"

    stale = _request("record_skill_use", {
        "id": "python-guidance", "skill": "python", "reason": "Source conventions",
        "status": "applied", "anchors": [anchor],
    }, operation="operation-stale", revision=2, mode="preview")
    assert control.amend_project(stale)["status"] == "preview"
    stale["mode"] = "apply"
    source.write_text("Changed after review.\n")
    with pytest.raises(ValueError, match="source_prerequisite_stale"):
        control.amend_project(stale)
    assert store.read_context("demo", control._metadata_scope("demo"))["revision"] == 2

    for profile in ("observer", "coder", "codex"):
        denied = ControlService(config, ContextHost(profile, "fixture-reader", frozenset({"demo"})),
                                metadata_store=store)
        with pytest.raises(PermissionError, match="mutator_profile_required"):
            denied.amend_project(_request("register_identity", {
                "id": "not-written", "repository": "source", "version": "1"}, revision=2))
    assert "amend_project" in MUTATOR_TOOL_NAMES
    assert "amend_project" not in OBSERVER_TOOL_NAMES + CODEX_TOOL_NAMES


def test_provider_metadata_is_limited_to_configured_semantic_options(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    config = ProjectControlConfig(workspaces={
        "demo": WorkspaceConfig(authority_repository="source", repositories={
            "source": RepositoryConfig(root=root),
        }, project_providers={"fixture": {"allowed_options": {"depth": [1, 2]}}}),
    })
    config_path = save_config(config, tmp_path / "project-control.toml")
    config = load_config(config_path)
    assert config.workspaces["demo"].project_providers["fixture"]["allowed_options"] == {"depth": [1, 2]}
    control = ControlService(config, ContextHost("mutator", "fixture-mutator", frozenset({"demo"})),
                             metadata_store=ProjectMetadataStore(tmp_path / "pc-state"))
    ok = _request("configure_provider", {"id": "provider", "provider_id": "fixture", "options": {"depth": 2}})
    assert control.amend_project(ok)["status"] == "applied"
    bad = _request("configure_provider", {"id": "provider", "provider_id": "fixture", "options": {"executable": "sh"}},
                   operation="operation-untrusted", revision=1)
    with pytest.raises(ValueError, match="untrusted_provider_option"):
        control.amend_project(bad)
    with pytest.raises(ValueError, match="cannot grant execution"):
        WorkspaceConfig(authority_repository="source", repositories={"source": RepositoryConfig(root=root)},
                        project_providers={"unsafe": {"allowed_options": {"executable": ["sh"]}}})


def test_amend_project_is_registered_and_invoked_only_for_mutator():
    calls = []
    composition = SimpleNamespace(control=SimpleNamespace(
        amend_project=lambda request: calls.append(request) or {"status": "applied"}))
    mutator = ProfiledFastMCP("pc", profile=MCPProfile.MUTATOR)
    register_surface(mutator, composition)
    schemas = asyncio.run(mutator.list_tools())
    amendment = next(tool for tool in schemas if tool.name == "amend_project")
    assert amendment.inputSchema["required"] == ["request"]
    request = _request("register_identity", {"id": "api", "repository": "source", "version": "1"})
    response = asyncio.run(mutator.call_tool("amend_project", {"request": request}))
    assert response[1] == {"status": "applied"}
    assert calls == [request]

    for profile in (MCPProfile.OBSERVER, MCPProfile.CODEX):
        server = ProfiledFastMCP("pc", profile=profile)
        register_surface(server, composition)
        assert "amend_project" not in {tool.name for tool in asyncio.run(server.list_tools())}
        with pytest.raises(ToolError, match="unavailable"):
            asyncio.run(server.call_tool("amend_project", {"request": request}))


def test_pc_tombstones_suppress_same_imported_declarations(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "README.md"
    source.write_text("source for project metadata\n")
    anchor = {"project": "demo", "repository": "source", "path": "README.md",
              "content_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}

    class Workflow:
        def invoke(self, tool, **arguments):
            if tool == "todo_read_authority":
                return {"status": "ok", "data": {
                    "project_id": "native-project", "authority_id": "native-authority",
                    "semantic_revision": 1, "accepted_plan_revision_id": None,
                    "sections": {"declaration_facts": [], "plan_revisions": [], "plan_membership": [],
                                 "work_definitions": [], "context_findings": []},
                    "page": {"complete": True, "next_page_token": None},
                }}
            if tool == "todo_read_legacy_project_context":
                origin = {"import_id": "legacy-import", "source_system": "legacy",
                          "source_project_id": "legacy-project", "source_schema_version": 2,
                          "source_authority_revision": 1, "source_digest": "a" * 64,
                          "source_project_name": "old", "source_repository_id": "legacy-repo",
                          "import_semantic_revision": 1, "imported_at": "2026-10-19T00:00:00Z"}
                records = []
                for kind, identity, payload in (
                    ("identity", "shared", {"id": "shared", "repository": "source", "version": "old"}),
                    ("orientation", "purpose", {"fields": {"purpose": "Old"}, "anchors": [anchor]}),
                    ("skill_use", "python", {"skill": "python", "reason": "Old", "status": "applied", "anchors": [anchor]}),
                ):
                    row = {"kind": kind, "id": identity, "version": 1, "retired": 0,
                           "payload_json": "{}", "revision": 1}
                    records.append({**origin, "source_table": "project_declarations", "source_id": f"{kind}:1",
                                    "source_entity_id": None, "row_revision": 1, "row": row, "payload": payload})
                return {"status": "ok", "data": {"kind": "legacy-project-context-page",
                                                     "payload": {"records": records, "next_cursor": None}}}
            raise AssertionError(tool)

    config = ProjectControlConfig(workspaces={"demo": WorkspaceConfig(
        authority_repository="source", repositories={"source": RepositoryConfig(root=root)})})
    control = ControlService(config, ContextHost("mutator", "fixture-mutator", frozenset({"demo"})),
                             metadata_store=ProjectMetadataStore(tmp_path / "pc-state"))
    control.workflow = Workflow()

    control.amend_project(_request("register_identity", {
        "id": "shared", "repository": "source", "version": "1"}, operation="operation-add"))
    control.amend_project(_request("remove_registration", {
        "id": "shared", "kind": "identity", "version": 1}, operation="operation-remove", revision=1))
    control.amend_project(_request("update_orientation", {
        "id": "purpose", "fields": {"purpose": "New"}, "anchors": [anchor]},
        operation="operation-orientation", revision=2))
    control.amend_project(_request("remove_registration", {
        "id": "purpose", "kind": "orientation", "version": 1},
        operation="operation-remove-orientation", revision=3))
    control.amend_project(_request("record_skill_use", {
        "id": "python", "skill": "python", "reason": "New", "status": "applied", "anchors": [anchor]},
        operation="operation-skill", revision=4))
    control.amend_project(_request("remove_registration", {
        "id": "python", "kind": "skill_use", "version": 1},
        operation="operation-remove-skill", revision=5))

    context = control.project_context("demo")
    assert not [item for item in context["declarations"] if item["id"] == "shared"]
    assert not [item for item in context["orientation"] if item["id"] == "purpose"]
    assert not [item for item in context["skill_uses"] if item["id"] == "python"]
