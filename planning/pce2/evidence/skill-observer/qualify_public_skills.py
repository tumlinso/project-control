"""Repeatable public skill-tool qualification; --live-machine permits one packet.

Run from the source checkout with its configured trusted runtime on PYTHONPATH.
No public call accepts a filesystem root. Default mode uses deterministic packet
fixtures; it does not construct a machine backend. Receipt contains no excerpts
or absolute personal paths. --output writes only the requested receipt.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import time
import tempfile
from pathlib import Path

from project_control.app import create_mcp
from project_control.config import ProjectControlConfig, configured_observer_skills_root, load_config


def decode(result):
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple):
        for part in result:
            if isinstance(part, dict):
                return part
        result = result[0]
    for block in result:
        if getattr(block, 'type', None) == 'text':
            return json.loads(block.text)
    raise AssertionError('public call did not return JSON')


async def qualify(live):
    started = time.monotonic()
    config = load_config()
    root = configured_observer_skills_root(config)
    mcp = create_mcp(config)
    broker = mcp._project_control_skill_context
    registry = mcp._project_control_observer_analysis_registry
    actual_analyze = registry.analyze_packet
    packets = []
    live_calls = 0

    def analyze(packet):
        nonlocal live_calls
        encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True).encode()
        assert len(encoded) < 65536
        assert str(root) not in encoded.decode()
        assert not ({'tools', 'root', 'repo_root', 'workflow_handle', 'capabilities'} & set(packet))
        record = {'packet_sha256': hashlib.sha256(encoded).hexdigest(),
                  'bytes': len(encoded), 'evidence_count': len(packet['evidence']),
                  'source_identity': packet['source_identity'],
                  'mode': 'live' if live and live_calls == 0 else 'deterministic_fixture'}
        if live and live_calls == 0:
            live_calls += 1
            response = actual_analyze(packet)
        elif live:
            record['mode'] = 'explicit_one_packet_limit'
            response = {'status': 'unavailable', 'reason': 'qualification_one_live_packet_limit', 'evidence_ids': []}
        else:
            response = {'status': 'available', 'summary': 'Deterministic transport fixture; no inference claim.',
                        'evidence_ids': [packet['evidence'][0]['id']]}
        record['provider_result'] = {k: response.get(k) for k in ('status', 'provider', 'reason', 'fallback', 'evidence_ids', 'mutation_authority') if k in response}
        packets.append(record)
        return response

    broker.analyze_packet = analyze

    async def call(name, args):
        result = decode(await mcp.call_tool(name, args))
        assert str(root) not in json.dumps(result)
        assert result.get('origin') == 'agent_skill'
        assert result.get('authority') == 'advisory_instruction'
        assert result.get('mutation_authority') is False
        return result

    tools = {t.name: t for t in await mcp.list_tools()}
    for name in ('skill_list', 'skill_read', 'skill_context'):
        assert tools[name].annotations.readOnlyHint
        assert not ({'root', 'project', 'backend', 'repository'} & set(tools[name].inputSchema['properties']))
    receipt = {'public_api': 'FastMCP.call_tool', 'machine_mode': 'one_live_packet' if live else 'deterministic_fixture', 'cases': []}
    for query, wanted in [('CUDA', 'cuda'), ('C++', 'cpp-context-compiler')]:
        listed = await call('skill_list', {'query': query})
        skill = next(row for row in listed['skills'] if row['name'] == wanted)
        instructions = await call('skill_read', {'skill_id': skill['id']})
        assert instructions.get('content')
        receipt['cases'].append({'case': 'discovery_instruction_read', 'skill': skill['id'], 'name': skill['name'],
                                  'metadata_identity': skill['metadata_identity'], 'instruction_identity': instructions.get('identity')})
    cuda = (await call('skill_list', {'query': 'CUDA'}))['skills']
    cuda_id = next(s['id'] for s in cuda if s['name'] == 'cuda')
    supporting = await call('skill_read', {'skill_id': cuda_id, 'resource': 'references/architectures/volta/v100_atlas/README.md'})
    assert supporting.get('content')
    receipt['cases'].append({'case': 'atlas_supporting_read', 'skill': cuda_id, 'identity': supporting.get('identity')})
    # Atlas machine query is last, ensuring the live allowance goes to this case.
    with tempfile.TemporaryDirectory(prefix='public-skill-fixture-') as raw:
        fixture = Path(raw)
        directory = fixture / 'cuda-review'
        (directory / 'references').mkdir(parents=True)
        (directory / 'SKILL.md').write_text('---\nname: cuda-review\ndescription: tiny Volta fixture\n---\n# Volta\nvolta fixture instructions\n')
        (directory / 'references' / 'volta.md').write_text('# Volta\nvolta bounded evidence fixture\n')
        tiny = create_mcp(ProjectControlConfig(observer_skills_root=fixture))
        listing = decode(await tiny.call_tool('skill_list', {'query': 'Volta'}))
        identity = listing['skills'][0]['id']
        for resource in ('SKILL.md', 'references/volta.md'):
            read = decode(await tiny.call_tool('skill_read', {'skill_id': identity, 'resource': resource}))
            assert read['content']
            assert str(fixture) not in json.dumps(read)
        direct = decode(await tiny.call_tool('skill_context', {'query': 'volta', 'skill': identity}))
        assert direct['route'] == 'direct' and direct['evidence']
        receipt['cases'].append({'case': 'small_public_direct', 'fixture': True, 'skill': identity, 'route': direct['route'], 'coverage': direct['coverage']})
    queries = [('cuda_indexed', 'FP8', 'cuda'),
               ('cpp_indexed', 'authoring', 'cpp-context-compiler'),
               ('atlas_machine_auto', 'overview V100 atlas prerequisites NVLink', 'auto')]
    for case, query, name in queries:
        skill_id = 'auto' if name == 'auto' else next(row['skill'] for row in receipt['cases'] if row.get('name') == name)
        if case != 'atlas_machine_auto':
            broker.analyze_packet = lambda packet: {'status': 'unavailable', 'evidence_ids': [], 'reason': 'qualification_live_reserved_for_atlas'}
        else:
            broker.analyze_packet = analyze
        result = await call('skill_context', {'query': query, 'skill': skill_id})
        assert result.get('evidence'), (case, result.get('status'))
        assert result.get('route') == ('machine' if case == 'atlas_machine_auto' else 'indexed')
        receipt['cases'].append({'case': case, 'query': query, 'skill': result.get('skill'),
                                  'status': result.get('status'), 'route': result.get('route'),
                                  'route_reason': result.get('route_reason'), 'corpus_identity': result.get('corpus_identity'),
                                  'coverage': result.get('coverage'), 'relationships': len(result.get('relationships', [])),
                                  'continuation': bool(result.get('continuation_cursor')),
                                  'response_bytes': len(json.dumps(result).encode()),
                                  'evidence_ids': [row['id'] for row in result.get('evidence', [])]})
    receipt['packets'] = packets
    receipt['live_calls'] = live_calls
    receipt['elapsed_seconds'] = round(time.monotonic() - started, 3)
    receipt['qualification_limits'] = ['One live packet maximum; subsequent packets explicitly unavailable.' if live else 'Deterministic backend fixture only; no live inference claim.', 'No corpus completeness or scientific validation claim.']
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live-machine', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    receipt = asyncio.run(qualify(args.live_machine))
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end='')
