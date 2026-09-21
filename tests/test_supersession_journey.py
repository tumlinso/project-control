from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class SupersessionJourneyTests(unittest.TestCase):
    def _fixture(self, root: Path):
        from todo_orchestrator.projections import atomic_write_json
        from todo_orchestrator.service import Service
        service, _ = Service.bootstrap(root, "fixture")
        plan = {"schema_version": 2, "project": {"name": "fixture"}, "invariants": [], "decisions": [], "locks": [], "interfaces": [], "barriers": [], "resource_classes": [], "tasks": [
            {"id": "OLD", "kind": "task", "title": "old", "objective": "old", "priority": 0, "parallel_policy": "parallel_safe", "scope": {"exclusive_paths": ["old"]}},
            {"id": "NEW", "kind": "task", "title": "new", "objective": "new", "priority": 0, "parallel_policy": "parallel_safe", "scope": {"exclusive_paths": ["new"]}},
        ]}
        atomic_write_json(root / "plan.json", plan); service.plan_apply(str(root / "plan.json"))
        (root / ".gitignore").write_text("runtime/\nplan.json\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"], check=True)
        (root / "retained.txt").write_bytes(b"dirty preserved work\n")
        head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        def seed(conn, revision):
            conn.execute("UPDATE workflow_lane_tasks SET state='skipped',completed_at='now',revision=? WHERE lane_id='compat-v2-main'", (revision,))
            for run, task in (("OLD-RUN", "OLD"), ("NEW-RUN", "NEW")):
                conn.execute("INSERT INTO workflow_runs(id,root_task_id,status,created_at,updated_at,revision) VALUES(?,?, 'active','now','now',?)", (run, task, revision))
            for lane, run in (("OLD-LANE", "OLD-RUN"), ("NEW-LANE", "NEW-RUN")):
                conn.execute("INSERT INTO workflow_lanes(id,run_id,role,workspace_mode,created_at,updated_at,revision) VALUES(?,?, 'implementer','exclusive','now','now',?)", (lane, run, revision))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('OLD-LANE',0,'OLD','queued','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('NEW-LANE',0,'NEW','queued','now',?)", (revision,))
            conn.execute("INSERT INTO workflow_workspaces(id,repository_identity,run_id,lane_id,mode,base_commit,worktree_path,state,cleanup_eligible,created_at,updated_at) VALUES('OLD-WS','repo-id','OLD-RUN','OLD-LANE','exclusive',?,?, 'quarantined',0,'now','now')", (head, str(root)))
        service.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="OLD", event_type="fixture.seed", payload={}, operation=seed)
        return service

    def test_signed_dirty_handoff_replays_and_claims_exact_successor(self):
        from project_control import admin
        from todo_orchestrator.workflow.service import WorkflowKernel
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            with patch.dict(os.environ, {"TODO_ORCHESTRATOR_STATE_DIR": str(root / "runtime")}, clear=False), patch.object(admin, "_runtime_identity"):
                self._fixture(root)
                intent = root / "intent.json"
                intent.write_text(json.dumps({"source_run_id": "OLD-RUN", "successor_run_id": "NEW-RUN", "reason": "replace", "preserved_work_handoffs": [{"source_workspace_id": "OLD-WS", "source_lane_id": "OLD-LANE", "successor_lane_id": "NEW-LANE", "successor_task_id": "NEW", "adopt_dirty": True}]}), encoding="utf-8")
                assignment = admin.prepare_supersession_assignment(root, intent, recipient_principal="operator")
                authorization_id = assignment["assignment"]["grant_reference"]
                first = admin.maintain_execution(root, authorization_id=authorization_id, recipient_principal="operator")
                second = admin.maintain_execution(root, authorization_id=authorization_id, recipient_principal="operator")
                self.assertEqual((first["status"], second["status"], second["replayed"]), ("superseded", "superseded", True))
                self.assertEqual((root / "retained.txt").read_bytes(), b"dirty preserved work\n")
                call = first["recommended_next_call"]
                self.assertEqual(call["arguments"], {"repo_root": str(root), "run_id": "NEW-RUN", "task_id": "NEW"})
                claimed = WorkflowKernel().next_task(**call["arguments"])
                self.assertIn(claimed["status"], {"claimed", "needs_context"})

    def test_signed_grant_refuses_wrong_principal(self):
        from project_control import admin
        with TemporaryDirectory() as temporary:
            root = Path(temporary); subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            with patch.dict(os.environ, {"TODO_ORCHESTRATOR_STATE_DIR": str(root / "runtime")}, clear=False), patch.object(admin, "_runtime_identity"):
                service = self._fixture(root)
                intent = root / "intent.json"; intent.write_text(json.dumps({"source_run_id": "OLD-RUN", "successor_run_id": "NEW-RUN", "reason": "replace"}), encoding="utf-8")
                grant = admin.prepare_supersession_assignment(root, intent, recipient_principal="operator")["assignment"]["grant_reference"]
                with self.assertRaisesRegex(ValueError, "principal"):
                    admin.maintain_execution(root, authorization_id=grant, recipient_principal="wrong")
                with service.db.read() as conn:
                    self.assertEqual(conn.execute("SELECT status FROM workflow_runs WHERE id='OLD-RUN'").fetchone()[0], "active")

    def test_prepared_renamed_destination_change_refuses_public_application(self):
        from project_control import admin
        from todo_orchestrator.models import TodoError
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            with patch.dict(os.environ, {"TODO_ORCHESTRATOR_STATE_DIR": str(root / "runtime")}, clear=False), patch.object(admin, "_runtime_identity"):
                service = self._fixture(root)
                old = root / "old.txt"
                old.write_text("baseline\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(root), "add", "old.txt"], check=True)
                subprocess.run(["git", "-C", str(root), "commit", "-qm", "rename baseline"], check=True)
                renamed = root / "new.txt"
                old.rename(renamed)
                renamed.write_text("reviewed\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
                intent = root / "intent.json"
                intent.write_text(json.dumps({"source_run_id": "OLD-RUN", "successor_run_id": "NEW-RUN", "reason": "replace", "preserved_work_handoffs": [{"source_workspace_id": "OLD-WS", "source_lane_id": "OLD-LANE", "successor_lane_id": "NEW-LANE", "successor_task_id": "NEW", "adopt_dirty": True}]}), encoding="utf-8")
                grant = admin.prepare_supersession_assignment(root, intent, recipient_principal="operator")["assignment"]["grant_reference"]
                renamed.write_text("changed after review\n", encoding="utf-8")
                with self.assertRaises(TodoError) as stale:
                    admin.maintain_execution(root, authorization_id=grant, recipient_principal="operator")
                self.assertEqual(stale.exception.code, "retirement_preserved_workspace_stale")
                with service.db.read() as conn:
                    self.assertEqual(conn.execute("SELECT status FROM workflow_runs WHERE id='OLD-RUN'").fetchone()[0], "active")

    def test_preparation_refuses_missing_authority_without_restoring_snapshot(self):
        from project_control import admin
        from todo_orchestrator.models import TodoError
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            with patch.dict(os.environ, {"TODO_ORCHESTRATOR_STATE_DIR": str(root / "runtime")}, clear=False), patch.object(admin, "_runtime_identity"):
                service = self._fixture(root)
                service.export()
                state_db = service.paths.db_file
                state_db.unlink()
                self.assertTrue(service.paths.snapshot_file.exists())
                intent = root / "intent.json"
                intent.write_text(json.dumps({"source_run_id": "OLD-RUN", "successor_run_id": "NEW-RUN", "reason": "replace"}), encoding="utf-8")
                with self.assertRaises(TodoError) as unavailable:
                    admin.prepare_supersession_assignment(root, intent, recipient_principal="operator")
                self.assertEqual(unavailable.exception.code, "todo_state_unavailable")
                self.assertFalse(state_db.exists())

    def test_cli_operator_and_ordinary_mcp_claim_adopted_isolated_worktree(self):
        child = r'''
import asyncio, json, os, subprocess, sys
from pathlib import Path
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from v2_helpers import V2Repo, base_plan, safe_task
from todo_orchestrator.workflow.service import WorkspaceService, repository_identity
repo=V2Repo()
try:
 repo.apply(base_plan([safe_task('OLD','src/old'),safe_task('NEW','src/new'),safe_task('INT','src/new',parallel_policy='integration_exclusive',gates=[{'id':'INT-RETAINED','type':'file_exists','path':'retained.txt','required':True}])]))
 (repo.root/'seed').write_text('seed\n'); subprocess.run(['git','-C',str(repo.root),'add','seed'],check=True); subprocess.run(['git','-C',str(repo.root),'commit','-qm','seed'],check=True)
 def seed(conn,rev):
  conn.execute("UPDATE workflow_lane_tasks SET state='skipped',completed_at='n',revision=? WHERE lane_id='compat-v2-main'",(rev,))
  for run,task in [('OLD-RUN','OLD'),('NEW-RUN','NEW')]: conn.execute("INSERT INTO workflow_runs(id,root_task_id,status,created_at,updated_at,revision) VALUES(?,?, 'active','n','n',?)",(run,task,rev))
  for lane,run,parent,role,mode in [('OLD-LANE','OLD-RUN',None,'implementer','isolated_merge'),('NEW-LANE','NEW-RUN',None,'implementer','isolated_merge'),('INT-LANE','NEW-RUN','NEW-LANE','integrator','exclusive')]: conn.execute("INSERT INTO workflow_lanes(id,run_id,parent_lane_id,role,workspace_mode,state,created_at,updated_at,revision) VALUES(?,?,?,?,?, 'ready','n','n',?)",(lane,run,parent,role,mode,rev))
  conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('OLD-LANE',0,'OLD','queued','n',?)",(rev,)); conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('NEW-LANE',0,'NEW','queued','n',?)",(rev,)); conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('INT-LANE',0,'INT','queued','n',?)",(rev,))
 repo.service.db.mutate(actor_session_id=None,entity_type='f',entity_id='r',event_type='f',payload={},operation=seed)
 ws=WorkspaceService(repo.service.db,managed_root=repo.service.paths.state_dir/'workflow-workspaces',repository_identity_resolver=lambda root:repository_identity(root,str(repo.service.project['project_uuid']))).create_workspace(repository_root=repo.root,repository_identity=repository_identity(repo.root,str(repo.service.project['project_uuid'])),run_id='OLD-RUN',lane_id='OLD-LANE',mode='isolated_merge',base_commit='HEAD',worktree_path=repo.service.paths.state_dir/'workflow-workspaces'/'old',branch='old',integration_task_id='OLD')
 WorkspaceService(repo.service.db,managed_root=repo.service.paths.state_dir/'workflow-workspaces',repository_identity_resolver=lambda root:repository_identity(root,str(repo.service.project['project_uuid']))).create_workspace(repository_root=repo.root,repository_identity=repository_identity(repo.root,str(repo.service.project['project_uuid'])),run_id='NEW-RUN',lane_id='INT-LANE',mode='exclusive',base_commit='HEAD',worktree_path=repo.service.paths.state_dir/'workflow-workspaces'/'integrator',branch='integrator',integration_task_id='INT')
 retained=Path(ws['worktree_path'])/'retained.txt'; retained.write_text('preserved\n'); before=retained.read_bytes()
 repo.service.db.mutate(actor_session_id=None,entity_type='f',entity_id='w',event_type='f',payload={},operation=lambda c,r:c.execute("UPDATE workflow_workspaces SET state='quarantined' WHERE id=?",(ws['workspace_id'],)))
 intent=repo.root/'intent.json'; intent.write_text(json.dumps({'source_run_id':'OLD-RUN','successor_run_id':'NEW-RUN','reason':'replace','preserved_work_handoffs':[{'source_workspace_id':ws['workspace_id'],'source_lane_id':'OLD-LANE','successor_lane_id':'NEW-LANE','successor_task_id':'NEW','adopt_dirty':True}]}))
 env={'PROJECT_CONTROL_SKILLS_ROOT':sys.argv[1],'TODO_ORCHESTRATOR_STATE_DIR':str(repo.state_root)}
 if os.environ.get('PROJECT_CONTROL_RELEASE_MANIFEST'):
  for key in ('PROJECT_CONTROL_RELEASE_MANIFEST','PROJECT_CONTROL_RELEASE_DIGEST'): env[key]=os.environ[key]
 else: env['PYTHONPATH']=os.pathsep.join([str(Path.cwd()/'src'),str(Path(sys.argv[1])/'todo-orchestrator')])
 issued=subprocess.run([sys.executable,'-m','project_control.cli','admin','prepare-supersession','--repo',str(repo.root),'--intent',str(intent),'--recipient','op'],cwd=str(Path.cwd()),env=env,text=True,capture_output=True); assert issued.returncode==0,issued.stderr
 assignment=json.loads(issued.stdout)
 async def call(command,name,args):
  p=StdioServerParameters(command=command['executable'],args=command['arguments'],env={**command['environment'],'TODO_ORCHESTRATOR_STATE_DIR':str(repo.state_root)},cwd=str(Path.cwd()))
  async with stdio_client(p) as (rd,wr):
   async with ClientSession(rd,wr) as s:
    await s.initialize(); x=await s.call_tool(name,args); assert not x.isError,x.content; return json.loads(x.content[0].text)
 maintained=asyncio.run(call(assignment['operator_launch'],'maintain_execution',{'repo_root':str(repo.root),'authorization_id':assignment['assignment']['grant_reference']}))
 ordinary={'executable':assignment['operator_launch']['executable'],'arguments':['-m','project_control.cli','codex'],'environment':assignment['operator_launch']['environment']}
 claimed=asyncio.run(call(ordinary,'next_task',maintained['recommended_next_call']['arguments']))
 assert retained.read_bytes()==before and claimed['status']=='claimed',claimed
 subprocess.run(['git','-C',str(retained.parent),'add','retained.txt'],check=True); subprocess.run(['git','-C',str(retained.parent),'commit','-qm','adopted producer'],check=True)
 completed=asyncio.run(call(ordinary,'finish_task',{'workflow_handle':claimed['workflow_handle'],'action':'complete','disposition':'implemented'}))
 assert completed['status']=='idle',completed
 integrator=asyncio.run(call(ordinary,'next_task',{'repo_root':str(repo.root),'run_id':'NEW-RUN','task_id':'INT'}))
 applied=asyncio.run(call(ordinary,'coordinate_task',{'workflow_handle':integrator['workflow_handle'],'action':'run_gates','payload':{'required':True}}))
 with repo.service.db.read() as c:
  dispatch=c.execute("SELECT workspace_id FROM workflow_dispatches WHERE state='active'").fetchone()[0]
  integration_task=c.execute("SELECT integration_task_id FROM workflow_workspaces WHERE id=?",(maintained['receipt']['preserved_workspace_ids'][0],)).fetchone()[0]
  integration_state=c.execute("SELECT state FROM workflow_integration_queue").fetchone()[0]
 assert dispatch!=maintained['receipt']['preserved_workspace_ids'][0],(dispatch,maintained)
 assert integration_task=='INT',integration_task
 assert integration_state=='integrated',(applied,integration_state)
 print(json.dumps({'status':maintained['status'],'claimed':claimed['status'],'integrated':integration_state}))
finally: repo.close()
'''
        environment = dict(os.environ)
        skills = Path(environment.get("PROJECT_CONTROL_SKILLS_ROOT", "/home/tumlinson/.agents/skills"))
        environment["PYTHONPATH"] = os.pathsep.join([str(skills / "todo-orchestrator/tests"), environment.get("PYTHONPATH", "")])
        result = subprocess.run([os.sys.executable, "-c", child, str(skills)], cwd=Path(__file__).parents[1], env=environment, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "superseded", "claimed": "claimed", "integrated": "integrated"})
