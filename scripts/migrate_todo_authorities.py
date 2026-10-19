#!/usr/bin/env python3
"""Prepare and activate isolated native Todo authorities from frozen exports.

Preparation consumes only the versioned snapshot files indexed by the export
manifest. It never opens a legacy database. Activation is an explicit operator
operation and refuses to run without a prepared manifest, verified source and
backup manifests, and a stopped-writers assertion.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import uuid

PC_SOURCE = Path(__file__).resolve().parents[1] / "src"
if PC_SOURCE.is_dir() and str(PC_SOURCE) not in sys.path:
    sys.path.insert(0, str(PC_SOURCE))


FORMAT = "project-control.todo-cutover-prepared"
VERSION = 1
SESSION_FORMAT = "project-control.todo-session-tokens"
ID_NAMESPACE = uuid.UUID("e23ef8ea-e23d-51b7-b1f0-df0e2e4b8763")
PRINCIPALS = {
    "observer": "project-control-observer",
    "coder": "project-control-coder",
    "mutator": "project-control-mutator",
    "investigator": "project-control-investigator",
    "skill_assembler": "project-control-skill_assembler",
}
WORKFLOW_PARENT_ACTIONS = frozenset({
    "todo.child.delegate", "todo.child.retry", "todo.child.review", "todo.child.cancel",
    "todo.child.stop.reconcile", "todo.child.read", "todo.child.write",
    "todo.child.candidate.submit", "todo.message.send", "todo.message.read", "todo.question.answer",
})
CODER_EXCLUDED = frozenset({
    "todo.plan.commit", "todo.run.schedule", "todo.run.close", "todo.run.retire",
    "todo.recovery.apply", "todo.outcome.accept", "todo.outcome.reject",
    "todo.authority.backup", "todo.capability.delegate", "todo.capability.read",
    "todo.capability.revoke", "todo.migration.import",
})


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"symlink is not allowed in prepared authority: {relative}")
        if path.is_file():
            digest.update(relative.encode("utf-8") + b"\0")
            digest.update(f"{stat.S_IMODE(path.stat().st_mode):04o}".encode("ascii") + b"\0")
            digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _tree_modes(root: Path) -> list[dict[str, object]]:
    values = []
    for path in [root, *sorted(root.rglob("*"))]:
        if path.is_symlink():
            raise ValueError(f"symlink is not allowed in preserved Todo metadata: {path}")
        values.append({"path": "." if path == root else path.relative_to(root).as_posix(),
                       "mode": stat.S_IMODE(path.stat().st_mode)})
    return values


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _replace_private_file(path: Path, data: bytes) -> None:
    home = Path.home().resolve()
    try:
        relative = path.parent.absolute().relative_to(home)
    except ValueError as error:
        raise ValueError("session token directory must remain under the operator home directory") from error
    current = home
    for part in relative.parts:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = current.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise PermissionError("session token directory must be owner-only")
    parent_mode = stat.S_IMODE(path.parent.stat().st_mode)
    if path.parent.stat().st_uid != os.getuid() or parent_mode & 0o077:
        raise PermissionError("session token directory must be owner-only")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _preflight_token_store(path: Path) -> dict | None:
    home = Path.home().resolve()
    absolute = path.expanduser().absolute()
    current = home
    try:
        relative = absolute.relative_to(home)
    except ValueError as error:
        raise ValueError("session token store must remain under the operator home directory") from error
    parent_missing = False
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError("session token store directory must not contain symlinks")
        if not current.exists():
            parent_missing = True
            continue
        if parent_missing:
            raise ValueError("session token directory has a missing parent before an existing child")
        info = current.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise PermissionError("session token directory must be owner-only")
    if parent_missing:
        return None
    if absolute.is_symlink():
        raise ValueError("session token store must not be a symlink")
    if not absolute.exists():
        return None
    info = absolute.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise PermissionError("existing session token store must be owner-only")
    value = _read_json(absolute)
    if (value.get("format") != SESSION_FORMAT or value.get("version") != 1
            or set(value) != {"format", "version", "sessions", "attempts", "children"}
            or not all(isinstance(value.get(name), list) for name in ("sessions", "attempts", "children"))):
        raise ValueError("existing session token store has unsupported format")
    return value


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _todo_api():
    try:
        from todo_orchestrator.api import (  # type: ignore
            CAPABILITY_DELEGATE_ACTION, CAPABILITY_READ_ACTION,
            CAPABILITY_REVOKE_ACTION, DEFAULT_OWNER_ACTIONS, LEGACY_IMPORT_ACTION,
            AuthenticatedPrincipal, ProviderComposition, TodoApplication,
        )
        from todo_orchestrator.contracts import ActorKind, StableId  # type: ignore
    except Exception as error:
        raise RuntimeError("standalone todo-orchestrator public package is unavailable") from error
    if not callable(getattr(TodoApplication, "initialize", None)):
        raise RuntimeError("loaded todo-orchestrator does not provide the standalone public API")
    return locals()


def _index_records(index: dict, index_path: Path) -> list[dict]:
    rows = index.get("current_exports")
    if not isinstance(rows, list) or not rows:
        raise ValueError("snapshot index current_exports must be a non-empty array")
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("snapshot index contains a malformed export record")
        workspace, repository = row.get("registered_workspace"), row.get("registered_repository")
        source_id, filename, expected = row.get("source_project_id"), row.get("snapshot_file"), row.get("snapshot_digest")
        if not all(isinstance(item, str) and item.strip() for item in (workspace, repository, source_id, filename, expected)):
            raise ValueError("snapshot index record is missing workspace, repository, source identity, file, or digest")
        key = (workspace, repository)
        if key in seen:
            raise ValueError(f"duplicate snapshot index authority {workspace}/{repository}")
        seen.add(key)
        snapshot = (index_path.parent / filename).resolve(strict=True)
        if index_path.parent.resolve() not in snapshot.parents:
            raise ValueError("snapshot file escapes the export directory")
        document = json.loads(snapshot.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("snapshot_digest") != expected:
            raise ValueError(f"snapshot digest does not match index for {workspace}/{repository}")
        if document.get("source", {}).get("project_id") != source_id:
            raise ValueError(f"snapshot source identity does not match index for {workspace}/{repository}")
        if row.get("authority_revision") != document.get("source", {}).get("authority_revision"):
            raise ValueError(f"snapshot source revision does not match index for {workspace}/{repository}")
        result.append({**row, "_snapshot_path": snapshot, "_snapshot": document,
                       "_initialized_new": False})
    return result


def _load_registry(path: Path | None):
    try:
        from project_control.config import load_config
        from project_control.registry import WorkspaceRegistry
    except Exception as error:
        raise RuntimeError("Project Control registry package is unavailable") from error
    config = load_config(path, require_private=False)
    return WorkspaceRegistry(config)


class _BootstrapAuthenticator:
    provider_id = "project-control-cutover-bootstrap-v1"

    def __init__(self, credentials: dict[str, object]):
        self._credentials = credentials

    def authenticate(self, credential: object):
        return self._credentials.get(credential) if isinstance(credential, str) else None


def _actions(api: dict):
    StableId = api["StableId"]
    defaults = {str(item) for item in api["DEFAULT_OWNER_ACTIONS"]}
    owner = defaults | WORKFLOW_PARENT_ACTIONS | {
        str(api["CAPABILITY_DELEGATE_ACTION"]), str(api["CAPABILITY_READ_ACTION"]),
        str(api["CAPABILITY_REVOKE_ACTION"]), str(api["LEGACY_IMPORT_ACTION"]),
        # The local bootstrap capability must strictly dominate every host
        # grant. This private marker has no command handler and is not issued
        # to any Project Control principal.
        "todo.cutover.bootstrap",
    }
    reads = {item for item in defaults | owner if item.endswith(".read")}
    reads |= {"todo.recovery.inspect", "todo.child.read", "todo.message.read"}
    coder = ((defaults | WORKFLOW_PARENT_ACTIONS) - CODER_EXCLUDED) | {
        "todo.authority.read", "todo.recovery.inspect",
    }
    return (tuple(sorted(StableId(item) for item in owner)),
            {"observer": tuple(sorted(StableId(item) for item in reads)),
             "investigator": tuple(sorted(StableId(item) for item in reads)),
             "skill_assembler": tuple(sorted(StableId(item) for item in reads)),
             "coder": tuple(sorted(StableId(item) for item in coder)),
             "mutator": tuple(sorted(StableId(item) for item in owner - {"todo.cutover.bootstrap"}))})


def _all_pages(session, method: str, *, import_id, expected_type):
    records = []
    cursor = None
    while True:
        page = getattr(session, method)(import_id=import_id, limit=500, after=cursor)
        if not isinstance(page.records, tuple):
            raise RuntimeError(f"public API returned malformed {method} page")
        records.extend(page.records)
        cursor = page.next_cursor
        if cursor is None:
            break
    if any(not isinstance(item, expected_type) for item in records):
        raise RuntimeError(f"public API returned malformed {method} record")
    return records


def prepare(args) -> dict:
    api = _todo_api()
    index_path = args.snapshot_index.resolve(strict=True)
    index = _read_json(index_path)
    records = _index_records(index, index_path)
    registry = _load_registry(args.config)
    indexed_keys = {(row["registered_workspace"], row["registered_repository"]) for row in records}
    configured_keys = {
        (workspace, repository)
        for workspace, value in registry.config.workspaces.items()
        for repository in value.repositories
    }
    missing = configured_keys - indexed_keys
    if missing != {("codex", "codex")}:
        raise ValueError("snapshot index must cover every configured Todo authority; only codex/codex may be new")
    fresh_root = registry.repository("codex", "codex").root
    if any((fresh_root / ".todo-orchestrator" / name).exists()
           for name in ("project.json", "authority.sqlite3", "authority.sqlite3-wal", "authority.sqlite3-shm")):
        raise ValueError("codex/codex already has native Todo metadata or authority")
    try:
        import subprocess
        common_text = subprocess.run(["git", "-C", str(fresh_root), "rev-parse", "--git-common-dir"],
                                     check=True, capture_output=True, text=True).stdout.strip()
        common = Path(common_text)
        if not common.is_absolute():
            common = (fresh_root / common).resolve()
        if (common / "todo-orchestrator").exists():
            raise ValueError("codex/codex already has legacy Todo state")
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("codex/codex must be a registered Git repository with no legacy Todo state") from error
    fresh_project_id = str(uuid.uuid5(
        ID_NAMESPACE, f"project-control.fresh:codex/codex:{fresh_root}",
    ))
    records.append({
        "registered_workspace": "codex", "registered_repository": "codex",
        "source_project_id": fresh_project_id, "authority_revision": None,
        "snapshot_digest": None, "_snapshot_path": None, "_snapshot": None,
        "_initialized_new": True,
    })
    destination = args.private_destination.expanduser().resolve(strict=False)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("private destination must be fresh and empty")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination, 0o700)
    for row in records:
        registered = registry.repository(row["registered_workspace"], row["registered_repository"])
        if destination == registered.root or destination in registered.root.parents or registered.root in destination.parents:
            raise ValueError("private destination must be separate from every registered repository")
    owner_actions, grants = _actions(api)
    StableId = api["StableId"]
    AuthenticatedPrincipal = api["AuthenticatedPrincipal"]
    ActorKind = api["ActorKind"]
    ProviderComposition = api["ProviderComposition"]
    prepared = []
    session_rows = []
    owner_files = []
    from todo_orchestrator.api import LegacyCurrentTask, LegacyHistoryRecord  # type: ignore

    for row in records:
        workspace, repository = row["registered_workspace"], row["registered_repository"]
        registered = registry.repository(workspace, repository)
        project_id = StableId(row["source_project_id"])
        authority_id = StableId(str(uuid.uuid5(ID_NAMESPACE, f"project-control.todo:{project_id}")))
        repository_id = StableId(repository)
        key = f"{workspace}--{repository}"
        project_root = destination / "prepared" / key
        if project_root.exists():
            raise ValueError(f"candidate project directory already exists: {key}")

        credentials = {}
        principal_values = {}
        for profile, principal in PRINCIPALS.items():
            credential = uuid.uuid4().hex + uuid.uuid4().hex
            principal_values[profile] = principal
            credentials[credential] = AuthenticatedPrincipal(principal, ActorKind.SERVICE, principal)
        providers = ProviderComposition(principal_authenticator=_BootstrapAuthenticator(credentials))
        owner_credential = uuid.uuid4().hex + uuid.uuid4().hex
        document = row["_snapshot"]
        source_tasks = [] if document is None else document["tables"]["tasks"]
        source_task_by_id = {task["id"]: task for task in source_tasks}
        if len(source_task_by_id) != len(source_tasks):
            raise ValueError(f"source snapshot has duplicate task IDs: {key}")
        with api["TodoApplication"].initialize(
            project_root, project_id=project_id, repository_id=repository_id,
            authority_id=authority_id, owner_credential=owner_credential,
            owner_actions=owner_actions, token_lifetime=timedelta(days=365), providers=providers,
        ) as session:
            if row["_initialized_new"]:
                receipt = None
                current = []
                history = []
            else:
                preview = session.preview_legacy_import(document)
                if preview.source_digest != row["snapshot_digest"]:
                    raise RuntimeError(f"public import preview digest differs from index for {key}")
                receipt = session.import_legacy_snapshot(
                    document, preview,
                    idempotency_key=StableId("cutover-import-" + hashlib.sha256(key.encode()).hexdigest()[:32]),
                )
                current = _all_pages(
                    session, "read_legacy_current_tasks", import_id=receipt.import_id,
                    expected_type=LegacyCurrentTask,
                )
                history = _all_pages(
                    session, "search_legacy_history", import_id=receipt.import_id,
                    expected_type=LegacyHistoryRecord,
                )
            if {item.source_task_id for item in current} != set(source_task_by_id) or len(current) != len(source_task_by_id):
                raise RuntimeError(f"imported task identity coverage mismatch for {key}")
            for item in current:
                source = source_task_by_id[item.source_task_id]
                if item.source_status != str(source.get("status", "unknown")):
                    raise RuntimeError(f"imported task status mismatch for {key}/{item.source_task_id}")
                expected_completed = source.get("status") == "done" and item.mapping_kind == "current"
                if item.completion_asserted != expected_completed:
                    raise RuntimeError(f"completion provenance mismatch for {key}/{item.source_task_id}")
            if not row["_initialized_new"]:
                if len(history) != len(document["history"]) or receipt.history_records != len(document["history"]):
                    raise RuntimeError(f"imported history count mismatch for {key}")
                expected_history = {
                    (item["source_table"], item["source_id"], item["entity_id"], item["revision"], item["event_type"])
                    for item in document["history"]
                }
                actual_history = {
                    (item.source_table, item.source_id, item.entity_id, item.source_revision, item.event_type)
                    for item in history
                }
                if actual_history != expected_history:
                    raise RuntimeError(f"imported history identity coverage mismatch for {key}")
            for profile, principal in PRINCIPALS.items():
                bootstrap_credential = next(token for token, ident in credentials.items()
                                            if ident.subject_id == principal)
                issued = session.delegate_principal(
                    bootstrap_credential,
                    scope=session.project.ref, actions=grants[profile],
                    expires_at=datetime.now(timezone.utc) + timedelta(days=30),
                    idempotency_key=StableId(f"cutover-session-{profile}-" + hashlib.sha256(key.encode()).hexdigest()[:24]),
                )
                capability_id = str(issued.capability.capability_id)
                session_rows.append({
                    "project_id": str(project_id), "authority_id": str(authority_id),
                    "principal_id": principal, "profile": profile,
                    "capability_id": capability_id,
                    "expires_at": issued.capability.expires_at.isoformat().replace("+00:00", "Z"),
                    "bearer_token": issued.bearer_token,
                })
            owner = {
                "project_id": str(project_id), "authority_id": str(authority_id),
                "owner_credential": owner_credential,
                # Retain this exact grant-family root privately so later
                # maintenance can revoke capabilities issued beneath it.
                "owner_bearer_token": session.bearer_token,
            }
            owner_file = destination / "owner-credentials" / f"{key}.json"
            _write_private(owner_file, _canonical(owner))
            owner_files.append(owner_file)
        authority_file = project_root / ".todo-orchestrator" / "authority.sqlite3"
        metadata_file = project_root / ".todo-orchestrator" / "project.json"
        prepared.append({
            "workspace": workspace, "repository": repository,
            "project_root": str(registered.root), "project_id": str(project_id),
            "repository_id": str(repository_id), "authority_id": str(authority_id),
            "source_project_id": row["source_project_id"],
            "initialized_new": bool(row["_initialized_new"]),
            "source_snapshot": None if row["_snapshot_path"] is None else str(row["_snapshot_path"]),
            "source_snapshot_digest": row["snapshot_digest"],
            "source_authority_revision": row["authority_revision"],
            "candidate_root": str(project_root),
            "candidate_metadata_sha256": _sha256(metadata_file),
            "candidate_authority_sha256": _sha256(authority_file),
            "candidate_tree_sha256": _tree_digest(project_root),
            "source_task_count": len(source_tasks),
            "imported_task_count": len(current),
            "source_history_count": 0 if document is None else len(document["history"]),
            "imported_history_count": len(history),
            "completion_assertion_count": sum(item.completion_asserted for item in current),
            "import_id": None if receipt is None else str(receipt.import_id),
        })

    token_doc = {"format": SESSION_FORMAT, "version": 1,
                 "sessions": session_rows, "attempts": [], "children": []}
    _write_private(destination / "session-tokens.json", _canonical(token_doc))
    manifest = {
        "format": FORMAT, "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "snapshot_index": str(index_path), "snapshot_index_sha256": _sha256(index_path),
        "private_destination": str(destination), "authority_count": len(prepared),
        "authorities": prepared,
        "secrets": {"session_tokens": "session-tokens.json",
                    "owner_credentials": [path.relative_to(destination).as_posix() for path in owner_files]},
    }
    manifest_path = destination / "prepared-manifest.json"
    _write_private(manifest_path, _canonical(manifest))
    return {"status": "prepared", "manifest": str(manifest_path),
            "authority_count": len(prepared), "session_token_file": str(destination / "session-tokens.json")}


def _verify_backup_coverage(backup_path: Path, authorities: list[dict]) -> None:
    backup = _read_json(backup_path)
    rows = backup.get("repositories")
    if not isinstance(rows, list):
        raise ValueError("backup manifest must have a repositories array")
    covered = {(item.get("workspace"), item.get("repository")): item for item in rows if isinstance(item, dict)}
    for authority in authorities:
        key = (authority["workspace"], authority["repository"])
        record = covered.get(key)
        if record is None:
            raise ValueError(f"backup manifest lacks {key[0]}/{key[1]}")
        if authority.get("initialized_new"):
            if record.get("push_status") not in {"verified", "local_only_authorized"} or not any(
                isinstance(worktree, dict) and worktree.get("status") == "snapshotted"
                for worktree in record.get("worktrees", [])
            ):
                raise ValueError(f"source repository backup is not verified for {key[0]}/{key[1]}")
            continue
        if record.get("push_status") != "verified":
            raise ValueError(f"source backup is not verified for {key[0]}/{key[1]}")
        state_value = record.get("authority_backup_path")
        state = Path(state_value) if isinstance(state_value, str) else (
            backup_path.parent / f"{key[0]}--{key[1]}" / "state.sqlite3"
        )
        if not state.is_absolute():
            state = backup_path.parent / state
        state = state.resolve(strict=False)
        if backup_path.parent.resolve() not in state.parents:
            raise ValueError(f"authority backup path escapes backup directory for {key[0]}/{key[1]}")
        expected = record.get("authority_backup_sha256")
        if not isinstance(expected, str) or not state.is_file() or _sha256(state) != expected:
            raise ValueError(f"authority backup digest is not verified for {key[0]}/{key[1]}")
        legacy_dir = record.get("legacy_state_dir")
        if not isinstance(legacy_dir, str) or not Path(legacy_dir).is_dir() or Path(legacy_dir).is_symlink():
            raise ValueError(f"legacy authority directory is unavailable for {key[0]}/{key[1]}")
        legacy_root = Path(legacy_dir).resolve(strict=True)
        common = _git_common_dir(Path(authority["project_root"]))
        if (legacy_root.parent != (common / "todo-orchestrator").resolve(strict=False)
                or legacy_root.name != authority["source_project_id"]):
            raise ValueError(f"legacy authority path does not match its registered repository for {key[0]}/{key[1]}")
        legacy_db = legacy_root / "state.sqlite3"
        source_doc = _read_json(Path(authority["source_snapshot"]))
        try:
            from project_control.legacy_todo_export import export_legacy_authority
            current = export_legacy_authority(
                legacy_db,
                repository_id=source_doc["project"].get("repository_id"),
                exported_at=source_doc["source"]["exported_at"],
            )
        except Exception as error:
            raise ValueError(f"cannot verify frozen source semantics for {key[0]}/{key[1]}") from error
        if current.get("snapshot_digest") != authority.get("source_snapshot_digest"):
            raise ValueError(f"legacy authority changed since its frozen snapshot for {key[0]}/{key[1]}")


def _git_common_dir(root: Path) -> Path:
    import subprocess
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-common-dir"],
                            check=True, capture_output=True, text=True)
    value = Path(result.stdout.strip())
    return (root / value).resolve() if not value.is_absolute() else value.resolve()


def _activation_marker_dir(private_destination: Path) -> Path:
    return private_destination / "activation-state"


def _activation_key(item: dict) -> str:
    return f"{item['workspace']}/{item['repository']}"


def _marker_path(private_destination: Path, item: dict) -> Path:
    safe = hashlib.sha256(_activation_key(item).encode("utf-8")).hexdigest()[:24]
    return _activation_marker_dir(private_destination) / f"{safe}.json"


def _activation_token_document(token_doc: dict, active_ids: set[tuple[str, str]],
                               previous_token_store: dict | None) -> dict:
    """Publish prepared sessions for active projects and retain runtime token records."""
    return {
        "format": SESSION_FORMAT,
        "version": 1,
        "sessions": [row for row in token_doc["sessions"]
                     if (row["project_id"], row["authority_id"]) in active_ids],
        "attempts": [] if previous_token_store is None else previous_token_store["attempts"],
        "children": [] if previous_token_store is None else previous_token_store["children"],
    }


def _read_activation_markers(private_destination: Path, authorities: list[dict]) -> set[tuple[str, str]]:
    active = set()
    manifest_digest = _sha256(private_destination / "prepared-manifest.json")
    for item in authorities:
        path = _marker_path(private_destination, item)
        if not path.exists():
            continue
        marker = _read_json(path)
        identity = (item["workspace"], item["repository"])
        if (marker.get("format") != "project-control.todo-cutover-activation"
                or marker.get("version") != 1
                or marker.get("prepared_manifest_sha256") != manifest_digest
                or (marker.get("workspace"), marker.get("repository")) != identity
                or (marker.get("project_id"), marker.get("authority_id")) !=
                   (item["project_id"], item["authority_id"])):
            raise ValueError(f"activation marker does not match prepared authority {_activation_key(item)}")
        target = Path(item["project_root"]) / ".todo-orchestrator"
        if not target.is_dir() or target.is_symlink():
            raise ValueError(f"activated authority is missing or unsafe: {_activation_key(item)}")
        metadata = _read_json(target / "project.json")
        if (metadata.get("project_id"), metadata.get("authority_id")) != (item["project_id"], item["authority_id"]):
            raise ValueError(f"activated authority identity changed: {_activation_key(item)}")
        active.add(identity)
    return active


def activate(args) -> dict:
    manifest_path = args.prepared_manifest.resolve(strict=True)
    manifest = _read_json(manifest_path)
    if manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
        raise ValueError("prepared manifest format is unsupported")
    authorities = manifest.get("authorities")
    if (not isinstance(authorities, list) or manifest.get("authority_count") != len(authorities)):
        raise ValueError("prepared manifest authority count is invalid")
    keys = [(item.get("workspace"), item.get("repository")) for item in authorities if isinstance(item, dict)]
    if len(keys) != len(authorities) or len(set(keys)) != len(keys):
        raise ValueError("prepared manifest contains malformed or duplicate project identities")
    if sum(bool(item.get("initialized_new")) for item in authorities) != 1 or ("codex", "codex") not in keys:
        raise ValueError("prepared manifest must contain exactly one fresh Codex authority")
    registry = _load_registry(args.config)
    configured_keys = {
        (workspace, repository)
        for workspace, value in registry.config.workspaces.items()
        for repository in value.repositories
    }
    if set(keys) != configured_keys:
        raise ValueError("prepared manifest does not cover every configured Todo authority")
    private_destination = Path(manifest.get("private_destination", "")).resolve(strict=True)
    if manifest_path.parent.resolve() != private_destination:
        raise ValueError("prepared manifest is outside its declared private destination")
    index_path = Path(manifest["snapshot_index"]).resolve(strict=True)
    if _sha256(index_path) != manifest.get("snapshot_index_sha256"):
        raise ValueError("snapshot index changed after preparation")
    index = _read_json(index_path)
    indexed = {(row.get("registered_workspace"), row.get("registered_repository")): row
               for row in index.get("current_exports", []) if isinstance(row, dict)}
    if set(keys) != set(indexed) | {("codex", "codex")}:
        raise ValueError("prepared manifest and current export index cover different authorities")
    for item in authorities:
        if item.get("initialized_new"):
            if (item["workspace"], item["repository"]) in indexed:
                raise ValueError("fresh Codex authority unexpectedly has a legacy snapshot")
        else:
            source = indexed.get((item["workspace"], item["repository"]))
            if source is None or source.get("snapshot_digest") != item["source_snapshot_digest"]:
                raise ValueError("prepared source snapshot no longer matches the authoritative index")
            source_path = (index_path.parent / source["snapshot_file"]).resolve(strict=True)
            document = _read_json(source_path)
            if document.get("snapshot_digest") != item["source_snapshot_digest"]:
                raise ValueError("source snapshot digest changed after preparation")
        registered = registry.repository(item["workspace"], item["repository"])
        if registered.root.resolve() != Path(item["project_root"]).resolve():
            raise ValueError("registered repository root changed after preparation")
        candidate_record_path = Path(item["candidate_root"])
        if candidate_record_path.is_symlink():
            raise ValueError("prepared candidate root must not be a symlink")
        candidate = candidate_record_path.resolve(strict=True)
        if private_destination not in candidate.parents:
            raise ValueError("prepared candidate escaped the private destination")
        if _tree_digest(candidate) != item["candidate_tree_sha256"]:
            raise ValueError(f"prepared candidate changed: {item['workspace']}/{item['repository']}")
        metadata = json.loads((candidate / ".todo-orchestrator" / "project.json").read_text(encoding="utf-8"))
        if (metadata.get("project_id"), metadata.get("authority_id")) != (item["project_id"], item["authority_id"]):
            raise ValueError("prepared metadata identity differs from manifest")
        state = candidate / ".todo-orchestrator" / "authority.sqlite3"
        if (_sha256(state) != item["candidate_authority_sha256"]
                or _sha256(candidate / ".todo-orchestrator" / "project.json") != item["candidate_metadata_sha256"]):
            raise ValueError("prepared authority or metadata digest differs from manifest")
    token_path_value = manifest_path.parent / manifest["secrets"]["session_tokens"]
    if token_path_value.is_symlink():
        raise ValueError("session token artifact must not be a symlink")
    token_path = token_path_value.resolve(strict=True)
    if manifest_path.parent.resolve() not in token_path.parents:
        raise ValueError("session token artifact escaped the private destination")
    if stat.S_IMODE(token_path.stat().st_mode) != 0o600:
        raise ValueError("session token file must remain private (0600)")
    token_doc = _read_json(token_path)
    if (token_doc.get("format") != SESSION_FORMAT or token_doc.get("version") != 1
            or set(token_doc) != {"format", "version", "sessions", "attempts", "children"}
            or token_doc.get("attempts") != [] or token_doc.get("children") != []):
        raise ValueError("session token artifact has unsupported structure")
    expected_sessions = {
        (item["project_id"], item["authority_id"], principal, profile)
        for item in manifest["authorities"] for profile, principal in PRINCIPALS.items()
    }
    actual_sessions = set()
    for session in token_doc["sessions"]:
        if (not isinstance(session, dict)
                or set(session) != {"project_id", "authority_id", "principal_id", "profile",
                                    "capability_id", "expires_at", "bearer_token"}
                or not isinstance(session.get("bearer_token"), str) or not session["bearer_token"]):
            raise ValueError("session token artifact contains an invalid entry")
        try:
            expiry = datetime.fromisoformat(session["expires_at"].replace("Z", "+00:00"))
        except (AttributeError, ValueError) as error:
            raise ValueError("session token expiry is invalid") from error
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError("session token has expired")
        identity = (session["project_id"], session["authority_id"], session["principal_id"], session["profile"])
        if identity in actual_sessions:
            raise ValueError("session token artifact contains a duplicate identity")
        actual_sessions.add(identity)
    if actual_sessions != expected_sessions:
        raise ValueError("session token artifact does not cover every prepared profile")
    owner_files = manifest.get("secrets", {}).get("owner_credentials")
    if not isinstance(owner_files, list) or len(owner_files) != len(manifest["authorities"]):
        raise ValueError("owner credential files are not listed for every authority")
    for relative in owner_files:
        owner_path = manifest_path.parent / relative
        if owner_path.is_symlink():
            raise ValueError("owner credential file must not be a symlink")
        path = owner_path.resolve(strict=True)
        if manifest_path.parent.resolve() not in path.parents or stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ValueError("owner credential file escaped private destination or is not private")
        credential = _read_json(path)
        if (set(credential) not in (
                    {"project_id", "authority_id", "owner_credential"},
                    {"project_id", "authority_id", "owner_credential", "owner_bearer_token"})
                or not isinstance(credential.get("owner_credential"), str)
                or not credential["owner_credential"]):
            raise ValueError("owner credential artifact is malformed")
        if "owner_bearer_token" in credential and (
                not isinstance(credential["owner_bearer_token"], str) or not credential["owner_bearer_token"]):
            raise ValueError("owner bearer credential artifact is malformed")
        matching = next((item for item in manifest["authorities"]
                         if (item["project_id"], item["authority_id"]) ==
                         (credential["project_id"], credential["authority_id"])), None)
        if matching is None:
            raise ValueError("owner credential identity does not match a prepared authority")
    token_store_path = args.session_token_store.expanduser().absolute()
    previous_token_store = _preflight_token_store(token_store_path)
    _verify_backup_coverage(args.backup_manifest.resolve(strict=True), manifest["authorities"])
    manifest_digest = _sha256(manifest_path)
    active_keys = _read_activation_markers(private_destination, authorities)
    requested = set(args.project or [])
    known = {f"{workspace}/{repository}" for workspace, repository in keys}
    unknown = requested - known
    if unknown:
        raise ValueError("unknown --project key(s): " + ", ".join(sorted(unknown)))
    selected = requested or (known - {f"{w}/{r}" for w, r in active_keys})
    if not selected:
        raise ValueError("no pending authority selected")
    operations = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for item in manifest["authorities"]:
        key = _activation_key(item)
        identity = (item["workspace"], item["repository"])
        if key not in selected:
            continue
        if identity in active_keys:
            # Explicitly selecting an already active authority is a harmless no-op;
            # its current native state is preserved and never replaced by its candidate.
            continue
        root = Path(item["project_root"])
        target = root / ".todo-orchestrator"
        if target.is_symlink():
            raise ValueError(f"existing Todo state is a symlink: {root}")
        common = _git_common_dir(root)
        archive = common / "todo-orchestrator-cutover" / stamp / f"{item['workspace']}--{item['repository']}" / "previous-native"
        backup = _read_json(args.backup_manifest.resolve(strict=True))
        backup_record = next(entry for entry in backup["repositories"]
                             if (entry.get("workspace"), entry.get("repository")) ==
                             (item["workspace"], item["repository"]))
        legacy_state_dir = None if item.get("initialized_new") else Path(backup_record["legacy_state_dir"])
        readonly_files = [] if legacy_state_dir is None else [path for path in (
            legacy_state_dir / "state.sqlite3", legacy_state_dir / "state.sqlite3-wal",
            legacy_state_dir / "state.sqlite3-shm", legacy_state_dir / "state.sqlite3-journal",
        ) if path.exists()]
        if legacy_state_dir is not None and any(path.is_symlink() or not path.is_file() for path in readonly_files):
            raise ValueError(f"legacy authority sidecar is unsafe: {item['workspace']}/{item['repository']}")
        candidate_state = Path(item["candidate_root"]) / ".todo-orchestrator"
        installed_recovery = False
        if target.exists():
            if not target.is_dir():
                raise ValueError(f"existing Todo path is not a directory: {target}")
            try:
                metadata = _read_json(target / "project.json")
            except Exception:
                metadata = {}
            same_identity = (metadata.get("project_id"), metadata.get("authority_id")) == (
                item["project_id"], item["authority_id"])
            if same_identity:
                if (_tree_digest(target) != _tree_digest(candidate_state)
                        or _sha256(target / "authority.sqlite3") != item["candidate_authority_sha256"]):
                    raise ValueError(f"unmarked native Todo state differs from candidate; refusing to overwrite: {key}")
                # An earlier apply may have installed the candidate and stopped before
                # writing its marker. Adopt it only while it remains byte-identical.
                installed_recovery = True
            elif item.get("initialized_new"):
                raise ValueError(f"fresh Codex Todo target appeared after preparation: {root}")
        operations.append({"workspace": item["workspace"], "repository": item["repository"],
                          "project_id": item["project_id"], "authority_id": item["authority_id"],
                          "target": str(target), "archive": str(archive),
                          "previous_native_tree_sha256": (_tree_digest(target) if target.is_dir()
                                                           and not installed_recovery else None),
                          "previous_native_modes": (_tree_modes(target) if target.is_dir()
                                                     and not installed_recovery else []),
                          "candidate": item["candidate_root"], "installed_recovery": installed_recovery,
                          "legacy_state_dir": None if legacy_state_dir is None else str(legacy_state_dir),
                          "legacy_files": [{"path": str(path), "mode": stat.S_IMODE(path.stat().st_mode)}
                                           for path in readonly_files],
                          "legacy_directory_mode": (None if legacy_state_dir is None
                                                     else stat.S_IMODE(legacy_state_dir.stat().st_mode))})
    result = {"status": "dry-run", "requires": ["--apply", "--writers-stopped"],
              "prepared_manifest": str(manifest_path), "authority_count": len(operations),
              "already_active": sorted(f"{w}/{r}" for w, r in active_keys),
              "pending_selection": sorted(selected),
              "session_token_store": str(token_store_path),
              "previous_session_token_store": previous_token_store is not None,
              "operations": operations}
    if not args.apply:
        return result
    if not args.writers_stopped:
        raise ValueError("activation requires --writers-stopped after the operator stops all writers")
    if not operations:
        active_ids = {(item["project_id"], item["authority_id"])
                      for item in authorities
                      if (item["workspace"], item["repository"]) in active_keys}
        token_bytes = _canonical(_activation_token_document(token_doc, active_ids, previous_token_store))
        _replace_private_file(token_store_path, token_bytes)
        return {**result, "status": "already-active", "authority_count": 0,
                "session_token_store_sha256": hashlib.sha256(token_bytes).hexdigest()}
    # Preflight every destination before the first mutation.
    for operation in operations:
        target, archive = Path(operation["target"]), Path(operation["archive"])
        if archive.exists():
            raise ValueError(f"rollback archive already exists: {archive}")
        if target.exists() and not target.is_dir():
            raise ValueError(f"existing Todo path is not a directory: {target}")
        if not target.exists() and Path(operation["candidate"]).stat().st_dev != target.parent.stat().st_dev:
            # Staging copies below make the final rename same-filesystem.
            pass
    journal = manifest_path.parent / f"activation-journal-{stamp}.json"
    previous_token_backup = None
    token_baseline = manifest_path.parent / "pre-cutover-session-store.json"
    if not token_baseline.exists():
        if previous_token_store is not None:
            previous_token_backup = manifest_path.parent / "pre-cutover-session-tokens.json"
            _write_private(previous_token_backup, token_store_path.read_bytes())
        _write_private(token_baseline, _canonical({
            "format": "project-control.todo-cutover-session-baseline", "version": 1,
            "existed": previous_token_store is not None,
            "backup": None if previous_token_backup is None else previous_token_backup.name,
            "sha256": None if previous_token_backup is None else _sha256(previous_token_backup),
        }))
    _write_private(journal, _canonical({"status": "activating", "operations": operations,
                                        "session_token_store": str(token_store_path),
                                        "previous_token_store_backup": None if previous_token_backup is None
                                        else str(previous_token_backup),
                                        "previous_token_store_sha256": None if previous_token_backup is None
                                        else _sha256(previous_token_backup)}))
    completed = []
    readonly_results = []
    for operation in operations:
        target = Path(operation["target"])
        archive = Path(operation["archive"])
        candidate_state = Path(operation["candidate"]) / ".todo-orchestrator"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not operation["installed_recovery"]:
            stage = target.parent / f".todo-orchestrator-cutover-stage-{stamp}"
            if stage.exists():
                raise ValueError(f"staging path already exists: {stage}")
            shutil.copytree(candidate_state, stage, copy_function=shutil.copy2)
            if _tree_digest(stage) != _tree_digest(candidate_state):
                shutil.rmtree(stage)
                raise RuntimeError("same-filesystem candidate staging copy failed verification")
            if target.exists():
                archive.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.replace(target, archive)
                if _tree_digest(archive) != operation["previous_native_tree_sha256"]:
                    raise RuntimeError("archived pre-cutover metadata differs from its recorded digest")
            os.replace(stage, target)
        if operation["legacy_state_dir"] is not None:
            for item in operation["legacy_files"]:
                path = Path(item["path"])
                os.chmod(path, item["mode"] & ~0o222)
            legacy_dir = Path(operation["legacy_state_dir"])
            os.chmod(legacy_dir, operation["legacy_directory_mode"] & ~0o222)
            readonly_results.append({"workspace": operation["workspace"],
                                     "repository": operation["repository"],
                                     "legacy_state_dir": str(legacy_dir), "read_only": True})
        marker = _marker_path(private_destination, operation)
        _write_private(marker, _canonical({
            "format": "project-control.todo-cutover-activation", "version": 1,
            "prepared_manifest_sha256": manifest_digest,
            "workspace": operation["workspace"], "repository": operation["repository"],
            "project_id": operation["project_id"], "authority_id": operation["authority_id"],
            "installed_tree_sha256": _tree_digest(target),
            "activated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }))
        completed.append({"workspace": operation["workspace"], "repository": operation["repository"],
                          "archive": str(archive) if archive.exists() else None,
                          "previous_native_tree_sha256": operation["previous_native_tree_sha256"],
                          "installed_tree_sha256": _tree_digest(target),
                          "installed_recovery": operation["installed_recovery"],
                          "legacy_read_only": bool(readonly_results)})
        _write_private(journal, _canonical({"status": "activating", "completed": completed,
                                            "remaining": operations[len(completed):]}))
    active_after = _read_activation_markers(private_destination, authorities)
    active_ids = {(item["project_id"], item["authority_id"])
                  for item in authorities
                  if (item["workspace"], item["repository"]) in active_after}
    token_bytes = _canonical(_activation_token_document(token_doc, active_ids, previous_token_store))
    _replace_private_file(token_store_path, token_bytes)
    _write_private(journal, _canonical({"status": "activated", "completed": completed,
                                        "legacy_read_only": readonly_results,
                                        "session_token_store": str(token_store_path),
                                        "session_token_store_sha256": hashlib.sha256(token_bytes).hexdigest(),
                                        "previous_token_store_backup": None if previous_token_backup is None
                                        else str(previous_token_backup)}))
    return {"status": "activated", "journal": str(journal), "authority_count": len(completed)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--config", type=Path)
    prep.add_argument("--snapshot-index", required=True, type=Path)
    prep.add_argument("--private-destination", required=True, type=Path)
    act = sub.add_parser("activate")
    act.add_argument("--config", type=Path)
    act.add_argument("--prepared-manifest", required=True, type=Path)
    act.add_argument("--backup-manifest", required=True, type=Path)
    act.add_argument("--project", action="append", default=[], metavar="WORKSPACE/REPOSITORY",
                     help="activate only this authority; repeatable; default is every not-yet-active authority")
    act.add_argument("--session-token-store", type=Path,
                     default=Path("~/.config/project-control/todo-orchestrator/session-tokens.json"))
    act.add_argument("--apply", action="store_true", help="perform activation; default is preflight only")
    act.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = prepare(args) if args.command == "prepare" else activate(args)
    except Exception as error:
        print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
