from __future__ import annotations

import os
import hashlib
import tempfile
import unittest
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

from project_control.observer_analysis import (
    DisabledObserverAnalysisProvider,
    ObserverAnalysisRegistry,
    SkillsObserverAnalysisProvider,
    observer_analysis_state_root,
)
from project_control.runtime_binding import local_runtime_identity


RECEIVER_SOURCE = Path(__file__).resolve().parents[1] / "src/project_control/local_runtime"


def _receiver_fixture():
    # Source fixtures use the current in-memory inventory, independent of the
    # checked-in release manifest. Frozen-release tests keep strict verification.
    return local_runtime_identity(root=RECEIVER_SOURCE)


def _supervisor_module(identity, client_type):
    source = identity.package_root / "supervisor.py"
    client_type.source_path = source
    return SimpleNamespace(__file__=str(source), SupervisorClient=client_type)


class _Client:
    def __init__(self, service_root, **kwargs):
        self.root = Path(service_root)
    def observer_status(self, **kwargs):
        source = Path(type(self).source_path)
        return {"observer_contract": "PC-OBSERVER-SUPERVISOR/1", "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "service_state_root": str(self.root), "runtime_root": str(self.root / "runtime"),
                "observer_only": True, "supervisor_pid": 123, "supervisor_process_start": "start"}


class _Provider:
    created = 0
    closed = 0
    def __init__(self, root: str):
        self.root = root
        self._backend = None
        type(self).created += 1
    def analyze(self, packet):
        return {"status": "available", "root": self.root, "packet": packet}
    def close(self):
        type(self).closed += 1


