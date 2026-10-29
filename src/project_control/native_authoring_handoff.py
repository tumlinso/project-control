"""Private durable host records for native attempt credential and authoring handoff.

These records are host runtime state. They are never written into the Todo
authority and are exposed only through trusted provider objects.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile
from typing import Any

from .native_providers import (
    NativeProviderUnavailable,
    _attempt_lock_path,
    _attempt_token,
    _ensure_private_directory,
    _locked_path,
)


_CREDENTIAL_FORMAT = "project-control.todo-attempt-credential"
_CREDENTIAL_VERSION = 1
_HANDOFF_FORMAT = "project-control.todo-authoring-handoff"
_HANDOFF_VERSION = 1
_MAX_RECORD_BYTES = 128 * 1024
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, field: str, *, maximum: int = 256) -> str:
    if value is None:
        raise NativeProviderUnavailable(f"{field} is invalid")
    raw = str(value)
    if not raw or raw != raw.strip() or len(raw.encode("utf-8")) > maximum:
        raise NativeProviderUnavailable(f"{field} is invalid")
    return raw


def _digest(value: object) -> str:
    raw = getattr(value, "hex", value)
    if not isinstance(raw, str) or not _DIGEST.fullmatch(raw):
        raise NativeProviderUnavailable("native Todo intent digest is invalid")
    return raw


def _record_path(root: Path, identity: str) -> Path:
    return root / f"{hashlib.sha256(identity.encode('utf-8')).hexdigest()}.json"


def _read_record(path: Path, *, format_name: str, version: int,
                 fields: frozenset[str]) -> dict[str, Any] | None:
    if path.is_symlink():
        raise NativeProviderUnavailable("native Todo host record must not be a symlink")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise NativeProviderUnavailable("native Todo host record could not be opened") from error
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > _MAX_RECORD_BYTES):
            raise NativeProviderUnavailable("native Todo host record has unsafe ownership or permissions")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            value = json.load(stream)
        if (not isinstance(value, dict) or value.get("format") != format_name
                or value.get("version") != version or set(value) != fields):
            raise NativeProviderUnavailable("native Todo host record is malformed")
        return value
    except NativeProviderUnavailable:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise NativeProviderUnavailable("native Todo host record is unreadable") from error
    finally:
        if fd >= 0:
            os.close(fd)


def _write_record(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_RECORD_BYTES:
        raise NativeProviderUnavailable("native Todo host record exceeds its size limit")
    _ensure_private_directory(path.parent)
    fd, temporary_name = tempfile.mkstemp(prefix=".native-record-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _request_id(request: object) -> tuple[str, ...]:
    names = ("project_id", "authority_id", "attempt_id", "capability_id", "idempotency_key")
    values = tuple(_text(getattr(request, name, None), name) for name in names)
    return values + (_digest(getattr(request, "intent_digest", None)),)


class NativeAttemptCredentialProvider:
    """Durably prepare an attempt bearer before native dispatch can commit."""

    def __init__(self, project_id: str, authority_id: str, root: Path) -> None:
        self.project_id = _text(project_id, "project_id")
        self.authority_id = _text(authority_id, "authority_id")
        self.root = Path(root)

    def prepare_attempt_credential(self, request: object) -> str:
        try:
            identity = _request_id(request)
            project_id, authority_id, attempt_id, capability_id, idempotency_key, intent_digest = identity
            if (project_id, authority_id) != (self.project_id, self.authority_id):
                raise NativeProviderUnavailable("attempt credential request has a different project authority")
            _ensure_private_directory(self.root)
            record_path = _record_path(self.root, f"{attempt_id}\0{capability_id}")
            with _locked_path(record_path.with_suffix(".lock")):
                prior = _read_record(
                    record_path, format_name=_CREDENTIAL_FORMAT, version=_CREDENTIAL_VERSION,
                    fields=frozenset({"format", "version", "project_id", "authority_id", "attempt_id",
                                      "capability_id", "idempotency_key", "intent_digest", "bearer_token"}),
                )
                if prior is not None:
                    if tuple(prior[name] for name in (
                        "project_id", "authority_id", "attempt_id", "capability_id",
                        "idempotency_key", "intent_digest",
                    )) != identity:
                        raise NativeProviderUnavailable("attempt credential identity conflicts with its durable record")
                    token = prior.get("bearer_token")
                    if not isinstance(token, str) or not token:
                        raise NativeProviderUnavailable("durable attempt credential is invalid")
                    return token
                token = secrets.token_urlsafe(32)
                _write_record(record_path, {
                    "format": _CREDENTIAL_FORMAT, "version": _CREDENTIAL_VERSION,
                    "project_id": project_id, "authority_id": authority_id,
                    "attempt_id": attempt_id, "capability_id": capability_id,
                    "idempotency_key": idempotency_key, "intent_digest": intent_digest,
                    "bearer_token": token,
                })
                return token
        except NativeProviderUnavailable:
            raise
        except Exception as error:
            raise NativeProviderUnavailable("attempt credential could not be prepared durably") from error


class NativeAuthoringHandoffProvider:
    """Pin and revalidate an exact authored commit without asserting process exit."""

    def __init__(self, git_provider: object, *, project_id: str, authority_id: str,
                 project_root: Path, record_root: Path, host_resources: object, command_journal_root: Path,
                 credential_principal: str, credential_profile: str) -> None:
        self.git = git_provider
        self.project_id = project_id
        self.authority_id = authority_id
        self.project_root = Path(project_root).resolve(strict=True)
        self.record_root = Path(record_root)
        self.host_resources = host_resources
        self.command_journal_root = Path(command_journal_root)
        self.principal = credential_principal
        self.profile = credential_profile

    @staticmethod
    def _stable_id(value: object):
        from todo_orchestrator.contracts import StableId

        return value if isinstance(value, StableId) else StableId(str(value))

    @staticmethod
    def _receipt_value(receipt: object) -> dict[str, object]:
        return {
            "receipt_id": str(receipt.receipt_id),
            "provider_id": str(receipt.provider.provider_id),
            "provider_version": receipt.provider.version,
            "provider_contract_version": receipt.provider.contract_version,
            "correlation_id": str(receipt.correlation_id),
            "request_digest": receipt.request_digest.hex,
            "provenance_ref": str(receipt.provenance_ref),
            "observed_at": receipt.observed_at.isoformat(timespec="microseconds"),
        }

    @staticmethod
    def _receipt_from(value: dict[str, object]):
        from datetime import datetime
        from todo_orchestrator.contracts import ProviderIdentity, ProviderReceipt, Sha256Digest, StableId

        return ProviderReceipt(
            StableId(str(value["receipt_id"])),
            ProviderIdentity(StableId(str(value["provider_id"])), str(value["provider_version"]),
                             int(value["provider_contract_version"])),
            StableId(str(value["correlation_id"])), Sha256Digest(str(value["request_digest"])),
            StableId(str(value["provenance_ref"])), datetime.fromisoformat(str(value["observed_at"])),
        )

    def _ids(self, request: object) -> tuple[object, str, str, str, str, str]:
        from todo_orchestrator.contracts import EntityKind

        project_id = _text(getattr(request, "project_id", None), "project_id")
        authority_id = _text(getattr(request, "authority_id", None), "authority_id")
        attempt = getattr(request, "attempt", None)
        if (getattr(attempt, "kind", None) is not EntityKind.ATTEMPT
                or str(getattr(attempt, "project_id", "")) != self.project_id):
            raise NativeProviderUnavailable("authoring handoff attempt is outside the registered project")
        attempt_id = _text(getattr(attempt, "entity_id", None), "attempt_id")
        lease_id = _text(getattr(request, "lease_id", None), "lease_id")
        workspace_id = _text(getattr(request, "workspace_id", None), "workspace_id")
        idempotency_key = _text(getattr(request, "idempotency_key", None), "idempotency_key")
        if (project_id, authority_id) != (self.project_id, self.authority_id):
            raise NativeProviderUnavailable("authoring handoff request has a different project authority")
        return attempt, attempt_id, lease_id, workspace_id, idempotency_key, project_id

    def _make_proof(self, request: object, *, attempt: object, attempt_id: str,
                    lease_id: str, workspace_id: str, idempotency_key: str,
                    commit: str, tree: str, source_digest: object):
        from datetime import datetime, timezone
        from todo_orchestrator.contracts import ProviderReceipt, Sha256Digest, StableId
        from todo_orchestrator.model.attempts import AttemptAuthoringHandoff, authoring_handoff_request_digest

        commit_id, tree_id = StableId(commit), StableId(tree)
        digest = source_digest if isinstance(source_digest, Sha256Digest) else Sha256Digest(str(source_digest))
        handoff_digest = authoring_handoff_request_digest(request, commit_id, tree_id, digest)
        identity = self.git.identity
        receipt = ProviderReceipt(
            StableId("pc-handoff-" + hashlib.sha256(
                f"{attempt_id}\0{handoff_digest.hex}".encode("utf-8")
            ).hexdigest()),
            identity,
            StableId(attempt_id),
            handoff_digest,
            commit_id,
            datetime.now(timezone.utc),
        )
        return AttemptAuthoringHandoff(request, commit_id, tree_id, digest, receipt)

    def _workspace_snapshot(self, workspace_id: object) -> tuple[Path, str, str, object]:
        from todo_orchestrator.model import SourceSafety

        stable_workspace = self._stable_id(workspace_id)
        workspace = self.git.workspace_path_for(stable_workspace)
        branch = self.git._workspace_branch(stable_workspace)
        _common, head, _index = self.git._assert_worktree(workspace, branch)
        files, index, safety = self.git._path_state(workspace, head)
        if safety is not SourceSafety.CLEAN:
            raise NativeProviderUnavailable("authoring handoff requires a clean committed workspace")
        digest = self.git._source_digest(files, index)
        tree_digest = self.git._tree_digest(head)
        if digest != tree_digest:
            raise NativeProviderUnavailable("authoring handoff source digest differs from its immutable commit")
        tree = self.git._git(["rev-parse", "--verify", f"{head}^{{tree}}"])
        tree_oid = os.fsdecode(tree).strip()
        if not tree_oid or not all(char in "0123456789abcdef" for char in tree_oid):
            raise NativeProviderUnavailable("authoring handoff commit tree is unavailable")
        return workspace, head, tree_oid, digest

    def _pin(self, attempt_id: str, commit: str) -> str:
        reference = "refs/todo-orchestrator/authoring-handoffs/" + _attempt_token(attempt_id)
        zero = "0" * len(commit)
        try:
            result = subprocess.run(
                ["git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
                 "-C", str(self.git.config.repository_root), "update-ref", reference, commit, zero],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=self.git._environment(), timeout=self.git.config.command_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise NativeProviderUnavailable("authoring handoff source pin could not be created") from error
        if result.returncode != 0:
            observed = os.fsdecode(self.git._git(["rev-parse", "--verify", reference], check=False)).strip()
        else:
            observed = os.fsdecode(self.git._git(["rev-parse", "--verify", reference])).strip()
        if observed != commit:
            # An exact retry may find the immutable ref already installed.
            raise NativeProviderUnavailable("authoring handoff source pin conflicts with another source")
        return reference

    def _record(self, request: object, *, attempt: object, attempt_id: str, lease_id: str,
                workspace_id: str, idempotency_key: str, commit: str, tree: str,
                digest: object, ref: str, proof: object) -> dict[str, Any]:
        from todo_orchestrator.contracts.providers import provider_request_digest

        return {
            "format": _HANDOFF_FORMAT, "version": _HANDOFF_VERSION,
            "project_id": self.project_id, "authority_id": self.authority_id,
            "attempt_id": attempt_id, "lease_id": lease_id, "workspace_id": workspace_id,
            "idempotency_key": idempotency_key,
            "request_digest": provider_request_digest(request).hex,
            "source_commit": commit, "source_tree": tree, "source_digest": digest.hex,
            "source_ref": ref, "receipt": self._receipt_value(proof.receipt),
        }

    def handoff_authoring(self, request: object):
        from todo_orchestrator.model.attempts import AttemptAuthoringHandoffRequest

        if not isinstance(request, AttemptAuthoringHandoffRequest):
            raise NativeProviderUnavailable("authoring handoff requires its typed request")
        attempt, attempt_id, lease_id, workspace_id, idempotency_key, _project_id = self._ids(request)
        fields = frozenset({"format", "version", "project_id", "authority_id", "attempt_id", "lease_id",
                            "workspace_id", "idempotency_key", "request_digest", "source_commit",
                            "source_tree", "source_digest", "source_ref", "receipt"})
        record_path = _record_path(self.record_root, attempt_id)
        _ensure_private_directory(self.record_root)
        from todo_orchestrator.contracts.providers import provider_request_digest

        # Serialize handoff with authored command starts on the same lock used
        # by command execution. This closes the check-then-fence race.
        with _locked_path(_attempt_lock_path(self.command_journal_root, attempt_id)):
            with _locked_path(record_path.with_suffix(".lock")):
                prior = _read_record(record_path, format_name=_HANDOFF_FORMAT,
                                     version=_HANDOFF_VERSION, fields=fields)
                if prior is not None:
                    identity_fields = ("project_id", "authority_id", "attempt_id", "lease_id", "workspace_id",
                                       "idempotency_key", "request_digest")
                    expected = (self.project_id, self.authority_id, attempt_id, lease_id, workspace_id,
                                idempotency_key, provider_request_digest(request).hex)
                    if tuple(prior.get(name) for name in identity_fields) != expected:
                        raise NativeProviderUnavailable("authoring handoff conflicts with its durable record")
                    proof = self._proof_from_record(prior, attempt)
                    if self._validate_record(prior, proof, attempt_id=attempt_id,
                                             lease_id=lease_id, workspace_id=workspace_id):
                        return proof
                    raise NativeProviderUnavailable("authoring handoff source changed after it was pinned")

                self._require_quiescent_host(attempt, attempt_id)
                _workspace, commit, tree, source_digest = self._workspace_snapshot(workspace_id)
                reference = self._pin(attempt_id, commit)
                # Re-read after pinning. The marker is published only for the same
                # exact immutable source still present in the managed workspace.
                _workspace, current_commit, current_tree, current_digest = self._workspace_snapshot(workspace_id)
                if (current_commit, current_tree, current_digest) != (commit, tree, source_digest):
                    raise NativeProviderUnavailable("authoring handoff workspace changed while it was being pinned")
                proof = self._make_proof(
                    request, attempt=attempt, attempt_id=attempt_id, lease_id=lease_id,
                    workspace_id=workspace_id, idempotency_key=idempotency_key,
                    commit=commit, tree=tree, source_digest=source_digest,
                )
                record = self._record(
                    request, attempt=attempt, attempt_id=attempt_id, lease_id=lease_id,
                    workspace_id=workspace_id, idempotency_key=idempotency_key,
                    commit=commit, tree=tree, digest=source_digest, ref=reference, proof=proof,
                )
                _write_record(record_path, record)
                return proof

    def _require_quiescent_host(self, attempt: object, attempt_id: str) -> None:
        from todo_orchestrator.model import ChildActivity
        from .native_providers import _command_journal_clear

        if not _command_journal_clear(self.command_journal_root, attempt_id):
            raise NativeProviderUnavailable("unresolved authored command effects prevent handoff")
        owners_for = getattr(self.host_resources, "owners_for_attempt", None)
        if not callable(owners_for):
            raise NativeProviderUnavailable("host resource ownership is unavailable for handoff")
        rows = owners_for(self.project_root, attempt_id)
        if not isinstance(rows, list) or rows:
            raise NativeProviderUnavailable("active or unknown host resource ownership prevents handoff")
        child_observer = getattr(self.git, "child_observer", None)
        inspect = getattr(child_observer, "inspect", None)
        if not hasattr(attempt, "ref"):
            from types import SimpleNamespace

            attempt = SimpleNamespace(project_id=attempt.project_id, ref=attempt)
        if not callable(inspect) or inspect(attempt) is not ChildActivity.NONE:
            raise NativeProviderUnavailable("active or unknown child execution prevents handoff")

    def _proof_from_record(self, record: dict[str, Any], attempt: object):
        from todo_orchestrator.contracts import Sha256Digest, StableId
        from todo_orchestrator.model.attempts import AttemptAuthoringHandoff, AttemptAuthoringHandoffRequest

        request = AttemptAuthoringHandoffRequest(
            StableId(record["project_id"]), StableId(record["authority_id"]), attempt,
            StableId(record["lease_id"]), StableId(record["workspace_id"]),
            StableId(record["idempotency_key"]),
        )
        return AttemptAuthoringHandoff(
            request, StableId(record["source_commit"]), StableId(record["source_tree"]),
            Sha256Digest(record["source_digest"]), self._receipt_from(record["receipt"]),
        )

    def _validate_record(self, record: dict[str, Any], proof: object, *,
                         attempt_id: str, lease_id: str, workspace_id: str) -> bool:
        try:
            if (record.get("attempt_id"), record.get("lease_id"), record.get("workspace_id")) != (
                attempt_id, lease_id, workspace_id,
            ):
                return False
            from todo_orchestrator.contracts.providers import provider_request_digest

            request = proof.request
            if (str(request.project_id) != self.project_id
                    or str(request.authority_id) != self.authority_id
                    or str(request.attempt.entity_id) != attempt_id
                    or str(request.lease_id) != lease_id
                    or str(request.workspace_id) != workspace_id
                    or str(request.idempotency_key) != record.get("idempotency_key")
                    or provider_request_digest(request).hex != record.get("request_digest")):
                return False
            ref = record.get("source_ref")
            expected_ref = ("refs/todo-orchestrator/authoring-handoffs/"
                            + _attempt_token(attempt_id))
            if ref != expected_ref:
                return False
            pinned = os.fsdecode(self.git._git(["rev-parse", "--verify", ref], check=False)).strip()
            workspace, current, tree, digest = self._workspace_snapshot(workspace_id)
            if (pinned != record.get("source_commit") or current != record.get("source_commit")
                    or tree != record.get("source_tree") or digest.hex != record.get("source_digest")):
                return False
            proof_receipt = self._receipt_value(proof.receipt)
            return (proof_receipt == record.get("receipt")
                    and str(proof.source_commit) == current
                    and str(proof.source_tree) == tree
                    and proof.source_digest.hex == digest.hex)
        except Exception:
            return False

    def authoring_handoff_for(self, attempt: object):
        """Return only a durable, freshly revalidated typed handoff proof."""
        from todo_orchestrator.contracts import EntityKind

        ref = getattr(attempt, "ref", None)
        if getattr(ref, "kind", None) is not EntityKind.ATTEMPT:
            return None
        attempt_id = str(ref.entity_id)
        path = _record_path(self.record_root, attempt_id)
        fields = frozenset({"format", "version", "project_id", "authority_id", "attempt_id", "lease_id",
                            "workspace_id", "idempotency_key", "request_digest", "source_commit",
                            "source_tree", "source_digest", "source_ref", "receipt"})
        try:
            with _locked_path(path.with_suffix(".lock")):
                record = _read_record(path, format_name=_HANDOFF_FORMAT,
                                      version=_HANDOFF_VERSION, fields=fields)
            if (record is None or record.get("project_id") != self.project_id
                    or record.get("authority_id") != self.authority_id):
                return None
            lease = getattr(attempt, "lease", None)
            if lease is None:
                from todo_orchestrator.contracts import AttemptState

                if getattr(attempt, "state", None) is not AttemptState.ACCEPTED:
                    return None
                # Acceptance removes the worker lease, not the durable source
                # fence. Revalidate no authored commands/resources/children;
                # this does not revive execution or assert a stopped process.
                self._require_quiescent_host(attempt, attempt_id)
                lease_id = str(record["lease_id"])
            else:
                lease_id = str(lease.lease_id)
                if record.get("lease_id") != lease_id:
                    return None
            workspace_id = record["workspace_id"]
            proof = self._proof_from_record(record, ref)
            return proof if self._validate_record(
                record, proof, attempt_id=attempt_id, lease_id=lease_id,
                workspace_id=workspace_id,
            ) else None
        except Exception:
            return None

    def has_handoff_marker(self, attempt_id: object) -> bool:
        """Fail closed on any durable handoff marker, even if its proof is stale."""
        path = _record_path(self.record_root, str(attempt_id))
        fields = frozenset({"format", "version", "project_id", "authority_id", "attempt_id", "lease_id",
                            "workspace_id", "idempotency_key", "request_digest", "source_commit",
                            "source_tree", "source_digest", "source_ref", "receipt"})
        try:
            _ensure_private_directory(self.record_root)
            with _locked_path(path.with_suffix(".lock")):
                return _read_record(path, format_name=_HANDOFF_FORMAT,
                                    version=_HANDOFF_VERSION, fields=fields) is not None
        except Exception:
            # An unreadable marker directory cannot authorize new effects.
            return True


__all__ = ["NativeAttemptCredentialProvider", "NativeAuthoringHandoffProvider"]
