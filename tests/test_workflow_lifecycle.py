"""Exercise real Git and Todo through the registered Project Control MCP tools."""
import asyncio
import os
from pathlib import Path
import sys
import unittest
import todo_orchestrator

sys.path.insert(0, str(Path(todo_orchestrator.__file__).resolve().parents[1] / 'tests'))
import test_workflow_isolated_claims as fixtures
from todo_orchestrator.interfaces import interface_hash
from project_control.workflow_tools import create_workflow_mcp

class WorkflowLifecycleTests(unittest.TestCase):
    setUp = fixtures.WorkflowIsolatedClaimTests.setUp
    tearDown = fixtures.WorkflowIsolatedClaimTests.tearDown
    workspace = fixtures.WorkflowIsolatedClaimTests.workspace

    def test_parallel_claim_publication_and_producer_completion_through_mcp(self):
        # Fixture setup is transactional; all execution below uses the public tools.
        def seed(conn, revision):
            conn.execute("UPDATE workflow_lanes SET role='integrator' WHERE id='A-LANE'")
        self.repo.service.db.mutate(actor_session_id=None,entity_type='fixture',entity_id='A-LANE',
            event_type='fixture',payload={},operation=seed)
        producer=self.workspace('A-LANE','isolated_merge')
        self.workspace('B-LANE','isolated_merge');self.workspace('INT-LANE','exclusive')
        manager=create_workflow_mcp(self.protocol)._tool_manager
        def call(name, **arguments):
            return asyncio.run(manager.call_tool(name,arguments))
        os.environ['CODEX_THREAD_ID']='lifecycle-a'
        first=call('next_task',repo_root=str(self.repo.root),task_id='A')
        os.environ['CODEX_THREAD_ID']='lifecycle-b'
        second=call('next_task',repo_root=str(self.repo.root),task_id='B')
        self.assertEqual((first['status'],second['status']),('claimed','claimed'))
        root=Path(producer['worktree_path']);target=root/'src/a/interface.hh'
        target.parent.mkdir(parents=True);target.write_text('#pragma once\n')
        fixtures.git(root,'add','src/a/interface.hh');fixtures.git(root,'commit','-qm','interface')
        digest,_=interface_hash(root,['src/a/interface.hh'])
        published=call('coordinate_task',workflow_handle=first['workflow_handle'],action='publish_interface',
            payload={'interface_id':'IFACE-A','version':'1','content_hash':digest})
        self.assertEqual(published['content_hash'],digest)
        complete=call('finish_task',workflow_handle=first['workflow_handle'],action='complete',disposition='implemented')
        self.assertEqual(complete['handoff']['producer_commit'],fixtures.git(root,'rev-parse','HEAD'))
        self.assertNotEqual(complete['handoff']['producer_commit'],complete['handoff']['authority_commit'])
        call('finish_task',workflow_handle=second['workflow_handle'],action='release',reason='fixture done')

    @unittest.skipUnless(os.environ.get('PROJECT_CONTROL_GPU_SMOKE_BINARY'), 'explicit real-GPU smoke only')
    def test_gpu_gate_and_completion_rerun_acquire_controller_lease(self):
        import json
        import shutil
        (self.repo.root/'.git/info/exclude').write_text('.todo-orchestrator/\n')
        plan=json.loads((self.repo.root/'plan.json').read_text())
        task=next(item for item in plan['tasks'] if item['id']=='A')
        task['gates']=[{'id':'GPU','type':'command','required':True,
            'argv':['./shared.txt','--require-sm70'],'timeout':120,
            'cuda':{'gpus':1,'cpu_threads':1,'binary_paths':['shared.txt'],
                'toolchain':{'root':os.environ['PROJECT_CONTROL_GPU_TOOLKIT'],'require_sanitizer':True}}}]
        self.repo.apply(plan)
        def seed(conn, revision):
            conn.execute("UPDATE workflow_lanes SET role='integrator' WHERE id='A-LANE'")
        self.repo.service.db.mutate(actor_session_id=None,entity_type='fixture',entity_id='A-LANE',
            event_type='fixture',payload={},operation=seed)
        producer=self.workspace('A-LANE','isolated_merge');self.workspace('INT-LANE','exclusive')
        root=Path(producer['worktree_path'])
        shutil.copy2(os.environ['PROJECT_CONTROL_GPU_SMOKE_BINARY'],root/'shared.txt')
        fixtures.git(root,'add','shared.txt');fixtures.git(root,'commit','-qm','real GPU fixture')
        manager=create_workflow_mcp(self.protocol)._tool_manager
        def call(name, **arguments):
            return asyncio.run(manager.call_tool(name,arguments))
        os.environ['CODEX_THREAD_ID']='gpu-lifecycle'
        claim=call('next_task',repo_root=str(self.repo.root),task_id='A')
        explicit=call('coordinate_task',workflow_handle=claim['workflow_handle'],action='run_gates')
        self.assertEqual(explicit['gates'][0]['status'],'passed',explicit)
        done=call('finish_task',workflow_handle=claim['workflow_handle'],action='complete',disposition='implemented')
        self.assertIn('handoff', done, done)
        self.assertEqual(done['handoff']['producer_commit'],fixtures.git(root,'rev-parse','HEAD'),done)
