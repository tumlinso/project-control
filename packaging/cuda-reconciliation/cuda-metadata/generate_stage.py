#!/usr/bin/env python3
"""Stage inert NONatlas CUDA metadata; never write to the source skill."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ATLAS = 'references/architectures/volta/v100_atlas/'
GENERATED = 'assets/cuda-markdown-manifest.json'
OLD_GUIDE = 'python <skill-dir>/scripts/cuda_controller.py guide --query "<architecture and question>" --json\n'
OLD_TEXT = ('Guidance returns exact bounded sections from the preserved Markdown corpus.\n'
            'Evidence summaries point to authoritative raw artifacts.')
NEW_TEXT = ('For guidance, prefer Project Control `skill_context(query="CUDA <architecture\n'
            'and question>", skill="auto")` to retrieve bounded sections and semantic links.\n'
            'Use `skill_read(skill_id=<returned CUDA ID>, resource=<returned path>)` for\n'
            'an explicit bounded follow-up read. The controller `guide` command remains\n'
            'a direct compatibility fallback.\n'
            'Evidence summaries point to authoritative raw artifacts.')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resource_id(path: str) -> str:
    return 'cuda-resource-' + digest(path.encode('utf-8'))


def stage_skill(original: bytes) -> bytes:
    text = original.decode('utf-8')
    if text.count(OLD_GUIDE) != 1 or text.count(OLD_TEXT) != 1:
        raise ValueError('retrieval edit preimage changed')
    return text.replace(OLD_GUIDE, '').replace(OLD_TEXT, NEW_TEXT).encode('utf-8')


def architecture_tags(path: str) -> list[str]:
    parts = Path(path).parts
    if 'architectures' in parts:
        return [parts[parts.index('architectures') + 1]]
    if re.search(r'(?:^|[^a-z0-9])(?:v100|volta)(?:[^a-z0-9]|$)', Path(path).stem.lower()):
        return ['volta', 'v100']
    return []


def local_links(root: Path, path: str, text: str, known: set[str]) -> set[str]:
    """Only literal inline Markdown links; no guessed backtick references."""
    targets = set()
    for match in re.finditer(r'(?<!!)\[[^\]\n]*\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+[^)]*)?\)', text):
        url = urlsplit(match.group(1).strip('<>'))
        if url.scheme or url.netloc or not url.path or url.path.startswith('/'):
            continue
        relative = unquote(url.path)
        for candidate in ((root / path).parent / relative, root / relative):
            try:
                target = candidate.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
            if target in known and target != path:
                targets.add(target)
                break
    return targets


def backtick_references(root: Path, path: str, text: str, known: set[str]) -> tuple[set[str], list[dict]]:
    """Exact existing Markdown path mentions, without interpreting code or prerequisites."""
    targets, skipped = set(), []
    for relative in sorted(set(re.findall(r'(?<!`)`([^`\n]+\.md(?:#[^`\n]*)?)`(?!`)', text))):
        url = urlsplit(relative)
        if url.scheme or url.netloc or url.path.startswith('/') or any(c in url.path for c in '<> {}'):
            skipped.append(dict(source=path, reference=relative, reason='not_a_local_literal_path'))
            continue
        candidates = set()
        for candidate in ((root / path).parent / unquote(url.path), root / unquote(url.path)):
            try:
                target = candidate.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
            if target in known:
                candidates.add(target)
        if len(candidates) == 1:
            target = next(iter(candidates))
            if target != path:
                targets.add(target)
        else:
            skipped.append(dict(source=path, reference=relative,
                                reason='ambiguous' if candidates else 'unresolved_or_outside_NONatlas'))
    return targets, skipped


def build(root: Path) -> tuple[dict, bytes, dict]:
    original_skill = (root / 'SKILL.md').read_bytes()
    skill = stage_skill(original_skill)
    paths = sorted(p.relative_to(root).as_posix() for p in root.rglob('*.md')
                   if not p.relative_to(root).as_posix().startswith(ATLAS))
    resources, contents, preservation = [], {}, []
    for path in paths + [GENERATED]:
        original = (root / path).read_bytes()
        data = skill if path == 'SKILL.md' else original
        text = data.decode('utf-8')
        title = next((line.lstrip('# ').strip() for line in text.splitlines()
                      if re.match(r'^#{1,6}\s', line)), path)
        role = ('canonical' if path == 'SKILL.md' else
                'legacy_router' if path == 'references/legacy-skill-router.md' else
                'generated' if path == GENERATED else 'operational_guide')
        tags = architecture_tags(path)
        aliases = list(dict.fromkeys([title, path] + tags))
        resources.append(dict(id=resource_id(path), path=path, title=title,
                              sha256=digest(data), bytes=len(data), role=role,
                              index_excluded=path == GENERATED, tags=tags,
                              aliases=aliases, lineage=[]))
        contents[path] = text
        preservation.append(dict(path=path, source_sha256=digest(original),
                                 staged_sha256=digest(data), unchanged=data == original))
    known = set(paths)
    edges, skipped = [], []
    for path in paths:
        references, unresolved = backtick_references(root, path, contents[path], known)
        skipped.extend(unresolved)
        for target in sorted(local_links(root, path, contents[path], known) | references):
            edges.append({'from': resource_id(path), 'to': resource_id(target), 'type': 'linked_to'})
    corpus = dict(schema_version=1, resources=resources, relationships=edges)
    receipt = dict(schema_version=1, mode='staged_only', source_root=str(root.resolve()),
                   markdown_count=len(paths), resource_count=len(resources),
                   relationship_count=len(edges), roles=dict(sorted(Counter(r['role'] for r in resources).items())),
                   skipped_references=skipped,
                   preservation=preservation,
                   atlas_resources_excluded=True, inferred_prerequisites=0,
                   lineage_policy='Empty: no evidenced equivalence to existing canonical IDs',
                   navigation_policy='No inspected NONatlas file is a pure inventory; mixed routers remain operational guides',
                   architecture_policy='Architecture directory or v100/volta filename only; generic files neutral',
                   limitations=['Literal inline Markdown links and unambiguous exact backtick Markdown paths only; reference-style links are not inferred',
                                'No atlas IDs, resources, or semantic edges are duplicated',
                                'Regenerate metadata if final integration changes referenced bytes, including generated assets',
                                'Root owns final installation, qualification, and commit'])
    assert all(r['unchanged'] for r in preservation if r['path'] != 'SKILL.md')
    return corpus, skill, receipt


def dump(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.source.resolve()):
        raise ValueError('output must be outside source skill')
    corpus, skill, receipt = build(args.source)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'SKILL.md').write_bytes(skill)
    dump(args.output / '.project-control-corpus.json', corpus)
    dump(args.output / 'receipt.json', receipt)
    print(json.dumps({k: receipt[k] for k in ('mode', 'markdown_count', 'resource_count', 'relationship_count', 'roles')}))


if __name__ == '__main__':
    main()
