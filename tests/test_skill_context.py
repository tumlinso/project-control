import hashlib
import json
from pathlib import Path

import pytest

from project_control.skill_context import SkillContext
from project_control.skills import SkillRegistry
from project_control.source_index import SourceLexicalIndex


def corpus(tmp_path, monkeypatch, files=1, lines=1):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    root = tmp_path / 'skills'
    directory = root / 'sample'
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text('---\nname: sample\ndescription: widget reference\n---\n# Instructions\nAdvisory widget knowledge.\n')
    for n in range(files):
        (directory / f'ref-{n}.md').write_text('# Widget\n' + ('widget evidence '+str(n)+'\n') * lines)
    registry = SkillRegistry(root)
    return registry, registry.list()['skills'][0]['id'], directory


def test_direct_and_shared_index(tmp_path, monkeypatch):
    registry, skill, _ = corpus(tmp_path, monkeypatch)
    result = SkillContext(registry).context('widget', skill)
    assert result['route'] == 'direct'
    assert result['evidence']
    assert result['authority'] == 'advisory_instruction'
    assert str(tmp_path) not in json.dumps(result)
    assert len(json.dumps(result).encode()) < 16384
    assert list((tmp_path / 'cache').rglob('index.sqlite3'))


def test_medium_uses_index(tmp_path, monkeypatch):
    registry, skill, _ = corpus(tmp_path, monkeypatch, files=5)
    result = SkillContext(registry).context('widget', skill)
    assert result['route'] == 'indexed'
    assert len({row['resource'] for row in result['evidence']}) >= 3


def test_large_defaults_machine_immutable_bounded(tmp_path, monkeypatch):
    registry, skill, _ = corpus(tmp_path, monkeypatch, files=12, lines=100)
    packets = []
    def analyze(packet):
        packets.append(packet)
        assert isinstance(packet['source_identity'], dict)
        assert len(json.dumps(packet).encode()) < 65536
        assert str(tmp_path) not in json.dumps(packet)
        assert len(packet['evidence']) <= 64
        return {'status': 'available', 'summary': 'Bounded synthesis', 'evidence_ids': [packet['evidence'][0]['id']]}
    result = SkillContext(registry, analyze).context('overview widget', skill)
    assert result['route'] == 'machine'
    assert packets
    assert len(packets) <= 4
    assert result['summaries'][0]['summary'] == 'Bounded synthesis'
    assert len(json.dumps(result).encode()) < 16384
    assert result['evidence'][0]['content'] != packets[0]['evidence'][0]['content'] or len(packets[0]['evidence'][0]['content']) <= 320


def test_unavailable_and_forged_citations(tmp_path, monkeypatch):
    registry, skill, _ = corpus(tmp_path, monkeypatch, files=10)
    for callback in (None, lambda p: {'status': 'available', 'summary': 'bad', 'evidence_ids': ['outside']}):
        result = SkillContext(registry, callback).context('overview widget', skill)
        assert result['route'] == 'machine'
        assert result['status'] == 'unavailable'
        assert result['evidence']
        assert not result['summaries'][0]['evidence_ids']


def test_continuation_invalidated(tmp_path, monkeypatch):
    registry, skill, directory = corpus(tmp_path, monkeypatch, files=3, lines=80)
    context = SkillContext(registry)
    result = context.context('widget', skill, budget_bytes=2048)
    cursor = result['continuation_cursor']
    assert cursor
    path = directory / 'ref-0.md'
    path.write_text(path.read_text() + 'fresh change\n')
    with pytest.raises(ValueError, match='stale'):
        context.context('widget', skill, budget_bytes=2048, continuation_cursor=cursor)


def test_semantic_manifest_expansion_and_hash(tmp_path, monkeypatch):
    registry, skill, directory = corpus(tmp_path, monkeypatch, files=2)
    (directory / 'ref-1.md').write_text('# Prerequisite\nRelevant dependency without query term\n')
    manifest = {'schema_version': 1, 'resources': [{'id': 'a', 'path': 'ref-0.md', 'title': 'widget'}, {'id': 'b', 'path': 'ref-1.md'}], 'relationships': [{'from': 'a', 'to': 'b', 'type': 'prerequisite'}, {'from': 'b', 'to': 'a', 'type': 'prerequisite'}, {'from': 'b', 'to': 'missing', 'type': 'evidence'}]}
    (directory / '.project-control-corpus.json').write_text(json.dumps(manifest))
    result = SkillContext(registry).context('widget', skill)
    assert any(row['resource'] == 'ref-1.md' for row in result['evidence'])
    assert result['relationships'] == [{'from': 'a', 'to': 'b', 'type': 'prerequisite'}]
    assert result['unresolved_relationships'][0]['to'] == 'missing'
    manifest['resources'][0]['sha256'] = 'bad'
    (directory / '.project-control-corpus.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='stale'):
        SkillContext(registry).context('widget', skill)


def test_no_code_execution_and_two_corpora(tmp_path, monkeypatch):
    registry, skill, directory = corpus(tmp_path, monkeypatch)
    (directory / 'evil.py').write_text('raise RuntimeError("must never execute")')
    other = directory.parent / 'other'
    other.mkdir()
    (other / 'SKILL.md').write_text('---\nname: other\ndescription: molecule reference\n---\n# Molecule\nmolecule evidence\n')
    context = SkillContext(registry)
    first = context.context('widget', skill)
    second = context.context('molecule', 'auto')
    assert first['skill'] != second['skill']
    assert all(row['resource'] != 'evil.py' for row in first['evidence'])


def test_build_rows_preserves_source_api(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))
    index = SourceLexicalIndex('fixture', 'identity')
    assert index.build_rows([('r.md', 1, 'needle widget')]) == {'files': 1, 'lines': 1}
    assert index.is_complete()
    assert index.search('needle')[0].path == 'r.md'
    index.build_rows([('new.md', 2, 'fresh widget')])
    assert not index.search('needle')
    assert index.search('fresh')[0].line == 2


