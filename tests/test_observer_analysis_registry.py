from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from project_control.observer_analysis import (
    DisabledObserverAnalysisProvider,
    ObserverAnalysisRegistry,
    SkillsObserverAnalysisProvider,
    observer_analysis_state_root,
)


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
    def test_investigator_turn_is_translated_to_skills_chat_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            skills = base / "skills"
            module_root = skills / "local-coding-worker" / "local_worker"
            module_root.mkdir(parents=True)
            captured = []
            class Backend:
                def __init__(self, *_args, **_kwargs): pass
                def run_observer_turn(self, request):
                    captured.append(request)
                    return {"status": "available", "text": '{"action":"answer","answer":{}}'}
            module = SimpleNamespace(__file__=str(module_root / "supervisor.py"), ProductionBackend=Backend)
            with mock.patch.dict(os.environ, {"PROJECT_CONTROL_SKILLS_ROOT": str(skills), "PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR": str(base / "state")}, clear=False), \
                 mock.patch("project_control.observer_analysis.importlib.import_module", return_value=module):
                result = SkillsObserverAnalysisProvider(base / "observed").investigate_turn({
                    "protocol": "PC-LOCAL-INVESTIGATOR-TURN/1", "system_prompt": "system", "question": "q",
                    "evidence": [{"id": "E1"}], "max_tokens": 123, "timeout_seconds": 12,
                    "messages": [{"round": 1}, {"role": "assistant", "content": "prior"}],
                })
            self.assertEqual(result["status"], "available")
            self.assertEqual(captured[0]["format"], "PC-LOCAL-INVESTIGATOR-TURN/1")
            self.assertEqual(captured[0]["messages"][0], {"role": "system", "content": "system"})
            self.assertEqual(captured[0]["messages"][1]["role"], "user")
            self.assertEqual(captured[0]["messages"][2], {"role": "assistant", "content": "prior"})
            self.assertEqual(captured[0]["max_tokens"], 123)
            self.assertEqual(captured[0]["timeout_seconds"], 12)

    def test_skills_provider_uses_private_service_state_not_observed_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            observed = base / "observed-read-only-project"
            observed.mkdir()
            skills = base / "skills"
            module_root = skills / "local-coding-worker" / "local_worker"
            module_root.mkdir(parents=True)
            module = SimpleNamespace(__file__=str(module_root / "supervisor.py"))
            captured: list[Path] = []
            captured_state: list[Path] = []

            class Backend:
                def __init__(self, service_root, *, service_state_root):
                    captured.append(Path(service_root))
                    captured_state.append(Path(service_state_root))

            module.ProductionBackend = Backend
            with mock.patch.dict(os.environ, {
                "PROJECT_CONTROL_SKILLS_ROOT": str(skills),
                "PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR": str(base / "service-state"),
            }, clear=False), mock.patch("project_control.observer_analysis.importlib.import_module", return_value=module):
                provider = SkillsObserverAnalysisProvider(observed)
                provider._get_backend()
                self.assertEqual(captured, [base / "service-state"])
                self.assertEqual(captured_state, [base / "service-state"])
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
