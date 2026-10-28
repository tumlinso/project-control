#!/usr/bin/env python3
"""Emit a read-only CORE4 host capability manifest."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


WEIGHT_SUFFIXES = {".gguf", ".safetensors"}
DEFAULT_CACHE_PATHS = (
    Path.home() / ".cache/huggingface/hub",
    Path.home() / ".local/share/core4/models",
    Path.home() / "models",
)


def _run(argv: list[str], timeout: float = 10.0) -> dict[str, Any]:
    try:
        process = subprocess.run(argv, text=True, capture_output=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"argv": argv, "returncode": None, "stdout": "", "stderr": str(error)}
    return {
        "argv": argv,
        "returncode": process.returncode,
        "stdout": process.stdout.strip(),
        "stderr": process.stderr.strip(),
    }


def _which(name: str) -> str | None:
    return shutil.which(name)


def parse_gpu_csv(text: str) -> list[dict[str, Any]]:
    fields = ("index", "name", "uuid", "memory_total_mib", "memory_free_mib", "compute_capability", "driver_version")
    records = []
    for line in text.splitlines():
        values = [value.strip() for value in line.split(",")]
        if len(values) != len(fields):
            continue
        record = dict(zip(fields, values))
        try:
            record["index"] = int(record["index"])
            record["memory_total_mib"] = int(record["memory_total_mib"])
            record["memory_free_mib"] = int(record["memory_free_mib"])
        except ValueError:
            continue
        record["architecture"] = "sm_" + record["compute_capability"].replace(".", "")
        records.append(record)
    return records


def parse_meminfo(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"^(MemTotal|MemAvailable):\s+(\d+)\s+kB$", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    return {"total_bytes": values.get("MemTotal", 0), "available_bytes": values.get("MemAvailable", 0)}


def _storage(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    probe = resolved
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    stat = os.statvfs(probe)
    return {
        "requested_path": str(resolved),
        "measured_at": str(probe),
        "exists": resolved.exists(),
        "total_bytes": stat.f_blocks * stat.f_frsize,
        "available_bytes": stat.f_bavail * stat.f_frsize,
    }


def _scan_cache(path: Path, *, max_files: int = 200, max_depth: int = 4) -> dict[str, Any]:
    resolved = path.resolve()
    files: list[dict[str, Any]] = []
    total_bytes = 0
    if resolved.is_dir():
        root_depth = len(resolved.parts)
        for current, directories, names in os.walk(resolved):
            current_path = Path(current)
            if len(current_path.parts) - root_depth >= max_depth:
                directories[:] = []
            for name in sorted(names):
                candidate = current_path / name
                if candidate.suffix.lower() not in WEIGHT_SUFFIXES:
                    continue
                try:
                    size = candidate.stat().st_size
                except OSError:
                    continue
                total_bytes += size
                if len(files) < max_files:
                    files.append({"path": str(candidate), "bytes": size})
    return {
        "path": str(resolved),
        "exists": resolved.is_dir(),
        "weight_file_count": len(files),
        "weight_bytes_listed": total_bytes,
        "truncated": len(files) >= max_files,
        "weight_files": files,
    }


def _tool(name: str, version_argv: Iterable[str] | None = None) -> dict[str, Any]:
    binary = _which(name)
    if binary is None:
        return {"available": False, "binary": None, "version": None}
    command = [binary, *(version_argv or ("--version",))]
    result = _run(command)
    combined = "\n".join(part for part in (result["stdout"], result["stderr"]) if part)
    return {
        "available": True,
        "binary": binary,
        "version": combined[:2000] or None,
        "version_returncode": result["returncode"],
    }


def inspect_host(cache_paths: Iterable[Path] = DEFAULT_CACHE_PATHS) -> dict[str, Any]:
    gpu_query = _run([
        "nvidia-smi", "--query-gpu=index,name,uuid,memory.total,memory.free,compute_cap,driver_version",
        "--format=csv,noheader,nounits",
    ]) if _which("nvidia-smi") else {"stdout": "", "returncode": None, "stderr": "nvidia-smi unavailable"}
    topology = _run(["nvidia-smi", "topo", "-m"]) if _which("nvidia-smi") else {"stdout": "", "returncode": None, "stderr": "nvidia-smi unavailable"}
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        meminfo = ""
    caches = [_scan_cache(path) for path in cache_paths]
    gpus = parse_gpu_csv(str(gpu_query.get("stdout", "")))
    nvcc = _tool("nvcc")
    nvcc_version = str(nvcc.get("version") or "")
    return {
        "format": "CORE4-HOST-MANIFEST/1",
        "schema_version": 1,
        "observed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "hostname": socket.gethostname(),
        "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "ram": parse_meminfo(meminfo),
        "storage": [_storage(Path.cwd()), _storage(Path.home() / ".local/share/core4/models")],
        "gpus": gpus,
        "topology": {
            "discovery_command": ["nvidia-smi", "topo", "-m"],
            "returncode": topology.get("returncode"),
            "matrix": topology.get("stdout", ""),
            "stderr": topology.get("stderr", ""),
            "hard_coded_bundles": False,
        },
        "cuda": {
            "nvcc": nvcc,
            "volta_build_target": "sm_70",
            "explicit_cuda_12x_available": bool(nvcc["available"] and re.search(r"release 12\.", nvcc_version)),
            "driver_report_is_not_compute_capability": True,
        },
        "adapters": {
            "llama_cpp_server": _tool("llama-server"),
            "qwen_code": _tool("qwen"),
            "codex_cli": _tool("codex"),
        },
        "model_caches": caches,
        "usable_model_weights_present": any(cache["weight_file_count"] for cache in caches),
        "inspection_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-path", action="append", default=[])
    parser.add_argument("--output")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    paths = [Path(value) for value in args.cache_path] or list(DEFAULT_CACHE_PATHS)
    manifest = inspect_host(paths)
    rendered = json.dumps(manifest, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    if args.json or not args.output:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
