from pathlib import Path
import json
import unittest
from tests.todo.test_workflow_workspaces import WorkflowWorkspaceTests, git
from todo_orchestrator.evidence import gate_input_fingerprint
from todo_orchestrator.config import utc_now
from todo_orchestrator.sessions import create_session

class ContractSplitCleanupTests(unittest.TestCase):
    setUp = WorkflowWorkspaceTests.setUp
    tearDown = WorkflowWorkspaceTests.tearDown
    producer_commit = WorkflowWorkspaceTests.producer_commit
    assert_code = WorkflowWorkspaceTests.assert_code

    def change(self, sql, values=()):
        self.db.mutate(actor_session_id=None, entity_type='fixture', entity_id='RUN', event_type='fixture', payload={},
                       operation=lambda c, r: c.execute(sql, values) and {})

    def fixture(self):
        git(self.repo, 'branch', '-M', 'main')
        self.change("UPDATE workflow_lanes SET workspace_mode='contract_split' WHERE id='PRODUCER'")
        self.workspace = self.service.create_workspace(repository_root=self.repo, repository_identity='repo-identity', run_id='RUN',
            lane_id='PRODUCER', mode='contract_split', base_commit=self.base, worktree_path=self.managed/'producer', branch='test-producer', integration_task_id=None)
        self.head = self.producer_commit(self.workspace, 'accepted producer\n')
        git(self.repo, 'merge', '--ff-only', self.head)
        self.change("UPDATE workflow_runs SET status='completed'")
        self.change("UPDATE workflow_lanes SET state='closed'")
        self.change("UPDATE workflow_lane_tasks SET state='completed'")
        self.change("UPDATE tasks SET status='done'")
        config = {'input_paths': ['shared.txt'], 'argv': ['true']}
        with self.db.read() as c:
            fp, inputs = gate_input_fingerprint(c, self.repo, config)
        self.change("UPDATE gates SET type='command',status='passed',valid=1,config_json=?,input_fingerprint=?", (json.dumps(config),fp))
        self.change("INSERT INTO evidence(id,gate_id,kind,status,metadata_json,created_at,revision) VALUES('EV','POST','gate','passed',?,?,1)",
                    (json.dumps({'input_fingerprint':fp,'inputs':inputs}),utc_now()))

    def record(self, apply=True):
        return self.service.record_contract_split_integration(repository_root=self.repo, workspace_id=self.workspace['workspace_id'],
            integration_task_id='INTEGRATE', accepted_commit=git(self.repo,'rev-parse','HEAD'), reason='final accepted merge', apply=apply)

    def test_preview_is_read_only_then_transition_preserves_git_and_cleanup(self):
        self.fixture(); rev=self.db.revision()
        self.assertEqual(self.record(False)['status'],'ready'); self.assertEqual(self.db.revision(),rev)
        result=self.record(); self.assertEqual(result['status'],'integrated')
        self.assertFalse(result['cleanup_eligible']); self.assertFalse(result['deleted'])
        self.assertEqual(git(self.repo,'rev-parse','HEAD'),self.head)
        self.assertTrue(Path(self.workspace['worktree_path']).is_dir())
        self.assertEqual(self.record()['status'],'noop')
        self.assertTrue(self.service.mark_cleanup_eligible(workspace_id=self.workspace['workspace_id'])['cleanup_eligible'])

    def test_reject_incomplete_run(self):
        self.fixture(); self.change("UPDATE workflow_runs SET status='active'")
        self.assert_code('contract_run_not_terminal',self.record)

    def test_reject_unmerged_producer(self):
        self.fixture(); self.producer_commit(self.workspace,'unmerged change\n')
        self.assert_code('contract_not_merged',self.record)

    def test_reject_dirty_worktree(self):
        self.fixture(); (Path(self.workspace['worktree_path'])/'shared.txt').write_text('dirty')
        self.assert_code('workspace_dirty_preserved',self.record)

    def test_reject_stale_gates(self):
        self.fixture(); self.change("UPDATE gates SET valid=0")
        self.assert_code('contract_gate_stale',self.record)

    def test_reject_wrong_integration_authority(self):
        self.fixture(); self.change("UPDATE workflow_lanes SET role='implementer' WHERE id='INTEGRATOR'")
        self.assert_code('contract_integration_not_complete',self.record)

    def test_reject_material_change_after_gates(self):
        self.fixture(); (self.repo/'other.txt').write_text('new source')
        git(self.repo,'add','other.txt'); git(self.repo,'commit','-qm','later source')
        self.assert_code('contract_gate_stale',self.record)

    def test_recheck_inside_transaction(self):
        self.fixture()
        original=self.service.db.mutate
        def raced(**kwargs):
            (Path(self.workspace['worktree_path'])/'shared.txt').write_text('raced dirty')
            return original(**kwargs)
        self.service.db.mutate=raced
        self.assert_code('workspace_dirty_preserved',self.record)

    def test_reject_active_claim_even_if_tasks_are_terminal(self):
        self.fixture()
        def seed(conn, revision):
            session, _ = create_session(conn, self.repo, {"command": "test"})
            now = utc_now()
            conn.execute(
                "INSERT INTO claims(id,task_id,session_id,token_hash,state,created_at,heartbeat_at,expires_at,baseline_revision) "
                "VALUES('ACTIVE','IMPL',?,'test-token-hash','active',?,?,?,?)",
                (session['agent_id'], now, now, now, revision),
            )
            return {}
        self.db.mutate(actor_session_id=None, entity_type='fixture', entity_id='RUN',
                       event_type='fixture', payload={}, operation=seed)
        self.assert_code('contract_owner_active', self.record)

    def test_reject_evidence_for_dirty_material_source(self):
        self.fixture()
        with self.db.read() as conn:
            metadata = json.loads(conn.execute("SELECT metadata_json FROM evidence WHERE id='EV'").fetchone()[0])
        metadata['inputs']['dirty_paths'] = ['shared.txt']
        self.change("UPDATE evidence SET metadata_json=? WHERE id='EV'", (json.dumps(metadata),))
        self.assert_code('contract_gate_stale', self.record)

    def test_workflow_projection_commit_does_not_stale_material_evidence(self):
        self.fixture()
        (self.repo / 'todo-status.md').write_text('final projection\n')
        git(self.repo, 'add', 'todo-status.md')
        git(self.repo, 'commit', '-qm', 'workflow projection')
        self.assertEqual(self.record()['status'], 'integrated')
