"""Transactional notebook records and evidence retention in the packet store."""
from __future__ import annotations

import unittest
import tempfile

from project_control.as1_contracts import SourceLocator
from project_control.as1_packets import SQLitePacketStore


SCOPE = {'principal': 'alice', 'profile': 'observer', 'project': 'pc'}
SOURCE = SourceLocator(project='pc', repository='pc', path='src/example.py',
                       content_sha256='a' * 64, line_start=1, line_end=2)


def note(note_id='note-1', **overrides):
    value = {
        'note_id': note_id,
        'project': 'pc',
        'kind': 'fact',
        'claim': 'The parser keeps quoted tokens together.',
        'reason_matters': 'This changes the safe query shape.',
        'sources': [SOURCE.model_dump(mode='json')],
        'evidence_packets': [],
        'dependencies': {'file:pc/src/example.py': 'a' * 64},
        'coverage': {'cases': 4},
        'uncertainty': ['Only the listed fixtures were checked.'],
        'provenance': 'source',
    }
    value.update(overrides)
    return value


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.directory = tempfile.TemporaryDirectory()
        self.store = SQLitePacketStore(self.directory.name, clock=lambda: self.now[0])

    def tearDown(self):
        self.directory.cleanup()

    def packet(self, *, tool='source', payload=None, source=SOURCE, ttl=1):
        return self.store.create(tool=tool, payload=payload or {'text': 'original evidence'},
                                 access_scope=SCOPE, sources=[source], ttl_seconds=ttl)

    def cited_note(self, note_id='note-1', **overrides):
        packet = overrides.pop('packet', None) or self.packet()
        value = note(note_id, evidence_packets=[packet.packet_id], **overrides)
        return value, packet

    def test_atomic_note_pins_survive_terminal_window_and_delete_only_own_pins(self):
        value, evidence = self.cited_note()
        self.store.put_note(value, access_scope=SCOPE)
        shared = self.packet(payload={'text': 'shared with a job'})
        self.store.pin('job:live', [shared.packet_id])
        self.store.put_note(note('note-2', evidence_packets=[evidence.packet_id, shared.packet_id]), access_scope=SCOPE)

        terminal = []
        for index in range(51):
            packet = self.packet(payload={'terminal': index})
            self.store.retain_job(f'terminal:{index}', [packet.packet_id], terminal=True)
            terminal.append(packet)
        self.now[0] += 2
        collected = self.store.gc()
        self.assertIn(terminal[0].packet_id, collected)
        self.assertEqual(self.store.lookup(evidence.packet_id, access_scope=SCOPE).status, 'ok')
        self.assertEqual(self.store.lookup(shared.packet_id, access_scope=SCOPE).status, 'ok')

        self.assertTrue(self.store.delete_note('note-2', access_scope=SCOPE))
        self.store.unpin('job:live')
        collected = self.store.gc()
        self.assertIn(shared.packet_id, collected)
        # note-1 still independently retains the same original packet.
        self.assertNotIn(evidence.packet_id, collected)
        self.assertEqual(self.store.lookup(evidence.packet_id, access_scope=SCOPE).status, 'ok')

    def test_failed_update_rolls_back_note_and_pin_replacement(self):
        value, evidence = self.cited_note()
        self.store.put_note(value, access_scope=SCOPE)
        broken = note('note-1', claim='Replacement claim.',
                      evidence_packets=['pkt_missing'])
        with self.assertRaises(ValueError):
            self.store.put_note(broken, access_scope=SCOPE)
        self.assertEqual(self.store.get_note('note-1', access_scope=SCOPE)['claim'], value['claim'])
        self.now[0] += 2
        self.assertNotIn(evidence.packet_id, self.store.gc())

    def test_scope_custom_authority_and_principal_boundaries_are_rechecked(self):
        value, _ = self.cited_note()
        self.store.put_note(value, access_scope=SCOPE)
        other_principal = {**SCOPE, 'principal': 'mallory'}
        self.assertIsNone(self.store.get_note('note-1', access_scope=other_principal))
        self.assertEqual(self.store.list_notes(access_scope=other_principal, project='pc'), [])
        with self.assertRaises(PermissionError):
            self.store.list_notes(access_scope=SCOPE, project='other')

        self.store.authority_access = lambda required, supplied, sources: (
            required.get('tenant') in (None, 'trusted') and supplied.get('tenant') == 'trusted'
        )
        trusted = {**SCOPE, 'tenant': 'trusted'}
        changed = {**SCOPE, 'tenant': 'untrusted'}
        self.assertIsNotNone(self.store.get_note('note-1', access_scope=trusted))
        self.assertIsNone(self.store.get_note('note-1', access_scope=changed))

    def test_user_goal_is_trusted_path_and_never_grants_authority(self):
        goal = note('goal-1', kind='goal', provenance='user', sources=[], evidence_packets=[],
                    claim='Keep the operator workflow easy to use.')
        with self.assertRaises(ValueError):
            self.store.put_note(goal, access_scope=SCOPE)
        result = self.store.put_user_goal({**goal, 'kind': 'suggestion', 'provenance': 'model'}, access_scope=SCOPE)
        self.assertEqual((result['kind'], result['provenance']), ('goal', 'user'))
        self.assertFalse(result['authoritative'])
        self.assertFalse(result['is_independent_evidence'])
        self.assertEqual(self.store.list_notes(access_scope=SCOPE, project='pc'), [result])

    def test_source_test_require_original_matching_packet_and_prepared_context_is_not_evidence(self):
        without_sources = self.packet(source=SOURCE)
        with self.assertRaises(ValueError):
            self.store.put_note(note('n1', evidence_packets=[without_sources.packet_id], sources=[]), access_scope=SCOPE)
        wrong_source = SourceLocator(project='pc', repository='pc', path='src/other.py',
                                     content_sha256='b' * 64)
        wrong = self.packet(source=wrong_source)
        with self.assertRaises(ValueError):
            self.store.put_note(note('n2', evidence_packets=[wrong.packet_id]), access_scope=SCOPE)
        prepared = self.packet(tool='prepared_context', payload={'knowledge_notes': [{'note_id': 'n2'}]})
        with self.assertRaises(ValueError):
            self.store.put_note(note('n3', evidence_packets=[prepared.packet_id]), access_scope=SCOPE)

    def test_integrated_prepared_context_packet_shape_is_rejected_but_original_notes_are_allowed(self):
        payloads = (
            {'status': 'ok', 'data': {'prepared_context': {
                'authoritative': False, 'notes': [{'note_id': 'n-from-notebook'}], 'goals': []}}},
            {'status': 'ok', 'data': {'prepared_context': {
                'authoritative': False, 'notes': [], 'goals': [{'note_id': 'g-from-user'}]}}},
        )
        for index, payload in enumerate(payloads):
            derived = self.packet(tool='evidence', payload=payload)
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'prepared knowledge context'):
                self.store.put_note(note(f'derived-{index}', evidence_packets=[derived.packet_id]),
                                    access_scope=SCOPE)

        # An original source packet may naturally describe notes; it is only
        # the explicit prepared-context result shape that is derived.
        original = self.packet(tool='search', payload={'notes': ['source file records three examples']})
        stored = self.store.put_note(note('original-notes', evidence_packets=[original.packet_id]),
                                     access_scope=SCOPE)
        self.assertEqual(stored['evidence_packets'], [original.packet_id])

    def test_model_notes_cannot_claim_authority_and_record_limits_are_explicit(self):
        model = note('model-1', provenance='model', sources=[], evidence_packets=[],
                     authoritative=True)
        with self.assertRaises(ValueError):
            self.store.put_note(model, access_scope=SCOPE)
        too_large = note('large-1', provenance='model', sources=[], evidence_packets=[],
                         uncertainty=['x' * 9000])
        with self.assertRaises(ValueError):
            self.store.put_note(too_large, access_scope=SCOPE)
        for index in range(128):
            self.store.put_note(note(f'capacity-{index}', provenance='model', sources=[], evidence_packets=[]),
                                access_scope=SCOPE)
        with self.assertRaisesRegex(ValueError, 'capacity'):
            self.store.put_note(note('capacity-overflow', provenance='model', sources=[], evidence_packets=[]),
                                access_scope=SCOPE)
        self.assertEqual(len(self.store.list_notes(access_scope=SCOPE, project='pc')), 128)


if __name__ == '__main__':
    unittest.main()
