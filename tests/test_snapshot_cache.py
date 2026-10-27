"""Repeated orientation reuses bounded observations without hiding changes."""
from pathlib import Path
from types import SimpleNamespace

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.snapshot import SnapshotBuilder


def _builder(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    common = root / ".git"
    common.mkdir()
    state = {"fingerprint": "first", "worktree_calls": 0, "cuda": "idle"}

    class Git:
        def __init__(self, _root):
            pass

        def identity(self):
            return SimpleNamespace(commit="a" * 40, dirty=True,
                status_fingerprint=state["fingerprint"])

        def common_dir(self):
            return common

        def worktrees(self):
            state["worktree_calls"] += 1
            return ()

    monkeypatch.setattr("project_control.snapshot.GitReadAdapter", Git)
    monkeypatch.setattr("project_control.snapshot.CudaReadAdapter.status",
        lambda *_args: {"warnings": [], "state": state["cuda"]})
    config = ProjectControlConfig(workspaces={"demo": WorkspaceConfig(
        repositories={"main": RepositoryConfig(root=root)})})
    builder = SnapshotBuilder(config)
    monkeypatch.setattr(builder, "_todo_provider", lambda _project:
        SimpleNamespace(compatible=False, read_port=None, error_code="todo_not_configured"))
    return builder, state, common


def test_repeat_orientation_reuses_worktree_observation(tmp_path, monkeypatch):
    builder, state, _ = _builder(tmp_path, monkeypatch)
    first = builder.build("demo")
    again = builder.build("demo")
    assert state["worktree_calls"] == 1
    assert first.model_dump() == again.model_dump()
    again.warnings.append("caller change")
    assert "caller change" not in builder.build("demo").warnings
    first.warnings.append("first caller change")
    assert "first caller change" not in builder.build("demo").warnings


def test_source_change_invalidates_worktree_observation(tmp_path, monkeypatch):
    builder, state, _ = _builder(tmp_path, monkeypatch)
    builder.build("demo")
    state["fingerprint"] = "changed-bytes"
    changed = builder.build("demo")
    assert state["worktree_calls"] == 2
    assert changed.repositories["main"].working_tree_fingerprint == "changed-bytes"


def test_worktree_membership_change_invalidates_observation(tmp_path, monkeypatch):
    builder, state, common = _builder(tmp_path, monkeypatch)
    builder.build("demo")
    (common / "worktrees" / "new-worktree").mkdir(parents=True)
    builder.build("demo")
    assert state["worktree_calls"] == 2


def test_volatile_provider_changes_are_visible_with_cached_git(tmp_path, monkeypatch):
    builder, state, _ = _builder(tmp_path, monkeypatch)
    builder.build("demo")
    state["cuda"] = "changed-observation"
    changed = builder.build("demo")
    assert changed.cuda["state"] == "changed-observation"
    assert state["worktree_calls"] == 1
