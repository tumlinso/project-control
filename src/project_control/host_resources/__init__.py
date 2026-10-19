"""Project Control-owned physical resource and process coordination."""

from .coordinator import HostCoordinator
from .contracts import ContractError, normalize_command_spec, normalize_source_identity
from .facade import HostResourceFacade
from .runtime import LocalWorkerRuntime
from .source import capture_source_identity
from .topology import classify_local_worker_host_topology, discover_gpu_topology

__all__ = [
    "ContractError", "HostCoordinator", "HostResourceFacade", "LocalWorkerRuntime",
    "capture_source_identity", "classify_local_worker_host_topology",
    "discover_gpu_topology", "normalize_command_spec", "normalize_source_identity",
]

