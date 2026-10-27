from __future__ import annotations

import tomllib
from pathlib import Path
from unittest import mock

import pytest

from project_control.local_runtime.local_worker.supervisor import (
    DeviceSchedulerManagedModelBackend,
    SupervisorError,
    SupervisorServer,
    main as supervisor_main,
)


_PROFILE_PATH = Path(__file__).resolve().parents[2] / "src/project_control/local_runtime/config/production-profile.toml"


class FakeSchedulerClient:
    def __init__(self, *, state="stopped", endpoint=None, ready=True):
        self.state = state
        self.endpoint = endpoint or {
            "protocol": "openai-compatible",
            "server_kind": "llama.cpp",
            "base_url": "http://127.0.0.1:23111/v1",
            "model_id": "operator-pinned-model",
        }
        self.ready = ready
        self.calls = []

    def _record(self, *, inspected=False):
        return {
            "id": "managed-model",
            "backend_id": "managed-model",
            "state": self.state,
            **({"observed_state": self.state} if inspected else {}),
            "observation": ({
                "state": self.state,
                "readiness": {
                    "ready": self.ready,
                    "identity_verified": self.ready,
                    "endpoint": self.endpoint if self.ready else None,
                },
            } if inspected else None),
        }

    def call(self, operation, arguments):
        self.calls.append((operation, dict(arguments)))
        if operation == "backends.get":
            return self._record()
        if operation == "backends.control" and arguments["action"] == "start":
            self.state = "ready"
            return self._record()
        if operation == "backends.control" and arguments["action"] == "inspect":
            return self._record(inspected=True)
        raise AssertionError(f"unexpected scheduler operation: {operation} {arguments}")


def _transport(method, url, payload, timeout):
    assert 0 < timeout <= 3600
    if url.endswith("/health"):
        return 200, {"status": "ok"}
    if url.endswith("/v1/models"):
        return 200, {"data": [{"id": "operator-pinned-model"}]}
    if url.endswith("/props"):
        return 200, {"default_generation_settings": {"n_ctx": 4096}}
    if url.endswith("/apply-template"):
        return 200, {"prompt": "bounded prompt"}
    if url.endswith("/tokenize"):
        return 200, {"tokens": [1, 2, 3]}
    if url.endswith("/completion"):
        return 200, {
            "content": "bounded answer",
            "tokens": [4],
            "stop_type": "eos",
            "timings": {
                "predicted_n": 1,
                "predicted_ms": 2.0,
                "prompt_n": 3,
                "prompt_ms": 1.0,
                "predicted_per_second": 500.0,
                "prompt_per_second": 3000.0,
            },
        }
    raise AssertionError(f"unexpected backend route: {method} {url}")


def _backend(tmp_path, client=None, *, endpoint=None, ready=True, state="stopped"):
    profile = tomllib.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    client = client or FakeSchedulerClient(state=state, endpoint=endpoint, ready=ready)
    backend = DeviceSchedulerManagedModelBackend(
        tmp_path,
        service_state_root=None,
        profile=profile,
        client=client,
        backend_id="managed-model",
        transport=_transport,
    )
    return backend, client


def test_managed_backend_starts_through_scheduler_and_releases_only_pc_session(tmp_path):
    backend, client = _backend(tmp_path)

    lease = backend.warm()
    assert lease["allocation_authority"] == "device-scheduler"
    assert lease["physical_lifecycle"] == "managed_backend"
    assert lease["base_url"] == "http://127.0.0.1:23111/v1"
    assert [operation for operation, _ in client.calls] == [
        "backends.get", "backends.control", "backends.control",
        "backends.get", "backends.control",
    ]

    released = backend.release(lease["service_lease_id"])
    assert released["scope"] == "pc_logical_session_only"
    assert released["backend_running"] is True
    assert backend._slots[next(iter(backend._slots))].service_lease_id is None
    assert not any(operation in {"backends.control:stop", "backends.control:evict"}
                   for operation, _ in client.calls)


def test_observer_generation_keeps_pc_llama_template_token_and_context_accounting(tmp_path):
    backend, client = _backend(tmp_path)

    result = backend.run_observer_turn({
        "format": "PC-LOCAL-INVESTIGATOR-TURN/2",
        "messages": [{"role": "user", "content": "Return a short answer."}],
        "max_tokens": 8,
        "timeout_seconds": 20,
        "compute_profile": "narrow",
        "parallelism": "default",
        "reasoning_mode": "off",
    })

    assert result["status"] == "available"
    assert result["text"] == "bounded answer"
    assert result["usage"]["effective_context_size"] == 4096
    assert result["usage"]["visible_tokens"] == 1
    assert backend.adapter._servers[backend._device_scheduler_handle]["usage"]["runs"] == 1
    assert [operation for operation, _ in client.calls].count("backends.control") >= 4


def test_unknown_or_unverified_backend_never_falls_back_to_local_allocator(tmp_path):
    unknown = FakeSchedulerClient(state="unknown")
    profile = tomllib.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    with pytest.raises(SupervisorError, match="device_scheduler_backend_not_ready"):
        DeviceSchedulerManagedModelBackend(
            tmp_path, service_state_root=None, profile=profile, client=unknown,
            backend_id="managed-model", transport=_transport,
        )
    assert [operation for operation, _ in unknown.calls] == ["backends.get"]

    unverified = FakeSchedulerClient(state="ready", ready=False)
    with pytest.raises(SupervisorError, match="model_identity_unverified"):
        DeviceSchedulerManagedModelBackend(
            tmp_path, service_state_root=None, profile=profile, client=unverified,
            backend_id="managed-model", transport=_transport,
        )
    assert [operation for operation, _ in unverified.calls] == ["backends.get", "backends.control"]


