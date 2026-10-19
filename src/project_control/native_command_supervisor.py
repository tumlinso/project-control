"""Per-command Linux child subreaper for native Todo verification effects.

This module runs in a dedicated process, never in the Project Control server.
Its private control report is separate from the authored command's stdout and
stderr. Success requires the kernel to report ECHILD after all adopted
descendants have exited or been reaped.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time
from typing import Any


_PROTOCOL = "project-control.todo-command-supervisor"
_VERSION = 1
_MAX_REQUEST_BYTES = 1024 * 1024
_CLEANUP_MS = 5000


def _report(fd: int, *, state: str, exit_code: int | None = None,
            timed_out: bool = False, tree_quiescent: bool = False,
            reason: str | None = None, stdout_digest: str | None = None,
            stderr_digest: str | None = None) -> None:
    payload = {
        "protocol": _PROTOCOL, "version": _VERSION, "state": state,
        "exit_code": exit_code, "timed_out": timed_out,
        "tree_quiescent": tree_quiescent, "reason": reason,
        "stdout_digest": stdout_digest, "stderr_digest": stderr_digest,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
    view = memoryview(encoded)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _enable_child_subreaper() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    # Linux PR_SET_CHILD_SUBREAPER. The dedicated helper adopts orphaned
    # descendants even when they call setsid() or double-fork.
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        return False
    # The authored command runs under the same uid. Make the helper
    # non-dumpable so it cannot inspect or open this process's private report
    # descriptor through /proc/<pid>/fd and forge a quiescence receipt.
    if libc.prctl(4, 0, 0, 0, 0) != 0:
        return False
    return True


def _no_children_now() -> bool:
    """Reap exited children and return true only when waitpid reports ECHILD."""
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return True
        if pid == 0:
            return False


def _direct_children() -> tuple[int, ...] | None:
    children_path = Path("/proc") / str(os.getpid()) / "task" / str(os.getpid()) / "children"
    try:
        content = children_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    if not content:
        return ()
    try:
        return tuple(int(item) for item in content.split())
    except ValueError:
        return None


def _wait_for_children(deadline: float) -> bool:
    while True:
        if _no_children_now():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def _kill_and_reap_children(deadline: float) -> bool:
    """Kill this isolated helper's remaining direct/adopted descendants."""
    while True:
        if _no_children_now():
            return True
        children = _direct_children()
        if children is None:
            return False
        for child_pid in children:
            try:
                # These are still our unreaped direct children, so their PIDs
                # cannot have been reused between enumeration and delivery.
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                return False
        if time.monotonic() >= deadline:
            return _no_children_now()
        time.sleep(0.01)


