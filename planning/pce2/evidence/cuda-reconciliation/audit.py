"""Check replay receipts against raw pinned corpus and reported transport limits."""
import hashlib
import gzip
import json
from pathlib import Path
import sys
import zipfile

path = Path(sys.argv[1])
receipt = json.loads(gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes())
assert len(receipt['queries']) == 32
assert receipt['gpu_calls'] == 0
with zipfile.ZipFile(path.with_name(receipt['phase'] + '-corpus.zip')) as archive:
    for resource in receipt['resources']:
        assert hashlib.sha256(archive.read(resource['resource'])).hexdigest() == resource['identity'], resource['resource']
for row in receipt['queries']:
    selected = {s['id'] for s in row['selected']}
    assert row['metrics']['continuation_complete']
    assert row['metrics']['selected_sections'] == len(selected)
    for packet in row['packets']:
        assert {s['id'] for s in packet['evidence']} <= selected
        assert len(packet['evidence']) <= 64
    for page in row['pages']:
        assert {s['id'] for s in page['evidence']} <= selected
        assert len(json.dumps(page, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()) <= 16384
        assert page['skill'] == receipt['skill_id']
print('PASS: 32 queries; source hashes, skill identity, selected IDs, packet limits, output budgets, continuations; zero GPU calls')
