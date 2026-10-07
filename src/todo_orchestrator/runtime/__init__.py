"""Supported, additive cross-skill runtime facade."""

from .contracts import (
    ContractError,
    normalize_artifact_ref,
    normalize_command_spec,
    normalize_evidence_summary,
    normalize_resource_request,
    normalize_source_identity,
)
from .facade import ArtifactFacade, HostResourceFacade, JobFacade, RuntimeFacade, SnapshotFacade
from .source import capture_source_identity
from .topology import LocalWorkerHostTopology, classify_local_worker_host_topology, discover_gpu_topology

__all__ = [
    "ArtifactFacade",
    "ContractError",
    "HostResourceFacade",
    "JobFacade",
    "LocalWorkerHostTopology",
    "RuntimeFacade",
    "SnapshotFacade",
    "capture_source_identity",
    "classify_local_worker_host_topology",
    "discover_gpu_topology",
    "normalize_artifact_ref",
    "normalize_command_spec",
    "normalize_evidence_summary",
    "normalize_resource_request",
    "normalize_source_identity",
]
