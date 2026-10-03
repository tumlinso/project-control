"""Qualify a release launcher through native MCP stdio without machine inference."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def data(result):
    assert not result.isError
    if result.structuredContent:
        return result.structuredContent
    return json.loads(next(block.text for block in result.content if block.type == 'text'))


async def qualify(launcher):
    parameters = StdioServerParameters(command=str(launcher), args=['codex'])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            names = {row.name for row in tools.tools}
            assert {'skill_list', 'skill_read', 'skill_context'} <= names
            for tool in tools.tools:
                if tool.name.startswith('skill_'):
                    assert tool.annotations.readOnlyHint
                    assert not {'root', 'project', 'backend'} & set(tool.inputSchema['properties'])
            cuda = data(await session.call_tool('skill_list', {'query': 'CUDA'}))
            cpp = data(await session.call_tool('skill_list', {'query': 'C++'}))
            cuda_id = next(s['id'] for s in cuda['skills'] if s['name'] == 'cuda')
            cpp_id = next(s['id'] for s in cpp['skills'] if s['name'] == 'cpp-context-compiler')
            read_result = data(await session.call_tool('skill_read', {'skill_id': cuda_id, 'resource': 'references/architectures/volta/v100_atlas/README.md'}))
            assert read_result['content']
            context = data(await session.call_tool('skill_context', {'query': 'authoring', 'skill': cpp_id}))
            assert context['route'] == 'indexed' and context['status'] == 'available'
            assert context['evidence']
            for result in (cuda, cpp, read_result, context):
                assert result['origin'] == 'agent_skill'
                assert result['authority'] == 'advisory_instruction'
                assert result['mutation_authority'] is False
                assert str(Path.home()) not in json.dumps(result)
            return {'transport': 'native_mcp_stdio', 'profile': 'codex', 'tool_count': len(names),
                    'server': initialized.serverInfo.model_dump(),
                    'launcher_sha256': hashlib.sha256(launcher.read_bytes()).hexdigest(),
                    'skill_ids': [cuda_id, cpp_id], 'atlas_resource_identity': read_result['identity'],
                    'cpp_context': {k: context[k] for k in ('status', 'route', 'corpus_identity', 'coverage', 'route_reason')},
                    'machine_calls': 0,
                    'boundary': 'Release launcher pins execution runtime; advisory root is separately configured and public calls use opaque IDs/relative resources.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('launcher', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(qualify(args.launcher))
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
