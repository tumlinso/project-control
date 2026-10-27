from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from project_control.device_scheduler import ResourceAuthority
from project_control.cli import _resource_authority_command


class FakeMcpClient:
    responses: dict[str, dict] = {}
    calls: list[str] = []

    def __init__(self, authority, *, timeout_seconds):
        self.authority = authority

    async def call(self, operation, arguments):
        type(self).calls.append(operation)
        return dict(type(self).responses.get(operation, {}))


class DeviceSchedulerAuthorityCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.token = self.root / "token"
        self.token.write_text("token\n", encoding="utf-8")
        self.token.chmod(0o600)
        FakeMcpClient.calls = []
        FakeMcpClient.responses = {
            "resources.list": {"resources": []},
            "leases.list": {"leases": []},
            "backends.get": {"backend": {"backend_id": "model-a"}},
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_select_ds_refuses_active_legacy_local_owners(self) -> None:
        current = ResourceAuthority()
        with mock.patch("project_control.device_scheduler.DeviceSchedulerMcpClient", FakeMcpClient), \
             mock.patch("project_control.device_scheduler.read_resource_authority", return_value=current), \
             mock.patch("project_control.host_resources.coordinator.HostCoordinator", create=True) as coordinator_type, \
             mock.patch("project_control.device_scheduler.write_resource_authority") as write:
            coordinator_type.return_value.database = self.root / "existing.sqlite"
            coordinator_type.return_value.database.touch()
            coordinator_type.return_value.release_observation_counts.return_value = (1, 1, 0)
            with self.assertRaisesRegex(RuntimeError, "local_host_resource_owners_must_be_released"):
                _resource_authority_command(SimpleNamespace(
                    resource_authority_command="select-device-scheduler", base_url="http://127.0.0.1:8787",
                    token_file=self.token, model_backend_id="model-a"))
            write.assert_not_called()

    def test_select_local_requires_scheduler_lease_inventory_to_be_empty(self) -> None:
        current = ResourceAuthority(mode="device_scheduler", base_url="http://127.0.0.1:8787",
                                    token_file=str(self.token), model_backend_id=None)
        FakeMcpClient.responses["leases.list"] = {"leases": [{"lease_id": "l1", "state": "unknown"}]}
        with mock.patch("project_control.device_scheduler.DeviceSchedulerMcpClient", FakeMcpClient), \
             mock.patch("project_control.device_scheduler.read_resource_authority", return_value=current), \
             mock.patch("project_control.device_scheduler.write_resource_authority") as write:
            with self.assertRaisesRegex(RuntimeError, "leases_must_be_reclaimed_before_authority_switch"):
                _resource_authority_command(SimpleNamespace(resource_authority_command="select-local"))
            write.assert_not_called()

    def test_select_ds_queries_target_inventory_before_persisting(self) -> None:
        current = ResourceAuthority()
        authority = ResourceAuthority(mode="device_scheduler", base_url="http://127.0.0.1:8787",
                                     token_file=str(self.token), model_backend_id="model-a")
        with mock.patch("project_control.device_scheduler.DeviceSchedulerMcpClient", FakeMcpClient), \
             mock.patch("project_control.device_scheduler.read_resource_authority", return_value=current), \
             mock.patch("project_control.host_resources.coordinator.HostCoordinator") as coordinator_type, \
             mock.patch("project_control.device_scheduler.write_resource_authority") as write:
            coordinator_type.return_value.database = self.root / "missing.sqlite"
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                result = _resource_authority_command(SimpleNamespace(
                    resource_authority_command="select-device-scheduler", base_url=authority.base_url,
                    token_file=self.token, model_backend_id=authority.model_backend_id))
            self.assertEqual(result, 0)
            self.assertEqual(FakeMcpClient.calls, ["resources.list", "backends.get"])
            write.assert_called_once_with(authority)


if __name__ == "__main__":
    unittest.main()