def _kill_target_group(target_pid: int) -> None:
    try:
        os.killpg(target_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        pass


def _run_command_tree(request: dict[str, Any], control_fd: int) -> int:
    """Run argv inside the already isolated command namespace."""
    if not _enable_child_subreaper():
        _report(control_fd, state="unavailable", reason="child_subreaper_unavailable")
        return 1

    argv = request["argv"]
    timeout_ms = request["timeout_ms"]
    cleanup_ms = request["cleanup_ms"]
    deadline = time.monotonic() + timeout_ms / 1000
    try:
        target = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, start_new_session=True,
        )
    except OSError:
        _report(control_fd, state="not_started", reason="command_launch_failed")
        return 1

    exit_code: int | None = None
    timed_out = False
    tree_quiescent = False
    digests: dict[str, str | None] = {"stdout": None, "stderr": None}

    def digest_stream(stream, name: str) -> None:
        digest = hashlib.sha256()
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            digests[name] = digest.hexdigest()
        except OSError:
            digests[name] = None
        finally:
            try:
                stream.close()
            except OSError:
                pass

    assert target.stdout is not None and target.stderr is not None
    stdout_thread = threading.Thread(target=digest_stream, args=(target.stdout, "stdout"), daemon=True)
    stderr_thread = threading.Thread(target=digest_stream, args=(target.stderr, "stderr"), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            exit_code = target.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_target_group(target.pid)
            try:
                exit_code = target.wait(timeout=cleanup_ms / 1000)
            except subprocess.TimeoutExpired:
                exit_code = None

        if exit_code is None:
            tree_quiescent = _kill_and_reap_children(time.monotonic() + cleanup_ms / 1000)
        else:
            tree_quiescent = _wait_for_children(deadline)
            if not tree_quiescent:
                timed_out = True
                _kill_target_group(target.pid)
                tree_quiescent = _kill_and_reap_children(time.monotonic() + cleanup_ms / 1000)

        if time.monotonic() > deadline:
            timed_out = True
        stdout_thread.join(timeout=cleanup_ms / 1000)
        stderr_thread.join(timeout=cleanup_ms / 1000)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            tree_quiescent = False
        state = "completed" if tree_quiescent and exit_code is not None else "unresolved"
        _report(
            control_fd, state=state,
            exit_code=None if timed_out else exit_code,
            timed_out=timed_out, tree_quiescent=tree_quiescent,
            stdout_digest=digests["stdout"], stderr_digest=digests["stderr"],
            reason=None if state == "completed" else "descendant_quiescence_unconfirmed",
        )
        return 0 if state == "completed" else 1
    except BaseException:
        _kill_target_group(target.pid)
        tree_quiescent = _kill_and_reap_children(time.monotonic() + cleanup_ms / 1000)
        try:
            _report(control_fd, state="unresolved", timed_out=timed_out,
                    tree_quiescent=tree_quiescent,
                    reason="supervisor_internal_error")
        except OSError:
            pass
        return 1


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _add_destination_directories(args: list[str], destination: Path,
                                 created: set[Path], *, include_leaf: bool = True) -> None:
    """Create only placeholder parents needed for an isolated bind mount."""
    path = Path(os.path.abspath(destination))
    for parent in reversed(path.parents):
        if parent == Path("/") or parent == Path("/usr") or parent in created:
            continue
        if _path_is_within(parent, Path("/usr")):
            continue
        if parent in {Path("/home"), Path("/tmp"), Path("/run")}:
            continue
        args.extend(("--dir", str(parent)))
        created.add(parent)
    if (include_leaf and path != Path("/") and path not in created
            and not _path_is_within(path, Path("/usr"))):
        args.extend(("--dir", str(path)))
        created.add(path)


def _sandbox_command(request: dict[str, Any]) -> list[str]:
    """Build a fail-closed bubblewrap invocation for one authored command."""
    bwrap = shutil.which("bwrap", path="/usr/local/bin:/usr/bin:/bin")
    if bwrap is None:
        raise FileNotFoundError("bubblewrap is unavailable")

    workspace = Path(request["workspace"]).resolve(strict=True)
    cwd = Path(request["cwd"]).resolve(strict=True)
    runtime_alias = Path(request["runtime_prefix"])
    if not runtime_alias.is_absolute():
        raise ValueError("runtime prefix must be absolute")
    runtime_source = runtime_alias.resolve(strict=True)
    helper = Path(__file__).resolve(strict=True)
    repository_value = request.get("repository_root")
    repository = None if repository_value is None else Path(repository_value).resolve(strict=True)

    if not workspace.is_dir() or not cwd.is_dir() or (cwd != workspace and workspace not in cwd.parents):
        raise ValueError("workspace or working directory is invalid")
    # /usr is the only broad host filesystem mount. Never allow it to contain
    # the registered repository itself. The runtime prefix is an explicit
    # trusted toolchain subtree, but may not enclose repository/workspace data.
    if repository is not None and (repository == Path("/usr") or Path("/usr") in repository.parents):
        raise ValueError("registered repository is under the exposed system toolchain")
    if repository is not None and (repository == runtime_source or runtime_source in repository.parents):
        raise ValueError("runtime prefix would expose the registered repository")
    if workspace == runtime_source or runtime_source in workspace.parents:
        raise ValueError("runtime prefix would expose the writable command workspace")
    if workspace == Path("/usr") or Path("/usr") in workspace.parents:
        raise ValueError("command workspace is under the exposed system toolchain")

    argv = list(request["argv"])
    raw_executable = Path(argv[0])
    if raw_executable.is_absolute():
        host_command_path = raw_executable
    elif "/" in argv[0]:
        host_command_path = Path.cwd() / raw_executable
    else:
        trusted_path = ":".join((str(runtime_alias / "bin"), "/usr/local/bin", "/usr/bin", "/bin"))
        found = shutil.which(argv[0], path=trusted_path)
        if found is None:
            raise FileNotFoundError("authored command executable is unavailable")
        host_command_path = Path(found)
        # Resolve bare names against only the configured runtime and system
        # tools, then make the selected path explicit in the isolated argv.
        argv[0] = str(host_command_path)
    host_executable = host_command_path.resolve(strict=True)
    if not host_executable.is_file() or not os.access(host_executable, os.X_OK):
        raise ValueError("authored command executable is not a regular executable file")

    args = [
        bwrap, "--die-with-parent", "--unshare-all", "--unshare-user", "--new-session",
        "--cap-drop", "ALL", "--disable-userns", "--assert-userns-disabled",
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/sbin", "/sbin",
        "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--tmpfs", "/run", "--tmpfs", "/home",
        "--dir", "/home/todo", "--dir", "/run/todo",
        "--clearenv",
        "--setenv", "HOME", "/home/todo",
        "--setenv", "PATH",
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:" + str(runtime_alias / "bin"),
        "--setenv", "LANG", "C",
        "--setenv", "LC_ALL", "C",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "XDG_CONFIG_HOME", "/tmp/xdg-config",
        "--setenv", "XDG_CACHE_HOME", "/tmp/xdg-cache",
        "--setenv", "XDG_DATA_HOME", "/tmp/xdg-data",
        "--setenv", "XDG_STATE_HOME", "/tmp/xdg-state",
        "--setenv", "XDG_RUNTIME_DIR", "/run/todo",
        "--setenv", "PYTHONNOUSERSITE", "1",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
    ]
    created: set[Path] = {Path("/home/todo"), Path("/run/todo")}

    # The helper interpreter and its exact runtime prefix are trusted. Bind
    # the resolved source at sys.prefix's original spelling so a symlinked
    # virtualenv alias continues to work inside the container.
    if not _path_is_within(runtime_source, Path("/usr")):
        _add_destination_directories(args, runtime_alias, created)
        args.extend(("--ro-bind", str(runtime_source), str(runtime_alias)))

    # The supervisor source can come from an editable checkout, so expose this
    # single trusted file rather than mounting the Project Control repository.
    if not (_path_is_within(helper, Path("/usr"))
            or _path_is_within(helper, runtime_source)
            or _path_is_within(helper, workspace)):
        _add_destination_directories(args, helper, created, include_leaf=False)
        args.extend(("--ro-bind", str(helper), str(helper)))

    if _path_is_within(host_command_path, runtime_alias):
        # Preserve the original virtualenv spelling (often a symlink alias)
        # that is mounted at runtime_alias below.
        argv[0] = str(host_command_path)
    elif _path_is_within(host_executable, runtime_source):
        argv[0] = str(runtime_alias / host_executable.relative_to(runtime_source))
    elif _path_is_within(host_executable, Path("/usr")) or _path_is_within(host_executable, workspace):
        argv[0] = str(host_executable)
    else:
        # Do not bind arbitrary host executables: a chosen executable could
        # itself be a credential or authority file disguised as a script.
        raise FileNotFoundError("authored command executable is outside trusted mounts")

    # Workspace is the only host-writable mount. Parent directories are empty
    # placeholders where a private XDG state path normally lives.
    _add_destination_directories(args, workspace, created)
    args.extend(("--bind", str(workspace), str(workspace),
                 "--chdir", str(cwd),
                 "--", sys.executable, "-S", str(helper), "--sandbox"))
    return args


def _run_request(request: dict[str, Any], control_fd: int) -> int:
    """Run the contained sandbox helper, then independently require ECHILD."""
    if not sys.platform.startswith("linux") or not _enable_child_subreaper():
        _report(control_fd, state="unavailable", reason="linux_sandbox_unavailable")
        return 1
    try:
        command = _sandbox_command(request)
    except FileNotFoundError as error:
        reason = "bubblewrap_unavailable" if "bubblewrap" in str(error) else "command_executable_unavailable"
        _report(control_fd, state="unavailable", reason=reason)
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        _report(control_fd, state="unavailable", reason="command_sandbox_configuration_invalid")
        return 1

    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, start_new_session=True,
        )
        payload = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        deadline = time.monotonic() + (request["timeout_ms"] + request["cleanup_ms"] + 5000) / 1000
        try:
            stdout, stderr = process.communicate(
                payload, timeout=max(0.0, deadline - time.monotonic()),
            )
        except subprocess.TimeoutExpired:
            _kill_target_group(process.pid)
            _kill_and_reap_children(time.monotonic() + request["cleanup_ms"] / 1000)
            try:
                process.communicate(timeout=request["cleanup_ms"] / 1000)
            except subprocess.TimeoutExpired:
                pass
            _report(control_fd, state="unresolved", timed_out=True,
                    reason="sandbox_supervisor_stop_unconfirmed")
            return 1
        if len(stdout) > 65536 or len(stderr) > 65536:
            _report(control_fd, state="unresolved", reason="sandbox_supervisor_output_too_large")
            return 1
        # bubblewrap can briefly leave its PID-namespace init/reaper exiting
        # after the command helper has produced its private receipt. Do not
        # mistake that teardown window for an escaped process, but continue to
        # require ECHILD before accepting the receipt.
        if not _wait_for_children(time.monotonic() + request["cleanup_ms"] / 1000):
            _report(control_fd, state="unresolved", reason="sandbox_descendant_quiescence_unconfirmed")
            return 1
        try:
            inner = json.loads(stdout)
        except (UnicodeError, json.JSONDecodeError):
            _report(control_fd, state="unresolved", reason="sandbox_supervisor_report_unconfirmed")
            return 1
        if (not isinstance(inner, dict) or inner.get("protocol") != _PROTOCOL
                or inner.get("version") != _VERSION
                or type(inner.get("tree_quiescent")) is not bool
                or type(inner.get("timed_out")) is not bool
                or any(value is not None and (not isinstance(value, str) or len(value) != 64)
                       for value in (inner.get("stdout_digest"), inner.get("stderr_digest")))):
            _report(control_fd, state="unresolved", reason="sandbox_supervisor_report_invalid")
            return 1
        return_code = process.returncode
        if inner.get("state") in {"not_started", "unavailable"} and return_code != 0:
            _report(control_fd, state=inner["state"], reason=inner.get("reason"))
            return 1
        if (inner.get("state") != "completed" or return_code != 0
                or inner.get("tree_quiescent") is not True):
            _report(control_fd, state="unresolved", timed_out=inner["timed_out"],
                    tree_quiescent=inner["tree_quiescent"], reason=inner.get("reason"))
            return 1
        _report(control_fd, state="completed", exit_code=inner.get("exit_code"),
                timed_out=inner["timed_out"], tree_quiescent=True,
                stdout_digest=inner.get("stdout_digest"), stderr_digest=inner.get("stderr_digest"))
        return 0
    except (BrokenPipeError, OSError, subprocess.SubprocessError):
        if process is not None and process.poll() is None:
            _kill_target_group(process.pid)
            _kill_and_reap_children(time.monotonic() + request["cleanup_ms"] / 1000)
        try:
            _report(control_fd, state="unresolved", reason="sandbox_supervisor_protocol_failed")
        except OSError:
            pass
        return 1
