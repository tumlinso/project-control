from __future__ import annotations

import codecs
import copy
import errno
import fcntl
import logging
import os
import re
import secrets
import select
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pyte

from .config import DEFAULT_DENY_PATTERNS, ProjectControlConfig
from .registry import WorkspaceRegistry
from .security import SecurityError, is_denied, resolve_registered_path


DEFAULT_ROWS = 40
DEFAULT_COLS = 120
MAX_WAIT_MS = 30_000
MAX_ROWS = 200
MAX_COLS = 400
MAX_ARG_COUNT = 64
MAX_ARG_BYTES = 8 * 1024
MAX_CAPTURE_BYTES = 1024 * 1024
MAX_SESSION_BYTES = 4 * 1024 * 1024
MAX_SCREEN_BYTES = 512 * 1024
MAX_ACTIVE_PER_WORKSPACE = 4
MAX_ACTIVE_GLOBAL = 8
MAX_SANDBOX_ENTRIES = 100_000
MAX_IDLE_SECONDS = 30 * 60
MAX_LIFETIME_SECONDS = 4 * 60 * 60
TERMINATION_GRACE_SECONDS = 0.35
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ALTERNATE_SCREEN_MODES = frozenset({47, 1047, 1049})
TERMINAL_PRIVATE_ROOTS = frozenset({".git", ".todo-orchestrator", ".ctxpp"})
LOGGER = logging.getLogger(__name__)


class TerminalError(ValueError):
    """A bounded, caller-safe terminal capability error."""


class _AlternateScreen(pyte.Screen):
    """Add xterm alternate-buffer behavior to pyte's VT state machine."""

    def __init__(self, columns: int, lines: int) -> None:
        self._primary_state: dict[str, object] | None = None
        super().__init__(columns, lines)

    def set_mode(self, *modes: int, **kwargs: object) -> None:
        alternate = bool(kwargs.get("private")) and any(mode in ALTERNATE_SCREEN_MODES for mode in modes)
        if alternate and self._primary_state is None:
            self._primary_state = copy.deepcopy({
                key: value for key, value in self.__dict__.items() if key != "_primary_state"
            })
            super().set_mode(*modes, **kwargs)
            self.reset()
            return
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes: int, **kwargs: object) -> None:
        alternate = bool(kwargs.get("private")) and any(mode in ALTERNATE_SCREEN_MODES for mode in modes)
        if alternate and self._primary_state is not None:
            columns, lines = self.columns, self.lines
            restored = self._primary_state
            self.__dict__.clear()
            self.__dict__.update(restored)
            self._primary_state = None
            if (self.columns, self.lines) != (columns, lines):
                self.resize(lines=lines, columns=columns)
            return
        super().reset_mode(*modes, **kwargs)


class TerminalEmulator:
    def __init__(self, rows: int, cols: int) -> None:
        self.screen = _AlternateScreen(cols, rows)
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def feed(self, payload: bytes, *, final: bool = False) -> None:
        text = self.decoder.decode(payload, final=final)
        if text:
            self.stream.feed(text)

    def resize(self, rows: int, cols: int) -> None:
        self.screen.resize(lines=rows, columns=cols)

    def render(self) -> str:
        lines = [line.rstrip() for line in self.screen.display]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)


def _validate_label(label: str | None) -> None:
    if label is not None and not LABEL.fullmatch(label):
        raise TerminalError("terminal_label_invalid")


def _validate_argv(argv: list[str]) -> None:
    if len(argv) > MAX_ARG_COUNT:
        raise TerminalError("terminal_argv_too_many_items")
    total = 0
    for item in argv:
        if not isinstance(item, str) or "\x00" in item or len(item.encode("utf-8")) > 1024:
            raise TerminalError("terminal_argv_invalid")
        total += len(item.encode("utf-8"))
    if total > MAX_ARG_BYTES:
        raise TerminalError("terminal_argv_too_large")


def _is_terminal_denied(relative: Path, patterns: list[str]) -> bool:
    return bool(relative.parts and relative.parts[0] in TERMINAL_PRIVATE_ROOTS) or is_denied(relative, patterns)


