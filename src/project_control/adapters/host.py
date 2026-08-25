from __future__ import annotations

from pathlib import Path
from typing import Any

from ..subprocesses import CommandError, FixedCommandRunner


class HostReadAdapter:
    def __init__(self, runner: FixedCommandRunner | None = None):
        self.runner = runner or FixedCommandRunner(max_capture_bytes=256 * 1024)

    def capacity(self, *, include_gpu: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {"status": "ok", "source": "host_read", "warnings": []}
        try:
            meminfo = Path("/proc/meminfo").read_text(encoding="ascii")
            values = {}
            for line in meminfo.splitlines():
                key, _, value = line.partition(":")
                if key in {"MemTotal", "MemAvailable"}:
                    values[key.lower()] = value.strip()
            result["memory"] = values
        except OSError:
            result["warnings"].append("host_memory_unavailable")
        if include_gpu:
            try:
                raw = self.runner.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=index,name,memory.total,memory.free,utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    cwd=Path("/"),
                    timeout=2.0,
                ).stdout
                devices = []
                for line in raw.splitlines():
                    fields = [field.strip() for field in line.split(",")]
                    if len(fields) == 5:
                        devices.append({
                            "logical_device": f"gpu-{len(devices)}",
                            "name": fields[1],
                            "memory_total_mib": fields[2],
                            "memory_free_mib": fields[3],
                            "utilization_percent": fields[4],
                        })
                result["accelerators"] = devices
            except CommandError:
                result["warnings"].append("host_accelerator_capacity_unavailable")
        if result["warnings"]:
            result["status"] = "partial"
        return result
