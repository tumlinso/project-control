"""Actual Unix RPC with CPU model fixtures; no GPU or inference execution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

from tests.local_runtime import assert_receiver_module, receiver_runtime_path
from tests.local_runtime.test_supervisor import _Adapter, _Cache, _Host, _PoolBackend, _Service, _profile
RECEIVER = receiver_runtime_path()
from project_control.runtime_binding import RuntimeBindingError, local_runtime_identity
from local_worker import supervisor as _supervisor_module
assert_receiver_module(_supervisor_module)
from local_worker.supervisor import (RPC_FRAME_BYTES, SupervisorClient, SupervisorError,
                                    SupervisorServer, _check_peer_uid, main)
from local_worker.residency import process_identity, process_start_time


class CentralBackend(_PoolBackend):
    def _version(self, binary, deadline_epoch=None):
        return "fixture"


class CentralSupervisorTests(unittest.TestCase):
    def setUp(self):
        context = {"fixture": "canonical-runtime"}
        identity = SimpleNamespace(root=Path("/todo-authority/runtime"), public=lambda: context)
        binding = patch("local_worker.supervisor.bind_canonical_runtime", return_value=(identity, context))
        validation = patch("local_worker.supervisor.validate_canonical_runtime")
        binding.start()
        validation.start()
        self.addCleanup(binding.stop)
        self.addCleanup(validation.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name)
        self.service = _Service()
        self.host = _Host()
        profile = _profile(maximum=2)
        profile["deployment_policy"]["allowed_gpu_uuids"] = ["GPU-a", "GPU-b", "GPU-c", "GPU-d"]
        self.backend = CentralBackend(self.state, service_state_root=self.state, profile=profile,
            cache=_Cache(), runtime=SimpleNamespace(host=self.host), service=self.service,
            adapter=_Adapter(), topology_classifier=lambda: SimpleNamespace(mode="normal", status="available"))
        self.server = SupervisorServer(self.backend, root=self.state / "runtime", observer_only=True)
        self.failures = []
        def serve():
            try:
                self.server.serve()
            except Exception as error:
                self.failures.append(error)
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        deadline = time.monotonic() + 3
        while not self.server.socket_path.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.server.socket_path.exists())
        self.client = self.new_client()

    def new_client(self):
        return SupervisorClient(self.state, root=self.state / "runtime")

    def shutdown(self):
        self.server.stop_accepting()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.failures, [])

    def turn(self, session_id=None, text="fixture", deadline_epoch=None):
        request = {"format": "PC-LOCAL-INVESTIGATOR-TURN/2",
            "messages": [{"role": "user", "content": text}], "max_tokens": 32,
            "timeout_seconds": 1, "compute_profile": "narrow", "parallelism": "layer"}
        if session_id is not None:
            request["session_id"] = session_id
        if deadline_epoch is not None:
            request["deadline_epoch"] = deadline_epoch
        return request

    def _cold_registry_host(self):
        class Host:
            def __init__(inner):
                inner.devices = {}
                inner.discovery_count = 0
                inner.releases = []

            def owner(inner, owner_id):
                return None

            def discover_gpus(inner):
                inner.discovery_count += 1
                inner.devices = {
                    "accelerator:GPU-a": {"id": "accelerator:GPU-a", "tags": {"nvlink_domain": "pair-a"}},
                    "accelerator:GPU-b": {"id": "accelerator:GPU-b", "tags": {"nvlink_domain": "pair-a"}},
                }
                return list(inner.devices.values())

            def list(inner, kind=None):
                return list(inner.devices.values())

            def release(inner, owner_id, **kwargs):
                inner.releases.append(owner_id)

        host = Host()
        self.backend.runtime.host = host
        self.backend._recovery_checked = False
        return host

    def _write_stale_residency_marker(self, *, uuids=("GPU-a", "GPU-b"), domain="pair-a"):
        marker_path = self.backend._marker_path("old-slot")
        marker_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = {
            "format": "CORE4-OWNED-RESIDENCY/1", "owner_id": "old-owner", "slot_id": "old-slot",
            "project_root": str(self.backend.repo_root),
            "service_state_root": str(self.backend.service_state_root), "source_sha256": "a" * 64,
            "process": {"pid": 99999999, "process_group": 99999999,
                        "executable": str(Path(self.backend.profile["server"]["binary"]).resolve()),
                        "process_start": "prior-boot", "boot_id": "prior-boot"},
            "gpu_uuids": list(uuids), "resource_ids": [*(f"accelerator:{gpu}" for gpu in uuids),
                                                          f"interference:nvlink:{domain}"],
            "memory_baseline": {gpu: 0 for gpu in uuids}, "residency_capability": "f" * 64,
            "generation": "old-owner", "origin": {"pid": 99999999, "process_start": "prior-boot"},
        }
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        marker_path.chmod(0o600)
        return marker_path, marker

    def test_recovery_discovers_cold_host_before_validating_stale_marker(self):
        host = self._cold_registry_host()
        marker_path, marker = self._write_stale_residency_marker()

        def validate_after_discovery(path, loaded_marker):
            self.assertEqual(host.discovery_count, 1)
            self.assertEqual(set(host.devices), {"accelerator:GPU-a", "accelerator:GPU-b"})
            self.assertEqual(path, marker_path)
            self.assertEqual(loaded_marker, marker)

        with patch.object(self.backend, "_recover_orphan_residency", side_effect=validate_after_discovery):
            self.backend._recover_residencies()
        self.assertTrue(self.backend._recovery_checked)
        self.assertTrue(marker_path.exists())

    def test_recovery_keeps_wrong_domain_and_unapproved_gpu_markers_blocked(self):
        cases = (("wrong domain", ("GPU-a", "GPU-b"), "wrong-pair"),
                 ("unapproved GPU", ("GPU-a", "GPU-x"), "pair-a"))
        for label, uuids, domain in cases:
            with self.subTest(case=label):
                host = self._cold_registry_host()
                self.backend.profile["deployment_policy"]["allowed_gpu_uuids"] = ["GPU-a", "GPU-b"]
                marker_path, _ = self._write_stale_residency_marker(uuids=uuids, domain=domain)
                with self.assertRaisesRegex(SupervisorError, "owned_residency_recovery_blocked"):
                    self.backend._recover_residencies()
                self.assertEqual(host.discovery_count, 1)
                self.assertTrue(marker_path.exists())
                self.assertEqual(host.releases, [])
                self.backend._recovery_checked = False

    def test_status_identity_policy_and_client_close_preserve_warm_pool(self):
        sessions = self.client.open_observer_sessions(2, compute_profile="narrow", parallelism="layer")
        first = self.client.observer_status(deadline_epoch=time.time() + 2)
        import local_worker.supervisor as supervisor
        self.assertEqual(first["observer_contract"], "PC-OBSERVER-SUPERVISOR/1")
        self.assertTrue(first["observer_only"])
        self.assertEqual(first["source_sha256"], hashlib.sha256(Path(supervisor.__file__).read_bytes()).hexdigest())
        self.assertEqual(first["project_control_fingerprint"],
                         self.server._project_control_fingerprint)
        self.assertEqual(first["supervisor_pid"], os.getpid())
        self.assertEqual(first["supervisor_process_start"], process_identity(os.getpid())["process_start"])
        self.assertEqual(first["service_state_root"], str(self.state))
        self.assertEqual(first["runtime_root"], str(self.state / "runtime"))
        self.assertEqual(first["allowed_gpu_uuids"], ["GPU-a", "GPU-b", "GPU-c", "GPU-d"])
        self.assertEqual(first["idle_ttl_seconds"], 900)
        self.assertEqual({slot["service_lease_id"] for slot in first["slots"]}, set(sessions["session_ids"]))
        self.assertTrue(all(slot["server_pid"] and slot["owner_id"] for slot in first["slots"]))
        self.client.close()
        second = self.new_client().observer_status()
        self.assertEqual(first["slots"], second["slots"])
        self.assertEqual(first["project_control_fingerprint"],
                         second["project_control_fingerprint"])
        for session in sessions["session_ids"]:
            self.assertTrue(self.client.close_observer_session(session)["released"])
        idle = self.client.observer_status()
        self.assertEqual(idle["active_leases"], 0)
        self.assertTrue(idle["running"])
        self.assertEqual({slot["server_pid"] for slot in first["slots"]},
                         {slot["server_pid"] for slot in idle["slots"]})
        self.client.open_observer_sessions(1)
        self.assertEqual(self.service.starts, 2)

    def test_two_sessions_and_clients_overlap_third_call_busy_status_responsive(self):
        sessions = self.client.open_observer_sessions(2)["session_ids"]
        entered = threading.Barrier(3)
        resume = threading.Event()
        self.addCleanup(resume.set)
        original_run = self.service.run
        def blocked_run(name, handle, request):
            entered.wait(timeout=2)
            if not resume.wait(timeout=3):
                raise RuntimeError("fixture release timed out")
            result = original_run(name, handle, request)
            result["text"] = request["messages"][-1]["content"]
            return result
        self.service.run = blocked_run
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(self.new_client().run_observer_turn, self.turn(sessions[0], "first"))
            second = workers.submit(self.new_client().run_observer_turn, self.turn(sessions[1], "second"))
            entered.wait(timeout=2)
            before = time.monotonic()
            status = self.client.observer_status(deadline_epoch=time.time() + .5)
            self.assertLess(time.monotonic() - before, .5)
            self.assertEqual(status["active_leases"], 2)
            busy = self.client.run_observer_turn(self.turn())
            self.assertEqual(busy["reason"], "observer_provider_busy")
            self.assertEqual(self.client.analyze_observer_packet({})["reason"], "observer_provider_busy")
            resume.set()
            self.assertEqual(first.result(timeout=2)["text"], "first")
            self.assertEqual(second.result(timeout=2)["text"], "second")
        self.assertEqual(len(self.service.requests), 2)
        self.assertEqual(len({request[1] for request in self.service.requests}), 2)

    def test_status_responds_while_cold_start_lifecycle_lock_is_held(self):
        held, release = threading.Event(), threading.Event()
        def lifecycle():
            with self.backend._pool_lock:
                held.set()
                release.wait(timeout=2)
        worker = threading.Thread(target=lifecycle)
        worker.start()
        self.assertTrue(held.wait(timeout=1))
        try:
            self.assertEqual(self.client.observer_status(deadline_epoch=time.time() + .5)["capacity"], 2)
        finally:
            release.set()
            worker.join(timeout=1)

    def test_absent_owner_never_autostarts_or_constructs_backend(self):
        absent = SupervisorClient(self.state, root=self.state / "absent")
        with patch.object(absent, "ensure_running", side_effect=AssertionError("autostart")), \
             patch("local_worker.supervisor.subprocess.Popen", side_effect=AssertionError("spawn")):
            for call in (absent.observer_status, lambda: absent.run_observer_turn(self.turn()),
                         lambda: absent.analyze_observer_packet({}), lambda: absent.open_observer_sessions(1),
                         lambda: absent.close_observer_session("session")):
                with self.assertRaisesRegex(SupervisorError, "central_supervisor_unavailable"):
                    call()
            absent.close()

    def raw(self, data):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(self.server.socket_path))
            connection.sendall(data)
            connection.shutdown(socket.SHUT_WR)
            result = b""
            while b"\n" not in result:
                result += connection.recv(4096)
            return json.loads(result)

    def test_malformed_and_oversized_requests_and_responses_are_explicit(self):
        for data in (b"[]\n", b"not-json\n", b'{"operation":NaN}\n', b'{}', b'{}\n{}\n'):
            with self.subTest(data=data):
                self.assertFalse(self.raw(data)["ok"])
        self.assertEqual(self.raw(b"x" * (RPC_FRAME_BYTES + 1))["error"], "supervisor_frame_too_large")
        with self.assertRaisesRegex(SupervisorError, "frame_too_large"):
            self.client.run_observer_turn({"text": "x" * RPC_FRAME_BYTES})
        self.backend.run_observer_turn = lambda request, **kwargs: {"text": "x" * RPC_FRAME_BYTES}
        with self.assertRaisesRegex(SupervisorError, "frame_too_large"):
            self.client.run_observer_turn(self.turn())

    def test_peer_uid_socket_permissions_symlink_and_owner_pid_are_checked(self):
        peer = Mock()
        peer.getsockopt.return_value = struct.pack("3i", os.getpid(), os.getuid() + 1, os.getgid())
        with self.assertRaisesRegex(SupervisorError, "peer_uid_mismatch"):
            _check_peer_uid(peer)
        self.server.socket_path.chmod(0o666)
        with self.assertRaisesRegex(SupervisorError, "private_path_invalid"):
            self.client.observer_status()
        self.server.socket_path.chmod(0o600)
        alias = self.state / "alias"
        alias.symlink_to(self.server.root)
        with self.assertRaisesRegex(SupervisorError, "private_path_invalid"):
            SupervisorClient(self.state, root=alias).observer_status()
        original = self.server._observer_status
        with patch.object(self.server, "_observer_status", side_effect=lambda: {**original(), "supervisor_pid": 1}):
            with self.assertRaisesRegex(SupervisorError, "process_identity_mismatch"):
                self.client.observer_status()

    def test_peer_stat_authenticates_status_when_executable_resolution_is_denied(self):
        session_id = self.client.open_observer_sessions(1)["session_ids"][0]
        model_pid = next(iter(self.backend._slots.values())).endpoint_descriptor["server_pid"]
        denied_executable_pids = {os.getpid(), model_pid}
        original_read_text = Path.read_text
        original_resolve = Path.resolve

        def read_text(path, *args, **kwargs):
            if path == Path("/proc") / str(model_pid) / "stat":
                fields = ["S", "1", str(model_pid)] + ["0"] * 16 + ["987654"]
                return f"{model_pid} (fixture model) " + " ".join(fields)
            return original_read_text(path, *args, **kwargs)

        def deny_executable(path, *args, **kwargs):
            if (path.name == "exe" and path.parent.parent == Path("/proc") and
                    path.parent.name in {str(pid) for pid in denied_executable_pids}):
                raise PermissionError("fixture executable access denied")
            return original_resolve(path, *args, **kwargs)

        with patch.object(Path, "read_text", read_text), patch.object(Path, "resolve", deny_executable):
            status = self.client.observer_status()
            self.assertEqual(status["supervisor_pid"], os.getpid())
            self.assertEqual(status["supervisor_process_start"], process_start_time(os.getpid()))
            with self.assertRaisesRegex(PermissionError, "fixture executable access denied"):
                process_identity(model_pid)
            closed = self.client.close_observer_session(session_id)

        self.assertTrue(closed["released"])
        self.assertIsNone(closed["owned_resource_receipt"])

    def test_peer_stat_denial_and_pid_reuse_fail_closed(self):
        with patch("local_worker.supervisor.process_start_time", side_effect=PermissionError("peer stat denied")):
            with self.assertRaisesRegex(SupervisorError, "peer stat denied"):
                self.client.observer_status()

        original_status = self.server._observer_status
        with patch.object(self.server, "_observer_status",
                          side_effect=lambda: {**original_status(), "supervisor_process_start": "1"}):
            with self.assertRaisesRegex(SupervisorError, "central_supervisor_process_identity_mismatch"):
                self.client.observer_status()

        session_id = self.client.open_observer_sessions(1)["session_ids"][0]
        with patch("local_worker.supervisor.process_start_time", return_value="1"):
            self.server._reap_borrowers()
        self.assertNotIn(session_id, self.backend._leases)
        self.assertNotIn(session_id, self.server._borrowers)

    def test_process_start_time_validates_stat_pid_and_token(self):
        pid = 8123
        fields = ["S", "1", "8123"] + ["0"] * 16 + ["12345"]
        with patch("local_worker.residency.Path.read_text",
                   return_value=f"{pid} (name with ) paren) " + " ".join(fields)):
            self.assertEqual(process_start_time(pid), "12345")
        with patch("local_worker.residency.Path.read_text", side_effect=PermissionError("stat denied")):
            with self.assertRaisesRegex(PermissionError, "stat denied"):
                process_start_time(pid)
        with patch("local_worker.residency.Path.read_text", return_value="not-a-stat"):
            with self.assertRaisesRegex(ValueError, "process_start_unavailable"):
                process_start_time(pid)
        with patch("local_worker.residency.Path.read_text",
                   return_value=f"{pid + 1} (reused pid) " + " ".join(fields)):
            with self.assertRaisesRegex(ValueError, "process_start_unavailable"):
                process_start_time(pid)

    def test_deadlines_invalid_and_transport_timeout_do_not_evict_pool(self):
        sessions = self.client.open_observer_sessions(1)["session_ids"]
        for deadline in (True, float("nan"), time.time() - 1):
            with self.subTest(deadline=deadline):
                with self.assertRaises((ValueError, TimeoutError, SupervisorError)):
                    self.client.run_observer_turn(self.turn(sessions[0], deadline_epoch=deadline))
        original = self.backend.run_observer_turn
        entered, resume = threading.Event(), threading.Event()
        self.addCleanup(resume.set)
        def delayed(request, **kwargs):
            entered.set()
            resume.wait(timeout=2)
            return original({**request, "deadline_epoch": time.time() + 1}, **kwargs)
        self.backend.run_observer_turn = delayed
        with self.assertRaisesRegex(SupervisorError, "central_supervisor_timeout"):
            self.client.run_observer_turn(self.turn(sessions[0], deadline_epoch=time.time() + .05))
        self.assertTrue(entered.is_set())
        self.assertEqual(self.client.observer_status()["active_leases"], 1)
        resume.set()

    def test_observer_only_denies_maintenance_and_stop_is_verified(self):
        for operation in ("status", "admit", "warm", "release", "evict", "drain"):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(SupervisorError, "maintenance_disabled"):
                    self.client._request(operation)
        with patch.object(self.backend, "evict", return_value={"quiescent": False}):
            with self.assertRaisesRegex(SupervisorError, "stop_not_quiescent"):
                self.client.request("stop")
        self.assertFalse(self.server.stopping)
        self.assertTrue(self.client.request("stop")["stopped"])
        self.thread.join(timeout=2)

    def test_operator_cli_roots_and_gpu_policy_bindings(self):
        args = ["--serve", "--repo-root", str(self.state), "--service-state-root", str(self.state),
                "--runtime-root", str(self.state / "runtime"), "--observer-only", "--allowed-gpu-uuid", "GPU-a"]
        with patch.dict(os.environ, {"PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR": str(self.state),
                                    "PROJECT_CONTROL_OBSERVER_GPU_UUIDS": '["GPU-a","GPU-b"]'}, clear=False), \
             patch("local_worker.supervisor.ProductionBackend") as backend, \
             patch("local_worker.supervisor.SupervisorServer") as server:
            server.return_value.serve.return_value = 0
            self.assertEqual(main(args), 0)
            self.assertEqual(backend.call_args.kwargs["service_state_root"], str(self.state))
            self.assertEqual(backend.call_args.kwargs["profile"]["deployment_policy"]["allowed_gpu_uuids"], ["GPU-a"])
            self.assertTrue(server.call_args.kwargs["observer_only"])
            with self.assertRaisesRegex(SupervisorError, "gpu_allowlist_mismatch"):
                main(args[:-1] + ["GPU-foreign"])
            with self.assertRaisesRegex(SupervisorError, "runtime_root_mismatch"):
                main([*args[:6], "/foreign", *args[7:]])

    def test_connection_bound_and_runtime_identity_rejection(self):
        with patch.object(self.server._connections, "acquire", return_value=False):
            self.assertEqual(self.raw(b'{"operation":"observer-status"}\n')["error"],
                             "supervisor_connections_busy")
        original = self.server.runtime_context
        self.server.runtime_context = {"foreign": True}
        try:
            with self.assertRaisesRegex(SupervisorError, "runtime_identity_mismatch"):
                self.client.observer_status()
        finally:
            self.server.runtime_context = original

    def test_owned_release_source_uses_distinct_manifest_pinned_receiver_root(self):
        receiver = local_runtime_identity()
        self.assertNotEqual(self.client.runtime_identity.root.resolve(), receiver.root.resolve())
        todo_fingerprint = hashlib.sha256(json.dumps(
            self.client.runtime_context, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
        self.assertNotEqual(todo_fingerprint, receiver.fingerprint)
        expected = receiver.root.joinpath("local_worker/supervisor.py").read_bytes()
        manifest = json.loads((receiver.root / "receiver-manifest.json").read_text())
        status = {"source_sha256": hashlib.sha256(expected).hexdigest(),
                  "runtime_identity": self.client.runtime_context,
                  "runtime_fingerprint": todo_fingerprint,
                  "supervisor_pid": os.getpid(), "supervisor_process_start": process_start_time(os.getpid()),
                  "daemon_epoch": "e" * 64}
        self.client._validate_owned_release_status(status)

        with self.subTest("receiver fingerprint is not Todo runtime fingerprint"):
            status["runtime_fingerprint"] = receiver.fingerprint
            with self.assertRaisesRegex(SupervisorError, "receiver_source_identity_mismatch"):
                self.client._validate_owned_release_status(status)
            status["runtime_fingerprint"] = todo_fingerprint

        with self.subTest("tampered daemon source digest"):
            status["source_sha256"] = "0" * 64
            with self.assertRaisesRegex(SupervisorError, "receiver_source_identity_mismatch"):
                self.client._validate_owned_release_status(status)
        status["source_sha256"] = manifest["files"]["local_worker/supervisor.py"]

        with self.subTest("tampered receiver rejected by binding"):
            with patch("local_worker.supervisor.bind_local_runtime",
                       side_effect=RuntimeBindingError("receiver_file_hash_mismatch")):
                with self.assertRaisesRegex(SupervisorError, "receiver_source_identity_unavailable"):
                    self.client._validate_owned_release_status(status)

        with self.subTest("missing receiver rejected by binding"):
            with patch("local_worker.supervisor.bind_local_runtime",
                       side_effect=FileNotFoundError("receiver source missing")):
                with self.assertRaisesRegex(SupervisorError, "receiver_source_identity_unavailable"):
                    self.client._validate_owned_release_status(status)

        with self.subTest("foreign imported origin rejected"):
            with tempfile.TemporaryDirectory() as foreign_dir:
                foreign = Path(foreign_dir) / "supervisor.py"
                foreign.write_text("# foreign receiver\n")
                with patch.object(_supervisor_module, "__spec__", SimpleNamespace(origin=str(foreign))):
                    with self.assertRaisesRegex(SupervisorError, "receiver_source_identity_mismatch"):
                        self.client._validate_owned_release_status(status)

    def _close_owned_receipt(self, session_id):
        def process_identity_for_fixture(pid):
            if pid == os.getpid():
                return process_identity(pid)
            return {"pid": pid, "process_start": f"fixture-{pid}", "boot_id": "fixture"}
        with patch("local_worker.supervisor.process_identity", side_effect=process_identity_for_fixture):
            result = self.client.close_observer_session(session_id)
        self.assertTrue(result["released"])
        receipt = result.get("owned_resource_receipt")
        self.assertIsInstance(receipt, dict)
        return receipt

    def _release_receipts(self, receipts):
        def process_identity_for_fixture(pid):
            if pid == os.getpid():
                return process_identity(pid)
            return {"pid": pid, "process_start": f"fixture-{pid}", "boot_id": "fixture"}
        with patch("local_worker.supervisor.process_identity", side_effect=process_identity_for_fixture):
            return self.client.release_owned_observer_resources(
                receipts, request_id=uuid.uuid4().hex, deadline_epoch=time.time() + 10)

    def test_bound_turn_invalidates_only_its_slot_and_preserves_other_closed_capability(self):
        sessions = self.client.open_observer_sessions(2)["session_ids"]
        receipt_a = self._close_owned_receipt(sessions[0])

        invalid = self.client.run_observer_turn({"format": "invalid", "session_id": sessions[1]})
        self.assertEqual(invalid["status"], "unavailable")
        self.assertEqual(invalid["reason"], "investigator_turn_invalid_request")

        self.assertEqual(self.client.run_observer_turn(self.turn(sessions[1]))["status"], "available")
        receipt_b = self._close_owned_receipt(sessions[1])
        released = self._release_receipts([receipt_a, receipt_b])
        self.assertEqual(released["status"], "released")
        self.assertTrue(all(item["released"] is True for item in released["sessions"]))

    def test_reopening_a_slot_invalidates_its_old_close_capability(self):
        sessions = self.client.open_observer_sessions(2)["session_ids"]
        receipt_a = self._close_owned_receipt(sessions[0])
        slot_a = receipt_a["slot_id"]
        reopened = self.client.open_observer_sessions(1)["session_ids"][0]
        with self.backend._pool_lock:
            self.assertEqual(self.backend._leases[reopened], slot_a)

        stale = self._release_receipts([receipt_a])
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["sessions"][0]["reason"], "owned_capability_unavailable")
        receipt_reopened = self._close_owned_receipt(reopened)
        receipt_b = self._close_owned_receipt(sessions[1])
        released = self._release_receipts([receipt_reopened, receipt_b])
        self.assertEqual(released["status"], "released")

    def test_unbound_turn_keeps_conservative_global_invalidation(self):
        sessions = self.client.open_observer_sessions(2)["session_ids"]
        receipt_a = self._close_owned_receipt(sessions[0])
        receipt_b = self._close_owned_receipt(sessions[1])

        self.assertEqual(self.client.run_observer_turn(self.turn())["status"], "available")
        released = self._release_receipts([receipt_a, receipt_b])
        self.assertEqual(released["status"], "stale")
        self.assertTrue(all(item["reason"] == "owned_capability_unavailable"
                            for item in released["sessions"]))

    def test_packet_analysis_keeps_conservative_global_invalidation(self):
        sessions = self.client.open_observer_sessions(2)["session_ids"]
        receipt_a = self._close_owned_receipt(sessions[0])
        receipt_b = self._close_owned_receipt(sessions[1])

        packet = {"source_identity": {"project": "fixture"},
                  "evidence": [{"id": "fixture-evidence"}], "query": "fixture"}
        self.client.analyze_observer_packet(packet)
        released = self._release_receipts([receipt_a, receipt_b])
        self.assertEqual(released["status"], "stale")
        self.assertTrue(all(item["reason"] == "owned_capability_unavailable"
                            for item in released["sessions"]))

    def test_owner_binding_between_status_and_operation_and_deadline_cap(self):
        self.client.observer_status()
        self.client._observer_owner = (os.getpid(), "wrong-start")
        with self.assertRaisesRegex(SupervisorError, "process_identity_mismatch"):
            self.client.open_observer_sessions(1)
        self.assertEqual(self.service.starts, 0)
        self.client.observer_status()
        with patch.object(self.backend, "run_observer_turn", return_value={"status": "available"}) as run:
            self.client.run_observer_turn(self.turn(deadline_epoch=time.time() + 9000))
            self.assertLessEqual(run.call_args.args[0]["deadline_epoch"] - time.time(), 300)

    def test_signal_stop_verifies_cleanup_of_only_fake_owned_models(self):
        self.client.open_observer_sessions(2)
        self.assertEqual(len(self.host.owners), 2)
        self.server.stop_accepting()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.host.owners, {})
        self.assertEqual(self.backend._slots, {})
        self.assertFalse(self.server.socket_path.exists())
        self.assertEqual(self.failures, [])

    def wait_for_leases(self, count):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.client.observer_status()
            if status["active_leases"] == count:
                return status
            time.sleep(.01)
        self.fail(f"expected {count} leases, got {status}")

    def test_lost_open_response_releases_only_new_session_and_reuses_warm_pid(self):
        other = self.client.open_observer_sessions(1)["session_ids"][0]
        opened, disconnected = threading.Event(), threading.Event()
        self.addCleanup(disconnected.set)
        captured = {}
        original_dispatch = self.server._dispatch
        def delayed_response(request):
            result = original_dispatch(request)
            if request.get("operation") == "observer-open":
                captured.update(result)
                opened.set()
                disconnected.wait(timeout=2)
            return result
        with patch.object(self.server, "_dispatch", side_effect=delayed_response):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.connect(str(self.server.socket_path))
                connection.sendall(b'{"operation":"observer-open","count":1}\n')
                self.assertTrue(opened.wait(timeout=2))
            disconnected.set()
            status = self.wait_for_leases(1)
            self.assertIn(other, self.backend._leases)
        lost = captured["session_ids"][0]
        self.assertNotIn(lost, self.backend._leases)
        self.assertNotIn(lost, self.server._borrowers)
        self.assertEqual(len(status["slots"]), 2)
        idle_pid = next(slot["server_pid"] for slot in status["slots"] if not slot["leased"])
        replacement = self.new_client().open_observer_sessions(1)["session_ids"][0]
        replacement_status = self.client.observer_status()
        self.assertEqual(next(slot["server_pid"] for slot in replacement_status["slots"]
                              if slot["service_lease_id"] == replacement), idle_pid)
        self.assertEqual(self.service.starts, 2)

    def test_delivered_session_dead_client_process_reclaimed_without_evicting_model(self):
        script = """import socket,sys,json
