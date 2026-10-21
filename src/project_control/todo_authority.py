"""Resolution and identity checks for the native standalone Todo read port."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .config import ProjectControlConfig, configured_observer_skills_root

TODO_READ_PORT_CONTRACT = "PCU-TODO-READ-PORT/2"
REQUIRED_TODO_READ_CAPABILITIES = ("authority.read_model",)


@runtime_checkable
class TodoReadPort(Protocol):
    def identity(self) -> Mapping[str, Any]: ...
    def invoke(self, operation: str, *, repo_root: Path, arguments: tuple[str, ...] = ()) -> Mapping[str, Any]: ...


TodoReadPortFactory = Callable[..., TodoReadPort | None]


@dataclass(frozen=True)
class TodoProviderResolution:
    skills_root: Path | None
    todo_script: Path | None
    compatible: bool
    selection_source: str | None
    version: str | None
    file_identity: str | None
    capabilities: tuple[str, ...]
    warnings: tuple[str, ...]
    error_code: str | None = None
    mode: str | None = None
    read_port: TodoReadPort | None = None
    package_root: Path | None = None

    def local_diagnostics(self) -> dict[str, Any]:
        return {"status": "available" if self.compatible else "unavailable", "mode": self.mode,
                "selection_source": self.selection_source,
                "skills_root": str(self.skills_root) if self.skills_root else None,
                "internal_package_root": str(self.package_root) if self.package_root else None,
                "todo_entrypoint": None, "version": self.version, "file_identity": self.file_identity,
                "capabilities": list(self.capabilities), "warnings": list(self.warnings),
                "error_code": self.error_code}


def _standalone_package_root() -> Path:
    from .runtime_identity import bind_runtime
    return bind_runtime().package_root


def _verify_read_port(port: TodoReadPort) -> tuple[str | None, str | None, tuple[str, ...], str | None]:
    try:
        identity = dict(port.identity())
    except Exception:
        return None, None, (), "todo_read_port_identity_unavailable"
    if identity.get("contract") != TODO_READ_PORT_CONTRACT:
        return None, None, (), "todo_read_port_contract_mismatch"
    capabilities = tuple(str(item) for item in identity.get("capabilities", ()))
    if not set(REQUIRED_TODO_READ_CAPABILITIES).issubset(capabilities):
        return None, None, capabilities, "todo_read_port_schema_incompatible"
    version, source = identity.get("version"), identity.get("source_identity")
    if not isinstance(source, str) or not source:
        return None, None, capabilities, "todo_read_port_identity_invalid"
    return str(version) if version is not None else None, source, capabilities, None


def _bundled_read_port_factory(environment=None) -> TodoReadPortFactory:
    from .workflow_binding import todo_read_port_factory
    # Preserve the distinction between the process environment and an
    # explicitly supplied mapping.  In a sealed candidate, the default
    # process environment carries the release manifest and digest that bind
    # Todo to the paired package.  Replacing that environment with ``{}``
    # would initialize the binding without those pins and make its later
    # validation fail when the native read port observes the sealed runtime.
    return todo_read_port_factory() if environment is None else todo_read_port_factory(environment)


def resolve_todo_provider(config: ProjectControlConfig, workspace_id: str, *,
                          read_port_factory: TodoReadPortFactory | None = None,
                          project_root: Path | None = None,
                          principal: str = "project-control-observer",
                          profile: str = "observer") -> TodoProviderResolution:
    """Resolve a project-bound standalone session with an explicit host principal."""
    if workspace_id not in config.workspaces:
        return TodoProviderResolution(None, None, False, "standalone_distribution", None, None, (), (),
                                      "workspace_unavailable", "in_process")
    if project_root is None:
        return TodoProviderResolution(None, None, False, "standalone_distribution", None, None, (), (),
                                      "todo_project_root_unavailable", "in_process")
    package_root = None
    try:
        factory = read_port_factory or _bundled_read_port_factory()
        port = factory(project_root, principal=principal, profile=profile)
        if read_port_factory is None:
            package_root = _standalone_package_root()
    except Exception as exc:
        code = getattr(exc, "code", None)
        return TodoProviderResolution(None, None, False, "standalone_distribution", None, None, (), (),
                                      code if isinstance(code, str) else "todo_read_port_initialization_failed",
                                      "in_process", None, package_root)
    if port is None:
        return TodoProviderResolution(None, None, False, "standalone_distribution", None, None, (), (),
                                      "todo_read_port_unavailable", "in_process", None, package_root)
    version, identity, capabilities, error = _verify_read_port(port)
    try:
        content_root = configured_observer_skills_root(config)
        if not content_root.is_dir():
            content_root = None
    except (OSError, ValueError):
        content_root = None
    return TodoProviderResolution(content_root, None, error is None, "standalone_distribution", version,
                                  identity, capabilities, (), error, "in_process",
                                  port if error is None else None, package_root)


def resolve_skills_root(config: ProjectControlConfig, workspace_id: str, *, read_port_factory=None) -> Path | None:
    if workspace_id not in config.workspaces:
        return None
    try:
        root = configured_observer_skills_root(config)
    except (OSError, ValueError):
        return None
    return root if root.is_dir() else None
