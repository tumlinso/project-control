"""Process-group execution with cancellation, timeout, and bounded metadata."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from .artifacts import attempt_directory, bounded_tail, file_digest
from .host import HostCoordinator
from .models import JobState
from .resources import background_environment, cpp_context_active, lower_process_priority


def _best_effort(callable_, *args) -> None:
    try:
        callable_(*args)
    except Exception:
        pass


def _best_effort_bool(callable_, *args) -> bool:
    try:
        return bool(callable_(*args))
    except Exception:
        return False


def terminate_group(pid: int, grace_seconds: float = 2.0) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_job(store, worker_id: str, job: dict[str, object], attempt_id: str) -> dict[str, object]:
    artifact_dir = attempt_directory(store.paths.artifacts, str(job["id"]), attempt_id)
    stdout_path = artifact_dir / "stdout.txt"
    stderr_path = artifact_dir / "stderr.txt"
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in dict(job.get("env", {})).items()})
    env.update(background_environment(dict(job.get("resources", {}))))
    allocated = list(job.get("allocated_resources", []))
    if allocated:
        env["TODO_BACKGROUND_RESOURCE_IDS"] = ",".join(allocated)
    env["TODO_BACKGROUND_JOB_ID"] = str(job["id"])
    env["TODO_BACKGROUND_ATTEMPT_ID"] = attempt_id
    env["TODO_BACKGROUND_ARTIFACT_DIR"] = str(artifact_dir)
    started = time.monotonic()
    reason = "completed"
    state = JobState.FAILED.value
    returncode = None
    host_owner_id = str(job.get("host_owner_id", ""))
    host = HostCoordinator() if host_owner_id else None
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            list(job["argv"]), cwd=str(job["cwd"]), env=env,
            stdout=stdout, stderr=stderr, start_new_session=True,
        )
        lower_process_priority(process.pid)
        _best_effort(store.heartbeat, worker_id, str(job["id"]), attempt_id, process.pid)
        if host:
            _best_effort(host.heartbeat, host_owner_id, process.pid)
        deadline = started + float(job.get("timeout_seconds", 3600))
        while process.poll() is None:
            if (_best_effort_bool(store.cancellation_requested, str(job["id"])) or
                    bool(host and _best_effort_bool(host.preempt_requested, host_owner_id))):
                reason = "foreground-preemption"
                terminate_group(process.pid)
                state = JobState.PREEMPTED.value
                break
            if bool(dict(job.get("resources", {})).get("cpu_heavy", False)) and cpp_context_active():
                reason = "foreground-context-compiler"
                terminate_group(process.pid)
                state = JobState.PREEMPTED.value
                break
            if time.monotonic() >= deadline:
                reason = "timeout"
                terminate_group(process.pid)
                state = JobState.FAILED.value
                break
            _best_effort(store.heartbeat, worker_id, str(job["id"]), attempt_id, process.pid)
            if host:
                _best_effort(host.heartbeat, host_owner_id, process.pid)
            time.sleep(0.2)
        returncode = process.wait()
        if reason == "completed":
            if (_best_effort_bool(store.cancellation_requested, str(job["id"])) or
                    bool(host and _best_effort_bool(host.preempt_requested, host_owner_id))):
                state, reason = JobState.PREEMPTED.value, "foreground-preemption"
            elif returncode == 0:
                state = JobState.SUCCEEDED.value
            elif returncode == 75:
                state, reason = JobState.SKIPPED.value, "contaminated-or-unavailable"
            else:
                state, reason = JobState.FAILED.value, f"exit-{returncode}"
    output = bounded_tail(stdout_path)
    parsed = None
    try:
        candidate = json.loads(output)
        if isinstance(candidate, dict):
            parsed = candidate
    except json.JSONDecodeError:
        pass
    metadata = {
        "argv": job["argv"], "cwd": job["cwd"], "allocated_resources": allocated,
        "elapsed_seconds": round(time.monotonic() - started, 6), "partial": state == JobState.PREEMPTED.value,
    }
    store.record_artifact(str(job["id"]), attempt_id, "stdout", str(stdout_path), file_digest(stdout_path), state != JobState.PREEMPTED.value)
    store.record_artifact(str(job["id"]), attempt_id, "stderr", str(stderr_path), file_digest(stderr_path), state != JobState.PREEMPTED.value)
    return {
        "state": state, "returncode": returncode, "reason": reason,
        "stdout_path": str(stdout_path), "stderr_path": str(stderr_path),
        "stdout_tail": output, "stderr_tail": bounded_tail(stderr_path),
        "metadata": metadata, "result": parsed,
    }
