"""Additive project facts, reviewed amendments and scoped publication.

The in-process role/principal/context arguments come from the trusted host adapter.
A role string is not authentication proof and is never accepted from wire payloads.
Provider executable/root trust remains host configuration; nothing here executes it.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import re
import uuid
from pathlib import Path
from typing import Mapping

from .config import utc_now
from .db import Unchanged
from .models import TodoError
from .ownership import scopes_for
from .sessions import authenticate_claim
from .readiness import ready_tasks

ACTIONS = {
    'register_identity': 'identity', 'register_relation': 'relation',
    'register_generation': 'generation', 'configure_provider': 'provider',
    'record_skill_use': 'skill_use', 'update_orientation': 'orientation',
    'remove_registration': None,
}


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def fail(code, message):
    raise TodoError(code, message)


def require_schema(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_declarations'").fetchone():
        observed = int(conn.execute('SELECT COALESCE(MAX(version),0) FROM schema_migrations').fetchone()[0])
        raise TodoError('schema_migration_required', 'Project semantic context requires canonical additive migration 12', details={'observed_migration_version': observed, 'required_migration_version': 12})


def relative(value):
    if not isinstance(value, str) or not value or value.startswith('/') or re.match(r'^[A-Za-z]:', value) or '\\' in value or '\0' in value or '..' in value.split('/'):
        fail('invalid_project_amendment', 'Source paths must be relative without traversal')
    return value


def anchors(payload):
    values = payload.get('anchors', [])
    if not isinstance(values, list):
        fail('invalid_project_amendment', 'anchors must be typed source locators')
    for locator in values:
        if not isinstance(locator, dict) or not all(isinstance(locator.get(k), str) and locator[k] for k in ('project', 'repository', 'path', 'content_sha256')):
            fail('invalid_project_amendment', 'Anchor needs project/repository/path/content_sha256')
        if not re.fullmatch('[0-9a-f]{64}', locator['content_sha256']):
            fail('invalid_project_amendment', 'Anchor digest must be SHA256')
        relative(locator['path'])
        for key in ('line_start', 'line_end'):
            if locator.get(key) is not None and (type(locator[key]) is not int or locator[key] < 1):
                fail('invalid_project_amendment', 'Anchor line bounds must be positive integers')
        if locator.get('line_start') and locator.get('line_end') and locator['line_end'] < locator['line_start']:
            fail('invalid_project_amendment', 'Anchor range is reversed')
    return values


def validate_request(request, project, context):
    if hasattr(request, 'model_dump'):
        request = request.model_dump(exclude_none=True)
    if not isinstance(request, dict):
        fail('invalid_project_amendment', 'Amendment must be an object')
    required = {'format', 'project', 'action', 'intent', 'expected_revision', 'operation_id', 'payload'}
    if not required <= request.keys() or request.keys() - required - {'mode'}:
        fail('invalid_project_amendment', 'Amendment fields do not match pc-project-amendment/1')
    if request['format'] != 'pc-project-amendment/1' or request['action'] not in ACTIONS:
        fail('invalid_project_amendment', 'Unknown amendment format/action')
    if not isinstance(request['intent'], str) or not request['intent'] or not isinstance(request['operation_id'], str) or len(request['operation_id']) < 8 or type(request['expected_revision']) is not int or request['expected_revision'] < 0 or request.get('mode', 'apply') not in ('preview', 'apply'):
        fail('invalid_project_amendment', 'Invalid intent, operation identity, revision or mode')
    configured = project.get('configuration', {}).get('registered_project_id')
    binding = configured or (context or {}).get('project_id')
    allowed = {binding} if binding else {project['project_uuid'], project['project_name']}
    if request['project'] not in allowed:
        fail('project_mismatch', 'Amendment targets another registered authority')
    # Canonical JSON both rejects non-wire types and isolates caller mutations.
    try:
        request = json.loads(encode(request))
    except (ValueError, TypeError):
        fail('invalid_project_amendment', 'Amendment payload must be finite JSON')
    payload = request['payload']
    if not isinstance(payload, dict) or not isinstance(payload.get('id'), str) or not payload['id']:
        fail('invalid_project_amendment', 'Payload needs an exact nonempty id')
    anchors(payload)
    action = request['action']
    if action == 'remove_registration':
        if payload.get('kind') not in set(ACTIONS.values()) - {None} or type(payload.get('version')) is not int or payload['version'] < 1:
            fail('invalid_project_amendment', 'Removal needs exact kind/id/version')
    elif action == 'register_identity':
        if not isinstance(payload.get('repository'), str) or not payload['repository'] or not payload.get('version'):
            fail('invalid_project_amendment', 'Identity needs repository and version')
    elif action == 'register_relation':
        for key in ('source', 'target'):
            ref = payload.get(key)
            if not isinstance(ref, dict) or not all(isinstance(ref.get(k), str) and ref[k] for k in ('project_uuid', 'repository', 'kind', 'id')):
                fail('invalid_project_amendment', 'Relations need typed entity references')
            if ref.get('path') is not None:
                relative(ref['path'])
        if not isinstance(payload.get('relation'), str) or not payload['relation']:
            fail('invalid_project_amendment', 'Relation type required')
    elif action == 'register_generation':
        if not isinstance(payload.get('roots'), list) or not payload['roots'] or not isinstance(payload.get('inputs'), list) or not payload.get('generator'):
            fail('invalid_project_amendment', 'Generation needs relative roots, inputs and generator provenance')
        for root in payload['roots']:
            relative(root)
        for locator in payload['inputs']:
            anchors({'anchors': [locator]})
    elif action == 'record_skill_use':
        if not all(isinstance(payload.get(k), str) and payload[k] for k in ('skill', 'reason')) or payload.get('status') not in ('consulted', 'applied') or not payload.get('anchors'):
            fail('invalid_project_amendment', 'Skill use needs skill/reason/status/anchors')
    elif action == 'update_orientation':
        if not isinstance(payload.get('fields'), dict) or not payload['fields'] or not payload.get('anchors'):
            fail('invalid_project_amendment', 'Orientation needs source-backed fields')
        field_anchors = payload.get('field_anchors', {})
        if not isinstance(field_anchors, dict) or field_anchors.keys() - payload['fields'].keys():
            fail('invalid_project_amendment', 'Field anchors must name orientation fields')
        for values in field_anchors.values():
            if not values:
                fail('invalid_project_amendment', 'Field source anchors cannot be empty')
            anchors({'anchors': values})
    elif action == 'configure_provider':
        provider = payload.get('provider_id')
        trusted = project.get('configuration', {}).get('project_providers', {}).get(provider)
        if not isinstance(trusted, dict) or not isinstance(payload.get('options', {}), dict):
            fail('untrusted_provider', 'Provider is not host-registered')
        allowed_options = trusted.get('allowed_options', {})
        for key, value in payload.get('options', {}).items():
            if key not in allowed_options or value not in allowed_options[key]:
                fail('untrusted_provider', 'Provider option is not host-permitted')
        if payload.keys() - {'id', 'provider_id', 'options', 'anchors'}:
            fail('untrusted_provider', 'Provider request cannot specify executable/install/root trust')
    return request


def latest(conn):
    return [dict(row) for row in conn.execute('SELECT d.* FROM project_declarations d WHERE version=(SELECT MAX(version) FROM project_declarations WHERE kind=d.kind AND id=d.id) ORDER BY kind,id')]


def local_repositories(project, repo_root):
    """Trusted local mapping only: configured ID or exact root path/name."""
    if repo_root is None:
        return set()
    root = Path(repo_root).resolve()
    configured = project.get('configuration', {}).get('registered_repository_id')
    return {configured} if configured else {str(root), root.name}


def read_context(conn, project, *, optional=False, repo_root=None, project_source_verifier=None):
    if optional and not conn.execute("SELECT 1 FROM sqlite_master WHERE name='project_declarations'").fetchone():
        return {'status': 'unavailable', 'required_migration_version': 12}
    require_schema(conn)
    rows = []
    for row in latest(conn):
        if not row['retired']:
            row['payload'] = json.loads(row.pop('payload_json'))
            if row['kind'] == 'orientation':
                row['field_freshness'] = {}
                payload = row['payload']
                local_ids = {project['project_uuid'], project['project_name'],
                             project.get('configuration', {}).get('registered_project_id')}
                for field in payload['fields']:
                    locators = payload.get('field_anchors', {}).get(field, payload['anchors'])
                    states = []
                    for locator in locators:
                        state = 'unavailable'
                        if repo_root is not None and locator['project'] in local_ids and locator['repository'] in local_repositories(project, repo_root):
                            root = Path(repo_root).resolve()
                            path = (root / locator['path']).resolve()
                            if path.is_relative_to(root) and path.is_file():
                                try:
                                    state = 'fresh' if hashlib.sha256(path.read_bytes()).hexdigest() == locator['content_sha256'] else 'stale'
                                except OSError:
                                    pass
                        elif project_source_verifier is not None:
                            try:
                                verified_foreign_source(project_source_verifier, locator)
                                state = 'fresh'
                            except TodoError as exc:
                                state = 'stale' if exc.code == 'source_prerequisite_stale' else 'unavailable'
                        states.append({'source': locator, 'status': state})
                    row['field_freshness'][field] = {'status': 'stale' if any(x['status'] == 'stale' for x in states) else 'unavailable' if any(x['status'] == 'unavailable' for x in states) else 'fresh', 'sources': states}
            rows.append(row)
    return {'project_uuid': project['project_uuid'], 'project_revision': int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0]),
            'declarations': [r for r in rows if r['kind'] not in ('skill_use', 'orientation')],
            'skill_uses': [r for r in rows if r['kind'] == 'skill_use'],
            'orientation': [r for r in rows if r['kind'] == 'orientation'],
            'invalidations': [dict(r) for r in conn.execute('SELECT * FROM project_fragment_invalidations ORDER BY revision,id')]}


def verified_foreign_source(verifier, locator):
    """Validate a startup-owned broker receipt; never accept substituted sources."""
    if verifier is None:
        fail('source_prerequisite_unavailable', 'Foreign source verification requires a trusted host source verifier')
    try:
        receipt = verifier(dict(locator))
    except Exception as exc:
        if isinstance(exc, TodoError) and exc.code == 'source_prerequisite_stale':
            raise
        fail('source_prerequisite_unavailable', 'Trusted foreign source verifier is unavailable')
    required = {'project', 'project_uuid', 'revision', 'repository', 'path', 'content_sha256'}
    if not isinstance(receipt, dict) or not required <= receipt.keys():
        fail('source_prerequisite_unavailable', 'Foreign source verifier returned an incomplete receipt')
    try:
        valid_uuid = str(uuid.UUID(receipt['project_uuid'])) == receipt['project_uuid']
    except (ValueError, TypeError, AttributeError):
        valid_uuid = False
    if not valid_uuid or type(receipt['revision']) is not int or receipt['revision'] < 0 or not isinstance(receipt['project'], str) or not receipt['project']:
        fail('source_prerequisite_unavailable', 'Foreign source verifier returned invalid authority identity')
    if (locator['project'] not in (receipt['project'], receipt['project_uuid'])
            or any(locator[key] != receipt[key] for key in ('repository', 'path', 'content_sha256'))
            or any(key in locator and locator[key] != receipt[key] for key in ('project_uuid', 'revision'))):
        fail('source_prerequisite_stale', 'Foreign source receipt does not match the exact locator and authority')
    return {**locator, **{key: receipt[key] for key in required}}


def source_prerequisites(service, request):
    result = []
    locators = list(anchors(request['payload']))
    if request['action'] == 'register_generation':
        locators += request['payload'].get('inputs', [])
    if request['action'] == 'update_orientation':
        for values in request['payload'].get('field_anchors', {}).values():
            locators += values
    for anchor in locators:
        local_ids = {request['project'], service.project['project_uuid'], service.project['project_name'],
                     service.project.get('configuration', {}).get('registered_project_id')}
        if service.project_source_verifier is not None:
            # The host verifier owns source admission, including local sources.
            # Normalize only exact known local identities, never repository/path.
            locator = dict(anchor)
            if locator['project'] in {service.project['project_uuid'], service.project['project_name'],
                                      service.project.get('configuration', {}).get('registered_project_id')}:
                locator['project'] = service.project['project_uuid']
            result.append(verified_foreign_source(service.project_source_verifier, locator))
            continue
        if anchor['project'] not in local_ids or anchor['repository'] not in local_repositories(service.project, service.paths.repo_root):
            result.append(verified_foreign_source(service.project_source_verifier, anchor))
            continue
        root = service.paths.repo_root.resolve()
        path = (root / anchor['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            fail('source_prerequisite_stale', 'Local anchor is unavailable or outside registered root')
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != anchor['content_sha256']:
            fail('source_prerequisite_stale', 'Source anchor hash changed')
        result.append({'path': anchor['path'], 'content_sha256': actual})
    return result


def affected_set(conn, request, project):
    payload = request['payload']
    kind = payload.get('kind') if request['action'] == 'remove_registration' else ACTIONS[request['action']]
    affected = [r for r in latest(conn) if r['kind'] == kind and r['id'] == payload['id']]
    if request['action'] == 'remove_registration':
        if not affected or affected[0]['retired'] or affected[0]['version'] != payload['version']:
            fail('registration_stale', 'Exact active registration version is unavailable')
    # Include local incoming/outgoing declared relations in the reviewed set.
    if kind != 'relation':
        repository = json.loads(affected[0]['payload_json']).get('repository') if affected else payload.get('repository')
        for row in latest(conn):
            content = json.loads(row['payload_json'])
            if row['kind'] == 'relation' and not row['retired'] and any(content.get(key, {}).get('id') == payload['id'] and content.get(key, {}).get('kind') == kind and content.get(key, {}).get('project_uuid') == project['project_uuid'] and (repository is None or content.get(key, {}).get('repository') == repository) for key in ('source', 'target')):
                if row not in affected:
                    affected.append(row)
    return kind, sorted(affected, key=lambda r: (r['kind'], r['id']))


def payload_locators(payload):
    values = list(payload.get('anchors', [])) + list(payload.get('inputs', []))
    for locators in payload.get('field_anchors', {}).values():
        values += locators
    return [value for value in values if isinstance(value, dict) and 'path' in value]


def review(service, conn, request):
    kind, rows = affected_set(conn, request, service.project)
    prerequisites = source_prerequisites(service, request)
    trust = service.project.get('configuration', {}).get('project_providers', {}) if kind == 'provider' else {}
    canonical_payload = json.loads(encode(request['payload']))
    for locator in payload_locators(canonical_payload):
        for source in prerequisites:
            # Local content anchors must not acquire a revision pin that the
            # amendment itself immediately invalidates. Keep caller pins intact;
            # source_prerequisites already validated them through the host.
            if ('project_uuid' in source and source['project_uuid'] != service.project['project_uuid']
                    and locator['project'] in (source['project'], source['project_uuid'])
                    and all(locator[key] == source[key] for key in ('repository', 'path', 'content_sha256'))):
                locator.update({key: source[key] for key in ('project', 'project_uuid', 'revision')})
                break
    current = next((r for r in rows if r['kind'] == kind and r['id'] == request['payload']['id']), None)
    noop = request['action'] != 'remove_registration' and current and not current['retired'] and json.loads(current['payload_json']) == canonical_payload and current['origin'] == 'project_declared'
    affected = [{'kind': r['kind'], 'id': r['id'], 'version': r['version']} for r in rows]
    identifiers = {request['payload']['id']} | {r['id'] for r in rows}
    for content in [request['payload']] + [json.loads(r['payload_json']) for r in rows]:
        identifiers.update(locator['path'] for locator in payload_locators(content))
    return {'canonical_payload': canonical_payload, 'affected': affected, 'affected_digest': digest(rows), 'sources': prerequisites, 'trust_digest': digest(trust),
            'kind': kind, 'noop': bool(noop), 'invalidation_entities': sorted(identifiers), 'invalidations': [] if noop else [{'kind': k, 'entity_id': identity} for identity in sorted(identifiers) for k in ('graph', 'search', 'orientation')],
            'changes': [] if noop else [{'action': request['action'], 'kind': kind, 'id': request['payload']['id']}]}


def amend_in_transaction(service, conn, revision, request):
    require_schema(conn)
    operation_id = request['operation_id']
    request_hash = digest({k: v for k, v in request.items() if k not in ('mode', 'expected_revision')})
    prior = conn.execute('SELECT * FROM project_amendments WHERE operation_id=?', (operation_id,)).fetchone()
    if prior and prior['request_hash'] != request_hash:
        fail('operation_id_conflict', 'Operation ID already names a different amendment')
    if prior and prior['receipt_json']:
        return Unchanged({'status': 'replayed', 'operation_id': operation_id, 'receipt': json.loads(prior['receipt_json']), 'current_readiness': ready_tasks(conn)})
    current = revision - 1
    mode = request.get('mode', 'apply')
    if (not prior or mode == 'preview') and current != request['expected_revision']:
        fail('proposal_stale', 'Expected project revision changed')
    current_review = review(service, conn, request)
    if prior:
        saved = json.loads(prior['review_json'])
        if any(saved[k] != current_review[k] for k in ('affected_digest', 'sources', 'trust_digest')):
            fail('proposal_stale', 'Reviewed exact affected set or source prerequisites changed')
        original = json.loads(prior['request_json'])
        if request['expected_revision'] != original['expected_revision']:
            fail('proposal_stale', 'Reviewed expected revision changed')
    else:
        conn.execute('INSERT INTO project_amendments VALUES(?,?,?,?,NULL,?)', (operation_id, request_hash, encode(request), encode(current_review), utc_now()))
    if mode == 'preview':
        return Unchanged({'status': 'preview', 'operation_id': operation_id, 'expected_revision': request['expected_revision'], **current_review})
    if current_review['noop']:
        receipt = {'status': 'noop', 'operation_id': operation_id, 'revision': current, 'affected': current_review['affected'], 'invalidations': []}
        conn.execute('UPDATE project_amendments SET receipt_json=? WHERE operation_id=?', (encode(receipt), operation_id))
        return Unchanged({**receipt, 'receipt': receipt})
    payload = current_review['canonical_payload']; kind = current_review['kind']; now = utc_now()
    targets = current_review['affected'] if request['action'] == 'remove_registration' else [{'kind': kind, 'id': payload['id']}]
    for target in targets:
        previous = conn.execute('SELECT * FROM project_declarations WHERE kind=? AND id=? ORDER BY version DESC LIMIT 1', (target['kind'], target['id'])).fetchone()
        version = previous['version'] + 1 if previous else 1
        retiring = request['action'] == 'remove_registration'
        content = previous['payload_json'] if retiring else encode(payload)
        conn.execute('INSERT INTO project_declarations VALUES(?,?,?,?,?,NULL,?,?,?)', (target['kind'], target['id'], version, content, 'project_declared', int(retiring), now, revision))
    for item in current_review['invalidations']:
        conn.execute('INSERT INTO project_fragment_invalidations VALUES(?,?,?,?,?)', (str(uuid.uuid4()), item['kind'], item['entity_id'], revision, request['action']))
    # Invalidate only related derived/context fragments; terminal proof stays frozen.
    identifiers = set(current_review['invalidation_entities'])
    for fragment in conn.execute("SELECT f.* FROM workflow_context_fragments f LEFT JOIN tasks t ON t.id=f.task_id WHERE f.invalidated_at IS NULL AND (t.status IS NULL OR t.status NOT IN ('done','superseded','cancelled','stale'))"):
        content = json.loads(fragment['content_json'])
        def mentions(value):
            if isinstance(value, str):
                return value in identifiers
            if isinstance(value, dict):
                return any(mentions(v) for v in value.values())
            if isinstance(value, list):
                return any(mentions(v) for v in value)
            return False
        if mentions(content):
            conn.execute('UPDATE workflow_context_fragments SET invalidated_at=?,invalidation_revision=? WHERE id=?', (now, revision, fragment['id']))
    receipt = {'status': 'applied', 'operation_id': operation_id, 'revision': revision, 'affected': current_review['affected'], 'changes': current_review['changes'], 'invalidations': current_review['invalidations']}
    conn.execute('UPDATE project_amendments SET receipt_json=? WHERE operation_id=?', (encode(receipt), operation_id))
    return {**receipt, 'receipt': receipt}


def publish_in_transaction(service, conn, revision, request, claim_token):
    require_schema(conn)
    claim = authenticate_claim(conn, claim_token)
    return _publish_authenticated_claim(service, conn, revision, request, claim)


def _publish_authenticated_claim(service, conn, revision, request, claim, *, source_verifier=None, strict=False):
    """Private fact writer; callers authenticate the claim in this transaction."""
    require_schema(conn)
    task_id = claim['task_id']
    kind = request.get('kind')
    if kind not in ('skill_use', 'finding', 'candidate_relation'):
        fail('publication_scope_denied', 'Coder may publish only skill use, findings or candidate relationships')
    payload = request.get('payload', {})
    if request.get('task_id', task_id) != task_id or not isinstance(payload.get('id'), str) or not payload['id']:
        fail('publication_scope_denied', 'Publication must target the authenticated task')
    source_anchors = anchors(payload)
    if not source_anchors:
        fail('invalid_project_amendment', 'Scoped publication needs source anchors')
    scopes = scopes_for(conn, task_id, 'exclusive' if strict else None)
    for anchor in source_anchors:
        path = anchor['path']
        if anchor['project'] not in (service.project['project_uuid'], service.project['project_name'], service.project.get('configuration', {}).get('registered_project_id')) or not any(scope == '.' or path == scope.rstrip('/') or path.startswith(scope.rstrip('/') + '/') for scope in scopes):
            fail('publication_scope_denied', 'Publication anchor exceeds authenticated task scopes')
    if strict:
        _verify_publication_sources(service, source_anchors, source_verifier)
    # Validate local source hashes without giving coders project declaration powers.
    source_prerequisites(service, {'project': source_anchors[0]['project'], 'action': 'record_skill_use', 'payload': payload})
    if strict:
        # Recheck every local locator after the final trusted source callback.
        for anchor in source_anchors:
            _verify_local_publication_path(service.paths.repo_root.resolve(), anchor)
    if kind == 'skill_use' and (payload.get('status') not in ('consulted', 'applied') or not payload.get('skill') or not payload.get('reason')):
        fail('invalid_project_amendment', 'Skill publication needs skill/reason/status')
    identity = task_id + ':' + payload['id']
    previous = conn.execute('SELECT * FROM project_declarations WHERE kind=? AND id=? ORDER BY version DESC LIMIT 1', (kind, identity)).fetchone()
    if previous and previous['payload_json'] == encode(payload):
        return Unchanged({'status': 'noop', 'task_id': task_id, 'id': identity})
    conn.execute('INSERT INTO project_declarations VALUES(?,?,?,?,?,?,0,?,?)', (kind, identity, previous['version'] + 1 if previous else 1, encode(payload), 'candidate' if kind == 'candidate_relation' else 'task_scoped', task_id, utc_now(), revision))
    return {'status': 'published', 'task_id': task_id, 'id': identity, 'origin': 'task_scoped'}


def _verify_publication_sources(service, source_anchors, source_verifier=None):
    """Exact local repository fence, then optional trusted host source receipt."""
    root = service.paths.repo_root.resolve()
    for anchor in source_anchors:
        if anchor['project'] not in (service.project['project_uuid'], service.project['project_name'], service.project.get('configuration', {}).get('registered_project_id')):
            fail('publication_scope_denied', 'Publication project differs from authenticated authority')
        if anchor['repository'] not in local_repositories(service.project, root):
            fail('publication_scope_denied', 'Publication repository differs from authenticated repository')
        _verify_local_publication_path(root, anchor)
        if source_verifier is not None:
            locator = {**anchor, 'project': service.project['project_uuid']}
            verified_foreign_source(source_verifier, locator)
            # A host callback may perform IO. Fence the actual source again
            # after it returns, immediately before the fact writer proceeds.
            _verify_local_publication_path(root, anchor)


def _verify_local_publication_path(root, anchor):
    """Hash through no-follow directory descriptors; never follow a swapped link."""
    descriptors = []
    try:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(parent)
        parts = [part for part in anchor['path'].split('/') if part and part != '.']
        if not parts:
            fail('source_prerequisite_stale', 'Publication source must be a file')
        for part in parts[:-1]:
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(parent)
        source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        descriptors.append(source)
        if not stat.S_ISREG(os.fstat(source).st_mode):
            fail('source_prerequisite_stale', 'Publication source must be a regular file')
        actual = hashlib.sha256()
        while chunk := os.read(source, 1024 * 1024):
            actual.update(chunk)
        if actual.hexdigest() != anchor['content_sha256']:
            fail('source_prerequisite_stale', 'Publication source hash changed')
    except OSError:
        fail('source_prerequisite_stale', 'Publication source is unavailable or traverses a symlink')
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
