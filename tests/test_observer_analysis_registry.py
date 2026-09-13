from __future__ import annotations

import unittest

from project_control.observer_analysis import DisabledObserverAnalysisProvider, ObserverAnalysisRegistry


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
