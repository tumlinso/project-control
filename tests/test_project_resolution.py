from __future__ import annotations

import subprocess

import pytest

from project_control.as1_context import ContextHost, InformationService
from project_control.as1_packets import SQLitePacketStore
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig


def _repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    (path / "README.md").write_text("A registered project.\n", encoding="utf-8")
    return path


def _service(tmp_path):
    roots = {
        "engine": _repo(tmp_path / "engine-root"),
        "guide_a": _repo(tmp_path / "guide-a-root"),
        "guide_b": _repo(tmp_path / "guide-b-root"),
        "hidden": _repo(tmp_path / "hidden-root"),
    }
    config = ProjectControlConfig(workspaces={
        "engine": WorkspaceConfig(display_name="Engine Project", repositories={
            "source": RepositoryConfig(root=roots["engine"])}),
        "guide_a": WorkspaceConfig(display_name="Shared Name", repositories={
            "guidance": RepositoryConfig(root=roots["guide_a"])}),
        "guide_b": WorkspaceConfig(display_name="Shared Name", repositories={
            "guidance": RepositoryConfig(root=roots["guide_b"])}),
        "hidden": WorkspaceConfig(display_name="Shared Name", repositories={
            "guidance": RepositoryConfig(root=roots["hidden"])}),
    })
    service = InformationService(config, SQLitePacketStore(tmp_path / "packets"),
        lambda _project: None,
        ContextHost("observer", "resolution-test", frozenset({"engine", "guide_a", "guide_b"})))
    return service, roots


@pytest.mark.parametrize("selector_kind", ["canonical", "name", "repository_root"])
def test_registered_project_selectors_resolve_to_canonical_identity(tmp_path, selector_kind):
    service, roots = _service(tmp_path)
    selector = {
        "canonical": "engine",
        "name": "Engine Project",
        "repository_root": str(roots["engine"]),
    }[selector_kind]

    result = service.call("read", project=selector, repository="source", paths=["README.md"])

    assert result["status"] == "ok"
    assert result["sources"][0]["project"] == "engine"
    assert result["sources"][0]["repository"] == "source"


def test_repository_alias_can_select_an_unambiguous_registered_project(tmp_path):
    service, _roots = _service(tmp_path)

    result = service.call("read", project="source", repository="source", paths=["README.md"])

    assert result["status"] == "ok"
    assert result["sources"][0]["project"] == "engine"


def test_ambiguous_project_name_returns_only_permitted_canonical_candidates(tmp_path):
    service, _roots = _service(tmp_path)

    result = service.call("overview", project="Shared Name")

    assert result["status"] == "partial"
    assert result["data"]["resolution"] == "ambiguous"
    assert [row["project"] for row in result["data"]["candidates"]] == ["guide_a", "guide_b"]
    assert all(row["project"] != "hidden" for row in result["data"]["candidates"])
    assert [row["arguments"]["project"] for row in result["next_calls"]] == ["guide_a", "guide_b"]


def test_inaccessible_canonical_project_does_not_become_an_alias_candidate(tmp_path):
    service, roots = _service(tmp_path)

    with pytest.raises(PermissionError, match="project_not_permitted"):
        service.call("read", project="hidden", repository=str(roots["hidden"]), paths=["README.md"])
