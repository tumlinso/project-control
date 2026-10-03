#!/usr/bin/env python3
"""Read-only atlas migration planner. Never executes archive code or edits input."""
import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path, PurePosixPath


def sha(data):
    return hashlib.sha256(data).hexdigest()


def slug(title):
    text = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-') or 'resource'


def plan(source):
    files = {str(p.relative_to(source)): p.read_bytes() for p in sorted(source.rglob('*')) if p.is_file()}
    manifest = json.loads(files['manifest.json'])
    corpus = json.loads(files['.project-control-corpus.json'])
    rows = manifest['documents'] + manifest['sources']
    rename = {}
    roles = {'M': 'canonical', 'C': 'canonical', 'R': 'deep_reference', 'E': 'experiment', 'S': 'evidence'}
    canonical = []
    for row in rows:
        if not re.fullmatch(r'[MCRES]\d+', row['id']):
            continue
        old = row['path']
        new = str(PurePosixPath(old).with_name(f"{row['id']}-{slug(row['title'])}.md"))
        assert old in files, old
        assert new not in files or new == old, new
        assert new not in rename.values(), new
        rename[old] = new
        canonical.append({'canonical_id': row['id'], 'title': row['title'], 'old_path': old,
                          'new_path': new, 'role': roles[row['id'][0]], 'index_excluded': False})
    # Match named files, including relative and explicit paths; IDs and edges never participate.
    by_name = {PurePosixPath(old).name: PurePosixPath(new).name for old, new in rename.items()}
    pattern = re.compile(r'(?<![A-Za-z0-9_.-])(' + '|'.join(map(re.escape, by_name)) + r')(?![A-Za-z0-9_.-])')
    reverse = re.compile(r'(?<![A-Za-z0-9_.-])(' + '|'.join(map(re.escape, by_name.values())) + r')(?![A-Za-z0-9_.-])')
    old_names = {new: old for old, new in by_name.items()}
    substitutions = []
    full = 'V100_ATLAS_FULL.md'
    for path, data in files.items():
        if path in {full, 'manifest.json', '.project-control-corpus.json', 'SHA256SUMS'}:
            continue
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            continue
        changed, count = pattern.subn(lambda m: by_name[m[1]], text)
        if count:
            normalized = reverse.sub(lambda m: old_names[m[1]], changed).encode()
            assert normalized == data, f'Non-path delta: {path}'
            substitutions.append({'path': path, 'destination': rename.get(path, path), 'substitutions': count,
                                  'before_sha256': sha(data), 'predicted_sha256': sha(changed.encode()),
                                  'normalized_sha256': sha(normalized)})
    full_text = files[full].decode()
    links = re.findall(r'\]\(([^)]+)\)', full_text)
    anchors = set(re.findall(r'(?:id|name)=[\"\']([^\"\']+)', full_text))
    assert all(link[1:] in anchors for link in links if link.startswith('#')), 'Broken FULL anchor'
    archive = []
    for link in sorted(set(links)):
        target = link.split('#', 1)[0]
        if target in rename:
            archive.append({'legacy_path': target, 'canonical_path': rename[target],
                            'canonical_id': next(r['canonical_id'] for r in canonical if r['old_path'] == target),
                            'legacy_sha256': sha(files[target])})
    source_zip = source.parent / 'V100_SXM2_Circuit_Bending_Atlas.zip'
    archive_sha = sha(source_zip.read_bytes())
    assert archive_sha == corpus['source']['sha256'], 'Source ZIP identity drift'
    return {
        'schema_version': 1, 'mode': 'dry_run', 'source': str(source),
        'source_zip': {'path': str(source_zip), 'sha256': archive_sha},
        'source_pins': {path: sha(data) for path, data in files.items()},
        'canonical_renames': canonical, 'path_substitutions': substitutions,
        'resource_ids': [r['id'] for r in corpus['resources']],
        'relationship_sha256': sha(json.dumps(corpus['relationships'], sort_keys=True, separators=(',', ':')).encode()),
        'resource_count': len(corpus['resources']), 'relationship_count': len(corpus['relationships']),
        'full_compendium': {'path': full, 'sha256': sha(files[full]), 'local_links': len(links),
                            'anchor_links': sum(link.startswith('#') for link in links),
                            'legacy_lookup': archive, 'strategy': 'byte_identical; all current links resolve as internal anchors'},
        'manifest_updates': ['paths', 'bytes', 'sha256', 'optional role/index_excluded/lineage metadata'],
        'corpus_updates': ['paths', 'sha256', 'metadata bytes/role/index_excluded/lineage; retain every id and edge'],
        'checksum_policy': 'Rebuild SHA256SUMS using the original listed membership with renamed paths; exclude self and preserve original unlisted exclusions.',
        'validation_policy': 'Retain historical CPU/GPU claims; new migration receipt covers fresh path/hash/graph/content audits only.',
        'apply_gates': ['root baseline/proposal approval', 'archive strategy decision', 'metadata schema confirmation',
                        'exact source file membership/hash pin match', 'unchanged source ZIP and FULL hashes',
                        'identical resource IDs and relationships', 'normalized byte equality for every path-edited technical file',
                        'all local links resolved or explicitly archive-resolved', 'fresh checksum and metadata hashes'],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = plan(args.source)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'renames': len(result['canonical_renames']), 'path_edited_files': len(result['path_substitutions']),
                      'resources': result['resource_count'], 'relationships': result['relationship_count'],
                      'full_archive_lookups': len(result['full_compendium']['legacy_lookup']), 'output': str(args.output)}))
