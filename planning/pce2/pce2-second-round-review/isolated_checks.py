#!/usr/bin/env python3
"""Isolated evidence for PCE2 review; NOT the project's regression suite.

The checks copy the inspected algorithms/SQL at Skills e6c1fc8 and PC 40f80f3.
They use disposable real Git, files, HMAC and SQLite. Readiness inputs and the
already-committed recovery engine are explicit stubs. No live project state,
installed server, full WorkflowKernel, or model execution is used.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent

def git(root: Path, *args: str) -> bytes:
    return subprocess.run(['git', *args], cwd=root, capture_output=True, check=True).stdout

def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def init_git(root: Path) -> None:
    git(root, 'init', '-q')
    git(root, 'config', 'user.email', 'review@example.invalid')
    git(root, 'config', 'user.name', 'Isolated Review')

# Copied algorithm from runtime/source.py, with output normalization omitted.
# Normalization validates/returns the generated fingerprint; it does not hash
# file contents again. Git and content hashing below are real.
def source_dirty_paths(status: bytes) -> list[str]:
    entries = status.split(b'\0')
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        state = entry[:2].decode('ascii', errors='replace')
        path = entry[3:].decode('utf-8', errors='surrogateescape')
        if 'R' in state or 'C' in state:
            if index < len(entries) and entries[index]:
                path = entries[index].decode('utf-8', errors='surrogateescape')
                index += 1
        paths.append(path)
    return sorted(set(paths))

def path_hash(root: Path, relative: str) -> str | None:
    path = root / relative
    try:
        if path.is_symlink():
            payload = b'symlink\0' + os.readlink(path).encode('utf-8', errors='surrogateescape')
        elif path.is_file():
            payload = path.read_bytes()
        else:
            return None
    except OSError:
        return None
    return digest(payload)

def source_identity(root: Path) -> dict[str, Any]:
    status = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    paths = source_dirty_paths(status)
    payload = {
        'git_head': git(root, 'rev-parse', '--verify', 'HEAD').decode().strip(),
        'status_sha256': digest(status),
        'files': [{'path': path, 'sha256': path_hash(root, path)} for path in paths],
    }
    return {'fingerprint': digest(canonical(payload)), 'parsed_paths': paths,
            'porcelain': repr(status), 'files': payload['files']}

def rename_fingerprint_check() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); init_git(root)
        (root/'old.txt').write_text('one\ntwo\nthree\n')
        git(root, 'add', 'old.txt'); git(root, 'commit', '-qm', 'base')
        git(root, 'mv', 'old.txt', 'new.txt')
        (root/'new.txt').write_text('one\ntwo\nthree\nfirst edit\n')
        first = source_identity(root); first['destination_hash'] = path_hash(root, 'new.txt')
        (root/'new.txt').write_text('one\ntwo\nthree\nsecond edit\n')
        second = source_identity(root); second['destination_hash'] = path_hash(root, 'new.txt')
        assert first['destination_hash'] != second['destination_hash']
        assert first['fingerprint'] == second['fingerprint']
        # Negative control: without a rename, changing tracked bytes is detected.
        git(root, 'add', 'new.txt'); git(root, 'commit', '-qm', 'rename committed')
        (root/'new.txt').write_text('third edit\n'); normal_a = source_identity(root)
        (root/'new.txt').write_text('fourth edit\n'); normal_b = source_identity(root)
        assert normal_a['fingerprint'] != normal_b['fingerprint']
        return {'counterexample_observed': True, 'first': first, 'second': second,
                'ordinary_modified_file_control': 'change detected'}

# Actual lane candidate/picker SQL with readiness deliberately fixed true for
# both disjoint tasks. This isolates ordering from actual eligibility.
def lane_head(conn: sqlite3.Connection, lane_id: str):
    return conn.execute("SELECT * FROM workflow_lane_tasks WHERE lane_id=? AND state NOT IN ('completed','cancelled','skipped') ORDER BY position LIMIT 1", (lane_id,)).fetchone()

def candidates(conn: sqlite3.Connection, run_id: str, role: str):
    result = []
    for lane in conn.execute("SELECT * FROM workflow_lanes WHERE run_id=? AND state IN ('ready','active') AND (? IS NULL OR role=?) ORDER BY id", (run_id, role, role)):
        if conn.execute("SELECT 1 FROM workflow_dispatches WHERE lane_id=? AND state='active'", (lane['id'],)).fetchone():
            continue
        head = lane_head(conn, lane['id'])
        if not head or head['state'] != 'queued':
            continue
        # Stub: both tasks' independent canonical readiness is true.
        task = conn.execute('SELECT priority,created_at FROM tasks WHERE id=?', (head['task_id'],)).fetchone()
        result.append({'run_id':run_id, 'lane_id':lane['id'], 'role':lane['role'],
                       'task_id':head['task_id'], 'position':head['position'],
                       'priority':task['priority'], 'created_at':task['created_at']})
    return sorted(result, key=lambda item: (-int(item['priority']),int(item['position']),str(item['lane_id']),str(item['task_id'])))

def lane_assessment_check() -> dict[str, Any]:
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    try:
        conn.executescript('''
        CREATE TABLE tasks(id TEXT,priority INTEGER,created_at TEXT);
        CREATE TABLE workflow_lanes(id TEXT,run_id TEXT,role TEXT,state TEXT);
        CREATE TABLE workflow_lane_tasks(lane_id TEXT,position INTEGER,task_id TEXT,state TEXT);
        CREATE TABLE workflow_dispatches(lane_id TEXT,state TEXT);
        INSERT INTO tasks VALUES('A',20,'now'),('B',10,'now');
        INSERT INTO workflow_lanes VALUES('LA','R','implementer','ready'),('LB','R','implementer','ready');
        INSERT INTO workflow_lane_tasks VALUES('LA',0,'A','queued'),('LB',0,'B','queued');
        ''')
        eligible = candidates(conn, 'R', 'implementer')
        candidate = eligible[0]
        # Copied assess_continuation comparison.
        rejected = candidate['lane_id'] != 'LB' or candidate['task_id'] != 'B'
        # Actual dispatch's lane-head predicate for an explicitly chosen B.
        head = lane_head(conn, 'LB')
        own_head_valid = bool(head and head['task_id']=='B' and head['state'] in {'queued','active'})
        assert rejected and own_head_valid
        return {'counterexample_observed':True, 'eligible_candidates':eligible,
                'requested_task':'B','assessment_blocker':'not_serial_lane_head',
                'explicit_dispatch_lane_head_predicate':own_head_valid,
                'limitation':'Readiness fixed true; no full claim or WorkflowKernel execution.'}
    finally: conn.close()

class RecoveryAuthorizationError(ValueError): pass

def read_signed_record(service, authorization_id):
    root = service.paths.state_dir/'project-control-recovery-authorizations'
    record = json.loads((root/f'{authorization_id}.json').read_text())
    payload = record['payload']
    expected = hmac.new((root/'.signing-key').read_bytes(), canonical(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(record['signature'], expected):
        raise RecoveryAuthorizationError('recovery_authorization_invalid')
    return record,payload

# The relevant complete preflight checks from PC recovery.py:196-233.
def inspect_maintenance(service, engine, authorization_id, recipient_principal):
    if not authorization_id.startswith('rca_') or '/' in authorization_id or '\\' in authorization_id:
        raise RecoveryAuthorizationError('recovery_authorization_invalid')
    record,payload = read_signed_record(service,authorization_id)
    if payload.get('format') != 'project-control-recovery-authorization-v2':
        raise RecoveryAuthorizationError('recovery_authorization_legacy_only')
    if not recipient_principal or not hmac.compare_digest(str(payload.get('recipient_principal','')), recipient_principal):
        raise RecoveryAuthorizationError('recovery_authorization_principal_mismatch')
    if payload.get('project_uuid') != str(engine.project_uuid):
        raise RecoveryAuthorizationError('recovery_authorization_project_mismatch')
    if payload.get('repository_root') != str(service.paths.repo_root.resolve()):
        raise RecoveryAuthorizationError('recovery_authorization_target_mismatch')
    if record.get('revoked') is True:
        raise RecoveryAuthorizationError('recovery_authorization_revoked')
    completed = record.get('completed_receipt')
    if completed is not None:
        if not isinstance(completed,dict): raise RecoveryAuthorizationError('recovery_authorization_invalid')
        return {'payload':payload,'completed_receipt':dict(completed)}
    expires_at = datetime.fromisoformat(str(payload['expires_at']))
    if datetime.now(timezone.utc) >= expires_at:
        raise RecoveryAuthorizationError('recovery_authorization_expired')
    return {'payload':payload}

def replay_expiry_check() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); private = root/'project-control-recovery-authorizations'; private.mkdir()
        key = b'fixture-key-only-never-a-production-key'; (private/'.signing-key').write_bytes(key)
        payload = {'format':'project-control-recovery-authorization-v2','project_uuid':'P',
                   'repository_root':str(root.resolve()),'recipient_principal':'op','task_id':'A',
                   'expires_at':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()}
        record = {'payload':payload,'signature':hmac.new(key,canonical(payload),hashlib.sha256).hexdigest()}
        file = private/'rca_test.json'; file.write_text(json.dumps(record))
        service = SimpleNamespace(paths=SimpleNamespace(state_dir=root,repo_root=root))
        engine = SimpleNamespace(project_uuid='P', canonical_queries=0)
        receipt = {'status':'recovered','recovery_request_id':'rca_test','actions_applied':1}
        def canonical_result(_):
            engine.canonical_queries += 1
            return receipt
        engine.recovery_result_for_request = canonical_result
        error = None
        # Public admin preflight ordering; the lower canonical replay follows it.
        try:
            inspect_maintenance(service,engine,'rca_test','op')
            engine.recovery_result_for_request('rca_test')
        except RecoveryAuthorizationError as exc: error = str(exc)
        assert error == 'recovery_authorization_expired' and engine.canonical_queries == 0
        file.write_text(json.dumps({**record,'completed_receipt':receipt}))
        projected = inspect_maintenance(service,engine,'rca_test','op')
        assert projected['completed_receipt'] == receipt
        return {'counterexample_observed':True,'public_preflight_error':error,
                'canonical_receipt_available':True,'canonical_lookups_before_rejection':0,
                'private_receipt_present_control':'replays after expiry',
                'limitation':'Canonical recovery engine is a stub; no remote public MCP server executed.'}

# Exact query/ownership-policy composition, not full RecoveryEngine execution.
def workspace_scope_check() -> dict[str, Any]:
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    try:
        conn.executescript('''
        CREATE TABLE workflow_workspaces(id TEXT,run_id TEXT,lane_id TEXT,state TEXT,integration_task_id TEXT);
        CREATE TABLE claims(id TEXT,task_id TEXT);
        CREATE TABLE workflow_dispatches(id TEXT,lane_id TEXT,workspace_id TEXT,claim_id TEXT,state TEXT);
        INSERT INTO workflow_workspaces VALUES('WP','R','LP','active','I');
        INSERT INTO claims VALUES('CP','P'),('CI','I');
        INSERT INTO workflow_dispatches VALUES('DP','LP','WP','CP','active'),('DI','LI',NULL,'CI','active');
        ''')
        # Engine considers I's stopped dispatch, not P's live dispatch.
        selected = [dict(r) for r in conn.execute(
            "SELECT DISTINCT w.* FROM workflow_workspaces w LEFT JOIN workflow_dispatches d ON d.workspace_id=w.id LEFT JOIN claims c ON c.id=d.claim_id WHERE w.state IN ('active','dirty','conflicted') AND (w.integration_task_id=? OR c.task_id=?) ORDER BY w.id",('I','I'))]
        assert [r['id'] for r in selected] == ['WP']
        actions = [{'kind':'retire_dispatch','id':'DI','lane_id':'LI','task_id':'I'},
                   {'kind':'quarantine_workspace','id':'WP','workspace_id':'WP','task_id':'I','state':'active'}]
        # Same allowed-kind/task comparison as delegated_effects_are_exact.
        allowed = {'requeue_expired_coordinator','expire_and_requeue_coordinator','retire_dispatch','release_claim','terminalize_dead_child','release_resource','release_lock','quarantine_workspace','retire_capability','requeue_blocked_task','finalize_terminal_checkpoints'}
        policy_accepts = all(a['kind'] in allowed and a['task_id']=='I' for a in actions)
        lanes = {a['lane_id'] for a in actions if a.get('lane_id')}
        workspaces = {a['workspace_id'] for a in actions if a.get('workspace_id')}
        assert len(lanes)==1 and len(workspaces)==1 and policy_accepts
        # delegated_continuation preferentially takes workspace's lane without
        # comparing it to the lane set. Assume its Git contents remain clean.
        continuation_lane = selected[0]['lane_id']
        assert continuation_lane != next(iter(lanes))
        return {'counterexample_observed':True,'requested_task':'I','workspace_owner_task':'P',
                'selected_workspace':'WP','policy_labels_accept':policy_accepts,
                'requested_dispatch_lane':'LI','derived_continuation_lane':continuation_lane,
                'same_run_check_still_passes':True,
                'limitation':'SQL/effect-policy/continuation-selection check; no full recovery mutation or process probe executed.'}
    finally: conn.close()

def adopted_integration_check() -> dict[str, Any]:
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    try:
        conn.executescript('''
        CREATE TABLE workflow_lanes(id TEXT,run_id TEXT,role TEXT);
        CREATE TABLE workflow_lane_tasks(lane_id TEXT,task_id TEXT);
        INSERT INTO workflow_lanes VALUES('NEW-P','NEW-R','implementer'),('NEW-I','NEW-R','integrator');
        INSERT INTO workflow_lane_tasks VALUES('NEW-P','NEW'),('NEW-I','INT');
        ''')
        # retirement.py sets integration_task_id to successor_task_id.
        adopted_integration_task = 'NEW'
        query = "SELECT l.id FROM workflow_lanes l JOIN workflow_lane_tasks lt ON lt.lane_id=l.id WHERE l.run_id=? AND l.role IN ('integrator','validator') AND lt.task_id=? ORDER BY l.id LIMIT 1"
        found = conn.execute(query,('NEW-R',adopted_integration_task)).fetchone()
        correct = conn.execute(query,('NEW-R','INT')).fetchone()
        assert found is None and correct is not None
        return {'counterexample_observed':True,'successor_producer':'NEW',
                'adopted_workspace_integration_task_id':adopted_integration_task,
                'actual_successor_integrator_task':'INT','publisher_lookup':None,
                'expected_failure':'integrator_lane_missing',
                'limitation':'Exact SQL on normal lane ownership; no full finish/publication executed.'}
    finally: conn.close()


def typed_consumer_check() -> dict[str, Any]:
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    try:
        conn.executescript("""
        CREATE TABLE tasks(id TEXT,status TEXT);
        CREATE TABLE checkpoints(id TEXT,task_id TEXT,state TEXT);
        CREATE TABLE task_dependencies(task_id TEXT,type TEXT,prerequisite_task_id TEXT,checkpoint_id TEXT,condition_json TEXT);
        CREATE TABLE interfaces(id TEXT,owner_task_id TEXT);
        CREATE TABLE interface_consumers(interface_id TEXT,task_id TEXT);
        INSERT INTO tasks VALUES('OLD','planned'),('NEW','planned'),('EXTERNAL','planned');
        INSERT INTO checkpoints VALUES('OLD-READY','OLD','pending');
        INSERT INTO task_dependencies VALUES('EXTERNAL','checkpoint',NULL,'OLD-READY','{}');
        """)
        # The two actual outgoing-consumer queries in retirement.py.
        direct = conn.execute("SELECT task_id,prerequisite_task_id FROM task_dependencies WHERE prerequisite_task_id IN (?) AND task_id NOT IN (?)", ('OLD','OLD')).fetchall()
        interface = conn.execute("SELECT i.id AS interface_id,ic.task_id,i.owner_task_id FROM interfaces i JOIN interface_consumers ic ON ic.interface_id=i.id JOIN tasks t ON t.id=ic.task_id WHERE i.owner_task_id IN (?) AND ic.task_id NOT IN (?) AND t.status NOT IN ('done','superseded','cancelled','stale')", ('OLD','OLD')).fetchall()
        checkpoint_consumers = conn.execute("SELECT d.task_id,c.task_id AS producer,c.state FROM task_dependencies d JOIN checkpoints c ON c.id=d.checkpoint_id WHERE c.task_id='OLD' AND d.task_id<>'OLD'").fetchall()
        assert len(direct)==len(interface)==0 and len(checkpoint_consumers)==1
        return {'counterexample_observed':True,'outgoing_guard_results':[],
                'real_pending_checkpoint_consumer':dict(checkpoint_consumers[0]),
                'limitation':'Typed-reference SQL check; not a full retirement transaction.'}
    finally: conn.close()


def main():
    result = {
        'review_date':'2026-09-21','type':'isolated_algorithm_counterexamples_not_product_tests',
        'code_endpoints':{'project-control':'40f80f3','skills':'e6c1fc8'},
        'checks':{
            'adoption_rename_fingerprint':rename_fingerprint_check(),
            'continuation_lane_ranking':lane_assessment_check(),
            'public_recovery_preflight_after_expiry':replay_expiry_check(),
            'integration_relation_is_not_workspace_ownership':workspace_scope_check(),
            'adopted_producer_integration_target':adopted_integration_check(),
            'retirement_typed_checkpoint_consumer':typed_consumer_check(),
        },
        'remote_project_tests_run':False,'live_mutations':False,
    }
    (ROOT/'isolated_results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__ == '__main__': main()
