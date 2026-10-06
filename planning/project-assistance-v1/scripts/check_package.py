#!/usr/bin/env python3
"""Validate an inert PA1 bundle; never apply a Todo plan or launch inference.

Default: standard-library integrity and structural checks only.
--native: additionally call the already-installed canonical Todo plan validator.
This is not a production source test, model evaluation, or release qualification.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import unquote, urlsplit
from typing import Any


class PackageError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def reject_constant(value: str) -> Any:
    raise PackageError(f'Non-finite JSON value: {value}')


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'),
                      object_pairs_hook=unique_pairs, parse_constant=reject_constant)


def safe_path(value: str) -> bool:
    p = PurePosixPath(value)
    return bool(value) and not p.is_absolute() and '..' not in p.parts and '\\' not in value


def indexed(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    require(all(isinstance(row, dict) and isinstance(row.get('id'), str) for row in rows),
            f'{label}: rows require IDs')
    values = {row['id']: row for row in rows}
    require(len(values) == len(rows), f'{label}: duplicate IDs')
    return values


def acyclic(nodes: set[str], edges: list[tuple[str, str]], label: str) -> None:
    children = {n: set() for n in nodes}
    degree = {n: 0 for n in nodes}
    for a, b in edges:
        require(a in nodes and b in nodes, f'{label}: unknown edge {a} -> {b}')
        require(a != b, f'{label}: self edge {a}')
        if b not in children[a]:
            children[a].add(b)
            degree[b] += 1
    ready = [n for n, d in degree.items() if d == 0]
    visited = 0
    while ready:
        n = ready.pop()
        visited += 1
        for b in children[n]:
            degree[b] -= 1
            if degree[b] == 0:
                ready.append(b)
    require(visited == len(nodes), f'{label}: dependency cycle')


def manifest_check(root: Path) -> int:
    path = root / 'MANIFEST.sha256'
    require(path.is_file(), 'Missing MANIFEST.sha256')
    actual = set()
    for p in root.rglob('*'):
        require(not p.is_symlink(), f'Unexpected symlink: {p}')
        if p.is_file() and p != path:
            actual.add(p.relative_to(root).as_posix())
    declared = set()
    for line in path.read_text().splitlines():
        m = re.fullmatch(r'([a-f0-9]{64})  (.+)', line)
        require(m is not None, 'Malformed manifest line')
        assert m
        digest, rel = m.groups()
        require(safe_path(rel) and rel != 'MANIFEST.sha256', f'Unsafe manifest path: {rel}')
        require(rel not in declared, f'Duplicate manifest path: {rel}')
        declared.add(rel)
        p = root / rel
        require(p.is_file(), f'Missing file: {rel}')
        require(hashlib.sha256(p.read_bytes()).hexdigest() == digest, f'Digest mismatch: {rel}')
    require(actual == declared, f'Manifest inventory mismatch: {sorted(actual ^ declared)}')
    return len(declared)


def validate(root: Path, native: bool) -> dict[str, Any]:
    file_count = manifest_check(root)
    docs = {p.relative_to(root).as_posix(): load(p) for p in root.rglob('*.json')}
    meta = docs['machine/package.json']
    require(meta['status'] == 'inert_not_applied', 'Package cannot imply live application')
    source = docs['evidence/sources.json']
    records = indexed(source['sources'] + source['observations'] + source['external_sources'], 'sources')
    for row in source['sources']:
        require(re.fullmatch(r'[a-f0-9]{64}', row['content_sha256']) is not None,
                f"Invalid source hash: {row['id']}")
        require(safe_path(row['path']), f"Unsafe source path: {row['id']}")
        require(all(isinstance(a, int) and isinstance(b, int) and 1 <= a <= b
                    for a, b in row['ranges_read']), f"Invalid source ranges: {row['id']}")
    outcomes = indexed(docs['machine/outcomes.json']['outcomes'], 'outcomes')
    cases = indexed(docs['machine/acceptance.json']['cases'], 'acceptance')
    require(len(outcomes) == 9 and len(cases) == 40, 'Unexpected outcome/case inventory')
    for o in outcomes.values():
        require(set(o['sources']) <= records.keys(), f"Unknown sources: {o['id']}")
        require(any(c['owner'] == o['id'] for c in cases.values()), f"No cases: {o['id']}")
    for c in cases.values():
        require(c['owner'] in outcomes and c['mandatory'] is True, f"Invalid case owner: {c['id']}")
        require(c['status'] == 'planned_not_executed', f"Unsubstantiated execution claim: {c['id']}")
        require(set(c['sources']) <= records.keys(), f"Unknown case source: {c['id']}")
    native_reports = {}
    native_tasks = {}
    edges = []
    for repo, prefix in (('project-control', 'PC'), ('skills', 'SK')):
        plan = docs[f'machine/{repo}.todo-plan.json']
        require(plan['schema_version'] == 3 and plan['project']['name'] == repo, f'Wrong native plan: {repo}')
        tasks = indexed(plan['tasks'], repo + ' tasks')
        invariant_ids = set(indexed(plan['invariants'], repo + ' invariants'))
        epic = prefix + '-PA1-0000'
        require(epic in tasks and tasks[epic]['kind'] == 'epic', f'Missing aggregate: {repo}')
        require(not tasks[epic].get('depends_on'), 'Aggregate parent must not introduce dependency cycles')
        expected = {o['id'] for o in outcomes.values() if o['repository'] == repo}
        require(set(tasks) == expected | {epic}, f'Native/catalog mismatch: {repo}')
        for t in tasks.values():
            require(set(t.get('invariants', [])) <= invariant_ids, f"Unknown invariant: {t['id']}")
            require('status' not in t and 'result' not in t, 'Native task must not claim completion')
            if t['id'] == epic:
                continue
            o = outcomes[t['id']]
            require(t['parent_id'] == epic, 'Wrong aggregate parent')
            require(t['title'] == o['title'] and t['objective'] == o['objective'], 'Outcome prose mismatch')
            require(t['scope']['exclusive_paths'] == o['scopes'], 'Outcome scope mismatch')
            note = json.loads(t['notes'])
            require(note['outcome'] == t['id'], 'Wrong task briefing')
            deps = []
            for dep in t.get('depends_on', []):
                require(dep['type'] == 'task' and dep['task_id'] in tasks, 'Foreign or unsupported native dependency')
                require(dep['task_id'] != epic, 'Child depends on aggregate')
                deps.append(dep['task_id'])
                edges.append((dep['task_id'], t['id']))
            require(deps == o['depends_on'], 'Outcome dependency mismatch')
            for field in ('exclusive_paths', 'read_paths', 'forbidden_paths'):
                require(all(safe_path(p) for p in t['scope'].get(field, [])), 'Unsafe native scope')
            require(len(t['gates']) == 1 and t['gates'][0]['required'] is True, 'Missing focused gate')
            require(t['gates'][0]['argv'] == ['python', '-m', 'unittest', o['gate_module'], '-v'],
                    'Unexpected gate command')
        require(len(plan['runs']) == 1, 'Expected one default run')
        run = plan['runs'][0]
        require(run['root_task_id'] == epic and len(run['lanes']) == 1, 'Wrong run/lane shape')
        lane = run['lanes'][0]
        require(lane['role'] == 'implementer' and lane['workspace']['mode'] == 'exclusive', 'Wrong lane policy')
        order = lane['tasks']
        require(len(order) == len(set(order)) and set(order) == set(tasks) and order[-1] == epic,
                'Invalid serial queue')
        positions = {t: i for i, t in enumerate(order)}
        for t in tasks.values():
            for dep in t.get('depends_on', []):
                require(positions[dep['task_id']] < positions[t['id']], 'Queue violates dependency')
        require(len(json.dumps(run['charter']).encode()) <= 8192, 'Run charter exceeds 8KiB')
        native_tasks.update(tasks)
        if native:
            try:
                module = importlib.import_module('todo_orchestrator.plan')
            except ImportError as e:
                raise PackageError('Canonical installed Todo validator unavailable; bind the trusted development environment, not an ambient replacement.') from e
            try:
                report = module.validate_plan(plan, repo_root=None)
            except Exception as e:
                raise PackageError(f'Canonical native validation rejected {repo}: {e}') from e
            require(isinstance(report, dict), 'Unexpected native validator report')
            require(not report.get('errors') and report.get('valid', True) is not False
                    and report.get('ok', True) is not False, f'Native validation failed: {report}')
            native_reports[repo] = {'module': str(Path(module.__file__).resolve()), 'report': report}
    cross = docs['machine/cross-authority.json']['edges']
    for e in cross:
        a, b = e['producer'], e['consumer']
        require(a in outcomes and b in outcomes and outcomes[a]['repository'] != outcomes[b]['repository'],
                'Invalid cross-authority edge')
        require(e['contract'] and e['verification'], 'Empty handoff contract')
        edges.append((a, b))
    acyclic(set(outcomes), edges, 'combined outcomes')
    evals = docs['machine/eval-cases.json']
    ec = indexed(evals['cases'], 'eval cases')
    fixture = root / evals['fixture_root']
    require(fixture.is_dir(), 'Missing evaluation fixture')
    for c in ec.values():
        for p in c['evidence_paths']:
            require(safe_path(p) and (fixture / p).is_file(), f"Missing fixture source: {p}")
    py_count = 0
    md_links = 0
    for p in root.rglob('*.py'):
        ast.parse(p.read_text(), filename=str(p))
        py_count += 1
    for p in root.rglob('*.md'):
        text = p.read_text()
        for ref in re.findall(r'\b(?:S|O|W)\d{2}\b', text):
            require(ref in records, f'Unknown source reference {ref} in {p.name}')
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
            url = urlsplit(target)
            if url.scheme or not url.path:
                continue
            rel = unquote(url.path)
            target_path = (p.parent / rel).resolve()
            require(target_path == root or root in target_path.parents, f'Escaping documentation link: {target}')
            require(target_path.exists(), f'Missing documentation link: {p.name} -> {target}')
            md_links += 1
    return {'status':'passed','scope':'package_integrity_and_structural_validation_only',
            'files_hashed':file_count,'sources':len(records),'outcomes':len(outcomes),
            'native_task_records':len(native_tasks),'acceptance_cases':len(cases),
            'eval_cases':len(ec),'python_files_parsed':py_count,'local_markdown_links_checked':md_links,
            'native_validation':native_reports if native else 'not_run_requires_trusted_installed_kernel',
            'product_acceptance':'not_executed','live_model_or_gpu':'not_executed','plans_applied':False}


def main() -> int:
    sys.dont_write_bytecode = True
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument('--native', action='store_true', help='Also use the already-installed canonical Todo validator; no application.')
    args = ap.parse_args()
    try:
        report = validate(args.root.resolve(strict=True), args.native)
        print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (PackageError, ValueError, OSError, KeyError, TypeError, SyntaxError) as e:
        print(json.dumps({'status':'failed','reason':str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
