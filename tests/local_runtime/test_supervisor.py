from __future__ import annotations

import os
import json
import tempfile
import threading
import time
import unittest
import shutil
import weakref
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from local_runtime import assert_receiver_module, receiver_runtime_path
RECEIVER_ROOT = receiver_runtime_path()

from local_worker import supervisor as _supervisor_module
assert_receiver_module(_supervisor_module)

from local_worker.supervisor import (RPC_FRAME_BYTES, AdapterError, ProductionBackend,
                                     SupervisorClient, SupervisorError, SupervisorServer,
                                     _rpc_frame, runtime_root)


class FakeBackend:
    def __init__(self):
        self.pid = 4242
        self.clients = 0
        self.loaded = False
        self.draining = False
        self.polls = 0
        self.leases = []
        self.admissions = []

    def status(self):
        return {"format": "CORE4-MODEL-SUPERVISOR/1", "running": self.loaded,
                "healthy": self.loaded, "clients": self.clients, "draining": self.draining}

    def admit(self):
        admission = f"admission-{len(self.admissions) + 1}"
        self.admissions.append(admission)
        return {"status": "admitted", "admission_id": admission}

    def cancel_admission(self, admission_id):
        self.admissions.remove(admission_id)
        return {"cancelled": True, "admission_id": admission_id}

    def warm(self, admission_id=None):
        if admission_id is not None:
            self.admissions.remove(admission_id)
        reused = self.loaded
        self.loaded = True
        self.clients += 1
        lease = f"lease-{self.clients}"
        self.leases.append(lease)
        return {"base_url": "http://127.0.0.1:8080/v1", "model_id": "fixture",
                "model_sha256": "a" * 64, "server_pid": self.pid, "owner_id": "owner",
                "gpu_uuids": ["GPU-a", "GPU-b"], "compatibility_key": "b" * 64,
                "slot_id": "slot-a", "service_lease_id": lease, "reused": reused}

    def release(self, service_lease_id=None):
        if service_lease_id is not None:
            self.leases.remove(service_lease_id)
        self.clients = max(0, self.clients - 1)
        return {"released": True, "clients": self.clients}

    def preemption_status(self, service_lease_id=None):
        return {"preempt_requested": False, "draining": self.draining, "clients": self.clients}

    def drain(self):
        self.draining = True
        return {"draining": True, "clients": self.clients}

    def evict(self):
        self.loaded = False
        self.clients = 0
        self.draining = False
        self.admissions.clear()
        return {"evicted": True, "quiescent": True}

    def poll(self):
        self.polls += 1

    def close(self):
        self.evict()


class _CanonicalRuntimeFixture(unittest.TestCase):
    """Keep supervisor tests independent of any live Todo authority."""

    def setUp(self):
        context = {"fixture": "canonical-runtime"}
        identity = SimpleNamespace(public=lambda: context)
        def validate_fixture(received):
            if received is not identity:
                raise AssertionError("supervisor used an unbound test runtime identity")

        binding = mock.patch(
            "local_worker.supervisor.bind_canonical_runtime",
            return_value=(identity, context),
        )
        validation = mock.patch(
            "local_worker.supervisor.validate_canonical_runtime",
            side_effect=validate_fixture,
        )
        subprocess_environment = mock.patch(
            "local_worker.supervisor.subprocess_environment",
            side_effect=AssertionError("fake supervisor tests must not launch subprocesses"),
        )
        binding.start()
        validation.start()
        subprocess_environment.start()
        self.addCleanup(binding.stop)
        self.addCleanup(validation.stop)
        self.addCleanup(subprocess_environment.stop)


