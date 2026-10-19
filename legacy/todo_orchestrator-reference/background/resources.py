"""Host-envelope and generic sidecar resource helpers."""

from __future__ import annotations

import os
import resource
from pathlib import Path


def _read_int(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
        return None if value == "max" else int(value)
    except (OSError, ValueError):
        return None


def cpu_capacity() -> int:
    affinity = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    quota = None
    try:
        raw_quota, raw_period = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="utf-8").split()
        if raw_quota != "max":
            quota = max(1, int(raw_quota) // max(1, int(raw_period)))
    except (OSError, ValueError):
        pass
    return max(1, min(affinity, quota or affinity))


def memory_capacity_bytes() -> int:
    cgroup = _read_int(Path("/sys/fs/cgroup/memory.max"))
    physical = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    return min(physical, cgroup) if cgroup else physical


def available_memory_bytes() -> int:
    try:
        fields = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            fields[key] = int(value.strip().split()[0]) * 1024
        host_available = fields["MemAvailable"]
    except (OSError, KeyError, ValueError):
        host_available = memory_capacity_bytes()
    limit = _read_int(Path("/sys/fs/cgroup/memory.max"))
    current = _read_int(Path("/sys/fs/cgroup/memory.current"))
    if limit is not None and current is not None:
        host_available = min(host_available, max(0, limit - current))
    return host_available


def cpp_context_active() -> bool:
    own = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == own:
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(token in command for token in ("/ctxpp ", "ctxpp-core", "ctxpp-libtooling-core", "ctxpp_runtime.py")):
            return True
    return False


def background_launch_allowed(request: dict[str, object]) -> bool:
    if not bool(request.get("cpu_heavy", False)):
        return True
    if cpp_context_active():
        return False
    total = memory_capacity_bytes()
    reserve = max(2 * 1024**3, int(total * 0.20))
    requested = int(request.get("ram_bytes", 0) or 0)
    if available_memory_bytes() - requested < reserve:
        return False
    try:
        load = os.getloadavg()[0]
    except OSError:
        load = 0.0
    usable = max(1, int(cpu_capacity() * 0.75))
    return load < usable


def background_environment(request: dict[str, object]) -> dict[str, str]:
    capacity = cpu_capacity()
    requested = int(request.get("cpu_threads", 0) or 0)
    limit = max(1, min(requested or max(1, int(capacity * 0.75)), max(1, int(capacity * 0.75))))
    return {
        "OMP_NUM_THREADS": str(limit),
        "CMAKE_BUILD_PARALLEL_LEVEL": str(limit),
        "MAKEFLAGS": f"-j{limit}",
    }


def lower_process_priority(pid: int) -> None:
    """Lower a child after spawn without using unsafe threaded preexec hooks."""
    try:
        os.setpriority(os.PRIO_PROCESS, pid, 10)
    except (AttributeError, OSError):
        pass
    try:
        resource.prlimit(pid, resource.RLIMIT_CORE, (0, 0))
    except (AttributeError, ProcessLookupError, ValueError, OSError):
        pass
    try:
        import ctypes

        libc = ctypes.CDLL(None)
        libc.syscall(251, 1, 7, pid)  # ioprio_set: process, idle class
    except Exception:
        pass