class ObserverAnalysisRegistryTests(unittest.TestCase):
    def test_packet_only_route_uses_inert_copy_and_no_observed_root(self):
        captured = []
        class PacketProvider(_Provider):
            def analyze(self, packet):
                captured.append(packet)
                packet["evidence"][0]["content"] = "backend edit"
                return {"status": "available", "summary": "bounded finding", "evidence_ids": ["ev-1"]}
        packet = {"query": "Volta", "source_identity": {"skill": "skill-123", "corpus": "digest"},
                  "origin": "agent_skill", "authority": "advisory_instruction", "mutation_authority": False,
                  "evidence": [{"id": "ev-1", "resource": "references/volta.md", "content": "original"}]}
        registry = ObserverAnalysisRegistry(PacketProvider)
        result = registry.analyze_packet(packet)
        self.assertEqual(result["status"], "available", result)
        self.assertEqual(packet["evidence"][0]["content"], "original")
        self.assertIsNone(registry._providers["local-observer-service"].root)
        self.assertFalse(result["mutation_authority"])
        self.assertEqual(captured[0]["source_identity"], packet["source_identity"])

    def test_packet_only_preserves_unavailable_backend_reason(self):
        class BusyProvider(_Provider):
            def analyze(self, packet):
                return {"status": "unavailable", "reason": "observer_provider_busy", "provider": "llama-server"}
        packet = {"source_identity": {"skill": "skill-123"}, "evidence": [{"id": "ev-1", "content": "bounded"}]}
        result = ObserverAnalysisRegistry(BusyProvider).analyze_packet(packet)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "observer_provider_busy")
        self.assertEqual(result["provider"], "llama-server")
        self.assertEqual(result["evidence_ids"], ["ev-1"])
        class PrivateProvider(_Provider):
            def analyze(self, packet):
                return {"status": "unavailable", "reason": "failure at /home/private/service sk_abcdefghijklmnopqrstuv"}
        private = ObserverAnalysisRegistry(PrivateProvider).analyze_packet(packet)
        self.assertNotIn("/home/private", private["reason"])
        self.assertNotIn("sk_abcdefghijkl", private["reason"])

    def test_packet_only_rejects_capabilities_size_and_invalid_citations(self):
        class BadProvider(_Provider):
            def analyze(self, packet):
                return {"status": "available", "summary": "bad", "evidence_ids": ["outside"]}
        registry = ObserverAnalysisRegistry(BadProvider)
        packet = {"source_identity": {"skill": "skill-123"}, "evidence": [{"id": "ev-1", "content": "bounded"}]}
        result = registry.analyze_packet(packet)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["evidence_ids"], ["ev-1"])
        for invalid in ({**packet, "tools": []}, {**packet, "source_identity": {"root": "/private"}},
                        {**packet, "evidence": [{"id": "ev-1", "content": "x" * 65536}]}):
            with self.subTest(packet_keys=list(invalid)):
                fresh = ObserverAnalysisRegistry(BadProvider)
                self.assertEqual(fresh.analyze_packet(invalid)["status"], "unavailable")
                self.assertEqual(fresh._providers, {})

    def test_status_never_creates_provider_or_backend(self):
        _Provider.created = 0
        registry = ObserverAnalysisRegistry(_Provider)
        with mock.patch("project_control.observer_analysis.bind_local_runtime",
                        side_effect=AssertionError("status must not bind or import a model runtime")) as bind, \
             mock.patch("project_control.observer_analysis.importlib.import_module",
                        side_effect=AssertionError("status must not import a model runtime")) as importer:
            provider = SkillsObserverAnalysisProvider()
            result = registry.status()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "observer_analysis_registry")
        self.assertFalse(result["running"])
        self.assertEqual(_Provider.created, 0)
        bind.assert_not_called()
        importer.assert_not_called()

    def test_module_import_and_status_do_not_import_or_launch_model_runtime(self):
        source = Path(__file__).resolve().parents[1] / "src"
        script = r'''
import json, sys
from project_control.observer_analysis import SkillsObserverAnalysisProvider
from project_control.observer_analysis import ObserverAnalysisRegistry
provider = SkillsObserverAnalysisProvider()
status = ObserverAnalysisRegistry().status()
assert status["status"] == "ok"
assert not any(name == "local_worker" or name.startswith("local_worker.") for name in sys.modules)
assert "llama_cpp" not in sys.modules
print(json.dumps({"status": status["status"], "provider_available": provider.available}))
'''
        environment = dict(os.environ, PYTHONPATH=str(source),
                           XDG_CACHE_HOME="/tmp/pc-pa1-import-cache")
        environment.pop("PROJECT_CONTROL_SKILLS_ROOT", None)
        result = subprocess.run([sys.executable, "-c", script], env=environment,
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertIn('"status": "ok"', result.stdout)
        self.assertIn('"provider_available": true', result.stdout)

    def test_status_reports_existing_backend_compactly(self):
        class Backend:
            def observer_status(self):
                return {
                    "running": True, "healthy": True, "draining": False,
                    "capacity": 2, "active_leases": 1, "active_admissions": 0,
                    "slots": [{"state": "ready", "leased": True,
                               "endpoint": "/private/endpoint", "gpu_uuids": ["private"]}],
                }

            def status(self):
                raise AssertionError("observer_status should be preferred")

        registry = ObserverAnalysisRegistry(_Provider)
        provider = _Provider("/tmp/observer")
        provider._backend = Backend()
        registry._providers["local-observer-service"] = provider
        result = registry.status()
        self.assertEqual(result["source"], "observer_analysis_backend")
        self.assertTrue(result["running"])
        self.assertEqual(result["slots"], [{"slot": 0, "state": "ready", "leased": True}])
        self.assertNotIn("endpoint", str(result))
    def test_investigator_turn_is_translated_to_skills_chat_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            identity = _receiver_fixture()
            captured = []
            class Backend(_Client):
                def __init__(self, *args, **kwargs): super().__init__(*args, **kwargs)
                def run_observer_turn(self, request):
                    captured.append(request)
                    return {"status": "available", "text": '{"action":"answer","answer":{}}'}
            module = _supervisor_module(identity, Backend)
            with mock.patch.dict(os.environ, {"PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR": str(base / "state")}, clear=False), \
                 mock.patch("project_control.observer_analysis.bind_local_runtime", return_value=identity), \
                 mock.patch("project_control.observer_analysis.importlib.import_module", return_value=module):
                result = SkillsObserverAnalysisProvider(base / "observed").investigate_turn({
                    "protocol": "PC-LOCAL-INVESTIGATOR-TURN/2", "max_tokens": 123, "timeout_seconds": 12,
                    "parallelism": "row", "messages": [{"role": "system", "content": "system"},
                        {"role": "user", "content": "question"}, {"role": "assistant", "content": "prior"}],
                })
            self.assertEqual(result["status"], "available", result)
            self.assertEqual(captured[0]["format"], "PC-LOCAL-INVESTIGATOR-TURN/2")
            self.assertEqual(captured[0]["messages"][0], {"role": "system", "content": "system"})
            self.assertEqual(captured[0]["messages"][1]["content"], "question")
            self.assertEqual(len(captured[0]["messages"]), 3)
            self.assertEqual(captured[0]["compute_profile"], "narrow")
            self.assertEqual(captured[0]["parallelism"], "row")
            self.assertEqual(captured[0]["max_tokens"], 123)
            self.assertEqual(captured[0]["timeout_seconds"], 12)

    def test_skills_provider_uses_private_service_state_not_observed_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            observed = base / "observed-read-only-project"
            observed.mkdir()
            identity = _receiver_fixture()
            captured: list[Path] = []
            captured_state: list[Path] = []

            class Backend(_Client):
                def __init__(self, service_root, *, root):
                    super().__init__(service_root)
                    captured.append(Path(service_root))
                    captured_state.append(Path(root))

            module = _supervisor_module(identity, Backend)
            with mock.patch.dict(os.environ, {
                "PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR": str(base / "service-state"),
            }, clear=False), \
                    mock.patch("project_control.observer_analysis.bind_local_runtime", return_value=identity), \
                    mock.patch("project_control.observer_analysis.importlib.import_module", return_value=module):
                provider = SkillsObserverAnalysisProvider(observed)
                provider._get_backend()
                self.assertEqual(captured, [base / "service-state"])
                self.assertEqual(captured_state, [base / "service-state/runtime"])
                self.assertNotEqual(captured[0], observed)
                self.assertTrue(captured[0].is_dir())
                self.assertFalse((observed / ".todo-orchestrator").exists())

    def test_observer_state_root_is_owner_private(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR": temporary}, clear=False
        ):
            root = observer_analysis_state_root()
            self.assertEqual(root, Path(temporary).resolve())
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_unavailable_provider_returns_packet_linked_content(self):
        result = DisabledObserverAnalysisProvider().analyze({"source_identity": {"project": "p"}, "evidence": [
            {"id": "ev-1", "text": "the authority is current"}, {"id": "ev-2", "title": "integration receipt"},
        ]})
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["authoritative"])
        self.assertEqual(result["evidence_ids"], ["ev-1", "ev-2"])
        self.assertIn("authority is current", result["summary"])

    def test_two_calls_reuse_one_provider_and_shutdown_closes_it(self):
        _Provider.created = _Provider.closed = 0
        registry = ObserverAnalysisRegistry(_Provider)
        self.assertEqual(registry.analyze("/tmp/observer-reuse", {"one": 1})["status"], "available")
        self.assertEqual(registry.analyze("/tmp/observer-reuse", {"two": 2})["status"], "available")
        self.assertEqual(_Provider.created, 1)
        registry.close()
        self.assertEqual(_Provider.closed, 1)

    def test_different_projects_share_one_local_service_provider(self):
        _Provider.created = _Provider.closed = 0
        registry = ObserverAnalysisRegistry(_Provider)
        first = registry.analyze("/tmp/observer-one", {"one": 1})
        second = registry.analyze("/tmp/observer-two", {"two": 2})
        self.assertEqual(first["root"], second["root"])
        self.assertEqual(_Provider.created, 1)
        registry.close()