class SupervisorTests(_CanonicalRuntimeFixture):
    def test_rpc_frame_fits_one_mib_utf8_normalized_message_with_unicode_and_controls(self):
        content = "😀" * 250_000 + "\x01\n\t"
        messages = [{"role": "user", "content": content}]
        normalized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        self.assertLessEqual(len(normalized.encode("utf-8")), 1024 * 1024)
        envelope = {"operation": "observer-turn", "request": {
            "format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": messages,
            "max_tokens": 2048, "timeout_seconds": 60}}

        frame = _rpc_frame(envelope)
        self.assertLessEqual(len(frame) - 1, RPC_FRAME_BYTES)
        self.assertEqual(json.loads(frame), envelope)

    def test_owner_only_protocol_reuses_and_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            backend = FakeBackend()
            server = SupervisorServer(backend, root=root)
            thread = threading.Thread(target=server.serve, daemon=True)
            thread.start()
            deadline = time.monotonic() + 2
            while not server.socket_path.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            client = SupervisorClient(Path(temporary), root=root)
            first = client.request("warm")
            second = client.request("warm")
            self.assertFalse(first["reused"])
            self.assertTrue(second["reused"])
            self.assertEqual(first["server_pid"], second["server_pid"])
            self.assertEqual(first["compatibility_key"], second["compatibility_key"])
            self.assertEqual(os.stat(server.socket_path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)
            self.assertEqual(client.request("release", service_lease_id=first["service_lease_id"])["clients"], 1)
            self.assertTrue(client.request("drain")["draining"])
            self.assertTrue(client.request("stop")["stopped"])
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertFalse(server.socket_path.exists())

    def test_owner_protocol_admission_is_explicit_and_cancellable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            backend = FakeBackend()
            server = SupervisorServer(backend, root=root)
            thread = threading.Thread(target=server.serve, daemon=True)
            thread.start()
            deadline = time.monotonic() + 2
            while not server.socket_path.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            client = SupervisorClient(Path(temporary), root=root)
            admitted = client.request("admit")
            self.assertEqual(backend.clients, 0)
            self.assertEqual(client.request("cancel-admission", admission_id=admitted["admission_id"])["cancelled"], True)
            client.request("stop")
            thread.join(timeout=2)

    def test_status_does_not_spawn_absent_supervisor(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = SupervisorClient(temporary, root=Path(temporary) / "runtime")
            self.assertEqual(client.request("status"), {
                "format": "CORE4-MODEL-SUPERVISOR/1", "running": False, "healthy": False,
            })

    def test_stale_runtime_identity_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            root.mkdir()
            (root / "supervisor.pid").write_text("99999999\n", encoding="ascii")
            (root / "supervisor.sock").write_text("stale", encoding="ascii")
            (root / "supervisor-state.json").write_text("{}", encoding="ascii")
            client = SupervisorClient(temporary, root=root)
            client._recover_stale()
            self.assertFalse((root / "supervisor.pid").exists())
            self.assertFalse((root / "supervisor.sock").exists())
            self.assertFalse((root / "supervisor-state.json").exists())

    def test_live_owner_is_probed_and_runtime_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "supervisor.sock").touch()
            (root / "supervisor.pid").write_text(str(os.getpid()) + "\n", encoding="ascii")
            client = SupervisorClient(temporary, root=root)
            with mock.patch.object(client, "_request", return_value={"running": True}), \
                 mock.patch("local_worker.supervisor.subprocess.Popen", side_effect=AssertionError("must not restart owner")):
                with self.assertRaisesRegex(SupervisorError, "runtime_identity_mismatch"):
                    client.ensure_running()

    def test_authority_database_path_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = SupervisorClient(temporary, root=Path(temporary) / "runtime")
            observed = dict(client.runtime_context)
            observed["db_path"] = "/different/state.sqlite3"
            with self.assertRaisesRegex(SupervisorError, "runtime_identity_mismatch"):
                client._validate_status({"runtime_identity": observed})


class _Cache:
    def active(self):
        return {"candidate_id": "fixture", "payload_sha256": "a" * 64}

    def verify(self, candidate_id, sha256, full=False):
        return {"ready": True}

    def list(self):
        return [
            {"candidate_id": "fixture", "payload_sha256": "a" * 64, "ready": True},
            {"candidate_id": "fixture-next", "payload_sha256": "b" * 64, "ready": True},
        ]

    @contextmanager
    def lease(self, candidate_id, sha256, owner_id):
        yield Path("/models/fixture.gguf")


class _Host:
    def __init__(self, *, islands=2):
        self.bundles = [
            {"resource_ids": ["accelerator:GPU-a", "accelerator:GPU-b"],
             "exclusive_resources": ["interference:nvlink:a"]},
            {"resource_ids": ["accelerator:GPU-c", "accelerator:GPU-d"],
             "exclusive_resources": ["interference:nvlink:b"]},
        ][:islands]
        self.owners = {}
        self.preemptions = set()
        self.serial = 0
        self.discoveries = 0
        self.priority_changes = 0
        self.reconciliations = []

    def discover_gpus(self): self.discoveries += 1; return []
    def list(self, kind=None):
        return [
            {"id": "accelerator:GPU-a", "tags": {"index": "0", "nvlink_domain": "pair-a"}},
            {"id": "accelerator:GPU-b", "tags": {"index": "2", "nvlink_domain": "pair-a"}},
            {"id": "accelerator:GPU-c", "tags": {"index": "1", "nvlink_domain": "pair-b"}},
            {"id": "accelerator:GPU-d", "tags": {"index": "3", "nvlink_domain": "pair-b"}},
        ][:2 if len(self.bundles) == 1 else 4]
    def compound_gpu_bundles(self, count):
        if count == 4 and len(self.bundles) == 2:
            return [{"resource_ids": ["accelerator:GPU-a", "accelerator:GPU-c",
                                      "accelerator:GPU-b", "accelerator:GPU-d"],
                     "exclusive_resources": [item for bundle in self.bundles for item in bundle["exclusive_resources"]]}]
        return list(self.bundles)
    def reserve_service(self, **kwargs):
        request = kwargs["resource_request"]
        wanted = set([*request["ids"], *request["exclusive_resources"]])
        if any(wanted & value for value in self.owners.values()):
            return None
        self.serial += 1
        owner = f"owner-{self.serial}"
        self.owners[owner] = wanted
        return {"owner_id": owner, "resource_ids": list(request["ids"])}
    def set_priority(self, owner_id, priority):
        self.priority_changes += 1
        return owner_id in self.owners
    def heartbeat(self, owner_id, pid=None): return None
    def reconcile_current_service_owners(self, **kwargs):
        self.reconciliations.append(kwargs)
        return []
    def preempt_requested(self, owner_id): return owner_id in self.preemptions
    def protect_residency(self, owner_id, *, memory_baseline):
        from local_worker.residency import process_identity
        identity = process_identity(os.getpid())
        return {"residency_capability": "f" * 64, "generation": owner_id,
                "origin": {key: identity[key] for key in ("pid", "process_start", "boot_id")},
                "resource_ids": sorted(self.owners[owner_id])}
    def record_residency_process(self, owner_id, *, pid, **kwargs): return None
    def release(self, owner_id, **kwargs): self.owners.pop(owner_id, None)


class _Runtime:
    def __init__(self, islands=2): self.host = _Host(islands=islands)


class _Adapter:
    def describe(self, handle):
        number = int(handle.split("-")[-1])
        return {"base_url": f"http://127.0.0.1:{8079 + number}", "pid": 4200 + number}


class _Service:
    def __init__(self, *, fail_start=0, delay=0.0, fail_run=False, run_delay=0.0):
        self.handles = {}
        self.starts = 0
        self.fail_start = fail_start
        self.delay = delay
        self.loading = 0
        self.maximum_loading = 0
        self.lock = threading.Lock()
        self.fail_run = fail_run
        self.run_delay = run_delay
        self.running = 0
        self.maximum_running = 0
        self.requests = []
        self.contexts = []

    def start(self, name, context):
        self.contexts.append(context)
        with self.lock:
            self.loading += 1
            self.maximum_loading = max(self.maximum_loading, self.loading)
            self.starts += 1
            number = self.starts
        try:
            if self.delay: time.sleep(self.delay)
            if number == self.fail_start: raise RuntimeError("fixture start failure")
            handle = f"handle-{number}"
            self.handles[handle] = True
            return handle
        finally:
            with self.lock: self.loading -= 1

    def health(self, name, handle): return {"healthy": self.handles.get(handle, False)}
    def run(self, name, handle, request):
        self.requests.append((name, handle, request))
        if self.fail_run:
            raise AdapterError("fixture run failure")
        with self.lock:
            self.running += 1
            self.maximum_running = max(self.maximum_running, self.running)
        try:
            if self.run_delay: time.sleep(self.run_delay)
            return {"text": '{"action":"answer"}', "usage": {"completion_tokens": 7}}
        finally:
            with self.lock: self.running -= 1
    def drain(self, name, handle): return {"draining": True}
    def evict(self, name, handle): self.handles[handle] = False; return {"evicted": True}


def _profile(*, maximum=2, ttl=900):
    return {
        "storage": {"cache_root": "/cache", "canonical_root": "/cold"},
        "server": {"binary": "/bin/true", "base_port": 8080, "startup_timeout_seconds": 1,
                   "gpu_layers": 999, "split_mode": "layer"},
        "experiment": {"initial_context": 32768},
        "deployment_policy": {"max_real_workers": maximum, "hot_idle_seconds": ttl},
        "compute_profiles": {"narrow": "fixture", "wide": "fixture-next"},
        "candidates": [{"id": "fixture", "profile": "one-island"},
                       {"id": "fixture-next", "profile": "all-gpu-single-wide"}],
    }


class _PoolBackend(ProductionBackend):
    def __init__(self, *args, **kwargs):
        if kwargs.get("service_state_root") is None:
            fixture_state = tempfile.mkdtemp(prefix="gpu-supervisor-unit-")
            weakref.finalize(self, shutil.rmtree, fixture_state, True)
            kwargs["service_state_root"] = fixture_state
        kwargs.setdefault("residency_observer", lambda uuids: {
            "available": True, "devices": [{"uuid": gpu, "memory_used_mib": 0} for gpu in uuids],
            "processes": []})
        super().__init__(*args, **kwargs)

    def _healthy(self, slot):
        return bool(self.service.health("llama", slot.handle).get("healthy"))

    def _version(self, binary): return "fixture"


class ServicePoolTests(_CanonicalRuntimeFixture):
    def test_failed_vram_cleanup_keeps_physical_owner_until_observed_release(self):
        backend, runtime, service = self.backend(maximum=1)
        lease = backend.warm()
        backend.release(lease["service_lease_id"])
        memory = {"value": 800}
        backend._observe_residency = lambda uuids: {
            "available": True, "devices": [{"uuid": gpu, "memory_used_mib": memory["value"]} for gpu in uuids],
            "processes": []}
        runtime.host.preemptions.add(lease["owner_id"])
        backend.poll()
        self.assertIn(lease["owner_id"], runtime.host.owners)
        self.assertEqual(backend._slots[lease["slot_id"]].state, "draining")
        with self.assertRaises(SupervisorError):
            backend.warm()
        memory["value"] = 0
        backend.poll()
        self.assertNotIn(lease["owner_id"], runtime.host.owners)
        self.assertTrue(backend.cleanup_receipts[-1]["memory_released"])

    def test_active_turn_drains_without_termination_and_blocks_next_turn(self):
        entered, resume = threading.Event(), threading.Event()
        class BlockedService(_Service):
            def run(self, name, handle, request):
                entered.set()
                if not resume.wait(2):
                    raise AssertionError("turn not resumed")
                return super().run(name, handle, request)
        backend, runtime, service = self.backend(service=BlockedService())
        lease = backend.warm()
        request = {"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "session_id": lease["service_lease_id"],
                   "messages": [{"role": "user", "content": "fixture"}],
                   "max_tokens": 20, "timeout_seconds": 2, "compute_profile": "narrow"}
        results = []
        thread = threading.Thread(target=lambda: results.append(backend.run_observer_turn(request)))
        thread.start()
        self.assertTrue(entered.wait(1))
        runtime.host.preemptions.add(lease["owner_id"])
        backend.poll()
        self.assertTrue(service.handles[backend._slots[lease["slot_id"]].handle])
        self.assertIn(lease["owner_id"], runtime.host.owners)
        resume.set()
        thread.join(2)
        self.assertEqual(results[0]["status"], "available")
        self.assertIn("preempt", backend.run_observer_turn(request)["reason"])
        backend.poll()
        self.assertTrue(backend.preemption_status(lease["service_lease_id"])["preempt_requested"])
        self.assertTrue(backend.preemption_status(lease["service_lease_id"])["preempt_requested"])
        backend.release(lease["service_lease_id"])
        backend.close()

    def test_allowed_device_policy_never_falls_back_to_other_island(self):
        backend, runtime, service = self.backend()
        backend.profile["deployment_policy"]["allowed_gpu_uuids"] = ["GPU-c", "GPU-d"]
        lease = backend.warm()
        self.assertEqual(set(lease["gpu_uuids"]), {"GPU-c", "GPU-d"})
        with self.assertRaises(SupervisorError):
            backend.warm()
        self.assertEqual(service.starts, 1)
        backend.close()

    def test_backend_creates_its_private_runtime_namespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            namespace = Path(temporary) / "app" / "observer-analysis"
            _PoolBackend("/read-only-observed-repository", service_state_root=namespace,
                         profile=_profile(maximum=1), cache=_Cache(),
                         runtime=_Runtime(), adapter=_Adapter(), service=_Service(),
                         topology_classifier=lambda: SimpleNamespace(mode="x_mode", status="available"))
            self.assertTrue(namespace.is_dir())
            self.assertEqual(namespace.stat().st_mode & 0o777, 0o700)

    def test_observer_service_state_owns_logs_and_model_leases(self):
        with tempfile.TemporaryDirectory() as temporary:
            namespace = Path(temporary) / "observer-service"
            backend = _PoolBackend("/read-only-observed-repository", service_state_root=namespace,
                                   profile=_profile(maximum=1), cache=_Cache(), runtime=_Runtime(),
                                   adapter=_Adapter(), service=_Service(),
                                   topology_classifier=lambda: SimpleNamespace(mode="x_mode", status="available"))
            self.assertEqual(backend._state_root(), namespace / "state")
            self.assertEqual(backend.service_state_root, namespace)
            self.assertEqual(runtime_root(namespace), namespace / "runtime")

    def test_observer_status_is_in_memory_and_non_probing(self):
        backend, runtime, service = self.backend(maximum=1)
        with mock.patch.object(service, "health", wraps=service.health) as health:
            initial = backend.observer_status()
        self.assertEqual(initial, {
            "format": "CORE4-OBSERVER-STATUS/1", "running": False,
            "healthy": False, "draining": False, "clients": 0,
            "active_leases": 0, "active_admissions": 0, "capacity": 1,
            "slots": [],
        })
        health.assert_not_called()
        self.assertEqual((service.starts, runtime.host.discoveries), (0, 0))

        lease = backend.warm()
        discovery_count = runtime.host.discoveries
        with mock.patch.object(service, "health", wraps=service.health) as health:
            active = backend.observer_status()
        health.assert_not_called()
        self.assertEqual(service.starts, 1)
        self.assertEqual(runtime.host.discoveries, discovery_count)
        self.assertEqual((active["running"], active["healthy"], active["active_leases"]),
                         (True, True, 1))
        self.assertEqual(active["slots"], [{
            "slot_id": lease["slot_id"], "state": "active", "leased": True,
            "compute_profile": "narrow", "parallelism": "layer",
        }])
        backend.release(lease["service_lease_id"])
        backend.close()

    def backend(self, *, islands=2, maximum=2, ttl=900, service=None):
        runtime = _Runtime(islands=islands)
        service = service or _Service()
        topology = {"value": SimpleNamespace(mode="normal", status="available")}
        backend = _PoolBackend(".", profile=_profile(maximum=maximum, ttl=ttl), cache=_Cache(),
                               runtime=runtime, adapter=_Adapter(), service=service,
                               topology_classifier=lambda: topology["value"])
        backend.test_topology = topology
        return backend, runtime, service

    def test_x_mode_admits_idle_service_with_existing_reservation_safety(self):
        backend, runtime, service = self.backend(maximum=1)
        endpoint = backend.warm()
        backend.release(endpoint["service_lease_id"])
        backend.test_topology["value"] = SimpleNamespace(mode="x_mode", status="available")
        admission = backend.admit()
        self.assertEqual(admission["status"], "admitted")
        self.assertEqual(backend.status()["slots"][0]["state"], "idle")
        backend.cancel_admission(admission["admission_id"])
        backend.close()

    def test_topology_change_does_not_disturb_active_work_and_respects_capacity(self):
        backend, runtime, service = self.backend(maximum=1)
        endpoint = backend.warm()
        owners = dict(runtime.host.owners)
        backend.test_topology["value"] = SimpleNamespace(mode="x_mode", status="available")
        with self.assertRaisesRegex(SupervisorError, "resource_unavailable"):
            backend.admit()
        self.assertEqual(dict(runtime.host.owners), owners)
        self.assertTrue(service.handles)
        slot = backend.status()["slots"][0]
        self.assertEqual((slot["state"], slot["leased"]), ("active", True))
        backend.release(endpoint["service_lease_id"])
        backend.close()

    def test_existing_admission_survives_change_to_x_mode(self):
        backend, _, service = self.backend()
        admission = backend.admit()
        backend.test_topology["value"] = SimpleNamespace(mode="x_mode", status="available")
        endpoint = backend.warm(admission["admission_id"])
        self.assertEqual((endpoint["reused"], service.starts), (False, 1))
        backend.release(endpoint["service_lease_id"])
        backend.close()

    def test_direct_warm_reuses_x_mode_slot_with_reservation_checks(self):
        backend, runtime, service = self.backend()
        endpoint = backend.warm()
        backend.release(endpoint["service_lease_id"])
        backend.test_topology["value"] = SimpleNamespace(mode="x_mode", status="available")
        reused = backend.warm()
        self.assertTrue(reused["reused"])
        backend.release(reused["service_lease_id"])
        backend.close()

        backend, runtime, service = self.backend()
        backend.test_topology["value"] = SimpleNamespace(mode="unknown", status="unsupported")
        with self.assertRaisesRegex(SupervisorError, "HOST_TOPOLOGY_UNSUPPORTED"):
            backend.warm()
        self.assertEqual((service.starts, runtime.host.discoveries, runtime.host.owners), (0, 0, {}))
        backend.close()

    def test_two_disjoint_slots_hot_reuse_and_third_rejected(self):
        backend, _, service = self.backend()
        first = backend.warm()
        with self.assertRaisesRegex(SupervisorError, "unknown"):
            backend.release("not-a-lease")
        backend.release(first["service_lease_id"])
        reused = backend.warm()
        self.assertEqual((reused["slot_id"], reused["server_pid"]),
                         (first["slot_id"], first["server_pid"]))
        self.assertTrue(reused["reused"])
        second = backend.warm()
        self.assertNotEqual(reused["slot_id"], second["slot_id"])
        self.assertTrue(set(reused["gpu_uuids"]).isdisjoint(second["gpu_uuids"]))
        with self.assertRaisesRegex(SupervisorError, "resource_unavailable.*retryable"):
            backend.warm()
        self.assertEqual(service.starts, 2)
        backend.close()

    def test_admission_reconciles_only_unrepresented_owners_before_idle_reuse(self):
        backend, runtime, service = self.backend(maximum=1)
        first = backend.warm()
        backend.release(first["service_lease_id"])
        admission = backend.admit(compute_profile="narrow", parallelism="layer")
        self.assertEqual(service.starts, 1)
        self.assertEqual(runtime.host.reconciliations[-1]["live_owner_ids"], {"owner-1"})
        reused = backend.warm(admission["admission_id"])
        self.assertTrue(reused["reused"])
        backend.release(reused["service_lease_id"])
        backend.close()

    def test_two_concurrent_services_reserve_the_two_disjoint_nvlink_pairs(self):
        backend, _, _ = self.backend()
        first, second = backend.warm(), backend.warm()
        self.assertEqual(
            {frozenset(first["gpu_uuids"]), frozenset(second["gpu_uuids"])},
            {frozenset({"GPU-a", "GPU-b"}), frozenset({"GPU-c", "GPU-d"})},
        )
        backend.close()

    def test_compute_profiles_use_compatible_two_or_four_gpu_slots(self):
        backend, _, service = self.backend()
        narrow = backend.warm(compute_profile="narrow")
        self.assertEqual((narrow["model_id"], narrow["compute_profile"], len(narrow["gpu_uuids"])), ("fixture", "narrow", 2))
        self.assertEqual(narrow["gpu_uuids"], ["GPU-a", "GPU-b"])
        self.assertTrue(narrow["p2p_enabled"])
        self.assertEqual(narrow["topology_order"]["nvlink_island_sizes"], [2])
        backend.release(narrow["service_lease_id"])
        reused = backend.warm(compute_profile="narrow")
        self.assertTrue(reused["reused"])
        backend.release(reused["service_lease_id"])
        wide = backend.warm(compute_profile="wide")
        self.assertEqual((wide["model_id"], wide["compute_profile"], len(wide["gpu_uuids"])), ("fixture-next", "wide", 4))
        self.assertEqual(wide["gpu_uuids"], ["GPU-a", "GPU-b", "GPU-c", "GPU-d"])
        self.assertTrue(wide["p2p_enabled"])
        self.assertTrue(wide["topology_order"]["pair_adjacent"])
        self.assertEqual(service.contexts[-1]["service_profile"]["allocated_gpu_uuids"],
                         ["GPU-a", "GPU-b", "GPU-c", "GPU-d"])
        self.assertFalse(wide["reused"])
        self.assertEqual(service.starts, 2)
        backend.close()

    def test_profile_candidate_change_reloads_idle_slot(self):
        backend, _, service = self.backend()
        first = backend.warm(compute_profile="wide")
        self.assertEqual(first["model_id"], "fixture-next")
        backend.release(first["service_lease_id"])
        backend.profile["compute_profiles"]["wide"] = "fixture"
        reloaded = backend.warm(compute_profile="wide")
        self.assertEqual((reloaded["model_id"], reloaded["reused"], service.starts),
                         ("fixture", False, 2))
        backend.close()

    def test_wide_parallelism_override_participates_in_idle_compatibility(self):
        backend, _, service = self.backend()
        layer = backend.warm(compute_profile="wide", parallelism="layer")
        self.assertEqual((layer["parallelism"], service.contexts[-1]["service_profile"]["split_mode"]),
                         ("layer", "layer"))
        backend.release(layer["service_lease_id"])
        tensor = backend.warm(compute_profile="wide", parallelism="tensor")
        self.assertEqual((tensor["parallelism"], tensor["reused"], service.starts,
                          service.contexts[-1]["service_profile"]["split_mode"]),
                         ("tensor", False, 2, "tensor"))
        backend.close()

    def test_startup_generation_knobs_are_forwarded_and_invalidate_idle_server(self):
        backend, _, service = self.backend()
        backend.profile["server"].update({"batch_size": 512, "ubatch_size": 128,
                                           "flash_attention": "auto", "parallel_slots": 1})
        first = backend.warm(compute_profile="narrow", parallelism="layer")
        settings = service.contexts[-1]["service_profile"]
        self.assertEqual((settings["batch_size"], settings["ubatch_size"],
                          settings["flash_attention"], settings["parallel_slots"]),
                         (512, 128, "auto", 1))
        backend.release(first["service_lease_id"])
        backend.profile["server"]["ubatch_size"] = 256
        second = backend.warm(compute_profile="narrow", parallelism="layer")
        self.assertFalse(second["reused"])
        self.assertEqual((service.starts, service.contexts[-1]["service_profile"]["ubatch_size"]),
                         (2, 256))
        backend.close()

    def test_explicit_parallelism_accepts_narrow_layer_and_tensor(self):
        backend, _, service = self.backend()
        layer = backend.warm(compute_profile="narrow", parallelism="layer")
        self.assertEqual(layer["parallelism"], "layer")
        backend.release(layer["service_lease_id"])
        tensor = backend.warm(compute_profile="narrow", parallelism="tensor")
        self.assertEqual((tensor["parallelism"], tensor["reused"], service.starts),
                         ("tensor", False, 2))
        backend.close()

    def test_row_parallelism_is_not_exposed(self):
        backend, _, _ = self.backend()
        with self.assertRaisesRegex(SupervisorError, "parallelism_invalid"):
            backend.warm(compute_profile="wide", parallelism="row")
        backend.close()

    def test_wide_never_evicts_an_active_incompatible_slot(self):
        backend, runtime, service = self.backend()
        active = backend.warm(compute_profile="narrow")
        with self.assertRaisesRegex(SupervisorError, "resource_unavailable"):
            backend.admit("wide")
        self.assertEqual(service.starts, 1)
        self.assertEqual(backend.status()["slots"][0]["state"], "active")
        self.assertIn(active["owner_id"], runtime.host.owners)
        backend.close()

    def test_observer_turn_defaults_to_wide_profile_metadata(self):
        backend, _, service = self.backend()
        result = backend.run_observer_turn({"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": [
            {"role": "system", "content": "investigate"}, {"role": "user", "content": "question"},
        ], "max_tokens": 128, "timeout_seconds": 10})
        self.assertEqual((result["status"], result["model_id"], result["compute_profile"]),
                         ("available", "fixture-next", "wide"))
        self.assertEqual(result["parallelism"], "layer")
        self.assertTrue(result["p2p_enabled"])
        self.assertEqual(result["topology_order"]["nvlink_island_sizes"], [2, 2])
        self.assertEqual(len(backend.status()["slots"][0]["gpu_uuids"]), 4)
        backend.close()

    def test_admission_reserves_capacity_before_model_or_service_start(self):
        backend, runtime, service = self.backend()
        first = backend.admit()
        second = backend.admit()
        self.assertEqual((service.starts, backend.status()["active_admissions"]), (0, 2))
        self.assertEqual(len(runtime.host.owners), 2)
        with self.assertRaisesRegex(SupervisorError, "resource_unavailable"):
            backend.admit()
        with self.assertRaisesRegex(SupervisorError, "resource_unavailable"):
            backend.warm()
        endpoint = backend.warm(first["admission_id"])
        self.assertEqual((service.starts, backend.status()["active_admissions"]), (1, 1))
        backend.release(endpoint["service_lease_id"])
        backend.cancel_admission(second["admission_id"])
        self.assertEqual(backend.status()["active_admissions"], 0)
        backend.close()

    def test_expired_admission_is_swept_without_starting_a_service(self):
        backend, _, service = self.backend()
        backend.admission_ttl = 0
        admission = backend.admit()
        backend.poll()
        self.assertEqual((backend.status()["active_admissions"], service.starts), (0, 0))
        with self.assertRaisesRegex(SupervisorError, "unknown, expired"):
            backend.warm(admission["admission_id"])
        backend.close()

    def test_leases_are_exact_and_double_release_is_rejected(self):
        backend, _, _ = self.backend()
        first, second = backend.warm(), backend.warm()
        with self.assertRaisesRegex(SupervisorError, "ambiguous"):
            backend.release()
        backend.release(first["service_lease_id"])
        self.assertEqual(backend.status()["active_leases"], 1)
        with self.assertRaisesRegex(SupervisorError, "unknown or already released"):
            backend.release(first["service_lease_id"])
        self.assertTrue(any(item["slot_id"] == second["slot_id"] and item["leased"]
                            for item in backend.status()["slots"]))
        backend.close()

    def test_slot_ttl_and_selective_then_global_preemption_are_independent(self):
        backend, runtime, _ = self.backend(ttl=0)
        first, second = backend.warm(), backend.warm()
        backend.release(first["service_lease_id"])
        backend.poll()
        self.assertEqual([item["slot_id"] for item in backend.status()["slots"]], [second["slot_id"]])
        replacement = backend.warm()
        runtime.host.preemptions.add(replacement["owner_id"])
        backend.poll()
        remaining = backend.status()["slots"]
        self.assertEqual([item["slot_id"] for item in remaining], [second["slot_id"]])
        runtime.host.preemptions.add(second["owner_id"])
        backend.poll()
        self.assertFalse(backend.status()["running"])

    def test_cold_starts_serialize_and_second_failure_preserves_first(self):
        service = _Service(delay=.03)
        backend, _, _ = self.backend(service=service)
        results = []
        threads = [threading.Thread(target=lambda: results.append(backend.warm())) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual((len(results), service.maximum_loading), (2, 1))
        backend.close()

        failing = _Service(fail_start=2)
        backend, _, _ = self.backend(service=failing)
        first = backend.warm()
        with self.assertRaisesRegex(RuntimeError, "fixture start failure"):
            backend.warm()
        self.assertEqual(backend.status()["slots"][0]["server_pid"], first["server_pid"])
        backend.release(first["service_lease_id"])
        self.assertTrue(backend.warm()["reused"])
        backend.close()

    def test_single_island_fallback_remains_unchanged(self):
        backend, _, _ = self.backend(islands=1)
        first = backend.warm()
        with self.assertRaisesRegex(SupervisorError, "resource_unavailable"):
            backend.warm()
        backend.release(first["service_lease_id"])
        self.assertTrue(backend.warm()["reused"])
        backend.close()

    def test_observer_packet_schema_budget_and_lease_cleanup(self):
        backend, _, service = self.backend()
        packet = {"source_identity": {"digest": "fixture"}, "query": "What is relevant?",
                  "evidence": [{"id": "E-1", "text": "bounded evidence"}]}
        original = service.run
        def run(name, handle, request):
            original(name, handle, request)
            return {"text": json.dumps({"summary": "Relevant evidence.", "evidence_ids": ["E-1"],
                                        "uncertainty": "Limited fixture."}),
                    "response_metadata": {"finish_reason": "stop"}}
        service.run = run
        try:
            result = backend.analyze_observer_packet(packet)
            self.assertEqual(result["status"], "available")
            request = service.requests[0][2]
            self.assertEqual((request["max_tokens"], request["timeout_seconds"], request["temperature"]), (1024, 90, 0))
            schema = request["response_format"]["schema"]
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(schema["properties"]["evidence_ids"]["items"]["enum"], ["E-1"])
            self.assertIn("not system authority", request["messages"][0]["content"])
            self.assertEqual((backend.status()["active_leases"], backend.status()["active_admissions"]), (0, 0))
        finally:
            backend.close()

    def test_observer_packet_invalid_output_is_typed_and_releases_capacity(self):
        backend, _, service = self.backend()
        packet = {"source_identity": {}, "evidence": [{"id": "E-1", "text": "evidence"}]}
        good = {"summary": "Short answer", "evidence_ids": ["E-1"], "uncertainty": ""}
        cases = [
            ({**good, "evidence_ids": ["unknown"]}, "stop", "observer_provider_unknown_evidence_id"),
            ({**good, "evidence_ids": [1]}, "stop", "observer_provider_invalid_shape"),
            ({**good, "evidence_ids": []}, "stop", "observer_provider_invalid_shape"),
            ({**good, "evidence_ids": ["E-1", "E-1"]}, "stop", "observer_provider_invalid_shape"),
            ({**good, "summary": " "}, "stop", "observer_provider_invalid_shape"),
            ({**good, "summary": "x" * 1201}, "stop", "observer_provider_invalid_shape"),
            ({**good, "uncertainty": 1}, "stop", "observer_provider_invalid_shape"),
            ({**good, "extra": True}, "stop", "observer_provider_invalid_shape"),
            (good, "length", "observer_provider_output_incomplete"),
            ([], "stop", "observer_provider_invalid_shape"),
        ]
        try:
            for value, finish, reason in cases:
                with self.subTest(reason=reason, value=value):
                    service.run = lambda *_args, value=value, finish=finish: {
                        "text": json.dumps(value), "response_metadata": {"finish_reason": finish}}
                    result = backend.analyze_observer_packet(packet)
                    self.assertEqual((result["status"], result["reason"]), ("unavailable", reason))
                    self.assertEqual((backend.status()["active_leases"], backend.status()["active_admissions"]), (0, 0))
            service.run = lambda *_args: {"text": "private invalid content"}
            self.assertEqual(backend.analyze_observer_packet(packet)["reason"], "observer_provider_invalid_shape")
        finally:
            backend.close()

    def test_observer_packet_rejects_empty_or_duplicate_refs_and_busy_without_admission(self):
        backend, runtime, service = self.backend()
        try:
            for evidence in ([], [{"id": ""}], [{"id": "E-1"}, {"id": "E-1"}]):
                result = backend.analyze_observer_packet({"source_identity": {}, "evidence": evidence})
                self.assertEqual(result["reason"], "observer_packet_evidence_refs_invalid")
            backend._analysis_capacity.acquire()
            backend._analysis_capacity.acquire()
            try:
                result = backend.analyze_observer_packet({"source_identity": {}, "evidence": [{"id": "E-1"}]})
            finally:
                backend._analysis_capacity.release()
                backend._analysis_capacity.release()
            self.assertEqual(result["reason"], "observer_provider_busy")
            self.assertEqual((service.starts, runtime.host.owners), (0, {}))
        finally:
            backend.close()

    def test_observer_turn_rejects_non_text_protocol_fields_before_admission(self):
        backend, runtime, service = self.backend()
        request = {"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": [
            {"role": "system", "content": "investigate"},
        ], "max_tokens": 128, "timeout_seconds": 30, "tools": []}
        result = backend.run_observer_turn(request)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "investigator_turn_invalid_request")
        self.assertEqual((service.starts, runtime.host.owners), (0, {}))
        backend.close()

    def test_observer_turn_validates_and_forwards_bounded_json_object_schema(self):
        backend, runtime, service = self.backend()
        base = {"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": [
            {"role": "user", "content": "Return an object."}],
            "max_tokens": 2048, "timeout_seconds": 20, "compute_profile": "narrow"}
        invalid_formats = [
            {"type": "json_object", "schema": {"type": "object"}, "extra": True},
            {"type": "json_schema", "schema": {"type": "object"}},
            {"type": "json_object", "schema": {"type": "array"}},
            {"type": "json_object", "schema": {"type": "object", "$ref": "https://example.test/schema.json"}},
            {"type": "json_object", "schema": {"type": "object", "description": "x" * (16 * 1024)}},
            {"type": "json_object", "schema": {"type": "object", "const": float("nan")}},
        ]
        for response_format in invalid_formats:
            result = backend.run_observer_turn({**base, "response_format": response_format})
            self.assertEqual((result["status"], result["reason"]),
                             ("unavailable", "investigator_turn_response_format_invalid"))
        self.assertEqual((service.starts, runtime.host.owners), (0, {}))

        response_format = {"type": "json_object", "schema": {
            "type": "object", "properties": {"answer": {"$ref": "#/definitions/Answer"}}}}
        result = backend.run_observer_turn({**base, "response_format": response_format})
        self.assertEqual(result["status"], "available")
        forwarded = service.requests[-1][2]["response_format"]
        self.assertEqual(forwarded, response_format)
        self.assertEqual(service.requests[-1][2]["max_tokens"], 2048)
        self.assertEqual(backend.status()["active_leases"], 0)
        backend.close()

    def test_observer_turn_forwards_only_text_messages_and_releases_lease(self):
        backend, _, service = self.backend()
        request = {"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "evidence E-1"},
            {"role": "assistant", "content": "{\"action\":\"search_source\"}"},
        ], "max_tokens": 256, "timeout_seconds": 20, "compute_profile": "narrow"}
        released = []
        original_release = backend.release
        backend.release = lambda lease_id: (released.append(lease_id), original_release(lease_id))[1]
        result = backend.run_observer_turn(request)
        self.assertEqual(result, {"status": "available", "authoritative": False,
                              "text": '{"action":"answer"}',
                              "usage": {"completion_tokens": 7}, "response_metadata": {},
                              "provider": "llama-server",
                              "warm_model_reused": False, "model_id": "fixture",
                              "compute_profile": "narrow", "parallelism": "layer",
                              "p2p_enabled": True,
                              "topology_order": {"gpu_count": 2, "nvlink_island_count": 1,
                                                 "nvlink_island_sizes": [2], "pair_adjacent": True},
                              "compatibility_key": result["compatibility_key"]})
        self.assertEqual({key: service.requests[0][2][key] for key in
                          ("messages", "max_tokens", "timeout_seconds", "observer_generation", "reasoning_mode")},
                         {"messages": request["messages"], "max_tokens": 256, "timeout_seconds": 20.0,
                          "observer_generation": {}, "reasoning_mode": "auto"})
        self.assertEqual(service.requests[0][2]["reasoning_state_key"],
                         released[0])
        self.assertEqual(backend.status()["active_leases"], 0)
        backend.close()

    def test_observer_turn_whitelists_reasoning_mode_and_clears_private_state_on_release(self):
        backend, runtime, service = self.backend()
        invalid = backend.run_observer_turn({"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": [
            {"role": "user", "content": "question"}], "max_tokens": 64,
            "timeout_seconds": 10, "reasoning_mode": "maybe"})
        self.assertEqual((invalid["status"], invalid["reason"]),
                         ("unavailable", "investigator_turn_invalid_request"))
        self.assertEqual((service.starts, runtime.host.owners), (0, {}))
        cleared = []
        backend.adapter.clear_reasoning = lambda handle, key: cleared.append((handle, key))
        answer = backend.run_observer_turn({"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": [
            {"role": "user", "content": "question"}], "max_tokens": 64,
            "timeout_seconds": 10, "compute_profile": "narrow", "reasoning_mode": "off"})
        request = service.requests[-1][2]
        self.assertEqual((answer["status"], request["reasoning_mode"]), ("available", "off"))
        self.assertEqual(cleared, [(service.requests[-1][1], request["reasoning_state_key"])])
        self.assertEqual(backend.status()["active_leases"], 0)
        backend.close()

    def test_observer_turn_accepts_private_message_budget_up_to_one_mib(self):
        backend, runtime, service = self.backend()
        accepted = backend.run_observer_turn({"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": [
            {"role": "user", "content": "x" * (1024 * 1024 - 64)}],
            "max_tokens": 64, "timeout_seconds": 10, "compute_profile": "narrow"})
        self.assertEqual(accepted["status"], "available")
        over_budget = backend.run_observer_turn({"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": [
            {"role": "user", "content": "x" * (1024 * 1024)}],
            "max_tokens": 64, "timeout_seconds": 10, "compute_profile": "narrow"})
        self.assertEqual((over_budget["status"], over_budget["reason"]),
                         ("unavailable", "investigator_turn_messages_too_large"))
        self.assertEqual(service.starts, 1)
        self.assertEqual(backend.status()["active_leases"], 0)
        backend.close()

    def test_adapter_context_budget_error_is_normalized_for_observer_pruning(self):
        backend, _, service = self.backend()
        service.run = lambda *_args: (_ for _ in ()).throw(AdapterError("observer_context_budget"))
        result = backend.run_observer_turn({"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": [
            {"role": "user", "content": "question"}], "max_tokens": 64,
            "timeout_seconds": 10, "compute_profile": "narrow"})
        self.assertEqual((result["status"], result["reason"], result["error_code"]),
                         ("unavailable", "context_budget", "observer_context_budget"))
        backend.close()

    def test_observer_turn_cancels_admission_and_releases_on_errors(self):
        backend, runtime, service = self.backend(service=_Service(fail_run=True))
        request = {"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": [
            {"role": "user", "content": "question"},
        ], "max_tokens": 128, "timeout_seconds": 15}
        failed_run = backend.run_observer_turn(request)
        self.assertEqual((failed_run["status"], backend.status()["active_leases"]), ("unavailable", 0))
        self.assertEqual(backend.status()["active_admissions"], 0)
        owners_before_warm_failure = dict(runtime.host.owners)
        with mock.patch.object(backend, "warm", side_effect=SupervisorError("fixture warm failure")):
            failed_warm = backend.run_observer_turn(request)
        self.assertEqual(failed_warm["status"], "unavailable")
        self.assertEqual(backend.status()["active_admissions"], 0)
        self.assertEqual(runtime.host.owners, owners_before_warm_failure)
        backend.close()

    def test_observer_turn_busy_returns_unavailable_without_admission(self):
        backend, runtime, service = self.backend()
        backend._analysis_capacity.acquire()
        backend._analysis_capacity.acquire()
        try:
            result = backend.run_observer_turn({"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": [
                {"role": "user", "content": "question"},
            ], "max_tokens": 64, "timeout_seconds": 10})
        finally:
            backend._analysis_capacity.release()
            backend._analysis_capacity.release()
        self.assertEqual((result["status"], result["reason"]), ("unavailable", "observer_provider_busy"))
        self.assertEqual((service.starts, runtime.host.owners), (0, {}))
        backend.close()

    def test_two_preopened_narrow_sessions_generate_concurrently_and_return_idle(self):
        service = _Service(run_delay=0.05)
        backend, runtime, _ = self.backend(service=service)
        opened = backend.open_observer_sessions(2, compute_profile="narrow", parallelism="layer")
        sessions = opened["session_ids"]
        slots = backend.status()["slots"]
        self.assertEqual({tuple(slot["gpu_uuids"]) for slot in slots},
                         {("GPU-a", "GPU-b"), ("GPU-c", "GPU-d")})
        request = {"format": "PC-LOCAL-INVESTIGATOR-TURN/2", "messages": [
            {"role": "user", "content": "question"}], "max_tokens": 64,
            "timeout_seconds": 10, "compute_profile": "narrow", "parallelism": "layer"}
        results = []
        threads = [threading.Thread(target=lambda session=session: results.append(
            backend.run_observer_turn({**request, "session_id": session}))) for session in sessions]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual((len(results), service.maximum_running, service.starts), (2, 2, 2))
        self.assertTrue(all(item["status"] == "available" for item in results))
        for session in sessions: backend.close_observer_session(session)
        self.assertEqual((backend.status()["active_leases"],
                          [slot["state"] for slot in backend.status()["slots"]]), (0, ["idle", "idle"]))
        reused = backend.warm(compute_profile="narrow", parallelism="layer")
        self.assertTrue(reused["reused"])
        backend.release(reused["service_lease_id"])
        backend.close()

    def test_one_preopened_narrow_session_is_reusable_for_serial_branches(self):
        backend, _, service = self.backend()
        first = backend.open_observer_sessions(
            1, compute_profile="narrow", parallelism="layer")["session_ids"][0]
        backend.close_observer_session(first)
        second = backend.open_observer_sessions(
            1, compute_profile="narrow", parallelism="layer")["session_ids"][0]
        self.assertEqual(service.starts, 1)
        backend.close_observer_session(second)
        backend.close()

    def test_parallel_open_fails_if_second_nvlink_island_is_unavailable(self):
        backend, runtime, service = self.backend(islands=1, maximum=2)
        with self.assertRaisesRegex(SupervisorError, "resource_unavailable"):
            backend.open_observer_sessions(2, compute_profile="narrow", parallelism="layer")
        self.assertEqual((service.starts, runtime.host.owners,
                          backend.status()["active_admissions"]), (0, {}, 0))
        backend.close()


if __name__ == "__main__":
    unittest.main()
