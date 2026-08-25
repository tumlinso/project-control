from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


MAX_CAPTURE_BYTES = 2 * 1024 * 1024


class CommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    argv0: str
    returncode: int
    stdout: str
    stderr: str

    def json(self) -> dict:
        try:
            value = json.loads(self.stdout)
        except json.JSONDecodeError as exc:
            raise CommandError("command returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise CommandError("command JSON must be an object")
        return value


class FixedCommandRunner:
    def __init__(self, *, max_capture_bytes: int = MAX_CAPTURE_BYTES):
        self.max_capture_bytes = max_capture_bytes

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float = 5.0,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> CommandResult:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must be a non-empty sequence of strings")
        if not cwd.is_absolute():
            raise ValueError("cwd must be absolute")
        process_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        if env:
            process_env.update(env)
        try:
            completed = subprocess.run(
                list(argv),
                cwd=cwd,
                env=process_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CommandError(f"read command unavailable: {Path(argv[0]).name}") from exc
        if len(completed.stdout) > self.max_capture_bytes or len(completed.stderr) > self.max_capture_bytes:
            raise CommandError("command output exceeded capture limit")
        result = CommandResult(
            argv0=Path(argv[0]).name,
            returncode=completed.returncode,
            stdout=completed.stdout.decode("utf-8", errors="replace"),
            stderr=completed.stderr.decode("utf-8", errors="replace"),
        )
        if check and result.returncode:
            raise CommandError(f"read command failed: {result.argv0}")
        return result
