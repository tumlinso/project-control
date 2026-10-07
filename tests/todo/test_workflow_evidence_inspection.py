from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task
from todo_orchestrator.workflow.capabilities import WorkflowCapabilityLocator
from todo_orchestrator.workflow.protocol import WorkflowProtocol
from todo_orchestrator.workflow.service import WorkflowKernel


class WorkflowEvidenceInspectionTests(unittest.TestCase):
    def setUp(self):
        self.repo = V2Repo()
        self.repo.apply(base_plan([safe_task('A', 'src/a'), safe_task('B', 'src/b')]))
        self.locator_temp = tempfile.TemporaryDirectory()
        locator = WorkflowCapabilityLocator(Path(self.locator_temp.name))
        self.protocol = WorkflowProtocol(WorkflowKernel(locator=locator), locator)
        self.old_thread = os.environ.get('CODEX_THREAD_ID')
        os.environ['CODEX_THREAD_ID'] = 'evidence-inspection-test'
        self.handle = self.protocol.next_task(repo_root=str(self.repo.root), task_id='A')['workflow_handle']

    def tearDown(self):
        if self.old_thread is None:
            os.environ.pop('CODEX_THREAD_ID', None)
        else:
            os.environ['CODEX_THREAD_ID'] = self.old_thread
        self.locator_temp.cleanup()
        self.repo.close()

    def inspect(self, target=None):
        return self.protocol.inspect_task(workflow_handle=self.handle, kind='evidence', target=target)['evidence']

    def test_empty_evidence_uses_actual_schema(self):
        self.assertEqual(self.inspect(), [])

    def test_gate_ownership_filters_default_and_explicit_target(self):
        def seed(conn, revision):
            for task in ('A', 'B'):
                conn.execute("INSERT INTO gates(id,task_id,type) VALUES(?,?,'command')", ('G-' + task, task))
            # Claim attribution is deliberately not gate ownership: callers must
            # see the selected task's gates, not every event sharing a claim.
            claim = conn.execute("SELECT id FROM claims WHERE task_id='A'").fetchone()[0]
            for name, gate, rev in [('A-old', 'G-A', 1), ('B-new', 'G-B', 20), ('A-new', 'G-A', 10), ('unscoped', None, 30)]:
                conn.execute("INSERT INTO evidence(id,gate_id,claim_id,kind,status,path,content_hash,created_at,revision) VALUES(?,?,?,'test','passed',?,'digest','now',?)",
                             (name, gate, claim, name + '.json', rev))
        self.repo.service.db.mutate(actor_session_id=None, entity_type='fixture', entity_id='A', event_type='fixture', payload={}, operation=seed)
        default = self.inspect()
        self.assertEqual([row['id'] for row in default], ['A-new', 'A-old'])
        self.assertTrue(all(row['task_id'] == 'A' and row['gate_id'] == 'G-A' for row in default))
        explicit = self.inspect('B')
        self.assertEqual([row['id'] for row in explicit], ['B-new'])
        self.assertEqual(explicit[0]['task_id'], 'B')
        self.assertEqual(self.inspect('unknown-task'), [])
        self.assertEqual(self.inspect("A' OR 1=1 --"), [])

    def test_checkpoint_owned_evidence_with_and_without_gate(self):
        def seed(conn, revision):
            for task in ('A', 'B'):
                conn.execute("INSERT INTO checkpoints(id,task_id,title) VALUES(?,?,?)", ('CP-' + task, task, task))
            conn.execute("INSERT INTO gates(id,checkpoint_id,type) VALUES('CP-GATE','CP-A','command')")
            conn.execute("INSERT INTO gates(id,task_id,type) VALUES('TASK-GATE','B','command')")
            for name, gate, checkpoint in [('direct-A', None, 'CP-A'), ('gate-A', 'CP-GATE', None), ('direct-B', None, 'CP-B'), ('gate-owner-wins', 'TASK-GATE', 'CP-A')]:
                conn.execute("INSERT INTO evidence(id,gate_id,checkpoint_id,kind,status,created_at,revision) VALUES(?,?,?,'test','passed','now',1)", (name, gate, checkpoint))
        self.repo.service.db.mutate(actor_session_id=None, entity_type='fixture', entity_id='A', event_type='fixture', payload={}, operation=seed)
        self.assertEqual([row['id'] for row in self.inspect()], ['direct-A', 'gate-A'])
        self.assertEqual([row['id'] for row in self.inspect('B')], ['direct-B', 'gate-owner-wins'])
        self.assertTrue(all(row['task_id'] == 'A' for row in self.inspect()))
        self.assertTrue(all(row['task_id'] == 'B' for row in self.inspect('B')))

    def test_evidence_inventory_remains_bounded(self):
        def seed(conn, revision):
            conn.execute("INSERT INTO gates(id,task_id,type) VALUES('G','A','command')")
            for number in range(55):
                conn.execute("INSERT INTO evidence(id,gate_id,kind,status,created_at,revision) VALUES(?,'G','test','passed','now',?)", (f'E{number:02}', number))
        self.repo.service.db.mutate(actor_session_id=None, entity_type='fixture', entity_id='A', event_type='fixture', payload={}, operation=seed)
        rows = self.inspect()
        self.assertEqual(len(rows), 50)
        self.assertEqual([row['id'] for row in rows], [f'E{number:02}' for number in range(54, 4, -1)])


if __name__ == '__main__':
    unittest.main()
