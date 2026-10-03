from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

from project_control.skill_ingestion import ingest_skill_archive
from project_control.skills import SkillError, SkillRegistry


def archive(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return out.getvalue()


def fixture(tmp_path, raw):
    root = tmp_path / 'skills'
    skill = root / 'cuda-review'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: CUDA review\ndescription: Review Volta mechanisms\n---\nReview instructions.\n')
    (skill / 'atlas.zip').write_bytes(raw)
    registry = SkillRegistry(root)
    return registry, registry._discover()[0][0].id, skill


def valid_entries():
    document = b'# M01 Packed logic\nFaithfully preserved text.\n'
    need = b'| Need | Mechanisms | Compositions | Required depth | Tests | Decision cue |\n|---|---|---|---|---|---|\n| Sparse support | M01 | C99 | M01 | E99 | Try packed logic. |\n'
    prototype = b'from pathlib import Path\nPath("EXECUTED").touch()\n'
    files = {'mechanisms/M01.md': document, 'NEED_INDEX.md': need, 'tools/router.py': prototype}
    manifest = {'documents': [{'id': 'M01', 'path': 'mechanisms/M01.md', 'title': 'Packed logic', 'summary': 'Review logic', 'tags': ['logic'], 'requires': [], 'sources': [], 'sha256': hashlib.sha256(document).hexdigest()}], 'sources': []}
    files['manifest.json'] = json.dumps(manifest).encode()
    files['SHA256SUMS'] = '\n'.join(hashlib.sha256(data).hexdigest() + '  ' + name for name, data in files.items()).encode()
    return [('v100_atlas/' + n, b) for n, b in files.items()]


def test_faithful_semantics_dryrun_idempotent_and_no_execution(tmp_path):
    raw = archive(valid_entries())
    registry, identity, skill = fixture(tmp_path, raw)
    result = ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas')
    assert result['status'] == 'validated'
    assert not (skill / 'references').exists()
    result = ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas', apply=True)
    assert result['status'] == 'ingested'
    assert (skill / 'atlas.zip').read_bytes() == raw
    for name, data in valid_entries():
        path = name.removeprefix('v100_atlas/')
        if path.endswith('.py'):
            assert not (skill / 'references/atlas' / path).exists()
        else:
            assert (skill / 'references/atlas' / path).read_bytes() == data
    corpus = json.loads((skill / 'references/atlas/.project-control-corpus.json').read_text())
    assert corpus['source']['sha256'] == hashlib.sha256(raw).hexdigest()
    assert corpus['resources'][0]['id'] == 'M01'
    assert any(r['line_start'] == 3 for r in corpus['resources'] if r['id'].startswith('need-'))
    assert any(e['type'] == 'mechanism' and e['to'] == 'M01' for e in corpus['relationships'])
    assert any(e['to'] == 'C99' for e in corpus['unresolved_relationships'])
    assert not list(tmp_path.rglob('EXECUTED'))
    assert ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas', apply=True)['status'] == 'unchanged'
    (skill / 'references/atlas/mechanisms/M01.md').write_text('modified')
    with pytest.raises(SkillError) as error:
        ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas', apply=True)
    assert error.value.code == 'ingestion_destination_conflict'


@pytest.mark.parametrize('path', ['../escape.md', '/escape.md', 'v100_atlas/../escape.md', 'v100_atlas\\escape.md', 'C:/escape.md', './escape.md'])
def test_archive_escape(tmp_path, path):
    registry, identity, _ = fixture(tmp_path, archive([(path, 'bad')]))
    with pytest.raises(SkillError):
        ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas', apply=True)


@pytest.mark.parametrize('destination', ['../escape', '/absolute', 'references/../escape', 'references\\escape'])
def test_destination_escape(tmp_path, destination):
    registry, identity, _ = fixture(tmp_path, archive(valid_entries()))
    with pytest.raises(SkillError):
        ingest_skill_archive(registry, identity, 'atlas.zip', destination, apply=True)


def test_symlink_archive_and_destination(tmp_path):
    member = zipfile.ZipInfo('escape.md')
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    registry, identity, skill = fixture(tmp_path, archive([(member, '../../escape')]))
    with pytest.raises(SkillError):
        ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas', apply=True)
    (skill / 'atlas.zip').write_bytes(archive(valid_entries()))
    outside = tmp_path / 'outside'
    outside.mkdir()
    (skill / 'references').symlink_to(outside, target_is_directory=True)
    with pytest.raises(SkillError):
        ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas', apply=True)
    assert not list(outside.iterdir())


@pytest.mark.parametrize('entries', [ [('x.md', 'first'), ('x.md', 'second')], [('x.md', 'first'), ('x.md/y.md', 'second')], [('x.bin', b'bad')], [('x.md', b'\xff')], [('x.md', b'\x00')], [('x.md', 'a'), ('SHA256SUMS', '0' * 64 + '  x.md')], [('manifest.json', '{malformed')], [('x.md', b'a' * (2 * 1024 * 1024 + 1))] ])
def test_malformed_limits_checksums_collisions(tmp_path, entries):
    registry, identity, _ = fixture(tmp_path, archive(entries))
    with pytest.raises(SkillError):
        ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas', apply=True)


def test_changed_source_before_publish_fails_closed(tmp_path, monkeypatch):
    registry, identity, skill = fixture(tmp_path, archive(valid_entries()))
    original = registry.read_bytes
    count = 0
    def read(*args, **kwargs):
        nonlocal count
        count += 1
        data, digest = original(*args, **kwargs)
        return data, ('f' * 64 if count == 2 else digest)
    monkeypatch.setattr(registry, 'read_bytes', read)
    with pytest.raises(SkillError) as error:
        ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas', apply=True)
    assert error.value.code == 'ingestion_source_changed'
    assert not (skill / 'references/atlas').exists()
    assert not list(skill.rglob('.pc-ingest-*'))


def test_cli_ingest_parser():
    from project_control.cli import _parser
    args = _parser().parse_args(['admin', 'ingest-skill-archive', '--skill', 'opaque', '--resource', 'atlas.zip', '--destination', 'references/atlas'])
    assert args.skill == 'opaque'
    assert not args.apply


def test_member_count_and_expansion_limits(tmp_path, monkeypatch):
    import project_control.skill_ingestion as ingestion
    registry, identity, _ = fixture(tmp_path, archive(valid_entries()))
    monkeypatch.setattr(ingestion, 'MAX_MEMBERS', 2)
    with pytest.raises(SkillError) as error:
        ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas')
    assert error.value.code == 'archive_member_limit'
    monkeypatch.setattr(ingestion, 'MAX_MEMBERS', 2048)
    monkeypatch.setattr(ingestion, 'MAX_MEMBER_BYTES', 10)
    with pytest.raises(SkillError) as error:
        ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas')
    assert error.value.code == 'archive_size_limit'


def test_incomplete_checksums_and_manifest_hash(tmp_path):
    entries = [('a.md', b'a'), ('b.md', b'b'), ('SHA256SUMS', hashlib.sha256(b'a').hexdigest() + '  a.md')]
    registry, identity, skill = fixture(tmp_path, archive(entries))
    with pytest.raises(SkillError) as error:
        ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas')
    assert error.value.code == 'archive_checksum_coverage'
    entries = [('a.md', b'a'), ('manifest.json', json.dumps({'documents': [{'id': 'A01', 'path': 'a.md', 'sha256': '0' * 64}]}))]
    (skill / 'atlas.zip').write_bytes(archive(entries))
    with pytest.raises(SkillError) as error:
        ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas')
    assert error.value.code == 'archive_checksum_mismatch'


def test_destination_swap_before_publication(tmp_path, monkeypatch):
    registry, identity, skill = fixture(tmp_path, archive(valid_entries()))
    original = registry.read_bytes
    count = 0
    def read(*args, **kwargs):
        nonlocal count
        count += 1
        result = original(*args, **kwargs)
        if count == 2:
            (skill / 'references').rename(skill / 'moved')
            (skill / 'references').mkdir()
        return result
    monkeypatch.setattr(registry, 'read_bytes', read)
    with pytest.raises(SkillError) as error:
        ingest_skill_archive(registry, identity, 'atlas.zip', 'references/atlas', apply=True)
    assert error.value.code == 'ingestion_destination_changed'
    assert not (skill / 'references/atlas').exists()
    assert not (skill / 'moved/atlas').exists()
    assert not list(skill.rglob('.pc-ingest-*'))


def test_source_destination_overlap(tmp_path):
    registry, identity, _ = fixture(tmp_path, archive(valid_entries()))
    with pytest.raises(SkillError) as error:
        ingest_skill_archive(registry, identity, 'atlas.zip', 'atlas.zip', apply=True)
    assert error.value.code == 'ingestion_source_overlap'


def test_cli_ingest_uses_only_configured_root(tmp_path, monkeypatch, capsys):
    from project_control.cli import main
    from project_control.config import ProjectControlConfig
    registry, identity, skill = fixture(tmp_path, archive(valid_entries()))
    monkeypatch.delenv('PROJECT_CONTROL_OBSERVER_SKILLS_ROOT', raising=False)
    monkeypatch.setattr('project_control.cli.load_config', lambda: ProjectControlConfig(observer_skills_root=registry.root))
    assert main(['admin', 'ingest-skill-archive', '--skill', identity, '--resource', 'atlas.zip', '--destination', 'references/atlas']) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['status'] == 'validated'
    assert str(tmp_path) not in json.dumps(receipt)
    assert not (skill / 'references').exists()
    assert main(['admin', 'ingest-skill-archive', '--skill', identity, '--resource', 'atlas.zip', '--destination', 'references/atlas', '--apply']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'ingested'
