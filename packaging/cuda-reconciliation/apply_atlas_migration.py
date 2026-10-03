#!/usr/bin/env python3
"""Root-only guarded publication. Defaults to verification; --apply swaps the complete atlas."""
import argparse
import datetime
import json
import os
import shutil
import tempfile
from pathlib import Path
from stage_atlas_migration import PACKAGE, inventory
from plan_atlas_migration import sha


def verify(source, stage, audit):
    assert inventory(source) == audit['source_pins'], 'Live source drift: do not publish'
    assert inventory(stage) == audit['staged_pins'], 'Staged artifact drift: do not publish'
    assert sha(Path(audit['source_zip']['path']).read_bytes()) == audit['source_zip']['sha256'], 'ZIP drift'
    assert sha((stage/'V100_ATLAS_FULL.md').read_bytes()) == sha((source/'V100_ATLAS_FULL.md').read_bytes()), 'FULL drift'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    proposal = json.loads((PACKAGE/'atlas-migration-dry-run.json').read_text())
    audit = json.loads((PACKAGE/'atlas-migration-audit.json').read_text())
    assert audit['status'] == 'PASS'
    source = Path(proposal['source'])
    stage = PACKAGE/'staged-atlas'
    verify(source, stage, audit)
    if not args.apply:
        print('PASS source/stage membership and hashes; FULL and ZIP identity; no live changes')
        return
    # Root must coordinate the writer boundary; hash checks do not lock unrelated writers.
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup_parent = PACKAGE/'atlas-backups'/timestamp
    backup_parent.mkdir(parents=True, exist_ok=False)
    assert source.stat().st_dev == backup_parent.stat().st_dev, 'Atomic backup requires same filesystem'
    temp = Path(tempfile.mkdtemp(prefix='.v100_atlas.migration-', dir=source.parent))
    candidate = temp/'v100_atlas'
    shutil.copytree(stage, candidate)
    assert inventory(candidate) == audit['staged_pins'], 'Candidate copy mismatch'
    verify(source, stage, audit)
    backup = backup_parent/'v100_atlas'
    os.rename(source, backup)
    try:
        assert inventory(backup) == audit['source_pins'], 'Source changed at publication boundary'
        os.rename(candidate, source)
    except BaseException:
        if not source.exists():
            os.rename(backup, source)
        raise
    temp.rmdir()
    assert inventory(source) == audit['staged_pins'], 'Published artifact mismatch'
    assert inventory(backup) == audit['source_pins'], 'Backup mismatch'
    assert sha(Path(audit['source_zip']['path']).read_bytes()) == audit['source_zip']['sha256']
    receipt = {'status':'applied','source':str(source),'backup':str(backup),'timestamp':timestamp,
               'published_pins':audit['staged_pins'],'original_pins':audit['source_pins'],
               'source_zip':audit['source_zip'],'full_compendium_sha256':audit['full_compendium_sha256']}
    path = PACKAGE/f'atlas-apply-{timestamp}.json'
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True)+'\n')
    print(json.dumps({'status':'applied','backup':str(backup),'receipt':str(path)}))


if __name__ == '__main__':
    main()
