"""Isolated review tests: never read or mutate a real project database."""
from pathlib import Path
import unittest
from tests.todo.test_workflow_workspaces import WorkflowWorkspaceTests, git


class ContractSplitReactivationTests(WorkflowWorkspaceTests):
    def contract_workspace(self):
        def mode(conn, revision):
            conn.execute("UPDATE workflow_lanes SET workspace_mode='contract_split' WHERE id='PRODUCER'")
        self.db.mutate(actor_session_id=None, entity_type='fixture', entity_id='PRODUCER',
                       event_type='fixture_contract_mode', payload={}, operation=mode)
        return self.service.create_workspace(
            repository_root=self.repo, repository_identity='repo-identity', run_id='RUN',
            lane_id='PRODUCER', mode='contract_split', base_commit=self.base,
            worktree_path=self.managed / 'producer', branch='test-producer', integration_task_id='INTEGRATE')

    def quarantine(self, workspace):
        def mutation(conn, revision):
            conn.execute("UPDATE workflow_workspaces SET state='quarantined',cleanup_eligible=0 WHERE id=?", (workspace['workspace_id'],))
        self.db.mutate(actor_session_id=None, entity_type='fixture', entity_id=workspace['workspace_id'],
                       event_type='fixture_quarantined', payload={}, operation=mutation)

    def stored(self, workspace):
        with self.db.read() as conn:
            return dict(conn.execute('SELECT * FROM workflow_workspaces WHERE id=?', (workspace['workspace_id'],)).fetchone())

    def recover(self, base=None):
        return self.service.reconcile_workspace_base(repository_root=self.repo, run_id='RUN',
            lane_id='PRODUCER', base_commit=base or self.base, reason='verified clean contract-split recovery')

    def test_contract_recovery_preserves_tip_and_other_participants(self):
        workspace = self.contract_workspace()
        sibling = self.create_producer(lane='PRODUCER2')
        destination = self.create_destination()
        self.quarantine(workspace)
        # Deliberately dirty unrelated participants: contract recovery must not touch them.
        (self.managed / 'producer2' / 'shared.txt').write_text('unrelated dirty work\n')
        (self.managed / 'integrator' / 'shared.txt').write_text('unrelated integration work\n')
        before_sibling, before_destination = self.stored(sibling), self.stored(destination)
        path = self.managed / 'producer'
        (path / 'native.txt').write_text('preserved accepted native implementation\n')
        git(path, 'add', 'native.txt'); git(path, 'commit', '-qm', 'native work')
        tip = git(path, 'rev-parse', 'HEAD')
        result = self.recover()
        self.assertEqual([x['workspace_id'] for x in result['reconciled_workspaces']], [workspace['workspace_id']])
        self.assertEqual(self.stored(workspace)['state'], 'active')
        self.assertEqual(self.stored(workspace)['cleanup_eligible'], 0)
        self.assertEqual(git(path, 'rev-parse', 'HEAD'), tip)
        self.assertEqual(git(path, 'status', '--porcelain'), '')
        self.assertEqual(self.stored(sibling), before_sibling)
        self.assertEqual(self.stored(destination), before_destination)

    def test_contract_recovery_refuses_dirty_selected_workspace(self):
        workspace = self.contract_workspace(); self.quarantine(workspace)
        (self.managed / 'producer' / 'shared.txt').write_text('keep my uncommitted work\n')
        before = self.stored(workspace)
        self.assert_code('workspace_reconcile_dirty', self.recover)
        self.assertEqual(self.stored(workspace), before)
        self.assertEqual((self.managed / 'producer' / 'shared.txt').read_text(), 'keep my uncommitted work\n')

    def test_contract_recovery_refuses_base_absent_from_selected_history(self):
        workspace = self.contract_workspace(); self.quarantine(workspace)
        (self.repo / 'main-new.txt').write_text('new main commit\n')
        git(self.repo, 'add', 'main-new.txt'); git(self.repo, 'commit', '-qm', 'advance main')
        advanced = git(self.repo, 'rev-parse', 'HEAD')
        before = self.stored(workspace)
        self.assert_code('workspace_base_not_in_history', lambda: self.recover(advanced))
        self.assertEqual(self.stored(workspace), before)


if __name__ == '__main__':
    unittest.main()