def _read_request() -> dict[str, Any] | None:
    raw = sys.stdin.buffer.readline(_MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > _MAX_REQUEST_BYTES:
        return None
    request = json.loads(raw)
    required = {"argv", "timeout_ms", "cleanup_ms"}
    outer = required | {"workspace", "cwd", "runtime_prefix", "repository_root"}
    if (not isinstance(request, dict) or set(request) not in (required, outer)
            or not isinstance(request["argv"], list) or not request["argv"]
            or any(not isinstance(arg, str) for arg in request["argv"])
            or type(request["timeout_ms"]) is not int or request["timeout_ms"] <= 0
            or type(request["cleanup_ms"]) is not int or request["cleanup_ms"] != _CLEANUP_MS):
        return None
    if set(request) == outer and any(not isinstance(request[key], str) for key in (
        "workspace", "cwd", "runtime_prefix",
    )):
        return None
    if set(request) == outer and request["repository_root"] is not None and not isinstance(
        request["repository_root"], str
    ):
        return None
    return request

    exit_code: int | None = None
    timed_out = False
    tree_quiescent = False
    try:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            exit_code = target.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_target_group(target.pid)
            try:
                exit_code = target.wait(timeout=cleanup_ms / 1000)
            except subprocess.TimeoutExpired:
                exit_code = None

        if exit_code is None:
            tree_quiescent = _kill_and_reap_children(time.monotonic() + cleanup_ms / 1000)
        else:
            tree_quiescent = _wait_for_children(deadline)
            if not tree_quiescent:
                timed_out = True
                _kill_target_group(target.pid)
                tree_quiescent = _kill_and_reap_children(time.monotonic() + cleanup_ms / 1000)

        if time.monotonic() > deadline:
            timed_out = True
        state = "completed" if tree_quiescent and exit_code is not None else "unresolved"
        _report(
            control_fd, state=state,
            exit_code=None if timed_out else exit_code,
            timed_out=timed_out, tree_quiescent=tree_quiescent,
            reason=None if state == "completed" else "descendant_quiescence_unconfirmed",
        )
        return 0 if state == "completed" else 1
    except BaseException:
        _kill_target_group(target.pid)
        tree_quiescent = _kill_and_reap_children(time.monotonic() + cleanup_ms / 1000)
        try:
            _report(control_fd, state="unresolved", timed_out=timed_out,
                    tree_quiescent=tree_quiescent,
                    reason="supervisor_internal_error")
        except OSError:
            pass
        return 1


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--sandbox":
        try:
            control_fd = sys.stdout.fileno()
            request = _read_request()
            if request is None or set(request) != {
                "argv", "timeout_ms", "cleanup_ms", "workspace", "cwd", "runtime_prefix", "repository_root",
            }:
                _report(control_fd, state="not_started", reason="supervisor_request_invalid")
                return 2
            return _run_command_tree(request, control_fd)
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return 2
    if len(sys.argv) != 2:
        return 2
    try:
        control_fd = int(sys.argv[1])
        request = _read_request()
        if request is None:
            _report(control_fd, state="not_started", reason="supervisor_request_invalid")
            return 2
        return _run_request(request, control_fd)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