def _deny_masks(root: Path, patterns: list[str]) -> list[tuple[Path, bool]]:
    masks: list[tuple[Path, bool]] = []
    entries = 0
    explicit_roots = TERMINAL_PRIVATE_ROOTS
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        entries += len(directories) + len(files)
        if entries > MAX_SANDBOX_ENTRIES:
            raise TerminalError("terminal_sandbox_repository_too_large")
        kept: list[str] = []
        for name in directories:
            relative = (current_path / name).relative_to(root)
            if name in explicit_roots or _is_terminal_denied(relative, patterns):
                masks.append((relative, True))
            else:
                kept.append(name)
        directories[:] = kept
        for name in files:
            relative = (current_path / name).relative_to(root)
            if name in explicit_roots or _is_terminal_denied(relative, patterns):
                masks.append((relative, False))
        if len(masks) > 256:
            raise TerminalError("terminal_sandbox_deny_set_too_large")
    return sorted(masks, key=lambda item: item[0].as_posix())


class BubblewrapSandbox:
    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("bwrap")
        self._probe_lock = threading.Lock()
        self._probe_result: bool | None = None

    @property
    def available(self) -> bool:
        return bool(self.executable and Path(self.executable).is_file() and os.access(self.executable, os.X_OK))

    def probe(self) -> bool:
        with self._probe_lock:
            if self._probe_result is not None:
                return self._probe_result
            if not self.available:
                self._probe_result = False
                return False
            command = [
                str(self.executable), "--die-with-parent", "--unshare-all", "--new-session",
                "--cap-drop", "ALL", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            ]
            for system_path in ("/usr", "/bin", "/lib", "/lib64"):
                if Path(system_path).exists():
                    command.extend(("--ro-bind", system_path, system_path))
            if Path("/etc/ld.so.cache").exists():
                command.extend(("--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache"))
            command.extend((
                "--clearenv", "--setenv", "PATH", "/usr/bin:/bin", "--", "/usr/bin/true",
            ))
            try:
                completed = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env={"PATH": "/usr/bin:/bin"},
                    timeout=2,
                    check=False,
                )
                self._probe_result = completed.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                self._probe_result = False
            return self._probe_result

    def command(
        self,
        *,
        repository_root: Path,
        executable_relative: Path,
        cwd_relative: Path,
        argv: list[str],
        deny_patterns: list[str],
    ) -> list[str]:
        if not self.probe():
            raise TerminalError("terminal_sandbox_unavailable")
        masks = _deny_masks(repository_root, deny_patterns)
        command = [
            str(self.executable), "--die-with-parent", "--unshare-all", "--new-session",
            "--cap-drop", "ALL", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--dir", "/home", "--dir", "/home/observer",
        ]
        for system_path in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(system_path).exists():
                command.extend(("--ro-bind", system_path, system_path))
        for system_file in ("/etc/ld.so.cache", "/etc/terminfo"):
            if Path(system_file).exists():
                command.extend(("--ro-bind", system_file, system_file))
        command.extend(("--ro-bind", str(repository_root), "/repo"))
        for relative, directory in masks:
            destination = "/repo/" + relative.as_posix()
            command.extend(("--tmpfs", destination) if directory else ("--ro-bind", "/dev/null", destination))
        command.extend((
            "--chdir", "/repo/" + (cwd_relative.as_posix() if cwd_relative.parts else ""),
            "--clearenv", "--setenv", "HOME", "/home/observer", "--setenv", "TMPDIR", "/tmp",
            "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "TERM", "xterm-256color",
            "--", "/repo/" + executable_relative.as_posix(), *argv,
        ))
        return command


@dataclass(frozen=True)
class TerminalCaptureResult:
    operation: str
    screen: str
    rows: int
    cols: int
    session_id: str
    label: str | None
    active: bool
    state: str
    returncode: int | None
    wait_ms: int
    capture_ms: int
    elapsed_ms: int
    stream_limited: bool
    screen_truncated: bool

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


