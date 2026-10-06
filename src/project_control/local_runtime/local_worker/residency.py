"""Read-only physical observations; ownership remains in the host interlock."""
from __future__ import annotations

import subprocess
import os
from pathlib import Path
import signal
import time
import select
import ctypes


def _open_pidfd(pid: int) -> int:
    if hasattr(os, "pidfd_open"):
        return os.pidfd_open(pid)
    # Some installed Python builds omit the wrapper while libc exposes the
    # same kernel primitive. Never fall back to numeric syscalls or PID signals.
    libc = ctypes.CDLL(None, use_errno=True)
    operation = getattr(libc, "pidfd_open", None)
    if operation is None:
        raise ValueError("owned_process_pidfd_unavailable")
    operation.argtypes = [ctypes.c_int, ctypes.c_uint]
    operation.restype = ctypes.c_int
    descriptor = operation(pid, 0)
    if descriptor < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return descriptor


def process_identity(pid: int) -> dict:
    """Identity strong enough to reject PID reuse before signaling a child."""
    stat = (Path("/proc") / str(pid) / "stat").read_text()
    fields = stat[stat.rfind(")") + 2:].split()
    return {"pid": pid, "process_start": fields[19], "process_group": int(fields[2]),
            "executable": str((Path("/proc") / str(pid) / "exe").resolve(strict=True)),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}


def terminate_owned(identity: dict, *, timeout: float = 10) -> None:
    if identity["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
        raise ValueError("owned_process_boot_mismatch")
    if not hasattr(signal, "pidfd_send_signal"):
        raise ValueError("owned_process_pidfd_unavailable")
    try:
        descriptor = _open_pidfd(identity["pid"])
    except ProcessLookupError:
        if not (Path("/proc") / str(identity["pid"])).exists():
            return
        raise ValueError("owned_process_identity_unavailable")
    try:
        if process_identity(identity["pid"]) != identity or identity["process_group"] != identity["pid"]:
            raise ValueError("owned_process_identity_mismatch")
        for task in (Path("/proc") / str(identity["pid"]) / "task").iterdir():
            if (task / "children").read_text().strip():
                raise ValueError("owned_process_descendants_unsupported")
        poll = select.poll()
        poll.register(descriptor, select.POLLIN)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if poll.poll(0):
                break
            signal.pidfd_send_signal(descriptor, sig)
            if poll.poll(max(0, int(timeout * 1000))):
                break
        else:
            raise ValueError("owned_process_wait_failed")
        try:
            os.waitpid(identity["pid"], os.WNOHANG)
        except ChildProcessError:
            pass
    finally:
        os.close(descriptor)


def observe_residency(uuids: list[str]) -> dict:
    observed_unix = time.time()
    try:
        devices = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True)
        processes = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True)
        selected = set(uuids)
        rows = []
        for line in devices.stdout.splitlines():
            gpu, memory = (part.strip() for part in line.split(","))
            if gpu in selected:
                rows.append({"uuid": gpu, "memory_used_mib": float(memory)})
        apps = []
        for line in processes.stdout.splitlines():
            gpu, pid = (part.strip() for part in line.split(","))
            if gpu in selected:
                apps.append({"uuid": gpu, "pid": int(pid)})
        return {"available": {r["uuid"] for r in rows} == selected,
                "devices": rows, "processes": apps, "observed_unix": observed_unix}
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {"available": False, "devices": [], "processes": [], "reason": str(error)[:300],
                "observed_unix": observed_unix}


def memory_snapshot(observation: dict, uuids: list[str]) -> dict[str, float]:
    if observation.get("available") is not True:
        raise ValueError("residency_observation_unavailable")
    memory = {row["uuid"]: float(row["memory_used_mib"]) for row in observation["devices"]}
    if set(memory) != set(uuids):
        raise ValueError("residency_observation_incomplete")
    return memory