with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
 connection.connect(sys.argv[1])
 connection.sendall(b'{"operation":"observer-open","count":1}\\n')
 data=b''
 while b'\\n' not in data: data+=connection.recv(4096)
 print(data.decode().strip(),flush=True)
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.server.socket_path)],
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = json.loads(result.stdout)
        self.assertTrue(reply["ok"], reply)
        session_id = reply["data"]["session_ids"][0]
        idle = self.wait_for_leases(0)
        self.assertNotIn(session_id, self.server._borrowers)
        self.assertTrue(idle["running"])
        pid = idle["slots"][0]["server_pid"]
        self.new_client().open_observer_sessions(1)
        self.assertEqual(self.client.observer_status()["slots"][0]["server_pid"], pid)
        self.assertEqual(self.service.starts, 1)

    def test_borrower_deadline_is_bounded_and_active_turn_defers_only_its_release(self):
        sessions = self.client.open_observer_sessions(2)["session_ids"]
        before = self.client.observer_status()
        for session_id in sessions:
            borrower = self.server._borrowers[session_id]
            self.assertEqual(borrower["pid"], os.getpid())
            self.assertEqual(borrower["process_start"], process_identity(os.getpid())["process_start"])
            self.assertLessEqual(borrower["deadline_epoch"] - time.time(), 300)
            self.assertGreater(borrower["deadline_epoch"] - time.time(), 290)
        slot = self.backend._slots[self.backend._leases[sessions[0]]]
        with self.backend._pool_lock:
            slot.active_turns += 1
        with self.server._borrowers_lock:
            self.server._borrowers[sessions[0]]["deadline_epoch"] = time.time() - 1
        with patch.object(self.backend, "evict", side_effect=AssertionError("no pool eviction")):
            self.server._reap_borrowers()
            self.assertIn(sessions[0], self.backend._leases)
            self.assertIn(sessions[0], self.server._borrowers)
            with self.backend._pool_lock:
                slot.active_turns -= 1
            self.server._reap_borrowers()
        after = self.client.observer_status()
        self.assertNotIn(sessions[0], self.backend._leases)
        self.assertIn(sessions[1], self.backend._leases)
        self.assertEqual({item["server_pid"] for item in before["slots"]},
                         {item["server_pid"] for item in after["slots"]})
        self.assertEqual(after["active_leases"], 1)

    def test_requested_deadline_bounds_session_and_unknown_process_proof_retains_it(self):
        deadline = time.time() + 10
        session_id = self.client.open_observer_sessions(1, deadline_epoch=deadline)["session_ids"][0]
        self.assertEqual(self.server._borrowers[session_id]["deadline_epoch"], deadline)
        with patch("local_worker.supervisor.process_start_time", side_effect=PermissionError("unknown borrower")):
            self.server._reap_borrowers()
        self.assertIn(session_id, self.backend._leases)
        with self.server._borrowers_lock:
            self.server._borrowers[session_id]["deadline_epoch"] = time.time() - 1
        self.server._reap_borrowers()
        self.assertNotIn(session_id, self.backend._leases)
        self.assertTrue(self.client.observer_status()["running"])

    def test_native_completed_leases_do_not_accumulate_stale_borrower_records(self):
        session_id = self.client.open_observer_sessions(1)["session_ids"][0]
        self.backend.close_observer_session(session_id)
        self.client.open_observer_sessions(1)
        self.assertNotIn(session_id, self.server._borrowers)
        self.assertEqual(len(self.server._borrowers), 1)
        self.assertEqual(self.service.starts, 1)


if __name__ == "__main__":
    unittest.main()
