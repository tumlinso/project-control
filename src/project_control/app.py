"""Project Control MCP startup and profile-specific lifecycle."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from .adapters.todo import TodoReadAdapter
from .as1_surface import compose_surface, register_surface
from .config import ProjectControlConfig, ServerConfig, load_config
from .models import ProjectSnapshot
from .profiles import MCPProfile, ProfiledFastMCP
from .snapshot import SnapshotBuilder
from .workflow_binding import initialize_workflow_binding, todo_read_port_factory
from .workflow_tools import (MaintenanceHostContext, WORKFLOW_INSTRUCTIONS,
    register_workflow_tools)
from .call_audit import caller_var


SERVER_INSTRUCTIONS = (
    "Project Control provides deterministic, source-grounded project retrieval. "
    "Use overview for orientation, frontier for current work, delta for changes, "
    "search for literal text or exact typed entities, context to assemble explicit "
    "targets, trace for typed relationships, evidence for recorded observations, "
    "and machine for bounded environment facts. Results preserve provenance and "
    "report unavailable or omitted material."
)


class Runtime:
    """Per-server source readers; Todo binding is opened only for local workflows."""

    def __init__(self, config: ProjectControlConfig, *, enable_workflow: bool = False):
        self.config = config
        self.workflow_enabled = enable_workflow
        self.workflow_binding = None
        self.workflow_binding_error = None
        self.todo_read_port_factory = None
        self.builder = SnapshotBuilder(config)

    def enable_workflow(self):
        if self.workflow_binding is not None:
            return
        try:
            binding = initialize_workflow_binding()
            binding.validate()
            self.workflow_binding = binding
            self.todo_read_port_factory = todo_read_port_factory()
            self.builder.todo_read_port_factory = self.todo_read_port_factory
        except Exception as error:
            self.workflow_binding_error = error
            self.workflow_binding = None

    def snapshot(self, project: str, *, host: bool = False, campaign: str | None = None) -> ProjectSnapshot:
        if self.workflow_enabled:
            self.enable_workflow()
        return self.builder.build(project, include_host=host, campaign=campaign)

    def todo_adapter(self, project: str) -> TodoReadAdapter | None:
        if self.workflow_enabled:
            self.enable_workflow()
        workspace = self.builder.registry.workspace(project)
        if not workspace.authority_repository:
            return None
        provider = self.builder._todo_provider(project)
        if not provider.compatible or not (provider.read_port or provider.todo_script):
            return None
        repository = self.builder.registry.repository(project, workspace.authority_repository)
        return TodoReadAdapter(repository.root, provider.todo_script, read_port=provider.read_port)


def create_mcp(config: ProjectControlConfig | None = None, *,
               profile: MCPProfile | str = MCPProfile.OBSERVER,
               maintenance_host: MaintenanceHostContext | None = None,
               host=None, state_directory: Path | None = None) -> ProfiledFastMCP:
    active_config = config or load_config()
    selected_profile = MCPProfile(profile)
    if maintenance_host is not None:
        from .as1_context import ContextHost
        if selected_profile != MCPProfile.MUTATOR or host is not None:
            raise ValueError("maintenance requires an explicit mutator startup principal")
        host = ContextHost("mutator", maintenance_host.recipient_principal,
                           frozenset(active_config.workspaces))
    runtime = Runtime(active_config,
        enable_workflow=selected_profile in {MCPProfile.CODEX, MCPProfile.MUTATOR})
    composition = compose_surface(runtime, selected_profile, host=host, state_directory=state_directory)
    instructions = SERVER_INSTRUCTIONS
    if selected_profile in {MCPProfile.CODEX, MCPProfile.MUTATOR}:
        instructions += " " + WORKFLOW_INSTRUCTIONS
    server = active_config.server
    mcp = ProfiledFastMCP("project-control", profile=selected_profile, instructions=instructions,
        host=server.host, port=server.port, streamable_http_path="/mcp", stateless_http=True,
        json_response=True, max_request_body_size=384 * 1024)
    register_surface(mcp, composition)
    if selected_profile in {MCPProfile.CODEX, MCPProfile.MUTATOR}:
        register_workflow_tools(mcp, composition.workflow)

    def readiness():
        runtime.enable_workflow()
        binding = runtime.workflow_binding
        if binding is None:
            reason = getattr(runtime.workflow_binding_error, "code", None) or "workflow_binding_unavailable"
            return {"status": "unavailable", "reason": str(reason)}
        try:
            binding.validate()
            return {"status": "available", "fingerprint": binding.identity.fingerprint}
        except Exception as error:
            return {"status": "unavailable", "reason": str(getattr(error, "code", None) or "workflow_binding_unavailable")}

    @mcp.custom_route("/healthz", methods=["GET"])
    async def health(_: Request):
        return JSONResponse({"status": "ok", "retrieval": "available"})

    @mcp.custom_route("/readyz", methods=["GET"])
    async def ready(_: Request):
        core = await asyncio.to_thread(readiness) if selected_profile != MCPProfile.OBSERVER else {"status": "not_required"}
        ok = selected_profile == MCPProfile.OBSERVER or core.get("status") == "available"
        return JSONResponse({"status": "ready" if ok else "unavailable", "retrieval": "available", "workflow": core},
            status_code=200 if ok else 503)

    @mcp.custom_route("/version", methods=["GET"])
    async def version(_: Request):
        return JSONResponse({"name": "project-control", "version": "0.5.1",
            "tool_schema_version": 13, "features": mcp.feature_metadata})

    setattr(mcp, "_project_control_runtime", runtime)
    setattr(mcp, "_project_control_surface", composition)
    return mcp


class AuditCallerMiddleware:
    """Capture the immediate HTTP peer without trusting forwarded headers."""
    def __init__(self, wrapped_app: Any):
        self.wrapped_app = wrapped_app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.wrapped_app(scope, receive, send)
            return
        peer = scope.get("client")
        caller = {"transport": "http", "upstream_identity": "unknown"}
        if isinstance(peer, (tuple, list)) and len(peer) >= 2:
            caller.update({"peer_ip": str(peer[0]), "peer_port": peer[1]})
        token = caller_var.set(caller)
        try:
            await self.wrapped_app(scope, receive, send)
        finally:
            caller_var.reset(token)


def create_asgi_app(config: ProjectControlConfig | None = None):
    mcp = create_mcp(config)
    app = mcp.streamable_http_app()
    app.add_middleware(AuditCallerMiddleware)
    original = app.router.lifespan_context
    composition = mcp._project_control_surface

    @asynccontextmanager
    async def lifespan(asgi_app):
        async with original(asgi_app) as state:
            composition.start()
            try:
                yield state
            finally:
                composition.close()

    app.router.lifespan_context = lifespan
    return app


def serve(*, host: str | None = None, port: int | None = None) -> int:
    import uvicorn
    config = load_config()
    selected = ServerConfig(host=host or config.server.host, port=port or config.server.port,
                            transport=config.server.transport)
    config.server = selected
    uvicorn.run(create_asgi_app(config), host=selected.host, port=selected.port,
        log_level="info", proxy_headers=False)
    return 0


def _serve_stdio(profile: MCPProfile, *, maintenance_host: MaintenanceHostContext | None = None) -> int:
    mcp = create_mcp(profile=profile, maintenance_host=maintenance_host)
    composition = mcp._project_control_surface
    try:
        composition.start()
        mcp.run(transport="stdio")
    finally:
        composition.close()
    return 0


def serve_codex() -> int:
    return _serve_stdio(MCPProfile.CODEX)


def serve_maintenance_operator(principal: str) -> int:
    from .workflow_tools import trusted_maintenance_context
    return _serve_stdio(MCPProfile.MUTATOR,
        maintenance_host=trusted_maintenance_context(principal))


def serve_mutator() -> int:
    return _serve_stdio(MCPProfile.MUTATOR)
