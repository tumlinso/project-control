"""CPU-only tests for demand service startup and identity checks."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from project_control.assistance.demand_runtime import (
    DemandRuntime,
    DemandRuntimeError,
    RuntimePin,
    _systemctl,
    capture_runtime_pin,
)
import project_control.assistance.demand_runtime as demand_runtime


def pin() -> RuntimePin:
    return RuntimePin(
        release_root=Path("/release/current"),
        release_manifest=Path("/release/current/release-manifest.json"),
        release_digest="a" * 64,
        receiver_manifest_sha256="b" * 64,
        receiver_fingerprint="c" * 64,
        receiver_source_commit="source-commit",
        todo_identity={"fingerprint": "d" * 64},
        todo_runtime_fingerprint="e" * 64,
        project_control_fingerprint="f" * 64,
    )


def status(expected: RuntimePin) -> dict:
    runtime_identity = dict(expected.todo_identity)
    runtime_identity.setdefault("skills_root", "/content/test")
    return {
        "runtime_identity": runtime_identity,
        "runtime_fingerprint": hashlib.sha256(json.dumps(
            runtime_identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            default=str).encode("utf-8")).hexdigest(),
        "receiver_manifest_sha256": expected.receiver_manifest_sha256,
        "receiver_fingerprint": expected.receiver_fingerprint,
        "receiver_source_commit": expected.receiver_source_commit,
        "project_control_fingerprint": expected.project_control_fingerprint,
        "supervisor_pid": 123,
        "supervisor_process_start": "12345",
        "daemon_epoch": "f" * 64,
    }


class DemandRuntimeTests(unittest.TestCase):
    def runtime(self, *, provider=None, pin_factory=None, release_veto=None,
                release_request=None, systemctl=None, process_matches=None,
                state_reader=None, admission_guard_factory=None,
                clock=time.time, sleeper=time.sleep):
        selected = pin()
        return DemandRuntime(
            provider_factory=lambda: provider,
            pin_factory=pin_factory or (lambda: selected),
            systemctl=systemctl or (lambda action, **kwargs: Mock(returncode=0, stdout="", stderr="")),
            state_reader=state_reader or (lambda **kwargs: {"status": "ok", "LoadState": "loaded",
                                            "ActiveState": "active", "SubState": "running", "MainPID": "123"}),
            release_veto=release_veto or (lambda: False),
            release_request=release_request or (lambda: {"release_veto_active": True}),
            admission_guard_factory=admission_guard_factory or (lambda deadline: nullcontext()),
            process_matches=process_matches or (lambda pid, selected_pin: True),
            clock=clock,
            sleeper=sleeper,
        )

    def test_capture_runtime_pin_checks_selected_digest_and_both_identities(self):
        import tempfile
        # Keep path setup explicit while using real manifest digest and symlink
        # resolution as the production helper does.
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            release_root = home / ".local/share/project-control/releases/test"
            release_root.mkdir(parents=True)
            manifest = release_root / "release-manifest.json"
            pc_fingerprint = demand_runtime.package_fingerprint(
                Path(demand_runtime.__file__).resolve().parents[1])
            raw = json.dumps({"schema_version": 3,
                              "project_control_fingerprint": pc_fingerprint,
                              "todo_package_root": "/release/current/site-packages/todo_orchestrator",
                              "todo_runtime_fingerprint": "0" * 64}, sort_keys=True).encode()
            manifest.write_bytes(raw)
            current = home / ".local/share/project-control/current"
            current.symlink_to(release_root, target_is_directory=True)
            receiver_root = release_root / "project_control/local_runtime"
            receiver_root.mkdir(parents=True)
            receiver_manifest = receiver_root / "receiver-manifest.json"
            receiver_raw = b'{"schema_version":1,"files":{}}'
            receiver_manifest.write_bytes(receiver_raw)
            digest = hashlib.sha256(raw).hexdigest()
            todo = SimpleNamespace(
                release_digest=digest,
                release_manifest=manifest,
                public=lambda: {"fingerprint": "todo-fingerprint", "skills_root": "skills"},
            )
            receiver = SimpleNamespace(root=receiver_root, package_root=Path(__file__).resolve().parent,
                                       fingerprint="receiver-fingerprint",
                                       source_commit="receiver-commit",
                                       manifest_sha256=hashlib.sha256(receiver_raw).hexdigest())
            runtime_context = {
                "contract": "TodoPCU-RUNTIME-IDENTITY/1",
                "skills_root": "skills",
                "package_root": "/skills/todo-orchestrator/todo_orchestrator",
                "package_source": "/skills/todo-orchestrator",
                "todo_schema_version": 4,
                "fingerprint": "canonical-todo-fingerprint",
            }
            canonical_runtime = SimpleNamespace(
                __file__=__file__, bind=Mock(return_value=(object(), runtime_context)))
            service_state_root = Path(temporary) / "observer-state"
            with patch.dict(os.environ, {
                "HOME": str(home), "PROJECT_CONTROL_RELEASE_MANIFEST": str(manifest),
                "PROJECT_CONTROL_RELEASE_DIGEST": digest,
            }, clear=False), patch("project_control.assistance.demand_runtime.bind_local_runtime",
                                   return_value=receiver) as receiver_bind, \
                 patch("project_control.assistance.demand_runtime.bind_runtime", return_value=todo), \
                 patch("project_control.observer_analysis.observer_analysis_state_root",
                       return_value=service_state_root), \
                 patch("project_control.assistance.demand_runtime.importlib.import_module",
                       return_value=canonical_runtime):
                selected = capture_runtime_pin()
            self.assertEqual(selected.release_root, release_root.resolve())
            self.assertEqual(selected.release_digest, digest)
            self.assertEqual(selected.receiver_manifest_sha256, hashlib.sha256(receiver_raw).hexdigest())
            self.assertEqual(selected.receiver_fingerprint, "receiver-fingerprint")
            executable_identity = {key: value for key, value in runtime_context.items()
                                   if key != "skills_root"}
            self.assertEqual(selected.todo_identity, executable_identity)
            self.assertEqual(selected.project_control_fingerprint, pc_fingerprint)
            expected_fingerprint = hashlib.sha256(json.dumps(
                executable_identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                default=str).encode("utf-8")).hexdigest()
            self.assertEqual(selected.todo_runtime_fingerprint, expected_fingerprint)
            receiver_bind.assert_called_once_with(expected_release_digest=digest)
            canonical_runtime.bind.assert_called_once_with(service_state_root)

    def test_capture_source_pin_needs_no_release_pointer_and_uses_dynamic_identities(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            source_root = Path(demand_runtime.__file__).resolve().parents[3]
            receiver_root = source_root / "src/project_control/local_runtime"
            receiver = SimpleNamespace(
                root=receiver_root,
                package_root=Path(__file__).resolve().parent,
                manifest_sha256="a" * 64,
                fingerprint="b" * 64,
                source_commit="working-tree",
            )
            todo = SimpleNamespace(release_digest=None, release_manifest=None)
            runtime_context = {
                "contract": "TodoPCU-RUNTIME-IDENTITY/1",
                "skills_root": "/content/first",
                "package_root": "/checkout/src/todo_orchestrator",
                "package_source": "/checkout/src/todo_orchestrator",
                "todo_schema_version": 4,
                "fingerprint": "c" * 64,
            }
            canonical_runtime = SimpleNamespace(
                __file__=__file__, bind=Mock(return_value=(object(), runtime_context)))
            with patch.dict(os.environ, {"HOME": str(Path(temporary) / "no-current-pointer")}, clear=True), \
                 patch("project_control.assistance.demand_runtime.bind_local_runtime",
                       return_value=receiver) as receiver_bind, \
                 patch("project_control.assistance.demand_runtime.bind_runtime", return_value=todo), \
                 patch("project_control.observer_analysis.observer_analysis_state_root",
                       return_value=Path(temporary) / "observer-state"), \
                 patch("project_control.assistance.demand_runtime.importlib.import_module",
                       return_value=canonical_runtime):
                selected = capture_runtime_pin()

            self.assertEqual(selected.runtime_mode, "source")
            self.assertIsNone(selected.release_manifest)
            self.assertIsNone(selected.release_digest)
            self.assertEqual(selected.release_root, source_root)
            self.assertEqual(selected.source_root, source_root)
            self.assertEqual(selected.receiver_manifest_sha256, "a" * 64)
            self.assertEqual(len(selected.project_control_fingerprint), 64)
            self.assertEqual(selected.todo_identity, {key: value for key, value in runtime_context.items()
                                                       if key != "skills_root"})
            self.assertEqual(selected.skills_root, Path("/content/first"))
            receiver_bind.assert_called_once_with(expected_release_digest=None)

    def test_direct_incomplete_or_bad_release_pins_do_not_fall_back_to_source(self):
        for environment in (
            {"PROJECT_CONTROL_RELEASE_MANIFEST": "/missing/manifest.json"},
            {"PROJECT_CONTROL_RELEASE_MANIFEST": "/missing/manifest.json",
             "PROJECT_CONTROL_RELEASE_DIGEST": "bad"},
        ):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(DemandRuntimeError):
                    capture_runtime_pin()

    def test_source_readiness_checks_both_control_plane_identities(self):
        expected = replace(pin(), runtime_mode="source", release_digest=None,
                           release_manifest=None, source_root=Path("/checkout"),
                           skills_root=Path("/content"))
        provider = Mock()
        provider.central_status.return_value = status(expected)
        runtime = self.runtime(provider=provider, pin_factory=lambda: expected,
                               sleeper=lambda _: None)

        result = runtime.ensure_ready(provider=provider, deadline_epoch=time.time() + 10)

        self.assertEqual(result["status"], "ready")
        self.assertIsNone(result["release_digest"])
        self.assertEqual(result["receiver_fingerprint"], expected.receiver_fingerprint)
        self.assertEqual(result["todo_runtime_fingerprint"], expected.todo_runtime_fingerprint)

    def test_source_process_binding_checks_interpreter_checkout_and_pin_absence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            proc_root = Path(temporary) / "proc"
            process = proc_root / "123"
            process.mkdir(parents=True)
            source_root = Path(temporary) / "checkout"
            source_path = source_root / "src"
            source_path.mkdir(parents=True)
            (process / "environ").write_bytes(
                f"PYTHONPATH={source_path}".encode() + b"\0")
            (process / "cmdline").write_bytes(
                b"python\0-m\0project_control.runtime_binding\0local_worker.supervisor\0--serve\0")
            (process / "exe").symlink_to(sys.executable)
            selected = replace(pin(), runtime_mode="source", release_digest=None,
                               release_manifest=None, source_root=source_root)

            self.assertTrue(demand_runtime._process_source_matches(123, selected, proc_root=proc_root))
            (process / "environ").write_bytes(
                f"PYTHONPATH={source_path}\0PROJECT_CONTROL_RELEASE_DIGEST={'a' * 64}\0".encode())
            self.assertFalse(demand_runtime._process_source_matches(123, selected, proc_root=proc_root))

    def test_source_readiness_rejects_old_pc_snapshot_and_accepts_new_bootstrap(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "project_control"
            package.mkdir()
            changed_module = package / "non_receiver_module.py"
            changed_module.write_text("VALUE = 1\n", encoding="utf-8")
            old_fingerprint = demand_runtime.package_fingerprint(package)
            old_pin = replace(pin(), runtime_mode="source", release_digest=None,
                              release_manifest=None, source_root=Path(temporary),
                              project_control_fingerprint=old_fingerprint)
            provider = Mock()
            provider.central_status.return_value = status(old_pin)
            changed_module.write_text("VALUE = 2\n", encoding="utf-8")
            new_fingerprint = demand_runtime.package_fingerprint(package)
            self.assertNotEqual(old_fingerprint, new_fingerprint)
            current_pin = replace(old_pin, project_control_fingerprint=new_fingerprint)
            with self.assertRaisesRegex(DemandRuntimeError,
                                        "inference_project_control_fingerprint_mismatch"):
                demand_runtime._central_ready(provider, current_pin, lambda *_: True,
                    deadline_epoch=time.time() + 2, clock=time.time, expected_main_pid=123)

            provider.central_status.return_value = status(current_pin)
            accepted = demand_runtime._central_ready(provider, current_pin, lambda *_: True,
                deadline_epoch=time.time() + 2, clock=time.time, expected_main_pid=123)
            self.assertEqual(accepted["project_control_fingerprint"],
                             current_pin.project_control_fingerprint)

    def test_status_is_cold_and_never_constructs_or_starts_provider(self):
        provider_factory = Mock(side_effect=AssertionError("status must stay cold"))
        systemctl = Mock(side_effect=AssertionError("status must not start inference"))
        runtime = DemandRuntime(provider_factory=provider_factory, systemctl=systemctl,
                                pin_factory=Mock(side_effect=AssertionError("status must not bind runtime")),
                                state_reader=lambda **kwargs: {"status": "ok", "LoadState": "loaded",
                                    "ActiveState": "inactive", "SubState": "dead", "MainPID": "0"},
                                release_veto=lambda: False)
        result = runtime.status()
        self.assertEqual(result["active_state"], "inactive")
        self.assertEqual(result["main_pid"], 0)
        provider_factory.assert_not_called()
        systemctl.assert_not_called()

    def test_startup_waits_for_ready_and_checks_all_runtime_identities(self):
        expected = pin()
        provider = Mock()
        provider.central_status.side_effect = [ConnectionRefusedError(), status(expected)]
        calls = []
        runtime = self.runtime(provider=provider, systemctl=lambda action, **kwargs: calls.append(action) or Mock(returncode=0),
                               sleeper=lambda _: None)
        result = runtime.ensure_ready(provider=provider, deadline_epoch=time.time() + 10)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["release_digest"], expected.release_digest)
        self.assertEqual(result["receiver_fingerprint"], expected.receiver_fingerprint)
        self.assertEqual(result["todo_runtime_fingerprint"], expected.todo_runtime_fingerprint)
        self.assertEqual(calls, ["start"])
        self.assertEqual(provider.central_status.call_count, 2)

    def test_startup_retries_wrapped_cold_supervisor_transport_then_verifies_ready(self):
        expected = pin()
        provider = Mock()
        provider.central_status.side_effect = [
            RuntimeError("central_supervisor_unavailable"),
            RuntimeError("central_supervisor_transport_timeout"),
            status(expected),
        ]
        runtime = self.runtime(provider=provider, sleeper=lambda _: None)

        result = runtime.ensure_ready(provider=provider, deadline_epoch=time.time() + 10)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(provider.central_status.call_count, 3)

    def test_unknown_and_identity_runtime_errors_fail_without_readiness_retry(self):
        for reason in ("unexpected_status_error", "central_supervisor_process_identity_mismatch"):
            with self.subTest(reason=reason):
                provider = Mock()
                provider.central_status.side_effect = RuntimeError(reason)
                runtime = self.runtime(provider=provider, sleeper=lambda _: None)

                with self.assertRaisesRegex(RuntimeError, reason):
                    runtime.ensure_ready(provider=provider, deadline_epoch=time.time() + 10)

                self.assertEqual(provider.central_status.call_count, 1)

    def test_constant_wrapped_cold_transport_is_bounded_by_request_deadline(self):
        now = [100.0]
        provider = Mock()
        provider.central_status.side_effect = RuntimeError("central_supervisor_unavailable")

        def start(action, **kwargs):
            now[0] += 0.1
            return Mock(returncode=0, stdout="", stderr="")

        runtime = self.runtime(provider=provider, systemctl=start,
            clock=lambda: now[0], sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds))

        with self.assertRaisesRegex(DemandRuntimeError, "inference_supervisor_readiness_timeout"):
            runtime.ensure_ready(provider=provider, deadline_epoch=100.6)

        self.assertLessEqual(now[0], 100.6)
        self.assertEqual(provider.central_status.call_count, 2)

    def test_explicit_start_returns_verified_readiness(self):
        expected = pin()
        provider = Mock()
        provider.central_status.return_value = status(expected)
        runtime = self.runtime(provider=provider)
        result = runtime.start(deadline_epoch=time.time() + 10)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["release_digest"], expected.release_digest)

    def test_admission_guard_covers_only_start_effect_not_readiness_wait(self):
        expected = pin()
        events = []
        class Guard:
            def __enter__(self):
                events.append("guard-enter")
            def __exit__(self, *_):
                events.append("guard-exit")
                return False
        class Provider:
            def central_status(self, **kwargs):
                events.append("status")
                return status(expected)
        runtime = self.runtime(provider=Provider(),
            systemctl=lambda action, **kwargs: (events.append(action) or Mock(returncode=0)),
            admission_guard_factory=lambda deadline: Guard())
        self.assertEqual(runtime.start(deadline_epoch=time.time() + 10)["status"], "ready")
        self.assertEqual(events, ["guard-enter", "start", "guard-exit", "status"])

    def test_status_verifies_active_service_without_starting_it(self):
        expected = pin()
        provider = Mock()
        provider.central_status.return_value = {**status(expected), "healthy": True, "draining": False,
            "capacity": 2, "active_leases": 1, "active_admissions": 0,
            "slots": [{"slot_id": "slot-a", "state": "idle", "leased": False,
                       "server_pid": 4242, "owner_id": "private-owner", "gpu_uuids": ["private-gpu"]},
                      {"slot_id": "slot-b", "state": "active", "leased": True,
                       "server_pid": 4343, "owner_id": "private-owner-2", "gpu_uuids": ["private-gpu-2"]},
                      {"slot_id": "slot-c", "state": "empty", "leased": False}]}
        systemctl = Mock()
        runtime = DemandRuntime(provider_factory=lambda: provider, pin_factory=lambda: expected,
            systemctl=systemctl,
            state_reader=lambda **kwargs: {"status": "ok", "LoadState": "loaded",
                "ActiveState": "active", "SubState": "running", "MainPID": "123"},
            release_veto=lambda: False, process_matches=lambda pid, selected: True)
        observed = runtime.status()
        self.assertEqual(observed["readiness"], "verified_ready")
        self.assertEqual(observed["resident_slots"], 2)
        self.assertEqual(observed["slots"], [
            {"state": "idle", "leased": False, "resident": True},
            {"state": "active", "leased": True, "resident": True},
            {"state": "empty", "leased": False, "resident": False},
        ])
        self.assertEqual(observed["active_leases"], 1)
        self.assertNotIn("server_pid", str(observed))
        self.assertNotIn("private-owner", str(observed))
        self.assertNotIn("private-gpu", str(observed))
        systemctl.assert_not_called()

    def test_status_keeps_unknown_or_pidless_slots_cold(self):
        expected = pin()
        provider = Mock()
        provider.central_status.return_value = {**status(expected), "healthy": True,
            "draining": False, "capacity": 3, "active_leases": 0, "active_admissions": 0,
            "slots": [{"state": "idle", "server_pid": 0},
                      {"state": "active", "server_pid": "4242"},
                      {"state": "warming", "server_pid": 4242}]}
        runtime = self.runtime(provider=provider)

        observed = runtime.status()

        self.assertEqual(observed["resident_slots"], 0)
        self.assertEqual([slot["resident"] for slot in observed["slots"]], [False, False, False])

    def test_status_classifies_wrapped_cold_transport_as_unavailable(self):
        provider = Mock()
        provider.central_status.side_effect = RuntimeError("central_supervisor_unavailable")
        runtime = self.runtime(provider=provider)

        observed = runtime.status()

        self.assertEqual(observed["readiness"], "unavailable")
        self.assertEqual(observed["readiness_reason"], "inference_supervisor_unavailable")

    def test_startup_serializes_concurrent_demands_and_uses_one_service(self):
        expected = pin()
        guard = threading.Lock()
        active = 0
        max_active = 0
        service_calls = []
        class Provider:
            def central_status(self, **kwargs):
                nonlocal active, max_active
                with guard:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.02)
                with guard:
                    active -= 1
                return status(expected)
        provider = Provider()
        runtime = self.runtime(provider=provider,
                               systemctl=lambda action, **kwargs: service_calls.append(action) or Mock(returncode=0),
                               sleeper=lambda _: None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: runtime.ensure_ready(provider=provider,
                deadline_epoch=time.time() + 10), range(2)))
        self.assertEqual([item["status"] for item in results], ["ready", "ready"])
        # StartUnit effects serialize, but metadata readiness polls may proceed
        # concurrently after the narrow process lock is released.
        self.assertEqual(service_calls, ["start", "start"])
        self.assertEqual(max_active, 2)

    def test_release_veto_prevents_start(self):
        systemctl = Mock()
        runtime = self.runtime(systemctl=systemctl, release_veto=lambda: True)
        with self.assertRaisesRegex(DemandRuntimeError, "assistance_release_veto_active"):
            runtime.ensure_ready(provider=Mock(), deadline_epoch=time.time() + 10)
        systemctl.assert_not_called()

    def test_identity_mismatch_is_hard_failure_without_restart(self):
        expected = pin()
        provider = Mock()
        provider.central_status.return_value = {**status(expected), "runtime_fingerprint": "0" * 64}
        systemctl = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        runtime = self.runtime(provider=provider, systemctl=systemctl, sleeper=lambda _: None)
        with self.assertRaisesRegex(DemandRuntimeError, "inference_todo_fingerprint_mismatch"):
            runtime.ensure_ready(provider=provider, deadline_epoch=time.time() + 10)
        self.assertEqual(systemctl.call_count, 1)
        self.assertEqual(provider.central_status.call_count, 1)

    def test_different_todo_domain_context_is_hard_failure_even_with_matching_fingerprint(self):
        expected = pin()
        foreign_context = {**expected.todo_identity, "package_root": "/other/todo/package"}
        foreign_fingerprint = hashlib.sha256(json.dumps(
            foreign_context, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            default=str).encode("utf-8")).hexdigest()
        provider = Mock()
        provider.central_status.return_value = {
            **status(expected), "runtime_identity": foreign_context,
            "runtime_fingerprint": foreign_fingerprint,
        }
        systemctl = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        runtime = self.runtime(provider=provider, systemctl=systemctl, sleeper=lambda _: None)

        with self.assertRaisesRegex(DemandRuntimeError, "inference_todo_identity_mismatch"):
            runtime.ensure_ready(provider=provider, deadline_epoch=time.time() + 10)

        self.assertEqual(systemctl.call_count, 1)
        self.assertEqual(provider.central_status.call_count, 1)

    def test_receiver_identity_mismatch_is_hard_failure_without_restart(self):
        expected = pin()
        provider = Mock()
        provider.central_status.return_value = {
            **status(expected), "receiver_manifest_sha256": "0" * 64,
        }
        systemctl = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        runtime = self.runtime(provider=provider, systemctl=systemctl, sleeper=lambda _: None)
        with self.assertRaisesRegex(DemandRuntimeError, "inference_receiver_identity_mismatch"):
            runtime.ensure_ready(provider=provider, deadline_epoch=time.time() + 10)
        self.assertEqual(systemctl.call_count, 1)
        self.assertEqual(provider.central_status.call_count, 1)

    def test_systemd_main_pid_mismatch_is_hard_failure_without_restart(self):
        expected = pin()
        provider = Mock()
        provider.central_status.return_value = status(expected)
        systemctl = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        runtime = self.runtime(provider=provider, systemctl=systemctl,
            state_reader=lambda **kwargs: {"status": "ok", "LoadState": "loaded",
                "ActiveState": "active", "SubState": "running", "MainPID": "456"},
            sleeper=lambda _: None)
        with self.assertRaisesRegex(DemandRuntimeError, "inference_supervisor_service_pid_mismatch"):
            runtime.ensure_ready(provider=provider, deadline_epoch=time.time() + 10)
        self.assertEqual(systemctl.call_count, 1)
        self.assertEqual(provider.central_status.call_count, 1)

    def test_readiness_failure_is_bounded_by_request_deadline(self):
        now = [100.0]
        provider = Mock()
        provider.central_status.side_effect = ConnectionRefusedError()
        def start(action, **kwargs):
            now[0] += 0.4
            return Mock(returncode=0, stdout="", stderr="")
        systemctl = Mock(side_effect=start)
        runtime = self.runtime(provider=provider, systemctl=systemctl,
                               clock=lambda: now[0],
                               sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds))
        with self.assertRaisesRegex(DemandRuntimeError, "inference_supervisor_readiness_timeout"):
            runtime.ensure_ready(provider=provider, deadline_epoch=100.6)
        self.assertLessEqual(now[0], 100.6)
        self.assertAlmostEqual(systemctl.call_args.kwargs["timeout"], 0.6)
        self.assertEqual(provider.central_status.call_count, 1)

    def test_time_waiting_for_start_lock_consumes_same_startup_deadline(self):
        from project_control.assistance import demand_runtime
        now = [100.0]
        class SlowLock:
            def acquire(self, *, timeout):
                now[0] += 0.7
                return True
            def release(self):
                return None
        provider = Mock()
        systemctl = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        runtime = self.runtime(provider=provider, systemctl=systemctl,
                               clock=lambda: now[0], sleeper=lambda _: None)
        with patch.object(demand_runtime, "_START_LOCK", SlowLock()):
            with self.assertRaisesRegex(DemandRuntimeError, "demand_deadline_exhausted_before_start"):
                runtime.ensure_ready(provider=provider, deadline_epoch=100.6)
        systemctl.assert_not_called()
        provider.central_status.assert_not_called()

    def test_real_lock_wait_is_bounded_by_short_request_deadline(self):
        from project_control.assistance import demand_runtime
        lock = threading.Lock()
        lock.acquire()
        released = threading.Event()
        def release_later():
            time.sleep(0.5)
            lock.release()
            released.set()
        holder = threading.Thread(target=release_later)
        holder.start()
        provider = Mock()
        systemctl = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        runtime = self.runtime(provider=provider, systemctl=systemctl)
        started = time.monotonic()
        with patch.object(demand_runtime, "_START_LOCK", lock):
            with self.assertRaisesRegex(DemandRuntimeError, "inference_start_lock_timeout"):
                runtime.ensure_ready(provider=provider, deadline_epoch=time.time() + 0.1)
        elapsed = time.monotonic() - started
        holder.join(timeout=1)
        self.assertTrue(released.is_set())
        self.assertLess(elapsed, 0.35)
        systemctl.assert_not_called()
        provider.central_status.assert_not_called()

    def test_systemctl_preserves_sub_100ms_remaining_time(self):
        with patch("project_control.assistance.demand_runtime.subprocess.run",
                   return_value=Mock(returncode=0, stdout="", stderr="")) as run:
            _systemctl("start", timeout=0.025)
        self.assertEqual(run.call_args.kwargs["timeout"], 0.025)
        with self.assertRaisesRegex(DemandRuntimeError, "demand_deadline_exhausted_before_start"):
            _systemctl("start", timeout=0)

    def test_delayed_systemd_show_receives_remaining_budget_and_cannot_return_ready_late(self):
        now = [100.0]
        observed_timeouts = []
        def delayed_show(*, timeout):
            observed_timeouts.append(timeout)
            now[0] += timeout + 0.005
            return {"status": "ok", "LoadState": "loaded", "ActiveState": "active",
                    "SubState": "running", "MainPID": "123"}
        provider = Mock()
        provider.central_status.return_value = status(pin())
        runtime = self.runtime(provider=provider,
            systemctl=lambda action, **kwargs: Mock(returncode=0, stdout="", stderr=""),
            state_reader=delayed_show, clock=lambda: now[0], sleeper=lambda _: None)
        with self.assertRaisesRegex(DemandRuntimeError, "demand_deadline_exhausted_before_start"):
            runtime.ensure_ready(provider=provider, deadline_epoch=100.025)
        self.assertEqual(len(observed_timeouts), 1)
        self.assertAlmostEqual(observed_timeouts[0], 0.025)
        provider.central_status.assert_not_called()

    def test_selected_runtime_change_during_readiness_is_rejected(self):
        expected = pin()
        changed = replace(expected, release_digest="9" * 64)
        provider = Mock()
        provider.central_status.return_value = status(expected)
        pins = iter([expected, changed])
        runtime = self.runtime(provider=provider, pin_factory=lambda: next(pins), sleeper=lambda _: None)
        with self.assertRaisesRegex(DemandRuntimeError, "selected_runtime_changed_during_startup"):
            runtime.ensure_ready(provider=provider, deadline_epoch=time.time() + 10)

    def test_optional_content_root_change_does_not_change_executable_pin(self):
        expected = replace(pin(), skills_root=Path("/content/first"))
        changed_content = replace(expected, skills_root=Path("/content/second"))
        provider = Mock()
        provider.central_status.return_value = status(expected)
        pins = iter([expected, changed_content])
        runtime = self.runtime(provider=provider, pin_factory=lambda: next(pins), sleeper=lambda _: None)

        result = runtime.ensure_ready(provider=provider, deadline_epoch=time.time() + 10)

        self.assertEqual(result["status"], "ready")

    def test_expired_deadline_and_cancellation_do_not_start(self):
        systemctl = Mock()
        runtime = self.runtime(systemctl=systemctl)
        with self.assertRaisesRegex(DemandRuntimeError, "demand_deadline_exhausted_before_start"):
            runtime.ensure_ready(provider=Mock(), deadline_epoch=time.time() - 1)
        with self.assertRaisesRegex(DemandRuntimeError, "demand_cancelled_before_start"):
            runtime.ensure_ready(provider=Mock(), cancelled=lambda: True,
                                 deadline_epoch=time.time() + 10)
        systemctl.assert_not_called()

    def test_stop_requires_cancellation_and_owned_release_proof(self):
        systemctl = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        veto = [False]
        def persist_veto():
            veto[0] = True
            return {"release_veto_active": True}
        runtime = self.runtime(systemctl=systemctl,
                               release_veto=lambda: veto[0],
                               release_request=persist_veto)
        pending = runtime.stop()
        self.assertEqual(pending["status"], "needs_coordination")
        self.assertTrue(pending["release_veto_active"])
        self.assertEqual(pending["next_action"], "project-control assistance resume --release")
        self.assertEqual(runtime.stop(coordinate_stop=lambda: {"active_work_cancelled": True,
                            "owned_resources_released": False})["status"], "not_stopped")
        self.assertTrue(veto[0])
        systemctl.assert_not_called()
        result = runtime.stop(coordinate_stop=lambda: {"active_work_cancelled": True,
                            "owned_resources_released": True})
        self.assertEqual(result["status"], "stopped")
        self.assertTrue(result["release_veto_active"])
        with self.assertRaisesRegex(DemandRuntimeError, "assistance_release_veto_active"):
            runtime.ensure_ready(provider=Mock(), deadline_epoch=time.time() + 10)
        self.assertEqual(systemctl.call_args.args[0], "stop")


if __name__ == "__main__":
    unittest.main()
