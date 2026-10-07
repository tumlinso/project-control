from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.background.store import BackgroundStore
from todo_orchestrator.background.host import HostCoordinator
from todo_orchestrator.background.runner import run_job
from todo_orchestrator.background.worker import _reap_done, _runtime_schema_available
from todo_orchestrator.background.wake import wake_worker


class BackgroundRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host_runtime = tempfile.TemporaryDirectory()
        self.host_environment = mock.patch.dict(os.environ, {
            "TODO_BACKGROUND_HOST_RUNTIME_DIR": self.host_runtime.name,
            "TODO_BACKGROUND_IDLE_SECONDS": "0.1",
        })
        self.host_environment.start()
        self.repo = V2Repo()
        self.store = BackgroundStore(self.repo.root)

    def tearDown(self) -> None:
        self.store.set_watch_state("stopped")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with self.store.connect(readonly=True) as connection:
                running = connection.execute(
                    "SELECT 1 FROM background_workers WHERE state='running' LIMIT 1"
                ).fetchone()
            if running is None:
                break
            time.sleep(0.05)
        self.repo.close()
        self.host_environment.stop()
        self.host_runtime.cleanup()

    def _arm(self) -> str:
        return self.store.arm_watch({"schema_version": 1, "project_root": str(self.repo.root), "watch": {}})

    def _wait(self, predicate, timeout: float = 8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        self.fail("timed out waiting for background runtime")

    def test_no_watch_keeps_public_mutation_silent(self) -> None:
        with mock.patch("todo_orchestrator.background.wake.subprocess.Popen") as popen:
            result = self.repo.apply(base_plan([safe_task("A", "src/a")]))
        popen.assert_not_called()
        self.assertEqual(result["project_revision"], 2)
        self.assertIn("projection", result)

    def test_wake_fails_closed_for_runtime_a_canonical_b_mismatch(self) -> None:
        class RuntimeMismatch(RuntimeError):
            code = "runtime_identity_mismatch"
            expected = "/canonical-b/src/todo_orchestrator/__init__.py"
            observed = "/runtime-a/src/todo_orchestrator/__init__.py"

        self._arm()
        with mock.patch("todo_orchestrator.background.wake._worker_is_live", return_value=False), \
                mock.patch("todo_orchestrator.runtime_identity.bind_canonical_runtime",
                           side_effect=RuntimeMismatch("runtime A is noncanonical")), \
                mock.patch("todo_orchestrator.background.wake.subprocess.Popen") as popen:
            self.assertFalse(wake_worker(self.repo.root))
        popen.assert_not_called()

    def test_wake_launches_with_explicit_canonical_b_identity(self) -> None:
        self._arm()
        identity = SimpleNamespace(
            skills_root=Path("/canonical-b"),
            package_root=Path("/canonical-b/src/todo_orchestrator"),
            fingerprint="b" * 64,
        )
        with mock.patch.dict(os.environ, {
                "TODO_ORCHESTRATOR_STATE_DIR": str(self.repo.state_root),
                "TODO_ORCHESTRATOR_READ_ONLY": "1",
                "PYTHONPATH": "/runtime-a/src",
            }), mock.patch("todo_orchestrator.background.wake._worker_is_live",
                           side_effect=[False, True]), \
                mock.patch("todo_orchestrator.runtime_identity.bind_canonical_runtime",
                           return_value=identity), \
                mock.patch("todo_orchestrator.runtime_identity.validate_runtime") as validate, \
                mock.patch("todo_orchestrator.runtime_identity.controlled_subprocess_env",
                           return_value={
                               **os.environ,
                               "PROJECT_CONTROL_SKILLS_ROOT": "/canonical-b",
                               "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT": "b" * 64,
                               "PYTHONPATH": "/canonical-b/src",
                           }), \
                mock.patch("todo_orchestrator.background.wake.subprocess.Popen") as popen:
            self.assertTrue(wake_worker(self.repo.root))
        validate.assert_called_once_with(identity)
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT"], "b" * 64)
        self.assertEqual(environment["TODO_ORCHESTRATOR_STATE_DIR"], str(self.repo.state_root))
        self.assertEqual(environment["PYTHONPATH"], "/canonical-b/src")
        self.assertNotIn("TODO_ORCHESTRATOR_READ_ONLY", environment)

    def test_shared_runtime_child_environment_keeps_canonical_and_legacy_aliases_equal(self) -> None:
        from todo_orchestrator import runtime_identity

        skills_root = Path.home() / ".agents" / "skills"
        runtime_identity._reset_for_testing()
        try:
            with mock.patch.dict(os.environ, {
                "PROJECT_CONTROL_SKILLS_ROOT": str(skills_root),
            }, clear=True):
                identity = runtime_identity.bind_canonical_runtime()
                environment = runtime_identity.controlled_subprocess_env(identity)
        finally:
            runtime_identity._reset_for_testing()
        self.assertEqual(environment["PROJECT_CONTROL_SKILLS_ROOT"], str(skills_root))
        self.assertNotIn("CODING_WORKFLOW_SKILLS_ROOT", environment)

    def test_armed_watch_auto_wakes_and_runs_structured_argv(self) -> None:
        watch_id = self._arm()
        job_id, created = self.store.enqueue({
            "watch_id": watch_id,
            "kind": "fake",
            "argv": [sys.executable, "-c", "import json; print(json.dumps({'valid': True, 'severity': 0}))"],
            "cwd": str(self.repo.root),
            "timeout": 5,
        })
        self.assertTrue(created)
        self.assertTrue(wake_worker(self.repo.root))
        result = self._wait(lambda: self.store.result(job_id))
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["valid"])

    def test_dedup_and_atomic_multi_resource_reservation(self) -> None:
        watch_id = self._arm()
        self.store.upsert_resources([
            {"id": "accelerator:GPU-a", "kind": "accelerator", "tags": {}},
            {"id": "accelerator:GPU-b", "kind": "accelerator", "tags": {}},
        ])
        spec = {"watch_id": watch_id, "kind": "fake", "argv": ["true"], "cwd": str(self.repo.root),
                "resources": {"kind": "accelerator", "ids": ["accelerator:GPU-a", "accelerator:GPU-b"]}, "dedup_key": "same"}
        first, created = self.store.enqueue(spec)
        second, duplicate = self.store.enqueue(spec)
        self.assertTrue(created)
        self.assertFalse(duplicate)
        self.assertEqual(first, second)
        claimed = self.store.claim("worker")
        self.assertEqual(set(claimed[0]["allocated_resources"]), {"accelerator:GPU-a", "accelerator:GPU-b"})
        intent = self.store.foreground_intent(["accelerator:GPU-a"])
        self.assertFalse(self.store.reserve_foreground(intent, ["accelerator:GPU-a"]))
        self.store.clear_foreground(intent)

    def test_one_supervisor_runs_independent_resources_concurrently(self) -> None:
        watch_id = self._arm()
        self.store.upsert_resources([
            {"id": "accelerator:GPU-a", "kind": "accelerator", "tags": {}},
            {"id": "accelerator:GPU-b", "kind": "accelerator", "tags": {}},
        ])
        for suffix in ("a", "b"):
            self.store.enqueue({
                "watch_id": watch_id, "kind": "parallel", "argv": [sys.executable, "-c", "import time; time.sleep(.5)"],
                "cwd": str(self.repo.root), "resources": {"ids": [f"accelerator:GPU-{suffix}"]}, "dedup_key": f"parallel-{suffix}",
            })
        self.assertTrue(wake_worker(self.repo.root))
        # Process startup can exceed the generic eight-second polling budget on
        # a loaded validation host. The timestamp assertion below still proves
        # concurrent execution rather than accepting serialized completion.
        self._wait(lambda: all(self.store.result(row) for row in self._parallel_job_ids()), timeout=20.0)
        with self.store.connect(readonly=True) as connection:
            rows = connection.execute(
                "SELECT a.started_at,a.finished_at FROM background_attempts a JOIN background_jobs j ON j.id=a.job_id "
                "WHERE j.kind='parallel' ORDER BY a.started_at"
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[1]["started_at"], rows[0]["finished_at"])

    def _parallel_job_ids(self):
        with self.store.connect(readonly=True) as connection:
            return [row[0] for row in connection.execute("SELECT id FROM background_jobs WHERE kind='parallel'")]

    def test_foreground_preemption_requeues_without_retry_or_valid_result(self) -> None:
        watch_id = self._arm()
        self.store.upsert_resources([{"id": "accelerator:GPU-a", "kind": "accelerator", "tags": {}}])
        job_id, _ = self.store.enqueue({
            "watch_id": watch_id, "kind": "slow", "argv": [sys.executable, "-c", "import time; time.sleep(30)"],
            "cwd": str(self.repo.root), "timeout": 60, "retry_limit": 1,
            "resources": {"kind": "accelerator", "ids": ["accelerator:GPU-a"]},
        })
        self.assertTrue(wake_worker(self.repo.root))
        host = HostCoordinator()
        self._wait(lambda: host.conflicts(["accelerator:GPU-a"]))
        owner, resources = host.begin_foreground(
            project_root=self.repo.root / "other-project", request={"ids": ["accelerator:GPU-a"]}, pid=os.getpid()
        )
        self._wait(lambda: not host.conflicts(resources))
        with self.store.connect(readonly=True) as conn:
            job = conn.execute("SELECT state,retries_used,result_id FROM background_jobs WHERE id=?", (job_id,)).fetchone()
            attempt = conn.execute("SELECT state FROM background_attempts WHERE job_id=?", (job_id,)).fetchone()
        self.assertEqual(dict(job), {"state": "queued", "retries_used": 0, "result_id": None})
        self.assertEqual(attempt["state"], "preempted")
        host.release(owner)

    def test_cpu_heavy_background_yields_to_context_compiler(self) -> None:
        watch_id = self._arm()
        job_id, _ = self.store.enqueue({
            "watch_id": watch_id, "kind": "build",
            "argv": [sys.executable, "-c", "import time; time.sleep(30)"],
            "cwd": str(self.repo.root), "resources": {"cpu_heavy": True},
        })
        worker = self.store.register_worker()
        job, attempt = self.store.claim(worker)
        with mock.patch("todo_orchestrator.background.runner.cpp_context_active", return_value=True):
            outcome = run_job(self.store, worker, job, attempt)
        self.store.finish(job_id, attempt, **outcome)
        self.store.stop_worker(worker)
        self.assertEqual(outcome["state"], "preempted")
        self.assertEqual(outcome["reason"], "foreground-context-compiler")
        with self.store.connect(readonly=True) as connection:
            row = connection.execute("SELECT state,retries_used,result_id FROM background_jobs WHERE id=?", (job_id,)).fetchone()
        self.assertEqual(dict(row), {"state": "queued", "retries_used": 0, "result_id": None})

    def test_failed_worker_future_does_not_stop_supervisor_reaping(self) -> None:
        import concurrent.futures

        failed = concurrent.futures.Future()
        failed.set_exception(RuntimeError("synthetic completion failure"))
        succeeded = concurrent.futures.Future()
        succeeded.set_result(None)
        active = {failed, succeeded}
        _reap_done(active)
        self.assertEqual(active, set())

    def test_torn_down_runtime_schema_stops_supervisor(self) -> None:
        self.assertTrue(_runtime_schema_available(self.store))
        self.store.paths.database.unlink()
        self.store.paths.database.touch()
        try:
            self.assertFalse(_runtime_schema_available(self.store))
        finally:
            self.store.initialize()

    def test_runner_reaps_child_when_queue_heartbeat_is_temporarily_unavailable(self) -> None:
        watch_id = self._arm()
        job_id, _ = self.store.enqueue({
            "watch_id": watch_id, "kind": "heartbeat-fault",
            "argv": [sys.executable, "-c", "print('completed')"],
            "cwd": str(self.repo.root), "timeout": 5,
        })
        worker = self.store.register_worker()
        job, attempt = self.store.claim(worker)
        with mock.patch.object(self.store, "heartbeat", side_effect=RuntimeError("database busy")):
            outcome = run_job(self.store, worker, job, attempt)
        self.assertEqual(outcome["state"], "succeeded")
        self.assertEqual(outcome["returncode"], 0)
        self.store.finish(job_id, attempt, **outcome)
        self.assertFalse(self.store.has_pending_jobs())
        self.store.stop_worker(worker)

    def test_canceled_stale_job_is_not_resurrected(self) -> None:
        watch_id = self._arm()
        job_id, _ = self.store.enqueue({
            "watch_id": watch_id, "kind": "stale", "argv": ["true"], "cwd": str(self.repo.root),
        })
        worker = self.store.register_worker()
        job, attempt = self.store.claim(worker)
        self.assertEqual(job["id"], job_id)
        connection = self.store._tx()
        try:
            connection.execute(
                "UPDATE background_attempts SET heartbeat_at=0,pid=? WHERE id=?", (99999999, attempt)
            )
            connection.execute("UPDATE background_jobs SET cancel_requested=1 WHERE id=?", (job_id,))
            connection.commit()
        finally:
            connection.close()
        replacement = self.store.register_worker()
        self.assertIsNone(self.store.claim(replacement))
        with self.store.connect(readonly=True) as connection:
            state = connection.execute("SELECT state FROM background_jobs WHERE id=?", (job_id,)).fetchone()[0]
            attempt_state = connection.execute(
                "SELECT state FROM background_attempts WHERE id=?", (attempt,)
            ).fetchone()[0]
        self.assertEqual(state, "canceled")
        self.assertEqual(attempt_state, "canceled")
        self.store.stop_worker(worker)
        self.store.stop_worker(replacement)

    def test_requested_cancellation_finishes_canceled_not_queued(self) -> None:
        watch_id = self._arm()
        job_id, _ = self.store.enqueue({
            "watch_id": watch_id, "kind": "cancel", "argv": ["true"], "cwd": str(self.repo.root),
        })
        worker = self.store.register_worker()
        job, attempt = self.store.claim(worker)
        self.assertEqual(job["id"], job_id)
        self.store.cancel_background()
        self.store.finish(
            job_id, attempt, state="preempted", returncode=-15, reason="requested-cancellation",
            stdout_path="", stderr_path="", stdout_tail="", stderr_tail="", metadata={}, result=None,
        )
        with self.store.connect(readonly=True) as connection:
            state = connection.execute("SELECT state FROM background_jobs WHERE id=?", (job_id,)).fetchone()[0]
        self.assertEqual(state, "canceled")
        self.store.stop_worker(worker)


if __name__ == "__main__":
    unittest.main()
