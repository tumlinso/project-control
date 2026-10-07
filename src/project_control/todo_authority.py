from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .config import ProjectControlConfig, configured_observer_skills_root


TODO_READ_PORT_CONTRACT = "PCU-TODO-READ-PORT/1"
REQUIRED_TODO_READ_CAPABILITIES = (
    "semantic.state",
    "semantic.anchor",
    "semantic.delta",
    "semantic.workflow",
    "export",
)


@runtime_checkable
class TodoReadPort(Protocol):
    """Narrow adapter seam for the bundled Todo read facade."""

    def identity(self) -> Mapping[str, Any]: ...

    def invoke(
        self,
        operation: str,
        *,
        repo_root: Path,
        arguments: tuple[str, ...] = (),
    ) -> Mapping[str, Any]: ...


TodoReadPortFactory = Callable[[Path], TodoReadPort | None]


@dataclass(frozen=True)
class TodoProviderResolution:
    # Kept for compatibility with diagnostics consumers. This is optional
    # documentation/content, never the source of executable Todo code.
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
        return {
            "status": "available" if self.compatible else "unavailable",
            "mode": self.mode,
            "selection_source": self.selection_source,
            "skills_root": str(self.skills_root) if self.skills_root else None,
            "internal_package_root": str(self.package_root) if self.package_root else None,
            "todo_entrypoint": str(self.todo_script) if self.todo_script else None,
            "version": self.version,
            "file_identity": self.file_identity,
            "capabilities": list(self.capabilities),
            "warnings": list(self.warnings),
            "error_code": self.error_code,
        }


def _bundled_package_root() -> Path:
    return (Path(__file__).resolve().parent.parent / "todo_orchestrator").resolve()


def _verify_read_port(
    port: TodoReadPort, package_root: Path,
) -> tuple[str | None, str | None, tuple[str, ...], str | None]:
    try:
        identity = dict(port.identity())
    except Exception:
        return None, None, (), "todo_read_port_identity_unavailable"
    if identity.get("contract") != TODO_READ_PORT_CONTRACT:
        return None, None, (), "todo_read_port_contract_mismatch"
    raw_package_root = identity.get("package_root")
    try:
        reported_package_root = Path(str(raw_package_root)).expanduser().resolve(strict=True)
    except (OSError, TypeError, ValueError):
        return None, None, (), "todo_read_port_identity_invalid"
    if reported_package_root != package_root:
        return None, None, (), "todo_read_port_package_mismatch"
    capabilities = tuple(str(item) for item in identity.get("capabilities", ()))
    if not set(REQUIRED_TODO_READ_CAPABILITIES).issubset(capabilities):
        return None, None, capabilities, "todo_read_port_schema_incompatible"
    source_identity = identity.get("source_identity")
    if not isinstance(source_identity, str) or not source_identity:
        return None, None, capabilities, "todo_read_port_identity_invalid"
    version = identity.get("version")
    return str(version) if version is not None else None, source_identity, capabilities, None


def _bundled_read_port_factory() -> TodoReadPortFactory:
    """Resolve the process-bound factory without consulting Skills paths."""
    from .workflow_binding import todo_read_port_factory

    factory = todo_read_port_factory()
    if factory is None:
        raise RuntimeError("bundled Todo read port is unavailable")
    return factory


def resolve_todo_provider(
    config: ProjectControlConfig,
    workspace_id: str,
    *,
    read_port_factory: TodoReadPortFactory | None = None,
) -> TodoProviderResolution:
    """Resolve the one verified Todo provider shipped with Project Control.

    Workspace and Skills roots identify optional content. They never choose
    executable code. The bundled package identity is checked by the runtime
    binding and again by the read-port identity before it is exposed here.
    """
    if workspace_id not in config.workspaces:
        return TodoProviderResolution(
            None, None, False, "bundled_package", None, None, (), (),
            "workspace_unavailable", "in_process", None, _bundled_package_root(),
        )
    package_root = _bundled_package_root()
    try:
        factory = read_port_factory or _bundled_read_port_factory()
        port = factory(package_root)
    except Exception:
        return TodoProviderResolution(
            None, None, False, "bundled_package", None, None, (), (),
            "todo_read_port_initialization_failed", "in_process", None, package_root,
        )
    if port is None:
        return TodoProviderResolution(
            None, None, False, "bundled_package", None, None, (), (),
            "todo_read_port_unavailable", "in_process", None, package_root,
        )
    version, identity, capabilities, error = _verify_read_port(port, package_root)
    try:
        content_root = configured_observer_skills_root(config)
        if not content_root.is_dir():
            content_root = None
    except (OSError, ValueError):
        content_root = None
    return TodoProviderResolution(
        content_root, None, error is None, "bundled_package", version, identity,
        capabilities, (), error, "in_process", port if error is None else None,
        package_root,
    )


def resolve_skills_root(
    config: ProjectControlConfig,
    workspace_id: str,
    *,
    read_port_factory: TodoReadPortFactory | None = None,
) -> Path | None:
    """Resolve optional Skills content for CUDA/docs, independent of Todo."""
    if workspace_id not in config.workspaces:
        return None
    try:
        root = configured_observer_skills_root(config)
    except (OSError, ValueError):
        return None
    return root if root.is_dir() else None
