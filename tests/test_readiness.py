from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from project_control import app
from project_control.config import ProjectControlConfig


class _Binding:
    identity = SimpleNamespace(fingerprint="todo-fixture-fingerprint")

    @staticmethod
    def validate():
        return None


class _BindingError(RuntimeError):
    code = "runtime_identity_mismatch"


class _Composition:
    def __init__(self, *, central_error=None, content_error=None):
        self.jobs = SimpleNamespace(health=lambda: {"status": "ok"})
        self.backend = SimpleNamespace(central_status=self._central_status)
        self.skills = SimpleNamespace(skills={}, catalog_identity=self._catalog_identity)
        self.workflow = SimpleNamespace()
        self.central_error = central_error
        self.content_error = content_error

    def _central_status(self):
        if self.central_error:
            raise RuntimeError(self.central_error)
        return {"observer_contract": "PC-OBSERVER-SUPERVISOR/1", "supervisor_pid": 42,
                "supervisor_process_start": "fixture", "source_sha256": "a" * 64}

    def _catalog_identity(self):
        if self.content_error:
            raise RuntimeError(self.content_error)
        return "b" * 64

    @staticmethod
    def start():
        return None

    @staticmethod
    def close():
        return None


def _routes(monkeypatch, *, profile="observer", binding_error=None, central_error=None, content_error=None):
    composition = _Composition(central_error=central_error, content_error=content_error)
    if content_error:
        composition.skills.skills["cuda"] = {"name": "cuda"}
    async def inline_thread(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(app.asyncio, "to_thread", inline_thread)
    monkeypatch.setattr(app, "todo_read_port_factory", lambda: (lambda _root: None))
    monkeypatch.setattr(app, "register_workflow_tools", lambda _mcp, _workflow: None)
    if binding_error is None:
        monkeypatch.setattr(app, "initialize_workflow_binding", lambda: _Binding())
    else:
        def fail_binding():
            raise _BindingError(binding_error)
        monkeypatch.setattr(app, "initialize_workflow_binding", fail_binding)
    monkeypatch.setattr("project_control.as1_surface.compose_surface",
                        lambda *args, **kwargs: composition)
    monkeypatch.setattr("project_control.as1_surface.register_surface", lambda *args: None)
    mcp = app.create_mcp(ProjectControlConfig(), profile=profile)
    return {route.path: route.endpoint for route in mcp._custom_starlette_routes}, composition


def _request(routes, path):
    response = asyncio.run(routes[path](None))
    return response.status_code, json.loads(response.body)


def test_observer_readiness_does_not_require_todo_or_inference(monkeypatch):
    routes, _ = _routes(monkeypatch)
    status, body = _request(routes, "/readyz")
    assert status == 200
    assert body["status"] == "ready"
    assert body["retrieval"] == "available"
    assert body["workflow"] == {"status": "not_required"}
    assert _request(routes, "/healthz")[0] == 200


def test_local_readiness_reports_unavailable_standalone_workflow_binding(monkeypatch):
    # Local profiles require the standalone Todo binding; Observer retrieval does not.
    routes, _ = _routes(monkeypatch, profile="codex", binding_error="runtime_identity_mismatch")
    status, body = _request(routes, "/readyz")
    assert status == 503
    assert body["status"] == "unavailable"
    assert body["workflow"] == {"status": "unavailable", "reason": "runtime_identity_mismatch"}
    assert _request(routes, "/healthz")[0] == 200
