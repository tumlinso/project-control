#!/usr/bin/env python3
"""Prepare root-approved navigation-only delta against the current audited stage."""
import copy
import difflib
import json
from pathlib import Path
from plan_atlas_migration import sha
from stage_atlas_migration import PACKAGE, PREFIX, inventory, dump


def main():
    stage=PACKAGE/'staged-atlas'
    audit=json.loads((PACKAGE/'atlas-migration-audit.json').read_text())
    beforepins=inventory(stage)
    assert beforepins==audit['staged_pins'], 'Stage drift'
    original_corpus=json.loads((stage/'.project-control-corpus.json').read_text())
    original_manifest=json.loads((stage/'manifest.json').read_text())
    substitutions={
      'README.md':[
        ('Python tools require Python 3.10+ and the standard library only:\n\n```sh\npython tools/read_atlas.py search "branchless sequence routing"\npython tools/read_atlas.py read M01 C01 --budget 1800 --refs\n',
         'Use Project Control to retrieve the relevant atlas resources:\n\n```python\nskill_context(query="V100 branchless sequence routing", skill="<returned CUDA ID>")\nskill_read(skill_id="<returned CUDA ID>", resource="<returned resource path>")\n```\n\nFor the full archival compendium, request it explicitly:\n\n```python\nskill_read(skill_id="<returned CUDA ID>", resource="references/architectures/volta/v100_atlas/V100_ATLAS_FULL.md")\n```\n\nThe authoring validation prototypes below are preserved in the original ZIP as historical tooling; they are not the Project Control observer runtime. They require Python 3.10+ and the standard library only:\n\n```sh\n'),
        ('Run refresh before validation after editing a document. The SHA256SUMS file describes this release; local edits intentionally change those hashes.',
         'The historical authoring workflow runs refresh before validation after editing a document. The SHA256SUMS file describes this release; local edits intentionally change those hashes.')],
      'START_HERE.md':[
        ('```\npython tools/read_atlas.py search "branchless sequence routing"\npython tools/read_atlas.py read M01 M03 C01 --budget 1800 --refs\npython tools/read_atlas.py read R05 C10 E09 --budget 2600\n```\n\nBudgets are words, not model-specific tokens. The reader emits complete cards or summaries with paths; it does not silently truncate correctness caveats. `--refs` shows prerequisite/source summaries without recursively dumping documents. The full compendium is for archival reading, not default model context.',
         '```python\nskill_context(query="V100 branchless sequence routing", skill="<returned CUDA ID>")\nskill_read(skill_id="<returned CUDA ID>", resource="<returned resource path>")\n```\n\nUse the returned resource paths to read the relevant mechanism, composition and prerequisite/source sections. The private reader is preserved in the original ZIP as historical authoring tooling; it is not the Project Control observer runtime. The full compendium is for explicit archival reading through `skill_read`, not default model context.')]
    }
    nav_audit=[];diff=[]
    for name,replacements in substitutions.items():
        original=(stage/name).read_text();changed=original
        for old,new in replacements:
            assert changed.count(old)==1,(name,'Replacement mismatch')
            changed=changed.replace(old,new,1)
        normalized=changed
        for old,new in reversed(replacements):
            assert normalized.count(new)==1
            normalized=normalized.replace(new,old,1)
        assert normalized.encode()==original.encode()
        diff.extend(difflib.unified_diff(original.splitlines(True),changed.splitlines(True),fromfile=name+'.before',tofile=name+'.after'))
        (stage/name).write_text(changed)
        nav_audit.append({'path':name,'before_sha256':sha(original.encode()),'after_sha256':sha(changed.encode()),'inverse_substitution_sha256':sha(normalized.encode()),'exact_substitutions':[{'old':old,'new':new} for old,new in replacements]})
    manifest=copy.deepcopy(original_manifest)
    for row in manifest['documents']+manifest['sources']:
        if row['path'] in substitutions:
            data=(stage/row['path']).read_bytes();row['sha256']=sha(data);row['bytes']=len(data);row['words']=len(data.decode().split())
    # The manifest aggregate word count reflects the same split-based document count.
    manifest['unique_document_words']=sum(r['words'] for r in manifest['documents'])
    dump(stage/'manifest.json',manifest)
    corpus=copy.deepcopy(original_corpus)
    for row in corpus['resources']:
        local=row['path'].removeprefix(PREFIX)
        if local in {*substitutions,'manifest.json'}:
            data=(stage/local).read_bytes();row['sha256']=sha(data)
            if 'bytes' in row.get('metadata',{}):row['metadata']['bytes']=len(data)
            if 'words' in row.get('metadata',{}):row['metadata']['words']=len(data.decode().split())
    dump(stage/'.project-control-corpus.json',corpus)
    checksum=[]
    for line in (stage/'SHA256SUMS').read_text().splitlines():
        _,name=line.split('  ',1);checksum.append(f'{sha((stage/name).read_bytes())}  {name}')
    (stage/'SHA256SUMS').write_text('\n'.join(checksum)+'\n')
    afterpins=inventory(stage);changedfiles=[k for k in beforepins if beforepins[k]!=afterpins[k]]
    assert set(changedfiles)=={'README.md','START_HERE.md','manifest.json','.project-control-corpus.json','SHA256SUMS'}
    assert corpus['relationships']==original_corpus['relationships']
    assert [r['id'] for r in corpus['resources']]==[r['id'] for r in original_corpus['resources']]
    canonical=json.loads((PACKAGE/'atlas-migration-dry-run.json').read_text())['canonical_renames']
    assert all(beforepins[r['new_path']]==afterpins[r['new_path']] for r in canonical)
    assert beforepins['NEED_INDEX.md']==afterpins['NEED_INDEX.md'] and beforepins['FIELD_GUIDE.md']==afterpins['FIELD_GUIDE.md'] and beforepins['V100_ATLAS_FULL.md']==afterpins['V100_ATLAS_FULL.md']
    delta={'status':'PASS','scope':'root-approved navigation-only delta; no live writes','before_staged_pins':beforepins,'after_staged_pins':afterpins,'changed_files':changedfiles,'navigation_inverse_substitution_audit':nav_audit,'canonical_body_hashes_unchanged':len(canonical),'resource_ids_preserved':len(corpus['resources']),'relationships_preserved':len(corpus['relationships']),'need_field_guide_full_unchanged':True}
    dump(PACKAGE/'atlas-navigation-delta.json',delta)
    (PACKAGE/'atlas-navigation.diff').write_text(''.join(diff))
    for nav in nav_audit:
        body=next(x for x in audit['body_audit'] if x['new_path']==nav['path'])
        assert body['staged_sha256']==nav['before_sha256']
        assert body['normalized_sha256']==nav['inverse_substitution_sha256']
        body['staged_sha256']=nav['after_sha256']
        body['normalization']='inverse navigation substitutions from atlas-navigation-delta.json'
    audit['staged_pins']=afterpins;audit['navigation_delta']={'receipt':'atlas-navigation-delta.json','exact_diff':'atlas-navigation.diff','inverse_substitution_audit':nav_audit};dump(PACKAGE/'atlas-migration-audit.json',audit)
    print(json.dumps({'status':'PASS','changed_files':changedfiles,'canonical_bodies_unchanged':len(canonical),'resource_ids':len(corpus['resources']),'edges':len(corpus['relationships'])}))


if __name__=='__main__':main()
