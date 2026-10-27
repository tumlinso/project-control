"""Resource-authority factory and local worker resource runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .coordinator import HostCoordinator
from .facade import HostResourceFacade


def host_resource_facade(*, coordinator: HostCoordinator | None = None,
                         client: Any | None = None,
                         authority: Any | None = None,
                         create_local: bool = True):
    """Construct the one active physical-resource authority for this process.

    The selector is a private operator-owned file. Once Device Scheduler is
    selected, failure to load its credentials or communicate with its MCP
    endpoint raises; the local SQLite allocator is never a fallback.
    """
    from ..device_scheduler import (
        DeviceSchedulerHostResourceFacade,
        DeviceSchedulerMcpClient,
        read_resource_authority,
    )

    selected = authority if authority is not None else read_resource_authority()
    if selected.mode == "device_scheduler":
        if coordinator is not None:
            raise RuntimeError("local_resource_coordinator_forbidden_under_device_scheduler_authority")
        return DeviceSchedulerHostResourceFacade(client or DeviceSchedulerMcpClient(selected))
    if client is not None:
        raise ValueError("a Device Scheduler client cannot be supplied while local authority is selected")
    return HostResourceFacade(coordinator or HostCoordinator(create=create_local))


class LocalWorkerRuntime:
    """Expose the configured physical-resource authority without a Todo sidecar."""

    def __init__(self, project_root: str | Path, *, host: HostCoordinator | None = None,
                 resource_client: Any | None = None, authority: Any | None = None):
        self.project_root = Path(project_root).resolve()
        self.host = host_resource_facade(coordinator=host, client=resource_client, authority=authority)
