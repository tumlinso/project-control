"""Self-terminating local supervisor for the private background queue."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import sqlite3
import time
import traceback
from pathlib import Path

from .runner import run_job
from .host import HostCoordinator
from .scheduler import claim_runnable, dispatch_watch_handlers
from .store import BackgroundStore


def _runtime_schema_available(store: BackgroundStore) -> bool:
    """Return false when a torn-down runtime can no longer supervise work."""

    try:
        with store.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='background_workers'"
            ).fetchone()
        return row is not None
    except (OSError, sqlite3.Error):
        return False


def _canonical_identity():
    from ..runtime_identity import bind_canonical_runtime, validate_runtime

    return bind_canonical_runtime(), validate_runtime


def _execute(store: BackgroundStore, worker_id: str, claimed) -> None:
    job, attempt_id = claimed
    try:
        outcome = run_job(store, worker_id, job, attempt_id)
    except Exception as error:
        outcome = {
            "state": "failed", "returncode": None, "reason": "runner-exception",
            "stdout_path": "", "stderr_path": "", "stdout_tail": "", "stderr_tail": str(error)[-1000:],
            "metadata": {"error": type(error).__name__},
            "result": {"valid": False, "status": "failed", "classification": "background-command-failure", "severity": 0},
        }
    try:
        for attempt in range(3):
            try:
                store.finish(str(job["id"]), attempt_id, **outcome)
                break
            except Exception:
                if attempt == 2:
                    break
                time.sleep(0.1 * (attempt + 1))
    finally:
        if job.get("host_owner_id"):
            try:
                HostCoordinator().release(str(job["host_owner_id"]))
            except Exception:
                pass


def _reap_done(active: set[concurrent.futures.Future]) -> None:
    """Consume completed jobs without allowing one failure to stop the queue."""
    done = {future for future in active if future.done()}
    for future in done:
        active.remove(future)
        try:
            future.result()
        except Exception:
            pass


def _record_supervisor_error(store: BackgroundStore, error: Exception) -> None:
    path = store.paths.root / "worker-errors.log"
    try:
        prior = path.read_text(encoding="utf-8")[-12000:] if path.exists() else ""
        detail = "".join(traceback.format_exception_only(type(error), error)).strip()
        path.write_text(f"{prior}{time.time():.6f} {detail}\n"[-16000:], encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project", required=True)
    parser.add_argument("--idle-seconds", type=float, default=float(os.environ.get("TODO_BACKGROUND_IDLE_SECONDS", "15")))
    parser.add_argument("--max-children", type=int, default=int(os.environ.get("TODO_BACKGROUND_MAX_CHILDREN", "4")))
    args = parser.parse_args()
    runtime_identity, validate_runtime = _canonical_identity()
    store = BackgroundStore(args.project)
    worker_id = store.register_worker()
    idle_since = time.monotonic()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.max_children), thread_name_prefix="todo-background")
    handler_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="todo-background-watch")
    handler_future: concurrent.futures.Future | None = None
    next_handler_dispatch = 0.0
    active: set[concurrent.futures.Future] = set()
    consecutive_supervisor_errors = 0
    try:
        while True:
            try:
                validate_runtime(runtime_identity)
            except Exception as error:
                _record_supervisor_error(store, error)
                return 2
            launched = False
            try:
                store.heartbeat(worker_id)
                _reap_done(active)
                if handler_future and handler_future.done():
                    try:
                        handler_future.result()
                    except Exception:
                        pass
                    handler_future = None
                if handler_future is None and time.monotonic() >= next_handler_dispatch:
                    handler_future = handler_executor.submit(dispatch_watch_handlers, store)
                    next_handler_dispatch = time.monotonic() + 5.0
                while len(active) < max(1, args.max_children):
                    claimed = claim_runnable(store, worker_id)
                    if not claimed:
                        break
                    active.add(executor.submit(_execute, store, worker_id, claimed))
                    launched = True
                consecutive_supervisor_errors = 0
            except Exception as error:
                _reap_done(active)
                consecutive_supervisor_errors += 1
                _record_supervisor_error(store, error)
                if not _runtime_schema_available(store):
                    return 1
                time.sleep(0.25)
                continue
            if not active and not launched:
                try:
                    if store.has_pending_jobs():
                        idle_since = time.monotonic()
                        time.sleep(0.25)
                        continue
                except Exception as error:
                    _record_supervisor_error(store, error)
                    time.sleep(0.25)
                    continue
                if time.monotonic() - idle_since >= args.idle_seconds:
                    return 0
                time.sleep(0.25)
                continue
            idle_since = time.monotonic()
            time.sleep(0.1)
    finally:
        executor.shutdown(wait=True, cancel_futures=False)
        handler_executor.shutdown(wait=False, cancel_futures=True)
        try:
            store.stop_worker(worker_id)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
