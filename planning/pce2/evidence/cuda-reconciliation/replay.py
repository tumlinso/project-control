"""Selection-only actual-code replay; recording callback is not an analysis model.

Run from repository root: .venv/bin/python planning/pce2/evidence/cuda-reconciliation/replay.py before
No ranking, parser, registry or selection overrides. Private snapshot/retrieve
calls expose the considered set independently of budget-truncated output.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import gzip
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'src'))
from project_control.skill_context import SkillContext, SUPPORTED
from project_control.skills import SkillRegistry

HERE = Path(__file__).resolve().parent

def digest(data):
    return hashlib.sha256(data).hexdigest()

def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()

def normalized(text):
    # Remove only Markdown link destinations, never link labels or prose.
    return re.sub(r'(?<=\]\()[^\n)]*(?=\))', lambda m: m.group(0) if re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:', m.group(0)) or m.group(0).startswith('#') else '<PATH_LINK_DESTINATION>', text)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase')
    parser.add_argument('--skills-root', type=Path, default=Path('/home/tumlinson/.agents/skills'))
    args = parser.parse_args()
    target = HERE / (args.phase + '.json.gz')
    if target.exists():
        raise SystemExit('refusing to overwrite receipt')
    registry = SkillRegistry(args.skills_root)
    skill = next(s['id'] for s in registry.list(query='cuda', max_items=128)['skills'] if s['name'] == 'cuda')
    inventory = registry.resources(skill)
    texts = {}
    for row in inventory:
        if Path(row['path']).suffix.lower() in SUPPORTED:
            texts[row['path']] = registry.read_text(skill, row['path'])
    manifest_resources = {}
    edges = []
    for path, doc in texts.items():
        if Path(path).name == '.project-control-corpus.json':
            manifest = json.loads(doc['content'])
            for item in manifest.get('resources', []):
                # Some manifests address paths from the skill root.
                manifest_resources.setdefault(item['path'], []).append(item)
            edges.extend(manifest.get('relationships', []))
    pinned = []
    for path, doc in texts.items():
        text = doc['content']
        heading = next((line.lstrip('# ') for line in text.splitlines() if line.startswith('#')), '')
        stable = re.match(r'([A-Z]\d{2})\b', heading)
        pinned.append({'resource': path, 'identity': doc['identity'], 'bytes': len(text.encode()),
                       'normalized_body_sha256': digest(normalized(text).encode()),
                       'stable_heading_id': stable.group(1) if stable else None,
                       'links': re.findall(r'\[[^\]]*\]\(([^\n)]*)\)', text),
                       'manifest_resources': manifest_resources.get(path, [])})
    # Preserve raw corpus bytes, including archive inputs, for exact refetch.
    with zipfile.ZipFile(HERE / (args.phase + '-corpus.zip'), 'x', zipfile.ZIP_DEFLATED) as archive:
        for row in inventory:
            payload, identity = registry.read_bytes(skill, row['path'], max_bytes=64*1024*1024)
            archive.writestr(row['path'], payload)
    packets = []
    def record(packet):
        packets.append(packet)
        return {'status': 'available', 'summary': 'SELECTION-ONLY recording provider; no answer generated.',
                'evidence_ids': [item['id'] for item in packet['evidence']],
                'uncertainty': 'No model-quality or scientific-validation claim.'}
    service = SkillContext(registry, record)
    _, sections, index, semantic = service._snapshot(skill)
    def descriptor(section):
        row = section.evidence()
        text = row.pop('content')
        row['content_sha256'] = digest(text.encode())
        row['bytes'] = len(text.encode())
        row['stable_resources'] = manifest_resources.get(section.path, [])
        row['atlas'] = '/v100_atlas/' in section.path
        row['full_aggregate'] = 'FULL' in Path(section.path).name.upper()
        return row
    queries = json.loads((HERE / 'queries.json').read_text())
    rows = []
    for query in queries:
        packets.clear()
        selected, expanded = service._retrieve(query['query'], sections, index, semantic)
        lexical = index.search(query['query'], limit=200, prefer_current=False)
        pages = []
        cursor = None
        for _ in range(128):
            output = service.context(query['query'], 'auto', budget_bytes=16384, continuation_cursor=cursor)
            pages.append(output)
            next_cursor = output.get('continuation_cursor')
            if not next_cursor:
                break
            if next_cursor == cursor:
                raise RuntimeError('non-progressing continuation')
            cursor = next_cursor
        else:
            raise RuntimeError('continuation limit exceeded')
        considered = [item for packet in packets for item in packet['evidence']]
        exposed = [item for page in pages for item in page.get('evidence', [])]
        counts = Counter(digest(s.text.encode()) for s in selected)
        lexical_counts = Counter(hit.excerpt for hit in lexical)
        topic = re.compile(query['topic_regex'], re.I)
        proxy = lambda items: sum(bool(topic.search(item['content'])) for item in items)
        rows.append({'id': query['id'], 'query': query['query'], 'expectations': query,
                     'selected': [descriptor(s) for s in selected],
                     'lexical_hits': [{'resource': h.path, 'line': h.line, 'score': h.score} for h in lexical],
                     'metrics': {'selected_sections': len(selected), 'selected_resources': len({s.path for s in selected}),
                                 'selected_bytes': sum(len(s.text.encode()) for s in selected),
                                 'atlas_sections': sum('/v100_atlas/' in s.path for s in selected),
                                 'full_aggregate_sections': sum('FULL' in Path(s.path).name.upper() for s in selected),
                                 'exact_duplicate_selected_excess': sum(c-1 for c in counts.values()),
                                 'exact_duplicate_lexical_excess': sum(c-1 for c in lexical_counts.values()),
                                 'packet_sections': len(considered), 'packet_bytes': sum(len(encoded(p)) for p in packets),
                                 'exposed_sections': len(exposed), 'output_bytes': sum(len(encoded(p)) for p in pages),
                                 'selected_topic_reference_hits': sum(bool(topic.search(s.text)) for s in selected),
                                 'packet_topic_reference_hits': proxy(considered), 'exposed_topic_reference_hits': proxy(exposed),
                                 'continuation_pages': len(pages), 'continuation_complete': not pages[-1].get('continuation_cursor')},
                     'packets': packets.copy(), 'pages': pages, 'expanded_relationships': expanded})
    assert registry.resources(skill) == inventory, 'corpus changed during replay'
    receipt = {'phase': args.phase, 'captured_at': datetime.now(timezone.utc).isoformat(),
               'scope': 'selection-only; topic-reference proxy, not model quality', 'gpu_calls': 0,
               'provider': 'recording callback, all packet IDs cited; no ranking alteration',
               'source_commit': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
               'source_files': {p: digest((ROOT/p).read_bytes()) for p in ('src/project_control/skill_context.py','src/project_control/source_index.py','src/project_control/skills.py')},
               'queryset_sha256': digest((HERE/'queries.json').read_bytes()), 'skill_id': skill,
               'corpus_identity': semantic['identity'], 'resources': pinned, 'manifest_edges': edges,
               'queries': rows}
    target.write_bytes(gzip.compress(encoded(receipt), mtime=0))
    print(json.dumps({'receipt': str(target.relative_to(ROOT)), 'sha256': digest(target.read_bytes()),
                     'queries': len(rows), 'route_counts': dict(Counter(r['pages'][0]['route'] for r in rows)),
                     'totals': {k: sum(r['metrics'][k] for r in rows) for k in ('selected_sections','atlas_sections','full_aggregate_sections','exact_duplicate_selected_excess','exact_duplicate_lexical_excess','selected_bytes','packet_bytes','output_bytes')},
                     'topic_proxy_queries': sum(r['metrics']['selected_topic_reference_hits']>0 for r in rows)}, indent=2))

if __name__ == '__main__':
    main()
