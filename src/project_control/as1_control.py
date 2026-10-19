"""Host-scoped Project Control context over the native Todo public API.

The bridge combines current native authority facts with bounded, public
migration records. It never opens a Todo database or reads the private legacy
snapshot envelope.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import Any

from .as1_context import ContextHost, InformationService
from .as1_contracts import ProjectAmendment, SourceLocator, relative_path
from .config import ProjectControlConfig
from .registry import WorkspaceRegistry
from .project_metadata import ProjectMetadataStore
from .security import is_denied


_PAGE_SIZE = 200
_MAX_AUTHORITY_PAGES = 24
_MAX_LEGACY_CONTEXT_ROWS = 5000


class ControlService:
    """Host-scoped context adapter backed by the standalone public API."""

    def __init__(self, config: ProjectControlConfig, host: ContextHost, *,
                 information_service: InformationService | None = None,
                 coder_claim_provider: Callable[[str, str], str] | None = None,
                 metadata_store: ProjectMetadataStore | None = None):
        self.config, self.host = config, host
        self.registry = WorkspaceRegistry(config)
        self.information_service = information_service
        self.coder_claim_provider = coder_claim_provider
        self.metadata_store = metadata_store or ProjectMetadataStore()
        self._workspace_identity_cache: dict[str, set[str]] = {}
        self.workflow = None
        if information_service is not None and information_service.host != host:
            raise ValueError("information_host_mismatch")

    def _access(self, project: str) -> None:
        if project not in self.host.projects:
            raise PermissionError("project_not_permitted")
        self.registry.workspace(project)

    def information(self, tool: str, *, project: str, **params):
        self._access(project)
        if self.information_service is None:
            return {"status": "unavailable", "reason": "information_port_unavailable"}
        return self.information_service.call(tool, project=project, **params)

    def _metadata_scope(self, project: str) -> str:
        workspace = self.registry.workspace(project)
        roots = {alias: str(repository.root.resolve())
                 for alias, repository in sorted(workspace.repositories.items())}
        return hashlib.sha256(json.dumps({"workspace": project, "roots": roots}, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()

    def _workspace_identities(self, project: str) -> set[str]:
        cached = self._workspace_identity_cache.get(project)
        if cached is not None:
            return cached
        workspace = self.registry.workspace(project)
        values = {project}
        for alias, repository in workspace.repositories.items():
            values.update({alias, repository.root.name, str(repository.root)})
        # Native IDs are exposed by the Todo public read API. They are useful
        # for typed cross-workspace relations and old source locators, but are
        # never accepted as workspace selectors themselves.
        if self.workflow is not None:
            for alias in sorted(workspace.repositories):
                try:
                    response = self.workflow.invoke("todo_read_authority", project=project,
                                                    repository=alias, limit=1, page_token=None)
                    data = self._response_data(response, "native_todo_read")
                    if isinstance(data.get("project_id"), str):
                        values.add(data["project_id"])
                    if isinstance(data.get("legacy_source_project_id"), str):
                        values.add(data["legacy_source_project_id"])
                    break
                except Exception:
                    continue
        self._workspace_identity_cache[project] = values
        return values

    def _resolve_metadata_locator(self, project: str, locator: Mapping[str, Any]):
        matches = []
        selected_project = locator.get("project")
        for candidate in sorted(self.host.projects):
            if candidate not in self.config.workspaces or selected_project not in self._workspace_identities(candidate):
                continue
            workspace = self.registry.workspace(candidate)
            alias = locator.get("repository")
            for repository_alias, repository_config in workspace.repositories.items():
                root = repository_config.root.resolve(strict=True)
                if alias in {repository_alias, str(root), root.name}:
                    matches.append((candidate, repository_alias, root))
        if len(matches) != 1:
            raise ValueError("source_project_or_repository_not_registered")
        source_project, alias, root = matches[0]
        relative = relative_path(locator.get("path"))
        deny = [*self.registry.workspace(source_project).deny_patterns]
        if is_denied(Path(relative), deny):
            raise PermissionError("source_not_permitted")
        return source_project, alias, root, relative

    def _metadata_source_access(self, project: str, locator: Mapping[str, Any], *, verify_digest: bool):
        source_project, alias, root, relative = self._resolve_metadata_locator(project, locator)
        if not verify_digest:
            return {**dict(locator), "project": source_project, "repository": alias, "path": relative}
        try:
            raw = InformationService._working_bytes(root, relative)
        except (OSError, ValueError) as error:
            raise ValueError("source_prerequisite_unavailable") from error
        digest = hashlib.sha256(raw).hexdigest()
        if digest != locator.get("content_sha256"):
            raise ValueError("source_prerequisite_stale")
        return {**dict(locator), "project": source_project, "repository": alias,
                "path": relative, "content_sha256": digest}

    def _validate_metadata_amendment(self, request: Mapping[str, Any]) -> dict[str, Any]:
        parsed = ProjectAmendment.model_validate(request).model_dump(mode="json", exclude_none=True)
        from .as1_packets import mask_payload
        _cleaned, private_fields = mask_payload(parsed)
        if private_fields:
            raise ValueError("project_amendment_private_fields_rejected")
        if parsed["project"] not in self.host.projects:
            raise PermissionError("project_not_permitted")
        self.registry.workspace(parsed["project"])
        payload = parsed["payload"]
        if not isinstance(payload.get("id"), str) or not payload["id"].strip() or len(payload["id"]) > 256:
            raise ValueError("project_amendment_id_invalid")
        action = parsed["action"]
        allowed = {
            "register_identity": {"id", "repository", "version", "anchors"},
            "register_relation": {"id", "source", "target", "relation", "anchors"},
            "register_generation": {"id", "roots", "inputs", "generator", "anchors"},
            "configure_provider": {"id", "provider_id", "options", "anchors"},
            "remove_registration": {"id", "kind", "version", "anchors"},
            "record_skill_use": {"id", "skill", "reason", "status", "anchors"},
            "update_orientation": {"id", "fields", "anchors", "field_anchors"},
        }[action]
        if payload.keys() - allowed:
            raise ValueError("project_amendment_payload_fields_invalid")
        if action == "register_identity":
            if not isinstance(payload.get("repository"), str) or not isinstance(payload.get("version"), str) or not payload["version"]:
                raise ValueError("identity_repository_and_version_required")
            self.registry.repository(parsed["project"], payload["repository"])
        elif action == "register_relation":
            if not isinstance(payload.get("relation"), str) or not payload["relation"]:
                raise ValueError("relation_type_required")
            for key in ("source", "target"):
                ref = payload.get(key)
                if not isinstance(ref, Mapping) or not all(isinstance(ref.get(field), str) and ref[field]
                                                            for field in ("project_uuid", "repository", "kind", "id")):
                    raise ValueError("typed_relation_endpoints_required")
                matches = [candidate for candidate in self.host.projects
                           if candidate in self.config.workspaces
                           if ref["project_uuid"] in self._workspace_identities(candidate)]
                if len(matches) != 1:
                    raise ValueError("relation_project_identity_unavailable")
                self.registry.repository(matches[0], ref["repository"])
        elif action == "register_generation":
            roots, inputs = payload.get("roots"), payload.get("inputs")
            if not isinstance(roots, list) or not roots or not isinstance(inputs, list) or not payload.get("generator"):
                raise ValueError("generation_roots_inputs_and_provenance_required")
            for root in roots:
                relative_path(root)
        elif action == "configure_provider":
            provider = payload.get("provider_id")
            trusted = (self.registry.workspace(parsed["project"]).project_providers.get(provider)
                       if isinstance(provider, str) else None)
            if not isinstance(trusted, Mapping) or not isinstance(payload.get("options", {}), Mapping):
                raise ValueError("untrusted_provider")
            allowed_options = trusted.get("allowed_options", {})
            for key, value in payload.get("options", {}).items():
                if key not in allowed_options or value not in allowed_options[key]:
                    raise ValueError("untrusted_provider_option")
        elif action == "remove_registration":
            if payload.get("kind") not in {"identity", "relation", "generation", "provider", "skill_use", "orientation"} or type(payload.get("version")) is not int or payload["version"] < 1:
                raise ValueError("exact_registration_kind_version_required")
        elif action == "record_skill_use":
            if not all(isinstance(payload.get(key), str) and payload[key]
                       for key in ("skill", "reason")) or payload.get("status") not in {"consulted", "applied"}:
                raise ValueError("skill_use_fields_invalid")
            if not isinstance(payload.get("anchors", []), list) or not payload.get("anchors"):
                raise ValueError("skill_use_source_anchors_required")
        elif action == "update_orientation":
            fields = payload.get("fields")
            field_anchors = payload.get("field_anchors", {})
            if not isinstance(fields, Mapping) or not fields or not isinstance(field_anchors, Mapping) or field_anchors.keys() - fields.keys():
                raise ValueError("orientation_fields_invalid")
            if not isinstance(payload.get("anchors", []), list) or not payload.get("anchors"):
                raise ValueError("orientation_source_anchors_required")
            if any(not isinstance(items, list) or not items for items in field_anchors.values()):
                raise ValueError("orientation_field_anchors_invalid")
        # Check root admission and canonicalize source aliases before hashing;
        # content hashes are rechecked inside the store transaction.
        payload.setdefault("anchors", [])
        anchor_lists = [payload["anchors"]]
        if action == "register_generation":
            anchor_lists.append(payload["inputs"])
        if action == "update_orientation":
            anchor_lists.extend(payload.get("field_anchors", {}).values())
        for locators in anchor_lists:
            if not isinstance(locators, list):
                raise ValueError("source_anchors_invalid")
            normalized = []
            for locator in locators:
                item = SourceLocator.model_validate(locator).model_dump(mode="json", exclude_none=True)
                normalized.append(self._metadata_source_access(parsed["project"], item, verify_digest=False))
            # Keep field-specific and general source lists canonical and trusted.
            if locators is payload.get("anchors"):
                payload["anchors"] = normalized
            elif action == "register_generation" and locators is payload.get("inputs"):
                payload["inputs"] = normalized
        if action == "update_orientation":
            for field, locators in payload.get("field_anchors", {}).items():
                payload["field_anchors"][field] = [self._metadata_source_access(
                    parsed["project"], SourceLocator.model_validate(locator).model_dump(mode="json", exclude_none=True),
                    verify_digest=False) for locator in locators]
        if action in {"update_orientation", "record_skill_use"} and not payload.get("anchors"):
            raise ValueError("source_anchors_required")
        if action == "register_generation":
            payload["roots"] = [relative_path(root) for root in payload["roots"]]
        if action == "remove_registration":
            # The exact version is resolved transactionally by the store.
            pass
        return parsed

    def amend_project(self, request: Mapping[str, Any] | ProjectAmendment) -> dict[str, Any]:
        """Apply a mutator-only PC metadata amendment with source-bound CAS."""
        if self.host.profile != "mutator":
            raise PermissionError("mutator_profile_required")
        parsed = self._validate_metadata_amendment(
            request.model_dump(mode="json", exclude_none=True) if isinstance(request, ProjectAmendment) else request)
        project = parsed["project"]
        providers = self.registry.workspace(project).project_providers

        def verify_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
            locators = [*payload.get("anchors", []), *payload.get("inputs", [])]
            for values in payload.get("field_anchors", {}).values():
                locators.extend(values)
            return [self._metadata_source_access(project, locator, verify_digest=True)
                    for locator in locators]

        return self.metadata_store.amend(parsed, scope_digest=self._metadata_scope(project),
                                         validate_sources=verify_sources,
                                         provider_allowlist=providers,
                                         project_identities=self._workspace_identities(project))

    @staticmethod
    def _response_data(response: object, operation: str) -> Mapping[str, Any]:
        if not isinstance(response, Mapping) or response.get("status") != "ok":
            reason = response.get("reason") if isinstance(response, Mapping) else None
            raise RuntimeError(str(reason or f"{operation}_unavailable"))
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise RuntimeError(f"{operation}_invalid")
        return data

    def _native_authority(self, project: str, repository: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        sections: dict[str, list[dict[str, Any]]] = {}
        metadata: dict[str, Any] = {}
        page_token = None
        pages = 0
        complete = False
        reason = None
        section_counts: dict[str, int] = {}
        while pages < _MAX_AUTHORITY_PAGES:
            response = self.workflow.invoke(
                "todo_read_authority", project=project, repository=repository,
                limit=_PAGE_SIZE, page_token=page_token,
            )
            try:
                data = self._response_data(response, "native_todo_read")
            except RuntimeError as error:
                reason = str(error)
                break
            identity = (data.get("project_id"), data.get("authority_id"), data.get("semantic_revision"))
            if pages and identity != (metadata.get("project_id"), metadata.get("authority_id"),
                                      metadata.get("semantic_revision")):
                reason = "native_authority_changed_during_paging"
                break
            if not pages:
                metadata = {key: data.get(key) for key in (
                    "project_id", "authority_id", "semantic_revision", "accepted_plan_revision_id",
                )}
            page_sections = data.get("sections")
            page_info = data.get("page")
            if not isinstance(page_sections, Mapping) or not isinstance(page_info, Mapping):
                reason = "native_authority_page_invalid"
                break
            advertised_counts = data.get("section_counts")
            if isinstance(advertised_counts, Mapping):
                section_counts.update({key: value for key, value in advertised_counts.items()
                                       if isinstance(key, str) and type(value) is int and value >= 0})
            for name, rows in page_sections.items():
                if isinstance(name, str) and isinstance(rows, list):
                    target = sections.setdefault(name, [])
                    for row in rows:
                        if isinstance(row, Mapping):
                            target.append({**row, "repository_alias": repository})
            pages += 1
            page_token = page_info.get("next_page_token")
            if not page_token:
                complete = page_info.get("complete") is True
                if not complete:
                    reason = "native_authority_incomplete"
                break
        if page_token and not reason:
            reason = "native_authority_page_limit"
        omissions = [
            {"section": name, "omitted_count": count - len(sections.get(name, []))}
            for name, count in sorted(section_counts.items())
            if count > len(sections.get(name, []))
        ]
        if not pages and reason:
            return None, {"repository": repository, "complete": False, "reason": reason,
                          "semantic_revision": None, "section_counts": section_counts,
                          "omissions": omissions}
        metadata["sections"] = sections
        metadata["page"] = {"pages": pages, "complete": complete, "next_page_token": page_token,
                             "section_counts": section_counts, "omissions": omissions,
                             **({"reason": reason} if reason else {})}
        return metadata, {
            "repository": repository, "project_id": metadata.get("project_id"),
            "authority_id": metadata.get("authority_id"),
            "semantic_revision": metadata.get("semantic_revision"),
            "complete": complete, "pages": pages, "section_counts": section_counts,
            "omissions": omissions, "next_page_token": page_token,
            **({"reason": reason} if reason else {}),
        }

    @staticmethod
    def _legacy_page_payload(document: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = document.get("payload")
        if isinstance(payload, Mapping):
            return payload
        # Accept the direct page shape for injected host test seams while the
        # production session always supplies its versioned public document.
        if isinstance(document.get("records"), list):
            return document
        raise RuntimeError("legacy_project_context_page_invalid")

    def _legacy_project_context(self, project: str, repository: str):
        latest: dict[tuple[str, str, str, str], tuple[tuple[int, int, int], dict[str, Any]]] = {}
        amendments: list[dict[str, Any]] = []
        invalidations: list[dict[str, Any]] = []
        page_cursor = None
        rows_seen = 0
        pages = 0
        complete = False
        reason = None
        while pages < (_MAX_LEGACY_CONTEXT_ROWS // _PAGE_SIZE):
            response = self.workflow.invoke(
                "todo_read_legacy_project_context", project=project, repository=repository,
                limit=_PAGE_SIZE, after=page_cursor,
            )
            try:
                document = self._response_data(response, "legacy_project_context_read")
                page = self._legacy_page_payload(document)
            except RuntimeError as error:
                reason = str(error)
                break
            records = page.get("records")
            if not isinstance(records, list):
                reason = "legacy_project_context_records_invalid"
                break
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                rows_seen += 1
                table = record.get("source_table")
                row = record.get("row")
                if not isinstance(row, Mapping):
                    row = {}
                origin = {
                    key: record.get(key) for key in (
                        "import_id", "source_system", "source_project_id", "source_schema_version",
                        "source_authority_revision", "source_digest", "source_project_name",
                        "source_repository_id", "import_semantic_revision", "imported_at",
                    )
                }
                selected = {"repository_alias": repository, "provenance": origin,
                            "source_table": table, "source_id": record.get("source_id"),
                            "source_entity_id": record.get("source_entity_id"),
                            "row_revision": record.get("row_revision"), "row": dict(row)}
                if table == "project_declarations":
                    kind, identity = row.get("kind"), row.get("id")
                    if not isinstance(kind, str) or not isinstance(identity, str):
                        continue
                    payload = record.get("payload")
                    if not isinstance(payload, Mapping):
                        raw = row.get("payload_json")
                        try:
                            payload = json.loads(raw) if isinstance(raw, str) else {}
                        except (TypeError, ValueError):
                            payload = {}
                    if not isinstance(payload, Mapping):
                        payload = {}
                    version = row.get("version", 0)
                    row_revision = record.get("row_revision", row.get("revision", 0))
                    import_revision = record.get("import_semantic_revision", 0)
                    rank = tuple(value if type(value) is int and value >= 0 else 0
                                 for value in (version, row_revision, import_revision))
                    key = (repository, str(origin.get("import_id")), kind, identity)
                    declaration = {
                        "id": identity, "kind": kind, "version": version,
                        "retired": bool(row.get("retired", False)), "payload": dict(payload),
                        "task_id": row.get("task_id"), "created_at": row.get("created_at"),
                        "revision": row_revision, "repository_alias": repository,
                        "provenance": origin,
                    }
                    previous = latest.get(key)
                    if previous is None or rank > previous[0]:
                        latest[key] = (rank, declaration)
                elif table == "project_amendments":
                    amendments.append(selected)
                elif table == "project_fragment_invalidations":
                    invalidations.append(selected)
            pages += 1
            page_cursor = page.get("next_cursor")
            if not page_cursor:
                complete = True
                break
            if rows_seen >= _MAX_LEGACY_CONTEXT_ROWS:
                reason = "legacy_project_context_row_limit"
                break
        if page_cursor and not reason:
            reason = "legacy_project_context_page_limit"
        return ([item[1] for item in latest.values()], amendments, invalidations,
                {"repository": repository, "pages": pages, "rows": rows_seen,
                 "complete": complete, **({"reason": reason} if reason else {})})

    def _freshness(self, project: str, declaration: Mapping[str, Any], field: str) -> dict[str, Any]:
        payload = declaration.get("payload")
        if not isinstance(payload, Mapping):
            return {"status": "unavailable", "sources": []}
        values = payload.get("field_anchors", {}).get(field) if isinstance(payload.get("field_anchors"), Mapping) else None
        locators = values if isinstance(values, list) else payload.get("anchors", [])
        if not isinstance(locators, list) or not locators:
            return {"status": "unavailable", "sources": []}
        repository = declaration.get("repository_alias")
        origin = declaration.get("provenance", {})
        try:
            local_registered = self.registry.repository(project, repository)
        except Exception:
            local_registered = None
        local_repository_matches = []
        if local_registered is not None:
            for alias in self.registry.workspace(project).repositories:
                try:
                    candidate = self.registry.repository(project, alias)
                except Exception:
                    continue
                repository_ids = {candidate.alias, str(candidate.root), candidate.root.name}
                if alias == repository and origin.get("source_repository_id"):
                    repository_ids.add(origin["source_repository_id"])
                local_repository_matches.append((candidate, repository_ids))
        statuses = []
        for locator in locators:
            if not isinstance(locator, Mapping):
                statuses.append({"source": {}, "status": "unavailable"})
                continue
            original = dict(locator)
            status = "unavailable"
            source_workspace = None
            source_alias = None
            source_root = None
            local_ids = {
                origin.get("source_project_id"), origin.get("source_project_name"), project,
            }
            if original.get("project") in local_ids and local_registered is not None:
                matches = [candidate for candidate, identifiers in local_repository_matches
                           if original.get("repository") in identifiers]
                if len(matches) == 1:
                    candidate = matches[0]
                    source_workspace, source_alias, source_root = project, candidate.alias, candidate.root
            elif isinstance(original.get("project"), str) and original.get("project") in self.host.projects:
                foreign_matches = []
                for alias in self.registry.workspace(original["project"]).repositories:
                    try:
                        foreign = self.registry.repository(original["project"], alias)
                    except Exception:
                        continue
                    if original.get("repository") in {alias, str(foreign.root), foreign.root.name}:
                        foreign_matches.append(foreign)
                if len(foreign_matches) == 1:
                    foreign = foreign_matches[0]
                    source_workspace, source_alias, source_root = original["project"], foreign.alias, foreign.root
            if source_workspace is not None and source_root is not None:
                try:
                    relative = relative_path(str(original["path"]))
                    raw = InformationService._working_bytes(source_root, relative)
                    digest = original.get("content_sha256")
                    status = "fresh" if isinstance(digest, str) and hashlib.sha256(raw).hexdigest() == digest else "stale"
                except (KeyError, OSError, ValueError):
                    status = "unavailable"
            normalized = {**original, "project": source_workspace or original.get("project"),
                          "repository": source_alias or original.get("repository")}
            statuses.append({"source": normalized, "status": status})
        state = ("stale" if any(item["status"] == "stale" for item in statuses) else
                 "unavailable" if any(item["status"] == "unavailable" for item in statuses) else "fresh")
        return {"status": state, "sources": statuses}

    def project_context(self, project: str) -> dict[str, Any]:
        """Expose native Todo facts, PC metadata, and preserved imports.

        Native authority sections are read through versioned bounded pages.
        Imported metadata comes from the public migration DTO, while current
        Project Control amendments are folded from its separate host-owned
        metadata log. Source anchors are checked against registered roots.
        """
        self._access(project)
        workspace = self.registry.workspace(project)
        failures = []
        if self.workflow is None:
            failures.append("native_todo_context_unavailable")
        authority_alias = workspace.authority_repository
        if authority_alias is None and len(workspace.repositories) == 1:
            authority_alias = next(iter(workspace.repositories))
        if self.workflow is None:
            native, authority_scope = None, {"repository": authority_alias, "complete": False,
                                             "reason": "native_todo_context_unavailable"}
        elif authority_alias is None:
            native, authority_scope = None, {
                "repository": None, "complete": False,
                "reason": "todo_authority_repository_unconfigured",
            }
        else:
            native, authority_scope = self._native_authority(project, authority_alias)
        authority_scopes = [authority_scope]
        native_context: dict[str, Any] = {}
        if native is not None:
            native_context = native
        elif authority_scope.get("reason"):
            failures.append(authority_scope["reason"])

        legacy_declarations: list[dict[str, Any]] = []
        amendments: list[dict[str, Any]] = []
        invalidations: list[dict[str, Any]] = []
        legacy_scopes = []
        for repository in sorted(workspace.repositories):
            if self.workflow is None:
                rows, amended, invalidated, scope = [], [], [], {
                    "repository": repository, "pages": 0, "rows": 0, "complete": False,
                    "reason": "legacy_project_context_unavailable"}
            else:
                rows, amended, invalidated, scope = self._legacy_project_context(project, repository)
            legacy_declarations.extend(rows)
            amendments.extend(amended)
            invalidations.extend(invalidated)
            legacy_scopes.append(scope)
            if not scope.get("complete"):
                failures.append(scope.get("reason", "legacy_project_context_incomplete"))

        grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        facts = native_context.get("sections", {}).get("declaration_facts", [])
        for fact in facts if isinstance(facts, list) else []:
            if not isinstance(fact, Mapping):
                continue
            kind, identity, revision_id, digest = (
                fact.get("declaration_kind"), fact.get("declaration_id"),
                fact.get("plan_revision_id"), fact.get("declaration_digest"),
            )
            if not all(isinstance(item, str) and item for item in (kind, identity, revision_id, digest)):
                continue
            repository = str(fact.get("repository_alias", authority_alias))
            key = (repository, kind, identity, revision_id, digest)
            entry = grouped.setdefault(key, {
                "id": identity, "kind": kind, "plan_revision_id": revision_id,
                "declaration_digest": digest, "repository_alias": repository, "facts": [],
            })
            payload: Any = {}
            raw_payload = fact.get("payload_json")
            if isinstance(raw_payload, str):
                try:
                    payload = json.loads(raw_payload)
                except json.JSONDecodeError:
                    payload = {}
            entry["facts"].append({
                "fact_sequence": fact.get("fact_sequence"), "fact_kind": fact.get("fact_kind"),
                "payload": payload, "semantic_revision": fact.get("semantic_revision"),
                "occurred_at": fact.get("occurred_at"),
            })
        native_declarations = [grouped[key] for key in sorted(grouped)]

        metadata = self.metadata_store.read_context(project, self._metadata_scope(project))
        pc_declarations = []
        for row in [*metadata["declarations"], *metadata["orientation"], *metadata["skill_uses"]]:
            content = row.get("payload", {})
            anchors = content.get("anchors", []) if isinstance(content, Mapping) else []
            repository = content.get("repository") if isinstance(content, Mapping) else None
            if not repository and anchors and isinstance(anchors[0], Mapping):
                repository = anchors[0].get("repository")
            pc_declarations.append({
                **row, "repository_alias": repository,
                "provenance": {"source_system": "project-control", "source_project_id": project,
                               "source_authority_revision": row.get("revision"),
                               "scope_digest": self._metadata_scope(project)},
            })
        # Preserve every imported legacy identity, while a current PC-owned
        # version supersedes the same semantic kind/id across the workspace.
        # Latest retired versions are tombstones: they suppress a formerly
        # active imported declaration with the same identity in the folded
        # project view, even though tombstones are not themselves displayed.
        pc_keys = {(item["kind"], item["id"]) for item in metadata["all_declarations"]}
        active_legacy = [item for item in legacy_declarations
                         if not item.get("retired") and (item.get("kind"), item.get("id")) not in pc_keys]
        active_legacy.extend(pc_declarations)
        orientation = [item for item in active_legacy if item.get("kind") == "orientation"]
        skill_uses = [item for item in active_legacy if item.get("kind") == "skill_use"]
        declarations = native_declarations + [
            item for item in active_legacy if item.get("kind") not in {"orientation", "skill_use"}
        ]
        for entry in orientation:
            fields = entry.get("payload", {}).get("fields", {})
            if isinstance(fields, Mapping):
                entry["field_freshness"] = {
                    field: self._freshness(project, entry, field) for field in fields
                }
                if entry.get("provenance", {}).get("source_system") == "project-control" and not metadata["scope_matches"]:
                    entry["field_freshness"] = {
                        field: {"status": "stale", "sources": [],
                                "reason": "registered_repository_scope_changed"}
                        for field in fields
                    }

        revision = native_context.get("semantic_revision")
        if revision is None:
            revision = max((int(row.get("provenance", {}).get("source_authority_revision") or 0)
                            for row in legacy_declarations), default=0)
        status = "partial" if failures or (native_context and not native_context.get("page", {}).get("complete")) else "ok"
        return {
            "status": status,
            "reason": ";".join(dict.fromkeys(str(value) for value in failures)) if failures else None,
            "project_uuid": native_context.get("project_id") or next((
                row.get("provenance", {}).get("source_project_id") for row in legacy_declarations
                if row.get("provenance", {}).get("source_project_id")
            ), project),
            "authority_id": native_context.get("authority_id"),
            "project_revision": revision,
            "metadata_revision": metadata["revision"],
            "authority_scopes": authority_scopes,
            "declarations": declarations,
            "declaration_facts": list(facts) if isinstance(facts, list) else [],
            "plan_revisions": list(native_context.get("sections", {}).get("plan_revisions", [])),
            "plan_membership": list(native_context.get("sections", {}).get("plan_membership", [])),
            "work_definitions": list(native_context.get("sections", {}).get("work_definitions", [])),
            "context_findings": list(native_context.get("sections", {}).get("context_findings", [])),
            "orientation": orientation,
            "skill_uses": skill_uses,
            "legacy_amendments": amendments,
            "invalidations": invalidations + metadata["invalidations"],
            "legacy_context_scopes": legacy_scopes,
        }
