"""Public AS1 transport contract; legacy backend behavior stays in service tests."""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import ANY, patch

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp.exceptions import ToolError
from starlette.testclient import TestClient
from project_control.app import create_asgi_app, create_mcp, serve_codex
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.profiles import CODEX_TOOL_NAMES, OBSERVER_TOOL_NAMES, CODEX_RICH_READ_DESCRIPTION_PREFIX


class MCPServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / 'repo'; self.root.mkdir()
        for args in (['init', '-b', 'main'], ['config', 'user.email', 'test@example.invalid'], ['config', 'user.name', 'Fixture']):
            subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)
        (self.root / 'README.md').write_text('fixture\n')
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        subprocess.run(['git', 'commit', '-m', 'fixture'], cwd=self.root, check=True, capture_output=True)
        self.config = ProjectControlConfig(workspaces={'demo': WorkspaceConfig(repositories={'source': RepositoryConfig(root=self.root)})})
        self.binding = patch('project_control.app.todo_read_port_factory', return_value=None); self.binding.start()
        self.state = patch.dict('os.environ', {'XDG_STATE_HOME': self.temporary.name}); self.state.start()
        self.servers = []
    def tearDown(self):
        for mcp in self.servers:
            mcp._project_control_surface.close()
        self.binding.stop(); self.state.stop(); self.temporary.cleanup()
    def make(self, profile='observer'):
        mcp = create_mcp(self.config, profile=profile)
        self.servers.append(mcp)
        return mcp
    def test_exact_tools_annotations_and_schemas(self):
        tools = asyncio.run(self.make().list_tools())
        self.assertEqual({t.name for t in tools}, set(OBSERVER_TOOL_NAMES))
        for tool in tools:
            self.assertTrue(tool.annotations.readOnlyHint)
        schemas = {t.name: t.inputSchema for t in tools}
        self.assertEqual(schemas['overview']['properties']['detail']['enum'], ['compact', 'standard', 'extended'])
        self.assertEqual(schemas['overview']['properties']['detail']['default'], 'compact')
        self.assertEqual(set(schemas['read']['required']), {'project', 'paths'})
        self.assertNotIn('profile', schemas['search']['properties'])
    def test_codex_composes_workflow_and_compact_reads(self):
        mcp = self.make('codex')
        tools = asyncio.run(mcp.list_tools())
        self.assertEqual({t.name for t in tools}, set(CODEX_TOOL_NAMES))
        for tool in tools:
            if tool.name not in {'next_task', 'inspect_task', 'coordinate_task', 'finish_task'}:
                self.assertTrue(tool.description.startswith(CODEX_RICH_READ_DESCRIPTION_PREFIX))
        self.assertNotIn('read', {t.name for t in tools})
    def test_hidden_workflow_and_legacy_aliases_rejected_before_binding(self):
        mcp = self.make()
        for name in ('next_task', 'project_overview', 'source_context', 'find', 'delegate_task', 'collect_delegation'):
            with self.assertRaises(ToolError):
                asyncio.run(mcp.call_tool(name, {'project': 'demo'}))
    def test_runtime_receives_configured_read_port_factory(self):
        factory = lambda root: None
        with patch('project_control.app.todo_read_port_factory', return_value=factory) as configured:
            mcp = self.make()
        configured.assert_called_once_with()
        self.assertIs(mcp._project_control_runtime.builder.todo_read_port_factory, factory)
    def test_codex_stdio_transport_closes_composition(self):
        with patch('project_control.app.create_mcp') as create:
            self.assertEqual(serve_codex(), 0)
        create.assert_called_once_with(profile=ANY, maintenance_host=None)
        create.return_value.run.assert_called_once_with(transport='stdio')
        create.return_value._project_control_surface.start.assert_called_once()
        create.return_value._project_control_surface.close.assert_called_once()
    def test_health_ready_version_and_nonloopback_refusal(self):
        owner = {'observer_contract': 'PC-OBSERVER-SUPERVISOR/1', 'supervisor_pid': 42,
                 'supervisor_process_start': 'fixture', 'source_sha256': 'a' * 64}
        with patch('project_control.app.SkillsObserverAnalysisProvider.central_status', return_value=owner) as central, \
                TestClient(create_asgi_app(self.config)) as client:
            self.assertEqual(client.get('/healthz').status_code, 200)
            self.assertEqual(client.get('/readyz').status_code, 200)
            central.side_effect = RuntimeError('central_supervisor_unavailable')
            ready = client.get('/readyz')
            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json()['core']['status'], 'available')
            self.assertEqual(ready.json()['central_inference']['reason'], 'central_supervisor_unavailable')
            version = client.get('/version').json()
            self.assertEqual(version['tool_schema_version'], 10)
            self.assertFalse(version['features']['automatic_overview'])
        from project_control.config import ServerConfig
        with self.assertRaises(ValueError):
            ServerConfig(host='0.0.0.0')
    def test_official_streamable_http_client(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        app = create_asgi_app(self.config)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        thread = threading.Thread(target=server.run, daemon=True); thread.start()
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=.2).close(); break
                except Exception:
                    time.sleep(.02)
            else:
                self.fail('server did not start')
            async def protocol():
                async with streamable_http_client(f'http://127.0.0.1:{port}/mcp') as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self.assertEqual({t.name for t in (await session.list_tools()).tools}, set(OBSERVER_TOOL_NAMES))
                        self.assertEqual((await session.list_resources()).resources, [])
                        self.assertEqual((await session.list_prompts()).prompts, [])
                        for name, args in [('overview', {}), ('read', {'project': 'demo', 'paths': ['README.md']})]:
                            result = await session.call_tool(name, args)
                            self.assertFalse(result.isError, result)
                            payload = json.loads(result.content[0].text)
                            self.assertTrue(payload['packet'])
                        removed = await session.call_tool('project_overview', {'project': 'demo'})
                        self.assertTrue(removed.isError)
            asyncio.run(protocol())
        finally:
            server.should_exit = True; thread.join(timeout=5)
