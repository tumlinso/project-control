"""The public repository read must not block the MCP event loop."""

import asyncio
import threading
import time
from unittest.mock import patch

from project_control.app import create_mcp
from project_control.config import ProjectControlConfig
from project_control.profiles import enumerate_tool_schemas


def test_read_offloads_blocking_information_call_and_keeps_schema(tmp_path):
    with patch('project_control.app.todo_read_port_factory', return_value=None):
        server = create_mcp(ProjectControlConfig(), state_directory=tmp_path / 'state')
    entered = threading.Event()
    release = threading.Event()
    release_at = []

    def blocked_read(tool, **kwargs):
        assert tool == 'read'
        entered.set()
        assert release.wait(2)
        return {'status': 'ok', 'paths': kwargs['paths'], 'revision': kwargs['revision']}

    server._project_control_surface.information.call = blocked_read

    async def exercise():
        async def tick_while_read_waits():
            await asyncio.sleep(.03)
            return time.monotonic()

        timer = threading.Timer(.3, lambda: (release_at.append(time.monotonic()), release.set()))
        timer.start()
        try:
            schemas = await enumerate_tool_schemas(server)
            read_schema = schemas['read']
            assert set(read_schema['properties']) == {
                'project', 'paths', 'repository', 'revision', 'detail'}
            assert set(read_schema['required']) == {'project', 'paths'}
            read_task = asyncio.create_task(server.call_tool('read', {
                'project': 'pc', 'paths': ['README.md']}))
            tick_task = asyncio.create_task(tick_while_read_waits())
            tick_at = await tick_task
            assert entered.is_set()
            assert not release.is_set()
            result = await read_task
            assert result[1] == {'status': 'ok', 'paths': ['README.md'], 'revision': None}
            assert release_at and tick_at < release_at[0]
        finally:
            release.set()
            timer.cancel()
            server._project_control_surface.close()

    asyncio.run(exercise())
