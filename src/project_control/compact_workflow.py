"""Compact Project Control workflow adapter over the standalone Todo host.

Only opaque handles are kept in the agent-facing surface.  Native identifiers,
operation keys, and recovery receipts remain in this private host journal and
are resolved against the principal/profile that received the handle.
"""

from __future__ import annotations

from collections.abc import Mapping
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import threading
import time
from typing import Any


_STORE_FORMAT = "project-control.compact-workflow-store"
_STORE_VERSION = 1
_MAX_STORE_BYTES = 32 * 1024 * 1024
_MAX_RECORDS = 4096
_MAX_PREVIEW_BYTES = 1024 * 1024
_MAX_RESULT_BYTES = 256 * 1024
_MAX_RESPONSE_BYTES = 8 * 1024
_MAX_CONTEXT_BYTES = 8192
_MAX_CONTEXT_CAPSULE_BYTES = 64 * 1024
_LEASE_RENEWAL_INTERVAL_SECONDS = 30.0


class CompactWorkflowError(RuntimeError):
    """A bounded compact workflow request could not be served safely."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bounded_mapping(value: object, field: str, limit: int) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise CompactWorkflowError(f"{field}_invalid")
    result = dict(value)
    try:
        encoded = _canonical(result)
    except (TypeError, ValueError) as error:
        raise CompactWorkflowError(f"{field}_invalid") from error
    if len(encoded) > limit:
        raise CompactWorkflowError(f"{field}_too_large")
    return result


def _safe_component(value: object, name: str, maximum: int = 256) -> str:
    if (not isinstance(value, str) or not value.strip()
            or len(value.encode("utf-8")) > maximum
            or any(ord(char) < 32 for char in value)):
        raise CompactWorkflowError(f"{name}_invalid")
    return value


def _private_directory(path: Path) -> Path:
    """Create/validate one private directory without following symlinks."""
    expanded = path.expanduser()
    absolute = Path(os.path.abspath(expanded))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise CompactWorkflowError("workflow_store_path_unsafe")
    info = absolute.stat()
    if info.st_uid != os.getuid():
        raise CompactWorkflowError("workflow_store_owner_invalid")
    os.chmod(absolute, 0o700)
    return absolute


class _PrivateWorkflowStore:
    """Small atomic JSON store scoped to the host principal and profile."""

    def __init__(self, root: Path, *, principal: str, profile: str) -> None:
        self.root = _private_directory(root)
        self.principal = _safe_component(principal, "host_principal", 256)
        self.profile = _safe_component(profile, "host_profile", 128)
        self._actor_digest = _digest(self.principal + "\0" + self.profile)
        self.path = self.root / f"{self._actor_digest}.json"
        self.lock_path = self.root / f"{self._actor_digest}.lock"

    def _lock(self):
        if self.lock_path.is_symlink():
            raise CompactWorkflowError("workflow_store_lock_unsafe")
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError as error:
            raise CompactWorkflowError("workflow_store_lock_unavailable") from error
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            os.close(fd)
            raise CompactWorkflowError("workflow_store_lock_unsafe")
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _read(self) -> dict[str, Any]:
        if self.path.is_symlink():
            raise CompactWorkflowError("workflow_store_file_unsafe")
        try:
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return {"format": _STORE_FORMAT, "version": _STORE_VERSION, "entries": []}
        except OSError as error:
            raise CompactWorkflowError("workflow_store_file_unavailable") from error
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > _MAX_STORE_BYTES):
                raise CompactWorkflowError("workflow_store_file_unsafe")
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                fd = -1
                value = json.load(stream)
        except CompactWorkflowError:
            raise
        except (OSError, ValueError) as error:
            raise CompactWorkflowError("workflow_store_file_invalid") from error
        finally:
            if fd >= 0:
                os.close(fd)
        if (not isinstance(value, dict) or set(value) != {"format", "version", "entries"}
                or value.get("format") != _STORE_FORMAT or value.get("version") != _STORE_VERSION
                or not isinstance(value.get("entries"), list)
                or len(value["entries"]) > _MAX_RECORDS):
            raise CompactWorkflowError("workflow_store_file_invalid")
        for entry in value["entries"]:
            if (not isinstance(entry, dict)
                    or set(entry) != {"kind", "identity", "identity_digest", "project", "repository", "created_at", "updated_at", "data"}
                    or not isinstance(entry.get("kind"), str)
                    or not isinstance(entry.get("identity"), str)
                    or not isinstance(entry.get("identity_digest"), str)
                    or not isinstance(entry.get("project"), str)
                    or not isinstance(entry.get("repository"), str)
                    or type(entry.get("created_at")) not in {int, float}
                    or type(entry.get("updated_at")) not in {int, float}
                    or not isinstance(entry.get("data"), dict)):
                raise CompactWorkflowError("workflow_store_file_invalid")
        return value

    def _write(self, value: dict[str, Any]) -> None:
        try:
            encoded = _canonical(value)
        except (TypeError, ValueError) as error:
            raise CompactWorkflowError("workflow_store_record_invalid") from error
        if len(encoded) > _MAX_STORE_BYTES:
            raise CompactWorkflowError("workflow_store_full")
        fd, name = tempfile.mkstemp(prefix=".compact-workflow-", dir=self.root)
        temporary = Path(name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
            directory_fd = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _identity(self, kind: str, identity: str) -> str:
        return _digest(self._actor_digest + "\0" + kind + "\0" + identity)

    def _mutate(self, fn):
        fd = self._lock()
        try:
            value = self._read()
            result, changed = fn(value)
            if changed:
                self._write(value)
            return result
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _find(self, value: dict[str, Any], *, kind: str, identity: str,
              project: str, repository: str) -> dict[str, Any] | None:
        digest = self._identity(kind, identity)
        matches = [entry for entry in value["entries"]
                   if entry["kind"] == kind and entry["identity_digest"] == digest]
        if len(matches) > 1:
            raise CompactWorkflowError("workflow_store_duplicate_identity")
        if not matches:
            return None
        entry = matches[0]
        if entry["project"] != project or entry["repository"] != repository:
            raise CompactWorkflowError("workflow_handle_scope_mismatch")
        return entry

    def create(self, kind: str, project: str, repository: str, data: Mapping[str, Any],
               *, identity: str | None = None) -> str:
        opaque = identity or secrets.token_urlsafe(32)
        record_data = _bounded_mapping(data, "workflow_record", _MAX_PREVIEW_BYTES)
        timestamp = time.time()

        def operation(value):
            if len(value["entries"]) >= _MAX_RECORDS:
                raise CompactWorkflowError("workflow_store_full")
            digest = self._identity(kind, opaque)
            if any(entry["identity_digest"] == digest for entry in value["entries"]):
                raise CompactWorkflowError("workflow_store_identity_conflict")
            value["entries"].append({
                "kind": kind, "identity": opaque, "identity_digest": digest, "project": project,
                "repository": repository, "created_at": timestamp, "updated_at": timestamp,
                "data": record_data,
            })
            return opaque, True

        return self._mutate(operation)

    def get(self, kind: str, opaque: str, project: str, repository: str) -> dict[str, Any]:
        opaque = _safe_component(opaque, "workflow_handle", 256)
        fd = self._lock()
        try:
            value = self._read()
            entry = self._find(value, kind=kind, identity=opaque,
                               project=project, repository=repository)
            if entry is None:
                raise CompactWorkflowError("workflow_handle_unavailable")
            return dict(entry["data"])
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def get_any_scope(self, kind: str, opaque: str) -> tuple[str, str, dict[str, Any]]:
        """Resolve a random opaque handle to its host-private project scope."""
        opaque = _safe_component(opaque, "workflow_handle", 256)
        digest = self._identity(kind, opaque)
        fd = self._lock()
        try:
            value = self._read()
            rows = [entry for entry in value["entries"]
                    if entry["kind"] == kind and entry["identity_digest"] == digest]
            if len(rows) > 1:
                raise CompactWorkflowError("workflow_store_duplicate_identity")
            if not rows:
                raise CompactWorkflowError("workflow_handle_unavailable")
            entry = rows[0]
            return entry["project"], entry["repository"], dict(entry["data"])
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def update(self, kind: str, opaque: str, project: str, repository: str,
               changes: Mapping[str, Any]) -> dict[str, Any]:
        update = _bounded_mapping(changes, "workflow_record", _MAX_PREVIEW_BYTES)
        timestamp = time.time()

        def operation(value):
            entry = self._find(value, kind=kind, identity=opaque,
                               project=project, repository=repository)
            if entry is None:
                raise CompactWorkflowError("workflow_handle_unavailable")
            entry["data"].update(update)
            entry["updated_at"] = timestamp
            return dict(entry["data"]), True

        return self._mutate(operation)

    def list(self, kind: str, project: str, repository: str) -> list[dict[str, Any]]:
        fd = self._lock()
        try:
            value = self._read()
            return [dict(entry["data"]) for entry in value["entries"]
                    if entry["kind"] == kind and entry["project"] == project
                    and entry["repository"] == repository]
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def find_current_workflow(self, project: str, repository: str,
                              requested: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
        request = _bounded_mapping(requested, "workflow_request", 4096)
        pointer_identity = "current-" + _digest(project + "\0" + repository)[:40]
        pointer_digest = self._identity("workflow_current", pointer_identity)
        active_phases = {"selecting", "workspace_pending", "dispatch_pending", "context_pending",
                         "active", "finishing", "handback_pending", "report_pending",
                         "release_pending"}
        fd = self._lock()
        try:
            value = self._read()
            pointers = [entry for entry in value["entries"]
                        if entry["kind"] == "workflow_current"
                        and entry["identity_digest"] == pointer_digest]
            if not pointers:
                return None
            if len(pointers) != 1 or pointers[0]["project"] != project or pointers[0]["repository"] != repository:
                raise CompactWorkflowError("workflow_store_current_invalid")
            handle = pointers[0]["data"].get("workflow_handle")
            if not isinstance(handle, str):
                raise CompactWorkflowError("workflow_store_current_invalid")
            digest = self._identity("workflow", handle)
            rows = [entry for entry in value["entries"]
                    if entry["kind"] == "workflow" and entry["identity_digest"] == digest]
            if len(rows) != 1:
                raise CompactWorkflowError("workflow_store_current_invalid")
            record = rows[0]["data"]
            if record.get("phase") not in active_phases:
                return None
            if record.get("phase") == "selecting":
                if record.get("request") != request:
                    raise CompactWorkflowError("workflow_task_already_active")
            elif ((request.get("task_id") is not None and request.get("task_id") != record.get("task_id"))
                  or (request.get("run_id") is not None and request.get("run_id") != record.get("run_id"))):
                raise CompactWorkflowError("workflow_task_already_active")
            return handle, dict(record)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def reserve_review(self, project: str, repository: str,
                       candidate: Mapping[str, Any]) -> tuple[str, dict[str, Any], bool]:
        data = _bounded_mapping(candidate, "review_workflow", 4096)
        candidate_id = data.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise CompactWorkflowError("review_candidate_invalid")
        timestamp = time.time()

        def operation(value):
            entries = value["entries"]
            existing = [entry for entry in entries if entry["kind"] == "workflow"
                        and entry["project"] == project and entry["repository"] == repository
                        and entry["data"].get("mode") == "review"
                        and entry["data"].get("candidate_id") == candidate_id
                        and entry["data"].get("phase") in {"review_pending", "review_accepted"}]
            if len(existing) > 1:
                raise CompactWorkflowError("workflow_store_duplicate_review")
            if existing:
                return (existing[0]["identity"], dict(existing[0]["data"]), False), False
            if len(entries) >= _MAX_RECORDS:
                raise CompactWorkflowError("workflow_store_full")
            handle = secrets.token_urlsafe(32)
            data.update(mode="review", phase="review_pending", created_at=timestamp,
                        project=project, repository=repository)
            entries.append({
                "kind": "workflow", "identity": handle,
                "identity_digest": self._identity("workflow", handle),
                "project": project, "repository": repository,
                "created_at": timestamp, "updated_at": timestamp, "data": data,
            })
            return (handle, dict(data), True), True

        return self._mutate(operation)

    def reserve_workflow(self, project: str, repository: str,
                         requested: Mapping[str, Any]) -> tuple[str, dict[str, Any], bool]:
        """Atomically resume or reserve the one authoring workflow for a scope."""
        request = _bounded_mapping(requested, "workflow_request", 4096)
        timestamp = time.time()
        pointer_identity = "current-" + _digest(project + "\0" + repository)[:40]
        pointer_digest = self._identity("workflow_current", pointer_identity)
        active_phases = {
            "selecting", "workspace_pending", "dispatch_pending", "context_pending",
            "active", "finishing", "handback_pending", "report_pending", "release_pending",
        }

        def operation(value):
            entries = value["entries"]
            pointer_rows = [entry for entry in entries
                            if entry["kind"] == "workflow_current"
                            and entry["identity_digest"] == pointer_digest]
            if len(pointer_rows) > 1:
                raise CompactWorkflowError("workflow_store_duplicate_current")
            if pointer_rows:
                pointer = pointer_rows[0]
                if pointer["project"] != project or pointer["repository"] != repository:
                    raise CompactWorkflowError("workflow_handle_scope_mismatch")
                handle = pointer["data"].get("workflow_handle")
                workflow_digest = self._identity("workflow", handle) if isinstance(handle, str) else None
                rows = [entry for entry in entries if entry["kind"] == "workflow"
                        and entry["identity_digest"] == workflow_digest]
                if len(rows) > 1:
                    raise CompactWorkflowError("workflow_store_duplicate_identity")
                if rows and rows[0]["project"] == project and rows[0]["repository"] == repository:
                    record = rows[0]["data"]
                    if record.get("phase") in active_phases:
                        if record.get("phase") == "selecting":
                            if record.get("request") != request:
                                raise CompactWorkflowError("workflow_task_already_active")
                        elif ((request.get("task_id") is not None
                               and request.get("task_id") != record.get("task_id"))
                              or (request.get("run_id") is not None
                                  and request.get("run_id") != record.get("run_id"))):
                            raise CompactWorkflowError("workflow_task_already_active")
                        return (handle, dict(record), False), False

            if len(entries) + (0 if pointer_rows else 1) >= _MAX_RECORDS:
                raise CompactWorkflowError("workflow_store_full")
            handle = secrets.token_urlsafe(32)
            data = {"phase": "selecting", "request": request, "project": project,
                    "repository": repository, "created_at": timestamp}
            entries.append({
                "kind": "workflow", "identity": handle,
                "identity_digest": self._identity("workflow", handle),
                "project": project, "repository": repository,
                "created_at": timestamp, "updated_at": timestamp, "data": data,
            })
            pointer_data = {"workflow_handle": handle}
            if pointer_rows:
                pointer_rows[0]["data"] = pointer_data
                pointer_rows[0]["updated_at"] = timestamp
            else:
                entries.append({
                    "kind": "workflow_current", "identity": pointer_identity,
                    "identity_digest": pointer_digest, "project": project,
                    "repository": repository, "created_at": timestamp,
                    "updated_at": timestamp, "data": pointer_data,
                })
            return (handle, dict(data), True), True

        return self._mutate(operation)

    def atomic_update(self, kind: str, opaque: str, project: str, repository: str,
                      fn) -> dict[str, Any]:
        """Apply a compare/update callback while holding the durable journal lock."""
        timestamp = time.time()

        def operation(value):
            entry = self._find(value, kind=kind, identity=opaque,
                               project=project, repository=repository)
            if entry is None:
                raise CompactWorkflowError("workflow_handle_unavailable")
            changed = fn(entry["data"])
            if changed:
                entry["updated_at"] = timestamp
            return dict(entry["data"]), bool(changed)

        return self._mutate(operation)


class CompactWorkflowHost:
    """Restore the four-tool workflow over the standalone native host.

    The constructor is supplied the already trusted, profile-bound native host.
    The public methods accept registered workspace names only; follow-up calls
    use the opaque handle's private mapping for project and repository scope.
    """

    def __init__(self, native_host: object, *, state_directory: Path | None = None) -> None:
        self.native_host = native_host
        principal = getattr(native_host.host, "principal", None)
        profile = getattr(native_host.host, "profile", None)
        if not isinstance(profile, str):
            profile = getattr(profile, "value", None)
        if not isinstance(principal, str) or not isinstance(profile, str):
            raise CompactWorkflowError("workflow_host_identity_unavailable")
        if state_directory is None:
            from .project_metadata import metadata_state_root

            state_directory = metadata_state_root() / "compact-workflow"
        self._store = _PrivateWorkflowStore(Path(state_directory), principal=principal, profile=profile)
        self.principal = principal
        self.profile = profile
        self._renewal_lock = threading.RLock()
        self._renewal_workers: dict[str, tuple[threading.Event, threading.Thread]] = {}

    @staticmethod
    def _attention(reason: str, *, workflow_handle: str | None = None,
                   allowed_actions: list[str] | None = None,
                   recommended_next_call: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "attention_required", "reason": reason,
            "allowed_actions": allowed_actions or [],
            "recommended_next_call": recommended_next_call,
        }
        if workflow_handle is not None:
            result["workflow_handle"] = workflow_handle
        return result

    @staticmethod
    def _value(value: object) -> object:
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        if isinstance(value, Mapping):
            return dict(value)
        return value

    @classmethod
    def _mapping(cls, value: object, field: str) -> dict[str, Any]:
        normalized = cls._value(value)
        if not isinstance(normalized, Mapping) or any(not isinstance(k, str) for k in normalized):
            raise CompactWorkflowError(f"{field}_invalid")
        try:
            return _bounded_mapping(normalized, field, _MAX_PREVIEW_BYTES)
        except CompactWorkflowError:
            raise

    def _invoke(self, tool: str, record: Mapping[str, Any], **arguments: Any) -> dict[str, Any]:
        project = record.get("project")
        repository = record.get("repository")
        if not isinstance(project, str) or not isinstance(repository, str):
            raise CompactWorkflowError("workflow_scope_invalid")
        result = self.native_host.invoke(tool, project=project, repository=repository,
                                         **arguments)
        if not isinstance(result, Mapping):
            raise CompactWorkflowError("native_result_invalid")
        if result.get("status") != "ok":
            return dict(result)
        data = result.get("data")
        if not isinstance(data, Mapping):
            raise CompactWorkflowError("native_result_invalid")
        return dict(data)

    def _scope(self, project: str, repository: str | None) -> tuple[str, str]:
        project = _safe_component(project, "project", 128)
        if repository is not None:
            repository = _safe_component(repository, "repository", 128)
        resolver = getattr(self.native_host, "_project_root", None)
        if not callable(resolver):
            raise CompactWorkflowError("registered_workspace_resolver_unavailable")
        try:
            _root, selected_repository = resolver(project, repository)
        except Exception as error:
            code = getattr(error, "code", None)
            raise CompactWorkflowError(code if isinstance(code, str) else "project_or_authority_unavailable") from error
        return project, _safe_component(selected_repository, "repository", 128)

    @staticmethod
    def _operation_key(handle: str, operation: str, payload: Mapping[str, Any] | None = None) -> str:
        body = _canonical(payload or {})
        digest = hashlib.sha256(handle.encode("utf-8") + b"\0" + operation.encode("utf-8") + b"\0" + body).hexdigest()
        return "pcw-" + digest[:44]

    @staticmethod
    def _bounded_response(value: Mapping[str, Any], *, limit: int = _MAX_RESPONSE_BYTES) -> dict[str, Any]:
        response = _bounded_mapping(value, "workflow_response", limit)
        return response

    def _handle(self, workflow_handle: str) -> tuple[str, str, dict[str, Any]]:
        project, repository, data = self._store.get_any_scope("workflow", workflow_handle)
        return project, repository, data

    def _update(self, workflow_handle: str, project: str, repository: str,
                changes: Mapping[str, Any]) -> dict[str, Any]:
        return self._store.update("workflow", workflow_handle, project, repository, changes)

    def _phase_intent(
        self, workflow_handle: str, project: str, repository: str,
        record: dict[str, Any], *, name: str, tool: str,
        arguments: Mapping[str, Any], expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[str, int]:
        """Persist one exact mutation request before sending it to native Todo.

        The native idempotency digest includes ``expected_revision``. Capturing
        it with the key prevents a lost response from changing the replay body
        after the authority revision advances.
        """
        payload = _bounded_mapping({"tool": tool, "arguments": dict(arguments)},
                                   "native_phase_intent", 32 * 1024)
        payload_digest = _digest(_canonical(payload).decode("utf-8"))
        latest = self._store.get("workflow", workflow_handle, project, repository)
        intents = latest.get("phase_intents", {})
        if not isinstance(intents, Mapping):
            raise CompactWorkflowError("workflow_phase_intents_invalid")
        prior = intents.get(name)
        if prior is not None:
            if (not isinstance(prior, Mapping) or prior.get("payload_digest") != payload_digest
                    or type(prior.get("expected_revision")) is not int
                    or not isinstance(prior.get("idempotency_key"), str)):
                raise CompactWorkflowError("workflow_phase_intent_conflict")
            record.update(latest)
            return prior["idempotency_key"], prior["expected_revision"]

        if expected_revision is None:
            observed = self._invoke("todo_read_authority", {
                "project": project, "repository": repository,
            }, limit=1, page_token=None, expected_revision=None)
            if observed.get("status") == "attention_required":
                raise CompactWorkflowError(str(observed.get("reason", "authority_revision_unavailable")))
            expected_revision = observed.get("semantic_revision")
        if type(expected_revision) is not int or expected_revision < 0:
            raise CompactWorkflowError("authority_revision_unavailable")
        key = idempotency_key or self._operation_key(
            workflow_handle, "native:" + name, payload,
        )
        key = _safe_component(key, "idempotency_key", 128)
        intent = {"payload_digest": payload_digest, "idempotency_key": key,
                  "expected_revision": expected_revision}

        def persist(current):
            current_intents = current.setdefault("phase_intents", {})
            if not isinstance(current_intents, dict):
                raise CompactWorkflowError("workflow_phase_intents_invalid")
            existing = current_intents.get(name)
            if existing is not None:
                if (not isinstance(existing, Mapping)
                        or existing.get("payload_digest") != payload_digest):
                    raise CompactWorkflowError("workflow_phase_intent_conflict")
                return False
            current_intents[name] = intent
            return True

        record.update(self._store.atomic_update(
            "workflow", workflow_handle, project, repository, persist,
        ))
        stored = record["phase_intents"][name]
        if (not isinstance(stored, Mapping) or stored.get("payload_digest") != payload_digest
                or type(stored.get("expected_revision")) is not int
                or not isinstance(stored.get("idempotency_key"), str)):
            raise CompactWorkflowError("workflow_phase_intent_conflict")
        return stored["idempotency_key"], stored["expected_revision"]

    def _refresh_phase_intent(self, workflow_handle: str, project: str, repository: str,
                              record: dict[str, Any], *, name: str, tool: str,
                              arguments: Mapping[str, Any]) -> None:
        """Advance a phase only after an explicit pre-commit stale-CAS reply."""
        observed = self._invoke("todo_read_authority", {
            "project": project, "repository": repository,
        }, limit=1, page_token=None, expected_revision=None)
        if observed.get("status") == "attention_required":
            raise CompactWorkflowError(str(observed.get("reason", "authority_revision_unavailable")))
        revision = observed.get("semantic_revision")
        if type(revision) is not int or revision < 0:
            raise CompactWorkflowError("authority_revision_unavailable")
        payload = {"tool": tool, "arguments": dict(arguments)}
        payload_digest = _digest(_canonical(payload).decode("utf-8"))

        def refresh(current):
            intents = current.get("phase_intents")
            if not isinstance(intents, dict) or not isinstance(intents.get(name), dict):
                raise CompactWorkflowError("workflow_phase_intent_unavailable")
            intent = intents[name]
            if intent.get("payload_digest") != payload_digest:
                raise CompactWorkflowError("workflow_phase_intent_conflict")
            generation = intent.get("generation", 0)
            if type(generation) is not int or generation < 0:
                raise CompactWorkflowError("workflow_phase_intent_invalid")
            if intent.get("expected_revision") == revision:
                return False
            intent.update(
                expected_revision=revision,
                idempotency_key=self._operation_key(
                    workflow_handle, f"native:{name}:retry:{generation + 1}", payload,
                ),
                generation=generation + 1,
            )
            return True

        record.update(self._store.atomic_update(
            "workflow", workflow_handle, project, repository, refresh,
        ))

    def _invoke_phase(
        self, workflow_handle: str, project: str, repository: str,
        record: dict[str, Any], *, name: str, tool: str,
        arguments: Mapping[str, Any], expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Invoke a durable mutation, refreshing only on a definite stale CAS."""
        for retry in range(2):
            key, revision = self._phase_intent(
                workflow_handle, project, repository, record,
                name=name, tool=tool, arguments=arguments,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
            result = self._invoke(tool, record, **dict(arguments),
                                  idempotency_key=key, expected_revision=revision)
            if (retry == 0 and result.get("status") == "attention_required"
                    and result.get("reason") == "stale_authority_revision"):
                self._refresh_phase_intent(
                    workflow_handle, project, repository, record,
                    name=name, tool=tool, arguments=arguments,
                )
                # The rejected CAS had no commit; a new exact revision and key
                # can safely retry. Unknown failures never enter this branch.
                expected_revision = None
                idempotency_key = None
                continue
            return result
        return self._attention("native_operation_rejected", workflow_handle=workflow_handle)

    @staticmethod
    def _native_attention(data: Mapping[str, Any], workflow_handle: str | None = None) -> dict[str, Any]:
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason:
            reason = "native_operation_rejected"
        return CompactWorkflowHost._attention(reason, workflow_handle=workflow_handle,
                                              allowed_actions=["inspect_task"] if workflow_handle else [])

    def record_admin_preview(
        self, project: str, repository: str, *, action: str, package_digest: str,
        authority_revision: int, preview: Mapping[str, Any],
    ) -> str:
        """Persist one exact administrator preview for later apply and replay."""
        project = _safe_component(project, "project", 128)
        repository = _safe_component(repository, "repository", 128)
        action = _safe_component(action, "admin_action", 64)
        if (not isinstance(package_digest, str) or len(package_digest) != 64
                or any(char not in "0123456789abcdef" for char in package_digest)
                or type(authority_revision) is not int or authority_revision < 0):
            raise CompactWorkflowError("admin_preview_identity_invalid")
        body = _bounded_mapping(preview, "admin_preview", _MAX_PREVIEW_BYTES)
        identity = secrets.token_urlsafe(32)
        idempotency_key = "pc-" + _digest(identity)[:48]
        # The identity is supplied as the opaque proposal reference.  A caller
        # must retain this exact reference to consume or replay the preview.
        return self._store.create(
            "admin_preview", project, repository,
            {"action": action, "package_digest": package_digest,
             "authority_revision": authority_revision, "preview": body,
             "idempotency_key": idempotency_key, "result": None},
            identity=identity,
        )

    def get_admin_preview(
        self, project: str, repository: str, *, proposal_ref: str, action: str,
        package_digest: str, authority_revision: int | None = None,
    ) -> Mapping[str, Any] | None:
        project = _safe_component(project, "project", 128)
        repository = _safe_component(repository, "repository", 128)
        try:
            record = self._store.get("admin_preview", proposal_ref, project, repository)
        except CompactWorkflowError as error:
            if error.code == "workflow_handle_unavailable":
                return None
            raise
        if (record.get("action"), record.get("package_digest")) != (action, package_digest):
            return None
        if authority_revision is not None and record.get("authority_revision") != authority_revision:
            return None
        return record

    def complete_admin_preview(
        self, project: str, repository: str, *, proposal_ref: str,
        idempotency_key: str, result: Mapping[str, Any],
    ) -> None:
        project = _safe_component(project, "project", 128)
        repository = _safe_component(repository, "repository", 128)
        key = _safe_component(idempotency_key, "idempotency_key", 128)
        receipt = _bounded_mapping(result, "admin_result", _MAX_RESULT_BYTES)
        def complete(record):
            if record.get("idempotency_key") != key:
                raise CompactWorkflowError("admin_preview_idempotency_mismatch")
            if record.get("result") is not None:
                if _canonical(record["result"]) != _canonical(receipt):
                    raise CompactWorkflowError("admin_preview_result_conflict")
                return False
            record["result"] = receipt
            return True

        self._store.atomic_update("admin_preview", proposal_ref, project, repository, complete)

    def next_task(
        self, *, project: str, repository: str | None = None,
        task_id: str | None = None, run_id: str | None = None,
    ) -> dict[str, Any]:
        """Claim or resume one native eligible assignment and return its packet."""
        try:
            selected_project, selected_repository = self._scope(project, repository)
            requested: dict[str, Any] = {}
            if task_id is not None:
                requested["task_id"] = _safe_component(task_id, "task_id", 128)
            if run_id is not None:
                requested["run_id"] = _safe_component(run_id, "run_id", 128)
            if self.profile == "mutator" and self._store.find_current_workflow(
                    selected_project, selected_repository, requested) is None:
                review = self._next_pending_review(selected_project, selected_repository, requested)
                if review is not None:
                    review_handle, review_record, _created = self._store.reserve_review(
                        selected_project, selected_repository, review,
                    )
                    return self._review_packet(review_handle, review_record)
            handle, record, is_new = self._store.reserve_workflow(
                selected_project, selected_repository, requested,
            )
            record.update(project=selected_project, repository=selected_repository)
            if record.get("phase") == "active" and isinstance(record.get("context"), Mapping):
                resumed = self._resume_active(handle, selected_project, selected_repository, record)
                if resumed.get("status") == "attention_required":
                    return resumed
                record = self._store.get("workflow", handle, selected_project, selected_repository)
                record.update(project=selected_project, repository=selected_repository)
                self._ensure_lease_renewal(handle, selected_project, selected_repository)
                return self._task_packet(handle, record, resumed=True)
            if record.get("phase") == "no_work":
                return self._bounded_response({
                    "status": "no_work", "workflow_handle": None,
                    "allowed_actions": ["next_task"], "recommended_next_call": "next_task",
                })

            if record.get("phase") == "selecting":
                choice = self._invoke("todo_select_next_work", record,
                    run_id=requested.get("run_id"), work_item_id=requested.get("task_id"))
                if choice.get("status") == "attention_required":
                    return self._native_attention(choice)
                if "selection" in choice:
                    choice = choice.get("selection")
                if choice is None:
                    def set_no_work(current):
                        if current.get("phase") != "selecting":
                            return False
                        current["phase"] = "no_work"
                        return True
                    record = self._store.atomic_update("workflow", handle, selected_project,
                                                       selected_repository, set_no_work)
                    if record.get("phase") == "no_work":
                        return self._bounded_response({
                            "status": "no_work", "workflow_handle": None,
                            "allowed_actions": ["next_task"], "recommended_next_call": "next_task",
                        })
                    # A concurrent request selected work first. Resume that
                    # exact persisted selection rather than replacing it.
                candidate = self._mapping(choice, "work_selection")
                work_item_id = self._first_text(candidate, "work_item_id", "target_id", "task_id")
                selected_run = self._optional_text(candidate, "run_id")
                assignment_id = self._optional_text(candidate, "assignment_id")
                plan_revision_id = self._first_text(candidate, "plan_revision_id", "plan_revision")
                observed_revision = candidate.get("authority_revision", candidate.get("observed_revision"))
                if type(observed_revision) is not int or observed_revision < 0:
                    raise CompactWorkflowError("work_selection_revision_invalid")
                if requested.get("task_id") is not None and requested["task_id"] != work_item_id:
                    raise CompactWorkflowError("work_selection_target_mismatch")
                if requested.get("run_id") is not None and requested["run_id"] != selected_run:
                    raise CompactWorkflowError("work_selection_run_mismatch")
                workspace_id = self._generated_id("ws", handle)
                create_key = self._operation_key(handle, "create_workspace", candidate)
                prepared = {
                    "phase": "workspace_pending", "selection": candidate,
                    "project": selected_project, "repository": selected_repository,
                    "request": requested, "created_at": record.get("created_at", time.time()),
                    "task_id": work_item_id, "run_id": selected_run,
                    "assignment_id": assignment_id, "plan_revision_id": plan_revision_id,
                    "authority_revision": observed_revision, "workspace_id": workspace_id,
                    "create_workspace_key": create_key,
                    "dispatch_key": self._operation_key(handle, "dispatch_attempt", candidate),
                }
                def save_selection(current):
                    if current.get("phase") != "selecting":
                        return False
                    current.update(prepared)
                    return True
                record = self._store.atomic_update("workflow", handle, selected_project,
                                                   selected_repository, save_selection)
                if record.get("phase") == "selecting":
                    # The callback can only leave the phase unchanged if the
                    # store mutation failed to win a concurrent selection.
                    return self._attention("workflow_selection_conflict", workflow_handle=handle)

            if record.get("phase") == "workspace_pending":
                workspace_args = {
                    "work_item_id": record["task_id"], "run_id": record.get("run_id"),
                    "assignment_id": record["assignment_id"],
                    "plan_revision_id": record["plan_revision_id"],
                    "workspace_id": record["workspace_id"],
                }
                workspace = self._invoke_phase(
                    handle, selected_project, selected_repository, record,
                    name="create_workspace", tool="todo_create_workspace",
                    arguments=workspace_args,
                    expected_revision=record.get("authority_revision"),
                    idempotency_key=record.get("create_workspace_key"),
                )
                if workspace.get("status") == "attention_required":
                    return self._native_attention(workspace)
                record.update(phase="dispatch_pending", workspace=workspace)
                self._update(handle, selected_project, selected_repository, record)

            if record.get("phase") == "dispatch_pending":
                dispatch_args = {
                    "workspace_id": record["workspace_id"], "work_item_id": record["task_id"],
                    "run_id": record.get("run_id"), "assignment_id": record["assignment_id"],
                    "plan_revision_id": record["plan_revision_id"],
                }
                dispatched = self._invoke_phase(
                    handle, selected_project, selected_repository, record,
                    name="dispatch_attempt", tool="todo_dispatch_attempt",
                    arguments=dispatch_args, idempotency_key=record.get("dispatch_key"),
                )
                if dispatched.get("status") == "attention_required":
                    return self._native_attention(dispatched, handle)
                attempt_id = self._nested_text(dispatched, "attempt_ref", "entity_id") or self._optional_text(dispatched, "attempt_id")
                lease_id = self._optional_text(dispatched, "lease_id")
                if not attempt_id or not lease_id:
                    raise CompactWorkflowError("dispatch_receipt_incomplete")
                record.update(phase="context_pending", attempt_id=attempt_id, lease_id=lease_id,
                              dispatch_receipt=dispatched)
                self._update(handle, selected_project, selected_repository, record)
                # Dispatch already owns a native lease. Keep it alive while a
                # potentially large context capsule is assembled and cached.
                self._ensure_lease_renewal(handle, selected_project, selected_repository)

            if record.get("phase") == "context_pending":
                context = self._invoke("todo_build_context", record,
                    work_item_id=record["task_id"], run_id=record.get("run_id"),
                    assignment_id=record["assignment_id"], attempt_id=record["attempt_id"],
                    max_bytes=4096)
                if context.get("status") == "attention_required":
                    return self._native_attention(context, handle)
                context = _bounded_mapping(context, "task_context", _MAX_CONTEXT_CAPSULE_BYTES)
                # Merge only context fields into the current record. The lease
                # renewer may have replaced lease_id while native context was
                # being rendered; writing the stale local record would undo it.
                record = self._update(handle, selected_project, selected_repository, {
                    "phase": "active", "context": context,
                    "requirement_ids": self._requirement_ids(context),
                })

            if record.get("phase") == "review_pending":
                return self._bounded_response({
                    "status": "review_pending", "workflow_handle": handle,
                    "task_id": record.get("task_id"), "allowed_actions": ["inspect_task", "next_task"],
                    "recommended_next_call": "next_task",
                })
            if record.get("phase") != "active":
                return self._attention("workflow_recovery_required", workflow_handle=handle,
                                       allowed_actions=["inspect_task", "finish_task"])
            if not is_new:
                resumed = self._resume_active(handle, selected_project, selected_repository, record)
                if resumed.get("status") == "attention_required":
                    return resumed
                record = self._store.get("workflow", handle, selected_project, selected_repository)
                record.update(project=selected_project, repository=selected_repository)
            self._ensure_lease_renewal(handle, selected_project, selected_repository)
            return self._task_packet(handle, record, resumed=not is_new)
        except CompactWorkflowError as error:
            return self._attention(error.code)
        except Exception:
            return self._attention("compact_workflow_next_task_failed")

    @staticmethod
    def _generated_id(prefix: str, handle: str) -> str:
        return f"pcw-{prefix}-" + _digest(handle)[:32]

    @staticmethod
    def _optional_text(value: Mapping[str, Any], key: str) -> str | None:
        raw = value.get(key)
        if raw is None:
            return None
        if isinstance(raw, Mapping) and isinstance(raw.get("entity_id"), str):
            raw = raw["entity_id"]
        if not isinstance(raw, str) or not raw:
            raise CompactWorkflowError(f"{key}_invalid")
        return raw

    @classmethod
    def _first_text(cls, value: Mapping[str, Any], *keys: str) -> str:
        for key in keys:
            result = cls._optional_text(value, key)
            if result is not None:
                return result
        raise CompactWorkflowError(f"{keys[0]}_missing")

    @classmethod
    def _nested_text(cls, value: Mapping[str, Any], *keys: str) -> str | None:
        current: Any = value
        for key in keys:
            if not isinstance(current, Mapping):
                return None
            current = current.get(key)
        return current if isinstance(current, str) and current else None

    @staticmethod
    def _requirement_ids(context: Mapping[str, Any]) -> list[str]:
        fragments = context.get("fragments")
        if not isinstance(fragments, list):
            return []
        result: set[str] = set()
        for fragment in fragments:
            if not isinstance(fragment, Mapping):
                continue
            payload = fragment.get("payload")
            requirements = payload.get("requirements") if isinstance(payload, Mapping) else None
            if not isinstance(requirements, list):
                continue
            for row in requirements:
                if not isinstance(row, Mapping):
                    continue
                kind = row.get("kind")
                requirement_id = row.get("id", row.get("requirement_id"))
                if kind in {"check", "RequirementKind.CHECK", None} and isinstance(requirement_id, str):
                    result.add(requirement_id)
        return sorted(result)

    @classmethod
    def _task_packet(cls, handle: str, record: Mapping[str, Any], *, resumed: bool) -> dict[str, Any]:
        status = "resumed" if resumed else "claimed"
        workspace = record.get("workspace")
        if isinstance(workspace, Mapping) and isinstance(workspace.get("workspace"), Mapping):
            workspace = workspace["workspace"]
        base = {
            "status": status,
            "task_id": record.get("task_id"),
            "workspace": workspace,
            "workflow_handle": handle,
            "allowed_actions": ["inspect_task", "coordinate_task", "finish_task"],
            "recommended_next_call": "finish_task",
        }
        context = record.get("context", {})
        if not isinstance(context, Mapping):
            context = {}
        packet = {**base, "context": dict(context)}
        task_summary = cls._task_summary(record, context)
        if task_summary:
            packet["task_summary"] = task_summary
        try:
            return cls._bounded_response(packet)
        except CompactWorkflowError as error:
            if error.code != "workflow_response_too_large":
                raise

        compact = cls._compact_public_context(context)
        packet["context"] = compact
        packet["context_status"] = "partial"
        packet["context_continuation"] = {
            "kind": "context_fragment",
            "target_format": "<fragment_id>#offset=<next_offset>",
            "omitted_fragment_ids": [
                row["fragment_id"] for row in compact.get("omitted", [])
                if isinstance(row, Mapping) and isinstance(row.get("fragment_id"), str)
            ],
        }
        if task_summary:
            packet["task_summary"] = task_summary

        refs = cls._context_refs(context)
        fragments = context.get("fragments", [])
        if isinstance(fragments, list):
            ordered = sorted(fragments, key=cls._context_fragment_priority)
            for fragment in ordered:
                reference = fragment.get("reference") if isinstance(fragment, Mapping) else None
                fragment_id = reference.get("fragment_id") if isinstance(reference, Mapping) else None
                if not isinstance(fragment_id, str):
                    continue
                included = compact.get("fragments", [])
                if not isinstance(included, list) or any(
                    isinstance(row, Mapping)
                    and isinstance(row.get("reference"), Mapping)
                    and row["reference"].get("fragment_id") == fragment_id
                    for row in included
                ):
                    continue
                candidate_context = cls._compact_public_context(
                    context, included=[*included, dict(fragment)],
                )
                packet["context"] = candidate_context
                packet["context_continuation"]["omitted_fragment_ids"] = [
                    row["fragment_id"] for row in candidate_context.get("omitted", [])
                    if isinstance(row, Mapping) and isinstance(row.get("fragment_id"), str)
                ]
                if len(_canonical(packet)) > _MAX_RESPONSE_BYTES:
                    packet["context"] = compact
                    packet["context_continuation"]["omitted_fragment_ids"] = [
                        row["fragment_id"] for row in compact.get("omitted", [])
                        if isinstance(row, Mapping) and isinstance(row.get("fragment_id"), str)
                    ]
                else:
                    compact = candidate_context
        # A minimal bounded context envelope always keeps the claim usable;
        # omitted fragments remain addressable through inspect_task.
        return cls._bounded_response(packet)

    @classmethod
    def _context_refs(cls, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        manifest = context.get("manifest")
        raw = manifest.get("fragments", []) if isinstance(manifest, Mapping) else []
        if not isinstance(raw, list):
            return []
        return [dict(row) for row in raw if isinstance(row, Mapping)
                and isinstance(row.get("fragment_id"), str)]

    @staticmethod
    def _context_fragment_priority(fragment: Mapping[str, Any]) -> int:
        reference = fragment.get("reference")
        kind = reference.get("kind") if isinstance(reference, Mapping) else None
        return {
            "work_item": 0, "assignment": 1, "plan": 2, "declarations": 3,
            "source_references": 4, "findings": 5, "scoped_deltas": 6,
        }.get(kind, 7)

    @classmethod
    def _task_summary(cls, record: Mapping[str, Any],
                      context: Mapping[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        task_id = record.get("task_id")
        if isinstance(task_id, str):
            summary["task_id"] = task_id
        fragments = context.get("fragments", [])
        if not isinstance(fragments, list):
            return summary
        work_item = next((item for item in fragments if isinstance(item, Mapping)
                          and isinstance(item.get("reference"), Mapping)
                          and item["reference"].get("kind") == "work_item"), None)
        payload = work_item.get("payload") if isinstance(work_item, Mapping) else None
        if not isinstance(payload, Mapping):
            return summary
        definition = payload.get("definition")
        authored = payload.get("authored")
        if isinstance(definition, Mapping):
            for source, target in (("title", "title"), ("name", "title"), ("summary", "summary")):
                value = definition.get(source)
                if isinstance(value, str):
                    summary[target] = value[:256]
                    break
        if isinstance(authored, Mapping):
            for source, limit in (("objective", 1200), ("scope", 500), ("acceptance", 500)):
                value = authored.get(source)
                if isinstance(value, str):
                    encoded = value.encode("utf-8")
                    summary[source] = encoded[:limit].decode("utf-8", "ignore")
                    if len(encoded) > limit:
                        summary[source + "_truncated"] = True
        return summary

    @staticmethod
    def _context_fragment_target(target: str | None) -> tuple[str, int | None]:
        value = _safe_component(target, "fragment_id", 256)
        marker = "#offset="
        if marker not in value:
            return _safe_component(value, "fragment_id", 128), None
        fragment_id, raw_offset = value.rsplit(marker, 1)
        fragment_id = _safe_component(fragment_id, "fragment_id", 128)
        if not raw_offset.isdecimal():
            raise CompactWorkflowError("context_fragment_offset_invalid")
        offset = int(raw_offset)
        if offset > 64 * 1024:
            raise CompactWorkflowError("context_fragment_offset_invalid")
        return fragment_id, offset

    @classmethod
    def _compact_public_context(
        cls, context: Mapping[str, Any], *,
        included: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        manifest = context.get("manifest")
        refs = cls._context_refs(context)
        included_rows = list(included or [])
        included_ids = {
            reference.get("fragment_id")
            for row in included_rows
            if isinstance((reference := row.get("reference")), Mapping)
        }
        omitted = [row for row in refs if row.get("fragment_id") not in included_ids]
        result: dict[str, Any] = {
            "format": context.get("format", "todo-orchestrator.context-capsule"),
            "version": context.get("version", 1),
            "manifest": dict(manifest) if isinstance(manifest, Mapping) else {},
            "semantic_revision": context.get("semantic_revision"),
            "fragments": included_rows,
            "omitted": omitted,
            "status": "partial" if omitted or context.get("status") == "partial" else context.get("status", "ready"),
            "changes_truncated": True,
        }
        # Changes are not needed to resume a fresh task; the immutable complete
        # manifest and omitted references are enough to continue inspection.
        encoded_bytes = len(_canonical(result))
        while True:
            result["encoded_bytes"] = encoded_bytes
            updated = len(_canonical(result))
            if updated == encoded_bytes:
                break
            encoded_bytes = updated
        return result

    def _next_pending_review(self, project: str, repository: str,
                             requested: Mapping[str, Any]) -> dict[str, Any] | None:
        scope_record = {"project": project, "repository": repository}
        page_token = None
        expected_revision = None
        candidates: list[dict[str, Any]] = []
        attempts: dict[str, dict[str, Any]] = {}
        for _ in range(32):
            page = self._invoke("todo_read_authority", scope_record, limit=200,
                                page_token=page_token, expected_revision=expected_revision)
            if page.get("status") == "attention_required":
                raise CompactWorkflowError(str(page.get("reason", "review_queue_unavailable")))
            if expected_revision is None:
                revision = page.get("semantic_revision")
                if type(revision) is int:
                    expected_revision = revision
            sections = page.get("sections")
            if isinstance(sections, Mapping):
                raw_candidates = sections.get("outcome_candidates", [])
                raw_attempts = sections.get("attempts", [])
                if isinstance(raw_candidates, list):
                    candidates.extend(row for row in raw_candidates if isinstance(row, dict))
                if isinstance(raw_attempts, list):
                    for row in raw_attempts:
                        if isinstance(row, dict) and isinstance(row.get("attempt_id"), str):
                            attempts[row["attempt_id"]] = row
            metadata = page.get("page")
            next_token = metadata.get("next_page_token") if isinstance(metadata, Mapping) else None
            if next_token is None:
                break
            if not isinstance(next_token, str) or not next_token:
                raise CompactWorkflowError("review_queue_cursor_invalid")
            page_token = next_token
        else:
            raise CompactWorkflowError("review_queue_too_large")
        matching = []
        for candidate in candidates:
            if candidate.get("state") != "pending":
                continue
            work_item_id = candidate.get("work_item_id")
            attempt_id = candidate.get("attempt_id")
            attempt = attempts.get(attempt_id) if isinstance(attempt_id, str) else None
            if requested.get("task_id") is not None and work_item_id != requested["task_id"]:
                continue
            if requested.get("run_id") is not None and (not attempt or attempt.get("run_id") != requested["run_id"]):
                continue
            candidate_id = candidate.get("candidate_id")
            if not all(isinstance(value, str) and value for value in
                       (candidate_id, attempt_id, work_item_id)):
                continue
            matching.append((str(candidate.get("reported_at", "")), candidate_id,
                             candidate, attempt or {}))
        if not matching:
            return None
        matching.sort(key=lambda item: (item[0], item[1]))
        _reported_at, _candidate_id, candidate, attempt = matching[0]
        return {
            "mode": "review", "project": project, "repository": repository,
            "candidate_id": candidate["candidate_id"], "attempt_id": candidate["attempt_id"],
            "task_id": candidate["work_item_id"], "run_id": attempt.get("run_id"),
            "assignment_id": candidate.get("assignment_id", attempt.get("assignment_id")),
            "candidate_disposition": candidate.get("disposition"),
            "candidate_state": candidate.get("state"),
            "reported_by_actor_id": candidate.get("reported_by_actor_id"),
            "candidate_source_digest": candidate.get("source_digest"),
        }

    @classmethod
    def _review_packet(cls, handle: str, record: Mapping[str, Any]) -> dict[str, Any]:
        return cls._bounded_response({
            "status": "review_required", "task_id": record.get("task_id"),
            "workflow_handle": handle, "candidate_handle": handle,
            "disposition": record.get("candidate_disposition"),
            "allowed_actions": ["inspect_task", "coordinate_task", "finish_task"],
            "recommended_next_call": "inspect_task(candidate)",
        })

    def inspect_task(
        self, *, workflow_handle: str, kind: str, target: str | None = None,
        budget_bytes: int = _MAX_CONTEXT_BYTES,
    ) -> dict[str, Any]:
        """Read one bounded native workflow detail under an opaque handle."""
        try:
            if type(budget_bytes) is not int or not 512 <= budget_bytes <= 64 * 1024:
                raise CompactWorkflowError("budget_bytes_invalid")
            if kind not in {
                "task", "source", "evidence", "run", "lane", "decision", "messages",
                "rendezvous", "context_fragment", "workspace", "integration", "candidate",
                "effect", "resource", "child", "recovery",
            }:
                raise CompactWorkflowError("inspection_kind_unsupported")
            project, repository, record = self._handle(workflow_handle)
            record.update(project=project, repository=repository)
            if kind == "task":
                if record.get("mode") == "review":
                    result = self._invoke("todo_read_candidate", record,
                                          candidate_id=record.get("candidate_id"))
                    if result.get("status") == "attention_required":
                        return self._native_attention(result, workflow_handle)
                else:
                    result = record.get("context", {})
            elif kind == "context_fragment":
                fragment_id, offset = self._context_fragment_target(target)
                context = record.get("context")
                if not isinstance(context, Mapping):
                    raise CompactWorkflowError("task_context_unavailable")
                manifest = context.get("manifest")
                references = manifest.get("fragments") if isinstance(manifest, Mapping) else None
                reference = next((item for item in references if isinstance(item, Mapping)
                                  and item.get("fragment_id") == fragment_id), None) if isinstance(references, list) else None
                if reference is None:
                    raise CompactWorkflowError("context_fragment_unavailable")
                expanded = self._invoke("todo_expand_context", record,
                    work_item_id=record["task_id"], run_id=record.get("run_id"),
                    assignment_id=record.get("assignment_id"), attempt_id=record.get("attempt_id"),
                    fragment_id=fragment_id, expected_digest=reference.get("digest"),
                    max_bytes=min(max(budget_bytes, 4096), 64 * 1024), offset=offset)
                if expanded.get("status") == "attention_required":
                    return self._native_attention(expanded, workflow_handle)
                result = dict(expanded)
                next_offset = result.get("next_offset")
                total_bytes = result.get("total_bytes")
                if (type(next_offset) is int and type(total_bytes) is int
                        and next_offset < total_bytes):
                    ref = result.get("reference")
                    continuation_id = (ref.get("fragment_id")
                                       if isinstance(ref, Mapping) else fragment_id)
                    if isinstance(continuation_id, str):
                        result["continuation_target"] = (
                            f"{continuation_id}#offset={next_offset}")
            elif kind == "source":
                context = record.get("context", {})
                manifest = context.get("manifest") if isinstance(context, Mapping) else None
                result = {"manifest": manifest, "source_fragments": [
                    item for item in manifest.get("fragments", []) if isinstance(item, Mapping)
                ] if isinstance(manifest, Mapping) else []}
            elif kind in {"run", "lane"}:
                run_id = target or record.get("run_id")
                if not isinstance(run_id, str) or not run_id:
                    raise CompactWorkflowError("run_id_unavailable")
                result = self._invoke("todo_read_run", record, run_id=run_id)
                if result.get("status") == "attention_required":
                    return self._native_attention(result, workflow_handle)
                if kind == "lane":
                    result = {"run_id": run_id, "lanes": result.get("schedule", {}).get("lanes", [])}
            elif kind == "messages":
                if not record.get("attempt_id"):
                    raise CompactWorkflowError("attempt_unavailable")
                result = self._invoke("todo_inbox_status", record,
                    scope={"kind": "attempt", "entity_id": record["attempt_id"]},
                    run_id=record.get("run_id"), assignment_id=record.get("assignment_id"),
                    attempt_id=record.get("attempt_id"))
                if result.get("status") == "attention_required":
                    return self._native_attention(result, workflow_handle)
            elif kind == "candidate":
                candidate_id = record.get("candidate_id")
                expected_candidate_handle = workflow_handle if record.get("mode") == "review" else record.get("candidate_handle")
                if target is not None and target != expected_candidate_handle:
                    raise CompactWorkflowError("candidate_handle_mismatch")
                if not isinstance(candidate_id, str):
                    raise CompactWorkflowError("candidate_unavailable")
                result = self._invoke("todo_read_candidate", record, candidate_id=candidate_id)
                if result.get("status") == "attention_required":
                    return self._native_attention(result, workflow_handle)
            elif kind in {"recovery", "integration"}:
                if kind == "integration" and record.get("integration_receipt") is not None:
                    result = record["integration_receipt"]
                else:
                    result = self._invoke("todo_inspect_attempt", record,
                                          attempt_id=record.get("attempt_id"))
                    if result.get("status") == "attention_required":
                        return self._native_attention(result, workflow_handle)
            elif kind == "effect":
                operation_id = _safe_component(target, "operation_id", 128)
                result = self._invoke("todo_inspect_effect", record, operation_id=operation_id)
                if result.get("status") == "attention_required":
                    return self._native_attention(result, workflow_handle)
            elif kind == "resource":
                operation_id = _safe_component(target, "operation_id", 128)
                result = self._invoke("todo_inspect_resource", record,
                                      attempt_id=record.get("attempt_id"), operation_id=operation_id)
                if result.get("status") == "attention_required":
                    return self._native_attention(result, workflow_handle)
            elif kind == "child":
                execution_id = _safe_component(target, "execution_id", 128)
                result = self._invoke("todo_read_child_execution", record,
                    parent_attempt_id=record.get("attempt_id"), execution_id=execution_id)
                if result.get("status") == "attention_required":
                    return self._native_attention(result, workflow_handle)
            elif kind in {"evidence", "decision", "rendezvous", "workspace"}:
                result = self._invoke("todo_read_authority", record, limit=50)
                if result.get("status") == "attention_required":
                    return self._native_attention(result, workflow_handle)
                sections = result.get("sections", {})
                aliases = {
                    "evidence": ("evidence", "artifacts", "outcomes"),
                    "decision": ("decisions",),
                    "rendezvous": ("rendezvous", "declarations"),
                    "workspace": ("workspaces",),
                }
                if isinstance(sections, Mapping):
                    result = {"sections": {key: sections[key] for key in aliases[kind] if key in sections},
                              "page": result.get("page")}
            else:
                raise CompactWorkflowError("inspection_kind_unsupported")
            bounded = _bounded_mapping(result, "inspection_result", budget_bytes)
            return self._bounded_response({"status": "ok", "kind": kind, "result": bounded,
                                           "workflow_handle": workflow_handle}, limit=budget_bytes + 1024)
        except CompactWorkflowError as error:
            return self._attention(error.code, workflow_handle=workflow_handle,
                                   allowed_actions=["inspect_task", "coordinate_task", "finish_task"])
        except Exception:
            return self._attention("compact_workflow_inspection_failed", workflow_handle=workflow_handle)

    def coordinate_task(
        self, *, workflow_handle: str, action: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform one typed workflow action permitted for this attempt."""
        try:
            allowed = {
                "sync", "fork", "message", "answer", "arrive", "arrive_rendezvous",
                "publish_interface", "publish_context", "run_gates", "bind_required_gates",
                "request_integration", "integrate", "accept_child", "reject_child",
                "resolve_decision", "accept_checkpoint", "release_barrier", "open_blocker",
                "clear_blocker", "publish_finding", "invalidate_finding", "start_child",
                "submit_child_candidate", "review_child_candidate", "cancel_child",
                "reconcile_child", "recover", "verify", "heartbeat", "resume",
                "release_attempt", "report_outcome", "accept_outcome", "reject_outcome",
                "reserve_resource", "reconcile_resource", "retry_resource", "release_resource",
            }
            if action not in allowed:
                raise CompactWorkflowError("workflow_action_unsupported")
            fields = _bounded_mapping(payload or {}, "workflow_payload", 32 * 1024)
            project, repository, record = self._handle(workflow_handle)
            record.update(project=project, repository=repository)
            if action == "bind_required_gates":
                requested = fields.get("required_gates", fields.get("gate_ids", []))
                if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
                    raise CompactWorkflowError("required_gates_invalid")
                native_requirements = record.get("requirement_ids", [])
                if sorted(set(requested)) != sorted(set(native_requirements)):
                    return self._bounded_response({
                        "status": "needs_plan_change", "reason": "plan_requirements_are_immutable",
                        "declared_requirements": native_requirements,
                        "allowed_actions": ["plan"], "recommended_next_call": "plan(validate/diff)",
                        "workflow_handle": workflow_handle,
                    })
                return self._bounded_response({
                    "status": "ok", "binding": "native_plan_requirements",
                    "required_gates": native_requirements,
                    "workflow_handle": workflow_handle,
                })
            if action == "request_integration":
                return self._attention("complete_and_hand_back_before_integration",
                                       workflow_handle=workflow_handle,
                                       allowed_actions=["finish_task", "inspect_task"],
                                       recommended_next_call="finish_task")
            if action == "run_gates":
                return self._run_gates(workflow_handle, project, repository, record, fields)
            if action == "verify":
                requirement_id = self._first_text(fields, "requirement_id")
                return self._run_gates(workflow_handle, project, repository, record,
                                       {"requirement_ids": [requirement_id]})
            if action in {"heartbeat", "resume"}:
                if record.get("phase") != "active":
                    raise CompactWorkflowError("workflow_attempt_not_active")
                refreshed = self._resume_active(workflow_handle, project, repository, record)
                if refreshed.get("status") == "attention_required":
                    return refreshed
                return self._bounded_response({"status": "ok", "action": action,
                                               "workflow_handle": workflow_handle})
            if action == "release_attempt":
                return self.finish_task(workflow_handle=workflow_handle, action="release",
                                        note=fields.get("note"), reason=fields.get("reason"))
            if action == "report_outcome":
                disposition = fields.get("disposition")
                action_map = {"completed": "complete", "no_change": "handoff",
                              "failed": "block"}
                if disposition not in action_map:
                    raise CompactWorkflowError("outcome_disposition_unsupported")
                return self.finish_task(workflow_handle=workflow_handle,
                    action=action_map[disposition], note=fields.get("note"),
                    reason=fields.get("reason"))
            is_review_handle = record.get("mode") == "review"
            if is_review_handle:
                allowed_review_actions = {
                    "accept_outcome": {"review_pending", "review_accepted"},
                    "reject_outcome": {"review_pending", "review_rejected"},
                    "integrate": {"review_accepted", "integrated"},
                }
                if action not in allowed_review_actions or record.get("phase") not in allowed_review_actions[action]:
                    raise CompactWorkflowError("review_action_not_permitted")
                if not all(isinstance(record.get(name), str) for name in ("attempt_id", "candidate_id")):
                    raise CompactWorkflowError("candidate_unavailable")
            elif record.get("phase") != "active":
                raise CompactWorkflowError("workflow_attempt_not_active")
            elif action in {"integrate", "accept_outcome", "reject_outcome"}:
                raise CompactWorkflowError("review_handle_required")
            return self._execute_coordinate_action(
                workflow_handle, project, repository, action, fields, record,
            )
        except CompactWorkflowError as error:
            return self._attention(error.code, workflow_handle=workflow_handle)
        except Exception:
            return self._attention("compact_workflow_action_failed", workflow_handle=workflow_handle)

    def _execute_coordinate_action(
        self, handle: str, project: str, repository: str, action: str,
        fields: Mapping[str, Any], record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Serialize a foreground mutation with heartbeat revision snapshots."""
        with self._renewal_lock:
            native_tool, native_args = self._coordinate_arguments(handle, action, fields, record)
            action_id = _digest(action + "\0" + _canonical(fields).decode("utf-8"))
            intent_name = "coordinate:" + action_id
            operations = record.get("operations", {})
            if not isinstance(operations, Mapping):
                operations = {}
            prior = operations.get(action_id)
            if isinstance(prior, Mapping) and prior.get("state") == "complete":
                return self._bounded_response({"status": "ok", "action": action,
                                               "result": prior.get("result"),
                                               "workflow_handle": handle})
            result = self._invoke_phase(
                handle, project, repository, dict(record), name=intent_name,
                tool=native_tool, arguments=native_args,
            )
            if result.get("status") == "attention_required":
                return self._native_attention(result, handle)
            current = self._store.get("workflow", handle, project, repository)
            phase_intents = current.get("phase_intents", {})
            intent = phase_intents.get(intent_name) if isinstance(phase_intents, Mapping) else None
            key = intent.get("idempotency_key") if isinstance(intent, Mapping) else None
            if not isinstance(key, str):
                raise CompactWorkflowError("workflow_phase_intent_unavailable")
            updated_operations = dict(operations)
            updated_operations[action_id] = {"action": action, "key": key, "state": "pending"}
            self._update(handle, project, repository, {"operations": updated_operations})
            if not isinstance(result, Mapping):
                raise CompactWorkflowError("native_result_invalid")
            bounded_result = _bounded_mapping(result, "workflow_action_result", _MAX_RESULT_BYTES)
            updated_operations[action_id] = {"action": action, "key": key,
                                             "state": "complete", "result": bounded_result}
            changes: dict[str, Any] = {"operations": updated_operations}
            if action in {"publish_finding", "publish_context"}:
                finding_id = self._nested_text(bounded_result, "finding_ref", "entity_id")
                if finding_id:
                    changes["finding_id"] = finding_id
            if action == "integrate":
                changes["integration_receipt"] = bounded_result
                changes["phase"] = "integrated"
            elif action == "accept_outcome":
                changes["phase"] = "review_accepted"
            elif action == "reject_outcome":
                changes["phase"] = "review_rejected"
            self._update(handle, project, repository, changes)
            return self._bounded_response({"status": "ok", "action": action,
                                           "result": bounded_result,
                                           "workflow_handle": handle})

    def _run_gates(self, handle: str, project: str, repository: str,
                   record: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._renewal_lock:
            return self._run_gates_locked(handle, project, repository, record, payload)

    def _run_gates_locked(self, handle: str, project: str, repository: str,
                          record: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        requested = payload.get("requirement_ids", record.get("requirement_ids", []))
        if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
            raise CompactWorkflowError("requirement_ids_invalid")
        declared = set(record.get("requirement_ids", []))
        if any(item not in declared for item in requested):
            raise CompactWorkflowError("requirement_not_declared_in_plan")
        results = []
        for requirement_id in requested:
            verify_args = {"attempt_id": record.get("attempt_id"),
                           "requirement_id": requirement_id}
            result = self._invoke_phase(
                handle, project, repository, dict(record),
                name="verify:" + requirement_id, tool="todo_verify_attempt",
                arguments=verify_args,
            )
            if result.get("status") == "attention_required":
                return self._native_attention(result, handle)
            results.append(result)
        return self._bounded_response({"status": "ok", "verified": results,
                                       "workflow_handle": handle})

    def _ensure_lease_renewal(self, handle: str, project: str, repository: str) -> None:
        """Keep this host's active attempt lease alive while its handle is active."""
        with self._renewal_lock:
            current = self._renewal_workers.get(handle)
            if current is not None and current[1].is_alive():
                return
            stop = threading.Event()
            thread = threading.Thread(
                target=self._lease_renewal_loop,
                args=(handle, project, repository, stop),
                name="pc-lease-" + _digest(handle)[:10], daemon=True,
            )
            self._renewal_workers[handle] = (stop, thread)
            thread.start()

    def _stop_lease_renewal(self, handle: str) -> None:
        with self._renewal_lock:
            current = self._renewal_workers.get(handle)
            if current is None:
                return
            stop, thread = current
            stop.set()
        if thread is not threading.current_thread():
            # Wait for an in-flight heartbeat so finish reads the newest lease.
            thread.join()
        with self._renewal_lock:
            if self._renewal_workers.get(handle) == current:
                self._renewal_workers.pop(handle, None)

    def _lease_renewal_loop(
        self, handle: str, project: str, repository: str, stop: threading.Event,
    ) -> None:
        try:
            while not stop.wait(_LEASE_RENEWAL_INTERVAL_SECONDS):
                with self._renewal_lock:
                    if stop.is_set():
                        return
                    record = self._store.get("workflow", handle, project, repository)
                    if record.get("phase") not in {"active", "context_pending"}:
                        return
                    renewed = self._heartbeat_active(handle, project, repository, record)
                    if renewed.get("status") == "attention_required":
                        self._update(handle, project, repository,
                                     {"renewal_attention": renewed.get("reason", "lease_renewal_failed")})
                        return
        except Exception:
            try:
                self._update(handle, project, repository,
                             {"renewal_attention": "lease_renewal_failed"})
            except Exception:
                pass
        finally:
            with self._renewal_lock:
                current = self._renewal_workers.get(handle)
                if current is not None and current[0] is stop:
                    self._renewal_workers.pop(handle, None)

    def _heartbeat_active(self, handle: str, project: str, repository: str,
                          record: Mapping[str, Any]) -> dict[str, Any]:
        attempt_id = record.get("attempt_id")
        lease_id = record.get("lease_id")
        generation = record.get("heartbeat_generation", 0)
        if (not isinstance(attempt_id, str) or not isinstance(lease_id, str)
                or not isinstance(record.get("plan_revision_id"), str)
                or type(generation) is not int or generation < 0):
            return self._attention("workflow_attempt_binding_incomplete", workflow_handle=handle)
        arguments = {"attempt_id": attempt_id, "lease_id": lease_id,
                     "plan_revision_id": record["plan_revision_id"]}
        renewed = self._invoke_phase(
            handle, project, repository, dict(record), name=f"heartbeat:{generation}",
            tool="todo_heartbeat_attempt", arguments=arguments,
        )
        if renewed.get("status") == "attention_required":
            return self._native_attention(renewed, handle)
        next_lease = self._nested_text(renewed, "result", "lease_id")
        if not next_lease:
            return self._attention("lease_renewal_receipt_incomplete", workflow_handle=handle)
        self._update(handle, project, repository, {
            "lease_id": next_lease, "heartbeat_generation": generation + 1,
            "last_heartbeat_receipt": renewed, "renewal_attention": None,
        })
        return {"status": "ok"}

    def _resume_active(self, handle: str, project: str, repository: str,
                       record: Mapping[str, Any]) -> dict[str, Any]:
        with self._renewal_lock:
            return self._resume_active_locked(handle, project, repository, record)

    def _resume_active_locked(self, handle: str, project: str, repository: str,
                              record: Mapping[str, Any]) -> dict[str, Any]:
        """Re-observe and renew only the exact persisted attempt lease."""
        attempt_id = record.get("attempt_id")
        lease_id = record.get("lease_id")
        if not all(isinstance(value, str) and value for value in
                   (attempt_id, lease_id, record.get("task_id"),
                    record.get("run_id"), record.get("assignment_id"),
                    record.get("plan_revision_id"))):
            return self._attention("workflow_attempt_binding_incomplete", workflow_handle=handle)
        call_record = dict(record, project=project, repository=repository)
        inspected = self._invoke("todo_inspect_attempt", call_record, attempt_id=attempt_id)
        if inspected.get("status") == "attention_required":
            return self._native_attention(inspected, handle)
        generation = record.get("resume_generation", 0)
        if type(generation) is not int or generation < 0:
            return self._attention("workflow_resume_generation_invalid", workflow_handle=handle)
        resume_args = {
            "attempt_id": attempt_id, "lease_id": lease_id,
            "plan_revision_id": record["plan_revision_id"],
            "work_item_id": record["task_id"], "run_id": record["run_id"],
            "assignment_id": record["assignment_id"],
        }
        resumed = self._invoke_phase(
            handle, project, repository, dict(record), name=f"resume:{generation}",
            tool="todo_resume_attempt", arguments=resume_args,
        )
        if resumed.get("status") == "attention_required":
            return self._native_attention(resumed, handle)
        next_lease = self._nested_text(resumed, "result", "lease_id")
        if not next_lease:
            return self._attention("attempt_resume_receipt_incomplete", workflow_handle=handle)
        self._update(handle, project, repository,
                     {"lease_id": next_lease, "resume_generation": generation + 1,
                      "last_resume_receipt": resumed, "last_attempt_inspection": inspected,
                      "renewal_attention": None})
        return {"status": "ok"}

    def _coordinate_arguments(self, handle: str, action: str, fields: Mapping[str, Any],
                              record: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        attempt_id = record.get("attempt_id")
        run_id = record.get("run_id")
        assignment_id = record.get("assignment_id")
        plan_revision_id = record.get("plan_revision_id")
        key = self._operation_key(handle, "coordinate:" + action, fields)

        def required(*names: str) -> dict[str, Any]:
            result = {}
            for name in names:
                if name not in fields:
                    raise CompactWorkflowError(f"{name}_missing")
                result[name] = fields[name]
            return result

        context = {"run_id": run_id, "assignment_id": assignment_id,
                   "attempt_id": attempt_id}
        if action == "sync":
            return "todo_sync_inbox", {**context,
                "scope": fields.get("scope", {"kind": "attempt", "entity_id": attempt_id}),
                "limit": fields.get("limit", 50)}
        if action in {"message", "publish_context", "publish_finding", "invalidate_finding",
                      "answer", "arrive", "arrive_rendezvous", "publish_interface",
                      "resolve_decision", "accept_checkpoint", "release_barrier",
                      "open_blocker", "clear_blocker", "fork", "start_child",
                      "accept_child", "reject_child", "submit_child_candidate",
                      "review_child_candidate", "cancel_child", "reconcile_child", "recover",
                      "accept_outcome", "reject_outcome", "reserve_resource",
                      "reconcile_resource", "retry_resource", "release_resource"}:
            pass
        if action == "message":
            native = required("scope", "recipients", "subject")
            for name in ("message_type", "body", "content_ref"):
                if name in fields:
                    native[name] = fields[name]
            return "todo_send_message", {**context, **native}
        if action == "answer":
            native = required("question_id")
            for name in ("body", "content_ref"):
                if name in fields:
                    native[name] = fields[name]
            return "todo_answer_question", {**context, **native}
        if action in {"arrive", "arrive_rendezvous", "publish_interface", "resolve_decision",
                      "accept_checkpoint", "release_barrier", "open_blocker", "clear_blocker"}:
            if not plan_revision_id:
                raise CompactWorkflowError("plan_revision_unavailable")
            native = required("declaration_id")
            native["plan_revision_id"] = plan_revision_id
            mapping = {
                "arrive": "todo_arrive_rendezvous", "arrive_rendezvous": "todo_arrive_rendezvous",
                "publish_interface": "todo_publish_interface", "resolve_decision": "todo_resolve_decision",
                "accept_checkpoint": "todo_accept_checkpoint", "release_barrier": "todo_release_barrier",
                "open_blocker": "todo_open_blocker", "clear_blocker": "todo_clear_blocker",
            }
            value_fields = {
                "arrive": ("participant",), "arrive_rendezvous": ("participant",),
                "publish_interface": ("version", "rationale"),
                "resolve_decision": ("option_id", "rationale"),
                "accept_checkpoint": ("evidence_ids", "rationale"),
                "release_barrier": ("rationale",), "open_blocker": ("reason",),
                "clear_blocker": ("reason",),
            }[action]
            native.update(required(*value_fields))
            return mapping[action], {**context, **native}
        if action in {"publish_context", "publish_finding", "invalidate_finding"}:
            native = required("work_item_id", "source_manifest")
            if action in {"publish_context", "publish_finding"}:
                native.update(required("source_fragment_refs", "summary"))
                if "finding_id" in fields:
                    native["finding_id"] = fields["finding_id"]
                if "expected_finding_version" in fields:
                    native["expected_finding_version"] = fields["expected_finding_version"]
                return "todo_publish_finding", {**context, **native}
            native.update(required("finding_id", "finding_version", "reason"))
            return "todo_invalidate_finding", {**context, **native}
        if action in {"fork", "start_child"}:
            native = required("scope", "actions")
            native["logical_id"] = fields.get("logical_id", self._generated_id("logical", handle))
            native["execution_id"] = fields.get("execution_id", self._generated_id("child", handle))
            if "retry_of" in fields:
                native["retry_of"] = fields["retry_of"]
            return "todo_start_child", {"parent_attempt_id": attempt_id, **native}
        if action in {"accept_child", "reject_child", "review_child_candidate"}:
            native = required("execution_id", "candidate_id")
            native["decision"] = "accepted" if action == "accept_child" else "rejected" if action == "reject_child" else fields.get("decision")
            native["review_id"] = fields.get("review_id", self._generated_id("review", handle))
            if "reason" in fields:
                native["reason"] = fields["reason"]
            return "todo_review_child_candidate", {"parent_attempt_id": attempt_id, **native}
        if action == "submit_child_candidate":
            native = required("execution_id", "candidate_id", "summary")
            native["artifacts"] = fields.get("artifacts", [])
            return "todo_submit_child_candidate", {"parent_attempt_id": attempt_id, **native}
        if action == "cancel_child":
            native = required("execution_id")
            native["cancellation_id"] = fields.get("cancellation_id", self._generated_id("cancel", handle))
            if "reason" in fields:
                native["reason"] = fields["reason"]
            return "todo_cancel_child", {"parent_attempt_id": attempt_id, **native}
        if action == "reconcile_child":
            native = required("execution_id")
            return "todo_reconcile_child", {"parent_attempt_id": attempt_id, **native}
        if action == "recover":
            return "todo_apply_recovery", required("intent")
        if action == "integrate":
            if not attempt_id:
                raise CompactWorkflowError("attempt_unavailable")
            return "todo_integrate_attempt", {"attempt_id": attempt_id}
        if action in {"accept_outcome", "reject_outcome"}:
            if not attempt_id or not isinstance(record.get("candidate_id"), str):
                raise CompactWorkflowError("candidate_unavailable")
            return ("todo_accept_outcome" if action == "accept_outcome" else "todo_reject_outcome"), {
                "attempt_id": attempt_id, "candidate_id": record["candidate_id"],
            }
        if action == "reserve_resource":
            if not attempt_id:
                raise CompactWorkflowError("attempt_unavailable")
            return "todo_reserve_resource", {"attempt_id": attempt_id,
                **required("resource_class", "units")}
        if action in {"reconcile_resource", "retry_resource", "release_resource"}:
            operation_id = required("operation_id")
            tool = {"reconcile_resource": "todo_reconcile_resource",
                    "retry_resource": "todo_retry_resource",
                    "release_resource": "todo_release_resource"}[action]
            if action == "reconcile_resource":
                tool = "todo_reconcile_resource"
            return tool, operation_id
        raise CompactWorkflowError("workflow_action_unsupported")

    def finish_task(
        self, *, workflow_handle: str, action: str, disposition: str | None = None,
        note: str | None = None, reason: str | None = None,
    ) -> dict[str, Any]:
        """Finish authoring work, or record a reviewer decision on its handle."""
        try:
            if action not in {"complete", "handoff", "block", "release",
                              "accept", "reject", "integrate"}:
                raise CompactWorkflowError("finish_action_unsupported")
            if note is not None:
                note = _safe_component(note, "note", 4000)
            if reason is not None:
                reason = _safe_component(reason, "reason", 2000)
            project, repository, record = self._handle(workflow_handle)
            record.update(project=project, repository=repository)
            if action in {"accept", "reject", "integrate"}:
                if record.get("mode") != "review":
                    raise CompactWorkflowError("review_handle_required")
                if action == "accept":
                    coordinate_action = "accept_outcome"
                elif action == "reject":
                    coordinate_action = "reject_outcome"
                else:
                    coordinate_action = "integrate"
                result = self.coordinate_task(workflow_handle=workflow_handle,
                                              action=coordinate_action,
                                              payload={"reason": reason} if reason else None)
                if result.get("status") != "ok":
                    return result
                response_status = {
                    "accept": "review_accepted", "reject": "review_rejected",
                    "integrate": "integrated",
                }[action]
                return self._bounded_response({
                    "status": response_status, "workflow_handle": workflow_handle,
                    "task_id": record.get("task_id"), "result": result.get("result"),
                    "allowed_actions": (["inspect_task", "finish_task"]
                                        if action == "accept" else ["inspect_task", "next_task"]),
                    "recommended_next_call": "finish_task" if action == "accept" else "next_task",
                })
            if record.get("mode") == "review":
                raise CompactWorkflowError("authoring_handle_required")
            if record.get("phase") == "review_pending":
                return self._review_pending_packet(workflow_handle, record)
            if record.get("phase") in {"released", "review_accepted", "review_rejected", "integrated"}:
                return self._bounded_response({"status": record["phase"],
                                               "workflow_handle": workflow_handle,
                                               "allowed_actions": ["inspect_task", "next_task"],
                                               "recommended_next_call": "next_task"})
            if record.get("phase") == "active":
                if action == "complete" and disposition not in {None, "completed"}:
                    raise CompactWorkflowError("completion_disposition_conflicts")
                outcome_disposition = {
                    "complete": "completed", "handoff": "no_change", "block": "failed",
                }.get(action)
                if action == "complete":
                    gates = self._run_gates(workflow_handle, project, repository, record, {})
                    if gates.get("status") != "ok":
                        return gates
                self._stop_lease_renewal(workflow_handle)
                record = self._store.get("workflow", workflow_handle, project, repository)
                record.update(project=project, repository=repository)
                candidate_handle = secrets.token_urlsafe(24) if outcome_disposition else None
                finish_record = {
                    "phase": "handback_pending",
                    "finish_action": action, "outcome_disposition": outcome_disposition,
                    "finish_note": note, "finish_reason": reason,
                    "candidate_handle": candidate_handle,
                    "report_key": self._operation_key(workflow_handle, "finish:report", {
                        "action": action, "disposition": outcome_disposition,
                        "note": note, "reason": reason,
                    }),
                    "handback_key": self._operation_key(workflow_handle, "finish:handback"),
                    "release_key": self._operation_key(workflow_handle, "finish:release"),
                }
                self._update(workflow_handle, project, repository, finish_record)
                record.update(finish_record)
            elif record.get("phase") in {"handback_pending", "report_pending", "release_pending"}:
                if record.get("finish_action") != action:
                    raise CompactWorkflowError("finish_action_conflicts_with_pending_operation")
                if note is not None and note != record.get("finish_note"):
                    raise CompactWorkflowError("finish_note_conflicts_with_pending_operation")
                if reason is not None and reason != record.get("finish_reason"):
                    raise CompactWorkflowError("finish_reason_conflicts_with_pending_operation")
                note = record.get("finish_note")
                reason = record.get("finish_reason")
            else:
                raise CompactWorkflowError("workflow_attempt_not_active")

            if record.get("phase") == "handback_pending":
                handback_args = {"attempt_id": record.get("attempt_id"),
                                 "lease_id": record.get("lease_id")}
                handback = self._invoke_phase(
                    workflow_handle, project, repository, record,
                    name="finish:handback", tool="todo_handback_attempt",
                    arguments=handback_args,
                    idempotency_key=record.get("handback_key"),
                )
                if handback.get("status") == "attention_required":
                    return self._native_attention(handback, workflow_handle)
                proof = self._validate_handoff_proof(handback, record)
                next_phase = "report_pending" if record.get("outcome_disposition") else "release_pending"
                record.update(handback_proof=proof, phase=next_phase)
                self._update(workflow_handle, project, repository,
                             {"handback_proof": proof, "phase": next_phase})

            if record.get("phase") == "report_pending":
                combined_note = "\n".join(value for value in (note, reason) if value)
                report_args = {"attempt_id": record.get("attempt_id"),
                               "disposition": record.get("outcome_disposition"),
                               "note": combined_note or None}
                report = self._invoke_phase(
                    workflow_handle, project, repository, record,
                    name="finish:report", tool="todo_report_outcome",
                    arguments=report_args, idempotency_key=record.get("report_key"),
                )
                if report.get("status") == "attention_required":
                    return self._native_attention(report, workflow_handle)
                candidate_id = self._nested_text(report, "candidate_ref", "entity_id")
                if not candidate_id:
                    return self._attention("outcome_report_receipt_incomplete",
                                           workflow_handle=workflow_handle)
                record.update(candidate_id=candidate_id, report_receipt=report,
                              phase="review_pending")
                self._update(workflow_handle, project, repository,
                             {"candidate_id": candidate_id, "report_receipt": report,
                              "phase": "review_pending"})
                return self._review_pending_packet(workflow_handle, record)

            if record.get("phase") == "release_pending":
                release_args = {"attempt_id": record.get("attempt_id"),
                                "lease_id": record.get("lease_id")}
                released = self._invoke_phase(
                    workflow_handle, project, repository, record,
                    name="finish:release", tool="todo_release_attempt",
                    arguments=release_args,
                    idempotency_key=record.get("release_key"),
                )
                if released.get("status") == "attention_required":
                    return self._native_attention(released, workflow_handle)
                terminal = "released"
                self._update(workflow_handle, project, repository,
                             {"phase": terminal, "release_receipt": released})
                record.update(phase=terminal, release_receipt=released)
                return self._bounded_response({
                    "status": terminal, "workflow_handle": workflow_handle,
                    "task_id": record.get("task_id"),
                    "candidate_handle": record.get("candidate_handle"),
                    "allowed_actions": ["inspect_task", "next_task"],
                    "recommended_next_call": "next_task",
                })
            return self._attention("workflow_recovery_required", workflow_handle=workflow_handle)
        except CompactWorkflowError as error:
            return self._attention(error.code, workflow_handle=workflow_handle)
        except Exception:
            return self._attention("compact_workflow_finish_failed", workflow_handle=workflow_handle)

    @staticmethod
    def _validate_handoff_proof(proof: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
        for name in ("attempt_id", "source_commit", "source_digest"):
            value = proof.get(name)
            if not isinstance(value, str) or not value:
                raise CompactWorkflowError("authoring_handoff_proof_incomplete")
        if proof.get("attempt_id") != record.get("attempt_id"):
            raise CompactWorkflowError("authoring_handoff_proof_scope_mismatch")
        digest = proof.get("source_digest")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise CompactWorkflowError("authoring_handoff_source_digest_invalid")
        return _bounded_mapping(proof, "authoring_handoff_proof", _MAX_RESULT_BYTES)

    @classmethod
    def _review_pending_packet(cls, handle: str, record: Mapping[str, Any]) -> dict[str, Any]:
        return cls._bounded_response({
            "status": "review_pending", "task_id": record.get("task_id"),
            "workflow_handle": handle,
            "candidate_handle": record.get("candidate_handle"),
            "reported_disposition": record.get("outcome_disposition"),
            "requested_action": record.get("finish_action"),
            "allowed_actions": ["inspect_task", "next_task"],
            "recommended_next_call": "next_task",
        })


__all__ = ["CompactWorkflowError", "CompactWorkflowHost"]
