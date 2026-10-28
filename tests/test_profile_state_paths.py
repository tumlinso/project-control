from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from project_control.as1_surface import compose_surface
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.profiles import MCPProfile


def test_default_packet_state_respects_xdg_state_home_without_touching_home(tmp_path, monkeypatch):
    xdg_state = tmp_path / "private-state"
    home = tmp_path / "separate-home"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PROJECT_CONTROL_AS1_STATE_DIR", raising=False)
    config = ProjectControlConfig()
    runtime = SimpleNamespace(config=config, snapshot=lambda *_args, **_kwargs: None, todo_adapter=lambda *_args: None)

    composition = compose_surface(runtime, MCPProfile.OBSERVER)

    assert composition.store.directory == xdg_state / "project-control/as1/packets"
    assert (composition.store.directory / "packets.sqlite3").is_file()
    assert not home.exists()


def test_workflow_provider_factory_is_lazy_and_keeps_native_provider_composition(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    config = ProjectControlConfig(workspaces={"demo": WorkspaceConfig(
        authority_repository="source", repositories={"source": RepositoryConfig(root=root)})})
    runtime = SimpleNamespace(config=config, snapshot=lambda *_args, **_kwargs: None,
        todo_adapter=lambda *_args: None)

    composition = compose_surface(runtime, MCPProfile.CODEX,
        state_directory=tmp_path / "state")
    expected = object()
    with patch("project_control.native_providers.providers_for_project", return_value=expected) as factory:
        assert composition.native_workflow._providers("demo", root, "source") is expected

    factory.assert_called_once()
    assert factory.call_args.kwargs["principal"] == composition.host.principal
    assert factory.call_args.kwargs["profile"] == "coder"
