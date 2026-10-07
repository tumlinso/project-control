from pathlib import Path
import unittest
from tests.todo.test_workflow_isolated_claims import WorkflowIsolatedClaimTests
from todo_orchestrator.interfaces import interface_hash
from todo_orchestrator.models import TodoError
from todo_orchestrator.workflow.roles import require_role_action
from todo_orchestrator.workflow.capabilities import default_first_class_operations

class IntegratorInterfaceAuthorityTests(unittest.TestCase):
    setUp = WorkflowIsolatedClaimTests.setUp
    tearDown = WorkflowIsolatedClaimTests.tearDown
    workspace = WorkflowIsolatedClaimTests.workspace
    claim = WorkflowIsolatedClaimTests.claim

    def prepare(self, role='integrator', owner='A'):
        def seed(conn, revision):
            conn.execute('UPDATE workflow_lanes SET role=? WHERE id=?',(role,'A-LANE'))
            conn.execute('UPDATE interfaces SET owner_task_id=? WHERE id=?',(owner,'IFACE-A'))
            return {}
        self.repo.service.db.mutate(actor_session_id=None,entity_type='fixture',entity_id='A-LANE',
            event_type='fixture',payload={},operation=seed)
        producer=self.workspace('A-LANE','isolated_merge')
        self.workspace('INT-LANE','exclusive')
        root=Path(producer['worktree_path'])
        target=root/'src/a/interface.hh';target.parent.mkdir(parents=True);target.write_text('#pragma once\n')
        self.digest,_=interface_hash(root,['src/a/interface.hh'])
        self.claimed=self.claim('integrator-publication-test','A')
        self.assertEqual(self.claimed['status'],'claimed')

    def publish(self, digest=None):
        return self.protocol.coordinate_task(workflow_handle=self.claimed['workflow_handle'],action='publish_interface',
            payload={'interface_id':'IFACE-A','version':'1','content_hash':digest or self.digest})

    def test_integrator_can_publish_exact_owned_dispatch_interface(self):
        self.prepare()
        published=self.publish()
        self.assertEqual(published['content_hash'],self.digest)
        with self.repo.service.db.read() as conn:
            row=conn.execute("SELECT state,content_hash FROM interfaces WHERE id='IFACE-A'").fetchone()
        self.assertEqual(tuple(row),('frozen',self.digest))

    def test_integrator_cannot_publish_another_tasks_interface(self):
        self.prepare(owner='B')
        with self.assertRaises(TodoError) as error:
            self.publish()
        self.assertEqual(error.exception.code,'interface_owner_mismatch')

    def test_integrator_hash_mismatch_rolls_back_freeze(self):
        self.prepare()
        with self.assertRaises(TodoError) as error:
            self.publish('0'*64)
        self.assertEqual(error.exception.code,'interface_hash_mismatch')
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM interfaces WHERE id='IFACE-A'").fetchone()[0],'draft')

    def test_validator_and_child_publication_remain_forbidden(self):
        self.prepare(role='validator')
        self.assertNotIn('coordinate:publish_interface',default_first_class_operations('validator'))
        with self.assertRaises(TodoError) as error:
            self.publish()
        self.assertEqual(error.exception.code,'capability_operation_forbidden')
        with self.assertRaises(TodoError) as error:
            require_role_action('integrator','publish_interface',actor_kind='child')
        self.assertEqual(error.exception.code,'child_run_authority_forbidden')
