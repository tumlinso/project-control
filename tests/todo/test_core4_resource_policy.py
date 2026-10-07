from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from todo_orchestrator.background.host import HostCoordinator
from todo_orchestrator.runtime.facade import HostResourceFacade
from todo_orchestrator.runtime.resources import PRIORITY_CLASSES, priority_value


class Core4ResourcePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(os.environ, {"TODO_BACKGROUND_HOST_RUNTIME_DIR": self.temporary.name})
        self.environment.start()
        self.host = HostCoordinator()
        self.host.upsert_resources([
            {"id": "accelerator:runtime-a", "kind": "accelerator", "tags": {"pcie_root": "island-0"}},
            {"id": "accelerator:runtime-b", "kind": "accelerator", "tags": {"pcie_root": "island-1"}},
            {"id": "accelerator:runtime-c", "kind": "accelerator", "tags": {"pcie_root": "island-0"}},
        ])
        self.project = Path(self.temporary.name) / "project"

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def service(self, service_id: str, gpu: str, priority: str):
        return self.host.reserve_service(
            project_root=self.project, service_id=service_id,
            request={"ids": [gpu]}, pid=os.getpid(), priority_class=priority,
        )

    def background(self, job: str, request: dict[str, object]):
        return self.host.reserve_background(
            project_root=self.project, job_id=job, attempt_id="attempt",
            request=request, pid=os.getpid(),
        )

    def test_named_priority_order_is_fixed(self) -> None:
        self.assertEqual(set(PRIORITY_CLASSES), {
            "clean_cuda_foreground", "active_local_delegation", "foreground_gpu",
            "background_cuda", "idle_model_residency",
        })
        self.assertGreater(priority_value("clean_cuda_foreground"), priority_value("active_local_delegation"))
        self.assertGreater(priority_value("active_local_delegation"), priority_value("foreground_gpu"))
        self.assertGreater(priority_value("foreground_gpu"), priority_value("background_cuda"))
        self.assertGreater(priority_value("background_cuda"), priority_value("idle_model_residency"))

    def test_existing_host_database_is_migrated_additively(self) -> None:
        with tempfile.TemporaryDirectory() as legacy_root, mock.patch.dict(
            os.environ, {"TODO_BACKGROUND_HOST_RUNTIME_DIR": legacy_root}
        ):
            legacy = HostCoordinator(create=False)
            legacy.root.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(legacy.database)
            try:
                connection.execute(
                    "CREATE TABLE host_owners(id TEXT PRIMARY KEY,owner_kind TEXT NOT NULL,project_root TEXT,"
                    "job_id TEXT,attempt_id TEXT,pid INTEGER,process_start TEXT,state TEXT NOT NULL,"
                    "preempt_requested INTEGER NOT NULL DEFAULT 0,cpu_threads INTEGER NOT NULL DEFAULT 0,"
                    "ram_bytes INTEGER NOT NULL DEFAULT 0,acquired_at REAL NOT NULL,heartbeat_at REAL NOT NULL)"
                )
                connection.commit()
            finally:
                connection.close()
            migrated = HostCoordinator()
            connection = migrated.connect(readonly=True)
            try:
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(host_owners)")}
            finally:
                connection.close()
            self.assertTrue({"service_id", "priority_class", "preemptible"}.issubset(columns))

    def test_active_delegation_drains_background_but_not_equal_priority(self) -> None:
        background = self.background("campaign", {"ids": ["accelerator:runtime-a"]})
        self.assertIsNone(self.service("delegate", "accelerator:runtime-a", "active_local_delegation"))
        self.assertTrue(self.host.preempt_requested(background[0]))
        self.host.release(background[0])
        first = self.service("delegate", "accelerator:runtime-a", "active_local_delegation")
        self.assertIsNotNone(first)
        self.assertIsNone(self.service("peer", "accelerator:runtime-a", "active_local_delegation"))
        self.assertFalse(self.host.preempt_requested(first[0]))

    def test_idle_residency_yields_to_background_and_spare_island_avoids_preemption(self) -> None:
        idle = self.service("resident", "accelerator:runtime-a", "idle_model_residency")
        spare = self.background("spare", {"count": 1})
        self.assertEqual(spare[1], ["accelerator:runtime-b"])
        self.assertFalse(self.host.preempt_requested(idle[0]))
        self.host.release(spare[0])
        self.assertIsNone(self.background("explicit", {"ids": ["accelerator:runtime-a"]}))
        self.assertTrue(self.host.preempt_requested(idle[0]))

    def test_clean_cuda_foreground_drains_active_service_before_activation(self) -> None:
        service = self.service("delegate", "accelerator:runtime-c", "active_local_delegation")
        foreground, resources = self.host.begin_foreground(
            project_root=self.project, request={"ids": ["accelerator:runtime-c"]},
            pid=os.getpid(), priority_class="clean_cuda_foreground",
        )
        self.assertTrue(self.host.preempt_requested(service[0]))
        self.assertFalse(self.host.activate_foreground(foreground, resources))
        self.host.release(service[0])
        self.assertTrue(self.host.activate_foreground(foreground, resources))
        self.assertEqual(self.host.owner(foreground)["priority_class"], "clean_cuda_foreground")

    def test_dead_stale_service_owner_is_recovered(self) -> None:
        stale = self.service("stale", "accelerator:runtime-a", "idle_model_residency")
        connection = self.host.connect()
        try:
            connection.execute(
                "UPDATE host_owners SET pid=?,process_start=?,heartbeat_at=? WHERE id=?",
                (999_999_999, "gone", time.time() - 60, stale[0]),
            )
            connection.commit()
        finally:
            connection.close()
        recovered = self.background("recovered", {"ids": ["accelerator:runtime-a"]})
        self.assertIsNotNone(recovered)
        self.assertEqual(self.host.owner(stale[0])["state"], "stale")

    def test_bundle_discovery_sweeps_dead_ghost_before_conflict_filter(self) -> None:
        stale = self.service("stale", "accelerator:runtime-a", "idle_model_residency")
        connection = self.host.connect()
        try:
            connection.execute(
                "UPDATE host_owners SET pid=?,process_start=?,heartbeat_at=? WHERE id=?",
                (999_999_999, "gone", time.time() - 60, stale[0]),
            )
            connection.commit()
        finally:
            connection.close()
        bundles = HostResourceFacade(self.host).compound_gpu_bundles(1)
        self.assertIn("accelerator:runtime-a", [item for bundle in bundles for item in bundle["resource_ids"]])
        self.assertEqual(self.host.owner(stale[0])["state"], "stale")

    def test_current_process_service_reconciliation_preserves_known_owner_only(self) -> None:
        owned = self.service("core4-local-model-known", "accelerator:runtime-a", "active_local_delegation")
        orphan = self.service("core4-local-model-orphan", "accelerator:runtime-b", "active_local_delegation")
        other = self.service("core4-local-model-other-process", "accelerator:runtime-c", "active_local_delegation")
        connection = self.host.connect()
        try:
            connection.execute(
                "UPDATE host_owners SET pid=?,process_start=? WHERE id=?",
                (999_999_999, "other-process", other[0]),
            )
            connection.commit()
        finally:
            connection.close()
        released = self.host.reconcile_current_service_owners(
            project_root=self.project, pid=os.getpid(), live_owner_ids={owned[0]},
        )
        self.assertEqual(released, [orphan[0]])
        self.assertEqual(self.host.owner(owned[0])["state"], "active")
        self.assertEqual(self.host.owner(orphan[0])["state"], "orphaned")
        self.assertEqual(self.host.owner(other[0])["state"], "active")


if __name__ == "__main__":
    unittest.main()
