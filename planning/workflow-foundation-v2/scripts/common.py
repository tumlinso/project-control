"""Local package primitives; no workflow writes or implicit runtime discovery."""
from __future__ import annotations
import hashlib, json, os, re, subprocess, time
from pathlib import Path
from typing import Any

class PackageError(ValueError):
    pass

def demand(condition: object, message: str) -> None:
    if not condition:
        raise PackageError(message)

def _pairs(pairs):
    result = {}
    for key, value in pairs:
        demand(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result

def load(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_pairs)

def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))+'\n').encode('utf-8')

def digest(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def object_digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()

def relpath(value: str) -> str:
    demand(isinstance(value, str) and value and '\0' not in value, 'relative path must be a nonempty text value')
    p = Path(value)
    demand(not p.is_absolute() and '..' not in p.parts and value != '.', f'unsafe relative path: {value}')
    demand('\\' not in value, 'backslash path is not portable')
    return p.as_posix()

def contained(root: Path, relative: str, *, exists: bool = True) -> Path:
    relative = relpath(relative)
    root = root.resolve()
    p = root / relative
    for q in [p, *p.parents]:
        if q == root: break
        demand(not q.is_symlink(), f'symlink is not permitted here: {relative}')
    demand(p.resolve().is_relative_to(root), f'path escapes root: {relative}')
    if exists: demand(p.is_file(), f'missing file: {relative}')
    return p

def new_output(path: Path, roots=()) -> Path:
    p = path.absolute()
    demand(p.parent.is_dir(), 'output parent must already exist')
    demand(not p.exists() and not p.is_symlink(), 'output exists; preserve previous evidence')
    resolved = p.parent.resolve() / p.name
    for root in roots:
        demand(not resolved.is_relative_to(Path(root).resolve()), 'runtime evidence must be outside repository/package roots')
    return resolved

def write_new(path: Path, value: Any) -> None:
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n'); f.flush(); os.fsync(f.fileno())

def call(argv, *, cwd=None, timeout=180, env=None) -> str:
    result = subprocess.run([str(x) for x in argv], cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise PackageError(f'command failed ({result.returncode}): {argv[0]}\n{result.stderr[-8000:]}\n{result.stdout[-8000:]}')
    return result.stdout

def git(root: Path, *args: str) -> str:
    return call(['git', '-C', root, *args]).strip()

def source_snapshot(root: Path, reviewed_head: str | None = None, *, allow_dirty=False) -> dict:
    root = root.resolve(strict=True)
    demand(Path(git(root, 'rev-parse', '--show-toplevel')).resolve() == root, 'explicit repository root required')
    head = git(root, 'rev-parse', 'HEAD')
    demand(re.fullmatch(r'[0-9a-f]{40,64}', head), 'invalid Git HEAD')
    if reviewed_head is not None: demand(reviewed_head == head, 'reviewed HEAD differs from current HEAD')
    status = git(root, 'status', '--porcelain=v1', '--untracked-files=all')
    demand(allow_dirty or not status, 'dirty work: inspect/preserve; an explicit --acknowledge-dirty is required')
    diff = subprocess.run(['git','-C',str(root),'diff','--binary','HEAD'],check=True,capture_output=True).stdout
    names = subprocess.run(['git','-C',str(root),'ls-files','--others','--exclude-standard','-z'],check=True,capture_output=True).stdout
    untracked = {}
    for raw in names.split(b'\0'):
        if not raw: continue
        name = os.fsdecode(raw); p = root/name
        if p.is_symlink(): untracked[name] = {'symlink_target':os.readlink(p)}
        elif p.is_file(): untracked[name] = {'sha256':digest(p)}
    return {'root':str(root),'head':head,'status':status,'tracked_diff_sha256':hashlib.sha256(diff).hexdigest(),'untracked':untracked}

def check_age(created: float, *, now: float | None=None, ttl=3600) -> None:
    age = (time.time() if now is None else now)-created
    demand(0 <= age <= ttl, 'reviewed preview expired or is future-dated; obtain a new preview')

def dag(nodes, edges):
    nodes = set(nodes); edges = set(tuple(e) for e in edges)
    outgoing={n:set() for n in nodes}; degree={n:0 for n in nodes}
    for a,b in edges:
        demand(a in nodes and b in nodes, f'unknown graph edge {a} -> {b}')
        outgoing[a].add(b); degree[b]+=1
    levels=[]; ready=sorted(n for n in nodes if degree[n]==0); seen=[]
    while ready:
        levels.append(ready); following=[]
        for node in ready:
            seen.append(node)
            for other in sorted(outgoing[node]):
                degree[other]-=1
                if degree[other]==0: following.append(other)
        ready=sorted(following)
    demand(len(seen)==len(nodes), 'dependency/queue cycle: '+', '.join(sorted(nodes-set(seen))))
    reach={n:set() for n in nodes}
    for n in reversed(seen):
        for other in outgoing[n]: reach[n].add(other);reach[n].update(reach[other])
    return levels,reach

def path_overlap(a: str,b: str) -> bool:
    a=a.rstrip('/');b=b.rstrip('/')
    return a==b or a.startswith(b+'/') or b.startswith(a+'/')
