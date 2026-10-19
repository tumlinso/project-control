"""Host-only runtime adapter for Project Control's local workers."""

from __future__ import annotations

from pathlib import Path

from .coordinator import HostCoordinator
from .facade import HostResourceFacade


class LocalWorkerRuntime:
    """Expose host physical ownership without a Todo sidecar or job store."""

    def __init__(self, project_root: str | Path, *, host: HostCoordinator | None = None):
        self.project_root = Path(project_root).resolve()
        self.host = HostResourceFacade(host or HostCoordinator())

