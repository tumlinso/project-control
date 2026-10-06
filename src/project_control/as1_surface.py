"""Startup composition and MCP adapters for the qualified AS1 producer ports."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import time
from typing import Any, Literal, get_args

from mcp.types import ToolAnnotations
from pydantic import ConfigDict, Field, ValidationError, create_model
from .as1_context import ContextHost, InformationService
from .as1_contracts import (ExactEntityQuery, ImpactTarget, SKILL_ASSEMBLY_DETAIL,
                            SourceLocator, canonical_digest, relative_path)
from .as1_control import ControlService, ProjectAmendment, MaintenanceRequest
from .as1_jobs import JobService, TrustedObserverFactory, InvalidToolArguments, ObserverLogArguments
from .as1_packets import SQLitePacketStore
from .as1_skill import SkillService, SkillObserverFactory, _verified_reads, _entry_precedes_resource
from .as1_trace import TraceService
from .config import DEFAULT_DENY_PATTERNS, configured_observer_skills_root
from .observer_analysis import SkillsObserverAnalysisProvider, observer_analysis_state_root
from .runtime_binding import local_runtime_identity
from .profiles import MCPProfile
from .models import (DeltaSince, EvidenceInput, HistoryTraceInput, InspectInput,
                     ArchitectureContextInput, SourceContextInput, CoordinationViewInput)
from .services.machine_inspection import MachineDiagnostic
from .security import is_denied

# Historical observer-source digest retained for import compatibility. Runtime
# integrity is bound to the verified receiver source during composition.
QUALIFIED_OBSERVER_RUNTIME_SHA256 = 'd3a65e54aaf4a6f0c6d38621d521ee0402aba749da4bf2a543c0df75550e580f'
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
ANALYSIS_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
_NATIVE_INPUT_ERROR_TITLES = frozenset(model.__name__ for model in (
    DeltaSince, EvidenceInput, HistoryTraceInput, InspectInput,
    ArchitectureContextInput, SourceContextInput, CoordinationViewInput))
_WORKER_EXACT_QUERY = create_model('ObserverExactEntityQuery', __base__=ExactEntityQuery,
    kind=(Literal[(*get_args(InspectInput.model_fields['kind'].annotation),
                   'packet', 'investigation', 'registration')], ...))


class _AutomaticSelectedReadCommand:
    """Read one sealed source through an existing command envelope, without exec."""

    MAX_BYTES = 256 * 1024
    MAX_VISIBLE_BYTES = 8 * 1024

    def __init__(self, delegate, *, project, selected, validate_current):
        self.delegate = delegate
        self.project = project
        self.validate_current = validate_current
        entries = {}
        for alias, relative, root, digest in selected:
            entries[(alias, relative)] = (Path(root).resolve(strict=True), digest)
        self.selected = entries
        self.roots = tuple(dict.fromkeys(root for root, _digest in entries.values()))
        self.root_identities = {}
        for root in self.roots:
            descriptor = self._open_directory_nofollow(root)
            try:
                info = os.fstat(descriptor)
                self.root_identities[root] = (info.st_dev, info.st_ino)
            finally:
                os.close(descriptor)

    def _packet(self, payload, guard=None):
        return self.delegate._packet(payload, guard)

    def allows(self, path):
        try:
            resolved = Path(path).resolve(strict=True)
        except (OSError, RuntimeError):
            return False
        return any(resolved == root / relative
                   for (_alias, relative), (root, _digest) in self.selected.items())

    @staticmethod
    def _identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    @staticmethod
    def _open_directory_nofollow(path):
        path = Path(path)
        if not path.is_absolute() or path == Path('/'):
            raise ValueError('trusted_repository_root_invalid')
        descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in path.parts[1:]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _read(self, root, relative):
        descriptor = self._open_directory_nofollow(root)
        leaf = None
        try:
            root_info = os.fstat(descriptor)
            if (not stat.S_ISDIR(root_info.st_mode)
                    or (root_info.st_dev, root_info.st_ino) != self.root_identities[root]):
                raise ValueError('trusted_repository_root_changed')
            parts = relative.split('/')
            for component in parts[:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                           dir_fd=descriptor)
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError('selected_source_not_regular')
            if before.st_size > self.MAX_BYTES:
                raise ValueError('selected_source_too_large')
            with os.fdopen(os.dup(leaf), 'rb') as stream:
                raw = stream.read(self.MAX_BYTES + 1)
            after = os.fstat(leaf)
            current = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
            if (len(raw) > self.MAX_BYTES or self._identity(before) != self._identity(after)
                    or self._identity(after) != self._identity(current)):
                raise ValueError('selected_source_changed_during_read')
            if b'\0' in raw:
                raise ValueError('selected_source_not_text')
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError('selected_source_not_text') from None
            return raw, text
        finally:
            if leaf is not None:
                os.close(leaf)
            os.close(descriptor)

    def run(self, argv, cwd, timeout_seconds=10, max_output_bytes=8192, *, guard=None,
            deadline_epoch=None):
        if guard is not None and not guard():
            raise RuntimeError('stale_attempt')
        base = {'status': 'denied', 'exit_code': None, 'stdout': '', 'stderr': '',
                'truncated': False, 'timed_out': False, 'duration_seconds': 0.0,
                'scope': {'roots': [], 'external': False, 'provenance': 'sealed_automatic'}}
        if (not isinstance(argv, list) or len(argv) != 2 or argv[0] != 'cat'
                or not isinstance(argv[1], str) or not isinstance(cwd, str)
                or isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not 0 < timeout_seconds <= 60 or isinstance(max_output_bytes, bool)
                or not isinstance(max_output_bytes, int) or not 1 <= max_output_bytes <= 65536):
            return self._packet({**base, 'reason': 'automatic_command_denied'}, guard)
        matches = [(root, alias, relative, digest)
                   for (alias, relative), (root, digest) in self.selected.items()
                   if argv[1] == str(root / relative) and cwd == str(root)]
        if len(matches) != 1:
            return self._packet({**base, 'reason': 'automatic_source_not_selected'}, guard)
        root, alias, relative, expected_digest = matches[0]
        if not self.allows(root / relative):
            return self._packet({**base, 'reason': 'automatic_source_not_selected'}, guard)
        try:
            if not self.validate_current():
                return self._packet({**base, 'reason': 'automatic_scope_unavailable'}, guard)
        except Exception:
            return self._packet({**base, 'reason': 'automatic_scope_unavailable'}, guard)
        try:
            raw, text = self._read(root, relative)
        except (OSError, ValueError) as error:
            return self._packet({**base, 'reason': str(error)[:96]}, guard)
        if guard is not None and not guard():
            raise RuntimeError('stale_attempt')
        try:
            if not self.validate_current():
                return self._packet({**base, 'reason': 'automatic_scope_unavailable'}, guard)
        except Exception:
            return self._packet({**base, 'reason': 'automatic_scope_unavailable'}, guard)
        if guard is not None and not guard():
            raise RuntimeError('stale_attempt')
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_digest:
            return self._packet({**base, 'reason': 'automatic_source_changed'}, guard)
        locator = SourceLocator(project=self.project, repository=alias, path=relative,
                                content_sha256=digest)
        output_budget = min(max_output_bytes, self.MAX_VISIBLE_BYTES)
        output = raw[:output_budget].decode('utf-8', errors='ignore')
        return self._packet({**base, 'status': 'completed', 'exit_code': 0,
            'stdout': output, 'truncated': len(raw) > len(output.encode('utf-8')),
            'source_reads': [{'path': str(root / relative), 'content_sha256': digest,
                              'line_count': len(text.splitlines()), 'method': 'selected_file_read'}],
            'sources': [locator.model_dump(exclude_none=True)],
            'operation': 'selected_file_read', 'executed_subprocess': False}, guard)


def observer_tool_argument_models(profile):
    """Describe the internal information adapter, using native field contracts.

    Project is optional because the durable admission scope supplies its default.
    Detail follows the original inquiry profile, never the dispatcher's profile.
    """
    detail = Literal['compact', 'standard', 'extended'] if profile == 'observer' else Literal['compact', 'standard']
    common = {'project': (str | None, None), 'detail': (detail, 'compact')}
    def native_fields(model, names):
        return {name: (model.model_fields[name].annotation, model.model_fields[name]) for name in names}
    fields = {
        'overview': {},
        'delta': {'since': (str | DeltaSince, ...)},
        'frontier': {'scope': (dict[str, Any] | None, None)},
        'search': {'query': (str | _WORKER_EXACT_QUERY, ...), 'scope': (dict[str, Any] | None, None)},
        'evidence': native_fields(EvidenceInput, ('subject', 'kinds', 'max_items')),
        'history': native_fields(HistoryTraceInput, tuple(name for name in HistoryTraceInput.model_fields if name not in {'project', 'detail'})),
        'impact': {'targets': (list[ImpactTarget], Field(min_length=1, max_length=32)),
                   'change_class': (Literal['body', 'interface', 'configuration', 'generator', 'removal', 'unknown'], 'unknown'),
                   'mode': (Literal['paths', 'snippets'], 'paths')},
        'machine': {'query_or_view': (MachineDiagnostic, 'host_memory')},
    }
    return {name: create_model('Observer' + name.title() + 'Arguments',
        __config__=ConfigDict(extra='forbid', strict=True), **common, **arguments)
        for name, arguments in fields.items()}


def observer_tool_argument_schemas(profile):
    schemas = {name: model.model_json_schema() for name, model in observer_tool_argument_models(profile).items()}
    schemas['log'] = ObserverLogArguments.model_json_schema()
    command = create_model('ObserverCommandArguments', __config__=ConfigDict(extra='forbid', strict=True),
        argv=(list[str], Field(min_length=1, max_length=128)), cwd=(str, ...),
        timeout_seconds=(int | float, Field(default=10, gt=0, le=60)),
        max_output_bytes=(int, Field(default=8192, ge=1, le=65536)))
    schemas['command'] = command.model_json_schema()
    def compact(value):
        if isinstance(value, dict):
            return {key: compact(item) for key, item in value.items() if key not in {'title', 'description'}}
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value
    return compact(schemas)


def _analysis_runtime_identity(runtime_root, observer_runtime_sha256, *, source_verification_state='source_verified'):
    """Bind inquiry cache entries to the validated receiver runtime and broker logic."""
    identity = local_runtime_identity(root=runtime_root)
    root = identity.root
    runtime_files = (
        'config/production-profile.toml',
        'local_worker/servers/llama_cpp.py',
        'local_worker/supervisor.py',
    )
    max_file_bytes = 1024 * 1024

    def file_digest(path):
        if path.stat().st_size > max_file_bytes:
            raise ValueError('analysis_runtime_identity_file_too_large')
        data = path.read_bytes()
        if len(data) > max_file_bytes:
            raise ValueError('analysis_runtime_identity_file_too_large')
        return hashlib.sha256(data).hexdigest()

    runtime_digests = {}
    for relative in runtime_files:
        path = (root / relative).resolve(strict=True)
        path.relative_to(root)
        runtime_digests[relative] = file_digest(path)

    observer_path = root / 'local_worker/observer_runtime.py'
    observer_digest = file_digest(observer_path)
    if observer_digest != observer_runtime_sha256:
        raise ValueError('observer runtime receipt mismatch')

    producer_root = Path(__file__).resolve(strict=True).parent
    # The inquiry epoch follows every controller component that can change
    # which runtime work is admitted or resumed. Keep model qualification as a
    # separate receipt; these source hashes establish cache invalidation only.
    producer_files = ('as1_jobs.py', 'as1_surface.py', 'as1_skill.py',
                      'as1_context.py', 'as1_packets.py', 'assistance/knowledge.py',
                      'assistance/frames.py', 'assistance/power.py',
                      'assistance/policies.py', 'assistance/resources.py')
    producer_digests = {}
    for relative in producer_files:
        path = (producer_root / relative).resolve(strict=True)
        path.relative_to(producer_root)
        producer_digests[relative] = file_digest(path)
    return canonical_digest({
        'receiver_runtime_root': str(identity.root),
        'receiver_source_root': identity.source_root,
        # The verified file fingerprint, not manifest metadata such as the
        # repository commit label or manifest byte digest, defines this cache
        # epoch. An unrelated commit that leaves the receiver inventory
        # unchanged must not evict inquiry results.
        'receiver_fingerprint': identity.fingerprint,
        'qualified_observer_runtime_sha256': observer_runtime_sha256,
        # This describes source integrity only. Runtime receipt equality does
        # not prove current model or device qualification.
        'source_verification_state': source_verification_state,
        'live_qualification': None,
        'receiver_runtime_files_sha256': runtime_digests,
        'analysis_producer_files_sha256': producer_digests,
    })


# Broker mechanics remain private even when a producer nests a result/continuation.
_PRIVATE_INQUIRY_FIELDS = {'job', 'observations', 'job_id', 'prior_job', 'attempt',
    'lease', 'lease_until', 'lease_owner', 'queue_position', 'queue', 'poll',
    'accepted', 'retry', 'immediate', 'request_id', 'execution_question',
    'refresh_context', 'deadline_epoch', 'attempt_generation', 'lease_epoch', 'poll_after_seconds',
    'attempts', 'leases', 'lease_expires_at', 'lease_expires_epoch', 'lease_seconds',
    'attempt_count', 'scheduler_generation', 'queue_depth'}


def public_inquiry(value):
    if isinstance(value, dict):
        if value.get('status') == 'thinking':
            return {'status': 'thinking', 'message': 'Read-only analysis is in progress. Continue useful work and repeat the identical question later; avoid submitting variants.'}
        if value.get('status') == 'busy':
            return {'status': 'busy', 'message': 'Read-only analysis is busy. This question was not accepted. Use search, read or evidence to contextualize or refine a later question.'}
        return {key: public_inquiry(item) for key, item in value.items()
                if key not in _PRIVATE_INQUIRY_FIELDS}
    if isinstance(value, list):
        return [public_inquiry(item) for item in value]
    return value


class InquiryFreshness:
    """Validate the retained material dependency manifest, never repository HEAD."""
    def __init__(self, composition):
        self.c = composition

    def __call__(self, job):
        c, scope = self.c, job['scope']
        changed, checked, seen = [], [], set()
        roots = [c.skills.root]
        project = scope.get('project')
        permitted_projects = c.host.projects if project == 'catalog' else ({project} & c.host.projects)
        for permitted in permitted_projects:
            roots += [r.root for r in c.control.registry.workspace(permitted).repositories.values()]

        def file_hash(path):
            path = Path(path)
            for root in roots:
                root = Path(root).absolute()
                if path.is_absolute() and path.is_relative_to(root):
                    try:
                        return hashlib.sha256(InformationService._working_bytes(root, path.relative_to(root).as_posix())).hexdigest()
                    except (OSError, ValueError):
                        return None
            return None

        def dependency(key):
            if key.startswith('file:'):
                target = key[5:]
                if target.startswith('/'):
                    return file_hash(target)
                alias, _, path = target.partition('/')
                if project in c.host.projects:
                    try:
                        return file_hash(c.control.registry.repository(project, alias).root / path)
                    except (KeyError, ValueError):
                        return None
            for prefix, provider in (('semantic_revision:', lambda p: c.information.snapshot_provider(p).todo_revision),
                                     ('semantic_context_revision:', lambda p: c.control.project_context(p).get('project_revision'))):
                if key.startswith(prefix):
                    target = key[len(prefix):]
                    if target not in permitted_projects:
                        return None
                    try:
                        value = provider(target)
                        return hashlib.sha256(str(value).encode()).hexdigest() if value is not None else None
                    except (OSError, ValueError, KeyError):
                        return None
            return None

        def visit(ref, result=False):
            if ref in seen:
                return
            seen.add(ref)
            lookup = c.store.lookup(ref, access_scope=scope)
            if lookup.status != 'ok':
                changed.append({'reference': ref, 'reason': lookup.status}); return
            packet = lookup.packet
            for parent in packet.parents:
                visit(parent)
            nested = packet.payload.get('packet')
            if isinstance(nested, str):
                visit(nested)
                return
            reads = packet.payload.get('source_reads', []) if packet.tool == 'command' else []
            if reads:
                for read in reads:
                    valid = (packet.payload.get('status') == 'completed' and packet.payload.get('exit_code') == 0
                             and not packet.payload.get('truncated') and not packet.payload.get('timed_out'))
                    method = read.get('method')
                    line_ranges = read.get('line_ranges')
                    line_window_valid = (method == 'direct_sed_lines'
                        and isinstance(read.get('line_count'), int) and not isinstance(read.get('line_count'), bool)
                        and read['line_count'] > 0
                        and isinstance(line_ranges, list) and 1 <= len(line_ranges) <= 16
                        and all(isinstance(item, dict)
                                and isinstance(item.get('start'), int) and not isinstance(item.get('start'), bool)
                                and isinstance(item.get('end'), int) and not isinstance(item.get('end'), bool)
                                and 1 <= item['start'] <= item['end'] <= read['line_count']
                                for item in line_ranges))
                    proof_valid = method == 'direct_cat' or line_window_valid
                    actual = file_hash(read.get('path', '')) if valid and proof_valid else None
                    checked.append({'path': read.get('path'), 'content_sha256': read.get('content_sha256')})
                    if not actual or actual != read.get('content_sha256'):
                        changed.append({'path': read.get('path'), 'reason': 'unverified' if actual is None else 'changed'})
            elif not result or packet.sources or (packet.freshness or {}).get('dependencies'):
                def packet_dependency(key):
                    matches = [source for source in packet.sources
                               if key == f'file:{source.repository}/{source.path}']
                    if matches and not matches[0].repository.startswith('/'):
                        values = set()
                        for source in matches:
                            if source.project not in permitted_projects:
                                return None
                            try:
                                values.add(file_hash(c.control.registry.repository(source.project, source.repository).root / source.path))
                            except (OSError, KeyError, ValueError):
                                return None
                        return next(iter(values)) if len(values) == 1 else None
                    return dependency(key)
                assembled = c.store.assemble_hints([ref], access_scope=scope, current_dependencies=packet_dependency,
                                                   budget_bytes=8*1024*1024)
                checked.extend(s.model_dump() for s in packet.sources)
                checked.extend({'dependency': k} for k in (packet.freshness or {}).get('dependencies', {}))
                changed.extend(o for o in assembled['omissions'] if o['reason'] != 'duplicate_content')
        # Accepted finding citations define answer dependencies. Observations and
        # the derived result's aggregate provenance also contain incidental reads.
        citations = [ref for finding in job.get('findings', [])
                     for ref in finding.get('evidence_packets', [])]
        refs = list(dict.fromkeys(citations if citations else
                                 job.get('hints', []) + job.get('evidence_packets', [])))
        for ref in refs:
            visit(ref)
        if job.get('result_packet'):
            if citations:
                result = c.store.lookup(job['result_packet'], access_scope=scope)
                if result.status != 'ok':
                    changed.append({'reference': job['result_packet'], 'reason': result.status})
            else:
                visit(job['result_packet'], result=True)
        else:
            changed.append({'reason': 'result_missing'})
        if job.get('mode') == 'skill' and job.get('result_packet'):
            result = c.store.lookup(job['result_packet'], access_scope=scope)
            stored = c.jobs.lookup(job['job_id'], access_scope=scope)
            reads = _verified_reads(c.store, job, stored.get('observations', []), scope)
            selections = (result.packet.payload.get('skill_selection') or {}).get('selections', []) if result.status == 'ok' else []
            if not selections:
                changed.append({'reason': 'selection_manifest_missing'})
            for item in selections:
                reader = c.skills.readers.get(item.get('skill'))
                entry = reads.get(str(reader.root / 'SKILL.md')) if reader else None
                resource = reads.get(str(reader.root / item.get('resource', ''))) if reader else None
                if (not entry or not resource or not _entry_precedes_resource(entry, resource)
                        or resource.get('content_sha256') != item.get('content_sha256')):
                    changed.append({'skill': item.get('skill'), 'resource': item.get('resource'),
                                    'reason': 'selection_proof_missing'})
                # Selection authority remains material even when findings cite
                # only the selected resource and omit its prerequisite entry.
                for read in (entry, resource):
                    if read:
                        actual = file_hash(read.get('path', ''))
                        checked.append({'path': read.get('path'), 'content_sha256': read.get('content_sha256')})
                        if not actual or actual != read.get('content_sha256'):
                            changed.append({'path': read.get('path'),
                                            'reason': 'unverified' if actual is None else 'changed'})
        if not checked:
            changed.append({'reason': 'dependency_manifest_missing'})
        return {'fresh': not changed, 'changed_sources': changed, 'dependencies': checked}


class SurfaceComposition:
    def start(self):
        if self.jobs.worker_factory is not None:
            self.jobs.start()
            self._start_attention_watcher()

    def _start_attention_watcher(self):
        tick = getattr(self, 'attention_tick', None)
        if not callable(tick):
            return
        thread = getattr(self, '_attention_thread', None)
        if thread is not None and thread.is_alive():
            return
        self._attention_stop = threading.Event()
        interval = float(getattr(self, 'attention_interval_seconds', 1.0))
        if not 0.05 <= interval <= 60:
            raise ValueError('attention_interval_out_of_bounds')

        def run():
            while not self._attention_stop.is_set():
                try:
                    tick()
                except Exception as error:
                    self.attention_last_error = type(error).__name__
                if self._attention_stop.wait(interval):
                    break

        self._attention_thread = threading.Thread(target=run,
            name='pc-source-attention-reconciler', daemon=True)
        self._attention_thread.start()

    def close(self):
        stop = getattr(self, '_attention_stop', None)
        thread = getattr(self, '_attention_thread', None)
        if stop is not None:
            stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=float(getattr(self, 'attention_join_timeout', 10.0)))
            if thread.is_alive():
                # Do not close the broker or backend underneath an active tick.
                return False
        stopped = self.jobs.shutdown()
        close = getattr(self.backend, 'close', None)
        if stopped and close:
            close()
        return stopped

    def publish_context(self, protocol, workflow_handle, request):
        if set(request) - {'kind', 'task_id', 'payload'}:
            raise ValueError('invalid_publication_fields')
        capability = protocol.capabilities.resolve(workflow_handle,
            required_operation='coordinate:publish_project_context', expected_class='first_class')
        root = capability.repository_root
        matches = [project for project in self.host.projects
                   if self.control.registry.workspace(project).authority_repository
                   and self.control.registry.repository(project,
                       self.control.registry.workspace(project).authority_repository).root.resolve() == root]
        if len(matches) != 1:
            raise PermissionError('publication_registered_authority_required')
        return self.control.publish_workflow_context(matches[0], request,
            workflow_handle=workflow_handle, protocol=protocol)

    def scope(self, project):
        if project is not None and project not in self.host.projects:
            raise PermissionError('project_not_permitted')
        scope = self.host.scope(project)
        if project is None:
            scope['catalog_projects'] = sorted(self.host.projects)
        return scope

    def can_execute_inquiry(self, job, shared):
        project = job.scope.get('project')
        if project == 'catalog':
            manifest = job.scope.get('catalog_projects')
            return manifest is None or set(manifest) <= self.host.projects
        return project in self.host.projects

    def inquiry_access(self, required, supplied, *, log=False):
        context = JobService.inquiry_context(supplied)
        if not log and required != context:
            return False
        permitted = self.host.projects & set(supplied.get('catalog_projects', self.host.projects))
        if required.get('project') == 'catalog':
            return ('catalog_projects' in required
                    and set(required['catalog_projects']) <= permitted)
        return (required.get('project') in permitted
                and {k: v for k, v in required.items() if k != 'project'}
                    == {k: v for k, v in context.items() if k not in {'project', 'catalog_projects'}})

    def packet_access(self, required, supplied, sources):
        """Trusted host source authority, independent of reader identity/role."""
        project = required.get('project')
        permitted = self.host.projects & set(supplied.get('catalog_projects', self.host.projects))
        if project is not None and project != 'catalog' and project not in permitted:
            return False
        if any(source.get('project') not in {*permitted, 'skills', 'catalog'} for source in sources):
            return False
        manifest = required.get('catalog_projects')
        if manifest is not None and not set(manifest) <= permitted:
            return False
        # These values come from registered host authority, never tool arguments.
        trusted = {**supplied, 'project': project}
        if manifest is not None:
            trusted['catalog_projects'] = manifest
        return SQLitePacketStore._authorized(required, trusted)


def compose_surface(runtime, profile, *, host=None, state_directory=None, backend=None,
                    observer_runtime_sha256=None, coder_claim_provider=None, command_port=None):
    c = SurfaceComposition()
    config = runtime.config
    canonical = 'coder' if profile == MCPProfile.CODEX else profile.value
    c.host = host or ContextHost(canonical, 'project-control-' + canonical, frozenset(config.workspaces))
    if c.host.profile != canonical or not c.host.projects <= config.workspaces.keys():
        raise ValueError('startup_host_profile_or_scope_mismatch')
    state_root = state_directory or observer_analysis_state_root() / 'as1'
    c.store = SQLitePacketStore(state_root)
    c.control = ControlService(config, c.host, coder_claim_provider=coder_claim_provider)
    c.trace = TraceService(config, lambda project: runtime.snapshot(project), c.host, semantic_provider=c.control.project_context)
    c.information = InformationService(config, c.store, lambda project: runtime.snapshot(project), c.host,
        semantic_provider=c.control.project_context, impact_provider=c.trace, todo_adapter=runtime.todo_adapter)
    c.control.information_service = c.information
    root = configured_observer_skills_root(config)
    runtime_root = local_runtime_identity().root
    if observer_runtime_sha256 is None:
        verified_runtime = local_runtime_identity(root=runtime_root)
        receiver_manifest = (verified_runtime.root / 'receiver-manifest.json').read_bytes()
        if hashlib.sha256(receiver_manifest).hexdigest() != verified_runtime.manifest_sha256:
            raise ValueError('receiver manifest changed during observer digest resolution')
        manifest = json.loads(receiver_manifest)
        try:
            observer_runtime_sha256 = manifest['files']['local_worker/observer_runtime.py']
        except (KeyError, TypeError) as exc:
            raise ValueError('observer runtime receiver manifest entry missing') from exc
    c.backend = backend or SkillsObserverAnalysisProvider()
    roots = [config.workspaces[p].repositories[a].root for p in sorted(c.host.projects)
             for a in config.workspaces[p].repositories]
    # Freeze labels independently of the global model/service state. Installed
    # Skills roots are navigation resources, never implicit inquiry targets.
    inquiry_repositories = tuple((project, alias, str(repository.root))
        for project in sorted(c.host.projects)
        for alias, repository in sorted(config.workspaces[project].repositories.items()))
    installed_skill_roots = (str(root),)
    def inquiry_context(job):
        scope = job.scope
        project = scope.get('project')
        projects = (set(scope.get('catalog_projects', c.host.projects))
                    if project in {None, 'catalog'} else {project})
        if not projects <= c.host.projects:
            raise PermissionError('job_scope_not_permitted')
        return {'inquiry_repositories': [
            {'project': project, 'repository': alias, 'root': repository_root}
            for project, alias, repository_root in inquiry_repositories if project in projects],
            'installed_skill_roots': list(installed_skill_roots),
            'tool_argument_schemas': observer_tool_argument_schemas(scope['profile'])}
    c.worker_unavailable = None
    c.command = command_port
    factory = None
    analysis_runtime_identity = None
    trusted = None
    try:
        catalog = json.loads((root / 'integrations/native-skill-catalog.json').read_text())
        skills = {entry['name']: {'name': entry['name'], 'root': str(root / entry['name'])}
                  for entry in catalog['entries'] if entry.get('status') == 'accessible'}
        def execution_permitted(scope, shared):
            from types import SimpleNamespace
            return c.can_execute_inquiry(SimpleNamespace(scope=scope), shared)

        def information_tool(name, arguments, scope, *, automatic_read_scope=None):
            # Jobs carry a trusted admission scope; requests cannot override it.
            if not execution_permitted(scope, scope.get('_shared_inquiry', False)):
                raise PermissionError('job_scope_not_permitted')
            if automatic_read_scope is not None:
                # The automatic source seal only authorizes the special host
                # selected-file command adapter below. Existing information
                # tools can discover data outside that exact file set.
                return {'status': 'denied', 'reason': 'automatic_source_scope_denied',
                        'tool': name, 'accepted': False, 'dispatched': False}
            args = dict(arguments)
            project = args.pop('project', None)
            if project is not None and not isinstance(project, str):
                raise InvalidToolArguments('project must be a string or null')
            if scope['project'] != 'catalog' and project not in {None, scope['project']}:
                raise PermissionError('job_project_not_permitted')
            project = project or (None if scope['project'] == 'catalog' else scope['project'])
            if project is not None and project not in c.host.projects:
                raise PermissionError('job_project_not_permitted')
            if args.get('detail') == 'extended' and scope['profile'] != 'observer':
                raise PermissionError('detail_not_permitted')
            try:
                observer_tool_argument_models(scope['profile'])[name].model_validate({'project': project, **args})
                if project is None and name not in {'overview', 'search', 'machine'}:
                    raise InvalidToolArguments('project_required')
                if name in {'evidence', 'history'}:
                    model = EvidenceInput if name == 'evidence' else HistoryTraceInput
                    # The outer packet detail is distinct from the underlying
                    # native evidence/history detail vocabulary.
                    model.model_validate({'project': project, **{key: value for key, value in args.items() if key != 'detail'}})
            except ValidationError as error:
                raise InvalidToolArguments(str(error)) from error
            class AdmissionHost(ContextHost):
                def scope(self, project):
                    return {k: v for k, v in scope.items() if not k.startswith('_')}
            worker_host = AdmissionHost(scope['profile'], scope['principal'],
                                        frozenset(scope.get('catalog_projects', c.host.projects)))
            info = InformationService(config, c.store, runtime.snapshot, worker_host,
                semantic_provider=c.control.project_context, impact_provider=c.trace,
                todo_adapter=runtime.todo_adapter, job_lookup=lambda ident, access: c.jobs.lookup(ident, access_scope=access))
            try:
                return info.call(name, project=project, **args)
            except ValidationError as error:
                # Native request DTOs may validate deeper semantic parameters.
                # Output packet/result DTO failures remain real backend errors.
                if error.title not in _NATIVE_INPUT_ERROR_TITLES:
                    raise
                raise InvalidToolArguments(str(error)) from error
        class ScopedTrustedObserverFactory(TrustedObserverFactory):
            def __call__(self, service, job):
                scope = job.scope
                shared = service.is_inquiry(job.job_id)
                if not execution_permitted(scope, shared):
                    raise PermissionError('job_scope_not_permitted')
                automatic_read_scope = service.preparation_read_scope(
                    job.job_id, access_scope=scope, expected_attempt=job.attempt)
                selected = []
                selected_roots = {}
                if automatic_read_scope is not None:
                    if (not isinstance(automatic_read_scope, dict)
                            or set(automatic_read_scope) != {'version', 'files'}
                            or automatic_read_scope.get('version') != 1
                            or not isinstance(automatic_read_scope.get('files'), list)
                            or not 1 <= len(automatic_read_scope['files']) <= 64
                            or not isinstance(scope.get('project'), str)
                            or scope.get('project') not in config.workspaces):
                        raise PermissionError('automatic_source_scope_invalid')
                    workspace = config.workspaces[scope['project']]
                    deny_patterns = [*DEFAULT_DENY_PATTERNS, *workspace.deny_patterns]
                    seen = set()
                    for entry in automatic_read_scope['files']:
                        if not isinstance(entry, dict) or set(entry) != {'repository', 'path', 'sha256'}:
                            raise PermissionError('automatic_source_scope_invalid')
                        alias, relative, digest = (entry['repository'], entry['path'], entry['sha256'])
                        if (not isinstance(alias, str) or alias not in workspace.repositories
                                or not isinstance(relative, str) or relative_path(relative) != relative
                                or is_denied(Path(relative), deny_patterns)
                                or not isinstance(digest, str) or len(digest) != 64
                                or any(char not in '0123456789abcdef' for char in digest)
                                or (alias, relative) in seen):
                            raise PermissionError('automatic_source_scope_invalid')
                        seen.add((alias, relative))
                        repository = c.information.registry.repository(scope['project'], alias)
                        repository_root = Path(repository.root).resolve(strict=True)
                        selected.append((alias, relative, repository_root, digest))
                        selected_roots[alias] = repository_root

                def command_factory(active_service, active_job, read_scope):
                    if read_scope is None:
                        return None
                    if active_service is not service or active_job.job_id != job.job_id:
                        raise PermissionError('automatic_source_scope_job_mismatch')

                    def revalidate_scope():
                        return service.preparation_read_scope(
                            job.job_id, access_scope=scope,
                            expected_attempt=job.attempt) == automatic_read_scope

                    class Packetizer:
                        def _packet(_self, payload, guard=None):
                            if (payload.get('operation') == 'selected_file_read'
                                    and not revalidate_scope()):
                                raise RuntimeError('automatic_scope_unavailable')
                            if guard is not None and not guard():
                                raise RuntimeError('stale_attempt')
                            packet_id = service.observe(job.job_id, job.attempt, payload)
                            if not isinstance(packet_id, str) or not packet_id:
                                raise ValueError('packetizer_returned_no_identity')
                            return {**payload, 'packet_id': packet_id}

                    return _AutomaticSelectedReadCommand(Packetizer(), project=scope['project'],
                        selected=selected, validate_current=revalidate_scope)

                # Command roots follow the durable admission domain. Catalog and
                # skill jobs never acquire implicit project filesystem access.
                permitted = [root]
                if automatic_read_scope is not None:
                    permitted = list(dict.fromkeys(selected_roots.values()))
                elif job.mode != 'skill' and scope['project'] != 'catalog':
                    permitted = [r.root for r in config.workspaces[scope['project']].repositories.values()] + [root]
                bound = TrustedObserverFactory(runtime_root, self.digest, backend=self.backend,
                    roots=permitted,
                    tools=lambda name, args, original: self.tools(name, args,
                        {**original, '_shared_inquiry': shared},
                        automatic_read_scope=automatic_read_scope),
                    skills=self.skills, command_factory=command_factory)
                return bound(service, job)
        trusted = ScopedTrustedObserverFactory(runtime_root, observer_runtime_sha256,
            backend=c.backend, roots=[root], tools=information_tool, skills=skills)
        factory = SkillObserverFactory(trusted, skills_root=root)
        if c.command is None and canonical in {'investigator', 'skill_assembler'}:
            def command(argv, cwd=None, limits=None):
                module = trusted.load_runtime_module()
                def packetize(payload):
                    return c.store.create(tool='command', payload=payload, sources=[], access_scope=c.host.scope(None)).packet_id
                permitted = [root] if canonical == 'skill_assembler' else [*roots, root]
                runner = module.ReadOnlyCommandRunner(permitted, packetize=packetize)
                limits = limits or {}
                return runner.run(argv, cwd or str(permitted[0]), timeout_seconds=min(30, max(1, limits.get('timeout_seconds', 10))),
                                  max_output_bytes=min(65536, max(256, limits.get('max_output_bytes', 8192))))
            c.command = command
    except (OSError, ValueError, KeyError) as exc:
        c.worker_unavailable = type(exc).__name__
    # Derive outside the worker-availability catch: a missing or corrupt
    # receiver runtime must fail startup rather than reuse an unscoped cache.
    # An unqualified observer remains gracefully unavailable, with its own epoch.
    analysis_runtime_identity = _analysis_runtime_identity(runtime_root,
        trusted.digest if trusted is not None else
            observer_runtime_sha256,
        source_verification_state='source_verified' if trusted is not None else 'source_unavailable')
    c.jobs = JobService(state_root / 'jobs-v2', legacy_directory=state_root / 'jobs',
                        packets=c.store, worker_factory=factory, backend=c.backend, inquiry_access=c.inquiry_access, can_execute=c.can_execute_inquiry,
                        inquiry_context_provider=inquiry_context,
                        analysis_runtime_identity=analysis_runtime_identity)
    c.store.authority_access = c.packet_access
    c.information.job_lookup = lambda ident, scope: c.jobs.lookup(ident, access_scope=scope)
    c.skills = SkillService(c.jobs, skills_root=root)
    c.jobs.freshness_provider = InquiryFreshness(c)
    # Source attention is observed only by an explicitly started observer
    # service. The cold composition has no watcher and performs no source scan.
    # Operator grants and the persisted power window still gate every file read
    # and private preparation admission inside each tick.
    attention_repositories = {}
    if canonical == 'observer':
        for project in sorted(c.host.projects):
            workspace = config.workspaces[project]
            alias = workspace.authority_repository
            if alias is None and len(workspace.repositories) == 1:
                alias = next(iter(workspace.repositories))
            if alias in workspace.repositories:
                attention_repositories[project] = (alias,
                    c.information.registry.repository(project, alias).root)
    if attention_repositories and c.jobs.worker_factory is not None:
        c.attention_interval_seconds = 1.0
        c.attention_join_timeout = 10.0

        def attention_tick():
            # All state uses the existing broker database. The connection is
            # fresh per tick and no explicit transaction spans source reads or
            # broker calls.
            from .assistance.attention import AttentionController
            from .assistance.power import PowerPolicy, trusted_operator_control

            with c.jobs._db() as db:
                policy = PowerPolicy(db, clock=c.jobs.clock)
                state = policy.snapshot()
                if (not state['automatic_enabled'] or state['quiet_active']
                        or state['release_veto_active']):
                    return {'status': 'demand_only'}
                roots = {project: root for project, (_, root) in attention_repositories.items()}

                def resolve_determinants(project):
                    alias, _ = attention_repositories[project]
                    observed = c.trace._repository(project, alias, runtime.snapshot(project))
                    return {f'configuration:{alias}': next(item['digest']
                        for item in observed.get('inputs', [])
                        if item.get('kind') == 'configuration' and item.get('key') == alias)}

                controller = AttentionController(db, power_policy=policy,
                    trusted_roots=roots, access_scope=lambda project: c.host.scope(project),
                    broker=c.jobs, notebook=c.store, clock=c.jobs.clock,
                    repository_for=lambda project: attention_repositories[project][0],
                    resolve_determinants=resolve_determinants)
                control = trusted_operator_control()
                matching = [focus for focus in controller.active_focuses(permit_automatic=True)
                    if focus['focus_id'] == state['automatic_focus']
                    and focus['project'] == state['automatic_project']
                    and focus['project'] in attention_repositories]
                if not matching:
                    return {'status': 'no_active_automatic_focus'}
                focus_ids = {focus['focus_id'] for focus in matching}
                for candidate in controller.candidates():
                    if candidate.focus_id not in focus_ids:
                        continue
                    if candidate.status == 'admitting':
                        # The durable intent contains the exact idempotent broker
                        # request and immutable deadline. Reconcile it before a
                        # new admission, including after process restart.
                        controller.reconcile_admission(candidate.candidate_id)
                    elif candidate.status == 'dispatched':
                        controller.record_result(candidate_id=candidate.candidate_id, control=control)
                for focus in matching:
                    controller.scan(focus['focus_id'])
                return controller.dispatch_next(control)

        c.attention_tick = attention_tick
    return c


def register_surface(mcp, c):
    detail_type = Literal['compact', 'standard', 'extended'] if mcp.profile == MCPProfile.OBSERVER else Literal['compact', 'standard']
    def register(fn, description, *, mutation=False, analysis=False):
        if 'detail' in fn.__annotations__:
            fn.__annotations__['detail'] = detail_type
        mcp.add_tool(fn, description=description, annotations=WRITE if mutation else ANALYSIS_READ if analysis else READ, structured_output=True)

    def overview(project: str | None = None, detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('overview', project=project, detail=detail)
    def delta(project: str, since: str | dict[str, Any], detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('delta', project=project, since=since, detail=detail)
    def frontier(project: str, scope: dict[str, Any] | None = None, detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('frontier', project=project, scope=scope, detail=detail)
    def search(query: str | ExactEntityQuery, project: str | None = None, scope: dict[str, Any] | None = None, detail: str = 'compact') -> dict[str, Any]:
        params = dict(scope or {}) if isinstance(query, ExactEntityQuery) else {"scope": scope}
        return c.information.call('search', project=project, query=query.model_dump() if isinstance(query, ExactEntityQuery) else query, detail=detail, **params)
    def evidence(project: str, subject: str, kinds: list[str] | None = None, detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('evidence', project=project, subject=subject, kinds=kinds or [], detail=detail)
    def impact(project: str, targets: list[ImpactTarget], change: Literal['body', 'interface', 'configuration', 'generator', 'removal', 'unknown'] = 'unknown', direction: Literal['dependents'] = 'dependents', view: Literal['paths', 'snippets'] = 'paths', detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('impact', project=project,
                                  targets=[target.model_dump(exclude_none=True) for target in targets],
                                  change_class=change, mode=view, detail=detail)
    def history(project: str, subject: str, from_revision: int | None = None, to_revision: int | None = None, detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('history', project=project, subject=subject, from_revision=from_revision, to_revision=to_revision, detail=detail)
    def machine(query_or_view: str = 'host_memory', detail: str = 'compact') -> dict[str, Any]:
        return c.information.call('machine', query_or_view=query_or_view, detail=detail)
    async def read(project: str, paths: list[str | dict[str, Any]], repository: str | None = None, revision: str | None = None, detail: str = 'compact') -> dict[str, Any]:
        return await asyncio.to_thread(c.information.call, 'read', project=project,
            paths=paths, repository=repository, revision=revision, detail=detail)
    async def investigate(question: str, project: str | None = None, hints: list[str] | None = None, request_id: str | None = None, detail: str = 'compact') -> dict[str, Any]:
        def invoke():
            scope = c.scope(project)
            value = c.jobs.inquire(question=question, access_scope=scope, hints=hints or (), request_id=request_id)
            if value.get('job') and value['status'] in {'completed', 'partial'}:
                c.jobs.reconcile()
                ref = value['job'].get('result_packet')
                result = c.store.lookup(ref, access_scope=scope) if ref else None
                if not result or result.status != 'ok':
                    return {'status': 'unavailable', 'reason': 'answer_unavailable'}
                return public_inquiry({**result.packet.payload, 'status': value['status'],
                    'packet_id': result.packet.packet_id, 'alias': result.packet.alias,
                    'evidence_packets': value['job'].get('evidence_packets', []),
                    **({'unresolved_questions': value['job']['unresolved_questions']}
                       if 'unresolved_questions' in value['job'] else {}),
                    'sources': [source.model_dump(exclude_none=True) for source in result.packet.sources]})
            return public_inquiry(value)
        # Inquiry polling, reconciliation and packet lookup use blocking SQLite
        # and sleeps. Keep them off the ASGI loop so concurrent HTTP routes run.
        return await asyncio.to_thread(invoke)

    async def skill(query: str | None = None, skill: str | None = None, project: str | None = None, hints: list[str] | None = None, request_id: str | None = None) -> dict[str, Any]:
        def invoke():
            value = c.skills.inquire(access_scope=c.scope(project), query=query, skill=skill,
                hints=hints or (), request_id=request_id, detail=SKILL_ASSEMBLY_DETAIL)
            if value.get('status') not in {'ok', 'thinking', 'busy', 'unavailable', 'completed', 'partial'}:
                return {'status': 'unavailable', 'reason': 'answer_unavailable'}
            return public_inquiry(value)
        return await asyncio.to_thread(invoke)
    def command(argv: list[str], cwd: str | None = None, limits: dict[str, Any] | None = None) -> dict[str, Any]:
        if c.command is None:
            return {'status': 'unavailable', 'reason': 'qualified_command_port_unavailable'}
        return c.command(argv, cwd=cwd, limits=limits)
    def log(query: str = '', job_id: str | None = None, path_or_entity: str | None = None, limit: int = 50, project: str | None = None) -> dict[str, Any]:
        scope = c.scope(project)
        if job_id:
            return c.jobs.lookup(job_id, access_scope=scope)
        return {'records': c.jobs.log(access_scope=scope, query=query or path_or_entity or '', limit=min(50, max(1, limit)))}
    def plan(project: str, action: Literal['validate', 'diff', 'apply', 'amend', 'supersede', 'retire'], native_plan: dict[str, Any] | None = None, proposal: dict[str, Any] | None = None, replan: dict[str, Any] | None = None, intent: dict[str, Any] | None = None, prepared_request: dict[str, Any] | None = None, authorization_id: str | None = None) -> dict[str, Any]:
        return c.control.plan(project, action, native_plan=native_plan, proposal=proposal, replan=replan, intent=intent, prepared_request=prepared_request, authorization_id=authorization_id)
    def amend_project(request: ProjectAmendment) -> dict[str, Any]:
        return c.control.amend_project(request)
    def maintain_execution(request: MaintenanceRequest) -> dict[str, Any]:
        return c.control.maintain_execution(request)
    descriptions = {
        'overview': 'Read registered projects and repositories, or give orientation for the named project. Omit project to list the catalog; nothing is loaded automatically.',
        'delta': 'Summarize project changes since a prior commit or a DeltaSince cursor with a supported revision, task, checkpoint, interface, or time anchor. Returns scoped immutable packets with source locators.',
        'frontier': 'Show current open work and relevant project context; use scope to narrow the view. Returns scoped immutable packets and source locators.',
        'search': 'Discover context with a text query, or resolve an exact typed {kind,target}. Exact lookups never fall back to fuzzy search; scope can narrow discovery.',
        'evidence': 'Retrieve supported evidence for a task, source, run, or other subject in a project. Use kinds to filter evidence; results include source locators.',
        'impact': 'Trace declared dependents of target entities for a change. Choose paths for affected locations or snippets for bounded source excerpts; results are read-only context.',
        'history': 'Trace supported history for a project subject, optionally bounded by from_revision and to_revision. Returns scoped history with source locators.',
        'machine': 'Read host facts or diagnostics selected by query_or_view, such as memory or runtime status. This reports machine context, not repository changes.',
        'read': 'Read exact project repository paths or requested ranges, optionally from a named repository and revision. Returns content with immutable source identities; observer profile only.',
        'investigate': 'Ask a read-only question about project context using optional evidence packet hints. A cached answer for the same question and context is reused only while its sources remain current; while thinking, continue useful work and repeat the identical question later; avoid submitting variants. If busy, use search, read or evidence to contextualize or refine a later question. Completed findings retain evidence and source references; use read or evidence to inspect authoritative source.',
        'skill': 'Read-only discovery and use of an installed native skill for a question, using optional project context and evidence hints. Always returns extended authoritative excerpts (up to 49,152 excerpt bytes across selected resources); this is a ceiling, not a target. The agent synthesis should stay concise and include useful context without repeating the excerpts. A cached answer for the same question and context is reused only while its selected sources remain current; while thinking, continue useful work and repeat the identical question later; avoid submitting variants. If busy, use search, read or evidence to contextualize or refine a later question. Results identify the selected authoritative skill source.',
        'command': 'Run a bounded read-only command within the configured repository roots using argv, optional cwd, and limits. Host policy clamps execution limits; output is recorded as evidence. No delegation or mutation.',
        'log': 'Retrieve up to five question-and-answer briefs from the global latest-50 answered-inquiry cache, using query/path_or_entity for lexical matches. Optional project narrows results; job_id remains a compatibility exact-record read.',
        'plan': 'Validate or compare a native Todo plan, or apply, amend, supersede, or retire project work through the scoped transaction authority. Supply the action and its matching plan or proposal; authorized mutations require valid prepared authority.',
        'amend_project': 'Submit a typed semantic project amendment, such as a supported registration or evidence update. The request is checked against current project authority before it is previewed or committed.',
        'maintain_execution': 'Resume one clean stopped execution under a host-issued maintenance mandate. Supply a typed request containing the authorized action and mandate; the mandate limits which execution can change.',
    }
    for fn in (overview, delta, frontier, search, evidence, impact, history, machine, read, investigate, skill, command, log, plan, amend_project, maintain_execution):
        register(fn, descriptions[fn.__name__], mutation=fn.__name__ in {'plan', 'amend_project', 'maintain_execution'}, analysis=fn.__name__ in {'investigate', 'skill'})
