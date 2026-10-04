"""Actual public calls for identical fixed source and semantic snapshot ports.

Snapshot injection is explicit fixture data, not a replacement authentication or
tool implementation. Frozen baseline keeps its installed release binding.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from project_control.app import Runtime, create_mcp
from project_control.config import ProjectControlConfig, WorkspaceConfig, RepositoryConfig
from project_control.models import ProjectSnapshot, RepositoryIdentity
import project_control.app

def wire(value):
    if hasattr(value, 'model_dump'):
        return wire(value.model_dump(mode='json'))
    if isinstance(value, (list, tuple)):
        return [wire(v) for v in value]
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    return value

def size(value):
    return len(json.dumps(wire(value), ensure_ascii=False, separators=(',', ':')).encode())

root = Path(os.environ['AS1_BENCH_ROOT'])
mode = os.environ['AS1_BENCH_MODE']
files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ('README.md', 'module.py')}
commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
snapshot = ProjectSnapshot(workspace_id='demo', project_uuid='fixed-economics-fixture', observed_at='2026-10-04T00:00:00Z',
    todo_revision=10, repositories={'source': RepositoryIdentity(commit=commit, dirty=False)},
    todo_tables={'tasks': [{'id': 'T1', 'title': 'Calculation', 'status': 'ready', 'revision': 2}],
                 'gates': [{'id': 'G1', 'task_id': 'T1', 'status': 'pending', 'valid': False}]})
Runtime.snapshot = lambda self, project, **kwargs: snapshot
config = ProjectControlConfig(workspaces={'demo': WorkspaceConfig(repositories={'source': RepositoryConfig(root=root)})})
start = time.perf_counter()
server = create_mcp(config, profile='observer')
startup = time.perf_counter() - start
schemas = asyncio.run(server.list_tools())
if mode == 'new':
    surface = server._project_control_surface
    # Both implementations receive the same absent declaration provider state.
    surface.information.semantic_provider = None
    calls = [('overview', {'project': 'demo'}),
             ('read', {'project': 'demo', 'repository': 'source', 'paths': ['README.md', 'module.py'], 'revision': commit}),
             ('search', {'project': 'demo', 'query': {'kind': 'task', 'target': 'T1'}}),
             ('evidence', {'project': 'demo', 'subject': 'T1'})]
else:
    calls = [('project_overview', {'project': 'demo', 'detail': 'compact'}),
             ('source_context', {'project': 'demo', 'repository': 'source', 'targets': [{'kind': 'path', 'value': p} for p in files], 'source_selector': commit, 'detail': 'compact'}),
             ('inspect', {'project': 'demo', 'kind': 'task', 'target': 'T1'}),
             ('evidence', {'project': 'demo', 'subject': 'T1'})]
results = []
for name, arguments in calls:
    started = time.perf_counter()
    raw = asyncio.run(server.call_tool(name, arguments))
    elapsed = time.perf_counter() - started
    structured = raw[1] if isinstance(raw, tuple) else raw
    results.append({'tool': name, 'arguments': arguments, 'request_bytes': size({'name': name, 'arguments': arguments}),
                    'response_bytes': size(raw), 'latency_seconds': elapsed, 'result': wire(structured)})
all_text = json.dumps(results)
def dictionaries(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from dictionaries(child)
    elif isinstance(value,list):
        for child in value:yield from dictionaries(child)
# Exact source and canonical semantic facts must be returned by both tools.
assert 'calculate_total' in all_text and 'value + 1' in all_text
assert 'module.py' in all_text and 'README.md' in all_text
assert 'T1' in all_text and 'Calculation' in all_text and 'G1' in all_text
assert 'pending' in all_text and commit in all_text, all_text
assert any(row.get('id')=='T1' and row.get('title')=='Calculation' and row.get('status')=='ready' for row in dictionaries(results[2]['result']))
assert any(row.get('id')=='G1' and row.get('status')=='pending' and row.get('valid') is False for row in dictionaries(results[3]['result']))
assert any(row.get('support')==[] and row.get('confidence')=='insufficient' and 'T1' in row.get('unmeasured_or_unvalidated',[]) for row in dictionaries(results[3]['result']))
if mode=='new':assert results[3]['result']['data']['source_mentions_are_proof'] is False
schema_bytes = size(schemas)
prompt_bytes = len(server.instructions.encode())
metrics = {'schema_bytes': schema_bytes, 'prompt_bytes': prompt_bytes,
           'tool_request_bytes': sum(r['request_bytes'] for r in results),
           'tool_response_bytes': sum(r['response_bytes'] for r in results),
           'calls': len(calls), 'retries': 0, 'repeated_full_reads': 0,
           'authority_revalidation_calls': 0, 'startup_seconds': startup,
           'latency_seconds': sum(r['latency_seconds'] for r in results),
           'model_tokens': None, 'model_usage_status': 'unmetered_no_inference',
           'queue_wait_seconds': 0, 'model_starts': 0,
           'human_prompts': 0}
metrics['total_client_visible_bytes'] = schema_bytes + prompt_bytes + metrics['tool_request_bytes'] + metrics['tool_response_bytes']
identity = {'module': project_control.app.__file__, 'module_sha256': hashlib.sha256(Path(project_control.app.__file__).read_bytes()).hexdigest(),
            'release_manifest': os.environ.get('PROJECT_CONTROL_RELEASE_MANIFEST'),
            'release_digest': os.environ.get('PROJECT_CONTROL_RELEASE_DIGEST')}
print('QUALIFICATION_JSON=' + json.dumps({'mode': mode, 'snapshot': snapshot.model_dump(mode='json'),
      'source_sha256': files, 'identity': identity, 'metrics': metrics, 'calls': results,
      'correct_facts': ['exact two file content', 'T1 Calculation', 'G1 pending', 'Git commit provenance'],
      'limits': ['Fixed snapshot fixture port; not live authority timing.', 'No model token, monetary, GPU or energy comparison.', 'No historical baseline for newly added cross-project trace.']}))
if mode == 'new':
    server._project_control_surface.close()
