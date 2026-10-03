"""Compare stable atlas coverage and transport costs; no model-quality claims."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent

def load(name):
    return json.loads(gzip.decompress((HERE/name).read_bytes()))

def stable_resources(receipt):
    return {r['resource']: r['stable_heading_id'] for r in receipt['resources'] if r.get('stable_heading_id') and Path(r['resource']).parent.name in {'sources','reference','mechanisms','compositions','experiments'}}

def stable_ids(items, resource_ids):
    known=set(resource_ids.values())
    return sorted({v for i in items for v in ([resource_ids.get(i['resource'])]+i.get('semantic_ids',[])) if v in known})

def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else 'after'
    before, after = load('before.json.gz'), load(phase + '.json.gz')
    prefix = 'comparison' if phase == 'after' else phase + '-comparison'
    targets = json.loads((HERE/'stable-targets.json').read_text())
    old_ids, new_ids = stable_resources(before), stable_resources(after)
    before_by_id = {r['stable_heading_id']:r for r in before['resources'] if r.get('stable_heading_id')}
    after_by_id = {r['stable_heading_id']:r for r in after['resources'] if r.get('stable_heading_id')}
    cards = []
    for identity, old in before_by_id.items():
        new = after_by_id.get(identity)
        cards.append({'id':identity, 'technical_card':Path(old['resource']).parent.name in {'sources','reference','mechanisms','compositions','experiments'}, 'before':old['resource'], 'after':new['resource'] if new else None,
                      'path_changed':bool(new and old['resource'] != new['resource']),
                      'normalized_body_preserved':bool(new and old['normalized_body_sha256'] == new['normalized_body_sha256'])})
    old_queries = {r['id']:r for r in before['queries']}
    delta = []
    for row in after['queries']:
        previous = old_queries[row['id']]
        def observation(query, ids):
            packet_items = [item for p in query['packets'] for item in p['evidence']]
            visible = [item for p in query['pages'] for item in p['evidence']]
            return {'selected_stable_ids':stable_ids(query['selected'], ids),
                    'packet_stable_ids':stable_ids(packet_items, ids),
                    'visible_stable_ids':stable_ids(visible, ids),
                    'first_packet': [{'resource':i['resource'], 'stable_id':ids.get(i['resource']), 'role':i.get('role'), 'semantic_ids':i.get('semantic_ids', [])} for i in packet_items[:8]],
                    'first_visible': [{'resource':i['resource'], 'stable_id':ids.get(i['resource']), 'role':i.get('role'), 'semantic_ids':i.get('semantic_ids', [])} for i in visible[:8]],
                    'selected_roles':dict(Counter(s.get('role','absent') for s in query['selected'])),
                    'visible_roles':dict(Counter(s.get('role','absent') for s in visible)),
                    'atlas_card_sections':sum(s['resource'] in ids for s in query['selected']),
                    'reported_visible_mismatches':sum(p['coverage']['returned_sections'] != len(p['evidence']) for p in query['pages'])}
        old_obs,new_obs=observation(previous,old_ids),observation(row,new_ids)
        expected=targets.get(row['id'])
        target=None if not expected else {'expected_any':expected['ids'], 'rationale':expected['rationale'],
                   'before_selected':bool(set(expected['ids']) & set(old_obs['selected_stable_ids'])),
                   'after_selected':bool(set(expected['ids']) & set(new_obs['selected_stable_ids'])),
                   'before_packet':bool(set(expected['ids']) & set(old_obs['packet_stable_ids'])),
                   'after_packet':bool(set(expected['ids']) & set(new_obs['packet_stable_ids'])),
                   'before_visible':bool(set(expected['ids']) & set(old_obs['visible_stable_ids'])),
                   'after_visible':bool(set(expected['ids']) & set(new_obs['visible_stable_ids'])),
                   'before_top8_packet':bool(set(expected['ids']) & {v for i in old_obs['first_packet'] for v in ([i['stable_id']]+i['semantic_ids'])}),
                   'after_top8_packet':bool(set(expected['ids']) & {v for i in new_obs['first_packet'] for v in ([i['stable_id']]+i['semantic_ids'])})}
        delta.append({'id':row['id'], 'target':target, 'before':old_obs,'after':new_obs,
                      'metric_delta':{k:row['metrics'][k]-previous['metrics'][k] for k in ('selected_sections','atlas_sections','full_aggregate_sections','exact_duplicate_selected_excess','exact_duplicate_lexical_excess','selected_bytes','packet_bytes','output_bytes','exposed_sections','continuation_pages')}})
    totals=lambda receipt:{k:sum(r['metrics'][k] for r in receipt['queries']) for k in ('selected_sections','atlas_sections','full_aggregate_sections','exact_duplicate_selected_excess','exact_duplicate_lexical_excess','selected_bytes','packet_bytes','output_bytes','exposed_sections','continuation_pages')}
    result={'scope':'Selection-only, stable-ID and reference coverage; not answer correctness.',
            'phase':phase,
            'receipt_hashes':{n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ('before.json.gz',phase + '.json.gz')},
            'before_totals':totals(before),'after_totals':totals(after),
            'ordinary_FULL_zero':all(r['metrics']['full_aggregate_sections']==0 for r in after['queries']),
            'body_preservation':cards,'target_checks':sum(r['target'] is not None for r in delta),
            'stable_target_coverage':{field:sum(bool(r['target'] and r['target'][field]) for r in delta) for field in ('before_selected','after_selected','before_packet','after_packet','before_visible','after_visible','before_top8_packet','after_top8_packet')},
            'preserved_bodies':sum(r['normalized_body_preserved'] for r in cards),
            'stable_heading_resources':len(cards),
            'technical_cards':sum(r['technical_card'] for r in cards),
            'preserved_technical_card_bodies':sum(r['technical_card'] and r['normalized_body_preserved'] for r in cards),
            'changed_navigation_bodies':[r for r in cards if not r['technical_card'] and not r['normalized_body_preserved']],
            'renamed_stable_heading_resources':sum(r['path_changed'] for r in cards),
            'atlas_card_query_participation':{phase_name:sum(bool(r[phase_name]['selected_stable_ids']) for r in delta) for phase_name in ('before','after')},
            'queries':delta}
    result['checks']={'ordinary_FULL_zero':result['ordinary_FULL_zero'],
                      'zero_selected_exact_duplicates':result['after_totals']['exact_duplicate_selected_excess']==0,
                      'technical_card_bodies_preserved':result['technical_cards']==result['preserved_technical_card_bodies'],
                      'baseline_targets_retained':all(not r['target'] or not r['target']['before_selected'] or r['target']['after_selected'] for r in delta),
                      'atlas_card_participation_32':result['atlas_card_query_participation']['after']==32}
    assert result['ordinary_FULL_zero'], 'ordinary FULL hits remain'
    (HERE/(prefix + '.json')).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in {'body_preservation','queries'}},indent=2))

if __name__=='__main__': main()
