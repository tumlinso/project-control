#!/usr/bin/env python3
"""Stage approved path/metadata migration; never mutate the live atlas."""
import argparse
import copy
import json
import re
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from plan_atlas_migration import plan, sha

PACKAGE = Path(__file__).resolve().parent
PREFIX = 'references/architectures/volta/v100_atlas/'


def dump(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def inventory(root):
    return {str(p.relative_to(root)): sha(p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file()}


def main():
    proposal = json.loads((PACKAGE / 'atlas-migration-dry-run.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-source', type=Path, help='Preserved original backup after publication')
    args = parser.parse_args()
    source = args.original_source or Path(proposal['source'])
    assert inventory(source) == proposal['source_pins'], 'Live source drift; replan and seek root direction'
    if not args.original_source:
        assert plan(source) == proposal, 'Proposal drift'
    else:
        assert sha(Path(proposal['source_zip']['path']).read_bytes()) == proposal['source_zip']['sha256'], 'ZIP identity drift'
    stage = PACKAGE / 'staged-atlas'
    assert not stage.exists(), 'Refusing to overwrite an existing stage'
    shutil.copytree(source, stage)
    renames = {r['old_path']: r['new_path'] for r in proposal['canonical_renames']}
    by_id = {r['canonical_id']: r for r in proposal['canonical_renames']}
    names = {PurePosixPath(a).name: PurePosixPath(b).name for a, b in renames.items()}
    pattern = re.compile(r'(?<![A-Za-z0-9_.-])(' + '|'.join(map(re.escape, names)) + r')(?![A-Za-z0-9_.-])')
    body_audit = []
    structured = {'manifest.json', '.project-control-corpus.json', 'SHA256SUMS'}
    for old, expected in proposal['source_pins'].items():
        if old in structured:
            continue
        original = (source / old).read_bytes()
        changed = original
        if old != 'V100_ATLAS_FULL.md':
            try:
                changed = pattern.sub(lambda m: names[m[1]], original.decode()).encode()
            except UnicodeDecodeError:
                pass
        dest = renames.get(old, old)
        if old != dest:
            (stage / old).unlink()
        (stage / dest).write_bytes(changed)
        inverse_names = {v: k for k, v in names.items()}
        inverse = re.compile(r'(?<![A-Za-z0-9_.-])(' + '|'.join(map(re.escape, inverse_names)) + r')(?![A-Za-z0-9_.-])')
        normalized = changed if changed == original else inverse.sub(lambda m: inverse_names[m[1]], changed.decode()).encode()
        assert normalized == original, old
        body_audit.append({'old_path': old, 'new_path': dest, 'original_sha256': expected,
                           'staged_sha256': sha(changed), 'normalized_sha256': sha(normalized)})
    manifest = json.loads((source / 'manifest.json').read_text())
    original_manifest = copy.deepcopy(manifest)
    for row in manifest['documents'] + manifest['sources']:
        row['path'] = renames.get(row['path'], row['path'])
        if row['id'] in by_id:
            row.update(role=by_id[row['id']]['role'], index_excluded=False, lineage=[])
        row['bytes'] = (stage / row['path']).stat().st_size
        row['sha256'] = sha((stage / row['path']).read_bytes())
    dump(stage / 'manifest.json', manifest)
    # Verify every field except explicit migration fields remains exact.
    for before, after in zip(original_manifest['documents'] + original_manifest['sources'], manifest['documents'] + manifest['sources']):
        assert {k:v for k,v in before.items() if k not in {'path','bytes','sha256','role','index_excluded','lineage'}} == {k:v for k,v in after.items() if k not in {'path','bytes','sha256','role','index_excluded','lineage'}}
    checksum_rows = []
    omitted = []
    for line in (source / 'SHA256SUMS').read_text().splitlines():
        _, old = line.split('  ', 1)
        target = renames.get(old, old)
        if not (stage / target).is_file():
            omitted.append(old)
            continue
        checksum_rows.append(f'{sha((stage / target).read_bytes())}  {target}')
    (stage / 'SHA256SUMS').write_text('\n'.join(checksum_rows) + '\n')
    corpus = json.loads((source / '.project-control-corpus.json').read_text())
    original_corpus = copy.deepcopy(corpus)
    for row in corpus['resources']:
        old = row['path'].removeprefix(PREFIX)
        assert row['path'].startswith(PREFIX), row['path']
        target = renames.get(old, old)
        row['path'] = PREFIX + target
        row['sha256'] = sha((stage / target).read_bytes())
        metadata = row.setdefault('metadata', {})
        if 'bytes' in metadata:
            metadata['bytes'] = (stage / target).stat().st_size
        identifier = row['id']
        if identifier in by_id:
            role = by_id[identifier]['role']
        elif identifier.startswith('need-') or target == 'NEED_INDEX.md':
            role = 'semantic_index'
        elif target == 'V100_ATLAS_FULL.md':
            role = 'aggregate_view'
        elif target in {'manifest.json', '.project-control-corpus.json', 'SHA256SUMS', 'ledger/VALIDATION.json', 'ledger/CPU_TEST_RESULTS.json'}:
            role = 'evidence'
        elif target.startswith('experiments/'):
            role = 'experiment'
        elif target in {'START_HERE.md', 'FIELD_GUIDE.md', 'README.md', 'benchmarks/README.md', 'sources/SOURCES.md'}:
            role = 'navigation'
        else:
            role = 'deep_reference'
        excluded = target in {'V100_ATLAS_FULL.md', 'manifest.json', '.project-control-corpus.json', 'SHA256SUMS', 'ledger/VALIDATION.json'}
        row.update(role=role, index_excluded=excluded, lineage=[])
        for tag in ['volta', 'v100', 'hardware-architecture']:
            if tag not in row.setdefault('tags', []):
                row['tags'].append(tag)
        if row['title'] not in row.setdefault('aliases', []):
            row['aliases'].append(row['title'])
        if old != target and PREFIX + old not in row['aliases']:
            row['aliases'].append(PREFIX + old)
    dump(stage / '.project-control-corpus.json', corpus)
    assert corpus['relationships'] == original_corpus['relationships']
    assert [r['id'] for r in corpus['resources']] == proposal['resource_ids']
    need_original = [r for r in original_corpus['resources'] if r['id'].startswith('need-')]
    need_staged = [r for r in corpus['resources'] if r['id'].startswith('need-')]
    assert [(r['id'],r['line_start'],r['line_end'],r['path']) for r in need_original] == [(r['id'],r['line_start'],r['line_end'],r['path']) for r in need_staged]
    assert (stage / 'V100_ATLAS_FULL.md').read_bytes() == (source / 'V100_ATLAS_FULL.md').read_bytes()
    links = []
    for path in stage.rglob('*.md'):
        text = path.read_text()
        for raw in re.findall(r'\]\(([^)]+)\)', text):
            target = raw.split(' ',1)[0].strip('<>')
            if re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:', target):
                continue
            local, _, anchor = unquote(target).partition('#')
            resolved = path.parent / local if local else path
            assert resolved.is_file(), f'Broken local link {path}: {raw}'
            if anchor:
                anchors = set(re.findall(r'(?:id|name)=[\"\']([^\"\']+)', resolved.read_text()))
                assert anchor in anchors, f'Unresolved anchor {path}: {raw}'
            links.append({'from': str(path.relative_to(stage)), 'target': raw})
    for line in (stage / 'SHA256SUMS').read_text().splitlines():
        expected, target = line.split('  ',1)
        assert sha((stage / target).read_bytes()) == expected
    assert inventory(source) == proposal['source_pins'], 'Source drift during staging'
    assert sha(Path(proposal['source_zip']['path']).read_bytes()) == proposal['source_zip']['sha256']
    audit = {'status': 'PASS', 'scope': 'staged mechanical migration only; no fresh scientific/CUDA/GPU qualification',
             'source_pins': proposal['source_pins'], 'staged_pins': inventory(stage), 'body_audit': body_audit,
             'resource_ids_preserved': len(corpus['resources']), 'relationships_preserved': len(corpus['relationships']),
             'need_rows_preserved': len(need_staged), 'checked_local_links': links, 'full_compendium_sha256': sha((stage/'V100_ATLAS_FULL.md').read_bytes()),
             'source_zip': proposal['source_zip'], 'archive_only_checksum_entries_removed': omitted,
             'index_excluded_resource_ids': [r['id'] for r in corpus['resources'] if r['index_excluded']]}
    dump(PACKAGE / 'atlas-migration-audit.json', audit)
    dump(PACKAGE / 'atlas-migration-map.json', {'schema_version':1,'index_excluded':True,'role':'evidence','renames':proposal['canonical_renames']})
    print(json.dumps({k:audit[k] for k in ['status','resource_ids_preserved','relationships_preserved','need_rows_preserved','archive_only_checksum_entries_removed']}))


if __name__ == '__main__':
    main()