class TerminalSession:
    def __init__(
        self,
        *,
        workspace_id: str,
        repository_alias: str,
        label: str | None,
        rows: int,
        cols: int,
        command: list[str],
        on_retire: Callable[["TerminalSession"], None],
    ) -> None:
        self.session_id = "terminal-" + secrets.token_hex(16)
        self.workspace_id = workspace_id
        self.repository_alias = repository_alias
        self.label = label
        self.rows = rows
        self.cols = cols
        self.created_monotonic = time.monotonic()
        self.last_capture_monotonic = self.created_monotonic
        self.total_bytes = 0
        self.bytes_at_last_capture = 0
        self.stream_limited = False
        self.state = "running"
        self.returncode: int | None = None
        self._on_retire = on_retire
        self._state_lock = threading.RLock()
        self._capture_lock = threading.Lock()
        self._condition = threading.Condition(self._state_lock)
        self._emulator = TerminalEmulator(rows, cols)
        self._master, slave = os.openpty()
        self._set_winsize(slave, rows, cols)
        os.set_blocking(self._master, False)
        try:
            self._process = subprocess.Popen(
                command,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
                start_new_session=True,
                env={"PATH": "/usr/bin:/bin", "TERM": "xterm-256color"},
            )
        except Exception:
            os.close(self._master)
            raise
        finally:
            os.close(slave)
        self._thread = threading.Thread(target=self._reader, name=f"pc-terminal-{self.session_id[-8:]}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    @staticmethod
    def _set_winsize(descriptor: int, rows: int, cols: int) -> None:
        fcntl.ioctl(descriptor, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def _signal_group(self, sig: signal.Signals) -> None:
        if self._process.poll() is None:
            try:
                os.killpg(self._process.pid, sig)
            except ProcessLookupError:
                pass

    def _reader(self) -> None:
        try:
            eof = False
            force_at: float | None = None
            while not eof:
                ready, _, _ = select.select([self._master], [], [], 0.05)
                if ready:
                    try:
                        payload = os.read(self._master, 65536)
                    except BlockingIOError:
                        payload = b""
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            eof = True
                            payload = b""
                        else:
                            raise
                    if payload:
                        with self._condition:
                            self.total_bytes += len(payload)
                            if self.total_bytes > MAX_SESSION_BYTES:
                                self.stream_limited = True
                                self.state = "stream_limit"
                                self._signal_group(signal.SIGTERM)
                                force_at = force_at or time.monotonic() + TERMINATION_GRACE_SECONDS
                            self._emulator.feed(payload)
                            self._condition.notify_all()
                    elif ready:
                        eof = True
                if self._process.poll() is not None and not ready:
                    # A PTY reports EIO/EOF after all final terminal bytes drain.
                    try:
                        payload = os.read(self._master, 65536)
                    except (BlockingIOError, OSError):
                        payload = b""
                    if payload:
                        with self._condition:
                            self.total_bytes += len(payload)
                            self._emulator.feed(payload)
                            self._condition.notify_all()
                    else:
                        eof = True
                now = time.monotonic()
                with self._state_lock:
                    if self.state == "running" and (
                        now - self.created_monotonic > MAX_LIFETIME_SECONDS
                        or now - self.last_capture_monotonic > MAX_IDLE_SECONDS
                    ):
                        self.state = "expired"
                        self._signal_group(signal.SIGTERM)
                        force_at = now + TERMINATION_GRACE_SECONDS
                if force_at is not None and now >= force_at and self._process.poll() is None:
                    self._signal_group(signal.SIGKILL)
            self._process.wait(timeout=1)
        except BaseException:
            self._signal_group(signal.SIGKILL)
            try:
                self._process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        finally:
            with self._condition:
                try:
                    self._emulator.feed(b"", final=True)
                except UnicodeDecodeError:
                    pass
                self.returncode = self._process.poll()
                if self.state == "running":
                    self.state = "exited"
                try:
                    os.close(self._master)
                except OSError:
                    pass
                self._condition.notify_all()
            self._on_retire(self)

    @property
    def active(self) -> bool:
        with self._state_lock:
            return self.state == "running" and self._process.poll() is None

    def resize(self, rows: int, cols: int) -> None:
        with self._condition:
            if not self.active:
                raise TerminalError("terminal_session_not_active")
            self._set_winsize(self._master, rows, cols)
            self.rows, self.cols = rows, cols
            self._emulator.resize(rows, cols)
            self._signal_group(signal.SIGWINCH)

    def terminate(self, state: str = "killed") -> None:
        with self._condition:
            if self._process.poll() is None:
                self.state = state
                self._signal_group(signal.SIGTERM)
        self._thread.join(timeout=TERMINATION_GRACE_SECONDS)
        if self._thread.is_alive():
            self._signal_group(signal.SIGKILL)
            self._thread.join(timeout=1.0)
        if self._thread.is_alive():
            raise TerminalError("terminal_cleanup_failed")

    def capture(self, *, operation: str, wait_ms: int, kill_after_capture: bool) -> TerminalCaptureResult:
        if not self._capture_lock.acquire(blocking=False):
            raise TerminalError("terminal_session_busy")
        started = time.monotonic()
        try:
            deadline = started + wait_ms / 1000
            with self._condition:
                while self.active and time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    self._condition.wait(timeout=min(remaining, 0.05))
                    if self.total_bytes - self.bytes_at_last_capture > MAX_CAPTURE_BYTES:
                        self.stream_limited = True
                        break
            if self.stream_limited and self._process.poll() is None:
                self.terminate("stream_limit")
            elif self.state == "expired" and self._process.poll() is None:
                self.terminate("expired")
            elif kill_after_capture and self.active:
                self.terminate("killed")
            with self._condition:
                now = time.monotonic()
                screen = self._emulator.render()
                encoded_screen = screen.encode("utf-8")
                screen_truncated = len(encoded_screen) > MAX_SCREEN_BYTES
                if screen_truncated:
                    screen = encoded_screen[:MAX_SCREEN_BYTES].decode("utf-8", errors="ignore")
                result = TerminalCaptureResult(
                    operation=operation,
                    screen=screen,
                    rows=self.rows,
                    cols=self.cols,
                    session_id=self.session_id,
                    label=self.label,
                    active=self.active,
                    state=self.state,
                    returncode=self.returncode,
                    wait_ms=wait_ms,
                    capture_ms=round((now - started) * 1000),
                    elapsed_ms=round((now - self.created_monotonic) * 1000),
                    stream_limited=self.stream_limited,
                    screen_truncated=screen_truncated,
                )
                self.last_capture_monotonic = now
                self.bytes_at_last_capture = self.total_bytes
                return result
        finally:
            self._capture_lock.release()


class TerminalSessionRegistry:
    def __init__(self, config: ProjectControlConfig, sandbox: BubblewrapSandbox | None = None) -> None:
        self.config = config
        self.repositories = WorkspaceRegistry(config)
        self.sandbox = sandbox or BubblewrapSandbox()
        self._lock = threading.RLock()
        self._sessions: dict[str, TerminalSession] = {}
        self._labels: dict[tuple[str, str], str] = {}
        self._expired: dict[tuple[str, str], float] = {}

    def _retire(self, session: TerminalSession) -> None:
        LOGGER.info("terminal session retired state=%s returncode=%s", session.state, session.returncode)
        with self._lock:
            self._sessions.pop(session.session_id, None)
            if session.label:
                self._labels.pop((session.workspace_id, session.label), None)
            if session.state == "expired":
                observed = time.monotonic()
                self._expired[(session.workspace_id, session.session_id)] = observed
                if session.label:
                    self._expired[(session.workspace_id, session.label)] = observed
                if len(self._expired) > 128:
                    for key, _ in sorted(self._expired.items(), key=lambda item: item[1])[:-128]:
                        self._expired.pop(key, None)

    def _lookup(self, workspace_id: str, identity: str) -> TerminalSession:
        with self._lock:
            session_id = self._labels.get((workspace_id, identity), identity)
            session = self._sessions.get(session_id)
            if session is None or session.workspace_id != workspace_id or not session.active:
                if (workspace_id, identity) in self._expired:
                    raise TerminalError("terminal_session_expired")
                raise TerminalError("terminal_session_unknown")
            return session

    def launch(
        self,
        *,
        workspace_id: str,
        repository: str | None,
        executable: str,
        argv: list[str],
        cwd: str,
        label: str | None,
        wait_ms: int,
        rows: int | None,
        cols: int | None,
        kill_after_capture: bool,
    ) -> TerminalCaptureResult:
        _validate_label(label)
        _validate_argv(argv)
        if label and kill_after_capture:
            raise TerminalError("terminal_label_requires_bonded_session")
        rows, cols = rows or DEFAULT_ROWS, cols or DEFAULT_COLS
        registered = self.repositories.repository(workspace_id, repository)
        workspace = self.repositories.workspace(workspace_id)
        deny = [*DEFAULT_DENY_PATTERNS, *workspace.deny_patterns]
        try:
            executable_path = resolve_registered_path(registered.root, executable, deny_patterns=workspace.deny_patterns)
            executable_relative = executable_path.relative_to(registered.root.resolve(strict=True))
            if _is_terminal_denied(executable_relative, workspace.deny_patterns):
                raise SecurityError("resolved executable is denied")
            cwd_path = (
                registered.root.resolve(strict=True)
                if cwd == "."
                else resolve_registered_path(registered.root, cwd, deny_patterns=workspace.deny_patterns, require_file=False)
            )
            cwd_relative = cwd_path.relative_to(registered.root.resolve(strict=True))
            if _is_terminal_denied(cwd_relative, workspace.deny_patterns):
                raise SecurityError("resolved cwd is denied")
        except (SecurityError, ValueError) as exc:
            raise TerminalError("terminal_path_invalid") from exc
        if not cwd_path.is_dir():
            raise TerminalError("terminal_cwd_invalid")
        if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
            raise TerminalError("terminal_executable_invalid")
        command = self.sandbox.command(
            repository_root=registered.root,
            executable_relative=executable_relative,
            cwd_relative=cwd_relative,
            argv=argv,
            deny_patterns=deny,
        )
        with self._lock:
            active_workspace = sum(session.workspace_id == workspace_id for session in self._sessions.values())
            if active_workspace >= MAX_ACTIVE_PER_WORKSPACE or len(self._sessions) >= MAX_ACTIVE_GLOBAL:
                raise TerminalError("terminal_session_limit")
            if label and (workspace_id, label) in self._labels:
                raise TerminalError("terminal_label_in_use")
            if label:
                self._expired.pop((workspace_id, label), None)
            session = TerminalSession(
                workspace_id=workspace_id,
                repository_alias=registered.alias,
                label=label,
                rows=rows,
                cols=cols,
                command=command,
                on_retire=self._retire,
            )
            self._sessions[session.session_id] = session
            if label:
                self._labels[(workspace_id, label)] = session.session_id
            session.start()
        return session.capture(operation="launched", wait_ms=wait_ms, kill_after_capture=kill_after_capture)

    def recapture(
        self,
        *,
        workspace_id: str,
        session_identity: str,
        wait_ms: int,
        rows: int | None,
        cols: int | None,
        kill_after_capture: bool,
    ) -> TerminalCaptureResult:
        session = self._lookup(workspace_id, session_identity)
        if (rows is None) != (cols is None):
            raise TerminalError("terminal_resize_requires_rows_and_cols")
        if rows is not None and cols is not None:
            session.resize(rows, cols)
        return session.capture(operation="recaptured", wait_ms=wait_ms, kill_after_capture=kill_after_capture)

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            try:
                session.terminate("shutdown")
            except TerminalError:
                continue
        with self._lock:
            self._sessions.clear()
            self._labels.clear()
            self._expired.clear()

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            return {
                "backend": "bubblewrap",
                "available": self.sandbox.available,
                "active_sessions": len(self._sessions),
                "max_active_global": MAX_ACTIVE_GLOBAL,
                "max_active_per_workspace": MAX_ACTIVE_PER_WORKSPACE,
                "max_idle_seconds": MAX_IDLE_SECONDS,
                "max_lifetime_seconds": MAX_LIFETIME_SECONDS,
            }
