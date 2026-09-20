from __future__ import annotations

import os
import json
import subprocess
import sys
import unittest
from pathlib import Path


SKILLS = Path("/home/tumlinson/.agents/skills")
RECOVERY_FIXTURE = SKILLS / "todo-orchestrator/tests/test_workflow_recovery.py"


@unittest.skipUnless(RECOVERY_FIXTURE.is_file(), "local Todo recovery fixture unavailable")
class ManagedMaintenanceJourneyTests(unittest.TestCase):
    def test_clean_managed_workspace_is_quarantined_resumed_and_publicly_claimed(self) -> None:
        child = r'''
import asyncio, json, os, socket, subprocess, sys
from pathlib import Path
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from v2_helpers import V2Repo, base_plan, safe_task
from todo_orchestrator.workflow.service import WorkspaceService, repository_identity

repo = V2Repo()
try:
    repo.apply(base_plan([safe_task('A', 'src/a')]))
    (repo.root / 'seed.txt').write_text('seed\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(repo.root), 'add', 'seed.txt'], check=True)
    subprocess.run(['git', '-C', str(repo.root), 'commit', '-qm', 'seed'], check=True)
    capsule = repo.service.continue_work(task_id='A')
    claim_id, session_id = capsule['claim']['claim_id'], capsule['session']['agent_id']
    def seed_lane(conn, revision):
        conn.execute("INSERT INTO workflow_runs(id,root_task_id,status,created_at,updated_at,revision) VALUES('RUN','A','active','now','now',?)", (revision,))
        conn.execute("INSERT INTO workflow_lanes(id,run_id,role,workspace_mode,state,created_at,updated_at,revision) VALUES('LANE','RUN','implementer','isolated_merge','ready','now','now',?)", (revision,))
    repo.service.db.mutate(actor_session_id=None, entity_type='fixture', entity_id='RUN', event_type='fixture.run', payload={}, operation=seed_lane)
    workspaces = WorkspaceService(repo.service.db, managed_root=repo.service.paths.state_dir / 'workflow-workspaces', repository_identity_resolver=lambda root: repository_identity(root, str(repo.service.project['project_uuid'])))
    workspace = workspaces.create_workspace(repository_root=repo.root, repository_identity=repository_identity(repo.root, str(repo.service.project['project_uuid'])), run_id='RUN', lane_id='LANE', mode='isolated_merge', base_commit='HEAD', worktree_path=repo.service.paths.state_dir / 'workflow-workspaces' / 'managed-a', branch='managed-a', integration_task_id='A')
    def seed_dispatch(conn, revision):
        conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('LANE',0,'A','queued','now',?)", (revision,))
        conn.execute("INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,workspace_id,state,context_version,heartbeat_at,hostname,pid,created_at,revision) VALUES('DISPATCH','LANE',?,?,?,'active',1,'2000-01-01T00:00:00Z',?,999999,'now',?)", (session_id, claim_id, workspace['workspace_id'], socket.gethostname(), revision))
    repo.service.db.mutate(actor_session_id=None, entity_type='fixture', entity_id='A', event_type='fixture.dispatch', payload={}, operation=seed_dispatch)
    env = {'PROJECT_CONTROL_SKILLS_ROOT': sys.argv[1], 'TODO_ORCHESTRATOR_STATE_DIR': str(repo.state_root), 'PYTHONPATH': os.pathsep.join([str(Path.cwd() / 'src'), str(Path(sys.argv[1]) / 'todo-orchestrator')])}
    issued = subprocess.run([sys.executable, '-m', 'project_control.cli', 'admin', 'prepare-maintenance', '--repo', str(repo.root), '--task', 'A', '--recipient', 'test-operator-a'], cwd=str(Path.cwd()), env=env, text=True, capture_output=True, check=False)
    assert issued.returncode == 0, issued.stderr
    assignment = json.loads(issued.stdout)
    async def call(command, name, arguments):
        parameters = StdioServerParameters(command=command['executable'], args=command['arguments'], env={**command['environment'], 'TODO_ORCHESTRATOR_STATE_DIR': str(repo.state_root)}, cwd=str(Path.cwd()))
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                assert not result.isError, result.content
                return json.loads(result.content[0].text)
    maintained = asyncio.run(call(assignment['operator_launch'], 'maintain_execution', {'repo_root': str(repo.root), 'authorization_id': assignment['assignment']['grant_reference']}))
    assert maintained['continuation']['status'] == 'ready', maintained
    resumed = asyncio.run(call({'executable': assignment['operator_launch']['executable'], 'arguments': ['-m', 'project_control.cli', 'codex'], 'environment': assignment['operator_launch']['environment']}, 'next_task', maintained['recommended_next_call']['arguments']))
    assert resumed['status'] == 'claimed', resumed
    print(json.dumps({'maintenance': maintained['status'], 'workspace': maintained['continuation']['workspace_resume']['state'], 'run': maintained['recommended_next_call']['arguments']['run_id'], 'claimed': resumed['status']}))
finally:
    repo.close()
'''
        environment = dict(os.environ)
        skills = Path(environment.get("PROJECT_CONTROL_SKILLS_ROOT", str(SKILLS)))
        environment["PROJECT_CONTROL_SKILLS_ROOT"] = str(skills)
        environment["PYTHONPATH"] = os.pathsep.join([str(skills / "todo-orchestrator/tests"), environment.get("PYTHONPATH", "")])
        result = subprocess.run([sys.executable, "-c", child, str(skills)], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"maintenance": "maintained", "workspace": "active", "run": "RUN", "claimed": "claimed"})

    def test_dirty_managed_workspace_is_preserved_and_refused_before_grant(self) -> None:
        child = r'''
import os, socket, subprocess, sys
from pathlib import Path
from v2_helpers import V2Repo, base_plan, safe_task
from todo_orchestrator.workflow.service import WorkspaceService, repository_identity
repo = V2Repo()
try:
 repo.apply(base_plan([safe_task('A','src/a')])); (repo.root/'seed').write_text('x'); subprocess.run(['git','-C',str(repo.root),'add','seed'],check=True); subprocess.run(['git','-C',str(repo.root),'commit','-qm','seed'],check=True); c=repo.service.continue_work(task_id='A')
 def lane(conn,rev): conn.execute("INSERT INTO workflow_runs(id,root_task_id,status,created_at,updated_at,revision) VALUES('RUN','A','active','n','n',?)",(rev,)); conn.execute("INSERT INTO workflow_lanes(id,run_id,role,workspace_mode,state,created_at,updated_at,revision) VALUES('LANE','RUN','implementer','isolated_merge','ready','n','n',?)",(rev,))
 repo.service.db.mutate(actor_session_id=None,entity_type='f',entity_id='r',event_type='f',payload={},operation=lane)
 ws=WorkspaceService(repo.service.db,managed_root=repo.service.paths.state_dir/'workflow-workspaces',repository_identity_resolver=lambda root:repository_identity(root,str(repo.service.project['project_uuid']))).create_workspace(repository_root=repo.root,repository_identity=repository_identity(repo.root,str(repo.service.project['project_uuid'])),run_id='RUN',lane_id='LANE',mode='isolated_merge',base_commit='HEAD',worktree_path=repo.service.paths.state_dir/'workflow-workspaces'/'dirty',branch='dirty',integration_task_id='A')
 dirty=Path(ws['worktree_path'])/'retained.txt'; dirty.write_text('preserve\n'); before=dirty.read_bytes()
 def dispatch(conn,rev): conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('LANE',0,'A','queued','n',?)",(rev,)); conn.execute("INSERT INTO workflow_dispatches(id,lane_id,session_id,claim_id,workspace_id,state,context_version,heartbeat_at,hostname,pid,created_at,revision) VALUES('D','LANE',?,?,?,'active',1,'2000',?,999999,'n',?)",(c['session']['agent_id'],c['claim']['claim_id'],ws['workspace_id'],socket.gethostname(),rev))
 repo.service.db.mutate(actor_session_id=None,entity_type='f',entity_id='d',event_type='f',payload={},operation=dispatch)
 env={'PROJECT_CONTROL_SKILLS_ROOT':sys.argv[1],'TODO_ORCHESTRATOR_STATE_DIR':str(repo.state_root),'PYTHONPATH':os.pathsep.join([str(Path.cwd()/'src'),str(Path(sys.argv[1])/'todo-orchestrator')])}; result=subprocess.run([sys.executable,'-m','project_control.cli','admin','prepare-maintenance','--repo',str(repo.root),'--task','A','--run','RUN','--recipient','op'],cwd=str(Path.cwd()),env=env,text=True,capture_output=True); assert result.returncode != 0, result.stderr; assert 'cannot bind the selected run' in result.stderr, result.stderr; assert dirty.read_bytes()==before
finally: repo.close()
'''
        environment = dict(os.environ)
        skills = Path(environment.get("PROJECT_CONTROL_SKILLS_ROOT", str(SKILLS)))
        environment["PYTHONPATH"] = os.pathsep.join([str(skills / "todo-orchestrator/tests"), environment.get("PYTHONPATH", "")])
        result = subprocess.run([sys.executable, "-c", child, str(skills)], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