class ObserverDeadlineTests(unittest.TestCase):
    def test_concurrent_lazy_backend_constructs_once_and_forwards_deadline(self):
        import threading
        import time
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            identity = _receiver_fixture()
            created, calls = [], []
            barrier = threading.Barrier(2)
            class Backend(_Client):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    time.sleep(.03)
                    created.append(self)
                def open_observer_sessions(self, count, **kwargs):
                    calls.append(kwargs)
                    return {"status": "available", "session_ids": ["session"]}
            module = _supervisor_module(identity, Backend)
            with mock.patch.dict(os.environ, {
                    "PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR": str(base / "state")}), \
                    mock.patch("project_control.observer_analysis.bind_local_runtime", return_value=identity), \
                    mock.patch("project_control.observer_analysis.importlib.import_module", return_value=module):
                provider = SkillsObserverAnalysisProvider()
                deadline = time.time() + 10
                def open_session():
                    barrier.wait()
                    return provider.open_sessions(1, compute_profile="narrow", parallelism="default", deadline_epoch=deadline)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: open_session(), range(2)))
            self.assertEqual(len(created), 1)
            self.assertTrue(all(row["status"] == "available" for row in results))
            self.assertEqual([row["deadline_epoch"] for row in calls], [deadline, deadline])


    def test_provider_close_propagates_retryable_owned_cleanup_failure(self):
        provider = SkillsObserverAnalysisProvider()
        provider._backend = SimpleNamespace(close_observer_session=mock.Mock(side_effect=RuntimeError("cleanup_pending")))
        provider._checked_client = mock.Mock(return_value=provider._backend)
        with self.assertRaisesRegex(RuntimeError, "cleanup_pending"):
            provider.close_session("owned-session")


    def test_provider_close_requires_verified_release(self):
        provider = SkillsObserverAnalysisProvider()
        provider._backend = SimpleNamespace(close_observer_session=mock.Mock(return_value={"released": False}))
        provider._checked_client = mock.Mock(return_value=provider._backend)
        with self.assertRaisesRegex(RuntimeError, "observer_session_not_quiescent"):
            provider.close_session("owned-session")
        provider._backend.close_observer_session.return_value = {"released": True}
        self.assertEqual(provider.close_session("owned-session"), {"released": True})
