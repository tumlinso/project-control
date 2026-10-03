"""Qualify activated HTTP MCP without fixtures or backend substitution."""
import argparse
import asyncio
from datetime import timedelta, datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def decode(result):
    if result.isError:
        raise AssertionError(str(result.content))
    return result.structuredContent or json.loads(next(c.text for c in result.content if c.type == 'text'))


async def qualify(base, output):
    receipt = {'transport': 'native_mcp_streamable_http', 'started_at': datetime.now(timezone.utc).isoformat(),
               'backend_substitution': False, 'cases': [], 'machine_calls': 0}
    def save():
        output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    async with httpx.AsyncClient(timeout=20) as http:
        receipt['http'] = {}
        for path in ('healthz', 'readyz', 'version'):
            response = await http.get(base + '/' + path)
            receipt['http'][path] = {'status_code': response.status_code, 'body': response.json()}
            assert response.status_code == 200
    save()
    async with streamablehttp_client(base + '/mcp', timeout=30, sse_read_timeout=480) as (read, write, _):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=480)) as session:
            initialized = await session.initialize()
            receipt['server'] = initialized.serverInfo.model_dump()
            tools = (await session.list_tools()).tools
            receipt['tool_names'] = sorted(t.name for t in tools)
            receipt['tool_count'] = len(tools)
            assert {'skill_list', 'skill_read', 'skill_context'} <= set(receipt['tool_names'])
            for tool in tools:
                if tool.name.startswith('skill_'):
                    assert tool.annotations.readOnlyHint
                    assert not {'root', 'project', 'backend'} & set(tool.inputSchema['properties'])
            async def call(name, args):
                started = time.monotonic()
                result = decode(await session.call_tool(name, args))
                encoded = json.dumps(result, sort_keys=True).encode()
                assert str(Path.home()) not in encoded.decode()
                assert result['origin'] == 'agent_skill'
                assert result['authority'] == 'advisory_instruction'
                assert result['mutation_authority'] is False
                print(f'{name}: {result.get("status")} {result.get("route", "")} {time.monotonic()-started:.2f}s', flush=True)
                return result, len(encoded)
            ids = {}
            for query, name in [('CUDA', 'cuda'), ('C++', 'cpp-context-compiler')]:
                listing, size = await call('skill_list', {'query': query})
                skill = next(s for s in listing['skills'] if s['name'] == name)
                ids[name] = skill['id']
                instructions, _ = await call('skill_read', {'skill_id': skill['id']})
                assert instructions['content']
                receipt['cases'].append({'case': 'discovery_instruction_read', 'name': name, 'skill': skill['id'],
                                         'metadata_identity': skill['metadata_identity'], 'identity': instructions['identity']})
                save()
            support, _ = await call('skill_read', {'skill_id': ids['cuda'], 'resource': 'references/architectures/volta/v100_atlas/mechanisms/M14-register-resident-local-state-machines.md'})
            assert support['content']
            repeated, _ = await call('skill_read', {'skill_id': ids['cuda'], 'resource': support['resource'], 'expected_identity': support['identity']})
            assert repeated['identity'] == support['identity']
            receipt['cases'].append({'case': 'atlas_M14_descriptive_read', 'identity': support['identity'], 'resource': support['resource']})
            full, size = await call('skill_read', {'skill_id': ids['cuda'], 'resource': 'references/architectures/volta/v100_atlas/V100_ATLAS_FULL.md', 'line_start': 1, 'line_end': 16})
            assert full['content']
            receipt['cases'].append({'case': 'excluded_FULL_explicit_bounded_read', 'identity': full['identity'], 'response_bytes': size})
            indexed, size = await call('skill_context', {'query': 'authoring', 'skill': ids['cpp-context-compiler']})
            assert indexed['status'] == 'available' and indexed['route'] == 'indexed' and indexed['evidence']
            receipt['cases'].append({'case': 'cpp_indexed', **{k: indexed[k] for k in ('status', 'route', 'route_reason', 'coverage', 'corpus_identity')}, 'response_bytes': size})
            save()
            receipt['machine_calls'] += 1
            save()
            result, size = await call('skill_context', {'query': 'overview V100 atlas prerequisites NVLink', 'skill': 'auto', 'budget_bytes': 16384})
            assert result['route'] == 'machine' and result['evidence']
            receipt['cases'].append({'case': 'atlas_machine_auto', **{k: result.get(k) for k in ('status', 'route', 'reason', 'route_reason', 'coverage', 'corpus_identity')},
                                     'response_bytes': size, 'continuation': bool(result.get('continuation_cursor')),
                                     'evidence_ids': [e['id'] for e in result['evidence']],
                                     'evidence_metadata': [{k:e.get(k) for k in ('id', 'resource', 'semantic_ids', 'role', 'identity')} for e in result['evidence']],
                                     'summaries': [{k:s.get(k) for k in ('status', 'reason', 'evidence_ids', 'uncertainty')} for s in result.get('summaries', [])],
                                     'summary_identity': hashlib.sha256(json.dumps(result.get('summaries'), sort_keys=True).encode()).hexdigest()})
            receipt['live_machine_qualified'] = result['status'] == 'available' and bool(result.get('summaries')) and all(s['status'] in ('available', 'completed', 'ok') for s in result['summaries'])
            save()
            assert receipt['live_machine_qualified'], result.get('reason', 'live_machine_unqualified')
            assert result['coverage']['analyzed_sections'] > 0
            assert all(s.get('summary') and s.get('evidence_ids') for s in result['summaries'])
            assert all('FULL' not in e['resource'] for e in result['evidence'])
            atlas = [e for e in result['evidence'] if '/v100_atlas/' in e['resource']]
            assert atlas and any(e.get('semantic_ids') and e.get('role') for e in atlas)
            assert any('-' in Path(e['resource']).stem for e in atlas)
    receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
    save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:8767')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(qualify(args.base, args.output))
