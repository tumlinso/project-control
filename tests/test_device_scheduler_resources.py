from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from project_control.device_scheduler import (
    DeviceSchedulerError,
    DeviceSchedulerHostResourceFacade,
    DeviceSchedulerReclamationPending,
    ResourceAuthority,
    UnsupportedDeviceSchedulerOperation,
    read_resource_authority,
    write_resource_authority,
)
from project_control.host_resources import HostCoordinator


class FakeSchedulerClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.resources = [{
            "resource_id": "gpu:GPU-test",
            "kind": "gpu",
            "source_adapter_id": "nvidia-test",
            "available": True,
            "capacities": {"memory_bytes": 8 * 1024**3},
            "attributes": {"uuid": "GPU-test", "index_observation": 0},
            "external_lease": {"supported": True, "mode": "exclusive_gpu_quiescence",
                               "required_allocation_mode": "exclusive",
                               "release_method": "repeatable_read_only_observation"},
        }]
        self.leases: list[dict] = []
        self.release_state = "released"
        self.error: dict | None = None

    def call(self, operation: str, arguments: dict | None = None) -> dict:
        arguments = dict(arguments or {})
        self.calls.append((operation, arguments))
        if self.error is not None:
            return {"error": dict(self.error)}
        if operation == "resources.list":
            return {"resources": list(self.resources)}
        if operation == "leases.list":
            return {"leases": list(self.leases)}
        if operation == "leases.release":
            lease = next(item for item in self.leases if item["lease_id"] == arguments["lease_id"])
            lease = {**lease, "state": self.release_state}
            self.leases = [lease]
            return {"lease": lease}
        if operation == "leases.request":
            request = arguments["request"]
            lease = {
                "lease_id": "lease-created", "owner_id": request["owner_id"],
                "owner_context": request["owner_context"], "state": "active",
                "fencing_token": 1, "expires_at": "2030-01-01T00:00:00Z",
                "allocations": [{
                    "resource_id": requirement["selectors"]["resource_id"],
                    "quantities": requirement["quantities"], "mode": requirement["mode"],
                } for requirement in request["requirements"]],
            }
            self.leases = [lease]
            return {"lease": lease}
        return {}


class DeviceSchedulerResourceAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "config" / "resource-authority.json"
        self.token = self.root / "token"
        self.token.write_text("test-token\n", encoding="utf-8")
        self.token.chmod(0o600)
        self.authority = ResourceAuthority(
            mode="device_scheduler", base_url="http://127.0.0.1:8787", token_file=str(self.token),
        )
        self.client = FakeSchedulerClient()
        self.facade = DeviceSchedulerHostResourceFacade(self.client)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_private_persistent_selector_round_trips_and_corruption_fails_closed(self) -> None:
        write_resource_authority(self.authority, self.config)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(read_resource_authority(self.config), self.authority)
        self.config.chmod(0o644)
        with self.assertRaisesRegex(DeviceSchedulerError, "resource_authority_config_not_private"):
            read_resource_authority(self.config)

    def test_legacy_coordinator_is_fenced_before_database_creation(self) -> None:
        xdg = self.root / "xdg"
        selector = xdg / "project-control" / "resource-authority.json"
        write_resource_authority(self.authority, selector)
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg),
                                         "PROJECT_CONTROL_HOST_RESOURCES_RUNTIME_DIR": str(self.root / "runtime")}, clear=False):
            coordinator = HostCoordinator(create=False)
            with self.assertRaisesRegex(RuntimeError, "local_host_resource_authority_disabled_device_scheduler_selected"):
                coordinator.connect()
            self.assertFalse(coordinator.database.exists())

    def test_resource_listing_maps_gpu_alias_and_observed_tags(self) -> None:
        resources = self.facade.discover_gpus()
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0]["id"], "accelerator:GPU-test")
        self.assertEqual(resources[0]["kind"], "accelerator")
        self.assertEqual(resources[0]["tags"]["index"], "0")
        self.assertEqual(self.client.calls, [("resources.list", {})])

    def test_owner_reads_are_limited_to_pc_namespace_and_description_context(self) -> None:
        pc_owner = "pc:background:abc"
        self.client.leases = [
            {"lease_id": "l-pc", "owner_id": pc_owner, "owner_context": {
                "issuer": "project-control", "owner_id": pc_owner, "owner_kind": "background",
                "project_root": "/tmp/a project", "job_id": "j1", "attempt_id": "a1", "pid": "123",
            }, "state": "active", "fencing_token": 4, "expires_at": "2030-01-01T00:00:00Z",
             "allocations": [{"resource_id": "gpu:GPU-test", "quantities": {"memory_bytes": 1}}]},
            {"lease_id": "l-other", "owner_id": "pc:forged", "owner_context": {
                "issuer": "not-project-control", "owner_id": "pc:forged", "attempt_id": "a1",
                "project_root": "/tmp/a project",
            }, "state": "active", "fencing_token": 5, "allocations": []},
        ]
        owner = self.facade.owner(pc_owner)
        self.assertEqual(owner["project_root"], "/tmp/a project")
        self.assertEqual(owner["resources"], ["accelerator:GPU-test"])
        self.assertEqual(self.facade.owners_for_attempt("/tmp/a project", "a1"), [owner])
        self.assertEqual(self.facade.owner("pc:forged"), None)
        self.assertEqual(self.facade.owner_activity([pc_owner]), (1, 1))

    def test_background_lease_uses_only_advertised_exclusive_gpu_quiescence_path(self) -> None:
        reservation = self.facade.reserve_background(
            project_root=self.root, job_id="j", attempt_id="a",
            resource_request={"schema_version": 1, "ids": ["accelerator:GPU-test"]}, pid=123,
        )
        self.assertEqual(reservation, {"owner_id": reservation["owner_id"], "resource_ids": ["accelerator:GPU-test"]})
        request = next(args["request"] for operation, args in self.client.calls if operation == "leases.request")
        self.assertEqual(request["owner_id"], reservation["owner_id"])
        self.assertEqual(request["owner_context"]["issuer"], "project-control")
        self.assertEqual(request["owner_context"]["attempt_id"], "a")
        self.assertEqual(request["requirements"], [{
            "kind": "gpu", "quantities": {"memory_bytes": 8 * 1024**3},
            "selectors": {"resource_id": "gpu:GPU-test"}, "mode": "exclusive", "same_host": True,
        }])

    def test_background_lease_uses_scheduler_advertised_headroom_budget(self) -> None:
        self.client.resources[0]["external_lease"]["admission_memory_bytes"] = 6 * 1024**3
        reservation = self.facade.reserve_background(
            project_root=self.root, job_id="headroom", attempt_id="a",
            resource_request={"schema_version": 1, "ids": ["accelerator:GPU-test"]}, pid=123,
        )
        self.assertIsNotNone(reservation)
        request = next(args["request"] for operation, args in self.client.calls if operation == "leases.request")
        self.assertEqual(request["requirements"][0]["quantities"], {"memory_bytes": 6 * 1024**3})
        self.assertEqual(request["requirements"][0]["mode"], "exclusive")

    def test_external_background_lease_refuses_unqualified_gpu_and_mixed_requests(self) -> None:
        cases = (
            {"schema_version": 1, "ids": ["accelerator:GPU-test"], "cpu_threads": 1},
            {"schema_version": 1, "ids": ["accelerator:GPU-test"], "ram_bytes": 1024},
            {"schema_version": 1, "ids": ["accelerator:GPU-test"], "isolate_nvlink_domain": True},
            {"schema_version": 1, "ids": ["accelerator:GPU-test"], "kind": "cpu"},
        )
        for request in cases:
            with self.subTest(request=request), self.assertRaises(UnsupportedDeviceSchedulerOperation):
                self.facade.reserve_background(project_root=self.root, job_id="j", attempt_id="a",
                                               resource_request=request)
        self.assertFalse(any(operation == "leases.request" for operation, _args in self.client.calls))

        self.client.resources[0].pop("external_lease")
        with self.assertRaises(UnsupportedDeviceSchedulerOperation):
            self.facade.reserve_background(project_root=self.root, job_id="j", attempt_id="a",
                                           resource_request={"schema_version": 1, "ids": ["accelerator:GPU-test"]})
        self.assertFalse(any(operation == "leases.request" for operation, _args in self.client.calls))

    def test_foreground_and_service_paths_refuse_without_fabricating_managed_execution(self) -> None:
        for reserve in (
            lambda: self.facade.begin_foreground(project_root=self.root,
                                                  resource_request={"schema_version": 1, "ids": ["accelerator:GPU-test"]}),
            lambda: self.facade.reserve_service(project_root=self.root, service_id="model",
                                                 resource_request={"schema_version": 1, "ids": ["accelerator:GPU-test"]}),
        ):
            with self.assertRaises(UnsupportedDeviceSchedulerOperation):
                reserve()
        self.assertEqual(self.client.calls, [])

    def test_release_waits_for_scheduler_verified_reclamation(self) -> None:
        owner_id = "pc:background:abc"
        self.client.leases = [{
            "lease_id": "lease-1", "owner_id": owner_id, "state": "active", "fencing_token": 7,
            "owner_context": {"issuer": "project-control", "owner_id": owner_id},
            "allocations": [{"resource_id": "gpu:GPU-test", "quantities": {"memory_bytes": 1}}],
        }]
        self.client.release_state = "reclaiming"
        with self.assertRaises(DeviceSchedulerReclamationPending):
            self.facade.release(owner_id)
        call = self.client.calls[-1]
        self.assertEqual(call[0], "leases.release")
        self.assertEqual(call[1], {"lease_id": "lease-1", "fencing_token": 7})

    def test_scheduler_conflict_is_a_non_grant_not_a_transport_failure(self) -> None:
        self.client.error = {"code": "resource_conflict", "message": "reserved"}
        with self.assertRaises(DeviceSchedulerError) as raised:
            self.facade._call("leases.request")
        self.assertEqual(raised.exception.code, "resource_conflict")

    def test_unmapped_priority_preemption_and_residency_controls_fail_closed(self) -> None:
        for call in (
            lambda: self.facade.preempt_requested("pc:owner"),
            lambda: self.facade.request_preemption("pc:owner"),
            lambda: self.facade.set_priority("pc:owner", "foreground"),
            lambda: self.facade.protect_residency("pc:owner", memory_baseline={}),
            lambda: self.facade.reconcile_current_service_owners(),
        ):
            with self.assertRaises(UnsupportedDeviceSchedulerOperation):
                call()


if __name__ == "__main__":
    unittest.main()
