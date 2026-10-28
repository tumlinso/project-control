from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from project_control.app import create_mcp
from project_control.as1_context import ContextHost
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.profiles import MCPProfile


class _PublicContext:
    def __init__(self, work_item_id: str):
        self.work_item_id = work_item_id

    def to_dict(self):
        return {
            "work_item_id": self.work_item_id,
            "semantic_revision": 41,
            "context": "Exact native task context from the trusted host session.",
        }


class _HostSession:
    def __init__(self):
        self.calls = []
        self.closed = False

    def build_context(self, work_item_id, **kwargs):
        self.calls.append((work_item_id, kwargs))
        return _PublicContext(work_item_id)

    def close(self):
        self.closed = True


def _public_result(server, name, arguments):
    value = asyncio.run(server.call_tool(name, arguments))
    return value[1] if isinstance(value, tuple) else value


def test_mutator_native_context_search_uses_guarded_native_reader(tmp_path: Path):
    allowed_root = tmp_path / "allowed"
    blocked_root = tmp_path / "blocked"
    allowed_root.mkdir()
    blocked_root.mkdir()
    config = ProjectControlConfig(workspaces={
        name: WorkspaceConfig(
            authority_repository="source",
            repositories={"source": RepositoryConfig(root=root)},
        )
        for name, root in (("allowed", allowed_root), ("blocked", blocked_root))
    })
    state = tmp_path / "state"
    host = ContextHost("mutator", "fixture-mutator", frozenset({"allowed"}))
    server = create_mcp(config, profile="mutator", host=host, state_directory=state)
    observer = create_mcp(config, profile="observer", state_directory=tmp_path / "observer")
    coder = create_mcp(config, profile="codex", state_directory=tmp_path / "coder")
    composition = server._project_control_surface
    observer_composition = observer._project_control_surface
    coder_composition = coder._project_control_surface
    native_host = composition.native_workflow
    session = _HostSession()
    try:
        assert composition.control is not None
        assert composition.information.native_reader == composition.control.native_read
        # Exercise the real NativeWorkflowHost dispatcher and ControlService
        # callback while stubbing only the Todo public API session result.
        native_host._binding_validator = lambda: None
        native_host._providers_for_project = lambda *_args, **_kwargs: None
        native_host._session_for_host = lambda *_args, **_kwargs: session

        response = _public_result(server, "search", {
            "project": "allowed",
            "query": {"kind": "native_context", "target": "WI-CURRENT-41"},
        })
        assert response["status"] == "ok"
        assert response["data"]["kind"] == "native_context"
        assert response["data"]["target"] == "WI-CURRENT-41"
        assert response["data"]["resolution"] == "resolved"
        assert response["data"]["result"]["work_item_id"] == "WI-CURRENT-41"
        assert session.calls == [("WI-CURRENT-41", {
            "run_id": None, "assignment": None, "attempt": None, "max_bytes": 8192,
        })]
        assert session.closed

        with pytest.raises(ToolError):
            _public_result(server, "search", {
                "project": "blocked",
                "query": {"kind": "native_context", "target": "WI-FORBIDDEN"},
            })
        assert len(session.calls) == 1

        # Existing observer/coder surfaces retain their existing read
        # composition and do not gain the mutator's ControlService reader.
        assert observer_composition.control is None
        assert observer_composition.information.native_reader is None
        assert coder_composition.control is None
        assert coder_composition.information.native_reader is None
    finally:
        composition.close()
        observer_composition.close()
        coder_composition.close()
