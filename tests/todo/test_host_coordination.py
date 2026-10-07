from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from todo_orchestrator.background.host import HostCoordinator


class HostCoordinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(os.environ, {"TODO_BACKGROUND_HOST_RUNTIME_DIR": self.temporary.name})
        self.environment.start()
        self.host = HostCoordinator()
        self.host.upsert_resources([
            {"id": "accelerator:GPU-a", "kind": "accelerator", "tags": {"pcie_root": "root-0", "nvlink_domain": "nv-0"}},
            {"id": "accelerator:GPU-b", "kind": "accelerator", "tags": {"pcie_root": "root-1", "nvlink_domain": "nv-1"}},
            {"id": "accelerator:GPU-c", "kind": "accelerator", "tags": {"pcie_root": "root-0", "nvlink_domain": "nv-0"}},
        ])

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def reserve(self, project: str, job: str, request: dict[str, object]):
        return self.host.reserve_background(
            project_root=Path(self.temporary.name) / project, job_id=job, attempt_id="attempt",
            request=request, pid=os.getpid(),
        )

    def test_gpu_ownership_is_cross_project_and_multi_gpu_atomic(self) -> None:
        first = self.reserve("one", "a", {"ids": ["accelerator:GPU-a"]})
        self.assertIsNotNone(first)
        self.assertIsNone(self.reserve("two", "same", {"ids": ["accelerator:GPU-a"]}))
        independent = self.reserve("two", "other", {"ids": ["accelerator:GPU-b"]})
        self.assertIsNotNone(independent)
        self.assertIsNone(self.reserve("three", "pair", {"ids": ["accelerator:GPU-a", "accelerator:GPU-c"]}))
        self.host.release(first[0])
        pair = self.reserve("three", "pair", {"ids": ["accelerator:GPU-a", "accelerator:GPU-c"]})
        self.assertIsNotNone(pair)
        self.assertEqual(set(pair[1]), {"accelerator:GPU-a", "accelerator:GPU-c"})

    def test_foreground_intent_preempts_only_conflicting_background_across_projects(self) -> None:
        background = self.reserve("one", "background", {"ids": ["accelerator:GPU-a"]})
        owner, resources = self.host.begin_foreground(
            project_root=Path(self.temporary.name) / "two",
            request={"ids": ["accelerator:GPU-a"], "cpu_threads": 1}, pid=os.getpid(),
        )
        self.assertTrue(self.host.preempt_requested(background[0]))
        self.assertFalse(self.host.activate_foreground(owner, resources))
        independent = self.reserve("three", "independent", {"ids": ["accelerator:GPU-b"]})
        self.assertIsNotNone(independent)
        self.host.release(background[0])
        self.assertTrue(self.host.activate_foreground(owner, resources))
        self.host.release(owner)

    def test_profiler_and_interference_domains_are_host_global(self) -> None:
        profiler = self.reserve("one", "profile-a", {
            "ids": ["accelerator:GPU-a"], "exclusive_resources": ["profiler:nvidia"],
        })
        self.assertIsNone(self.reserve("two", "profile-b", {
            "ids": ["accelerator:GPU-b"], "exclusive_resources": ["profiler:nvidia"],
        }))
        ordinary = self.reserve("two", "ordinary-b", {"ids": ["accelerator:GPU-b"]})
        self.assertIsNotNone(ordinary)
        self.host.release(profiler[0])
        self.host.release(ordinary[0])
        domain = self.reserve("one", "domain-a", {"ids": ["accelerator:GPU-a"], "isolate_pcie_root": True})
        self.assertIsNone(self.reserve("two", "domain-c", {"ids": ["accelerator:GPU-c"], "isolate_pcie_root": True}))
        self.assertIsNotNone(self.reserve("two", "domain-b", {"ids": ["accelerator:GPU-b"], "isolate_pcie_root": True}))
        self.host.release(domain[0])

    def test_cpu_pressure_is_coordinated_without_project_migration(self) -> None:
        with mock.patch("todo_orchestrator.background.host.cpu_capacity", return_value=4):
            first = self.reserve("one", "build-a", {"count": 0, "cpu_heavy": True})
            self.assertIsNotNone(first)
            self.assertIsNone(self.reserve("two", "build-b", {"count": 0, "cpu_heavy": True}))
            foreground, resources = self.host.begin_foreground(
                project_root=Path(self.temporary.name) / "foreground", request={"count": 0, "cpu_threads": 1}, pid=os.getpid()
            )
            self.assertTrue(self.host.preempt_requested(first[0]))
            self.assertIsNone(self.reserve("two", "while-pending", {"count": 0, "cpu_threads": 1}))
            self.host.release(first[0])
            self.assertTrue(self.host.activate_foreground(foreground, resources))
            self.host.release(foreground)
        self.assertFalse((Path(self.temporary.name) / "one" / ".todo-orchestrator").exists())

    def test_service_owners_count_toward_cpu_and_ram_pressure(self) -> None:
        with mock.patch("todo_orchestrator.background.host.cpu_capacity", return_value=4):
            service = self.host.reserve_service(
                project_root=Path(self.temporary.name) / "service",
                service_id="model",
                request={"count": 0, "cpu_threads": 3},
                pid=os.getpid(),
            )
            self.assertIsNotNone(service)
            self.assertIsNone(self.reserve("two", "build", {"count": 0, "cpu_threads": 1}))
            self.host.release(service[0])
            self.assertIsNotNone(self.reserve("two", "build", {"count": 0, "cpu_threads": 1}))

    def test_host_database_contains_no_project_campaign_state(self) -> None:
        connection = self.host.connect(readonly=True)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        self.assertIn("host_reservations", tables)
        self.assertNotIn("background_jobs", tables)
        self.assertNotIn("background_watches", tables)
        self.assertNotIn("background_results", tables)


if __name__ == "__main__":
    unittest.main()
