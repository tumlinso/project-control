"""Isolated source-bound runners; never change the parent deployment binding."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SKILLS = Path('/home/tumlinson/.agents/skills')
REPORTS = ROOT / 'planning/adaptive-surface-v1/validation/qualification'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def child(source, name, *, environment=None, interpreter=None):
    from project_control.runtime_identity import package_fingerprint
    env = dict(os.environ)
    for key in ('PROJECT_CONTROL_RELEASE_MANIFEST', 'PROJECT_CONTROL_RELEASE_DIGEST',
                'PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT', 'CODING_WORKFLOW_RUNTIME_FINGERPRINT',
                'CODING_WORKFLOW_SKILLS_ROOT', 'TODO_ORCHESTRATOR_READ_ONLY', 'TODO_ORCHESTRATOR_STATE_DIR'):
        env.pop(key, None)
    native = SKILLS / 'todo-orchestrator'
    env.update(PROJECT_CONTROL_SKILLS_ROOT=str(SKILLS),
               PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT=package_fingerprint(native / 'todo_orchestrator'),
               PYTHONPATH=os.pathsep.join([str(ROOT / 'src'), str(ROOT / 'tests/as1'), str(ROOT / 'tests'), str(native), str(native / 'tests')]),
               PYTHONDONTWRITEBYTECODE='1')
    source_paths=list((ROOT/'src/project_control').glob('as1_*.py'))
    source_paths += [native/'todo_orchestrator'/p for p in ('project_amendments.py','workflow/protocol.py','workflow/service.py','workflow/capabilities.py','workflow/roles.py')]
    env['AS1_QUALIFY_SOURCE_HASHES']=json.dumps({str(p):sha(p) for p in source_paths})
    if environment:
        env.update(environment)
    result = subprocess.run([interpreter or sys.executable, '-c', source], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    record = json.loads(result.stdout.split('QUALIFICATION_JSON=')[-1])
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / (name + '.json')).write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    return record


COMMON = r'''
import asyncio, hashlib, json, os, subprocess, tempfile, time
from pathlib import Path
from project_control.app import create_mcp
from project_control.config import ProjectControlConfig, WorkspaceConfig, RepositoryConfig
from project_control.profiles import enumerate_tool_schemas
from project_control.as1_context import ContextHost
from todo_orchestrator.service import Service
from v2_helpers import base_plan, safe_task
import project_control.as1_surface, todo_orchestrator.workflow.protocol
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
source_identity = {str(Path(m.__file__).resolve()):digest(m.__file__) for m in (project_control.as1_surface,todo_orchestrator.workflow.protocol)}
expected_source_identity=json.loads(os.environ['AS1_QUALIFY_SOURCE_HASHES'])
assert all(digest(path)==expected for path,expected in expected_source_identity.items())
source_identity.update(expected_source_identity)
assert Path(project_control.as1_surface.__file__).resolve() == Path.cwd()/'src/project_control/as1_surface.py'
assert Path(todo_orchestrator.workflow.protocol.__file__).resolve() == Path(os.environ['PROJECT_CONTROL_SKILLS_ROOT'])/'todo-orchestrator/todo_orchestrator/workflow/protocol.py'
tmp = tempfile.TemporaryDirectory(); home=Path(tmp.name)
os.environ['XDG_STATE_HOME']=str(home/'state'); os.environ['XDG_CACHE_HOME']=str(home/'cache')
def git(root,*args): return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
def repository(name, files):
    root=home/name; root.mkdir(); git(root,'init','-q','-b','main'); git(root,'config','user.name','Fixture'); git(root,'config','user.email','fixture@example.invalid')
    for path,text in files.items():
        p=root/path; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text)
    (root/'.gitignore').write_text('.todo-orchestrator/\ntodos/\ntodos.md\ntodo-status.md\n')
    git(root,'add','.'); git(root,'commit','-qm','qualification fixture')
    service,_=Service.bootstrap(root,name)
    return root,service
def configuration(roots):
    return ProjectControlConfig(skills_root=Path(os.environ['PROJECT_CONTROL_SKILLS_ROOT']),workspaces={n:WorkspaceConfig(authority_repository='source',repositories={'source':RepositoryConfig(root=r)}) for n,r in roots.items()})
def call(server,name,args):
    value=asyncio.run(server.call_tool(name,args)); return value[1] if isinstance(value,tuple) else value
def amend(server,name,service,action,payload,operation):
    return call(server,'amend_project',{'request':{'format':'pc-project-amendment/1','project':name,'action':action,'intent':'qualification fixture','expected_revision':service.db.revision(),'operation_id':operation,'payload':payload,'mode':'apply'}})
def emit(data): print('QUALIFICATION_JSON='+json.dumps({'source_identity':source_identity,**data}))
'''
