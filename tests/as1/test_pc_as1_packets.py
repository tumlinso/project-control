"""Durable store acceptance uses real SQLite, restart and independent callers."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from project_control.as1_contracts import SourceLocator, canonical_digest
from project_control.as1_packets import SQLitePacketStore, WORDS

SCOPE = {'principal': 'alice', 'profile': 'observer', 'project': 'pc'}


def issue(store, **kwargs):
    return store.create(tool='search', payload=kwargs.pop('payload', {'text': 'exact source span\nsecond line'}),
                        access_scope=SCOPE, **kwargs)


@pytest.mark.as1_case('PKT-01')
def test_exact_payload_survives_process_restart_and_masks_private_fields(tmp_path):
    payload = {'text': 'legitimate token grammar and command strings', 'token': 'domain concept',
               'authorization': 'Bearer abc.def', 'nested': {'workflow_handle': 'opaque-secret'},
               'hidden_reasoning': 'private thoughts', 'stderr': 'Bearer top-secret'}
    store = SQLitePacketStore(tmp_path)
    p = issue(store, payload=payload, audit_id='audit-001', normalized_request={'query': 'token grammar'})
    payload['text'] = 'caller mutation'
    p.payload['text'] = 'returned model mutation'
    code = '''import json,sys
from project_control.as1_packets import SQLitePacketStore, WORDS
s=SQLitePacketStore(sys.argv[1]); p=s.resolve(sys.argv[2], access_scope=json.loads(sys.argv[3]))
print(p.model_dump_json())'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path), p.alias, json.dumps(SCOPE)], check=True, capture_output=True, text=True)
    restored = json.loads(result.stdout)
    assert restored['payload']['text'] == 'legitimate token grammar and command strings'
    assert restored['payload']['token'] == 'domain concept'
    assert restored['payload']['nested']['workflow_handle'] == '[masked]'
    assert len(restored['omissions']) == 4
    assert store.invocation(p.alias, access_scope=SCOPE) == {'audit_id': 'audit-001', 'normalized_request': {'query': 'token grammar'}}
    for suffix in ['', '-wal']:
        path = Path(str(store.path) + suffix)
        if path.exists():
            data = path.read_bytes()
            assert b'opaque-secret' not in data and b'private thoughts' not in data and b'top-secret' not in data
    # A process dies after a committed admission; WAL recovery retains evidence.
    crash_code = '''import json,os,sys
from project_control.as1_packets import SQLitePacketStore
s=SQLitePacketStore(sys.argv[1])
s.create(tool='command', payload={'crash_committed': True}, access_scope=json.loads(sys.argv[2]))
os._exit(23)'''
    crashed = subprocess.run([sys.executable, '-c', crash_code, str(tmp_path), json.dumps(SCOPE)])
    assert crashed.returncode == 23
    with sqlite3.connect(store.path) as db:
        ident = db.execute('SELECT id FROM packets ORDER BY rowid DESC LIMIT 1').fetchone()[0]
    assert SQLitePacketStore(tmp_path).resolve(ident, access_scope=SCOPE).payload == {'crash_committed': True}
    # Death before COMMIT leaves neither a body nor an alias reservation.
    rollback_code = '''import os,sqlite3,sys
c=sqlite3.connect(sys.argv[1]); c.execute('BEGIN IMMEDIATE')
c.execute("INSERT INTO packets(id,alias,metadata,hash) VALUES ('pkt_rollback','cedar-reed','{}','hash')")
os._exit(24)'''
    assert subprocess.run([sys.executable, '-c', rollback_code, str(store.path)]).returncode == 24
    assert SQLitePacketStore(tmp_path).lookup('cedar-reed', access_scope=SCOPE).status == 'not_found'
    q = store.resolve(p.alias, access_scope=SCOPE)
    assert canonical_digest(q.payload) == q.payload_sha256
    with pytest.raises(ValueError):
        store.put(q.model_copy(update={'payload': {'changed': True}}))
    with pytest.raises(ValueError):
        issue(store, normalized_request={'access_token': 'private'})


@pytest.mark.as1_case('PKT-02')
def test_concurrent_collision_tombstone_backup_namespace_and_immutability(tmp_path):
    now = [1000.0]
    store = SQLitePacketStore(tmp_path / 'live', clock=lambda: now[0], alias_factory=lambda size: 'amber-birch')
    def reserve(index):
        attempt = [0]
        def collide(size):
            attempt[0] += 1
            return 'amber-birch' if attempt[0] == 1 else 'cedar-' + WORDS[index]
        p = issue(SQLitePacketStore(tmp_path / 'live', clock=lambda: now[0], alias_factory=collide), payload={'i': index}, ttl_seconds=2)
        return p
    # An actual SQLite unique-constraint collision never overwrites either body.
    first = issue(store, ttl_seconds=2)
    collision = first.model_copy(update={'packet_id': 'pkt_other'})
    with pytest.raises(sqlite3.IntegrityError):
        store.put(collision)
    with pytest.raises(ValueError):
        store.put(first.model_copy(update={'packet_id': first.alias, 'alias': 'cedar-dawn'}))
    with ThreadPoolExecutor(max_workers=6) as workers:
        packets = list(workers.map(reserve, range(12)))
    assert len({p.alias for p in packets} | {first.alias}) == 13
    assert len({p.packet_id for p in packets}) == 12
    now[0] += 3
    assert store.lookup(first.alias, access_scope=SCOPE).status == 'expired'
    assert first.packet_id in store.gc()
    backup = tmp_path / 'backup.sqlite3'; store.backup(backup)
    restore = tmp_path / 'restored'; restore.mkdir(); shutil.copyfile(backup, restore / 'packets.sqlite3')
    other = SQLitePacketStore(restore, clock=lambda: now[0])
    assert other.lookup(first.alias, access_scope=SCOPE).status == 'expired'
    with pytest.raises(ValueError):
        SQLitePacketStore(restore, namespace='foreign')
    with pytest.raises(sqlite3.IntegrityError):
        other.put(collision)
    with pytest.raises(ValueError):
        other.put(first)
    # Saturated two-word namespace expands rather than recycles.
    counter = [0]
    def alias(size):
        counter[0] += 1
        return first.alias if size == 2 else 'amber-birch-cedar'
    extended = SQLitePacketStore(restore, clock=lambda: now[0], alias_factory=alias)
    assert issue(extended).alias == 'amber-birch-cedar'


@pytest.mark.as1_case('PKT-03')
def test_scoped_hint_dedup_exact_spans_stale_expired_budget_and_hash(tmp_path):
    now = [1000.0]; store = SQLitePacketStore(tmp_path, clock=lambda: now[0])
    source = SourceLocator(project='pc', repository='pc', path='src/file.py', content_sha256='a'*64, line_start=3, line_end=4)
    p = issue(store, sources=[source], omissions=['results truncated'])
    duplicate = issue(store, sources=[source])
    expired = issue(store, ttl_seconds=0)
    assert store.lookup(p.alias, access_scope={'principal': 'bob'}).status == 'forbidden'
    assert store.lookup(expired.alias, access_scope={'principal': 'bob'}).status == 'forbidden'
    assert store.lookup('absent', access_scope=SCOPE).status == 'not_found'
    result = store.assemble_hints([p.alias, duplicate.alias, expired.alias, 'absent'], access_scope=SCOPE,
        current_dependencies={'file:pc/src/file.py': 'b'*64})
    assert len(result['packets']) == 1
    assert result['packets'][0]['packet']['payload']['text'] == 'exact source span\nsecond line'
    assert result['packets'][0]['packet']['sources'][0]['line_start'] == 3
    assert {'stale', 'expired', 'not_found', 'duplicate_content', 'producer_omission'} <= {x['reason'] for x in result['omissions']}
    assert not result['authority']
    assert result['packets'][0]['also_from'][0]['packet_id'] == duplicate.packet_id
    assert store.assemble_hints([p.alias], access_scope=SCOPE, budget_bytes=0)['omissions'][-1]['reason'] == 'budget'
    # Detect persisted-byte corruption, not merely caller input corruption.
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE bodies SET payload=? WHERE hash=?', ('{"text":"tampered"}', p.payload_sha256))
    with pytest.raises(ValueError):
        store.resolve(p.alias, access_scope=SCOPE)


@pytest.mark.as1_case('PKT-04')
def test_active_recent_explicit_and_child_retention(tmp_path):
    now = [1000.0]; store = SQLitePacketStore(tmp_path, clock=lambda: now[0], recent_terminal_limit=2)
    parent = issue(store, ttl_seconds=1)
    child = issue(store, parents=[parent.packet_id], ttl_seconds=10)
    active = issue(store, payload={'active': True}, ttl_seconds=1)
    store.retain_job('active-job', [active.packet_id])
    explicit = issue(store, payload={'explicit': True}, ttl_seconds=1); store.pin('user', [explicit.alias])
    terminals = []
    for i in range(3):
        packet = issue(store, payload={'terminal': i}, ttl_seconds=1)
        store.retain_job(f'terminal-{i}', [packet.packet_id], terminal=True)
        terminals.append(packet); now[0] += .1
    elapsed = issue(store, payload={'elapsed': True}, ttl_seconds=0)
    with pytest.raises(ValueError):
        issue(store, parents=[elapsed.packet_id])
    now[0] = 1003
    collected = store.gc()
    assert terminals[0].packet_id in collected
    for packet in [parent, child, active, explicit, *terminals[1:]]:
        assert store.lookup(packet.alias, access_scope=SCOPE).status == 'ok'
    now[0] = 1011; store.unpin('user'); store.unpin('active-job')
    collected = store.gc()
    assert {parent.packet_id, child.packet_id, active.packet_id, explicit.packet_id} <= set(collected)
    with pytest.raises(ValueError):
        store.pin('late', [parent.packet_id])
    with pytest.raises(ValueError):
        issue(store, parents=[parent.packet_id])


@pytest.mark.as1_case('PKT-05')
def test_semantic_registry_discovery_and_volatile_freshness_are_targeted(tmp_path):
    now = [1000.0]; store = SQLitePacketStore(tmp_path, clock=lambda: now[0])
    deps = {'file:pc/seed.py': 'a'*64, 'discovery:pc/importers': 'b'*64,
            'semantic_revision:pc/task/PC-AS1-PACKETS': 'r7', 'registry:pc': 'r3',
            'provider:python/config': 'c'*64, 'skill:cuda': 'd'*64, 'environment:host': 'e'*64}
    p = issue(store, freshness={'dependencies': deps, 'negative': True})
    visited = []
    def verify(key):
        visited.append(key); return deps[key]
    result = store.assemble_hints([p.alias], access_scope=SCOPE, current_dependencies=verify)
    assert result['packets'][0]['freshness'] == 'current' and set(visited) == set(deps)
    current = dict(deps); current['file:unrelated.py'] = 'new'
    assert store.assemble_hints([p.alias], access_scope=SCOPE, current_dependencies=current)['packets'][0]['freshness'] == 'current'
    for changed in deps:
        current = {**deps, changed: 'changed'}
        stale = store.assemble_hints([p.alias], access_scope=SCOPE, current_dependencies=current)
        reasons = stale['omissions'][0]['dependencies']
        assert reasons == [{'dependency': changed, 'reason': 'changed'}]
    negative = issue(store, freshness={'dependencies': {'file:pc/seed.py': 'a'*64}, 'negative': True})
    assert store.assemble_hints([negative.alias], access_scope=SCOPE, current_dependencies=deps)['packets'][0]['freshness'] == 'stale'
    volatile = issue(store, freshness={'volatile': True, 'max_age_seconds': 2})
    now[0] += 3
    assert store.assemble_hints([volatile.alias], access_scope=SCOPE)['omissions'][0]['dependencies'] == [{'reason': 'volatile_observation_expired'}]
    assert store.assemble_hints([p.alias], access_scope=SCOPE)['packets'][0]['freshness'] == 'stale'


@pytest.mark.as1_case('PKT-03')
@pytest.mark.parametrize('expiry_cause', ['unpin', 'ttl'])
def test_lookup_has_consistent_snapshot_during_gc(tmp_path, monkeypatch, expiry_cause):
    from threading import Event
    now = [1000.0]
    reader = SQLitePacketStore(tmp_path, clock=lambda: now[0])
    writer = SQLitePacketStore(tmp_path, clock=lambda: now[0])
    packet = issue(reader, ttl_seconds=1)
    if expiry_cause == 'unpin':
        writer.pin('active', [packet.packet_id]); now[0] = 1002
    read_live, collected = Event(), Event()
    original = reader._live_ids
    def pause_after_liveness(db):
        live = original(db)
        assert db.in_transaction
        read_live.set()
        assert collected.wait(5), 'concurrent GC failed to complete'
        return live
    monkeypatch.setattr(reader, '_live_ids', pause_after_liveness)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(reader.lookup, packet.alias, access_scope=SCOPE)
        try:
            assert read_live.wait(5)
            if expiry_cause == 'unpin':
                writer.unpin('active')
            else:
                now[0] = 1002
            assert packet.packet_id in writer.gc()
        finally:
            collected.set()
        assert future.result(timeout=5).status == 'ok'
    assert writer.lookup(packet.alias, access_scope=SCOPE).status == 'expired'


@pytest.mark.as1_case('PKT-04')
def test_reactivating_terminal_owner_atomically_preserves_active_inputs(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from threading import Event
    now = [1000.0]
    active_store = SQLitePacketStore(tmp_path, clock=lambda: now[0], recent_terminal_limit=1)
    terminal_store = SQLitePacketStore(tmp_path, clock=lambda: now[0], recent_terminal_limit=1)
    old = issue(active_store, payload={'old': True}, ttl_seconds=10)
    new = issue(active_store, payload={'new': True}, ttl_seconds=10)
    other = issue(active_store, payload={'other': True}, ttl_seconds=10)
    active_store.retain_job('A', [old.packet_id], terminal=True)
    pinned, competing_write, release = Event(), Event(), Event()
    original_pin = active_store._pin
    def pause_after_pin(db, owner, refs):
        original_pin(db, owner, refs)
        assert db.in_transaction
        pinned.set()
        assert release.wait(5)
    monkeypatch.setattr(active_store, '_pin', pause_after_pin)
    original_db = terminal_store._db
    @contextmanager
    def track_competing_transaction():
        with original_db() as db:
            db.set_trace_callback(lambda sql: competing_write.set() if sql == 'BEGIN IMMEDIATE' else None)
            yield db
    monkeypatch.setattr(terminal_store, '_db', track_competing_transaction)
    with ThreadPoolExecutor(max_workers=2) as executor:
        active = executor.submit(active_store.retain_job, 'A', [new.packet_id], terminal=False)
        try:
            assert pinned.wait(5)
            terminal = executor.submit(terminal_store.retain_job, 'B', [other.packet_id], terminal=True)
            assert competing_write.wait(5)
        finally:
            release.set()
        active.result(timeout=5); terminal.result(timeout=5)
    now[0] = 1011
    terminal_store.gc()
    assert terminal_store.lookup(new.packet_id, access_scope=SCOPE).status == 'ok'
    with sqlite3.connect(active_store.path) as db:
        assert db.execute('SELECT terminal FROM retention WHERE owner=?', ('A',)).fetchone() == (0,)
        assert db.execute('SELECT 1 FROM pins WHERE owner=? AND packet=?', ('A', new.packet_id)).fetchone() == (1,)


@pytest.mark.as1_case('PKT-05')
@pytest.mark.parametrize('conflict_kind', ['producer', 'source_revisions'])
def test_conflicting_source_dependencies_cannot_be_current(tmp_path, conflict_kind):
    store = SQLitePacketStore(tmp_path)
    source = SourceLocator(project='pc', repository='pc', path='src/file.py', content_sha256='a'*64)
    key = 'file:pc/src/file.py'
    sources, freshness = [source], None
    if conflict_kind == 'producer':
        freshness = {'dependencies': {key: 'b'*64}}
    else:
        sources.append(source.model_copy(update={'content_sha256': 'b'*64}))
    packet = issue(store, sources=sources, freshness=freshness)
    for current in ['a'*64, 'b'*64]:
        hints = store.assemble_hints([packet.alias], access_scope=SCOPE, current_dependencies={key: current})
        assert hints['packets'][0]['freshness'] == 'stale'
        assert hints['omissions'][0]['dependencies'] == [{
            'dependency': key, 'reason': 'conflicting_dependency', 'expected_values': ['a'*64, 'b'*64]}]
