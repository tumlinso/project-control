"""Small private scheduler: event handlers, host headroom, and one job claim."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from .resources import background_launch_allowed
from .host import HostCoordinator


def dispatch_watch_handlers(store) -> None:
    for watch in store.watches("armed"):
        private = watch["spec"].get("_runtime", {})
        argv = private.get("event_handler_argv") if isinstance(private, dict) else None
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            continue
        try:
            subprocess.run(argv, cwd=watch["project_root"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=2, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue


def claim_runnable(store, worker_id: str):
    claimed = store.claim(worker_id, defer_resources=True)
    if not claimed:
        return None
    job, attempt_id = claimed
    request = dict(job.get("resources", {}))
    reason = "foreground-host-headroom"
    allocation = None
    if background_launch_allowed(request):
        try:
            allocation = HostCoordinator().reserve_background(
                project_root=store.project_root, job_id=str(job["id"]), attempt_id=attempt_id,
                request=request, pid=os.getpid(),
            )
            reason = "host-resource-unavailable"
        except (OSError, sqlite3.Error):
            reason = "host-coordinator-unavailable"
    if allocation:
        owner_id, resources = allocation
        job["host_owner_id"] = owner_id
        job["allocated_resources"] = resources
        return job, attempt_id
    store.finish(str(job["id"]), attempt_id, state="preempted", returncode=None, reason=reason,
                 stdout_path="", stderr_path="", stdout_tail="", stderr_tail="",
                 metadata={"deferred": True}, result=None)
    store.defer_job(str(job["id"]), 0.5)
    return None
