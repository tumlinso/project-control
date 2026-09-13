from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from ..adapters.git import GitReadAdapter
from ..config import ProjectControlConfig, ensure_private_directory
from ..models import PerformanceProbeInput, ProjectSnapshot, ToolEnvelope, ToolStatus, envelope
from ..registry import WorkspaceRegistry
from ..snapshot import resolve_skills_root
from ..subprocesses import FixedCommandRunner


_REGISTRY_NAME = "cuda-benchmarks.json"
_MAX_RESULT_BYTES = 16 * 1024


def performance_probe_artifact_root() -> Path:
    """The only writable root handed to a registered measurement probe."""

    configured = os.environ.get("PROJECT_CONTROL_PERFORMANCE_PROBE_STATE_DIR")
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    root = Path(configured) if configured else base / "project-control" / "performance-probe"
    ensure_private_directory(root)
    return root


def _worktree_state(root: Path) -> str:
    """Full Git status digest, deliberately including generated Todo files."""

    raw = GitReadAdapter(root)._git("status", "--porcelain=v2", "-z", "--untracked-files=all", timeout=8.0)
    return hashlib.sha256(raw.encode()).hexdigest()


def _compact_probe_result(value: dict[str, Any]) -> dict[str, Any]:
    """Export evidence, never controller command/environment implementation detail."""

    def select(mapping: object, names: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(mapping, dict):
            return {}
        return {name: mapping[name] for name in names if name in mapping}

    result: dict[str, Any] = select(value, (
        "status", "code", "reason", "campaign", "mode", "effective_parameters",
        "elapsed_ms", "evidence_id", "artifact_id", "artifact_ids", "rebuild",
    ))
    probe = value.get("probe") if isinstance(value.get("probe"), dict) else value
    # The registered controller's compact `probe` evidence is the canonical
    # wire form. Deliberately retain identity and statistical substance while
    # stripping artifact paths and controller diagnostics.
    for source, target, names in (
        ("identity", "identity", ("binary", "binary_sha256", "source_commit", "input", "input_identity", "registry_identity")),
        ("placement", "placement", ("gpu_uuids", "gpu_indices", "topology", "nvlink_domains", "interference_domains")),
        ("metrics", "metrics", ("summary", "statistics", "baseline", "comparability")),
        ("profiler", "profiler", ("summary", "artifact_id", "tool")),
        ("quiescence", "quiescence", ("status", "contaminated", "reasons")),
    ):
        selected = select(probe.get(source), names)
        if selected:
            result[target] = selected
    for source, target, names in (
        ("registry", "registry", ("id", "sha256", "digest", "campaign")),
        ("source", "source", ("commit", "fingerprint", "dirty", "patch_hash", "identity", "sha256")),
        ("binary", "binary", ("sha256", "identity", "name")),
        ("statistics", "statistics", ("median", "mad", "confidence_interval_approx_95", "mean", "min", "max", "stddev", "samples", "unit")),
        ("baseline", "baseline", ("id", "identity", "comparability", "delta_percent")),
        ("profiler_summary", "profiler_summary", (
            "tool", "status", "returncode", "artifact_id", "artifact_ids",
            "trace_valid", "steady_state_timing_valid", "needs_rerun_for_timing",
            "counter_valid", "timing_valid", "needs_more_data", "measurement_scope",
            "reasons", "bottleneck_hints", "recommended_route",
            "recommended_route_reason", "top_kernels", "hot_kernel", "api_signals", "next_step",
        )),
        ("quiescence", "quiescence", ("status", "contaminated", "reasons")),
    ):
        selected = select(probe.get(source), names)
        if selected:
            result[target] = selected
    inputs = probe.get("inputs")
    if isinstance(inputs, list):
        result["inputs"] = [
            select(item, ("parameter", "source", "reference", "sha256", "dataset_id", "tier"))
            for item in inputs[:32] if isinstance(item, dict)
        ]
    comparable = probe.get("comparable")
    if isinstance(comparable, (bool, str)):
        result["comparable"] = comparable
    if isinstance(probe.get("elapsed_ms"), (int, float)):
        result["elapsed_ms"] = probe["elapsed_ms"]
    artifacts = probe.get("artifacts")
    if isinstance(artifacts, list):
        result["artifact_ids"] = [
            str(item.get("id") or item.get("artifact_id") or item.get("sha256"))
            for item in artifacts if isinstance(item, dict) and (item.get("id") or item.get("artifact_id") or item.get("sha256"))
        ][:32]
    # The Skills adapter may use the same names at top level; support that
    # compact wire form without exposing unrecognized diagnostic payloads.
    for name in ("binary", "binary_sha256", "source_commit", "input_identity", "registry_identity",
                 "gpu_uuids", "gpu_indices", "topology", "statistics", "baseline", "comparability",
                 "contaminated", "quiescence"):
        if name in probe and name not in result:
            result[name] = probe[name]
    encoded = json.dumps(result, sort_keys=True, default=str).encode()
    if len(encoded) > _MAX_RESULT_BYTES:
        return {"status": "partial", "code": "probe_evidence_exceeds_budget", "evidence_id": result.get("evidence_id")}
    return result


def performance_probe(
    config: ProjectControlConfig,
    request: PerformanceProbeInput,
    *,
    snapshot: ProjectSnapshot,
    snapshot_getter: Callable[[], ProjectSnapshot],
    runner: FixedCommandRunner | None = None,
    skills_root: Path | None = None,
    allow_rebuild: bool = True,
) -> ToolEnvelope:
    """Run a fixed Skills-side registered-probe command with a closed input surface."""

    registry = WorkspaceRegistry(config)
    workspace = registry.workspace(request.project)
    repository = registry.repository(request.project, workspace.authority_repository)
    if request.rebuild and not allow_rebuild:
        result = envelope("performance_probe", snapshot, {
            "status": "unavailable", "code": "probe_rebuild_requires_root_profile",
            "campaign": request.campaign, "mode": request.mode, "rebuild": True,
        }, warnings=["probe_rebuild_requires_root_profile"], compact_identity=True)
        result.status = ToolStatus.UNAVAILABLE
        return result
    registry_path = repository.root / _REGISTRY_NAME
    if not registry_path.is_file():
        result = envelope("performance_probe", snapshot, {
            "status": "unavailable", "code": "registered_campaign_registry_unavailable",
            "campaign": request.campaign, "mode": request.mode,
        }, warnings=["registered_campaign_registry_unavailable"], compact_identity=True)
        result.status = ToolStatus.UNAVAILABLE
        return result
    # The application passes the root pinned by its verified in-process Todo
    # binding. The resolver is a narrow direct-service fallback for tests and
    # offline callers, never an MCP-supplied location.
    bound_skills_root = skills_root or resolve_skills_root(config, request.project)
    controller = bound_skills_root / "cuda" / "scripts" / "cuda_controller.py" if bound_skills_root else None
    if controller is None or not controller.is_file():
        result = envelope("performance_probe", snapshot, {
            "status": "unavailable", "code": "cuda_controller_unavailable",
            "campaign": request.campaign, "mode": request.mode,
        }, warnings=["cuda_controller_unavailable"], compact_identity=True)
        result.status = ToolStatus.UNAVAILABLE
        return result

    before = {alias: _worktree_state(registry.repository(request.project, alias).root) for alias in workspace.repositories}
    artifact_root = performance_probe_artifact_root()
    spec = {
        "schema_version": 1,
        "project_root": str(repository.root),
        "campaign": request.campaign,
        "mode": request.mode,
        "parameters": request.parameters,
        "rebuild": request.rebuild,
    }
    argv = [sys.executable, str(controller), "probe", "--spec", "-", "--json"]
    completed = (runner or FixedCommandRunner(max_capture_bytes=256 * 1024)).run(
        argv, cwd=repository.root, timeout=7200.0,
        env={"PROJECT_CONTROL_PERFORMANCE_PROBE_STATE_DIR": str(artifact_root)},
        input_text=json.dumps(spec, sort_keys=True, separators=(",", ":")), check=False,
    )
    try:
        raw = completed.json()
    except Exception:
        raw = {"status": "unavailable", "code": "cuda_probe_invalid_response"}
    after = {alias: _worktree_state(registry.repository(request.project, alias).root) for alias in workspace.repositories}
    changed = sorted(alias for alias in before if before[alias] != after.get(alias))
    data = _compact_probe_result(raw)
    data.update({
        "campaign": request.campaign,
        "mode": request.mode,
        "requested_parameters": request.parameters,
        "rebuild": request.rebuild,
        "registry_identity": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
        "project_worktree_unchanged": not changed,
        "artifact_root_id": hashlib.sha256(str(artifact_root).encode()).hexdigest()[:20],
    })
    warnings: list[str] = []
    if completed.returncode and not data.get("code"):
        data["code"] = "cuda_probe_failed"
    code = str(data.get("code", ""))
    if code:
        warnings.append(code)
    if changed:
        data["status"] = "failed"
        data["valid"] = False
        warnings.append("project_worktree_changed")
    # A fresh snapshot is acquired after the process solely to attach a fresh
    # cursor; source mutation is never accepted as a successful measurement.
    fresh = snapshot_getter()
    result = envelope("performance_probe", fresh, data, warnings=warnings, compact_identity=True)
    if changed:
        result.status = ToolStatus.INTERNAL_ERROR
    elif code in {
        "probe_resource_unavailable", "probe_binary_unavailable",
        "foreground_resource_contention", "requested_accelerator_unavailable",
        "foreign_gpu_activity", "gpu_not_quiescent",
    }:
        result.status = ToolStatus.UNAVAILABLE
    elif code == "probe_invalid_request":
        result.status = ToolStatus.INVALID_REQUEST
    return result
