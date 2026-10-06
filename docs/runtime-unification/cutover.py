#!/usr/bin/env python3
"""Operator-authorized entrypoint cutover. Never edits workspace authority."""
from pathlib import Path
import argparse, os, shutil, subprocess, json, hashlib, datetime, tomllib, re
ROOT=Path('/home/tumlinson/project-control')
HOME=Path('/home/tumlinson')
BACKUP=HOME/'.local/share/project-control/rollbacks/runtime-unification-20261006T2005'
def atomic_copy(source,dest,mode):
    dest.parent.mkdir(parents=True,exist_ok=True)
    tmp=dest.with_name(dest.name+'.unification-new')
    shutil.copyfile(source,tmp); tmp.chmod(mode); os.replace(tmp,dest)
def move_preserved(path,label):
    if path.exists() or path.is_symlink():
        target=BACKUP/'retired-entrypoints'/label
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists(): raise RuntimeError(f'backup target already exists: {target}')
        shutil.move(str(path),str(target))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('release',type=Path); args=ap.parse_args()
    release=args.release.resolve()
    if not (release/'bin/project-control-release').is_file(): raise RuntimeError('release missing')
    manifest=json.loads((release/'release-manifest.json').read_text())
    if manifest.get('schema_version')!=2 or not manifest.get('local_runtime_binding'): raise RuntimeError('strict release binding missing')
    if not (ROOT/'docs/runtime-unification/state-backup.json').exists(): raise RuntimeError('state backup receipt missing')
    changes=[]
    # Preserve the entire config and change only its default HTTP port.
    config=HOME/'.config/project-control/config.toml'; before=config.read_text()
    updated,n=re.subn(r'(\[server\]\s*\n(?:(?!\[).)*?\bport\s*=\s*)8767\b',r'\g<1>8768',before,count=1,flags=re.S)
    parsed_before=tomllib.loads(before); parsed_after=tomllib.loads(updated)
    expected=json.loads(json.dumps(parsed_before)); expected['server']['port']=8768
    if parsed_after!=expected: raise RuntimeError('config change exceeded port alignment')
    changes.append((config,before,updated,0o600))
    # The standard launcher supplies the release-bound Skills path; remove old MCP env override only.
    codex=HOME/'.codex/config.toml'; before=codex.read_text()
    parsed_before=tomllib.loads(before)
    updated=re.sub(r'(\[mcp_servers\.project-control\.env\]\s*\n)(?:(?!\[).)*',lambda m: re.sub(r'^PROJECT_CONTROL_SKILLS_ROOT\s*=.*\n?','',m.group(0),flags=re.M),before,flags=re.S)
    parsed_after=tomllib.loads(updated); expected=parsed_before.copy()
    expected=json.loads(json.dumps(expected)); expected['mcp_servers']['project-control'].get('env',{}).pop('PROJECT_CONTROL_SKILLS_ROOT',None)
    if parsed_after!=expected: raise RuntimeError('Codex configuration changed beyond stale Skills override')
    changes.append((codex,before,updated,codex.stat().st_mode & 0o777))
    units=HOME/'.config/systemd/user'
    for name in ('project-control.service','project-control-as1.service','project-control-inference.service'):
        state=subprocess.check_output(['systemctl','--user','show',name,'--property=ActiveState','--value'],text=True).strip()
        if state not in ('inactive','failed'): raise RuntimeError(f'old unit not stopped: {name}: {state}')
    subprocess.run(['systemctl','--user','disable','project-control-as1.service','project-control-inference.service'],check=True)
    move_preserved(units/'project-control-as1.service','project-control-as1.service')
    move_preserved(units/'project-control-as1.service.d','project-control-as1.service.d')
    # The prior tunnel hook requires inference readiness, deliberately held off overnight.
    move_preserved(units/'project-control.service.d/refresh-remote-tunnel.conf','refresh-remote-tunnel.conf')
    move_preserved(units/'project-control.service.d/wf2-execution.conf','wf2-execution.conf')
    move_preserved(units/'project-control-inference.service','project-control-inference.service')
    move_preserved(units/'project-control-inference.service.d','project-control-inference.service.d')
    current=HOME/'.local/share/project-control/current'
    if current.exists() or current.is_symlink():
        if not current.is_symlink(): raise RuntimeError('current selector is not a symlink')
        (BACKUP/'previous-current.txt').write_text(os.readlink(current)+'\n')
    new=current.with_name('current.unification-new'); new.symlink_to(release); os.replace(new,current)
    artifacts=ROOT/'docs/runtime-unification/entrypoints'
    atomic_copy(artifacts/'project-control',HOME/'.local/bin/project-control',0o755)
    atomic_copy(artifacts/'shell-env.sh',HOME/'.config/project-control/shell-env.sh',0o644)
    atomic_copy(ROOT/'deployment/project-control.service',units/'project-control.service',0o644)
    atomic_copy(artifacts/'project-control-inference',HOME/'.local/bin/project-control-inference',0o755)
    atomic_copy(ROOT/'deployment/project-control-inference.service',units/'project-control-inference.service',0o644)
    for path,before,updated,mode in changes:
        if path.read_text()!=before: raise RuntimeError(f'configuration changed during cutover: {path}')
        if updated!=before:
            tmp=path.with_name(path.name+'.unification-new'); tmp.write_text(updated); tmp.chmod(mode); os.replace(tmp,path)
    # Retire the failed disposable development environment, retaining it for provenance.
    dev=ROOT/'.venv'
    move_preserved(dev,'partial-development-venv')
    dev.symlink_to(current,target_is_directory=True)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','enable','project-control.service'],check=True)
    subprocess.run(['systemctl','--user','start','project-control.service'],check=True)
    record={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'release':str(release),'release_manifest_sha256':hashlib.sha256((release/'release-manifest.json').read_bytes()).hexdigest(),'current':str(current),'cli':str(HOME/'.local/bin/project-control'),'service':'project-control.service','legacy_as1':'alias of project-control.service','inference':'disabled and inactive','rollback':str(BACKUP),'config_change':'server.port 8767 to8768; all workspace settings preserved','codex_change':'remove stale live Skills override; same standard launcher','development_env':'canonical .venv alias of current release; no uv sync against this alias'}
    (ROOT/'docs/runtime-unification/cutover.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record))
if __name__=='__main__': main()
