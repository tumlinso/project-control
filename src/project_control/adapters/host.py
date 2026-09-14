from __future__ import annotations

from pathlib import Path
from typing import Any

from ..subprocesses import CommandError, FixedCommandRunner


def host_memory_values(*, include_swap: bool = False) -> dict[str, str]:
    """Read the small deterministic memory subset shared by host consumers."""
    wanted = {"MemTotal", "MemAvailable"}
    if include_swap:
        wanted.update({"SwapTotal", "SwapFree"})
    values: dict[str, str] = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        key, _, value = line.partition(":")
        if key in wanted:
            values[key] = value.strip()[:64]
    return values


def host_gpu_summary(runner: FixedCommandRunner) -> list[dict[str, str]]:
    """One bounded GPU observation used by capacity and machine diagnostics."""
    raw = runner.run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,driver_version,memory.total,memory.free,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        cwd=Path("/"), timeout=2.0,
    ).stdout
    devices: list[dict[str, str]] = []
    for line in raw.splitlines()[:16]:
        fields = [field.strip()[:128] for field in line.split(",")]
        # Older fixed test runners and some NVIDIA versions omit driver_version.
        if len(fields) == 5:
            fields.insert(2, "")
        if len(fields) == 6:
            devices.append(dict(zip(
                ("index", "name", "driver_version", "memory_total_mib", "memory_free_mib", "utilization_percent"),
                fields, strict=True,
            )))
    return devices


class HostReadAdapter:
    def __init__(self, runner: FixedCommandRunner | None = None):
        self.runner = runner or FixedCommandRunner(max_capture_bytes=256 * 1024)

    def capacity(self, *, include_gpu: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {"status": "ok", "source": "host_read", "warnings": []}
        try:
            values = host_memory_values()
            # Preserve the existing compact HostReadAdapter contract.
            result["memory"] = {key.lower(): value for key, value in values.items()}
        except OSError:
            result["warnings"].append("host_memory_unavailable")
        if include_gpu:
            try:
                result["accelerators"] = [{
                    "logical_device": f"gpu-{index}", "name": device["name"],
                    "memory_total_mib": device["memory_total_mib"],
                    "memory_free_mib": device["memory_free_mib"],
                    "utilization_percent": device["utilization_percent"],
                } for index, device in enumerate(host_gpu_summary(self.runner))]
            except CommandError:
                result["warnings"].append("host_accelerator_capacity_unavailable")
        if result["warnings"]:
            result["status"] = "partial"
        return result
