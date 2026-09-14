"""Bounded arbitrary read-only command execution for the local investigator."""
from __future__ import annotations

import os
import selectors
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from ..security import SecurityError, redact_text
from ..terminal import BubblewrapSandbox

MAX_OUTPUT_BYTES = 16 * 1024
MAX_ARG_BYTES = 12 * 1024
_PRIVATE_DIRS = (
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".codex",
    ".password-store", ".mozilla", ".config/google-chrome", ".config/chromium",
    ".config/gh", ".config/gcloud", ".config/rclone", ".local/share/keyrings",
)
_PRIVATE_FILES = (
    ".git-credentials", ".netrc", ".npmrc", ".pypirc",
)


def _valid_argv(argv: Any) -> list[str]:
    if not isinstance(argv, list) or not 1 <= len(argv) <= 64:
        raise SecurityError("readonly_exec_argv_invalid")
    if any(not isinstance(item, str) or not item or "\0" in item or len(item.encode()) > 2048 for item in argv):
        raise SecurityError("readonly_exec_argv_invalid")
    if sum(len(item.encode()) for item in argv) > MAX_ARG_BYTES:
        raise SecurityError("readonly_exec_argv_too_large")
    return argv


def _mask(command: list[str], path: Path, *, directory: bool) -> None:
    try:
        if not path.exists() and not path.is_symlink():
            return
    except OSError:
        return
    command.extend(("--tmpfs", str(path)) if directory else ("--ro-bind", "/dev/null", str(path)))


def _sandbox_command(argv: list[str], cwd: Path, sandbox: BubblewrapSandbox) -> list[str]:
    if not sandbox.probe():
        raise SecurityError("readonly_exec_sandbox_unavailable")
    command = [
        str(sandbox.executable), "--die-with-parent", "--unshare-all", "--new-session",
        "--cap-drop", "ALL", "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--tmpfs", "/run", "--dir", "/tmp/home", "--chdir", str(cwd),
    ]
    homes = [Path("/root")]
    try:
        homes.extend(path for path in Path("/home").iterdir() if path.is_dir())
    except OSError:
        pass
    for home in homes:
        for relative in _PRIVATE_DIRS:
            _mask(command, home / relative, directory=True)
        for relative in _PRIVATE_FILES:
            _mask(command, home / relative, directory=False)
    command.extend((
        "--clearenv", "--setenv", "HOME", "/tmp/home", "--setenv", "TMPDIR", "/tmp",
        "--setenv", "PATH", "/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--",
        "/usr/bin/prlimit", "--cpu=8", "--as=1073741824", "--nproc=128", "--nofile=128",
        "--fsize=16777216", "--", *argv,
    ))
    return command


def exec_readonly(argv: Any, *, cwd: str | None, default_cwd: Path,
                  timeout_seconds: float = 5.0, output_limit: int = MAX_OUTPUT_BYTES) -> dict[str, Any]:
    """Execute one argv in a read-only, no-network host view with capped streaming output."""
    arguments = _valid_argv(argv)
    working = Path(cwd).expanduser() if cwd is not None else default_cwd
    if not working.is_absolute() or not working.resolve().is_dir():
        raise SecurityError("readonly_exec_cwd_invalid")
    working = working.resolve()
    timeout = min(15.0, max(0.1, float(timeout_seconds)))
    cap = min(MAX_OUTPUT_BYTES, max(1024, int(output_limit)))
    command = _sandbox_command(arguments, working, BubblewrapSandbox())
    started = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True, env={"PATH": "/usr/bin:/bin"})
    selector = selectors.DefaultSelector()
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    for stream in streams:
        if stream is not None:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
    status = "ok"
    total = 0
    try:
        while selector.get_map():
            if time.monotonic() - started >= timeout:
                status = "timeout"
                break
            for key, _ in selector.select(0.05):
                chunk = os.read(key.fileobj.fileno(), min(4096, cap - total + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                remaining = max(0, cap - total)
                streams[key.fileobj].extend(chunk[:remaining])
                total += len(chunk)
                if total > cap:
                    status = "output_limit"
                    break
            if status != "ok" or (process.poll() is not None and not selector.get_map()):
                break
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=0.35)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1)
        selector.close()
    stdout = redact_text(bytes(streams[process.stdout]).decode("utf-8", "replace")) if process.stdout else ""
    stderr = redact_text(bytes(streams[process.stderr]).decode("utf-8", "replace")) if process.stderr else ""
    if process.stdout:
        process.stdout.close()
    if process.stderr:
        process.stderr.close()
    return {"status": status, "argv": arguments, "cwd": str(working),
            "returncode": process.returncode, "stdout": stdout, "stderr": stderr,
            "output_truncated": status == "output_limit",
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}
