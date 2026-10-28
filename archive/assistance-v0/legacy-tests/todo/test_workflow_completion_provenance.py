from pathlib import Path
import unittest
import tests.todo.test_workflow_isolated_claims as fixtures
from tests.todo.test_workflow_isolated_claims import git
from todo_orchestrator.models import TodoError

class CompletionProvenanceTests(unittest.TestCase):
    setUp=fixtures.WorkflowIsolatedClaimTests.setUp
    tearDown=fixtures.WorkflowIsolatedClaimTests.tearDown
    workspace=fixtures.WorkflowIsolatedClaimTests.workspace
    claim=fixtures.WorkflowIsolatedClaimTests.claim

    def test_wrong_task_cannot_resume_and_completion_records_real_producer(self):
        producer=self.workspace('A-LANE','isolated_merge')
        self.workspace('B-LANE','isolated_merge');self.workspace('INT-LANE','exclusive')
        self.claim('same-client','A')
        with self.assertRaises(TodoError) as error:
            self.claim('same-client','B')
        self.assertEqual(error.exception.code,'workflow_session_already_dispatched')
        resumed=self.claim('same-client','A');self.assertEqual(resumed['task_id'],'A')
        root=Path(producer['worktree_path']);(root/'shared.txt').write_text('producer change\n')
        git(root,'add','shared.txt');git(root,'commit','-qm','producer implementation')
        producer_head=git(root,'rev-parse','HEAD');main_head=git(self.repo.root,'rev-parse','HEAD')
        self.assertNotEqual(producer_head,main_head)
        complete=self.protocol.finish_task(workflow_handle=resumed['workflow_handle'],action='complete',disposition='implemented')
        self.assertEqual(complete['handoff']['producer_commit'],producer_head)
        self.assertEqual(complete['handoff']['completion_commit'],producer_head)
        self.assertEqual(complete['handoff']['authority_commit'],main_head)
        self.assertIsNone(complete['handoff']['integration_commit'])

    def test_missing_declared_artifact_blocks_completion(self):
        import json
        plan=json.loads((self.repo.root/'plan.json').read_text())
        task=next(item for item in plan['tasks'] if item['id']=='A')
        task['produced_artifacts']=[{'path':'src/a/missing.hh','kind':'source'}]
        self.repo.apply(plan)
        self.workspace('A-LANE','isolated_merge');self.workspace('INT-LANE','exclusive')
        claimed=self.claim('artifact-test','A')
        with self.assertRaises(TodoError) as error:
            self.protocol.finish_task(workflow_handle=claimed['workflow_handle'],action='complete',disposition='implemented')
        self.assertEqual(error.exception.code,'completion_artifacts_missing')
