"""Bounded reproduction for missing skill-resource cache dependencies."""
import hashlib
from types import SimpleNamespace

import pytest

from project_control.as1_surface import InquiryFreshness
from project_control.as1_packets import SQLitePacketStore
from project_control.as1_skill import SkillService


@pytest.mark.xfail(strict=True, reason='InquiryFreshness does not track exact failed skill-resource reads')
def test_appearing_unresolved_skill_resource_invalidates_partial_cache(tmp_path):
    root = tmp_path / 'skills'
    skill_root = root / 'fixture'
    skill_root.mkdir(parents=True)
    entry = skill_root / 'SKILL.md'
    selected = skill_root / 'START_HERE.md'
    missing = skill_root / 'reference' / 'R06.md'
    entry.write_text('Follow START_HERE.md; consult reference/R06.md for the detailed route.\n')
    selected.write_text('START_HERE keeps the cached partial answer grounded.\n')

    store = SQLitePacketStore(tmp_path / 'packets')
    jobs = SimpleNamespace(
        packets=store,
        worker_factory=SimpleNamespace(skills={
            'fixture': {'name': 'fixture', 'root': str(skill_root)},
        }),
    )
    skills = SkillService(jobs, skills_root=root, discovery_skill='fixture')
    composition = SimpleNamespace(
        store=store,
        jobs=jobs,
        skills=skills,
        host=SimpleNamespace(projects=frozenset()),
        control=SimpleNamespace(),
    )
    fresh = InquiryFreshness(composition)
    scope = {'principal': 'alice', 'profile': 'observer'}

    def direct_read(path):
        content = path.read_bytes()
        return store.create(tool='command', access_scope=scope, payload={
            'status': 'completed',
            'exit_code': 0,
            'source_reads': [{
                'method': 'direct_cat',
                'path': str(path),
                'content_sha256': hashlib.sha256(content).hexdigest(),
            }],
        })

    entry_packet = direct_read(entry)
    selected_packet = direct_read(selected)
    # Match the installed ReadOnlyCommandRunner packet for a missing direct cat:
    # failed with exit_code 1; its public call carries exact argv/cwd, and no
    # source_reads field is emitted. The saved real inquiry lacks this raw packet.
    missing_packet = store.create(tool='command', access_scope=scope, payload={
        'status': 'failed',
        'exit_code': 1,
        'stderr': 'cat: reference/R06.md: No such file or directory',
    })
    selections = [
        {'skill': 'fixture', 'resource': 'SKILL.md',
         'content_sha256': hashlib.sha256(entry.read_bytes()).hexdigest()},
        {'skill': 'fixture', 'resource': 'START_HERE.md',
         'content_sha256': hashlib.sha256(selected.read_bytes()).hexdigest()},
    ]
    result = store.create(tool='skill', access_scope=scope, payload={
        'status': 'partial',
        'unresolved': ['reference/R06.md was not found or could not be read.'],
        'skill_selection': {'selections': selections},
    })
    citations = [{'text': 'The selected guide contains the verified route.',
                  'evidence_packets': [selected_packet.packet_id]}]
    observation_ids = [entry_packet.packet_id, selected_packet.packet_id, missing_packet.packet_id]
    jobs.lookup = lambda _job_id, access_scope: {
        'observations': [
            {'packet_id': entry_packet.packet_id},
            {'packet_id': selected_packet.packet_id},
            {'packet_id': missing_packet.packet_id, 'public_tool_call': {
                'tool': 'command',
                'arguments': {'argv': ['cat', str(missing)], 'cwd': str(skill_root)},
            }},
        ],
    }
    job = {
        'job_id': 'cached-partial',
        'mode': 'skill',
        'scope': scope,
        'findings': citations,
        'hints': [],
        'evidence_packets': observation_ids,
        'result_packet': result.packet_id,
    }

    before = fresh(job)
    assert before['fresh'] is True
    assert not missing.exists()

    missing.parent.mkdir()
    missing.write_text('New detailed route, now available.\n')
    after = fresh(job)

    assert after['fresh'] is False
    assert after['changed_sources']