def test_managed_backend_rejects_remote_endpoint_and_model_identity_changes(tmp_path):
    unsafe = FakeSchedulerClient(endpoint={
        "protocol": "openai-compatible", "server_kind": "llama.cpp",
        "base_url": "http://192.0.2.99/v1", "model_id": "operator-pinned-model",
    })
    profile = tomllib.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    with pytest.raises(SupervisorError, match="endpoint_not_loopback"):
        DeviceSchedulerManagedModelBackend(
            tmp_path, service_state_root=None, profile=profile, client=unsafe,
            backend_id="managed-model", transport=_transport,
        )

    backend, client = _backend(tmp_path)
    client.endpoint = {**client.endpoint, "model_id": "different-model"}
    with pytest.raises(SupervisorError, match="backend_model_identity_mismatch|backend_pinned_model_changed"):
        backend._refresh_managed_backend(allow_start=False)


def test_supervisor_refuses_physical_release_and_handoff_but_stops_locally(tmp_path):
    backend, _client = _backend(tmp_path)
    server = SupervisorServer(backend, root=tmp_path / "runtime")

    for operation in ("drain", "evict", "stop", "preemption-status", "observer-quiesce",
                      "observer-resume", "observer-reclaim-closed", "observer-release-owned"):
        with pytest.raises(SupervisorError, match="non_evictable|handoff"):
            server._dispatch({"operation": operation})

    backend.shutdown_local()
    shutdown = backend.shutdown()
    assert shutdown["pc_supervisor_stopped"] is True
    assert shutdown["backend_preserved"] is True
    assert shutdown["physical_quiescence"] is None
    assert shutdown["evicted"] is False


def test_supervisor_entrypoint_uses_persistent_device_scheduler_authority(tmp_path, monkeypatch):
    from project_control.device_scheduler import ResourceAuthority

    authority = ResourceAuthority(mode="device_scheduler", base_url="http://127.0.0.1:8787",
                                  token_file=str(tmp_path / "token"), model_backend_id="managed-model")
    seen = {}

    class ManagedBackend:
        def __init__(self, *args, **kwargs):
            seen["managed_backend"] = kwargs

    class Server:
        def __init__(self, backend, **kwargs):
            seen["backend"] = backend
            self.backend = backend

        def serve(self):
            return 0

        def stop_accepting(self, *_args):
            pass

    monkeypatch.setattr("project_control.device_scheduler.read_resource_authority", lambda: authority)
    monkeypatch.setattr("project_control.device_scheduler.DeviceSchedulerMcpClient", lambda selected: "authenticated-client")
    monkeypatch.setattr("project_control.local_runtime.local_worker.supervisor.DeviceSchedulerManagedModelBackend", ManagedBackend)
    monkeypatch.setattr("project_control.local_runtime.local_worker.supervisor.ProductionBackend",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local backend selected")))
    monkeypatch.setattr("project_control.local_runtime.local_worker.supervisor.SupervisorServer", Server)
    monkeypatch.setattr("project_control.local_runtime.local_worker.supervisor.signal.signal", lambda *_args: None)

    with monkeypatch.context() as context:
        context.delenv("PROJECT_CONTROL_OBSERVER_GPU_UUIDS", raising=False)
        assert supervisor_main(["--serve", "--repo-root", str(tmp_path)]) == 0
    assert isinstance(seen["backend"], ManagedBackend)
    assert seen["managed_backend"]["client"] == "authenticated-client"
    assert seen["managed_backend"]["backend_id"] == "managed-model"


def test_supervisor_entrypoint_refuses_local_gpu_allowlists_under_scheduler_authority(tmp_path, monkeypatch):
    from project_control.device_scheduler import ResourceAuthority

    authority = ResourceAuthority(mode="device_scheduler", base_url="http://127.0.0.1:8787",
                                  token_file=str(tmp_path / "token"), model_backend_id="managed-model")
    monkeypatch.setattr("project_control.device_scheduler.read_resource_authority", lambda: authority)
    monkeypatch.setattr("project_control.device_scheduler.DeviceSchedulerMcpClient",
                        lambda selected: pytest.fail("must refuse before constructing an MCP client"))
    monkeypatch.setattr("project_control.local_runtime.local_worker.supervisor.signal.signal", lambda *_args: None)
    with monkeypatch.context() as context:
        context.delenv("PROJECT_CONTROL_OBSERVER_GPU_UUIDS", raising=False)
        with pytest.raises(SupervisorError, match="allowlist_conflicts"):
            supervisor_main(["--serve", "--repo-root", str(tmp_path), "--allowed-gpu-uuid", "GPU-local"])


def test_accepted_patch_does_not_invoke_retired_cuda_controller_registry_path(monkeypatch):
    from types import SimpleNamespace
    from project_control.local_runtime.local_worker.controller import IntegrationController

    monkeypatch.setattr("project_control.device_scheduler.read_resource_authority",
                        lambda: SimpleNamespace(mode="device_scheduler"))
    controller = object.__new__(IntegrationController)
    controller.cuda_discovery_runner = lambda _evidence: pytest.fail("retired CUDA controller path was invoked")
    result = controller._cuda_discovery(
        {"cuda_registry": "/unused/registry", "task_id": "task", "repo_root": "/unused"},
        {"accepted": True, "changed_paths": ["kernel.cu"]},
    )
    assert result == {"state": "unavailable", "code": "cuda_controller_retired_under_device_scheduler_authority",
                      "campaign_ids": [], "auto_queued": False}
