"""Private host binding to the standalone Todo application API.

Caller identity and credentials are supplied by trusted Project Control code;
none of the token-store operations are exposed through MCP.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Mapping

from .runtime_identity import RuntimeIdentity, bind_runtime, validate_runtime

TOKEN_STORE_PATH = Path("~/.config/project-control/todo-orchestrator/session-tokens.json")
TOKEN_STORE_FORMAT = "project-control.todo-session-tokens"
TOKEN_STORE_VERSION = 1
_LOCK = RLock()


class HostSessionUnavailable(RuntimeError):
    """A trusted Project Control session token is missing or invalid."""

    code = "todo_host_session_unavailable"


@dataclass(frozen=True)
class WorkflowBinding:
    identity: RuntimeIdentity

    def validate(self) -> None:
        validate_runtime(self.identity)

    def application(self):
        self.validate()
        from todo_orchestrator.api import TodoApplication
        return TodoApplication

    def open_session(self, project_root: Path, bearer_token: str, providers=None):
        self.validate()
        from todo_orchestrator.api import ProviderComposition, TodoApplication
        return TodoApplication.open(
            project_root,
            bearer_token=bearer_token,
            providers=providers or ProviderComposition(),
        )


_BINDING: WorkflowBinding | None = None


def initialize_workflow_binding(environment: Mapping[str, str] = os.environ) -> WorkflowBinding:
    global _BINDING
    with _LOCK:
        if _BINDING is None:
            _BINDING = WorkflowBinding(bind_runtime(environment))
        else:
            _BINDING.validate()
            requested = bind_runtime(environment)
            if (requested.module_file, requested.fingerprint, requested.release_manifest, requested.release_digest) != (
                _BINDING.identity.module_file, _BINDING.identity.fingerprint,
                _BINDING.identity.release_manifest, _BINDING.identity.release_digest,
            ):
                from .runtime_identity import RuntimeIdentityError
                raise RuntimeIdentityError("Project Control cannot rebind Todo after initialization",
                                           expected=str(_BINDING.identity.module_file), observed=str(requested.module_file))
        return _BINDING


def reset_runtime_for_testing() -> None:
    global _BINDING
    with _LOCK:
        _BINDING = None


def runtime_identity(environment: Mapping[str, str] = os.environ) -> RuntimeIdentity:
    return initialize_workflow_binding(environment).identity


def todo_application(environment: Mapping[str, str] = os.environ):
    return initialize_workflow_binding(environment).application()


def open_todo_session(project_root: Path, bearer_token: str, providers=None,
                      environment: Mapping[str, str] = os.environ):
    return initialize_workflow_binding(environment).open_session(project_root, bearer_token, providers)


def _store_path(path: Path | None = None, *, readonly: bool = False) -> Path:
    selected = Path(path or TOKEN_STORE_PATH).expanduser()
    parent = selected.parent
    home = Path.home().resolve()
    try:
        relative = parent.absolute().relative_to(home)
    except ValueError:
        relative = None
    if relative is None:
        if parent.is_symlink():
            raise HostSessionUnavailable("private Todo credential directory is unsafe")
    else:
        current = home
        for component in relative.parts:
            current = current / component
            if current.is_symlink():
                raise HostSessionUnavailable("private Todo credential directory must not contain symlinks")
    if readonly:
        if not parent.exists():
            raise HostSessionUnavailable("private Todo credential directory is unavailable")
    else:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = parent.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
            or (readonly and not info.st_mode & 0o100)
            or (not readonly and not info.st_mode & 0o200)):
        raise HostSessionUnavailable("private Todo credential directory must be owner-only")
    return selected


def _read_store(path: Path) -> dict:
    if path.is_symlink():
        raise HostSessionUnavailable("private Todo credential store must not be a symlink")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return {"format": TOKEN_STORE_FORMAT, "version": TOKEN_STORE_VERSION, "sessions": [], "attempts": [], "children": []}
    except OSError as exc:
        raise HostSessionUnavailable("private Todo credential store could not be opened safely") from exc
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 1024 * 1024:
        os.close(fd)
        raise HostSessionUnavailable("private Todo credential store has unsafe ownership, permissions, or size")
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, ValueError) as exc:
        raise HostSessionUnavailable("private Todo credential store is unreadable") from exc
    if (not isinstance(value, dict) or value.get("format") != TOKEN_STORE_FORMAT
            or value.get("version") != TOKEN_STORE_VERSION
            or set(value) != {"format", "version", "sessions", "attempts", "children"}
            or not isinstance(value["sessions"], list) or not isinstance(value["attempts"], list)
            or not isinstance(value["children"], list)):
        raise HostSessionUnavailable("private Todo credential store format is invalid")
    return value


def _write_store(path: Path, value: dict) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=".session-tokens-", dir=path.parent)
    temp = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        temp.unlink(missing_ok=True)


def _locked_store(path: Path):
    class StoreLock:
        def __enter__(self_nonlocal):
            lock_path = path.with_suffix(path.suffix + ".lock")
            if lock_path.is_symlink():
                raise HostSessionUnavailable("private Todo credential lock must not be a symlink")
            self_nonlocal.fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            stat_result = os.fstat(self_nonlocal.fd)
            if stat_result.st_uid != os.getuid() or stat_result.st_mode & 0o077:
                os.close(self_nonlocal.fd)
                raise HostSessionUnavailable("private Todo credential lock has unsafe ownership or permissions")
            fcntl.flock(self_nonlocal.fd, fcntl.LOCK_EX)
            return self_nonlocal
        def __exit__(self_nonlocal, *_):
            fcntl.flock(self_nonlocal.fd, fcntl.LOCK_UN)
            os.close(self_nonlocal.fd)
    return StoreLock()


def _project_metadata(project_root: Path):
    from todo_orchestrator.api import read_project_metadata
    return read_project_metadata(project_root)


def _valid_entry(entry: object, *, project_id: str, principal: str, profile: str, authority_id: str) -> dict | None:
    if not isinstance(entry, dict):
        return None
    required = {"project_id", "authority_id", "principal_id", "profile", "capability_id", "expires_at", "bearer_token"}
    if set(entry) != required or (entry["project_id"], entry["authority_id"], entry["principal_id"], entry["profile"]) != (
        project_id, authority_id, principal, profile
    ) or not isinstance(entry["bearer_token"], str) or not entry["bearer_token"]:
        return None
    try:
        expires = datetime.fromisoformat(entry["expires_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        return None
    return entry


def resolve_session_token(project_root: Path, *, principal: str, profile: str,
                          store_path: Path | None = None) -> str:
    """Resolve one unexpired exact-scope host credential; never log or return it beyond the caller."""
    metadata = _project_metadata(project_root)
    path = _store_path(store_path, readonly=True)
    # Credential writers replace the complete file atomically. A reader can
    # therefore observe either complete version without creating a lockfile;
    # this is required for the observer service's read-only home namespace.
    with _LOCK:
        value = _read_store(path)
    matches = [item for item in value["sessions"] if isinstance(item, dict)
               and (item.get("project_id"), item.get("principal_id"), item.get("profile")) ==
               (str(metadata.project_id), principal, profile)]
    if len(matches) != 1:
        raise HostSessionUnavailable("no unique Todo session is available for this principal and profile")
    entry = _valid_entry(matches[0], project_id=str(metadata.project_id), principal=principal,
                         profile=profile, authority_id=str(metadata.authority_id))
    if entry is None:
        raise HostSessionUnavailable("Todo session is expired or does not match project authority")
    return entry["bearer_token"]


def open_session_for_host(project_root: Path, *, principal: str, profile: str, providers=None):
    metadata = _project_metadata(project_root)
    token = resolve_session_token(project_root, principal=principal, profile=profile)
    session = initialize_workflow_binding().open_session(project_root, token, providers)
    if (str(session.metadata.project_id), str(session.metadata.authority_id)) != (
        str(metadata.project_id), str(metadata.authority_id)
    ):
        raise HostSessionUnavailable("Todo session opened a different project authority")
    return session


def store_attempt_bearer(project_root: Path, *, principal: str, attempt_id: str, bearer_token: str,
                         profile: str = "coder", store_path: Path | None = None) -> None:
    if not all(isinstance(item, str) and item.strip() for item in (principal, attempt_id, bearer_token, profile)):
        raise ValueError("attempt session fields must be non-empty strings")
    metadata = _project_metadata(project_root)
    path = _store_path(store_path)
    entry = {"project_id": str(metadata.project_id), "authority_id": str(metadata.authority_id),
             "principal_id": principal, "profile": profile, "attempt_id": attempt_id,
             "bearer_token": bearer_token}
    with _LOCK, _locked_store(path):
        value = _read_store(path)
        records = value["attempts"]
        key = (entry["project_id"], principal, profile, attempt_id)
        matches = [item for item in records if isinstance(item, dict)
                   and (item.get("project_id"), item.get("principal_id"), item.get("profile"), item.get("attempt_id")) == key]
        if matches:
            if len(matches) != 1 or matches[0] != entry:
                raise HostSessionUnavailable("attempt session already exists with a different identity")
            return
        records.append(entry)
        _write_store(path, value)


def open_attempt_session(project_root: Path, *, principal: str, attempt_id: str, providers=None,
                         profile: str = "coder", store_path: Path | None = None):
    metadata = _project_metadata(project_root)
    path = _store_path(store_path, readonly=True)
    with _LOCK:
        value = _read_store(path)
    matches = [item for item in value["attempts"] if isinstance(item, dict)
               and (item.get("project_id"), item.get("principal_id"), item.get("profile"), item.get("attempt_id")) ==
               (str(metadata.project_id), principal, profile, attempt_id)]
    if len(matches) != 1:
        raise HostSessionUnavailable("no unique Todo attempt session is available")
    entry = matches[0]
    if (set(entry) != {"project_id", "authority_id", "principal_id", "profile", "attempt_id", "bearer_token"}
            or entry["authority_id"] != str(metadata.authority_id) or not isinstance(entry["bearer_token"], str)):
        raise HostSessionUnavailable("Todo attempt session does not match project authority")
    session = initialize_workflow_binding().open_session(project_root, entry["bearer_token"], providers)
    if (str(session.metadata.project_id), str(session.metadata.authority_id)) != (
        str(metadata.project_id), str(metadata.authority_id)
    ):
        raise HostSessionUnavailable("Todo attempt session opened a different project authority")
    return session


def forget_attempt_bearer(project_root: Path, *, principal: str, attempt_id: str,
                          profile: str = "coder", store_path: Path | None = None) -> None:
    metadata = _project_metadata(project_root)
    path = _store_path(store_path)
    with _LOCK, _locked_store(path):
        value = _read_store(path)
        key = (str(metadata.project_id), principal, profile, attempt_id)
        matches = [item for item in value["attempts"] if isinstance(item, dict)
                   and (item.get("project_id"), item.get("principal_id"), item.get("profile"), item.get("attempt_id")) == key]
        if len(matches) > 1:
            raise HostSessionUnavailable("duplicate Todo attempt sessions prevent safe removal")
        value["attempts"] = [item for item in value["attempts"] if item not in matches]
        _write_store(path, value)


def store_child_bearer(project_root: Path, *, principal: str, parent_attempt_id: str,
                       execution_id: str, child_principal: str, bearer_token: str,
                       profile: str = "coder", store_path: Path | None = None) -> None:
    values = (principal, parent_attempt_id, execution_id, child_principal, bearer_token, profile)
    if not all(isinstance(item, str) and item.strip() for item in values):
        raise ValueError("child session fields must be non-empty strings")
    metadata = _project_metadata(project_root)
    path = _store_path(store_path)
    entry = {"project_id": str(metadata.project_id), "authority_id": str(metadata.authority_id),
             "principal_id": principal, "profile": profile, "parent_attempt_id": parent_attempt_id,
             "execution_id": execution_id, "child_principal_id": child_principal,
             "bearer_token": bearer_token}
    key = (entry["project_id"], principal, profile, parent_attempt_id, execution_id, child_principal)
    with _LOCK, _locked_store(path):
        value = _read_store(path)
        records = value["children"]
        matches = [item for item in records if isinstance(item, dict)
                   and (item.get("project_id"), item.get("principal_id"), item.get("profile"),
                        item.get("parent_attempt_id"), item.get("execution_id"),
                        item.get("child_principal_id")) == key]
        if matches:
            if len(matches) != 1 or matches[0] != entry:
                raise HostSessionUnavailable("child session already exists with a different identity")
            return
        records.append(entry)
        _write_store(path, value)


def open_child_session(project_root: Path, *, principal: str, parent_attempt_id: str,
                       execution_id: str, child_principal: str, profile: str = "coder",
                       providers=None, store_path: Path | None = None):
    metadata = _project_metadata(project_root)
    path = _store_path(store_path, readonly=True)
    key = (str(metadata.project_id), principal, profile, parent_attempt_id, execution_id, child_principal)
    with _LOCK:
        value = _read_store(path)
    matches = [item for item in value["children"] if isinstance(item, dict)
               and (item.get("project_id"), item.get("principal_id"), item.get("profile"),
                    item.get("parent_attempt_id"), item.get("execution_id"),
                    item.get("child_principal_id")) == key]
    if len(matches) != 1:
        raise HostSessionUnavailable("no unique Todo child session is available")
    entry = matches[0]
    required = {"project_id", "authority_id", "principal_id", "profile", "parent_attempt_id",
                "execution_id", "child_principal_id", "bearer_token"}
    if (set(entry) != required or entry["authority_id"] != str(metadata.authority_id)
            or not isinstance(entry["bearer_token"], str) or not entry["bearer_token"]):
        raise HostSessionUnavailable("Todo child session does not match project authority")
    session = initialize_workflow_binding().open_session(project_root, entry["bearer_token"], providers)
    if (str(session.metadata.project_id), str(session.metadata.authority_id)) != (
        str(metadata.project_id), str(metadata.authority_id)
    ):
        raise HostSessionUnavailable("Todo child session opened a different project authority")
    return session


def forget_child_bearer(project_root: Path, *, principal: str, parent_attempt_id: str,
                        execution_id: str, child_principal: str, profile: str = "coder",
                        store_path: Path | None = None) -> None:
    metadata = _project_metadata(project_root)
    path = _store_path(store_path)
    key = (str(metadata.project_id), principal, profile, parent_attempt_id, execution_id, child_principal)
    with _LOCK, _locked_store(path):
        value = _read_store(path)
        matches = [item for item in value["children"] if isinstance(item, dict)
                   and (item.get("project_id"), item.get("principal_id"), item.get("profile"),
                        item.get("parent_attempt_id"), item.get("execution_id"),
                        item.get("child_principal_id")) == key]
        if len(matches) > 1:
            raise HostSessionUnavailable("duplicate Todo child sessions prevent safe removal")
        value["children"] = [item for item in value["children"] if item not in matches]
        _write_store(path, value)


def todo_read_port_factory(environment: Mapping[str, str] = os.environ):
    """Create a native read port factory bound to trusted host identity."""
    from .adapters.todo import NativeTodoReadPort
    binding = initialize_workflow_binding(environment)
    def create(project_root: Path, *, principal: str, profile: str = "observer", providers=None):
        binding.validate()
        return NativeTodoReadPort(project_root, principal=principal, profile=profile, providers=providers)
    return create
