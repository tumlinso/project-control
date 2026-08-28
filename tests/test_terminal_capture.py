from __future__ import annotations

import concurrent.futures
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import TerminalCaptureInput
from project_control.security import redact_output
from project_control.terminal import (
    BubblewrapSandbox,
    TerminalError,
    TerminalSessionRegistry,
)


FIXTURE = r'''#!/usr/bin/python3
import curses
import os
import socket
import secrets
import sys
import time

mode = sys.argv[1]
if mode == "tty":
    size = os.get_terminal_size(1)
    print(f"TTY={os.isatty(0)}/{os.isatty(1)}/{os.isatty(2)} SIZE={size.lines}x{size.columns} TERM={os.environ.get('TERM')}", flush=True)
    time.sleep(5)
elif mode == "curses":
    screen = curses.initscr()
    screen.addstr(2, 4, "CURSES-READY")
    screen.refresh()
    time.sleep(5)
elif mode == "ansi":
    os.write(1, b"old line\rNEW\nremove-me\x1b[2K\rkept\n")
    os.write(1, b"\x1b[?1049hALT")
    time.sleep(.05)
    os.write(1, b"\x1b[2J\x1b[Hsplit-")
    os.write(1, b"\xe2\x82")
    time.sleep(.03)
    os.write(1, b"\xac\x1b[?1049l")
    os.write(1, b"\x1b[Hfinal")
    time.sleep(5)
elif mode == "phases":
    os.write(1, b"STATE-A")
    time.sleep(.35)
    os.write(1, b"\r\x1b[2KSTATE-B")
    time.sleep(5)
elif mode == "natural":
    print("finished", flush=True)
elif mode == "counter":
    run = secrets.token_hex(6)
    for value in range(100):
        os.write(1, f"\r\x1b[2KRUN={run} COUNT={value}".encode())
        time.sleep(.08)
elif mode == "resize":
    for value in range(100):
        size = os.get_terminal_size(1)
        os.write(1, f"\r\x1b[2KSIZE={size.lines}x{size.columns}".encode())
        time.sleep(.05)
elif mode == "literal":
    print(repr(sys.argv[2:]), flush=True)
    time.sleep(5)
elif mode == "security":
    checks = []
    try:
        open("created-by-terminal", "w").write("bad")
        checks.append("WRITE=allowed")
    except OSError:
        checks.append("WRITE=blocked")
    try:
        open(sys.argv[2]).read()
        checks.append("HOST=allowed")
    except OSError:
        checks.append("HOST=blocked")
    try:
        open(".env").read()
        checks.append("DENY=allowed")
    except OSError:
        checks.append("DENY=blocked")
    checks.append("ENV=" + str(os.environ.get("CALLER_SECRET")))
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=.2)
        checks.append("NET=allowed")
    except OSError:
        checks.append("NET=blocked")
    print(" ".join(checks), flush=True)
    time.sleep(5)
elif mode == "secret":
    print("token=sk_abcdefghijklmnopqrstuv", flush=True)
    time.sleep(5)
elif mode == "fork":
    child = os.fork()
    if child == 0:
        time.sleep(30)
        os._exit(0)
    print(f"CHILD={child}", flush=True)
    time.sleep(30)
elif mode == "spam":
    os.write(1, b"x" * (2 * 1024 * 1024))
    time.sleep(30)
'''


class TerminalCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Tests"], cwd=self.root, check=True)
        self.executable = self.root / "terminal-fixture"
        self.executable.write_text(FIXTURE, encoding="utf-8")
        self.executable.chmod(0o755)
        (self.root / ".gitignore").write_text(".env\n", encoding="utf-8")
        (self.root / ".env").write_text("CALLER_SECRET=repository-secret\n", encoding="utf-8")
        subprocess.run(["git", "add", "terminal-fixture", ".gitignore"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.root, check=True, capture_output=True)
        self.outside_secret = Path(self.temporary.name) / "outside-secret"
        self.outside_secret.write_text("host secret", encoding="utf-8")
        self.config = ProjectControlConfig(workspaces={
            "demo": WorkspaceConfig(authority_repository="source", repositories={
                "source": RepositoryConfig(root=self.root),
            })
        })
        self.registry = TerminalSessionRegistry(self.config)
        self.assertTrue(BubblewrapSandbox().available)

    def tearDown(self) -> None:
        self.registry.shutdown()
        self.temporary.cleanup()

    def launch(self, mode: str, **kwargs: object):
        options = {
            "workspace_id": "demo",
            "repository": "source",
            "executable": "terminal-fixture",
            "argv": [mode],
            "cwd": ".",
            "label": None,
            "wait_ms": 150,
            "rows": None,
            "cols": None,
            "kill_after_capture": True,
        }
        options.update(kwargs)
        return self.registry.launch(**options)

    def test_real_pty_geometry_term_and_default_kill(self) -> None:
        result = self.launch("tty", rows=31, cols=97)
        self.assertIn("TTY=True/True/True SIZE=31x97 TERM=xterm-256color", result.screen)
        self.assertFalse(result.active)
        self.assertEqual(result.state, "killed")
        self.assertEqual(self.registry.diagnostics()["active_sessions"], 0)

    def test_public_operation_validation_and_defaults(self) -> None:
        launch = TerminalCaptureInput(project="demo", executable="terminal-fixture")
        self.assertTrue(launch.kill_after_capture)
        self.assertEqual(launch.wait_ms, 250)
        for invalid in (
            {"project": "demo"},
            {"project": "demo", "executable": "terminal-fixture", "session": "active"},
            {"project": "demo", "session": "active", "argv": ["not-launch"]},
            {"project": "demo", "session": "active", "rows": 20},
        ):
            with self.assertRaises(ValueError):
                TerminalCaptureInput(**invalid)

    def test_rendered_screen_handles_ansi_alternate_and_split_utf8(self) -> None:
        result = self.launch("ansi", wait_ms=180)
        self.assertNotIn("\x1b", result.screen)
        self.assertIn("finaline", result.screen)
        self.assertIn("kept", result.screen)
        self.assertNotIn("ALT", result.screen)
        self.assertNotIn("split-€", result.screen)

    def test_curses_program_initializes_on_real_terminal(self) -> None:
        result = self.launch("curses", wait_ms=200)
        self.assertIn("CURSES-READY", result.screen)
        self.assertNotIn("\x1b", result.screen)

    def test_wait_drains_continuously_and_observes_later_state(self) -> None:
        early = self.launch("phases", wait_ms=100)
        self.assertIn("STATE-A", early.screen)
        late = self.launch("phases", wait_ms=500)
        self.assertIn("STATE-B", late.screen)
        self.assertNotIn("STATE-A", late.screen)

    def test_bonded_recapture_by_id_and_label_is_same_execution(self) -> None:
        first = self.launch("counter", label="txtdesk-dev", wait_ms=100, kill_after_capture=False)
        self.assertTrue(first.active)
        run_identity = first.screen.split("RUN=")[-1].split()[0]
        first_count = int(first.screen.split("COUNT=")[-1])
        second = self.registry.recapture(
            workspace_id="demo", session_identity=first.session_id, wait_ms=180,
            rows=None, cols=None, kill_after_capture=False,
        )
        second_count = int(second.screen.split("COUNT=")[-1])
        self.assertIn(f"RUN={run_identity}", second.screen)
        self.assertGreater(second_count, first_count)
        third = self.registry.recapture(
            workspace_id="demo", session_identity="txtdesk-dev", wait_ms=100,
            rows=None, cols=None, kill_after_capture=True,
        )
        self.assertGreaterEqual(int(third.screen.split("COUNT=")[-1]), second_count)
        self.assertIn(f"RUN={run_identity}", third.screen)
        self.assertFalse(third.active)
        self.assertEqual(third.state, "killed")
        self.assertEqual(self.registry.diagnostics()["active_sessions"], 0)
        with self.assertRaisesRegex(TerminalError, "terminal_session_unknown"):
            self.registry.recapture(workspace_id="demo", session_identity="txtdesk-dev", wait_ms=0, rows=None, cols=None, kill_after_capture=True)

    def test_natural_exit_reaps_and_releases_label(self) -> None:
        result = self.launch("natural", label="reusable", wait_ms=1000, kill_after_capture=False)
        self.assertEqual(result.screen, "finished")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.state, "exited")
        self.assertFalse(result.active)
        self.assertLess(result.capture_ms, 500)
        replacement = self.launch("counter", label="reusable", wait_ms=80, kill_after_capture=False)
        self.assertTrue(replacement.active)

    def test_duplicate_and_invalid_labels(self) -> None:
        self.launch("counter", label="same", wait_ms=20, kill_after_capture=False)
        with self.assertRaisesRegex(TerminalError, "terminal_label_in_use"):
            self.launch("counter", label="same", wait_ms=20, kill_after_capture=False)
        with self.assertRaisesRegex(TerminalError, "terminal_label_invalid"):
            self.launch("counter", label="bad label", wait_ms=20, kill_after_capture=False)
        with self.assertRaisesRegex(TerminalError, "terminal_label_requires_bonded_session"):
            self.launch("counter", label="short-lived")

    def test_resize_retained_session(self) -> None:
        first = self.launch("resize", wait_ms=80, rows=20, cols=80, kill_after_capture=False)
        self.assertIn("SIZE=20x80", first.screen)
        resized = self.registry.recapture(
            workspace_id="demo", session_identity=first.session_id, wait_ms=100,
            rows=50, cols=140, kill_after_capture=True,
        )
        self.assertEqual((resized.rows, resized.cols), (50, 140))
        self.assertIn("SIZE=50x140", resized.screen)

    def test_same_session_capture_is_busy_and_independent_sessions_coexist(self) -> None:
        first = self.launch("counter", label="one", wait_ms=30, kill_after_capture=False)
        second = self.launch("counter", label="two", wait_ms=30, kill_after_capture=False)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(
                self.registry.recapture,
                workspace_id="demo", session_identity=first.session_id, wait_ms=400,
                rows=None, cols=None, kill_after_capture=False,
            )
            time.sleep(.05)
            with self.assertRaisesRegex(TerminalError, "terminal_session_busy"):
                self.registry.recapture(workspace_id="demo", session_identity=first.session_id, wait_ms=0, rows=None, cols=None, kill_after_capture=False)
            independent = self.registry.recapture(workspace_id="demo", session_identity=second.session_id, wait_ms=20, rows=None, cols=None, kill_after_capture=False)
            self.assertTrue(independent.active)
            pending.result(timeout=2)

    def test_literal_argv_sandbox_and_environment_isolation(self) -> None:
        literal = self.launch("literal", argv=["literal", "; touch /tmp/not-shell", "$(id)"], wait_ms=100)
        self.assertIn("; touch /tmp/not-shell", literal.screen)
        self.assertIn("$(id)", literal.screen)
        old = os.environ.get("CALLER_SECRET")
        os.environ["CALLER_SECRET"] = "must-not-cross"
        try:
            security = self.launch("security", argv=["security", str(self.outside_secret)], wait_ms=300)
        finally:
            if old is None:
                os.environ.pop("CALLER_SECRET", None)
            else:
                os.environ["CALLER_SECRET"] = old
        self.assertIn("WRITE=blocked", security.screen)
        self.assertIn("HOST=blocked", security.screen)
        self.assertIn("DENY=blocked", security.screen)
        self.assertIn("ENV=None", security.screen)
        self.assertIn("NET=blocked", security.screen)
        self.assertFalse((self.root / "created-by-terminal").exists())
        self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=self.root, check=True, capture_output=True, text=True).stdout, "")

    def test_path_and_argument_rejections(self) -> None:
        outside = Path(self.temporary.name) / "outside-executable"
        outside.write_text(FIXTURE, encoding="utf-8")
        outside.chmod(0o755)
        (self.root / "escape").symlink_to(outside)
        (self.root / "not-executable").write_text("plain", encoding="utf-8")
        private_executable = self.root / ".todo-orchestrator" / "private-executable"
        private_executable.parent.mkdir()
        private_executable.write_text(FIXTURE, encoding="utf-8")
        private_executable.chmod(0o755)
        for executable in (str(self.executable), "../outside-executable", "escape", ".env", ".todo-orchestrator/private-executable"):
            with self.assertRaisesRegex(TerminalError, "terminal_path_invalid"):
                self.launch("tty", executable=executable)
        with self.assertRaisesRegex(TerminalError, "terminal_executable_invalid"):
            self.launch("tty", executable="not-executable")
        with self.assertRaisesRegex(TerminalError, "terminal_path_invalid"):
            self.launch("tty", cwd="../")
        with self.assertRaisesRegex(TerminalError, "terminal_path_invalid"):
            self.launch("tty", cwd=".git")
        with self.assertRaisesRegex(TerminalError, "terminal_argv"):
            self.launch("tty", argv=["x"] * 65)

    def test_secret_redaction_and_output_contract(self) -> None:
        result = self.launch("secret", wait_ms=100)
        output = redact_output({"data": result.as_dict()})
        serialized = str(output)
        self.assertIn("[REDACTED]", serialized)
        self.assertNotIn("sk_abcdefghijklmnopqrstuv", serialized)
        for forbidden in ("stdout", "stderr", "raw_log", "transcript", "command_line", "environment"):
            self.assertNotIn(forbidden, output["data"])
        self.assertNotIn(str(self.root), serialized)

    def test_resource_limits_and_fail_closed_sandbox(self) -> None:
        sessions = [self.launch("counter", wait_ms=10, kill_after_capture=False) for _ in range(4)]
        with self.assertRaisesRegex(TerminalError, "terminal_session_limit"):
            self.launch("counter", wait_ms=10, kill_after_capture=False)
        for session in sessions:
            self.registry.recapture(workspace_id="demo", session_identity=session.session_id, wait_ms=0, rows=None, cols=None, kill_after_capture=True)
        limited = self.launch("spam", wait_ms=3000, kill_after_capture=False)
        self.assertTrue(limited.stream_limited)
        self.assertEqual(limited.state, "stream_limit")
        self.assertFalse(limited.active)
        unavailable = TerminalSessionRegistry(self.config, BubblewrapSandbox("/definitely/missing/bwrap"))
        try:
            with self.assertRaisesRegex(TerminalError, "terminal_sandbox_unavailable"):
                unavailable.launch(
                    workspace_id="demo", repository="source", executable="terminal-fixture",
                    argv=["tty"], cwd=".", label=None, wait_ms=0, rows=None, cols=None,
                    kill_after_capture=True,
                )
        finally:
            unavailable.shutdown()

    def test_idle_expiry_is_deterministic_and_releases_label(self) -> None:
        with patch("project_control.terminal.MAX_IDLE_SECONDS", 0.1):
            retained = self.launch("counter", label="expires", wait_ms=10, kill_after_capture=False)
            for _ in range(30):
                if self.registry.diagnostics()["active_sessions"] == 0:
                    break
                time.sleep(.03)
            else:
                self.fail("idle session did not expire")
            with self.assertRaisesRegex(TerminalError, "terminal_session_expired"):
                self.registry.recapture(workspace_id="demo", session_identity=retained.session_id, wait_ms=0, rows=None, cols=None, kill_after_capture=True)
            with self.assertRaisesRegex(TerminalError, "terminal_session_expired"):
                self.registry.recapture(workspace_id="demo", session_identity="expires", wait_ms=0, rows=None, cols=None, kill_after_capture=True)
        replacement = self.launch("counter", label="expires", wait_ms=10, kill_after_capture=False)
        self.assertTrue(replacement.active)

    def test_shutdown_terminates_process_groups_and_clears_registry(self) -> None:
        result = self.launch("fork", wait_ms=100, kill_after_capture=False)
        self.assertIn("CHILD=", result.screen)
        owned_pgid = self.registry._sessions[result.session_id]._process.pid
        self.registry.shutdown()
        self.assertEqual(self.registry.diagnostics()["active_sessions"], 0)
        for _ in range(20):
            try:
                os.killpg(owned_pgid, 0)
            except ProcessLookupError:
                break
            time.sleep(.05)
        else:
            self.fail("owned process-group child survived registry shutdown")


if __name__ == "__main__":
    unittest.main()
