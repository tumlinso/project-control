from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..service import AdapterError


class OneShotHarnessAdapter:
    adapter_name = "harness"

    def __init__(self, binary: str, *, process_factory=subprocess.Popen) -> None:
        self.binary = binary
        self.process_factory = process_factory
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _resolved_binary(self) -> str | None:
        if os.path.sep in self.binary:
            path = Path(self.binary)
            return str(path.resolve()) if path.is_file() and os.access(path, os.X_OK) else None
        return shutil.which(self.binary)

    def inspect(self) -> dict[str, Any]:
        resolved = self._resolved_binary()
        return {
            "adapter": self.adapter_name,
            "available": resolved is not None,
            "binary": resolved,
            "capabilities": ["inspect", "start", "health", "run", "cancel", "drain", "evict", "usage"],
        }

    def start(self, context: dict[str, Any]) -> str:
        binary = self._resolved_binary()
        if binary is None:
            raise AdapterError(f"{self.adapter_name} binary is unavailable")
        cwd = Path(str(context.get("cwd", ""))).resolve()
        if not cwd.is_dir():
            raise AdapterError("harness context requires an existing cwd")
        timeout = context.get("timeout_seconds", 600)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
            raise AdapterError("harness timeout_seconds must be between 0 and 3600")
        requested_runtime = context.get("runtime_dir")
        if requested_runtime:
            runtime = Path(str(requested_runtime)).resolve()
            runtime.mkdir(parents=True, exist_ok=True)
            owns_runtime = False
        else:
            runtime = Path(tempfile.mkdtemp(prefix=f"{self.adapter_name}-"))
            owns_runtime = True
        handle = str(uuid.uuid4())
        self._sessions[handle] = {
            "binary": binary, "cwd": cwd, "runtime": runtime, "owns_runtime": owns_runtime,
            "accepting": True, "evicted": False, "process": None,
            "usage": {"runs": 0, "input_chars": 0, "output_chars": 0, "duration_ms": 0.0},
            "config": dict(context),
        }
        return handle

    def _session(self, handle: str) -> dict[str, Any]:
        session = self._sessions.get(handle)
        if session is None:
            raise AdapterError(f"unknown {self.adapter_name} handle")
        return session

    def health(self, handle: str) -> dict[str, Any]:
        session = self._session(handle)
        process = session["process"]
        running = process is not None and process.poll() is None
        state = "evicted" if session["evicted"] else ("draining" if not session["accepting"] else "ready")
        return {"state": state, "active_run": running, "healthy": not session["evicted"]}

    def build_command(self, session: dict[str, Any], prompt: str) -> tuple[list[str], dict[str, str]]:
        raise NotImplementedError

    def parse_output(self, stdout: str) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError

    def build_environment(self, session: dict[str, Any], adapter_env: dict[str, str]) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(adapter_env)
        return environment

    def normalize_outcome(
        self, text: str, usage: dict[str, Any], session: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"status": "succeeded"}

    def normalize_process_error(
        self, returncode: int, stdout: str, stderr: str, session: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return a bounded terminal outcome for a recognized process error."""
        return None

    def run(self, handle: str, request: dict[str, Any]) -> dict[str, Any]:
        session = self._session(handle)
        prompt = request.get("prompt")
        if not isinstance(prompt, str) or not prompt or len(prompt) > 200_000:
            raise AdapterError("harness request requires a bounded prompt")
        with self._lock:
            if session["evicted"] or not session["accepting"]:
                raise AdapterError("harness is not accepting work")
            if session["process"] is not None and session["process"].poll() is None:
                raise AdapterError("harness already has an active run")
            argv, adapter_env = self.build_command(session, prompt)
            environment = self.build_environment(session, adapter_env)
            started = time.perf_counter()
            process = self.process_factory(
                argv, cwd=session["cwd"], env=environment, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            session["process"] = process
        timeout = float(request.get("timeout_seconds", session["config"].get("timeout_seconds", 600)))
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            stdout, stderr = process.communicate()
            raise AdapterError(f"{self.adapter_name} run timed out")
        finally:
            duration = (time.perf_counter() - started) * 1000
            with self._lock:
                session["process"] = None
                session["usage"]["runs"] += 1
                session["usage"]["input_chars"] += len(prompt)
                session["usage"]["duration_ms"] += duration
        if process.returncode != 0:
            normalized = self.normalize_process_error(process.returncode, stdout, stderr, session)
            if normalized is not None:
                return {
                    **normalized, "text": "", "usage": normalized.get("usage", {}),
                    "duration_ms": round(duration, 3), "raw_output_omitted_chars": 0,
                }
            diagnostic = " ".join(
                item for item in (stderr.strip()[-500:], stdout.strip()[-500:]) if item
            )[-1000:]
            raise AdapterError(f"{self.adapter_name} exited {process.returncode}: {diagnostic}")
        text, provider_usage = self.parse_output(stdout)
        session["usage"]["output_chars"] += len(text)
        return {
            **self.normalize_outcome(text, provider_usage, session), "text": text[:20_000], "usage": provider_usage,
            "duration_ms": round(duration, 3), "raw_output_omitted_chars": max(len(text) - 20_000, 0),
        }

    def cancel(self, handle: str, request_id: str | None = None) -> dict[str, Any]:
        session = self._session(handle)
        process = session["process"]
        if process is None or process.poll() is not None:
            return {"canceled": False, "reason": "no_active_run"}
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        return {"canceled": True}

    def drain(self, handle: str) -> dict[str, Any]:
        session = self._session(handle)
        session["accepting"] = False
        return {"draining": True, "active_run": session["process"] is not None}

    def evict(self, handle: str) -> dict[str, Any]:
        session = self._session(handle)
        self.cancel(handle)
        session["accepting"] = False
        session["evicted"] = True
        if session["owns_runtime"]:
            shutil.rmtree(session["runtime"], ignore_errors=True)
        return {"evicted": True}

    def usage(self, handle: str) -> dict[str, Any]:
        usage = dict(self._session(handle)["usage"])
        usage["duration_ms"] = round(float(usage["duration_ms"]), 3)
        return usage


def parse_json_lines(stdout: str) -> list[dict[str, Any]]:
    records = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            records.append(value)
    return records
