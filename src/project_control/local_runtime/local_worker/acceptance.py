from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
import fcntl
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from .verification import VerificationError, require_verification
from .workspace import WritableWorkspace, normalize_scopes


class AcceptanceError(RuntimeError):
    pass


class ScopeViolation(AcceptanceError):
    pass


class StaleSourceError(AcceptanceError):
    pass


class PatchConflict(AcceptanceError):
    pass


def _runtime_source():
    from .canonical_runtime import bind
    bind(Path.cwd())
    from todo_orchestrator.runtime import capture_source_identity
    return capture_source_identity


def _git(root: Path, *args: str, input_data: bytes | None = None,
         env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.run(["git", *args], cwd=root, input=input_data, env=env,
                             capture_output=True, check=False)
    if check and process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise AcceptanceError(f"git {' '.join(args)} failed: {detail[:500]}")
    return process


def _inside(path: str, scope: str) -> bool:
    return path == scope or path.startswith(scope + "/")


def build_patch_artifact(workspace: WritableWorkspace, artifact_dir: str | Path,
                         external_verification: dict[str, Any]) -> dict[str, Any]:
    if not external_verification.get("ok") or not external_verification.get("results"):
        raise AcceptanceError("current workspace requires successful external verification")
    environment = workspace.index_environment
    _git(workspace.path, "add", "-A", "--", ".", env=environment)
    changed_raw = _git(
        workspace.path, "diff", "--cached", "--name-only", "-z", workspace.baseline_tree,
        env=environment,
    ).stdout
    changed = sorted({item.decode("utf-8", errors="surrogateescape") for item in changed_raw.split(b"\0") if item})
    denied = [path for path in changed if not any(_inside(path, scope) for scope in workspace.write_scopes)]
    if denied:
        raise ScopeViolation(f"worker changed paths outside its write scopes: {denied}")
    patch = _git(
        workspace.path, "diff", "--cached", "--binary", "--full-index", workspace.baseline_tree,
        env=environment,
    ).stdout
    if not patch or not changed:
        raise AcceptanceError("worker produced no patch")
    destination = Path(artifact_dir).resolve()
    common_raw = _git(workspace.primary_root, "rev-parse", "--git-common-dir").stdout.decode().strip()
    common = Path(common_raw) if Path(common_raw).is_absolute() else workspace.primary_root / common_raw
    common = common.resolve()
    in_common = destination == common or common in destination.parents
    if (destination == workspace.path or workspace.path in destination.parents
            or (destination == workspace.primary_root or workspace.primary_root in destination.parents) and not in_common):
        raise AcceptanceError("patch artifacts must live outside primary and detached worktrees")
    destination.mkdir(parents=True, exist_ok=True)
    artifact_id = str(uuid.uuid4())
    patch_path = destination / f"{artifact_id}.patch"
    metadata_path = destination / f"{artifact_id}.json"
    patch_path.write_bytes(patch)
    patch_hash = hashlib.sha256(patch).hexdigest()
    metadata = {
        "format": "LOCAL-WORKER-PATCH/1",
        "schema_version": 1,
        "artifact_id": artifact_id,
        "patch": {"schema_version": 1, "artifact_id": artifact_id, "kind": "patch",
                  "path": str(patch_path), "content_hash": patch_hash, "complete": True},
        "metadata_path": str(metadata_path),
        "baseline_source_identity": workspace.source_identity,
        "write_scopes": workspace.write_scopes,
        "changed_paths": changed,
        "external_verification": external_verification,
    }
    metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return metadata


def accept_patch_artifact(repo_root: str | Path, artifact: dict[str, Any],
                          current_source_commands: list[object], *,
                          result_recorder: Any | None = None) -> dict[str, Any]:
    primary = Path(repo_root).resolve()
    with _acceptance_locks(primary, artifact.get("write_scopes") or []):
        return _accept_patch_locked(primary, artifact, current_source_commands, result_recorder=result_recorder)


@contextmanager
def _acceptance_locks(primary: Path, scopes: list[str]):
    common = _git(primary, "rev-parse", "--git-common-dir").stdout.decode().strip()
    root = Path(common) if Path(common).is_absolute() else primary / common
    lock_root = root.resolve() / "local-coding-worker/locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    names = ["acceptance", *["scope-" + hashlib.sha256(item.encode()).hexdigest() for item in normalize_scopes(scopes)]]
    with ExitStack() as stack:
        streams = [stack.enter_context((lock_root / f"{name}.lock").open("a+b")) for name in sorted(names)]
        for stream in streams: fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try: yield
        finally:
            for stream in reversed(streams): fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _accept_patch_locked(primary: Path, artifact: dict[str, Any], current_source_commands: list[object], *,
                         result_recorder: Any | None = None) -> dict[str, Any]:
    capture_source_identity = _runtime_source()
    expected = artifact.get("baseline_source_identity") or {}
    current = capture_source_identity(primary)
    if current.get("fingerprint") != expected.get("fingerprint") or current.get("git_head") != expected.get("git_head"):
        raise StaleSourceError("primary source changed after worker materialization")
    scopes = normalize_scopes(artifact.get("write_scopes") or [])
    changed = artifact.get("changed_paths")
    if not isinstance(changed, list) or any(not isinstance(path, str) for path in changed):
        raise AcceptanceError("patch artifact changed_paths are invalid")
    denied = [path for path in changed if not any(_inside(path, scope) for scope in scopes)]
    if denied:
        raise ScopeViolation(f"patch artifact exceeds its write scopes: {denied}")
    patch_info = artifact.get("patch") or {}
    patch_path = Path(str(patch_info.get("path", ""))).resolve()
    if not patch_path.is_file():
        raise AcceptanceError("patch artifact is missing")
    patch = patch_path.read_bytes()
    if hashlib.sha256(patch).hexdigest() != patch_info.get("content_hash"):
        raise AcceptanceError("patch artifact hash mismatch")
    checked = _git(primary, "apply", "--check", "--binary", "-", input_data=patch, check=False)
    if checked.returncode != 0:
        raise PatchConflict(checked.stderr.decode("utf-8", errors="replace").strip()[:500])
    _git(primary, "apply", "--binary", "-", input_data=patch)
    try:
        verification = require_verification(primary, current_source_commands, phase="current-source-acceptance")
    except VerificationError as error:
        reversed_patch = _git(primary, "apply", "--reverse", "--check", "--binary", "-", input_data=patch, check=False)
        if reversed_patch.returncode != 0:
            raise AcceptanceError("acceptance gate failed and patch rollback is unsafe") from error
        _git(primary, "apply", "--reverse", "--binary", "-", input_data=patch)
        raise AcceptanceError("current-source acceptance gate failed; patch was reversed") from error
    result = {
        "format": "LOCAL-WORKER-ACCEPTANCE/1",
        "accepted": True,
        "artifact_id": artifact.get("artifact_id"),
        "changed_paths": changed,
        "source_identity_before": current,
        "source_identity_after": capture_source_identity(primary),
        "verification": verification,
        "parent_task_completed": False,
    }
    if result_recorder is not None:
        try:
            recorded = result_recorder(result)
            if recorded is False:
                raise AcceptanceError("result recorder declined the accepted patch")
        except Exception as error:
            reversed_patch = _git(primary, "apply", "--reverse", "--check", "--binary", "-", input_data=patch, check=False)
            if reversed_patch.returncode != 0:
                raise AcceptanceError("result recording failed and patch rollback is unsafe") from error
            _git(primary, "apply", "--reverse", "--binary", "-", input_data=patch)
            raise AcceptanceError("result recording failed; accepted patch was reversed") from error
        result["result_recorded"] = True
    return result
