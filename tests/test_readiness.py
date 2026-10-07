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


def _routes(monkeypatch, *, binding_error=None, central_error=None, content_error=None):
    composition = _Composition(central_error=central_error, content_error=content_error)
    if content_error:
        composition.skills.skills["cuda"] = {"name": "cuda"}
    async def inline_thread(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(app.asyncio, "to_thread", inline_thread)
    monkeypatch.setattr(app, "todo_read_port_factory", lambda: (lambda _root: None))
    if binding_error is None:
        monkeypatch.setattr(app, "initialize_workflow_binding", lambda: _Binding())
    else:
        def fail_binding():
            raise _BindingError(binding_error)
        monkeypatch.setattr(app, "initialize_workflow_binding", fail_binding)
    monkeypatch.setattr("project_control.as1_surface.compose_surface",
                        lambda *args, **kwargs: composition)
    monkeypatch.setattr("project_control.as1_surface.register_surface", lambda *args: None)
    mcp = app.create_mcp(ProjectControlConfig())
    return {route.path: route.endpoint for route in mcp._custom_starlette_routes}, composition


def _request(routes, path):
    response = asyncio.run(routes[path](None))
    return response.status_code, json.loads(response.body)


def test_core_readiness_ignores_unavailable_optional_providers(monkeypatch):
    routes, _ = _routes(monkeypatch, central_error="central_supervisor_unavailable",
                        content_error="skill_catalog_unavailable")
    status, body = _request(routes, "/readyz")
    assert status == 200
    assert body["status"] == "ready"
    assert body["core"]["status"] == "available"
    assert body["core"]["configuration"] == "valid"
    assert body["core"]["workflow_engine"] == "available"
    assert body["core"]["fingerprint"] == "todo-fixture-fingerprint"
    assert body["central_inference"] == {
        "status": "unavailable", "reason": "central_supervisor_unavailable"}
    assert body["optional_content"] == {
        "status": "unavailable", "reason": "skill_catalog_unavailable"}
    assert _request(routes, "/healthz")[0] == 200


def test_core_readiness_fails_when_bundled_workflow_binding_is_unavailable(monkeypatch):
    routes, _ = _routes(monkeypatch, binding_error="runtime_identity_mismatch")
    status, body = _request(routes, "/readyz")
    assert status == 503
    assert body["status"] == "unavailable"
    assert body["core"] == {
        "status": "unavailable", "configuration": "valid",
        "workflow_engine": "unavailable", "reason": "runtime_identity_mismatch"}
    assert body["central_inference"]["status"] == "available"
    assert _request(routes, "/healthz")[0] == 200
