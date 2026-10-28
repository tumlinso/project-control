"""Project Control's host-bound front door to standalone Todo APIs.

The Todo package is selected by the verified workflow binding. This module
does not import Todo implementation modules or inspect its SQLite authority.
Caller-supplied values describe native operations; project roots, principals,
profiles, providers, and bearer tokens come from trusted host composition.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import base64
import json
from pathlib import Path
from typing import Any

from .config import ProjectControlConfig
from .registry import WorkspaceRegistry
from .native_child_identity import NativeChildCredentialIssuer


class NativeWorkflowError(RuntimeError):
    """A bounded native Todo request cannot be served by this host."""

    def __init__(self, code: str, message: str = "native workflow request rejected") -> None:
        super().__init__(message)
        self.code = code


SessionFactory = Callable[..., Any]
ProviderFactory = Callable[..., object | None]


_MAX_PLAN_BYTES = 256 * 1024
_MAX_COLLECTION = 4096
_AUTHORITY_PAGE_BYTES = 160 * 1024
_HOST_SESSION_OPERATIONS = frozenset({
    "todo_integrate_attempt",
    "todo_accept_outcome",
    "todo_reject_outcome",
    "todo_report_outcome",
    "todo_finish_attempt",
    "todo_handback_attempt",
    "todo_release_attempt",
    "todo_inspect_attempt",
    "todo_inspect_resource",
    "todo_reconcile_resource",
    "todo_retry_resource",
    "todo_release_resource",
})
_RESOURCE_OPERATION_ID_ONLY = frozenset({
    "todo_inspect_resource",
    "todo_reconcile_resource",
    "todo_retry_resource",
    "todo_release_resource",
})
# These operations either have no external write effect (read-only source or
# physical observations are allowed) or use only a recoverable credential
# prewrite before the authority transaction. The native
# dispatcher can raise StaleAuthorityRevisionError both before a handler and
# after a handler's semantic compare-and-swap; for this set, the latter is in
# the same SQLite transaction and rolls back, so callers may safely refresh a
# snapshot while retaining the same idempotency intent. Keep provider-effect
# operations (workspace creation, integration, handback, resource and
# verification operations) out of this set.
_STALE_REVISION_SAFE_OPERATIONS = frozenset({
    "todo_commit_plan",
    "todo_open_run",
    "todo_dispatch_attempt",
    "todo_report_outcome",
    "todo_accept_outcome",
    "todo_reject_outcome",
    "todo_heartbeat_attempt",
    "todo_resume_attempt",
    "todo_release_attempt",
    "todo_close_run",
    "todo_retire_run",
    "todo_resolve_decision",
    "todo_publish_interface",
    "todo_accept_checkpoint",
    "todo_release_barrier",
    "todo_arrive_rendezvous",
    "todo_open_blocker",
    "todo_clear_blocker",
})


def _required_text(value: object, field: str, *, max_bytes: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{field}_invalid")
    return value


def _optional_revision(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("expected_revision_invalid")
    return value


def _optional_repository(value: object) -> str | None:
    if value is None:
        return None
    return _required_text(value, "repository", max_bytes=128)


def _authority_cursor(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    token = _required_text(value, "page_token", max_bytes=2048)
    try:
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        result = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("page_token_invalid") from error
    if (not isinstance(result, dict)
            or set(result) != {"project_id", "authority_id", "semantic_revision", "section_index", "row_offset"}
            or type(result.get("semantic_revision")) is not int
            or type(result.get("section_index")) is not int or result["section_index"] < 0
            or type(result.get("row_offset")) is not int or result["row_offset"] < 0):
        raise ValueError("page_token_invalid")
    return result


def _authority_page(session: object, *, expected_revision: int | None,
                    limit: int, page_token: object) -> dict[str, object]:
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit_invalid")
    cursor = _authority_cursor(page_token)
    model = session.read_model(expected_revision=expected_revision)
    value = model.to_dict()
    sections = value.pop("sections", {})
    if not isinstance(sections, dict):
        raise NativeWorkflowError("native_authority_sections_invalid")
    names = sorted(sections)
    project_id, authority_id = value.get("project_id"), value.get("authority_id")
    revision = value.get("semantic_revision")
    if cursor is not None and (cursor["project_id"], cursor["authority_id"], cursor["semantic_revision"]) != (
        project_id, authority_id, revision
    ):
        raise NativeWorkflowError("todo_read_cursor_stale")
    section_index = 0 if cursor is None else cursor["section_index"]
    row_offset = 0 if cursor is None else cursor["row_offset"]
    if section_index > len(names):
        raise ValueError("page_token_invalid")
    page_sections: dict[str, list[object]] = {name: [] for name in names}
    counts: dict[str, int] = {}
    encoded_size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    taken = 0
    next_cursor: dict[str, object] | None = None
    for section_i in range(section_index, len(names)):
        name = names[section_i]
        rows = sections[name]
        if not isinstance(rows, list):
            raise NativeWorkflowError("native_authority_sections_invalid")
        counts[name] = len(rows)
        offset = row_offset if section_i == section_index else 0
        if offset > len(rows):
            raise ValueError("page_token_invalid")
        for row_i in range(offset, len(rows)):
            row = rows[row_i]
            row_bytes = len(json.dumps(row, ensure_ascii=False, allow_nan=False,
                                       separators=(",", ":")).encode("utf-8"))
            if row_bytes > _AUTHORITY_PAGE_BYTES:
                raise NativeWorkflowError("todo_authority_row_too_large")
            if taken and (taken >= limit or encoded_size + row_bytes > _AUTHORITY_PAGE_BYTES):
                next_cursor = {"section_index": section_i, "row_offset": row_i}
                break
            if not taken and encoded_size + row_bytes > _AUTHORITY_PAGE_BYTES:
                raise NativeWorkflowError("todo_authority_row_too_large")
            page_sections[name].append(row)
            encoded_size += row_bytes
            taken += 1
            if taken >= limit:
                if row_i + 1 < len(rows):
                    next_cursor = {"section_index": section_i, "row_offset": row_i + 1}
                else:
                    following = next((index for index in range(section_i + 1, len(names))
                                      if sections[names[index]]), None)
                    if following is not None:
                        next_cursor = {"section_index": following, "row_offset": 0}
                break
        if next_cursor is not None:
            break
    complete = next_cursor is None
    if next_cursor is None:
        next_page_token = None
    else:
        token_payload = {
            "project_id": project_id,
            "authority_id": authority_id,
            "semantic_revision": revision,
            **next_cursor,
        }
        next_page_token = base64.urlsafe_b64encode(
            json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
    omitted = [
        {"section": name, "omitted_count": counts.get(name, len(sections[name])) - len(page_sections[name])}
        for name in names if counts.get(name, len(sections[name])) > len(page_sections[name])
    ]
    return {
        **value,
        "sections": page_sections,
        "section_counts": {name: len(sections[name]) for name in names},
        "page": {"limit": limit, "included_rows": taken, "complete": complete,
                 "next_page_token": next_page_token, "omissions": omitted},
    }


def _json_mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field}_invalid")
    return dict(value)


def _public(session: object, kind: str, value: object) -> dict[str, object]:
    """Use a public DTO codec; never walk internal authority objects here."""
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, dict):
            return result
    serialize = getattr(session, "serialize", None)
    if not callable(serialize):
        raise NativeWorkflowError("native_serializer_unavailable")
    try:
        encoded = serialize(kind, value)
        result = json.loads(encoded)
    except Exception as error:
        raise NativeWorkflowError("native_result_serialization_failed") from error
    if not isinstance(result, dict):
        raise NativeWorkflowError("native_result_serialization_failed")
    return result


class NativeWorkflowHost:
    """A profile-scoped facade that resolves only registered authority roots.

    `session_for_host` and `session_for_attempt` are trusted runtime-binding
    functions. Their parameters contain host-derived identity, never values
    from MCP arguments. A provider factory can supply Project Control's
    operation observer and Git effect provider without granting Todo access to
    the host's physical resource inventory.
    """

    def __init__(
        self,
        config: ProjectControlConfig,
        host: object,
        *,
        session_for_host: SessionFactory | None = None,
        session_for_attempt: SessionFactory | None = None,
        store_attempt_bearer: Callable[..., None] | None = None,
        store_child_bearer: Callable[..., None] | None = None,
        forget_attempt_bearer: Callable[..., None] | None = None,
        providers_for_project: ProviderFactory | None = None,
        child_credential_for: Callable[..., object] | None = None,
        child_identity_issuer: NativeChildCredentialIssuer | None = None,
        binding_validator: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self.host = host
        self.registry = WorkspaceRegistry(config)
        self._session_for_host = session_for_host or self._default_host_session
        self._session_for_attempt = session_for_attempt or self._default_attempt_session
        self._store_attempt_bearer = store_attempt_bearer or self._default_store_attempt_bearer
        self._store_child_bearer = store_child_bearer or self._default_store_child_bearer
        self._forget_attempt_bearer = forget_attempt_bearer or self._default_forget_attempt_bearer
        self._providers_for_project = providers_for_project
        self._child_identity_issuer = child_identity_issuer or NativeChildCredentialIssuer()
        self._child_credential_for = child_credential_for or self._child_identity_issuer.issue
        self._binding_validator = binding_validator or self._default_binding_validator

    @staticmethod
    def _default_binding_validator() -> None:
        from .workflow_binding import initialize_workflow_binding

        initialize_workflow_binding().validate()

    def _providers(self, project: str, root: Path, repository: str):
        if self._providers_for_project is None:
            from todo_orchestrator.api import ProviderComposition, read_project_metadata

            metadata = read_project_metadata(root)
            return ProviderComposition(
                principal_authenticator=self._child_identity_issuer.for_project(str(metadata.project_id)),
            )
        return self._providers_for_project(
            self.config, project, root, principal=self.host.principal,
            profile=self.host.profile, repository=repository,
            principal_authenticator=self._child_identity_issuer,
        )

    def _default_host_session(self, root: Path, *, principal: str, profile: str, providers=None):
        from .workflow_binding import open_session_for_host

        return open_session_for_host(root, principal=principal, profile=profile, providers=providers)

    def _default_attempt_session(self, root: Path, *, principal: str, profile: str,
                                 attempt_id: str, providers=None):
        from .workflow_binding import open_attempt_session

        return open_attempt_session(root, principal=principal, profile=profile,
                                    attempt_id=attempt_id, providers=providers)

    def _default_store_attempt_bearer(self, root: Path, *, principal: str, profile: str,
                                      attempt_id: str, bearer_token: str) -> None:
        from .workflow_binding import store_attempt_bearer

        store_attempt_bearer(root, principal=principal, profile=profile,
                              attempt_id=attempt_id, bearer_token=bearer_token)

    def _default_forget_attempt_bearer(self, root: Path, *, principal: str,
                                       profile: str, attempt_id: str) -> None:
        from .workflow_binding import forget_attempt_bearer

        forget_attempt_bearer(root, principal=principal, profile=profile, attempt_id=attempt_id)

    def _default_store_child_bearer(self, root: Path, *, principal: str, parent_attempt_id: str,
                                    execution_id: str, child_principal: str, profile: str,
                                    bearer_token: str) -> None:
        from .workflow_binding import store_child_bearer

        store_child_bearer(root, principal=principal, profile=profile,
                           parent_attempt_id=parent_attempt_id, execution_id=execution_id,
                           child_principal=child_principal, bearer_token=bearer_token)

    def _project_root(self, project: str, repository: str | None = None) -> tuple[Path, str]:
        if not isinstance(project, str) or project not in getattr(self.host, "projects", frozenset()):
            raise NativeWorkflowError("project_not_permitted")
        try:
            workspace = self.registry.workspace(project)
            alias = repository if repository is not None else workspace.authority_repository
            if not alias:
                raise NativeWorkflowError("todo_authority_repository_unconfigured")
            root = self.registry.repository(project, alias).root.resolve(strict=True)
        except NativeWorkflowError:
            raise
        except Exception as error:
            raise NativeWorkflowError("project_or_authority_unavailable") from error
        return root, alias

    def _session(self, project: str, *, attempt_id: str | None = None,
                 repository: str | None = None):
        root, selected_repository = self._project_root(project, repository)
        self._binding_validator()
        providers = self._providers(project, root, selected_repository)
        if attempt_id is None:
            session = self._session_for_host(
                root, principal=self.host.principal, profile=self.host.profile,
                providers=providers,
            )
        else:
            session = self._session_for_attempt(
                root, principal=self.host.principal, profile=self.host.profile,
                attempt_id=attempt_id, providers=providers,
            )
        return root, session

    @staticmethod
    def _close(session: object) -> None:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    def _guarded(self, project: str, operation: Callable[[Path, Any], dict[str, object]],
                 *, attempt_id: str | None = None,
                 repository: str | None = None,
                 stale_revision_safe: bool = False) -> dict[str, object]:
        root, session = self._session(project, attempt_id=attempt_id,
                                      repository=repository)
        try:
            result = operation(root, session)
            return {"status": "ok", "data": result}
        except NativeWorkflowError:
            raise
        except Exception as error:
            self._binding_validator()
            try:
                from todo_orchestrator.authority import StaleAuthorityRevisionError
            except ImportError:
                StaleAuthorityRevisionError = ()
            if isinstance(error, StaleAuthorityRevisionError):
                if stale_revision_safe:
                    raise NativeWorkflowError("stale_authority_revision") from error
                # An external-effect operation can fail with this class after
                # a provider has already acted. Never let its class or a
                # provider-supplied `.code` imply a safe pre-handler CAS miss.
                raise NativeWorkflowError("native_operation_rejected") from error
            code = getattr(error, "code", None)
            if isinstance(code, str) and code:
                raise NativeWorkflowError(code) from error
            # Do not echo authority, provider, path, or authored-content errors.
            raise NativeWorkflowError("native_operation_rejected") from error
        finally:
            try:
                self._binding_validator()
            finally:
                self._close(session)

    @staticmethod
    def _idempotency_key(value: object):
        from todo_orchestrator.contracts import StableId

        return StableId(_required_text(value, "idempotency_key", max_bytes=128))

    @staticmethod
    def _run_schedule(session: object, plan_revision_id: str, raw: object):
        from todo_orchestrator.api import IsolationPolicy, LaneSpec, QueuePolicy, RunCharter, RunSchedule, ScheduledWork
        from todo_orchestrator.contracts import StableId

        value = _json_mapping(raw, "schedule")
        if set(value) != {"run_id", "lanes", "work", "charter"}:
            raise ValueError("schedule_fields_invalid")
        lanes_raw, work_raw = value["lanes"], value["work"]
        if not isinstance(lanes_raw, list) or not isinstance(work_raw, list):
            raise ValueError("schedule_lists_invalid")
        if len(lanes_raw) > _MAX_COLLECTION or len(work_raw) > _MAX_COLLECTION:
            raise ValueError("schedule_too_large")
        lanes = []
        for item in lanes_raw:
            row = _json_mapping(item, "lane")
            if set(row) - {"lane_id", "queue_policy", "required_role", "isolation_policy"} or "lane_id" not in row:
                raise ValueError("lane_fields_invalid")
            role = row.get("required_role")
            lanes.append(LaneSpec(
                StableId(_required_text(row["lane_id"], "lane_id", max_bytes=128)),
                QueuePolicy(row.get("queue_policy", "serial")),
                None if role is None else StableId(_required_text(role, "required_role", max_bytes=128)),
                IsolationPolicy(row.get("isolation_policy", "shared_scopes")),
            ))
        work = []
        for item in work_raw:
            row = _json_mapping(item, "scheduled_work")
            if set(row) - {"work_item_id", "lane_id"} or "work_item_id" not in row:
                raise ValueError("scheduled_work_fields_invalid")
            lane_id = row.get("lane_id")
            work.append(ScheduledWork(
                session.work_item_ref(_required_text(row["work_item_id"], "work_item_id", max_bytes=128),
                                      StableId(plan_revision_id)),
                None if lane_id is None else StableId(_required_text(lane_id, "lane_id", max_bytes=128)),
            ))
        charter_raw = value.get("charter")
        charter = None
        if charter_raw is not None:
            charter = _json_mapping(charter_raw, "charter")
            if set(charter) != {"objective", "boundaries", "acceptance_conditions"}:
                raise ValueError("charter_fields_invalid")
            if not isinstance(charter["boundaries"], list) or not isinstance(charter["acceptance_conditions"], list):
                raise ValueError("charter_lists_invalid")
            charter = RunCharter(str(charter["objective"]), tuple(charter["boundaries"]),
                                 tuple(charter["acceptance_conditions"]))
        return RunSchedule(
            StableId(_required_text(value["run_id"], "run_id", max_bytes=128)),
            session.ref("plan_revision", plan_revision_id), tuple(lanes), tuple(work), charter,
        )

    def invoke(self, tool: str, **arguments: object) -> dict[str, object]:
        """Dispatch a typed Todo API operation or a private host adapter route."""
        try:
            # Resolve and validate executable identity before importing the
            # colliding ``todo_orchestrator`` namespace or constructing a DTO.
            self._binding_validator()
            project = _required_text(arguments.get("project"), "project", max_bytes=128)
            repository = _optional_repository(arguments.get("repository"))
            def guarded(operation, **kwargs):
                kwargs.setdefault("stale_revision_safe", tool in _STALE_REVISION_SAFE_OPERATIONS)
                return self._guarded(project, operation, repository=repository, **kwargs)
            if tool == "todo_preview_plan":
                package_json = _required_text(arguments.get("package_json"), "package_json", max_bytes=_MAX_PLAN_BYTES)
                return guarded(lambda _root, session: session.preview_plan(package_json).to_dict())
            if tool == "todo_commit_plan":
                from todo_orchestrator.api import PlanPreviewV1

                package_json = _required_text(arguments.get("package_json"), "package_json", max_bytes=_MAX_PLAN_BYTES)
                preview_raw = arguments.get("preview")
                if isinstance(preview_raw, str):
                    preview = PlanPreviewV1.from_json(preview_raw)
                else:
                    preview = PlanPreviewV1.from_json(json.dumps(preview_raw, allow_nan=False,
                                                                  sort_keys=True, separators=(",", ":")))
                key = self._idempotency_key(arguments.get("idempotency_key"))
                return guarded(lambda _root, session:
                                     session.commit_plan(package_json, preview, idempotency_key=key).to_dict())
            if tool == "todo_read_authority":
                revision = _optional_revision(arguments.get("expected_revision"))
                limit = arguments.get("limit", 50)
                page_token = arguments.get("page_token")
                return guarded(lambda _root, session:
                                     _authority_page(session, expected_revision=revision,
                                                     limit=limit, page_token=page_token))
            if tool == "todo_open_run":
                from todo_orchestrator.contracts import StableId

                plan_revision_id = _required_text(arguments.get("plan_revision_id"), "plan_revision_id", max_bytes=128)
                key = self._idempotency_key(arguments.get("idempotency_key"))
                revision = _optional_revision(arguments.get("expected_revision"))

                def open_run(_root, session):
                    return session.open_run(StableId(plan_revision_id),
                                            self._run_schedule(session, plan_revision_id, arguments.get("schedule")),
                                            idempotency_key=key, expected_revision=revision).to_dict()
                return guarded(open_run)
            if tool == "todo_read_run":
                run_id = _required_text(arguments.get("run_id"), "run_id", max_bytes=128)
                return guarded(lambda _root, session: _public(session, "run_schedule", session.read_run(run_id)))
            if tool == "todo_select_next_work":
                from todo_orchestrator.contracts import StableId

                selectors = {}
                for name in ("run_id", "work_item_id", "assignment_id", "plan_revision_id"):
                    value = arguments.get(name)
                    if value is not None:
                        selectors[name] = _required_text(value, name, max_bytes=128)

                def select_next(_root, session):
                    selection = session.select_next_work(
                        run_id=selectors.get("run_id"),
                        target=selectors.get("work_item_id"),
                        assignment=selectors.get("assignment_id"),
                        plan_revision_id=(None if selectors.get("plan_revision_id") is None
                                          else StableId(selectors["plan_revision_id"])),
                    )
                    if selection is None:
                        return {"selection": None}
                    result = selection.to_dict()
                    if not isinstance(result, dict):
                        raise NativeWorkflowError("native_selection_invalid")
                    if (selectors.get("work_item_id") is not None
                            and result.get("work_item_id") != selectors["work_item_id"]):
                        raise NativeWorkflowError("native_selection_scope_mismatch")
                    if (selectors.get("run_id") is not None
                            and result.get("run_id") != selectors["run_id"]):
                        raise NativeWorkflowError("native_selection_scope_mismatch")
                    if (selectors.get("assignment_id") is not None
                            and result.get("assignment_id") != selectors["assignment_id"]):
                        raise NativeWorkflowError("native_selection_scope_mismatch")
                    if (selectors.get("plan_revision_id") is not None
                            and result.get("plan_revision_id") != selectors["plan_revision_id"]):
                        raise NativeWorkflowError("native_selection_scope_mismatch")
                    return result

                return guarded(select_next)
            if tool == "todo_create_workspace":
                from todo_orchestrator.contracts import StableId

                work_item_id = _required_text(arguments.get("work_item_id"), "work_item_id", max_bytes=128)
                plan_revision_id = arguments.get("plan_revision_id")
                run_id = arguments.get("run_id")
                assignment_id = arguments.get("assignment_id")
                workspace_id = arguments.get("workspace_id")
                key = self._idempotency_key(arguments.get("idempotency_key"))
                revision = _optional_revision(arguments.get("expected_revision"))
                def create_workspace(_root, session):
                    result = session.create_workspace(
                        work_item_id, idempotency_key=key,
                        workspace_id=None if workspace_id is None else StableId(_required_text(workspace_id, "workspace_id", max_bytes=128)),
                        run_id=run_id, assignment=assignment_id,
                        plan_revision_id=None if plan_revision_id is None else StableId(_required_text(plan_revision_id, "plan_revision_id", max_bytes=128)),
                        expected_revision=revision,
                    )
                    packet = _public(session, "git_effect", result)
                    # The host resolves this locator; callers never select a
                    # filesystem root or infer a provider's private layout.
                    _bound_root, selected_repository = self._project_root(project, repository)
                    composition = self._providers(project, _bound_root, selected_repository)
                    git_provider = getattr(composition, "git_provider", None)
                    locate = getattr(git_provider, "workspace_path_for", None)
                    created_id = getattr(getattr(result, "request", None), "workspace_id", None)
                    if callable(locate) and created_id is not None:
                        packet["workspace"] = {
                            "workspace_id": str(created_id),
                            "path": str(locate(created_id)),
                            "repository": selected_repository,
                        }
                    return packet
                return guarded(create_workspace)
            if tool == "todo_dispatch_attempt":
                from todo_orchestrator.contracts import StableId

                workspace_id = _required_text(arguments.get("workspace_id"), "workspace_id", max_bytes=128)
                key = self._idempotency_key(arguments.get("idempotency_key"))
                revision = _optional_revision(arguments.get("expected_revision"))
                selectors = {}
                for name in ("work_item_id", "run_id", "assignment_id", "plan_revision_id"):
                    value = arguments.get(name)
                    if value is not None:
                        selectors[name] = _required_text(value, name, max_bytes=128)
                def dispatch(root, session):
                    result = session.dispatch(
                        StableId(workspace_id), idempotency_key=key,
                        work_item=selectors.get("work_item_id"), run_id=selectors.get("run_id"),
                        assignment=selectors.get("assignment_id"),
                        plan_revision_id=None if selectors.get("plan_revision_id") is None else StableId(selectors["plan_revision_id"]),
                        expected_revision=revision,
                    )
                    token_id = str(result.attempt_ref.entity_id)
                    try:
                        self._store_attempt_bearer(
                            root, principal=self.host.principal, profile=self.host.profile,
                            attempt_id=token_id, bearer_token=result.bearer_token,
                        )
                    except Exception as error:
                        raise NativeWorkflowError("dispatch_secret_storage_failed") from error
                    return result.to_dict()
                return guarded(dispatch)
            if tool == "todo_inspect_effect":
                operation_id = _required_text(arguments.get("operation_id"), "operation_id", max_bytes=128)
                return guarded(lambda _root, session:
                                     _public(session, "recovery_inspection", session.inspect_effect(operation_id)))
            if tool in {"todo_integrate_attempt", "todo_verify_attempt", "todo_report_outcome",
                        "todo_accept_outcome", "todo_reject_outcome", "todo_finish_attempt",
                        "todo_handback_attempt",
                        "todo_heartbeat_attempt", "todo_resume_attempt", "todo_release_attempt",
                        "todo_reserve_resource", "todo_inspect_resource", "todo_reconcile_resource",
                        "todo_retry_resource", "todo_release_resource", "todo_inspect_attempt",
                        }:
                return self._attempt_operation(tool, project, arguments, repository)
            if tool in {"todo_send_message", "todo_ask_question", "todo_answer_question",
                        "todo_sync_inbox", "todo_mark_messages_read", "todo_inbox_status"}:
                return self._message_operation(tool, project, arguments, repository)
            if tool in {"todo_read_child_execution", "todo_submit_child_candidate",
                        "todo_review_child_candidate", "todo_cancel_child", "todo_reconcile_child",
                        "todo_start_child"}:
                return self._child_operation(tool, project, arguments, repository)
            if tool == "todo_read_candidate":
                candidate_id = _required_text(arguments.get("candidate_id"), "candidate_id", max_bytes=128)
                return guarded(lambda _root, session:
                                     _public(session, "outcome_candidate", session.read_candidate(candidate_id)))
            if tool == "todo_apply_recovery":
                intent = _required_text(arguments.get("intent"), "intent", max_bytes=_MAX_PLAN_BYTES)
                key = self._idempotency_key(arguments.get("idempotency_key"))
                revision = _optional_revision(arguments.get("expected_revision"))
                return guarded(lambda _root, session:
                                     _public(session, "recovery", session.apply_recovery(
                                         intent, idempotency_key=key, expected_revision=revision)))
            if tool in {"todo_close_run", "todo_retire_run"}:
                return self._lifecycle_operation(tool, project, arguments, repository)
            if tool in {"todo_resolve_decision", "todo_publish_interface", "todo_accept_checkpoint",
                        "todo_release_barrier", "todo_arrive_rendezvous", "todo_open_blocker",
                        "todo_clear_blocker"}:
                return self._declaration_operation(tool, project, arguments, repository)
            if tool == "todo_build_context":
                work_item_id = _required_text(arguments.get("work_item_id"), "work_item_id", max_bytes=128)
                optional = {name: arguments.get(name) for name in ("run_id", "assignment_id", "attempt_id")}
                max_bytes = arguments.get("max_bytes", 16 * 1024)
                if type(max_bytes) is not int or not 4096 <= max_bytes <= 64 * 1024:
                    raise ValueError("max_bytes_invalid")
                return guarded(lambda _root, session:
                                     _public(session, "context_capsule", session.build_context(
                                         work_item_id, run_id=optional["run_id"],
                                         assignment=optional["assignment_id"], attempt=optional["attempt_id"],
                                         max_bytes=max_bytes)),
                                     attempt_id=optional["attempt_id"])
            if tool == "todo_expand_context":
                work_item_id = _required_text(arguments.get("work_item_id"), "work_item_id", max_bytes=128)
                optional = {name: arguments.get(name) for name in ("run_id", "assignment_id", "attempt_id")}
                max_bytes = arguments.get("max_bytes", 16 * 1024)
                offset = arguments.get("offset")
                since_revision = arguments.get("since_revision")
                if type(max_bytes) is not int or not 4096 <= max_bytes <= 64 * 1024:
                    raise ValueError("max_bytes_invalid")
                if offset is not None and (type(offset) is not int or offset < 0):
                    raise ValueError("offset_invalid")
                if since_revision is not None and (type(since_revision) is not int or since_revision < 0):
                    raise ValueError("since_revision_invalid")
                return guarded(lambda _root, session:
                                     _public(session, "context_expansion", session.expand_context(
                                         work_item_id,
                                         _required_text(arguments.get("fragment_id"), "fragment_id", max_bytes=128),
                                         _required_text(arguments.get("expected_digest"), "expected_digest", max_bytes=64),
                                         run_id=optional["run_id"], assignment=optional["assignment_id"],
                                         attempt=optional["attempt_id"], max_bytes=max_bytes,
                                         offset=offset, since_revision=since_revision)),
                                     attempt_id=optional["attempt_id"])
            if tool == "todo_publish_finding":
                return self._finding_operation(tool, project, arguments, repository)
            if tool == "todo_invalidate_finding":
                return self._finding_operation(tool, project, arguments, repository)
            if tool == "todo_search_legacy_history":
                query = arguments.get("query", "")
                if not isinstance(query, str) or len(query.encode("utf-8")) > 512:
                    raise ValueError("query_invalid")
                limit = arguments.get("limit", 100)
                if type(limit) is not int or not 1 <= limit <= 500:
                    raise ValueError("limit_invalid")
                import_id = arguments.get("import_id")
                if import_id is not None:
                    import_id = _required_text(import_id, "import_id", max_bytes=128)
                after = arguments.get("after")
                if after is not None:
                    after = _json_mapping(after, "after")
                    if set(after) != {"source_revision", "occurred_at", "history_id"}:
                        raise ValueError("after_invalid")
                    if (type(after["source_revision"]) is not int or after["source_revision"] < 0
                            or not isinstance(after["occurred_at"], str)
                            or not isinstance(after["history_id"], str)):
                        raise ValueError("after_invalid")
                return guarded(lambda _root, session:
                                     _public(session, "legacy-history-page", session.search_legacy_history(
                                         query, import_id=import_id, limit=limit, after=after)))
            if tool == "todo_read_legacy_current_tasks":
                limit = arguments.get("limit", 100)
                if type(limit) is not int or not 1 <= limit <= 500:
                    raise ValueError("limit_invalid")
                import_id = arguments.get("import_id")
                if import_id is not None:
                    import_id = _required_text(import_id, "import_id", max_bytes=128)
                after = arguments.get("after")
                if after is not None:
                    after = _json_mapping(after, "after")
                    if set(after) != {"import_id", "source_task_id"}:
                        raise ValueError("after_invalid")
                    if not all(isinstance(after[name], str) and after[name]
                               for name in ("import_id", "source_task_id")):
                        raise ValueError("after_invalid")
                return guarded(lambda _root, session:
                                     _public(session, "legacy-current-task-page", session.read_legacy_current_tasks(
                                         import_id=import_id, limit=limit, after=after)))
            if tool == "todo_read_legacy_project_context":
                limit = arguments.get("limit", 100)
                if type(limit) is not int or not 1 <= limit <= 200:
                    raise ValueError("limit_invalid")
                import_id = arguments.get("import_id")
                if import_id is not None:
                    import_id = _required_text(import_id, "import_id", max_bytes=128)
                after = arguments.get("after")
                if after is not None:
                    after = _json_mapping(after, "after")
                    if set(after) != {"import_id", "source_table", "source_id"}:
                        raise ValueError("after_invalid")
                    if (not isinstance(after["import_id"], str) or not after["import_id"]
                            or not isinstance(after["source_table"], str)
                            or after["source_table"] not in {
                                "project_declarations", "project_amendments", "project_fragment_invalidations",
                            }
                            or not isinstance(after["source_id"], str) or not after["source_id"]):
                        raise ValueError("after_invalid")
                return guarded(lambda _root, session:
                                     _public(session, "legacy-project-context-page",
                                             session.read_legacy_project_context(
                                                 import_id=import_id, limit=limit, after=after)))
            if tool == "todo_preview_legacy_import":
                snapshot = _json_mapping(arguments.get("snapshot"), "snapshot")
                accepted_plan = _required_text(arguments.get("accepted_plan_revision_id"),
                                                "accepted_plan_revision_id", max_bytes=128)
                return guarded(lambda _root, session:
                                     _public(session, "legacy_import_preview", session.preview_legacy_import(
                                         snapshot, accepted_plan_revision_id=accepted_plan)))
            if tool == "todo_import_legacy_snapshot":
                snapshot = _json_mapping(arguments.get("snapshot"), "snapshot")
                preview = _json_mapping(arguments.get("preview"), "preview")
                key = self._idempotency_key(arguments.get("idempotency_key"))
                return guarded(lambda _root, session:
                                     _public(session, "legacy_import_receipt", session.import_legacy_snapshot(
                                         snapshot, preview, idempotency_key=key)))
            raise NativeWorkflowError("native_todo_operation_unknown")
        except NativeWorkflowError as error:
            return {"status": "attention_required", "reason": error.code}
        except Exception as error:
            code = getattr(error, "code", None)
            return {"status": "attention_required", "reason": code if isinstance(code, str) else "native_operation_rejected"}

    def _attempt_operation(self, tool: str, project: str, args: Mapping[str, object],
                           repository: str | None) -> dict[str, object]:
        attempt_id = (None if tool in _RESOURCE_OPERATION_ID_ONLY else
                      _required_text(args.get("attempt_id"), "attempt_id", max_bytes=128))
        mutating = tool not in {"todo_inspect_resource", "todo_inspect_attempt"}
        key = self._idempotency_key(args.get("idempotency_key")) if mutating else None
        revision = _optional_revision(args.get("expected_revision"))
        def action(root, session):
            if tool == "todo_integrate_attempt":
                value = session.integrate_attempt(attempt_id, idempotency_key=key, expected_revision=revision)
                return _public(session, "git_effect", value)
            if tool == "todo_verify_attempt":
                requirement_id = _required_text(args.get("requirement_id"), "requirement_id", max_bytes=128)
                return _public(session, "verification", session.verify(attempt_id, requirement_id,
                              idempotency_key=key, expected_revision=revision))
            if tool == "todo_heartbeat_attempt":
                plan_revision = _required_text(args.get("plan_revision_id"), "plan_revision_id", max_bytes=128)
                lease_id = _required_text(args.get("lease_id"), "lease_id", max_bytes=128)
                return session.heartbeat_attempt(attempt_id, lease_id, plan_revision_id=plan_revision,
                                                 idempotency_key=key, expected_revision=revision).to_dict()
            if tool == "todo_resume_attempt":
                plan_revision = _required_text(args.get("plan_revision_id"), "plan_revision_id", max_bytes=128)
                lease_id = _required_text(args.get("lease_id"), "lease_id", max_bytes=128)
                optional = {}
                for name in ("work_item_id", "run_id", "assignment_id"):
                    value = args.get(name)
                    if value is not None:
                        optional[name] = _required_text(value, name, max_bytes=128)
                return session.resume_attempt(
                    attempt_id, lease_id, plan_revision_id=plan_revision,
                    work_item=optional.get("work_item_id"), run_id=optional.get("run_id"),
                    assignment=optional.get("assignment_id"), idempotency_key=key,
                    expected_revision=revision,
                ).to_dict()
            if tool == "todo_release_attempt":
                lease_id = _required_text(args.get("lease_id"), "lease_id", max_bytes=128)
                return session.release_attempt(attempt_id, lease_id, idempotency_key=key,
                                               expected_revision=revision).to_dict()
            if tool == "todo_report_outcome":
                value = session.report(attempt_id, args.get("disposition"), idempotency_key=key,
                                       expected_revision=revision, note=args.get("note"))
                return value.to_dict()
            if tool in {"todo_accept_outcome", "todo_reject_outcome"}:
                candidate = _required_text(args.get("candidate_id"), "candidate_id", max_bytes=128)
                method = session.accept if tool == "todo_accept_outcome" else session.reject
                return method(attempt_id, candidate, idempotency_key=key,
                              expected_revision=revision).to_dict()
            if tool == "todo_finish_attempt":
                lease_id = _required_text(args.get("lease_id"), "lease_id", max_bytes=128)
                value = session.finish(attempt_id, lease_id, idempotency_key=key, expected_revision=revision)
                try:
                    self._forget_attempt_bearer(root, principal=self.host.principal,
                                                profile=self.host.profile, attempt_id=attempt_id)
                    cleanup = "complete"
                except Exception:
                    # The authority commit is confirmed. Report cleanup as a
                    # host obligation instead of misreporting the commit.
                    cleanup = "pending"
                return {"receipt": value.to_dict(), "attempt_credential_cleanup": cleanup}
            if tool == "todo_handback_attempt":
                lease_id = _required_text(args.get("lease_id"), "lease_id", max_bytes=128)
                value = session.handback_attempt(attempt_id, lease_id, idempotency_key=key,
                                                 expected_revision=revision)
                receipt = value.to_dict()
                if (receipt.get("command_kind") != "todo.attempt.authoring_handoff"
                        or not isinstance(receipt.get("result"), Mapping)):
                    raise NativeWorkflowError("authoring_handoff_receipt_invalid")
                result = receipt["result"]
                source_commit = _required_text(result.get("source_commit"), "source_commit", max_bytes=128)
                source_digest = _required_text(result.get("source_digest"), "source_digest", max_bytes=64)
                if (result.get("attempt_id") != attempt_id
                        or result.get("state") not in {"admitted", "active"}
                        or len(source_digest) != 64
                        or any(character not in "0123456789abcdef" for character in source_digest)):
                    raise NativeWorkflowError("authoring_handoff_scope_mismatch")
                return {
                    "attempt_id": attempt_id,
                    "lease_id": lease_id,
                    "state": result["state"],
                    "source_commit": source_commit,
                    "source_digest": source_digest,
                    "receipt": receipt,
                }
            if tool == "todo_reserve_resource":
                resource_class = _required_text(args.get("resource_class"), "resource_class", max_bytes=128)
                units = args.get("units")
                if type(units) is not int or units <= 0:
                    raise ValueError("units_invalid")
                value = session.reserve_resource(attempt_id, resource_class, units,
                                                idempotency_key=key, expected_revision=revision)
                return _public(session, "resource_permit", value)
            if tool == "todo_inspect_resource":
                operation_id = _required_text(args.get("operation_id"), "operation_id", max_bytes=128)
                return _public(session, "resource_permit", session.inspect_resource(operation_id))
            if tool == "todo_reconcile_resource":
                operation_id = _required_text(args.get("operation_id"), "operation_id", max_bytes=128)
                value = session.reconcile_resource(operation_id, idempotency_key=key,
                                                   expected_revision=revision)
                return _public(session, "resource_permit", value)
            if tool == "todo_retry_resource":
                operation_id = _required_text(args.get("operation_id"), "operation_id", max_bytes=128)
                value = session.retry_resource(operation_id, idempotency_key=key,
                                               expected_revision=revision)
                return _public(session, "resource_permit", value)
            if tool == "todo_release_resource":
                operation_id = _required_text(args.get("operation_id"), "operation_id", max_bytes=128)
                value = session.release_resource(operation_id, idempotency_key=key,
                                                 expected_revision=revision)
                return _public(session, "resource_permit", value)
            if tool == "todo_inspect_attempt":
                return _public(session, "recovery_inspection", session.inspect_attempt(attempt_id))
            raise NativeWorkflowError("native_todo_operation_unknown")
        # These lifecycle and review operations use the profile's project-level
        # host grant, so an expired worker bearer cannot prevent source-fenced
        # finalization. Native ownership, capability and handoff checks still
        # apply to the exact attempt ID. Computation, verification and renewal
        # continue to use the worker's attempt-scoped session.
        session_attempt_id = None if tool in _HOST_SESSION_OPERATIONS else attempt_id
        return self._guarded(
            project, action, attempt_id=session_attempt_id, repository=repository,
            stale_revision_safe=tool in _STALE_REVISION_SAFE_OPERATIONS,
        )

    def _lifecycle_operation(self, tool: str, project: str, args: Mapping[str, object],
                             repository: str | None) -> dict[str, object]:
        from todo_orchestrator.contracts import StableId, Sha256Digest

        run_id = _required_text(args.get("run_id"), "run_id", max_bytes=128)
        key = self._idempotency_key(args.get("idempotency_key"))
        revision = _optional_revision(args.get("expected_revision"))
        if revision is None:
            raise ValueError("expected_revision_required")

        handoff_rows: tuple[tuple[str, str], ...] = ()
        unresolved_rows: tuple[tuple[str, str], ...] = ()
        if tool == "todo_retire_run":
            allowed = {
                "project", "repository", "run_id", "successor_run_id", "schedule_digest", "idempotency_key",
                "expected_revision", "disposition_schema_version", "handoffs", "unresolved",
            }
            if set(args) - allowed:
                raise ValueError("retire_run_fields_invalid")
            has_version = "disposition_schema_version" in args
            has_dispositions = "handoffs" in args or "unresolved" in args
            if has_version != has_dispositions:
                raise ValueError("retirement_dispositions_require_version")
            if has_version:
                version = args.get("disposition_schema_version")
                if type(version) is not int or version != 1:
                    raise ValueError("disposition_schema_version_unsupported")
                if "handoffs" not in args or "unresolved" not in args:
                    raise ValueError("retirement_disposition_arrays_required")
                raw_handoffs, raw_unresolved = args["handoffs"], args["unresolved"]
                if not isinstance(raw_handoffs, list) or not isinstance(raw_unresolved, list):
                    raise ValueError("retirement_disposition_arrays_invalid")
                if len(raw_handoffs) + len(raw_unresolved) > _MAX_COLLECTION:
                    raise ValueError("retirement_dispositions_too_large")
                parsed_handoffs: list[tuple[str, str]] = []
                for raw in raw_handoffs:
                    row = _json_mapping(raw, "handoff")
                    if set(row) != {"source_assignment_id", "successor_assignment_id"}:
                        raise ValueError("handoff_fields_invalid")
                    parsed_handoffs.append((
                        _required_text(row["source_assignment_id"], "source_assignment_id", max_bytes=128),
                        _required_text(row["successor_assignment_id"], "successor_assignment_id", max_bytes=128),
                    ))
                parsed_unresolved: list[tuple[str, str]] = []
                for raw in raw_unresolved:
                    row = _json_mapping(raw, "unresolved")
                    if set(row) != {"assignment_id", "reason"}:
                        raise ValueError("unresolved_fields_invalid")
                    parsed_unresolved.append((
                        _required_text(row["assignment_id"], "assignment_id", max_bytes=128),
                        _required_text(row["reason"], "reason", max_bytes=512),
                    ))
                handoff_sources = [source for source, _target in parsed_handoffs]
                unresolved_ids = [assignment for assignment, _reason in parsed_unresolved]
                if len(set(handoff_sources)) != len(handoff_sources) or len(set(unresolved_ids)) != len(unresolved_ids):
                    raise ValueError("retirement_assignment_disposition_duplicate")
                if set(handoff_sources) & set(unresolved_ids):
                    raise ValueError("retirement_assignment_disposition_conflict")
                if parsed_handoffs and args.get("successor_run_id") is None:
                    raise ValueError("retirement_handoffs_require_successor")
                handoff_rows = tuple(parsed_handoffs)
                unresolved_rows = tuple(parsed_unresolved)

        def action(_root, session):
            digest_value = args.get("schedule_digest")
            digest = Sha256Digest(_required_text(digest_value, "schedule_digest", max_bytes=64)) if digest_value else session.run_schedule_digest(run_id)
            if tool == "todo_close_run":
                value = session.close_run(run_id, digest, idempotency_key=key, expected_revision=revision)
            else:
                from todo_orchestrator.operations import RunAssignmentHandoff, UnresolvedRunAssignment

                successor = args.get("successor_run_id")
                successor_id = None if successor is None else StableId(
                    _required_text(successor, "successor_run_id", max_bytes=128)
                )
                handoffs = tuple(
                    RunAssignmentHandoff(session.assignment_ref(source), session.assignment_ref(target))
                    for source, target in handoff_rows
                )
                unresolved = tuple(
                    UnresolvedRunAssignment(session.assignment_ref(assignment), reason)
                    for assignment, reason in unresolved_rows
                )
                value = session.retire_run(
                    run_id, digest, idempotency_key=key, expected_revision=revision,
                    successor_run=successor_id, handoffs=handoffs, unresolved=unresolved,
                )
            return value.to_dict()
        return self._guarded(
            project, action, repository=repository,
            stale_revision_safe=tool in _STALE_REVISION_SAFE_OPERATIONS,
        )

    def _declaration_operation(self, tool: str, project: str,
                               args: Mapping[str, object], repository: str | None) -> dict[str, object]:
        key = self._idempotency_key(args.get("idempotency_key"))
        revision = _optional_revision(args.get("expected_revision"))
        plan_revision = _required_text(args.get("plan_revision_id"), "plan_revision_id", max_bytes=128)
        declaration_id = _required_text(args.get("declaration_id"), "declaration_id", max_bytes=128)
        context = {name: args.get(name) for name in ("run_id", "assignment_id", "attempt_id")}
        mapping = {
            "todo_resolve_decision": ("resolve_decision", ("option_id", "rationale")),
            "todo_publish_interface": ("publish_interface", ("version", "rationale")),
            "todo_accept_checkpoint": ("accept_checkpoint", ("evidence_ids", "rationale")),
            "todo_release_barrier": ("release_barrier", ("rationale",)),
            "todo_arrive_rendezvous": ("arrive_rendezvous", ("participant",)),
            "todo_open_blocker": ("open_blocker", ("reason",)),
            "todo_clear_blocker": ("clear_blocker", ("reason",)),
        }
        method_name, fields = mapping[tool]
        def action(_root, session):
            from todo_orchestrator.contracts import StableId
            method = getattr(session, method_name)
            values = {}
            for field in fields:
                value = args.get(field)
                if field in {"option_id", "evidence_ids"}:
                    if field == "evidence_ids":
                        if not isinstance(value, list) or len(value) > _MAX_COLLECTION:
                            raise ValueError("evidence_ids_invalid")
                        values[field] = tuple(StableId(_required_text(item, field, max_bytes=128)) for item in value)
                    else:
                        values[field] = StableId(_required_text(value, field, max_bytes=128))
                elif field == "participant":
                    participant = _json_mapping(value, "participant")
                    values[field] = session.ref(participant.get("kind"), _required_text(participant.get("entity_id"), "participant_id", max_bytes=128))
                else:
                    values[field] = _required_text(value, field, max_bytes=1000)
            result = method(declaration_id, **values, plan_revision_id=plan_revision,
                            idempotency_key=key, expected_revision=revision,
                            run_id=context["run_id"], assignment=context["assignment_id"],
                            attempt=context["attempt_id"])
            return result.to_dict()
        return self._guarded(project, action, attempt_id=context["attempt_id"],
                             repository=repository,
                             stale_revision_safe=tool in _STALE_REVISION_SAFE_OPERATIONS)

    def _finding_operation(self, tool: str, project: str,
                           args: Mapping[str, object], repository: str | None) -> dict[str, object]:
        key = self._idempotency_key(args.get("idempotency_key"))
        revision = _optional_revision(args.get("expected_revision"))
        work_item_id = _required_text(args.get("work_item_id"), "work_item_id", max_bytes=128)
        context = {name: args.get(name) for name in ("run_id", "assignment_id", "attempt_id")}
        manifest = _json_mapping(args.get("source_manifest"), "source_manifest")
        def action(_root, session):
            if tool == "todo_publish_finding":
                fragments = args.get("source_fragment_refs")
                if not isinstance(fragments, list) or len(fragments) > _MAX_COLLECTION:
                    raise ValueError("source_fragment_refs_invalid")
                summary = _required_text(args.get("summary"), "summary", max_bytes=4096)
                finding_id = args.get("finding_id")
                version = args.get("expected_finding_version")
                if version is not None and (type(version) is not int or version < 0):
                    raise ValueError("expected_finding_version_invalid")
                value = session.publish_finding(
                    work_item_id, summary, manifest, tuple(fragments), finding_id=finding_id,
                    expected_finding_version=version, run_id=context["run_id"],
                    assignment=context["assignment_id"], attempt=context["attempt_id"],
                    idempotency_key=key, expected_revision=revision,
                )
            else:
                finding_version = args.get("finding_version")
                if type(finding_version) is not int or finding_version < 1:
                    raise ValueError("finding_version_invalid")
                value = session.invalidate_finding(
                    work_item_id, _required_text(args.get("finding_id"), "finding_id", max_bytes=128),
                    finding_version, _required_text(args.get("reason"), "reason", max_bytes=1000),
                    manifest, run_id=context["run_id"], assignment=context["assignment_id"],
                    attempt=context["attempt_id"], idempotency_key=key,
                    expected_revision=revision,
                )
            return value.to_dict()
        return self._guarded(project, action, attempt_id=context["attempt_id"],
                             repository=repository)

    def _message_operation(self, tool: str, project: str,
                           args: Mapping[str, object], repository: str | None) -> dict[str, object]:
        context = {name: args.get(name) for name in ("run_id", "assignment_id", "attempt_id")}
        key = None if tool == "todo_inbox_status" else self._idempotency_key(args.get("idempotency_key"))
        revision = _optional_revision(args.get("expected_revision"))
        def action(_root, session):
            if tool == "todo_answer_question":
                question_id = _required_text(args.get("question_id"), "question_id", max_bytes=128)
                value = session.answer_question(
                    question_id, body=args.get("body"), content_ref=args.get("content_ref"),
                    run_id=context["run_id"], assignment=context["assignment_id"],
                    attempt=context["attempt_id"], idempotency_key=key,
                    expected_revision=revision,
                )
                return value.to_dict()
            scope = _json_mapping(args.get("scope"), "scope")
            if tool == "todo_inbox_status":
                return _public(session, "inbox_status", session.inbox_status(
                    scope, run_id=context["run_id"], assignment=context["assignment_id"],
                    attempt=context["attempt_id"],
                ))
            if tool in {"todo_send_message", "todo_ask_question"}:
                recipients = args.get("recipients")
                if not isinstance(recipients, list) or not 1 <= len(recipients) <= 128:
                    raise ValueError("recipients_invalid")
                normalized_recipients = tuple(_json_mapping(value, "recipient") for value in recipients)
                subject = _required_text(args.get("subject"), "subject", max_bytes=512)
                options = {
                    "subject": subject, "body": args.get("body"),
                    "content_ref": args.get("content_ref"), "run_id": context["run_id"],
                    "assignment": context["assignment_id"], "attempt": context["attempt_id"],
                    "idempotency_key": key, "expected_revision": revision,
                }
                if tool == "todo_send_message":
                    options["message_type"] = _required_text(args.get("message_type", "status"), "message_type", max_bytes=64)
                    value = session.send_message(scope, normalized_recipients, **options)
                else:
                    value = session.ask_question(scope, normalized_recipients, **options)
                return value.to_dict()
            if tool == "todo_sync_inbox":
                limit = args.get("limit", 50)
                if type(limit) is not int or not 1 <= limit <= 200:
                    raise ValueError("limit_invalid")
                value = session.sync_inbox(
                    scope, run_id=context["run_id"], assignment=context["assignment_id"],
                    attempt=context["attempt_id"], limit=limit, idempotency_key=key,
                    expected_revision=revision,
                )
                return _public(session, "inbox_page", value)
            if tool == "todo_mark_messages_read":
                sequence = args.get("through_sequence")
                if type(sequence) is not int or sequence < 0:
                    raise ValueError("through_sequence_invalid")
                value = session.mark_messages_read(
                    scope, sequence, run_id=context["run_id"], assignment=context["assignment_id"],
                    attempt=context["attempt_id"], idempotency_key=key,
                    expected_revision=revision,
                )
                return value.to_dict()
            raise NativeWorkflowError("native_todo_operation_unknown")
        return self._guarded(project, action, attempt_id=context["attempt_id"],
                             repository=repository)

    def _child_operation(self, tool: str, project: str,
                         args: Mapping[str, object], repository: str | None) -> dict[str, object]:
        parent_attempt_id = _required_text(args.get("parent_attempt_id"), "parent_attempt_id", max_bytes=128)
        execution_id = _required_text(args.get("execution_id"), "execution_id", max_bytes=128)
        key = None if tool == "todo_read_child_execution" else self._idempotency_key(args.get("idempotency_key"))
        revision = _optional_revision(args.get("expected_revision"))
        def action(_root, session):
            if tool == "todo_start_child":
                if self._child_credential_for is None:
                    raise NativeWorkflowError("child_credential_issuer_unavailable")
                from todo_orchestrator.api import AttemptScopeSnapshot
                raw_scope = _json_mapping(args.get("scope"), "scope")
                if set(raw_scope) != {"paths", "locks"}:
                    raise ValueError("child_scope_fields_invalid")
                paths_raw, locks_raw = raw_scope["paths"], raw_scope["locks"]
                if not isinstance(paths_raw, list) or not isinstance(locks_raw, list):
                    raise ValueError("child_scope_lists_invalid")
                if len(paths_raw) > _MAX_COLLECTION or len(locks_raw) > _MAX_COLLECTION:
                    raise ValueError("child_scope_too_large")
                def pairs(items, label):
                    output = []
                    for item in items:
                        if not isinstance(item, list) or len(item) != 2:
                            raise ValueError(f"{label}_entry_invalid")
                        output.append((_required_text(item[0], label, max_bytes=512),
                                       _required_text(item[1], label, max_bytes=32)))
                    return tuple(output)
                scope = AttemptScopeSnapshot(pairs(paths_raw, "child_path"), pairs(locks_raw, "child_lock"))
                raw_actions = args.get("actions")
                if not isinstance(raw_actions, list) or not 1 <= len(raw_actions) <= 128:
                    raise ValueError("child_actions_invalid")
                actions = tuple(self._stable_id(item, "action") for item in raw_actions)
                if len(set(actions)) != len(actions):
                    raise ValueError("child_actions_not_unique")
                logical_id = self._stable_id(args.get("logical_id"), "logical_id")
                execution_id = self._stable_id(args.get("execution_id"), "execution_id")
                credential = self._child_credential_for(
                    project_id=str(session.metadata.project_id),
                    principal=self.host.principal, profile=self.host.profile,
                    parent_attempt_id=parent_attempt_id, logical_id=str(logical_id),
                    execution_id=str(execution_id), scope=scope, actions=tuple(str(item) for item in actions),
                    idempotency_key=str(key),
                )
                retry_of = args.get("retry_of")
                if retry_of is not None:
                    retry_of = self._stable_id(retry_of, "retry_of")
                grant = session.start_child(
                    parent_attempt_id, credential, execution_id=execution_id, logical_id=logical_id,
                    scope=scope, actions=actions, idempotency_key=key, retry_of=retry_of,
                    expected_revision=revision,
                )
                try:
                    self._store_child_bearer(
                        _root, principal=self.host.principal, profile=self.host.profile,
                        parent_attempt_id=parent_attempt_id, execution_id=str(execution_id),
                        child_principal=str(grant.view.actor.entity_id), bearer_token=grant.bearer_token,
                    )
                except Exception as error:
                    raise NativeWorkflowError("child_credential_storage_failed") from error
                return _public(session, "child_execution_grant", grant)
            if tool == "todo_read_child_execution":
                value = session.read_child_execution(execution_id)
                return _public(session, "child_execution", value)
            if tool == "todo_submit_child_candidate":
                from todo_orchestrator.api import ChildArtifactRef
                from todo_orchestrator.contracts import Sha256Digest
                artifact_values = args.get("artifacts", [])
                if not isinstance(artifact_values, list) or len(artifact_values) > _MAX_COLLECTION:
                    raise ValueError("artifacts_invalid")
                artifacts = []
                for item in artifact_values:
                    row = _json_mapping(item, "artifact")
                    ref = _json_mapping(row.get("ref"), "artifact_ref")
                    if set(ref) - {"project_id", "kind", "entity_id", "definition_revision_id", "incarnation_id"}:
                        raise ValueError("artifact_ref_fields_invalid")
                    if ref.get("kind") != "artifact":
                        raise ValueError("artifact_ref_kind_invalid")
                    typed_ref = session.ref("artifact", _required_text(ref.get("entity_id"), "artifact_id", max_bytes=128))
                    digest = Sha256Digest(_required_text(row.get("digest"), "digest", max_bytes=64))
                    artifacts.append(ChildArtifactRef(typed_ref, digest))
                value = session.submit_child_candidate(
                    execution_id, candidate_id=self._stable_id(args.get("candidate_id"), "candidate_id"),
                    summary=_required_text(args.get("summary"), "summary", max_bytes=4096),
                    artifacts=tuple(artifacts), idempotency_key=key, expected_revision=revision,
                )
            elif tool == "todo_review_child_candidate":
                decision = _required_text(args.get("decision"), "decision", max_bytes=16)
                if decision not in {"accepted", "rejected"}:
                    raise ValueError("child_decision_invalid")
                reason = args.get("reason")
                if reason is not None:
                    reason = _required_text(reason, "reason", max_bytes=1000)
                value = session.review_child_candidate(
                    execution_id, _required_text(args.get("candidate_id"), "candidate_id", max_bytes=128),
                    decision, review_id=self._stable_id(args.get("review_id"), "review_id"),
                    reason=reason, idempotency_key=key, expected_revision=revision,
                )
            elif tool == "todo_cancel_child":
                reason = args.get("reason")
                if reason is not None:
                    reason = _required_text(reason, "reason", max_bytes=1000)
                value = session.request_child_cancel(
                    execution_id, cancellation_id=self._stable_id(args.get("cancellation_id"), "cancellation_id"),
                    reason=reason, idempotency_key=key, expected_revision=revision,
                )
            elif tool == "todo_reconcile_child":
                value = session.reconcile_child_stopped(execution_id, idempotency_key=key,
                                                        expected_revision=revision)
            else:
                raise NativeWorkflowError("native_todo_operation_unknown")
            return _public(session, "child_operation", value)
        return self._guarded(project, action, attempt_id=parent_attempt_id,
                             repository=repository)

    @staticmethod
    def _stable_id(value: object, field: str):
        from todo_orchestrator.contracts import StableId

        return StableId(_required_text(value, field, max_bytes=128))


def local_mutator_workflow(config: ProjectControlConfig) -> NativeWorkflowHost:
    """Construct the trusted local CLI host using the registered mutator identity."""
    from .as1_context import ContextHost

    host = ContextHost("mutator", "project-control-mutator", frozenset(config.workspaces))

    def providers(active_config, project, project_root, *, principal, profile, repository=None,
                  principal_authenticator=None):
        from .native_providers import providers_for_project

        return providers_for_project(active_config, project, project_root,
                                     principal=principal, profile=profile,
                                     repository=repository,
                                     principal_authenticator=principal_authenticator)

    return NativeWorkflowHost(config, host, providers_for_project=providers)


def invoke_local_mutator(config: ProjectControlConfig, project: str, tool: str,
                         arguments: Mapping[str, object]) -> dict[str, object]:
    """Run one public native Todo operation as the trusted local mutator."""
    if not isinstance(tool, str) or not tool.startswith("todo_"):
        raise ValueError("native_todo_tool_required")
    if not isinstance(arguments, Mapping) or any(not isinstance(key, str) for key in arguments):
        raise ValueError("native_todo_arguments_invalid")
    if "project" in arguments:
        raise ValueError("project_must_be_selected_outside_todo_arguments")
    return local_mutator_workflow(config).invoke(tool, project=project, **dict(arguments))