def test_auto_natural_language_and_resource_names(tmp_path, monkeypatch):
    registry, skill, directory = corpus(tmp_path, monkeypatch)
    (directory / 'V100_NVLink_atlas.md').write_text('# NVLink\nNVLink atlas widget prerequisites\n')
    context = SkillContext(registry)
    assert context.context('How should I review widget?', 'auto')['skill'] == skill
    assert context.context('overview widget', 'auto')['skill'] == skill
    assert context.context('V100 atlas NVLink prerequisites', 'auto')['skill'] == skill


def test_unavailable_can_retry_same_packet(tmp_path, monkeypatch):
    registry, skill, _ = corpus(tmp_path, monkeypatch, files=10)
    context = SkillContext(registry)
    first = context.context('overview widget', skill)
    assert first['status'] == 'unavailable'
    assert first['continuation_cursor']
    assert first['coverage']['analyzed_sections'] == 0
    def available(packet):
        return {'status': 'available', 'summary': 'Recovered', 'evidence_ids': [packet['evidence'][0]['id']]}
    context.analyze_packet = available
    recovered = context.context('overview widget', skill, continuation_cursor=first['continuation_cursor'])
    assert recovered['status'] == 'available'
    assert recovered['coverage']['analyzed_sections'] == first['coverage']['total_sections']


def test_semantic_need_spans_expand_only_matched_need(tmp_path, monkeypatch):
    registry, skill, directory = corpus(tmp_path, monkeypatch, files=2)
    (directory / 'needs.md').write_text('# Needs\nfirst irrelevant need\nlater tensor need\n')
    (directory / 'ref-0.md').write_text('# Wrong\nfirst dependency\n')
    (directory / 'ref-1.md').write_text('# Right\nsecond dependency\n')
    manifest = {'schema_version': 1, 'resources': [
        {'id': 'first', 'path': 'needs.md', 'title': 'first', 'line_start': 2, 'line_end': 2},
        {'id': 'later', 'path': 'needs.md', 'title': 'tensor', 'line_start': 3, 'line_end': 3},
        {'id': 'wrong', 'path': 'ref-0.md'}, {'id': 'right', 'path': 'ref-1.md'}],
        'relationships': [{'from': 'first', 'to': 'wrong', 'type': 'prerequisite'}, {'from': 'later', 'to': 'right', 'type': 'prerequisite'}]}
    (directory / '.project-control-corpus.json').write_text(json.dumps(manifest))
    result = SkillContext(registry).context('tensor', skill)
    assert result['relationships'] == [{'from': 'later', 'to': 'right', 'type': 'prerequisite'}]
    assert any(row['resource'] == 'ref-1.md' for row in result['evidence'])
    assert all(row['resource'] != 'ref-0.md' for row in result['evidence'])
    assert any(row['resource'] == 'needs.md' and row['line_start'] == 3 for row in result['evidence'])


def test_unavailable_backend_race_rejects_old_fallback(tmp_path, monkeypatch):
    registry, skill, directory = corpus(tmp_path, monkeypatch, files=10)
    def race(packet):
        resource = directory / packet['evidence'][0]['resource']
        resource.write_text(resource.read_text() + '\nchanged during analysis\n')
        return {'status': 'unavailable'}
    with pytest.raises(ValueError, match='changed_during_context'):
        SkillContext(registry, race).context('overview widget', skill)


def test_auto_skips_unavailable_candidate_inventory(tmp_path, monkeypatch):
    registry, skill, _ = corpus(tmp_path, monkeypatch)
    original = registry.list
    original_resources = registry.resources
    def listed(*args, **kwargs):
        result = original(*args, **kwargs)
        result['skills'].insert(0, {'id': 'broken', 'name': 'widget', 'description': 'widget'})
        return result
    def resources(identity):
        if identity == 'broken':
            raise ValueError('inventory limit')
        return original_resources(identity)
    monkeypatch.setattr(registry, 'list', listed)
    monkeypatch.setattr(registry, 'resources', resources)
    result = SkillContext(registry).context('widget', 'auto')
    assert result['skill'] == skill
    assert result['selection_warnings'] == [{'skill': 'broken', 'reason': 'inventory_unavailable'}]


def test_machine_unavailable_reason_is_bounded_redacted(tmp_path, monkeypatch):
    registry, skill, _ = corpus(tmp_path, monkeypatch, files=10)
    def unavailable(packet):
        return {'status': 'unavailable', 'reason': 'observer_analysis_busy /home/private/runtime token=secret123'}
    result = SkillContext(registry, unavailable).context('overview widget', skill)
    assert result['status'] == 'unavailable'
    assert 'observer_analysis_busy' in result['reason']
    assert result['summaries'][0]['reason'] == result['reason']
    assert '/home/private/runtime' not in result['reason']
    assert 'secret123' not in result['reason']
    assert len(result['reason']) <= 500
    assert result['continuation_cursor']
