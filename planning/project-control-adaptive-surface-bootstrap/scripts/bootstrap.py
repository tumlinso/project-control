#!/usr/bin/env python3
"""Safe package checks/staging and explicit additive native-plan bootstrap."""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path
from typing import Any
from check_package import ROOT, check, relative_path, verify_manifest
DEST = Path('planning/adaptive-surface-v1')


def no_symlinks(root: Path, relative: str) -> Path:
    relative=relative_path(relative)
    p=root
    for part in Path(relative).parts:
        p=p/part
        if p.is_symlink(): raise ValueError(f'symlink rejected: {relative}')
    return p


def repository_root(repo: Path) -> Path:
    repo=repo.expanduser().resolve(strict=True)
    if not repo.is_dir(): raise ValueError('repository directory required')
    result=subprocess.run(['git','-C',str(repo),'rev-parse','--show-toplevel'],capture_output=True,text=True,timeout=10)
    if result.returncode or Path(result.stdout.strip()).resolve()!=repo:
        raise ValueError('supply the exact Git checkout root')
    return repo


def manifest_files(package: Path) -> list[str]:
    verify_manifest(package,required=True)
    return [line.split('  ',1)[1] for line in (package/'MANIFEST.sha256').read_text().splitlines()]+['MANIFEST.sha256']


def atomic_new_file(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.as1-stage-',dir=destination.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(content);stream.flush();os.fsync(stream.fileno())
        os.link(temp,destination)  # Atomic create, never overwrites a concurrent file.
    finally:
        Path(temp).unlink(missing_ok=True)


def stage(package: Path, repo: Path) -> dict[str,Any]:
    check(package,require_manifest=True)
    destination=no_symlinks(repo,DEST.as_posix())
    names=manifest_files(package); pending=[]
    # Check every existing target before writing any file.
    for name in names:
        target=no_symlinks(repo,(DEST/name).as_posix());source=package/name
        if target.exists():
            if not target.is_file() or target.read_bytes()!=source.read_bytes():
                raise ValueError(f'existing different file; not overwritten: {target}')
        else: pending.append((name,source.read_bytes()))
    for name,content in pending:
        target=no_symlinks(repo,(DEST/name).as_posix())
        try: atomic_new_file(target,content)
        except FileExistsError:
            if target.is_symlink() or target.read_bytes()!=content: raise
    return {'status':'staged','path':str(destination),'created_files':len(pending),'unchanged_files':len(names)-len(pending),'product_or_todo_changed':False}


def state_root(repo: Path, project: str, custom: Path|None) -> Path:
    base=custom.expanduser().resolve() if custom else Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state'))).expanduser().resolve()/'project-control/as1-bootstrap'
    # Receipt writes must not change the source identity they attest.
    if base==repo or repo in base.parents: raise ValueError('bootstrap state must be outside the repository')
    key=hashlib.sha256((project+'\0'+str(repo)).encode()).hexdigest()[:20]
    folder=base/key
    if folder.is_symlink(): raise ValueError('bootstrap state symlink rejected')
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    return folder


def write_receipt(path: Path, value: dict[str,Any]) -> None:
    if path.is_symlink(): raise ValueError('receipt symlink rejected')
    fd,tmp=tempfile.mkstemp(prefix='.receipt-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as s:
            json.dump(value,s,indent=2,sort_keys=True);s.write('\n');s.flush();os.fsync(s.fileno())
        os.replace(tmp,path)
    finally: Path(tmp).unlink(missing_ok=True)


def native(action: str, package: Path, repo: Path, project: str, runtime_python: str, folder: Path) -> dict[str,Any]:
    check(package,require_manifest=True)
    staged=repo/DEST;check(staged,require_manifest=True)
    plan=staged/f'planning/{project}.todo-plan.json'
    receipt=folder/'native-validation.json'
    if action=='apply' and not receipt.exists(): raise ValueError('run explicit native validation first')
    argv=[runtime_python,str(package/'scripts/native_bridge.py'),action,'--repo',str(repo),'--project',project,'--plan',str(plan)]
    if action=='apply': argv+=['--receipt',str(receipt)]
    process=subprocess.run(argv,capture_output=True,text=True,timeout=180,cwd=repo)
    try: result=json.loads(process.stdout)
    except json.JSONDecodeError:
        raise ValueError('native bridge did not return JSON; verify the bound runtime Python') from None
    if process.returncode:
        raise ValueError('native operation failed: '+str(result.get('error','unknown')))
    body=result.get('result',{})
    if action=='validate' and (not body.get('valid') or body.get('would_modify')):
        write_receipt(folder/'native-validation-rejected.json',result)
        raise ValueError('native validation invalid or would modify existing tasks; inspect rejected receipt')
    target=receipt if action=='validate' else folder/'native-application.json'
    write_receipt(target,result)
    return {'status':{'validate':'validated','apply':'applied'}[action],'receipt':str(target),'native_result':body}


def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='action',required=True)
    for action in ['check','stage','validate','apply']:
        p=sub.add_parser(action);p.add_argument('--package',type=Path,default=ROOT)
        if action!='check':
            p.add_argument('--repo',type=Path,required=True);p.add_argument('--project',choices=['project-control','skills'],required=True)
        if action in ('validate','apply'):
            p.add_argument('--runtime-python',default=sys.executable);p.add_argument('--state-dir',type=Path)
        if action=='apply': p.add_argument('--allow-additive-apply',action='store_true')
    args=ap.parse_args()
    try:
        package=args.package.resolve(strict=True)
        if args.action=='check': out=check(package,require_manifest=True)
        else:
            repo=repository_root(args.repo)
            if args.action=='stage': out=stage(package,repo)
            else:
                if args.action=='apply' and not args.allow_additive_apply: raise ValueError('explicit --allow-additive-apply is required')
                out=native(args.action,package,repo,args.project,args.runtime_python,state_root(repo,args.project,args.state_dir))
        print(json.dumps(out,sort_keys=True,default=str));return 0
    except Exception as e:
        print(json.dumps({'status':'failed','error':str(e),'live_state_not_reset':True},sort_keys=True));return 1

if __name__=='__main__': raise SystemExit(main())
