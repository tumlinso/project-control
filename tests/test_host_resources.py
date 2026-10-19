from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from project_control.host_resources import HostCoordinator, HostResourceFacade


class HostResourceSeparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(
            os.environ, {"TODO_BACKGROUND_HOST_RUNTIME_DIR": self.temporary.name}
        )
        self.environment.start()
        self.host = HostCoordinator()
        self.facade = HostResourceFacade(self.host)
        self.facade.upsert([
            {"id": "accelerator:fixture-a", "kind": "accelerator", "tags": {}},
            {"id": "accelerator:fixture-b", "kind": "accelerator", "tags": {}},
        ])

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def test_host_reservations_are_global_and_project_todo_is_untouched(self) -> None:
        first = self.facade.reserve_background(
            project_root=Path(self.temporary.name) / "one",
            job_id="job-a", attempt_id="attempt-a",
            resource_request={"schema_version": 1, "ids": ["accelerator:fixture-a"]},
        )
        self.assertIsNotNone(first)
        blocked = self.facade.reserve_background(
            project_root=Path(self.temporary.name) / "two",
            job_id="job-b", attempt_id="attempt-b",
            resource_request={"schema_version": 1, "ids": ["accelerator:fixture-a"]},
        )
        self.assertIsNone(blocked)
        independent = self.facade.reserve_background(
            project_root=Path(self.temporary.name) / "two",
            job_id="job-c", attempt_id="attempt-c",
            resource_request={"schema_version": 1, "ids": ["accelerator:fixture-b"]},
        )
        self.assertIsNotNone(independent)
        self.assertEqual(self.host.release_observation_counts()[:2], (2, 2))
        self.facade.release(str(first["owner_id"]))
        self.facade.release(str(independent["owner_id"]))
        self.assertEqual(self.host.release_observation_counts(), (0, 0, 0))
        self.assertFalse((Path(self.temporary.name) / "one" / ".todo-orchestrator").exists())

    def test_foreground_intent_keeps_preemption_and_release_fencing(self) -> None:
        owner = self.facade.reserve_background(
            project_root=Path(self.temporary.name) / "one",
            job_id="job-a", attempt_id="attempt-a",
            resource_request={"schema_version": 1, "ids": ["accelerator:fixture-a"]},
        )
        foreground = self.facade.begin_foreground(
            project_root=Path(self.temporary.name) / "two",
            resource_request={"schema_version": 1, "ids": ["accelerator:fixture-a"]},
        )
        self.assertTrue(self.facade.preempt_requested(str(owner["owner_id"])))
        self.assertFalse(self.facade.activate_foreground(foreground))
        self.facade.release(str(owner["owner_id"]))
        self.assertTrue(self.facade.activate_foreground(foreground))
        self.facade.release(str(foreground["owner_id"]))

    def test_attempt_lookup_is_exact_read_only_and_reports_process_identity(self) -> None:
        project = Path(self.temporary.name) / "one"
        reservation = self.facade.reserve_background(
            project_root=project,
            job_id="job-a", attempt_id="attempt-a",
            resource_request={"schema_version": 1, "ids": ["accelerator:fixture-a"]},
            pid=os.getpid(),
        )
        rows = self.facade.owners_for_attempt(project, "attempt-a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], reservation["owner_id"])
        self.assertEqual(rows[0]["project_root"], str(project.resolve()))
        self.assertEqual(rows[0]["attempt_id"], "attempt-a")
        self.assertEqual(rows[0]["pid"], os.getpid())
        self.assertTrue(rows[0]["process_start"])
        self.assertEqual(rows[0]["resources"], ["accelerator:fixture-a"])
        self.assertEqual(self.facade.owners_for_attempt(project, "other-attempt"), [])
        self.assertEqual(self.facade.owners_for_attempt(Path(self.temporary.name) / "other", "attempt-a"), [])
        # This observer lookup does not sweep or release ownership.
        self.assertEqual(self.host.release_observation_counts()[:2], (1, 1))


if __name__ == "__main__":
    unittest.main()
