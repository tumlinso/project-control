"""Fixed, bounded host diagnostics for the local-investigator broker."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from ..config import ProjectControlConfig
from ..models import ProjectSnapshot, ToolEnvelope, envelope
from ..security import redact_output, redact_text
from ..subprocesses import CommandError, FixedCommandRunner

MachineDiagnostic = Literal[
    "gpu_summary", "gpu_topology", "gpu_processes", "host_memory",
    "filesystem_capacity", "services", "system",
]
_MAX_ROWS = 16


def _runtime_root() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return (base / "project-control").expanduser().resolve()


def _bounded_lines(value: str, limit: int = _MAX_ROWS) -> list[str]:
    return [redact_text(line)[:256] for line in value.splitlines() if line.strip()][:limit]


def _gpu_csv(raw: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in raw.splitlines()[:_MAX_ROWS]:
        values = [item.strip()[:128] for item in line.split(",")]
        if len(values) == len(fields):
            rows.append(dict(zip(fields, values, strict=True)))
    return rows


def machine_inspection(
    config: ProjectControlConfig, snapshot: ProjectSnapshot, *, project: str,
    diagnostic: MachineDiagnostic, runner: FixedCommandRunner | None = None,
) -> ToolEnvelope:
    """Execute one allowlisted, non-mutating diagnostic without exposing argv/output."""
    command_runner = runner or FixedCommandRunner(max_capture_bytes=32 * 1024)
    data: dict[str, Any] = {"diagnostic": diagnostic, "status": "ok", "warnings": []}
    try:
        if diagnostic == "gpu_summary":
            raw = command_runner.run([
                "nvidia-smi", "--query-gpu=index,name,driver_version,memory.total,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ], cwd=Path("/"), timeout=2.0).stdout
            data["devices"] = _gpu_csv(raw, ("index", "name", "driver_version", "memory_total_mib", "memory_free_mib", "utilization_percent"))
        elif diagnostic == "gpu_topology":
            raw = command_runner.run(["nvidia-smi", "topo", "-m"], cwd=Path("/"), timeout=2.0).stdout
            data["topology_rows"] = _bounded_lines(raw)
        elif diagnostic == "gpu_processes":
            raw = command_runner.run([
                "nvidia-smi", "--query-compute-apps=process_name,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ], cwd=Path("/"), timeout=2.0, check=False).stdout
            data["processes"] = _gpu_csv(raw, ("process_name", "used_gpu_memory_mib"))
        elif diagnostic == "host_memory":
            values: dict[str, str] = {}
            for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
                key, _, value = line.partition(":")
                if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                    values[key] = value.strip()[:64]
            data["memory"] = values
        elif diagnostic == "filesystem_capacity":
            workspace = config.workspaces[project]
            roots = [(f"repository:{alias}", item.root) for alias, item in sorted(workspace.repositories.items())]
            roots.append(("project_control_runtime", _runtime_root()))
            capacities = []
            for label, root in roots[:_MAX_ROWS]:
                try:
                    root.stat()  # Reject absent roots rather than probing arbitrary paths.
                    usage = os.statvfs(root)
                    capacities.append({"root": label, "available_bytes": usage.f_bavail * usage.f_frsize,
                                       "total_bytes": usage.f_blocks * usage.f_frsize, "present": root.is_dir()})
                except OSError:
                    capacities.append({"root": label, "status": "unavailable"})
            data["filesystems"] = capacities
        elif diagnostic == "services":
            services = ("project-control.service", "project-control-local-inference.service")
            states = []
            for service in services:
                raw = command_runner.run([
                    "systemctl", "--user", "show", service,
                    "--property=LoadState,ActiveState,SubState", "--no-pager",
                ], cwd=Path("/"), timeout=2.0, check=False).stdout
                fields = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
                states.append({"service": service, **{key: fields.get(key, "unknown")[:64]
                                                        for key in ("LoadState", "ActiveState", "SubState")}})
            data["services"] = states
        elif diagnostic == "system":
            raw = command_runner.run(["uname", "-srmo"], cwd=Path("/"), timeout=2.0).stdout
            data["kernel"] = _bounded_lines(raw, 1)
        else:  # Defensive runtime guard for callers outside the Pydantic broker.
            raise ValueError("machine_diagnostic_not_allowlisted")
    except (CommandError, OSError, KeyError, ValueError):
        data["status"] = "partial"
        data["warnings"].append("machine_diagnostic_unavailable")
    return envelope("machine_inspection", snapshot, redact_output(data), compact_identity=True)
