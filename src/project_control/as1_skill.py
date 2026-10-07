"""Observer skill broker: worker-selected sources, direct authority, shared jobs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

from .as1_contracts import RESPONSE_BUDGETS_BYTES, SKILL_ASSEMBLY_DETAIL, SkillSelection, SourceLocator
from .as1_jobs import TERMINAL, stamp
from .as1_packets import mask_payload
from .security import redact_output
from .skills import MAX_FILE_BYTES, SkillError, SkillRegistry, _relative, _stamp

RETIRED_SKILLS = frozenset({'local-coding-worker'})


def _catalog_snapshot(skills_root, registrations=None):
    """Build a bounded catalog from real installed entries and trusted roots.

    ``SkillRegistry`` bounds root inventory and reads entry files through its
    descriptor-pinned, no-symlink reader. Catalog identity is derived from the
    effective entries, not a synthetic file or a Skills-repository sidecar.
    """
    root = Path(skills_root).expanduser().absolute()
    registry = SkillRegistry(root)
    discovered, incomplete, rejected = registry._discover()
    registration_check = registrations is not None
    registrations = dict(registrations or {})
    counts = {}
    for skill in discovered:
        if skill.name not in RETIRED_SKILLS and skill.directory not in RETIRED_SKILLS:
            counts[skill.name] = counts.get(skill.name, 0) + 1
    rows, sources, accepted = [], [], {}
    for skill in discovered:
        if skill.name in RETIRED_SKILLS or skill.directory in RETIRED_SKILLS:
            continue
        entry = f'{skill.directory}/SKILL.md'
        row = {'id': skill.id, 'name': skill.name, 'description': skill.description,
               'entry': entry, 'directory': skill.directory, 'status': 'unregistered'}
        try:
            _raw, digest = _read(registry, entry)
            row['content_sha256'] = digest
            registered = registrations.get(skill.name) if registration_check else None
            expected_root = (root / skill.directory).absolute()
            if counts.get(skill.name, 0) > 1:
                row['status'] = 'duplicate_name'
            elif not registration_check:
                row['status'] = 'accessible'
            elif registered is not None:
                if isinstance(registered, dict):
                    registered_root = Path(registered.get('root', '')).absolute()
                    row['status'] = 'accessible' if registered.get('name') == skill.name and registered_root == expected_root else 'registration_mismatch'
                else:
                    row['status'] = 'registration_mismatch'
            if row['status'] == 'accessible':
                accepted[skill.name] = {'name': skill.name, 'root': str(expected_root)}
            sources.append(SourceLocator(project='skills', repository=str(root), path=entry,
                                         content_sha256=digest))
        except (SkillError, OSError, ValueError):
            row['status'] = 'unavailable'
        rows.append(row)
    identity = {'root': str(root), 'skills': [
        {key: row[key] for key in ('name', 'entry', 'content_sha256', 'status') if key in row}
        for row in rows], 'incomplete': bool(incomplete), 'rejected': int(rejected)}
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    complete = not incomplete and not rejected and all(row['status'] != 'unavailable' for row in rows)
    return {'skills': rows, 'complete': complete, 'incomplete': bool(incomplete),
            'rejected': int(rejected), 'catalog_sha256': digest}, sources, accepted


def registered_skill_roots(skills_root):
    """Resolve the active, real skill registrations from the configured root."""
    snapshot, _sources, registrations = _catalog_snapshot(skills_root)
    if snapshot['incomplete']:
        raise SkillError('inventory_limit', 'Skill root inventory exceeds the limit')
    return registrations


class SkillObserverFactory:
    """Narrow installed-port adapter for registered cross-skill source proof.

    Wrap a receipt-verified TrustedObserverFactory. The original port still
    controls the loop, command sandbox, observations, checkpoints and fences.
    Project Control supplies discovery instructions and real registrations.
    """
    def __init__(self, trusted_factory, *, skills_root):
        self.trusted = trusted_factory
        self.skills = {name: item for name, item in trusted_factory.skills.items()
                       if name not in RETIRED_SKILLS}
        self.trusted.skills = dict(self.skills)
        self.root = Path(skills_root).absolute()

    def __call__(self, service, job):
        worker = self.trusted(service, job)
        if job.mode != 'skill':
            return worker
        validate = worker._validate_selection
        registrations = self.skills

        def validate_registered(selection, initial_skill, observe, guard, reads, *, deadline_epoch=None):
            manifest = SkillSelection.model_validate(selection)
            if len(manifest.selections) > 12:
                raise ValueError('invalid_skill_selection')
            stored = service.lookup(job.job_id, access_scope=job.scope)
            reads = _verified_reads(service.packets, stored['job'], stored['observations'], job.scope)
            valid, errors = 0, []
            for item in manifest.selections:
                if not guard():
                    raise RuntimeError('stale_attempt')
                try:
                    entry = registrations.get(item.skill)
                    if entry is None or entry.get('name') != item.skill:
                        raise ValueError('unregistered_skill')
                    root = Path(entry['root']).absolute()
                    # Exact original entry must be read by this worker. The original
                    # port verifies command roots and selected-resource hashes.
                    proof = reads.get(str(root / 'SKILL.md'))
                    if not proof or proof.get('method') != 'direct_cat':
                        raise ValueError('selected_skill_entry_not_read_agentically')
                    resource_proof = reads.get(str(root / item.resource))
                    if resource_proof and not _entry_precedes_resource(proof, resource_proof):
                        raise ValueError('selected_skill_entry_must_precede_resource')
                    raw, digest = _read(SkillRegistry(root), 'SKILL.md')
                    if proof.get('content_sha256') != digest:
                        raise ValueError('selected_skill_entry_hash_mismatch')
                    validate({'format': manifest.format, 'selections': [item.model_dump(exclude_none=True)],
                              'synthesis': manifest.synthesis}, entry, observe, guard, reads, deadline_epoch=deadline_epoch)
                    valid += 1
                except (ValueError, OSError) as error:
                    if not guard():
                        raise RuntimeError('stale_attempt') from error
                    errors.append(str(error))
            if not valid:
                raise ValueError(errors[0] if errors else 'invalid_skill_selection')
            # Preserve the original model manifest unchanged. The broker rejects
            # each failed selection independently and retains valid excerpts.

        worker._validate_selection = validate_registered
        original_backend = worker.backend
        snapshot, _sources, _accepted = _catalog_snapshot(self.root, registrations)
        allowed = json.dumps([
            {'name': row['name'], 'entry': row['entry'], 'content_sha256': row.get('content_sha256'),
             'description': row['description'], 'root': str(self.root / row['directory'])}
            for row in snapshot['skills'] if row['status'] == 'accessible'
        ], ensure_ascii=False, sort_keys=True)
        class NativeNavigation:
            def run_observer_turn(self, request):
                request = {**request, 'messages': [dict(m) for m in request['messages']]}
                request['messages'][0]['content'] += (
                    ' Project Control discovered this bounded catalog from the configured observer content root; '
                    'there is no bootstrap skill or external catalog file. Effective catalog SHA256: '
                    + snapshot['catalog_sha256'] + '. Registered identities, entry hashes, descriptions and roots: '
                    + allowed + '. Read a relevant registered SKILL.md before its resources. '
                    'Follow its own routes, maps and cross-skill prerequisites; indexes are only navigation aids. '
                    'When a skill name is supplied, keep normal navigation scoped to it and identify cross-skill '
                    'dependencies explicitly. When no skill name is supplied, inspect the registered entries, '
                    'choose relevant skills from their own instructions, and explain why. '
                    'Missing/external/unregistered references must be unresolved. '
                    'Selection reasons and prerequisites must cite resource identities; synthesis must be tiny and evidence-linked.')
                return original_backend.run_observer_turn(request)
            def preemption_status(self, session):
                return original_backend.preemption_status(session)
        worker.backend = NativeNavigation()
        return worker


def _verified_reads(packets, job, observations, scope):
    reads = {}
    for index, observation in enumerate(observations):
        ref = observation.get('packet_id')
        if ref not in job['evidence_packets']:
            continue
        result = packets.lookup(ref, access_scope=scope)
        if result.status != 'ok' or result.packet.tool != 'command':
            continue
        payload = result.packet.payload
        if (payload.get('status') != 'completed' or payload.get('exit_code') != 0
                or payload.get('truncated') or payload.get('timed_out')):
            continue
        for read in payload.get('source_reads', []):
            if isinstance(read, dict) and read.get('method') == 'direct_cat':
                path = read.get('path')
                first = reads.get(path, {}).get('first_observation_index', index)
                # Derive order only from durable broker observations. Never
                # trust order annotations supplied by a tool/model payload.
                reads[path] = {**read, 'first_observation_index': first,
                               'observation_index': index}
    return reads


def _entry_precedes_resource(entry, resource):
    # Equality permits selecting SKILL.md itself as original entry authority.
    return entry['first_observation_index'] <= resource['first_observation_index']


def _read(registry, resource):
    """Reuse the existing descriptor-pinned, no-symlink reader; never an index."""
    with registry._open(_relative(resource)) as fd:
        before = os.fstat(fd)
        if before.st_size > MAX_FILE_BYTES:
            raise SkillError('oversized')
        chunks, size = [], 0
        while True:
            chunk = os.read(fd, min(65536, MAX_FILE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk); size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise SkillError('oversized')
        if _stamp(before) != _stamp(os.fstat(fd)) or size != before.st_size:
            raise SkillError('stale_resource')
        raw = b''.join(chunks)
    return raw, hashlib.sha256(raw).hexdigest()


class SkillService:
    """Public adapter seam; host owns JobService lifespan and trusted scope.

    The bounded catalog is derived directly from real skill entries. Unnamed
    inquiries are routed by the observer over those entries without a pseudo
    bootstrap skill.
    """
    def __init__(self, jobs, *, skills_root, clock=time.time):
        self.jobs, self.packets, self.clock = jobs, jobs.packets, clock
        self.root = Path(skills_root).absolute()
        self.registry = SkillRegistry(self.root)
        # Roots are host-owned registrations. No caller can supply another root.
        self.skills = {name: item for name, item in getattr(jobs.worker_factory, 'skills', {}).items()
                       if name not in RETIRED_SKILLS}
        self.readers = {name: SkillRegistry(Path(entry['root'])) for name, entry in self.skills.items()}
        if jobs.skill_catalog_identity_provider is None:
            jobs.skill_catalog_identity_provider = self.catalog_identity

    def catalog_identity(self):
        return _catalog_snapshot(self.root, self.skills)[0]['catalog_sha256']

    @staticmethod
    def _authorize(scope):
        if scope.get('profile') != 'observer' or not scope.get('principal'):
            raise PermissionError('skill is observer-only with trusted principal scope')

    def catalog(self, *, access_scope):
        self._authorize(access_scope)
        try:
            catalog, sources, _accepted = _catalog_snapshot(self.root, self.skills)
        except (SkillError, OSError, ValueError, UnicodeError):
            return {'status': 'unavailable', 'complete': False, 'reason': 'skill_catalog_unavailable'}
        payload = {'status': 'ok', **catalog, 'external_dependencies': [], 'unsupported': [],
                   'continuation': None, 'authority': 'Project Control bounded skill discovery',
                   'freshness': {'checked_at': stamp(self.clock()), 'max_age_seconds': 0}}
        packet = self.packets.create(tool='skill', payload=payload, access_scope=access_scope,
                                     freshness=payload['freshness'], sources=sources)
        return {**packet.payload, 'packet_id': packet.packet_id, 'alias': packet.alias}

    def inquire(self, *, access_scope, query=None, skill=None, hints=(), request_id=None, detail=SKILL_ASSEMBLY_DETAIL):
        """Public cached inquiry; submit/poll below are private compatibility seams."""
        self._authorize(access_scope)
        if detail not in RESPONSE_BUDGETS_BYTES:
            raise ValueError('invalid detail')
        if not query and not skill:
            return self.catalog(access_scope=access_scope)
        selected = skill
        if selected is not None and selected not in self.skills:
            return {'status': 'unavailable', 'reason': 'unregistered_skill', 'skill': selected}
        question = query if query is not None else 'Read the installed entry and select the instructions relevant to using this skill.'
        execution = question
        if not skill:
            execution += ('\nNo skill name was supplied. Use Project Control registered skill entries to discover '
                'relevant instructions and references. Report missing, external or unregistered dependencies explicitly.')
        value = self.jobs.inquire(question=question, execution_question=execution, access_scope=access_scope,
            mode='skill', skill=selected, hints=hints, request_id=request_id)
        if value.get('job') and value['status'] in {'completed', 'partial'}:
            return self.poll(value['job']['job_id'], access_scope=access_scope, detail=detail, _inquiry=True)
        return value

    def submit(self, *, access_scope, query=None, skill=None, hints=(), request_id=None, job_id=None, detail=SKILL_ASSEMBLY_DETAIL):
        self._authorize(access_scope)
        if detail not in RESPONSE_BUDGETS_BYTES:
            raise ValueError('invalid detail')
        if job_id:
            return self.poll(job_id, access_scope=access_scope, detail=detail)
        if not query and not skill:
            return self.catalog(access_scope=access_scope)
        selected = skill
        if selected is not None and selected not in self.skills:
            return {'accepted': False, 'reason': 'unregistered_skill', 'skill': selected}
        question = query or 'Read the installed entry and select the instructions relevant to using this skill.'
        if not skill:
            # Explicit discovery request. The worker decides; PC does not match
            # the question to names, maps, architecture or reference paths.
            question += ('\nNo skill name was supplied. Use Project Control registered skill entries to discover '
                         'relevant instructions and references. Report missing, external or unregistered dependencies explicitly.')
        return self.jobs.submit(question=question, access_scope=access_scope, request_id=request_id,
                                mode='skill', skill=selected, hints=hints)

    def poll(self, job_id, *, access_scope, detail=SKILL_ASSEMBLY_DETAIL, _inquiry=False):
        self._authorize(access_scope)
        value = (self.jobs.lookup_inquiry if _inquiry else self.jobs.lookup)(job_id, access_scope=access_scope)
        if value['status'] != 'ok':
            return value
        if value['job']['mode'] != 'skill':
            return {'status': 'wrong_job_mode'}
        if value['job']['status'] not in TERMINAL:
            return {'status': value['job']['status'], 'job_id': job_id, 'attempt': value['job']['attempt']}
        # Terminal commit precedes cross-store outbox materialization. Replay
        # the existing broker outbox before resolving; this never runs a model.
        self.jobs.reconcile()
        ref = value['job']['result_packet']
        result = self.packets.lookup(ref, access_scope=access_scope) if ref else None
        if not result or result.status != 'ok':
            return {'status': value['job']['status'], 'job_id': job_id, 'reason': 'selection_unavailable'}
        manifest = result.packet.payload.get('skill_selection')
        if not manifest:
            return {'status': 'partial', 'job_id': job_id, 'reason': result.packet.payload.get('reason', 'selection_unavailable'),
                    'unresolved': value['job']['unresolved_questions']}
        return self.assemble(manifest, access_scope=access_scope, observations=value['observations'],
                             parents=[ref], detail=detail, job_id=job_id, attempt=value['job']['attempt'], _inquiry=_inquiry)

    def assemble(self, selection, *, access_scope, observations=(), parents=(), detail=SKILL_ASSEMBLY_DETAIL, job_id=None, attempt=None, _inquiry=False):
        self._authorize(access_scope)
        if detail not in RESPONSE_BUDGETS_BYTES:
            raise ValueError('invalid detail')
        selected = SkillSelection.model_validate(selection)
        # Read proofs must come from durable, scope-checked broker observations,
        # not a caller-created list. Public assembly requires a terminal skill job.
        if job_id is None:
            raise ValueError('assembly requires durable job identity')
        stored = (self.jobs.lookup_inquiry if _inquiry else self.jobs.lookup)(job_id, access_scope=access_scope)
        if (stored['status'] != 'ok' or stored['job']['mode'] != 'skill' or
                stored['job']['status'] not in {'completed', 'partial'} or stored['job']['attempt'] != attempt):
            return {'status': 'stale_attempt', 'job_id': job_id}
        ref = stored['job']['result_packet']
        result = self.packets.lookup(ref, access_scope=access_scope)
        if result.status != 'ok' or result.packet.payload.get('skill_selection') != selected.model_dump(exclude_none=True):
            # Allow explicit null optional fields in the producer wire value.
            if result.status != 'ok' or SkillSelection.model_validate(result.packet.payload.get('skill_selection')).model_dump() != selected.model_dump():
                return {'status': 'unverified_selection', 'job_id': job_id}
        parents = [ref]
        for hint in stored['job']['hints']:
            resolved = self.packets.lookup(hint, access_scope=access_scope)
            if resolved.status == 'ok' and resolved.packet.packet_id not in parents:
                parents.append(resolved.packet.packet_id)
        observations = stored['observations']
        reads = _verified_reads(self.packets, stored['job'], observations, stored['job']['scope'] if _inquiry else access_scope)
        excerpts, omissions, sources = [], [], []
        budget = RESPONSE_BUDGETS_BYTES[detail]
        used = 0
        for index, item in enumerate(selected.selections):
            identity = {'selection': index, 'skill': item.skill, 'resource': item.resource}
            try:
                reader = self.readers.get(item.skill)
                if reader is None:
                    raise SkillError('unregistered_skill')
                entry_proof = reads.get(str(reader.root / 'SKILL.md'))
                if not entry_proof or entry_proof.get('method') != 'direct_cat':
                    raise SkillError('unverified_skill_entry')
                if _read(reader, 'SKILL.md')[1] != entry_proof.get('content_sha256'):
                    raise SkillError('stale_skill_entry')
                path = reader.root / item.resource
                proof = reads.get(str(path))
                if not proof or proof.get('method') != 'direct_cat' or proof.get('content_sha256') != item.content_sha256:
                    raise SkillError('unverified_reader_hash')
                if not _entry_precedes_resource(entry_proof, proof):
                    raise SkillError('skill_entry_read_after_resource')
                raw, digest = _read(reader, item.resource)
                if digest != item.content_sha256:
                    raise SkillError('stale_resource')
                text = raw.decode('utf-8')
                if redact_output(text) != text or mask_payload(text)[0] != text:
                    raise SkillError('redacted_resource')
                lines = text.splitlines(keepends=True)
                if item.line_end > len(lines):
                    raise SkillError('out_of_range')
                excerpt = ''.join(lines[item.line_start - 1:item.line_end])
                if used + len(excerpt.encode()) > budget * .75:
                    raise SkillError('excerpt_budget')
                used += len(excerpt.encode())
                ident = 'excerpt_' + str(index)
                excerpts.append({**identity, 'id': ident, 'content': excerpt, 'content_sha256': digest,
                                 'line_start': item.line_start, 'line_end': item.line_end,
                                 'reason': item.reason, 'prerequisites': item.prerequisites or [],
                                 'authority': 'direct original text', 'verbatim': True})
                relative = path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else item.resource
                repository = self.root if path.is_relative_to(self.root) else reader.root
                sources.append(SourceLocator(project='skills', repository=str(repository), path=relative,
                                             content_sha256=digest, line_start=item.line_start, line_end=item.line_end))
            except (SkillError, UnicodeError) as error:
                omissions.append({**identity, 'reason': getattr(error, 'code', 'non_utf8_resource')})
        unresolved = list(dict.fromkeys((selected.unresolved or []) + stored['job']['unresolved_questions']))
        def current_dependency(key):
            prefix = 'file:' + str(self.root) + '/'
            if key.startswith(prefix):
                try:
                    return _read(self.registry, key[len(prefix):])[1]
                except SkillError:
                    return None
            for name, reader in self.readers.items():
                prefix = 'file:' + str(reader.root) + '/'
                if key.startswith(prefix):
                    try:
                        return _read(reader, key[len(prefix):])[1]
                    except SkillError:
                        return None
            return None
        retained = self.packets.assemble_hints(stored['job']['hints'], access_scope=access_scope,
                                             current_dependencies=current_dependency)
        retained_dependencies = []
        for excerpt in excerpts:
            for prerequisite in excerpt['prerequisites']:
                matched = [e['id'] for e in excerpts if prerequisite in {e['id'], e['resource'], e['skill'] + '/' + e['resource']}]
                if not matched:
                    match = next((p['packet'] for p in retained['packets'] if p['freshness'] == 'current'
                                  and prerequisite in {p['packet'].get('packet_id'), p['packet'].get('alias')}), None)
                    if match:
                        retained_dependencies.append({'prerequisite': prerequisite, 'packet_id': match['packet_id']})
                    else:
                        unresolved.append('Missing or unverified prerequisite: ' + prerequisite)
        synthesis_limit = min(768, int(budget * .12), max(0, int(used * .15)))
        synthesis = selected.synthesis.encode()[:synthesis_limit].decode('utf-8', errors='ignore')
        if len(synthesis) < len(selected.synthesis):
            omissions.append({'reason': 'synthesis_budget', 'continuation': 'worker selection retained in job result'})
        payload = {'status': 'partial' if omissions or unresolved else 'completed', 'job_id': job_id, 'attempt': attempt,
                   'synthesis': {'label': 'local-agent synthesis; not authoritative source text', 'text': synthesis,
                                 'excerpt_refs': [e['id'] for e in excerpts], 'entailment_verified': False},
                   'excerpts': excerpts, 'omissions': omissions, 'unresolved': unresolved,
                   'retained_dependencies': retained_dependencies,
                   'freshness': {'checked_at': stamp(self.clock()), 'max_age_seconds': 0,
                                 'basis': 'selected full-file hashes checked during this assembly only'},
                   'continuation': {'job_id': job_id, 'detail': 'extended'} if omissions else None}
        # Check again after reads: cancelled/superseded jobs cannot publish stale
        # authority. Terminal records are immutable in the shared job service.
        current = (self.jobs.lookup_inquiry if _inquiry else self.jobs.lookup)(job_id, access_scope=access_scope)
        if current['status'] != 'ok' or current['job'] != stored['job']:
            return {'status': 'stale_attempt', 'job_id': job_id}
        packet_payload = payload
        if _inquiry:
            from .as1_surface import public_inquiry
            packet_payload = public_inquiry(payload)
        packet = self.packets.create(tool='skill', payload=packet_payload, access_scope=stored['job']['scope'] if _inquiry else access_scope, sources=sources,
                                     parents=parents, freshness=payload['freshness'], omissions=omissions)
        return {**packet.payload, 'packet_id': packet.packet_id, 'alias': packet.alias}
