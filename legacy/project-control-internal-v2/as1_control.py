"""Typed control ports. Startup host policy, never request prose, grants authority."""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any, Literal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from . import admin, mutation
from .as1_context import ContextHost, InformationService
from .config import ProjectControlConfig
from .models import ProposalEnvelope
from .registry import WorkspaceRegistry
from .security import is_denied


class ProjectAmendment(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    format: Literal['pc-project-amendment/1']
    project: str
    action: Literal['register_identity', 'register_relation', 'register_generation',
                    'configure_provider', 'remove_registration', 'record_skill_use', 'update_orientation']
    intent: str = Field(min_length=1)
    expected_revision: int = Field(ge=0, strict=True)
    operation_id: str = Field(min_length=8)
    payload: dict[str, Any]
    mode: Literal['preview', 'apply'] = 'apply'


class MaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    project: str
    action: Literal['diagnose', 'prepare', 'execute']
    task_id: str | None = None
    run_id: str | None = None
    authorization_id: str | None = None


class ControlService:
    """In-process facade; adapters must construct this with authenticated host policy.

    No SQL/projection writes, caller paths, role overrides, commands or executable
    registration are accepted. InformationService provides independent orientation.
    Coder publication uses a host-bound canonical claim callback, not wire tokens.
    """
    def __init__(self, config: ProjectControlConfig, host: ContextHost, *,
                 information_service: InformationService | None = None,
                 coder_claim_provider: Callable[[str, str], str] | None = None):
        self.config, self.host = config, host
        self.registry = WorkspaceRegistry(config)
        self.information_service = information_service
        self.coder_claim_provider = coder_claim_provider
        if information_service is not None and information_service.host != host:
            raise ValueError('information_host_mismatch')

    def _access(self, project: str, *, mutator: bool = True):
        if project not in self.host.projects:
            raise PermissionError('project_not_permitted')
        self.registry.workspace(project)
        if mutator and self.host.profile != 'mutator':
            raise PermissionError('mutator_required')

    def _service(self, project: str, *, read_only: bool,
                 source_verifier: Callable[[Mapping], Mapping] | None = None):
        environment = mutation._runtime_environment(self.config)
        binding, service = mutation._todo_service(self.config, project, environment, read_only=read_only,
                                                  project_source_verifier=source_verifier or self.verify_source)
        binding.validate()
        return binding, service

    def information(self, tool: str, *, project: str, **params):
        self._access(project)
        if self.information_service is None:
            return {'status': 'unavailable', 'reason': 'information_port_unavailable'}
        return self.information_service.call(tool, project=project, **params)

    def project_context(self, project: str):
        self._access(project, mutator=False)
        binding, service = self._service(project, read_only=True)
        port = getattr(service, 'project_context', None)
        if not callable(port):
            return {'status': 'partial', 'reason': 'project_semantic_extension_unavailable', 'required_migration_version': 12}
        try:
            result = port()
        except Exception as exc:
            if getattr(exc, 'code', None) == 'schema_migration_required':
                return {'status': 'partial', 'reason': exc.code, 'required_migration_version': 12}
            raise mutation._todo_error(exc) from exc
        # Resolve field locators through the host registry when the kernel has
        # no registered-ID read binding. This changes only the returned view.
        for row in result.get('orientation', []):
            payload = row['payload']
            freshness = {}
            for field in payload['fields']:
                locators = payload.get('field_anchors', {}).get(field, payload.get('anchors', []))
                sources = []
                for locator in locators:
                    try:
                        identity = self.verify_source(locator)
                        sources.append({'source': locator, 'status': 'fresh', 'identity': identity})
                    except Exception as exc:
                        state = 'stale' if getattr(exc, 'code', None) == 'source_prerequisite_stale' else 'unavailable'
                        sources.append({'source': locator, 'status': state})
                status = 'stale' if any(x['status'] == 'stale' for x in sources) else 'unavailable' if not sources or any(x['status'] == 'unavailable' for x in sources) else 'fresh'
                freshness[field] = {'status': status, 'sources': sources}
            row['field_freshness'] = freshness
        binding.validate()
        return result

    def plan(self, project: str, action: str, *, native_plan: Mapping | None = None,
             proposal: Mapping | ProposalEnvelope | None = None, replan: Mapping | None = None,
             intent: Mapping | None = None, prepared_request: Mapping | None = None,
             authorization_id: str | None = None):
        self._access(project)
        supplied = {k for k, v in {'native_plan': native_plan, 'proposal': proposal,
                    'replan': replan, 'intent': intent, 'prepared_request': prepared_request,
                    'authorization_id': authorization_id}.items() if v is not None}
        allowed = {'context': set(), 'validate': {'native_plan'}, 'diff': {'native_plan'},
                   'apply': {'native_plan', 'proposal'}, 'amend': {'replan'},
                   'supersede': {'intent', 'authorization_id'}, 'retire': {'intent', 'prepared_request'}}
        if action not in allowed or not supplied <= allowed[action] or (action != 'context' and len(supplied) != 1):
            raise ValueError('invalid_plan_action_payload')
        if action == 'context':
            snapshot = mutation.build_mutation_snapshot(self.config, project)
            return {'status': 'ok', 'project_uuid': snapshot.project_uuid, 'revision': snapshot.todo_revision,
                    'observation_preconditions': snapshot.observation_preconditions().model_dump(mode='json'),
                    'semantic_context': self.project_context(project)}
        if action in {'validate', 'diff'}:
            return mutation.validate_native_plan(self.config, project, native_plan)
        if action == 'apply':
            if proposal is None:
                snapshot = mutation.build_mutation_snapshot(self.config, project)
                proposal = ProposalEnvelope.create(intent='Apply native plan', proposed_change=dict(native_plan),
                                                   observation_preconditions=snapshot.observation_preconditions())
            return mutation.apply_proposal(self.config, project, proposal)
        if action == 'amend':
            return mutation.apply_selective_replan(self.config, project, replan)
        root = mutation._authority_root(self.config, project)
        if action == 'supersede':
            if authorization_id is not None:
                return self.maintain_execution({'project': project, 'action': 'execute', 'authorization_id': authorization_id})
            with mutation._temporary_plan(intent) as path:
                return admin.prepare_supersession_assignment(root, path, recipient_principal=self.host.principal)
        if intent is not None:
            with mutation._temporary_plan(intent) as path:
                return admin.prepare_retire_run_batch(root, path)
        # The kernel validates the complete reviewed affected set under its transaction.
        from .workflow_core.retirement import RetirementRequest, retire_run_batch
        parsed = RetirementRequest.model_validate(prepared_request)
        _, service = self._service(project, read_only=False)
        return retire_run_batch(service, parsed)

    def amend_project(self, request: Mapping | ProjectAmendment):
        parsed = ProjectAmendment.model_validate(request).model_dump(mode='json')
        self._access(parsed['project'])
        # Validate registered repositories and typed foreign entities before any
        # writable Service construction. Declarations cannot extend root trust.
        binding, readonly = self._service(parsed['project'], read_only=True)
        if not callable(getattr(readonly, 'amend_project', None)):
            return {'status': 'unavailable', 'reason': 'project_semantic_extension_unavailable', 'required_migration_version': 12}
        from todo_orchestrator.project_amendments import validate_request
        try:
            parsed = validate_request(parsed, readonly.project, {'project_id': parsed['project']})
        except Exception as exc:
            raise mutation._todo_error(exc) from exc
        self._declaration_access(parsed['project'], parsed['action'], parsed['payload'])
        # Schema inspection is read-only; do not opportunistically migrate live authorities.
        context = self.project_context(parsed['project'])
        if context.get('status') == 'partial':
            return context
        binding, service = self._service(parsed['project'], read_only=False)
        try:
            result = service.amend_project(parsed, principal=self.host.principal, role='mutator',
                                           context={'project_id': parsed['project']})
        except Exception as exc:
            raise mutation._todo_error(exc) from exc
        binding.validate()
        return result

    def _declaration_access(self, project, action, payload):
        if action == 'register_identity':
            self.registry.repository(project, payload.get('repository'))
        if action == 'register_relation':
            for key in ('source', 'target'):
                ref = payload.get(key, {})
                matches = []
                for candidate in sorted(self.host.projects):
                    if candidate not in self.config.workspaces:
                        continue
                    _, service = self._service(candidate, read_only=True)
                    if service.project['project_uuid'] == ref.get('project_uuid'):
                        matches.append(candidate)
                if len(matches) != 1:
                    raise mutation.MutationRejected('project_identity_unavailable', 'Entity project UUID must resolve exactly once in host registrations')
                self.registry.repository(matches[0], ref.get('repository'))
        locators = list(payload.get('anchors', [])) + list(payload.get('inputs', []))
        for values in payload.get('field_anchors', {}).values():
            locators.extend(values)
        for locator in locators:
            self._source_access(locator)

    def _source_access(self, locator):
        project = locator.get('project')
        native_service = None
        if project not in self.config.workspaces:
            matches = []
            for candidate in sorted(self.host.projects):
                if candidate not in self.config.workspaces:
                    continue
                _, service = self._service(candidate, read_only=True)
                if service.project['project_uuid'] == project:
                    matches.append((candidate, service))
            if len(matches) != 1:
                raise mutation.MutationRejected('project_identity_unavailable', 'Source project must be an exact configured ID or canonical UUID')
            project, native_service = matches[0]
        self._access(project, mutator=False)
        alias = locator.get('repository')
        if native_service is not None:
            # Only canonical UUID locators may use exact native local aliases.
            # Arbitrary registered PC aliases retain their own repository roots.
            from todo_orchestrator.project_amendments import local_repositories
            authority_root = mutation._authority_root(self.config, project)
            if (alias not in self.registry.workspace(project).repositories
                    and alias in local_repositories(native_service.project, authority_root)):
                alias = self.registry.workspace(project).authority_repository
        repository = self.registry.repository(project, alias)
        from .as1_contracts import relative_path
        relative = relative_path(locator.get('path'))
        if Path(relative).name in {'auth.json', 'runtime-state.json'} or is_denied(Path(relative), self.registry.workspace(project).deny_patterns):
            raise PermissionError('source_not_permitted')
        path = (repository.root / relative).resolve()
        if not path.is_relative_to(repository.root):
            raise mutation.MutationRejected('source_prerequisite_unavailable', 'Source is outside registered repository')
        return project, repository, relative, path

    def verify_source(self, locator: Mapping):
        """Trusted broker for exact configured IDs, canonical UUID/revision and bytes.

        This callback is installed only through trusted service construction.
        Source bytes use the shared descriptor reader, never symlink traversal.
        """
        project, repository, relative, path = self._source_access(locator)
        if not path.is_file():
            raise mutation.MutationRejected('source_prerequisite_unavailable', 'Direct source is unavailable')
        binding, service = self._service(project, read_only=True)
        revision = service.db.revision()
        uuid = service.project['project_uuid']
        if locator.get('project_uuid', uuid) != uuid or locator.get('revision', revision) != revision:
            raise mutation.MutationRejected('source_prerequisite_stale', 'Canonical source authority changed')
        try:
            raw = InformationService._working_bytes(repository.root, relative)
        except (OSError, ValueError) as exc:
            raise mutation.MutationRejected('source_prerequisite_unavailable',
                                            'Direct source is unavailable, symlinked or changed during read') from exc
        digest = hashlib.sha256(raw).hexdigest()
        if digest != locator.get('content_sha256'):
            raise mutation.MutationRejected('source_prerequisite_stale', 'Direct source bytes changed')
        if service.db.revision() != revision:
            raise mutation.MutationRejected('source_prerequisite_stale', 'Source authority changed during verification')
        binding.validate()
        return {'project': project, 'project_uuid': uuid, 'revision': revision,
                'repository': locator['repository'], 'path': relative, 'content_sha256': digest}

    def publish_context(self, project: str, request: Mapping):
        self._access(project, mutator=False)
        if self.host.profile not in {'coder', 'codex'} or self.coder_claim_provider is None:
            raise PermissionError('authenticated_coder_claim_required')
        if set(request) - {'kind', 'task_id', 'payload'}:
            raise ValueError('invalid_publication_fields')
        token = self.coder_claim_provider(self.host.principal, project)
        binding, readonly = self._service(project, read_only=True)
        if not callable(getattr(readonly, 'publish_project_context', None)):
            return {'status': 'unavailable', 'reason': 'project_semantic_extension_unavailable'}
        context = self.project_context(project)
        if context.get('status') == 'partial':
            return context
        # A configured repository is readable, but registration alone does not
        # grant the coder claim ownership there. Authenticate against this
        # authority and resolve every anchor before opening a writable service.
        from todo_orchestrator.project_amendments import anchors
        from todo_orchestrator.sessions import authenticate_claim
        from todo_orchestrator.ownership import scopes_for
        authority_root = mutation._authority_root(self.config, project)
        def publication_access(locator):
            with readonly.db.read() as conn:
                claim = authenticate_claim(conn, token)
                scopes = scopes_for(conn, claim['task_id'])
            source_project, repository, relative, path = self._source_access(locator)
            resolved_relative = path.relative_to(repository.root).as_posix()
            if (source_project != project or repository.root != authority_root
                    or not all(any(scope == '.' or candidate == scope.rstrip('/')
                                   or candidate.startswith(scope.rstrip('/') + '/')
                                   for scope in scopes)
                               for candidate in (relative, resolved_relative))):
                raise mutation.MutationRejected('publication_scope_denied',
                                                'Publication anchor exceeds authenticated claim repository or task scopes')

        def verify_publication(locator):
            publication_access(locator)
            return self.verify_source(locator)

        try:
            for locator in anchors(request.get('payload', {})):
                publication_access(locator)
        except Exception as exc:
            raise mutation._todo_error(exc) from exc
        binding.validate()
        binding, service = self._service(project, read_only=False, source_verifier=verify_publication)
        try:
            result = service.publish_project_context(dict(request), claim_token=token)
        except Exception as exc:
            raise mutation._todo_error(exc) from exc
        binding.validate()
        return result

    def publish_workflow_context(self, project: str, request: Mapping, *, workflow_handle: str, protocol):
        """Capability publication keeps credentials inside the canonical kernel.

        The startup adapter supplies the verified protocol, never a model port or
        raw token. The native port reauthenticates the live dispatch and owned
        task inside its transaction before using this source verifier.
        """
        self._access(project, mutator=False)
        if self.host.profile not in {'coder', 'codex'}:
            raise PermissionError('authenticated_coder_claim_required')
        if set(request) - {'kind', 'task_id', 'payload'}:
            raise ValueError('invalid_publication_fields')
        publisher = getattr(protocol, 'publish_project_context', None)
        if not callable(publisher):
            return {'status': 'unavailable', 'reason': 'capability_publication_port_unavailable'}
        authority = mutation._authority_root(self.config, project)
        return publisher(workflow_handle, kind=request['kind'], payload=request['payload'],
            task_id=request.get('task_id'), source_verifier=self.verify_source,
            expected_repository_root=authority)

    def maintain_execution(self, request: Mapping | MaintenanceRequest):
        parsed = MaintenanceRequest.model_validate(request)
        self._access(parsed.project)
        root = mutation._authority_root(self.config, parsed.project)
        if parsed.action == 'execute':
            if not parsed.authorization_id or parsed.task_id is not None or parsed.run_id is not None:
                raise ValueError('maintenance_execute_requires_exact_grant')
            return admin.maintain_execution(root, authorization_id=parsed.authorization_id,
                                            recipient_principal=self.host.principal)
        if not parsed.task_id or parsed.authorization_id is not None:
            raise ValueError('maintenance_target_required')
        if parsed.action == 'diagnose':
            return admin.inspect_recovery(root, parsed.task_id)
        return admin.prepare_maintenance_assignment(root, task_id=parsed.task_id, run_id=parsed.run_id,
                                                     recipient_principal=self.host.principal)
