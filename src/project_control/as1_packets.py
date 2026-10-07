"""Service-private, transactional packet persistence. References are never authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
import uuid

from .as1_contracts import InformationPacket, SourceLocator, canonical_digest

WORDS = ('amber', 'birch', 'cedar', 'dawn', 'elm', 'fern', 'glen', 'heron',
         'iris', 'jade', 'kite', 'lake', 'moss', 'oak', 'pine', 'reed')
_PRIVATE_KEYS = {'password', 'secret', 'access_token', 'refresh_token',
                 'api_key', 'authorization', 'credential', 'credentials',
                 'workflow_handle', 'capability', 'bearer', 'hidden_reasoning',
                 'chain_of_thought', 'private_context'}
_BEARER = re.compile(r'\bBearer\s+[A-Za-z0-9._~+/=-]+', re.IGNORECASE)
# Serialize connection lifecycle across store instances, while leaving WAL
# transactions independent so readers can retain their snapshots during writes.
SQLITE_CONNECTION_LOCK = threading.RLock()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _stamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace('Z', '+00:00').replace('z', '+00:00')).timestamp()


def mask_payload(value: Any, path: str = '') -> tuple[Any, list[dict[str, str]]]:
    """Mask recognized credential/opaque-authority fields, preserving ordinary text.

    Callers remain responsible for enforcing their output policy before submission;
    arbitrary secrets cannot be identified from unlabeled text.
    """
    omissions: list[dict[str, str]] = []
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            location = f'{path}/{key}'
            if key.lower().replace('-', '_') in _PRIVATE_KEYS:
                result[key] = '[masked]'
                if item != '[masked]':
                    omissions.append({'path': location, 'reason': 'policy_masked'})
            else:
                result[key], removed = mask_payload(item, location)
                omissions.extend(removed)
        return result, omissions
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            cleaned, removed = mask_payload(item, f'{path}/{index}')
            result.append(cleaned)
            omissions.extend(removed)
        return result, omissions
    if isinstance(value, str):
        cleaned = _BEARER.sub('Bearer [masked]', value)
        if cleaned != value:
            omissions.append({'path': path, 'reason': 'policy_masked'})
        return cleaned, omissions
    return value, omissions


@dataclass(frozen=True)
class PacketLookup:
    status: str
    packet: InformationPacket | None = None


class SQLitePacketStore:
    """One crash-safe SQLite commit contains body, provenance and alias reservation.

    The directory must be service-private on a local WAL-capable filesystem.
    Each operation opens its own connection, permitting concurrent callers.
    """
    def __init__(self, directory: str | Path, *, namespace: str = 'default',
                 clock: Callable[[], float] = time.time, alias_factory: Callable[[int], str] | None = None,
                 recent_terminal_limit: int = 50, max_payload_bytes: int = 4 * 1024 * 1024, authority_access=None):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / 'packets.sqlite3'
        self.namespace, self.clock = namespace, clock
        self.authority_access = authority_access
        self.alias_factory = alias_factory or (lambda size: '-'.join(secrets.choice(WORDS) for _ in range(size)))
        self.recent_terminal_limit, self.max_payload_bytes = recent_terminal_limit, max_payload_bytes
        # WAL persists on disk; initialize it before concurrent operations rather
        # than changing journal mode on every connection.
        with SQLITE_CONNECTION_LOCK:
            initial = sqlite3.connect(self.path, timeout=30)
            try:
                if initial.execute('PRAGMA journal_mode').fetchone()[0] != 'wal':
                    initial.execute('PRAGMA journal_mode=WAL').fetchone()
            finally:
                initial.close()
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS identity(namespace TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bodies(hash TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS packets(id TEXT PRIMARY KEY, alias TEXT UNIQUE NOT NULL,
                    metadata TEXT NOT NULL, hash TEXT NOT NULL, expired INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pins(owner TEXT, packet TEXT, PRIMARY KEY(owner, packet));
                CREATE TABLE IF NOT EXISTS retention(owner TEXT PRIMARY KEY, terminal INTEGER, updated REAL);
                CREATE TABLE IF NOT EXISTS knowledge_notes(
                    note_id TEXT PRIMARY KEY, project TEXT NOT NULL, metadata TEXT NOT NULL,
                    access_scope TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS knowledge_notes_project_updated
                    ON knowledge_notes(project, updated DESC, note_id);
            ''')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT namespace FROM identity').fetchone()
            if row and row[0] != namespace:
                raise ValueError('packet namespace mismatch')
            if not row:
                db.execute('INSERT INTO identity VALUES (?)', (namespace,))
        os.chmod(self.path, 0o600)

    _NOTE_FIELDS = {
        'note_id', 'project', 'kind', 'claim', 'reason_matters', 'sources',
        'evidence_packets', 'dependencies', 'coverage', 'uncertainty',
        'provenance', 'supersedes', 'contradicts', 'next_action',
        'authoritative', 'is_independent_evidence',
    }
    _NOTE_PROVENANCE = {'user', 'source', 'test', 'model'}
    _MAX_NOTE_BYTES = 8 * 1024
    _MAX_NOTES_PER_PROJECT = 128

    def _note_scope_allows(self, saved: Mapping[str, Any], supplied: Mapping[str, Any], sources: list[dict]) -> bool:
        # The packet store's legacy default intentionally treats principal and
        # profile as provenance. Notes are user data, so principal ownership is
        # part of their read boundary even when no richer callback is installed.
        if not supplied or saved.get('principal') != supplied.get('principal'):
            return False
        if self.authority_access is not None:
            return bool(self.authority_access(dict(saved), dict(supplied), sources))
        return self._authorized(saved, supplied)

    @staticmethod
    def _validate_note(record: Mapping[str, Any], *, user_goal: bool) -> dict:
        if not isinstance(record, Mapping):
            raise TypeError('note record must be a mapping')
        value = dict(record)
        unknown = set(value) - SQLitePacketStore._NOTE_FIELDS
        if unknown:
            raise ValueError('unknown note fields: ' + ', '.join(sorted(unknown)))
        if not user_goal and (value.get('provenance') == 'user' or value.get('kind') == 'goal'):
            raise ValueError('user goals require the trusted put_user_goal path')
        if user_goal:
            value['kind'] = 'goal'
            value['provenance'] = 'user'
        value.setdefault('sources', [])
        value.setdefault('evidence_packets', [])
        value.setdefault('dependencies', {})
        value.setdefault('coverage', {})
        value.setdefault('uncertainty', [])
        if not isinstance(value.get('note_id'), str) or not value['note_id'].strip() or len(value['note_id']) > 128:
            raise ValueError('note_id must be a stable nonempty identifier of at most 128 characters')
        if any(ord(char) < 32 for char in value['note_id']):
            raise ValueError('note_id contains a control character')
        for field in ('project', 'kind', 'claim', 'reason_matters'):
            if not isinstance(value.get(field), str) or not value[field].strip():
                raise ValueError(f'{field} must be a nonempty string')
        if value.get('provenance') not in SQLitePacketStore._NOTE_PROVENANCE:
            raise ValueError('unsupported note provenance')
        if not isinstance(value['sources'], list) or not isinstance(value['evidence_packets'], list):
            raise ValueError('sources and evidence_packets must be lists')
        try:
            value['sources'] = [SourceLocator.model_validate(source).model_dump(mode='json') for source in value['sources']]
        except Exception as exc:
            raise ValueError('invalid source locator') from exc
        if any(not isinstance(ref, str) or not ref.startswith('pkt_') for ref in value['evidence_packets']):
            raise ValueError('evidence_packets must contain original packet IDs')
        if len(value['evidence_packets']) != len(set(value['evidence_packets'])):
            raise ValueError('evidence_packets must be unique')
        if not isinstance(value['dependencies'], Mapping) or any(
            not isinstance(key, str) or not key or not isinstance(digest, str) or not digest
            for key, digest in value['dependencies'].items()
        ):
            raise ValueError('dependencies must map nonempty determinant names to digests')
        value['dependencies'] = dict(value['dependencies'])
        for field in ('coverage', 'uncertainty'):
            try:
                _json(value[field])
            except (TypeError, ValueError) as exc:
                raise ValueError(f'{field} must be JSON data') from exc
        for field in ('supersedes', 'contradicts'):
            if field in value and value[field] is not None:
                refs = value[field] if isinstance(value[field], list) else [value[field]]
                if any(not isinstance(ref, str) or not ref for ref in refs):
                    raise ValueError(f'{field} must contain note IDs')
        if 'next_action' in value and value['next_action'] is not None and not isinstance(value['next_action'], str):
            raise ValueError('next_action must be text or null')
        for field in ('authoritative', 'is_independent_evidence'):
            if value.get(field) is True:
                raise ValueError('knowledge notes cannot be authority or independent evidence')
            value[field] = False
        if value['provenance'] in {'source', 'test'} and (
            not value['sources'] or not value['evidence_packets']
        ):
            raise ValueError('source/test notes require original source locators and evidence packets')
        cleaned, removed = mask_payload(value)
        if removed:
            raise ValueError('note contains private or authority material')
        if len(_json(cleaned).encode('utf-8')) > SQLitePacketStore._MAX_NOTE_BYTES:
            raise ValueError('note exceeds 8 KiB record cap')
        return cleaned

    @staticmethod
    def _prepared_context_packet(metadata: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
        # A packet carrying notebook retrieval is a derived presentation, not an
        # independent witness that can be fed back as evidence for those notes.
        markers = {'knowledge_notes', 'prepared_notes', 'derived_knowledge', 'knowledge_context'}
        def contains_marker(value):
            if isinstance(value, Mapping):
                if any(key in markers for key in value):
                    return True
                # This is the actual InformationService packet envelope:
                # payload.data.prepared_context contains provider notes/goals.
                # Match that typed location rather than any ordinary `notes`
                # field, which can be part of an original source packet.
                prepared = value.get('prepared_context')
                if isinstance(prepared, Mapping) and ('notes' in prepared or 'goals' in prepared):
                    return True
                return any(contains_marker(item) for item in value.values())
            if isinstance(value, list):
                return any(contains_marker(item) for item in value)
            return False
        if contains_marker(metadata) or contains_marker(payload):
            return True
        tool = str(metadata.get('tool', '')).lower().replace('-', '_')
        return tool in {'knowledge', 'knowledge_context', 'prepared_context', 'notebook_context'}

    def _validate_note_evidence(self, db, record: dict, access_scope: Mapping[str, Any]) -> list[str]:
        saved_scope = dict(access_scope)
        if not saved_scope or saved_scope.get('project') != record['project']:
            raise PermissionError('note project must match the trusted access scope')
        if not self._note_scope_allows(saved_scope, access_scope, record['sources']):
            raise PermissionError('note outside current trusted access scope')
        allowed_ids, evidence_sources, live_ids = [], [], self._live_ids(db)
        for packet_id in record['evidence_packets']:
            row = db.execute('SELECT id,metadata,hash,expired FROM packets WHERE id=?', (packet_id,)).fetchone()
            if not row or row[3] or row[0] not in live_ids:
                raise ValueError('note evidence packet is missing or expired')
            metadata = json.loads(row[1])
            packet_scope, packet_sources = metadata['access_scope'], metadata['sources']
            if not self._note_scope_allows(packet_scope, access_scope, packet_sources):
                raise PermissionError('note evidence packet is outside current trusted scope')
            body = db.execute('SELECT payload FROM bodies WHERE hash=?', (row[2],)).fetchone()
            if not body:
                raise RuntimeError('packet body missing: storage corruption')
            if self._prepared_context_packet(metadata, json.loads(body[0])):
                raise ValueError('prepared knowledge context cannot corroborate a note')
            allowed_ids.append(row[0])
            evidence_sources.extend(packet_sources)
        if record['provenance'] in {'source', 'test'}:
            source_wires = { _json(source) for source in evidence_sources }
            if not {_json(source) for source in record['sources']} <= source_wires:
                raise ValueError('source/test note locators must appear in cited original packets')
        return allowed_ids

    def _put_note(self, record: Mapping[str, Any], *, access_scope: Mapping[str, Any], user_goal: bool) -> dict:
        value = self._validate_note(record, user_goal=user_goal)
        now = self.clock()
        owner = 'note:' + value['note_id']
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT project,metadata,access_scope,created FROM knowledge_notes WHERE note_id=?',
                               (value['note_id'],)).fetchone()
            if prior:
                prior_scope = json.loads(prior[2])
                prior_record = json.loads(prior[1])
                if prior[0] != value['project'] or not self._note_scope_allows(
                    prior_scope, access_scope, prior_record['sources']
                ):
                    raise PermissionError('cannot update a note outside its original trusted scope')
                # Keep original scope and creation time on update; a caller cannot
                # widen an existing note by presenting a broader request scope.
                scope_to_store, created = prior_scope, prior[3]
                if access_scope.get('project') != value['project']:
                    raise PermissionError('note project must match the trusted access scope')
            else:
                scope_to_store, created = dict(access_scope), now
            refs = self._validate_note_evidence(db, value, access_scope)
            count = db.execute('SELECT count(*) FROM knowledge_notes WHERE project=? AND note_id<>?',
                               (value['project'], value['note_id'])).fetchone()[0]
            if not prior and count >= self._MAX_NOTES_PER_PROJECT:
                raise ValueError('project knowledge capacity reached (128 records)')
            wire = _json(value)
            stored_scope = _json(scope_to_store)
            # Evidence validation happens before pin changes, within the same
            # write transaction. Any insertion failure rolls both back.
            db.execute('DELETE FROM pins WHERE owner=?', (owner,))
            db.executemany('INSERT INTO pins(owner,packet) VALUES (?,?)', [(owner, ref) for ref in refs])
            db.execute('INSERT OR REPLACE INTO knowledge_notes(note_id,project,metadata,access_scope,created,updated) '
                       'VALUES (?,?,?,?,?,?)',
                       (value['note_id'], value['project'], wire, stored_scope, created, now))
        return json.loads(wire)

    def put_note(self, record: Mapping[str, Any], *, access_scope: Mapping[str, Any]) -> dict:
        """Persist one advisory note and its evidence pins in the packet DB transaction."""
        return self._put_note(record, access_scope=access_scope, user_goal=False)

    def put_user_goal(self, record: Mapping[str, Any], *, access_scope: Mapping[str, Any]) -> dict:
        """Persist trusted user intent with user provenance; it grants no runtime power."""
        return self._put_note(record, access_scope=access_scope, user_goal=True)

    def get_note(self, note_id: str, *, access_scope: Mapping[str, Any]) -> dict | None:
        with self._db() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT metadata,access_scope FROM knowledge_notes WHERE note_id=?', (note_id,)).fetchone()
            if not row:
                return None
            record, saved_scope = json.loads(row[0]), json.loads(row[1])
            if (access_scope.get('project') != record['project']
                    or not self._note_scope_allows(saved_scope, access_scope, record['sources'])):
                return None
            return record

    def list_notes(self, *, access_scope: Mapping[str, Any], project: str, limit: int = 128) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self._MAX_NOTES_PER_PROJECT:
            raise ValueError('limit must be between 1 and 128')
        if access_scope.get('project') != project:
            raise PermissionError('requested project must match the trusted access scope')
        with self._db() as db:
            db.execute('BEGIN')
            rows = db.execute('SELECT metadata,access_scope FROM knowledge_notes WHERE project=? '
                              'ORDER BY updated DESC,note_id LIMIT ?', (project, limit)).fetchall()
            result = []
            for metadata, stored_scope in rows:
                record, saved_scope = json.loads(metadata), json.loads(stored_scope)
                if self._note_scope_allows(saved_scope, access_scope, record['sources']):
                    result.append(record)
            return result

    def delete_note(self, note_id: str, *, access_scope: Mapping[str, Any]) -> bool:
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT metadata,access_scope FROM knowledge_notes WHERE note_id=?', (note_id,)).fetchone()
            if not row:
                return False
            record, saved_scope = json.loads(row[0]), json.loads(row[1])
            if (access_scope.get('project') != record['project']
                    or not self._note_scope_allows(saved_scope, access_scope, record['sources'])):
                return False
            db.execute('DELETE FROM pins WHERE owner=?', ('note:' + note_id,))
            db.execute('DELETE FROM knowledge_notes WHERE note_id=?', (note_id,))
            return True

    @contextmanager
    def _db(self):
        with SQLITE_CONNECTION_LOCK:
            db = sqlite3.connect(self.path, timeout=30)
            try:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('PRAGMA foreign_keys=ON')
            except BaseException:
                db.close()
                raise
        try:
            with db:
                yield db
        finally:
            with SQLITE_CONNECTION_LOCK:
                db.close()

    @staticmethod
    def _authorized(required, supplied):
        # Caller/profile identify provenance; other fields remain authority.
        return bool(required) and bool(supplied) and all(
            key in supplied and supplied[key] == value for key, value in required.items()
            if key not in {'principal', 'profile'})

    def create(self, *, tool: str, payload: dict[str, Any], access_scope: Mapping[str, Any],
               sources: list[SourceLocator] | None = None, parents: list[str] | None = None,
               normalized_request: dict | None = None, audit_id: str | None = None,
               freshness: dict | None = None, omissions: list | None = None,
               ttl_seconds: float | None = 7 * 86400) -> InformationPacket:
        cleaned, masked = mask_payload(payload)
        now = self.clock()
        extra = {'normalized_request': normalized_request or {}, 'audit_id': audit_id}
        _, removed = mask_payload(extra)
        if removed:
            raise ValueError('request/audit metadata contains private fields')
        # Reserve body and alias atomically; extend the vocabulary on saturation.
        for attempt in range(4096):
            size = min(4, 2 + attempt // 256)
            packet = InformationPacket(packet_id='pkt_' + uuid.uuid4().hex,
                alias=self.alias_factory(size), created_at=_stamp(now), tool=tool,
                payload=cleaned, payload_sha256=canonical_digest(cleaned), sources=sources or [],
                parents=parents or [], access_scope=dict(access_scope),
                expires_at=_stamp(now + ttl_seconds) if ttl_seconds is not None else None,
                freshness=freshness, omissions=list(omissions or []) + masked)
            try:
                return self._put(packet, extra)
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError('alias namespace exhausted; no alias was recycled')

    def put(self, packet: InformationPacket) -> InformationPacket:
        return self._put(packet, {})

    def _put(self, packet, extra):
        # Revalidate even if a caller used unchecked model_copy/model_construct.
        packet = InformationPacket.model_validate(packet.model_dump())
        wire = packet.model_dump()
        _, removed = mask_payload(wire)
        if removed:
            raise ValueError('packet contains unmasked private fields')
        if not packet.access_scope:
            raise ValueError('explicit trusted access scope required')
        body = _json(packet.payload)
        if len(body.encode()) > self.max_payload_bytes:
            raise ValueError('packet exceeds configured payload cap')
        metadata = {key: value for key, value in wire.items() if key != 'payload'}
        metadata['_invocation'] = extra
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT metadata, expired FROM packets WHERE id=?', (packet.packet_id,)).fetchone()
            if prior:
                old = json.loads(prior[0]); old.pop('_invocation', None)
                if prior[1] or old != {k: v for k, v in wire.items() if k != 'payload'}:
                    raise ValueError('immutable packet ID cannot be rebound')
                return packet.model_copy(deep=True)
            if packet.packet_id == packet.alias or db.execute(
                'SELECT 1 FROM packets WHERE id=? OR alias=?',
                (packet.alias, packet.packet_id),
            ).fetchone():
                raise ValueError('packet ID and alias namespaces cannot shadow one another')
            for parent in packet.parents:
                row = db.execute('SELECT metadata,expired FROM packets WHERE id=?', (parent,)).fetchone()
                if not row or row[1] or parent not in self._live_ids(db) or not self._authorized(json.loads(row[0])['access_scope'], packet.access_scope):
                    raise ValueError('parent missing, expired or outside packet scope')
            db.execute('INSERT OR IGNORE INTO bodies VALUES (?,?)', (packet.payload_sha256, body))
            db.execute('INSERT INTO packets(id,alias,metadata,hash) VALUES (?,?,?,?)',
                (packet.packet_id, packet.alias, _json(metadata), packet.payload_sha256))
            for owner in packet.pinned_by or []:
                db.execute('INSERT OR IGNORE INTO pins VALUES (?,?)', (owner, packet.packet_id))
        return packet.model_copy(deep=True)

    def _live_ids(self, db):
        rows = db.execute('SELECT id,metadata,expired FROM packets').fetchall()
        live = {row[0] for row in db.execute('SELECT packet FROM pins')}
        parents = {}
        for ident, metadata, expired in rows:
            value = json.loads(metadata)
            parents[ident] = value['parents']
            if not expired and (value['expires_at'] is None or _epoch(value['expires_at']) > self.clock()):
                live.add(ident)
        pending = list(live)
        while pending:
            for parent in parents.get(pending.pop(), []):
                if parent not in live:
                    live.add(parent); pending.append(parent)
        return live

    def reconcile_retention(self, retained: Mapping[str, tuple[Mapping[str, Any], list[str]]],
                            unpin_owners: list[str]) -> None:
        """Apply one broker retention snapshot with one shared packet liveness closure.

        Pins remain additive for retained owners, as with ``pin``. Owners that
        are no longer retained are fully unpinned after eligible additions are
        resolved against the preexisting pin graph, matching broker reconcile
        ordering while avoiding a whole-store scan for every reference.
        """
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            live_ids = self._live_ids(db)
            rows = db.execute('SELECT id,alias,metadata,hash,expired FROM packets').fetchall()
            by_reference = {}
            metadata_by_id = {}
            for row in rows:
                by_reference[row[0]] = row
                by_reference[row[1]] = row
                metadata = json.loads(row[2])
                metadata.pop('_invocation', None)
                metadata_by_id[row[0]] = metadata

            pins = []
            validated_packets = set()
            for owner, (access_scope, references) in retained.items():
                for reference in references:
                    if not isinstance(reference, str):
                        continue
                    row = by_reference.get(reference)
                    if row is None:
                        continue
                    metadata = metadata_by_id[row[0]]
                    if self.authority_access is not None:
                        permitted = self.authority_access(
                            metadata['access_scope'], dict(access_scope), metadata['sources'])
                    else:
                        permitted = self._authorized(metadata['access_scope'], access_scope)
                    if not permitted or row[4] or row[0] not in live_ids:
                        continue
                    if row[0] not in validated_packets:
                        body = db.execute('SELECT payload FROM bodies WHERE hash=?', (row[3],)).fetchone()
                        if not body:
                            raise RuntimeError('packet body missing: storage corruption')
                        packet_wire = dict(metadata)
                        packet_wire['payload'] = json.loads(body[0])
                        InformationPacket.model_validate(packet_wire)
                        validated_packets.add(row[0])
                    pins.append((owner, row[0]))

            db.executemany('INSERT OR IGNORE INTO pins(owner,packet) VALUES (?,?)', pins)
            db.executemany('DELETE FROM pins WHERE owner=?', ((owner,) for owner in unpin_owners))

    def lookup(self, reference: str, *, access_scope: Mapping[str, Any]) -> PacketLookup:
        with self._db() as db:
            # Metadata, references and body must come from one WAL snapshot.
            db.execute('BEGIN')
            row = db.execute('SELECT id,metadata,hash,expired FROM packets WHERE id=? OR alias=?', (reference, reference)).fetchone()
            if not row:
                return PacketLookup('not_found')
            metadata = json.loads(row[1]); metadata.pop('_invocation', None)
            if self.authority_access is not None:
                permitted = self.authority_access(metadata['access_scope'], dict(access_scope), metadata['sources'])
            else:
                permitted = self._authorized(metadata['access_scope'], access_scope)
            if not permitted:
                return PacketLookup('forbidden')
            if row[3] or row[0] not in self._live_ids(db):
                return PacketLookup('expired')
            body = db.execute('SELECT payload FROM bodies WHERE hash=?', (row[2],)).fetchone()
            if not body:
                raise RuntimeError('packet body missing: storage corruption')
            metadata['payload'] = json.loads(body[0])
            return PacketLookup('ok', InformationPacket.model_validate(metadata))

    def resolve(self, reference: str, *, access_scope: Mapping[str, Any]) -> InformationPacket | None:
        return self.lookup(reference, access_scope=access_scope).packet

    def pin(self, owner: str, references: list[str]) -> None:
        """Trusted engine retention operation, never exposed as a hint capability."""
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._pin(db, owner, references)

    def _pin(self, db, owner: str, references: list[str]) -> None:
        # Caller holds the write transaction through its retention bookkeeping.
        for ref in references:
            row = db.execute('SELECT id,expired FROM packets WHERE id=? OR alias=?', (ref, ref)).fetchone()
            if not row or row[1] or row[0] not in self._live_ids(db):
                raise ValueError('cannot pin missing/expired evidence')
            db.execute('INSERT OR IGNORE INTO pins VALUES (?,?)', (owner, row[0]))

    def unpin(self, owner: str) -> None:
        with self._db() as db:
            db.execute('DELETE FROM pins WHERE owner=?', (owner,))

    def retain_job(self, owner: str, references: list[str], *, terminal: bool = False) -> None:
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._pin(db, owner, references)
            db.execute('INSERT OR REPLACE INTO retention VALUES (?,?,?)', (owner, int(terminal), self.clock()))
            stale = db.execute('SELECT owner FROM retention WHERE terminal=1 ORDER BY updated DESC,owner DESC LIMIT -1 OFFSET ?', (self.recent_terminal_limit,)).fetchall()
            for (old,) in stale:
                db.execute('DELETE FROM pins WHERE owner=?', (old,))
                db.execute('DELETE FROM retention WHERE owner=?', (old,))

    def gc(self) -> list[str]:
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            live = self._live_ids(db)
            expired = [r[0] for r in db.execute('SELECT id FROM packets WHERE expired=0') if r[0] not in live]
            db.executemany('UPDATE packets SET expired=1 WHERE id=?', [(i,) for i in expired])
            db.execute('DELETE FROM bodies WHERE hash NOT IN (SELECT hash FROM packets WHERE expired=0)')
            return expired

    def backup(self, destination: str | Path) -> None:
        destination = Path(destination)
        with self._db() as source:
            with SQLITE_CONNECTION_LOCK:
                target = sqlite3.connect(destination)
            try:
                with target:
                    source.backup(target)
            finally:
                with SQLITE_CONNECTION_LOCK:
                    target.close()
        os.chmod(destination, 0o600)

    def invocation(self, reference: str, *, access_scope: Mapping[str, Any]) -> dict | None:
        if self.lookup(reference, access_scope=access_scope).status != 'ok':
            return None
        with self._db() as db:
            return json.loads(db.execute('SELECT metadata FROM packets WHERE id=? OR alias=?', (reference, reference)).fetchone()[0]).get('_invocation', {})

    def assemble_hints(self, references: list[str], *, access_scope: Mapping[str, Any],
                       current_dependencies: Mapping[str, str] | Callable[[str], str | None] | None = None,
                       budget_bytes: int = 8192) -> dict:
        """Keep exact selected payloads/spans; stale observations remain attributed leads.

        Dependency keys are producer-defined identities (e.g. file:repo/path,
        registry:project, semantic_revision:project, discovery:scope, environment:host).
        Missing verification is explicit, never assumed current.
        """
        packets, omissions, seen = [], [], {}
        used = 0
        for ref in dict.fromkeys(references):
            result = self.lookup(ref, access_scope=access_scope)
            if result.status != 'ok':
                omissions.append({'reference': ref, 'reason': result.status}); continue
            packet = result.packet
            freshness = packet.freshness or {}
            deps = dict(freshness.get('dependencies', {}))
            expectations = {key: {value} for key, value in deps.items()}
            for source in packet.sources:
                key = f'file:{source.repository}/{source.path}'
                expectations.setdefault(key, set()).add(source.content_sha256)
            changes = []
            for key, values in expectations.items():
                if len(values) != 1:
                    changes.append({'dependency': key, 'reason': 'conflicting_dependency',
                                    'expected_values': sorted(values)})
                    continue
                expected = next(iter(values))
                actual = current_dependencies(key) if callable(current_dependencies) else (current_dependencies or {}).get(key)
                if actual != expected:
                    changes.append({'dependency': key, 'reason': 'unverified' if actual is None else 'changed'})
            if freshness.get('negative') and not any(k.startswith(('discovery:', 'directory_membership:', 'exports:')) for k in expectations):
                changes.append({'reason': 'negative_discovery_scope_missing'})
            if freshness.get('volatile'):
                max_age = freshness.get('max_age_seconds', 0)
                if self.clock() - _epoch(packet.created_at) > max_age:
                    changes.append({'reason': 'volatile_observation_expired'})
            if not expectations and not freshness.get('volatile'):
                changes.append({'reason': 'dependency_manifest_missing'})
            if changes:
                omissions.append({'reference': ref, 'reason': 'stale', 'dependencies': changes})
            omissions.extend({'reference': ref, 'reason': 'producer_omission', 'detail': o} for o in packet.omissions or [])
            if packet.payload_sha256 in seen:
                provenance = {'reference': ref, 'packet_id': packet.packet_id,
                              'sources': [s.model_dump() for s in packet.sources],
                              'freshness': 'stale' if changes else 'current'}
                size = len(_json(provenance).encode())
                if used + size <= budget_bytes:
                    seen[packet.payload_sha256].setdefault('also_from', []).append(provenance)
                    used += size
                    omissions.append({'reference': ref, 'reason': 'duplicate_content'})
                else:
                    omissions.append({'reference': ref, 'reason': 'budget', 'needed_bytes': size})
                continue
            item = {'packet': packet.model_dump(), 'freshness': 'stale' if changes else 'current'}
            size = len(_json(item).encode())
            if used + size > budget_bytes:
                omissions.append({'reference': ref, 'reason': 'budget', 'needed_bytes': size}); continue
            packets.append(item); used += size; seen[packet.payload_sha256] = item
        return {'packets': packets, 'omissions': omissions, 'budget_unit': 'utf8_bytes', 'used_bytes': used,
                'authority': False}
