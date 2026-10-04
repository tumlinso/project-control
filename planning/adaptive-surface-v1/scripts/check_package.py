#!/usr/bin/env python3
"""Validate bootstrap consistency. This does not validate product implementation."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def relative_path(value: str, *, allow_dot: bool = False) -> str:
    if not isinstance(value, str) or not value or '\x00' in value or '\\' in value:
        raise ValueError('invalid relative path')
    if value.startswith('/') or re.match(r'^[A-Za-z]:', value):
        raise ValueError('absolute/drive path rejected')
    if '..' in value.split('/'):
        raise ValueError('parent traversal rejected')
    p = PurePosixPath(value)
    if str(p) == '.' and not allow_dot:
        raise ValueError('file path required')
    return p.as_posix()


def read_json(root: Path, name: str) -> Any:
    return json.loads((root / name).read_text(encoding='utf-8'))


def assert_dag(nodes: set[str], edges: list[tuple[str, str]]) -> None:
    outgoing = {n: [] for n in nodes}
    indegree = {n: 0 for n in nodes}
    for dependency, consumer in set(edges):
        if dependency not in nodes or consumer not in nodes:
            raise ValueError(f'unknown dependency endpoint: {dependency} -> {consumer}')
        outgoing[dependency].append(consumer)
        indegree[consumer] += 1
    stack = sorted(n for n, d in indegree.items() if d == 0)
    visited = 0
    while stack:
        n = stack.pop(); visited += 1
        for q in outgoing[n]:
            indegree[q] -= 1
            if indegree[q] == 0: stack.append(q)
    if visited != len(nodes):
        raise ValueError('dependency cycle: '+', '.join(sorted(n for n,d in indegree.items() if d)))


def verify_manifest(root: Path, *, required: bool = False) -> int:
    manifest = root / 'MANIFEST.sha256'
    if not manifest.exists():
        if required: raise ValueError('package checksum manifest is missing')
        return 0
    count = 0
    for line in manifest.read_text().splitlines():
        digest, name = line.split('  ', 1)
        name = relative_path(name)
        p = root / name
        for ancestor in [p, *p.parents]:
            if ancestor == root.parent: break
            if ancestor.is_symlink(): raise ValueError(f'symlink in package: {name}')
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError(f'checksum mismatch: {name}')
        count += 1
    return count


def check_lanes(plan: dict[str, Any]) -> None:
    """Check complete membership and producer-to-exclusive-integrator ownership."""
    tasks = {t['id']: t for t in plan['tasks']}
    run = plan['runs'][0]
    lanes = run['lanes']
    queues = [task for lane in lanes for task in lane['tasks']]
    if (len({lane['id'] for lane in lanes}) != len(lanes)
            or len(queues) != len(set(queues)) or set(queues) != set(tasks)):
        raise ValueError('lane membership must be complete and unique')
    owners = {task: lane for lane in lanes for task in lane['tasks']}
    root_lane = owners[run['root_task_id']]
    if ([lane for lane in lanes if not lane.get('parent_lane_id')] != [root_lane]
            or any(lane.get('parent_lane_id') != root_lane['id'] for lane in lanes if lane is not root_lane)):
        raise ValueError('producer lanes must belong to the single root integration lane')
    if root_lane['tasks'][-1] != run['root_task_id']:
        raise ValueError('aggregate must close its owning lane')
    for lane in lanes:
        workspace = lane.get('workspace', {})
        if set(workspace) - {'mode', 'integration_task_id'}:
            raise ValueError('unsupported native workspace declaration')
        if workspace.get('mode') == 'isolated_merge':
            target = workspace.get('integration_task_id')
            destination = owners.get(target)
            if (destination is None or destination is lane
                    or destination['role'] != 'integrator'
                    or destination['workspace'].get('mode') != 'exclusive'):
                raise ValueError('isolated producer requires an exclusive integrator target')
            prerequisites = {d['task_id'] for d in tasks[target].get('depends_on', [])}
            if not set(lane['tasks']) <= prerequisites:
                raise ValueError('integration target must depend on every producer task')
            if any(tasks[task]['parallel_policy'] != 'parallel_safe' for task in lane['tasks']):
                raise ValueError('isolated producer task requires parallel_safe policy')
        elif workspace.get('mode') != 'exclusive' or workspace.get('integration_task_id'):
            raise ValueError('unexpected AS1 workspace mode or integration target')
    if plan['project']['name'] == 'project-control':
        expected = [
            ('integrator', ['CONTRACT', 'PACKETS', 'SURFACE', 'QUALIFY', 'RELEASE', '0000']),
            ('implementer', ['CONTEXT', 'TRACE', 'CONTROL']),
            ('implementer', ['JOBS', 'SKILL']),
        ]
        if [(lane['role'], lane['tasks']) for lane in lanes] != [
                (role, ['PC-AS1-' + name for name in names]) for role, names in expected]:
            raise ValueError('Project Control lane layout drift')
        if any(lane['workspace'].get('integration_task_id') != 'PC-AS1-SURFACE' for lane in lanes[1:]):
            raise ValueError('Project Control producers must integrate at SURFACE')
        if root_lane['workspace'].get('mode') != 'exclusive':
            raise ValueError('Project Control integrator must be exclusive')
    else:
        expected = ['SK-AS1-' + name for name in ('SEMANTICS', 'RUNTIME', 'GPU', 'ROUTING', 'QUALIFY', 'RELEASE', '0000')]
        if len(lanes) != 1 or lanes[0]['tasks'] != expected or lanes[0]['role'] != 'implementer':
            raise ValueError('Skills must retain one ordered exclusive lane')


def stale_correction_matches(root: Path, project: str, plan: dict[str, Any], preview: dict[str, Any]) -> bool:
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, indent=2).encode()).hexdigest()
    file_digest = hashlib.sha256((root / f'planning/{project}.todo-plan.json').read_bytes()).hexdigest()
    for name, format_name in [('parallel-correction.json', 'pc-as1-parallel-correction/1'),
                               ('search-correction.json', 'pc-as1-package-correction/1')]:
        path = root / 'validation' / name
        if not path.exists():
            continue
        correction = json.loads(path.read_text())
        record = correction.get('plans', {}).get(project, {}) if name == 'parallel-correction.json' else correction
        if (correction.get('format') == format_name
                and correction.get('authority_to_apply') is False
                and correction.get('requires_live_revalidation_before_import') is True
                and record.get('project') == project
                and record.get('historical_preview') == f'validation/{project}.live-preview.json'
                and record.get('historical_plan_digest') == preview.get('plan_digest')
                and record.get('corrected_plan_digest') == digest
                and record.get('corrected_plan_file_sha256') == file_digest
                and record.get('live_preview_status') == 'stale_for_corrected_plan'):
            return True
    return False


def check(root: Path = ROOT, *, require_manifest: bool = False) -> dict[str, Any]:
    root = root.resolve()
    surface = read_json(root, 'contracts/surface.json')
    if len(surface['shared_information_tools'])!=8 or 'find' in surface['shared_information_tools']:
        raise ValueError('shared information surface must have eight tools without public find')
    expected_profiles = {'observer','investigator','coder','mutator','skill_assembler'}
    if set(surface['profiles']) != expected_profiles: raise ValueError('profile set drift')
    old = set(surface['removed_default_names']) | set(surface['temporarily_inactive'])
    for profile, spec in surface['profiles'].items():
        names=spec['tools']
        if len(names)!=len(set(names)): raise ValueError(f'duplicate tools: {profile}')
        if set(names)&old: raise ValueError(f'old/inactive advertised tool: {profile}')
        if spec['automatic_overview']: raise ValueError('automatic overview injection is not approved')
        if ('extended' in spec['details']) != (profile=='observer'): raise ValueError('extended policy drift')
        if profile!='observer' and {'read','skill'}&set(names): raise ValueError('redundant native adapter')
        if profile in {'investigator','skill_assembler'} and ({'investigate','plan','amend_project','maintain_execution'}&set(names)):
            raise ValueError('internal worker privilege/recursion leak')
    outcomes = read_json(root,'planning/outcomes.json')['outcomes']
    case_rows = read_json(root,'contracts/acceptance-cases.json')['cases']
    if len({x['id'] for x in outcomes}) != len(outcomes): raise ValueError('duplicate outcome')
    if len({x['id'] for x in case_rows}) != len(case_rows): raise ValueError('duplicate case')
    by_out={o['id']:o for o in outcomes}; by_case={c['id']:c for c in case_rows}
    native_ids:set[str]=set(); global_edges:list[tuple[str,str]]=[]
    counts={}
    for project in ('project-control','skills'):
        plan=read_json(root,f'planning/{project}.todo-plan.json')
        if plan['schema_version']!=3 or plan['project']['name']!=project: raise ValueError('native plan header')
        tasks=plan['tasks']; ids={t['id'] for t in tasks}
        if len(ids)!=len(tasks) or ids & native_ids: raise ValueError('duplicate native task')
        native_ids |= ids; counts[project]=len(tasks)
        preview_path=root/f'validation/{project}.live-preview.json'
        if preview_path.exists():
            preview=json.loads(preview_path.read_text())
            digest=hashlib.sha256(json.dumps(plan,sort_keys=True,indent=2).encode()).hexdigest()
            if not preview.get('valid'):
                raise ValueError('recorded native plan preview was invalid')
            if preview.get('plan_digest')!=digest:
                if not stale_correction_matches(root, project, plan, preview):
                    raise ValueError('native plan no longer matches its recorded live preview or explicit stale correction')
        check_lanes(plan)
        run=plan['runs'][0]; root_id=run['root_task_id']
        for t in tasks:
            if t.get('parent_id') not in (None,root_id): raise ValueError('parent hierarchy')
            if t.get('parent_id'): global_edges.append((t['parent_id'],t['id']))
            for mode, paths in t['scope'].items():
                if mode in ('exclusive_paths','read_paths','forbidden_paths'):
                    for p in paths: relative_path(p,allow_dot=True)
            for d in t.get('depends_on',[]):
                if d['type']!='task' or d['task_id'] not in ids: raise ValueError('foreign or unsupported native dependency')
                if d['task_id']==root_id: raise ValueError('aggregate gates its own child')
                global_edges.append((d['task_id'],t['id']))
            if t['id']==root_id:
                if t.get('depends_on'): raise ValueError('aggregate prerequisites create parent-edge cycles')
                gates=t.get('gates',[])
                if {g.get('task_id') for g in gates} != ids-{root_id} or not all(g.get('type')=='task_state' and g.get('status')=='done' and g.get('required') for g in gates):
                    raise ValueError('aggregate missing child-completion gates')
                continue
            o=by_out[t['id']]
            if o['project']!=project: raise ValueError('wrong authority')
            if not t.get('gates') or not all(g['required'] for g in t['gates']): raise ValueError('missing required native gate')
            for g in t['gates']:
                if g['type']!='command' or g['argv'][-1]!=t['id']: raise ValueError('gate does not target outcome')
                for p in g['input_paths']: relative_path(p)
                if g.get('track_git_head'): raise ValueError('unnecessary global HEAD-based gate invalidation')
                owned_roots=t['scope']['exclusive_paths'] + t['scope'].get('read_paths',[])
                if any(not any(x==root or x.startswith(root.rstrip('/')+'/') for root in owned_roots) for x in g['input_paths']):
                    raise ValueError('gate input outside declared task scope')
            for name in o['specs']:
                if not (root/name).is_file(): raise ValueError(f'missing spec: {name}')
            for case in o['acceptance_cases']:
                if by_case[case]['outcome']!=o['id']: raise ValueError('case ownership drift')
        assert_dag(ids, [(a,b) for a,b in global_edges if a in ids and b in ids])
    for edge in read_json(root,'planning/cross-authority.json')['edges']:
        for consumer in edge['consumers']:
            if by_out[edge['producer']]['project']==by_out[consumer]['project']: raise ValueError('cross-authority edge is local')
            global_edges.append((edge['producer'],consumer))
    assert_dag(native_ids,global_edges)
    owned=set()
    for requirement in read_json(root,'contracts/requirements.json')['requirements']:
        if not requirement['owners'] or set(requirement['owners'])-set(by_out): raise ValueError('unowned requirement')
        owned.update(requirement['owners'])
    if set(by_out)-owned: raise ValueError('outcome has no requirement owner: '+str(set(by_out)-owned))
    if set(by_case)!=set(c for o in outcomes for c in o['acceptance_cases']): raise ValueError('case coverage drift')
    schema_results={}
    try:
        import jsonschema
    except ImportError:
        schema_results['status']='not_run_jsonschema_unavailable'
    else:
        examples=read_json(root,'examples/contracts.json')
        for schema_name, value in examples.items():
            schema=read_json(root,'contracts/'+schema_name+'.schema.json')
            jsonschema.Draft202012Validator.check_schema(schema)
            jsonschema.Draft202012Validator(schema,format_checker=jsonschema.FormatChecker()).validate(value)
        schema_results={'status':'passed','examples':len(examples)}
    n=verify_manifest(root,required=require_manifest)
    return {'status':'passed','kind':'package_consistency_not_product_acceptance','outcomes':len(outcomes),'native_tasks':counts,'acceptance_cases':len(case_rows),'requirements':len(read_json(root,'contracts/requirements.json')['requirements']),'schema_checks':schema_results,'checksums_verified':n}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--root',type=Path,default=ROOT); parser.add_argument('--require-manifest',action='store_true')
    args=parser.parse_args()
    try: result=check(args.root,require_manifest=args.require_manifest)
    except Exception as e:
        print(json.dumps({'status':'failed','error':str(e)},sort_keys=True));return 1
    print(json.dumps(result,sort_keys=True));return 0

if __name__=='__main__': raise SystemExit(main())
