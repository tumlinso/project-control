"""Trusted host provider composition for the standalone Todo engine.

Project Control selects repository roots and principals. This module supplies
the host-owned, bounded effects that Todo's public provider ports require; it
never reads or writes Todo's private authority database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from typing import Any, Callable

from .registry import WorkspaceRegistry


class NativeProviderUnavailable(RuntimeError):
    """A trusted Project Control provider could not be composed safely."""


_COMMAND_FORMAT = "project-control.todo-command-execution"
_COMMAND_VERSION = 1
_MAX_JOURNAL_BYTES = 1024 * 1024
_MAX_COMMAND_TIMEOUT_MS = 24 * 60 * 60 * 1000
_COMMAND_SUPERVISOR_CLEANUP_MS = 5000
_COMMAND_SUPERVISOR_PROTOCOL = "project-control.todo-command-supervisor"
_COMMAND_SUPERVISOR_VERSION = 1
_EXTERNAL_ATTESTATION_FORMAT = "project-control.todo-external-producer-attestation"
_EXTERNAL_ATTESTATION_VERSION = 1
_EXTERNAL_ATTESTATION_TTL_SECONDS = 24 * 60 * 60
_EXTERNAL_ATTESTATION_FIELDS = frozenset({
    "format", "version", "project_id", "project_root", "attempt_id", "actor", "target",
    "lease_id", "lease_expires_at", "workspace_id", "base_ref", "base_digest", "source_safety",
    "source_digest",
    "producer_id", "lifecycle_source", "lifecycle_state", "attested_by", "host_receipt",
    "observed_at", "expires_at", "attestation_id",
})


def _process_start(pid: int, *, proc_root: Path = Path("/proc")) -> tuple[str | None, str | None]:
    """Return (start ticks, process state); ``None`` means no sound observation."""
    try:
        raw = (proc_root / str(pid) / "stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None, "gone"
    except (OSError, UnicodeError):
        return None, None
    # comm is parenthesized and may itself contain spaces or closing parens.
    close = raw.rfind(")")
    if close < 0:
        return None, None
    fields = raw[close + 1 :].split()
    if len(fields) <= 19:
        return None, None
    return fields[19], fields[0]


def _stop_supervisor(process: subprocess.Popen[bytes], *, timeout: float = 2.0) -> bool:
    """Stop the dedicated supervisor group without waiting indefinitely."""
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            try:
                process.kill()
            except OSError:
                pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


class TrackedProcessObserver:
    """Observe only processes registered by Project Control for an attempt.

    An absent or unavailable registry is UNKNOWN. A registered PID is matched
    against its kernel start token, so PID reuse cannot turn an old owner into
    a live process. This reports the registered producer boundary only; it
    does not infer that unregistered processes are absent.
    """

    def __init__(self, project_root: Path, host_resources: object | None,
                 *, proc_root: Path = Path("/proc")) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.host_resources = host_resources
        self.proc_root = proc_root

    def owners_for(self, attempt: object) -> list[dict[str, Any]] | None:
        return self.owners_for_id(str(attempt.ref.entity_id))

    def owners_for_id(self, attempt_id: str) -> list[dict[str, Any]] | None:
        query = getattr(self.host_resources, "owners_for_attempt", None)
        if not callable(query):
            return None
        try:
            rows = query(self.project_root, attempt_id)
        except Exception:
            return None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return None
        return rows

    def inspect_rows(self, rows: list[dict[str, Any]] | None):
        from todo_orchestrator.model import ProcessLiveness

        if rows is None or not rows:
            return ProcessLiveness.UNKNOWN

        for row in rows:
            pid, expected = row.get("pid"), row.get("process_start")
            if type(pid) is not int or pid <= 0 or not isinstance(expected, str) or not expected:
                return ProcessLiveness.UNKNOWN
            actual, state = _process_start(pid, proc_root=self.proc_root)
            if actual is None:
                if state == "gone":
                    continue
                return ProcessLiveness.UNKNOWN
            if actual == expected and state not in {"Z", "X", "x"}:
                return ProcessLiveness.LIVE
            # A different start token proves that the registered process has
            # exited and the numeric PID has since been reused.
        return ProcessLiveness.STOPPED

    def inspect(self, attempt: object):
        return self.inspect_rows(self.owners_for(attempt))


def _attempt_token(attempt_id: object) -> str:
    return hashlib.sha256(str(attempt_id).encode("utf-8")).hexdigest()


def _attestation_path(root: Path, attempt_id: object) -> Path:
    return root / f"{_attempt_token(attempt_id)}.json"


def _attempt_lock_path(command_root: Path, attempt_id: object) -> Path:
    return command_root / "attempt-locks" / f"{_attempt_token(attempt_id)}.lock"


def _locked_path(path: Path):
    """Take an owner-only advisory lock on a durable native state path."""
    class Lock:
        def __enter__(inner):
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.is_symlink():
                raise NativeProviderUnavailable("native state lock must not be a symlink")
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                os.close(descriptor)
                raise NativeProviderUnavailable("native state lock has unsafe ownership or permissions")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            inner.descriptor = descriptor
            return inner

        def __exit__(inner, *_):
            fcntl.flock(inner.descriptor, fcntl.LOCK_UN)
            os.close(inner.descriptor)

    return Lock()


def _read_attestation(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        raise NativeProviderUnavailable("external producer attestation must not be a symlink")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise NativeProviderUnavailable("external producer attestation could not be opened") from error
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > _MAX_JOURNAL_BYTES):
            raise NativeProviderUnavailable("external producer attestation has unsafe ownership or permissions")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            value = json.load(stream)
        if (not isinstance(value, dict) or value.get("format") != _EXTERNAL_ATTESTATION_FORMAT
                or value.get("version") != _EXTERNAL_ATTESTATION_VERSION
                or set(value) != _EXTERNAL_ATTESTATION_FIELDS):
            raise NativeProviderUnavailable("external producer attestation is malformed")
        return value
    except (OSError, UnicodeError, ValueError) as error:
        raise NativeProviderUnavailable("external producer attestation is unreadable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_attestation(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_JOURNAL_BYTES:
        raise NativeProviderUnavailable("external producer attestation exceeds its size limit")
    fd, temporary_name = tempfile.mkstemp(prefix=".attestation-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _attestation_digest(value: dict[str, Any]) -> str:
    material = {key: item for key, item in value.items() if key != "attestation_id"}
    encoded = json.dumps(material, ensure_ascii=True, allow_nan=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _command_journal_clear(journal_root: Path, attempt_id: object) -> bool:
    """Return true only when every durable operation for this attempt is terminal."""
    if journal_root.is_symlink():
        return False
    if not journal_root.exists():
        return True
    if not journal_root.is_dir():
        return False
    try:
        entries = tuple(journal_root.iterdir())
    except OSError:
        return False
    for path in entries:
        if path.is_symlink() or path.name.startswith(".command-"):
            return False
        if path.suffix != ".json":
            continue
        try:
            value = JournaledCommandExecutor._read(path)
        except Exception:
            return False
        if value is None:
            continue
        if value.get("format") != _COMMAND_FORMAT or value.get("version") != _COMMAND_VERSION:
            return False
        if value.get("attempt_id") == str(attempt_id) and value.get("state") not in {"completed", "not_started"}:
            return False
    return True


def _entity_value(ref: object) -> dict[str, object]:
    return {
        "project_id": str(ref.project_id), "kind": ref.kind.value,
        "entity_id": str(ref.entity_id),
        "definition_revision_id": None if ref.definition_revision_id is None else str(ref.definition_revision_id),
        "incarnation_id": None if ref.incarnation_id is None else str(ref.incarnation_id),
    }


class ExternalProducerProcessObserver:
    """Use a root-recorded Codex completion only when no managed writer exists.

    This state is a bounded host lifecycle attestation, never a claim that the
    kernel process is absent. Ordinary registered PIDs continue to produce
    only LIVE/STOPPED/UNKNOWN. External completion is accepted only for the
    exact attempt/lease/actor/workspace/source identity and a fresh clean tree.
    """

    def __init__(self, *, project_root: Path, host_resources: object,
                 command_journal_root: Path, attestation_root: Path,
                 git_provider: object | None = None,
                 clock: Callable[[], datetime] | None = None,
                 proc_root: Path = Path("/proc")) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.tracked = TrackedProcessObserver(project_root, host_resources, proc_root=proc_root)
        self.command_journal_root = command_journal_root
        self.attestation_root = attestation_root
        self.git_provider = git_provider
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def inspect(self, attempt: object):
        from todo_orchestrator.contracts import StableId
        from todo_orchestrator.model import ProcessLiveness, SourceSafety

        rows = self.tracked.owners_for(attempt)
        process = self.tracked.inspect_rows(rows)
        if process in {ProcessLiveness.LIVE, ProcessLiveness.STOPPED}:
            return process
        # A successful empty query is the only case where a Codex lifecycle
        # attestation may substitute for missing kernel PID registration.
        if rows != [] or not _command_journal_clear(self.command_journal_root, attempt.ref.entity_id):
            return ProcessLiveness.UNKNOWN
        if self.git_provider is None or getattr(attempt, "lease", None) is None:
            return ProcessLiveness.UNKNOWN
        try:
            record = _read_attestation(_attestation_path(self.attestation_root, attempt.ref.entity_id))
            if record is None:
                return ProcessLiveness.UNKNOWN
            observed = datetime.fromisoformat(record["observed_at"])
            expires = datetime.fromisoformat(record["expires_at"])
            if (observed.tzinfo is None or observed.utcoffset() is None
                    or expires.tzinfo is None or expires.utcoffset() is None
                    or expires - observed != timedelta(seconds=_EXTERNAL_ATTESTATION_TTL_SECONDS)):
                return ProcessLiveness.UNKNOWN
            now = self.clock().astimezone(timezone.utc)
            if now < observed.astimezone(timezone.utc) or now >= expires.astimezone(timezone.utc):
                return ProcessLiveness.UNKNOWN
            if (record.get("project_id") != str(attempt.project_id)
                    or record.get("project_root") != str(self.project_root)
                    or record.get("attempt_id") != str(attempt.ref.entity_id)
                    or record.get("actor") != _entity_value(attempt.actor)
                    or record.get("target") != _entity_value(attempt.work_item)
                    or record.get("lease_id") != str(attempt.lease.lease_id)
                    or record.get("lease_expires_at") != attempt.lease.expires_at.isoformat(timespec="microseconds")
                    or record.get("lifecycle_state") != "completed"
                    or record.get("lifecycle_source") != "configured_codex_collaboration"
                    or not isinstance(record.get("producer_id"), str) or not record["producer_id"].strip()
                    or not isinstance(record.get("attested_by"), dict)
                    or not all(isinstance(record["attested_by"].get(key), str)
                               and record["attested_by"][key].strip() for key in ("principal", "profile"))
                    or not isinstance(record.get("host_receipt"), dict)
                    or not isinstance(record["host_receipt"].get("receipt_id"), str)
                    or not isinstance(record["host_receipt"].get("request_digest"), str)
                    or len(record["host_receipt"]["request_digest"]) != 64):
                return ProcessLiveness.UNKNOWN
            if record.get("attestation_id") != _attestation_digest(record):
                return ProcessLiveness.UNKNOWN
            workspace_id = StableId(record["workspace_id"])
            qualification = self.git_provider.qualify_workspace(
                workspace_id, attempt.work_item, attempt.ref.entity_id,
            )
            if (str(qualification.workspace_id) != record.get("workspace_id")
                    or str(qualification.common_base.ref) != record.get("base_ref")
                    or qualification.common_base.digest != record.get("base_digest")):
                return ProcessLiveness.UNKNOWN
            workspace = self.git_provider.workspace_path_for(workspace_id)
            branch = self.git_provider._workspace_branch(workspace_id)
            _common, head, _index_path = self.git_provider._assert_worktree(workspace, branch)
            files, index, safety = self.git_provider._path_state(workspace, head)
            source_digest = self.git_provider._source_digest(files, index)
            if (safety not in {SourceSafety.CLEAN, SourceSafety.DIRTY}
                    or record.get("source_safety") != safety.value
                    or source_digest.hex != record.get("source_digest")):
                return ProcessLiveness.UNKNOWN
            return ProcessLiveness.EXTERNAL_PRODUCER_QUIESCENT
        except Exception:
            return ProcessLiveness.UNKNOWN


class AuthenticatedChildObserver:
    """Read child occupancy from the exact authenticated native authority."""

    def __init__(self, project_root: Path, *, principal: str, profile: str,
                 open_session: Callable[..., object] | None = None) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.principal = principal
        self.profile = profile
        self.open_session = open_session

    def inspect(self, attempt: object):
        from todo_orchestrator.model import ChildActivity

        try:
            if self.open_session is None:
                from .workflow_binding import open_session_for_host

                opener = open_session_for_host
            else:
                opener = self.open_session
            session = opener(self.project_root, principal=self.principal, profile=self.profile)
            try:
                model = session.read_model()
                if str(model.project_id) != str(attempt.project_id):
                    return ChildActivity.UNKNOWN
                rows = model.sections.get("child_executions")
                if not isinstance(rows, tuple):
                    return ChildActivity.UNKNOWN
                children = [row for row in rows
                            if row.get("parent_attempt_id") == str(attempt.ref.entity_id)]
                if any(row.get("state") in {"active", "cancel_requested", "candidate"}
                       for row in children):
                    return ChildActivity.ACTIVE
                if any(row.get("state") != "stopped" for row in children):
                    return ChildActivity.UNKNOWN
                return ChildActivity.NONE
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
        except Exception:
            return ChildActivity.UNKNOWN


def _digest_stream(stream, result: dict[str, str | None], name: str) -> None:
    digest = hashlib.sha256()
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        result[name] = digest.hexdigest()
    except OSError:
        result[name] = None
    finally:
        try:
            stream.close()
        except OSError:
            pass


class JournaledCommandExecutor:
    """Run exact authored argv checks once and journal their durable outcome.

    The journal intent is fsynced before process creation. Inspection never
    executes a command. Incomplete intents remain UNKNOWN, preventing replay
    after a service crash.
    """

    def __init__(self, *, project_id: object, identity: object, git_provider: object,
                 journal_root: Path, attestation_root: Path | None = None,
                 handoff_provider: object | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.project_id = project_id
        self.identity = identity
        self.git_provider = git_provider
        self.journal_root = journal_root
        self.attestation_root = attestation_root
        self.handoff_provider = handoff_provider
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _paths(self, operation_id: object) -> tuple[Path, Path]:
        token = hashlib.sha256(str(operation_id).encode("utf-8")).hexdigest()
        return self.journal_root / f"{token}.json", self.journal_root / f"{token}.lock"

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        if path.is_symlink():
            raise NativeProviderUnavailable("command journal contains a symlink")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return None
        except OSError as error:
            raise NativeProviderUnavailable("command journal could not be opened") from error
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > _MAX_JOURNAL_BYTES):
                raise NativeProviderUnavailable("command journal has unsafe ownership, permissions, or size")
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                value = json.load(stream)
            if not isinstance(value, dict):
                raise NativeProviderUnavailable("command journal record is malformed")
            return value
        except (OSError, UnicodeError, ValueError) as error:
            raise NativeProviderUnavailable("command journal record is unreadable") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False,
                             sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _MAX_JOURNAL_BYTES:
            raise NativeProviderUnavailable("command journal record exceeds its size limit")
        fd, temporary_name = tempfile.mkstemp(prefix=".command-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _locked(self, path: Path):
        class Lock:
            def __enter__(inner):
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if path.is_symlink():
                    raise NativeProviderUnavailable("command journal lock must not be a symlink")
                descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
                info = os.fstat(descriptor)
                if info.st_uid != os.getuid() or info.st_mode & 0o077:
                    os.close(descriptor)
                    raise NativeProviderUnavailable("command journal lock has unsafe permissions")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                inner.descriptor = descriptor
                return inner

            def __exit__(inner, *_):
                fcntl.flock(inner.descriptor, fcntl.LOCK_UN)
                os.close(inner.descriptor)

        return Lock()

    def _workspace(self, request: object) -> tuple[Path, Path]:
        from todo_orchestrator.contracts import StableId

        if request.workspace_id is None:
            raise NativeProviderUnavailable("command workspace is unavailable")
        workspace = self.git_provider.workspace_path_for(request.workspace_id).resolve(strict=True)
        workspace_root = self.git_provider.config.workspace_root.resolve(strict=True)
        if workspace == workspace_root or workspace_root not in workspace.parents:
            raise NativeProviderUnavailable("command workspace escaped its configured root")
        branch = self.git_provider._workspace_branch(request.workspace_id)
        _common, head, _index = self.git_provider._assert_worktree(workspace, branch)
        files, index, _safety = self.git_provider._path_state(workspace, head)
        source = self.git_provider._source_digest(files, index)
        if source != request.expected_source:
            raise NativeProviderUnavailable("command source differs from its authorized source identity")
        cwd = workspace if request.cwd in (None, ".") else workspace / request.cwd
        resolved_cwd = cwd.resolve(strict=True)
        if (resolved_cwd != workspace and workspace not in resolved_cwd.parents) or not resolved_cwd.is_dir():
            raise NativeProviderUnavailable("command working directory escaped its workspace")
        return workspace, resolved_cwd

    def _receipt(self, request: object, observed_at: datetime):
        from todo_orchestrator.contracts import StableId
        from todo_orchestrator.contracts.providers import ProviderReceipt, provider_request_digest

        digest = provider_request_digest(request)
        token = hashlib.sha256((str(request.operation_id) + digest.hex).encode()).hexdigest()
        return ProviderReceipt(
            StableId("pc-command-" + token), self.identity, request.operation_id, digest,
            request.operation_id, observed_at,
        )

    @staticmethod
    def _record_observation(value: dict[str, Any]):
        from todo_orchestrator.contracts import Sha256Digest, StableId
        from todo_orchestrator.contracts.providers import CommandObservation

        return CommandObservation(
            StableId(value["operation_id"]), value["exit_code"], value["timed_out"],
            datetime.fromisoformat(value["started_at"]), datetime.fromisoformat(value["completed_at"]),
            None if value["stdout_digest"] is None else Sha256Digest(value["stdout_digest"]),
            None if value["stderr_digest"] is None else Sha256Digest(value["stderr_digest"]),
        )

    @staticmethod
    def _receipt_from(value: dict[str, Any]):
        from todo_orchestrator.contracts import ProviderIdentity, Sha256Digest, StableId
        from todo_orchestrator.contracts.providers import ProviderReceipt

        return ProviderReceipt(
            StableId(value["receipt_id"]),
            ProviderIdentity(StableId(value["provider_id"]), value["provider_version"],
                             value["provider_contract_version"]),
            StableId(value["correlation_id"]), Sha256Digest(value["request_digest"]),
            StableId(value["provenance_ref"]), datetime.fromisoformat(value["observed_at"]),
        )

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

    def _unknown(self, request: object, reason: str):
        from todo_orchestrator.contracts import StableId
        from todo_orchestrator.contracts.providers import ProviderResult, ProviderStatus

        return ProviderResult(ProviderStatus.UNKNOWN, request.operation_id, self.identity,
                              reason_code=StableId(reason))

    def _unavailable(self, request: object, reason: str):
        from todo_orchestrator.contracts import StableId
        from todo_orchestrator.contracts.providers import ProviderResult, ProviderStatus

        return ProviderResult(ProviderStatus.UNAVAILABLE, request.operation_id,
                              reason_code=StableId(reason))

    def execute(self, request: object):
        from todo_orchestrator.contracts.providers import CommandExecutionRequest, ProviderResult, ProviderStatus, provider_request_digest

        if not isinstance(request, CommandExecutionRequest):
            from todo_orchestrator.contracts import StableId
            return ProviderResult(ProviderStatus.UNAVAILABLE, StableId("invalid-command-request"),
                                  reason_code=StableId("command_request_invalid"))
        if request.context.project_id != self.project_id:
            return self._unavailable(request, "command_project_unavailable")
        if request.timeout_ms > _MAX_COMMAND_TIMEOUT_MS:
            return self._unavailable(request, "command_timeout_exceeds_host_limit")
        record_path, lock_path = self._paths(request.operation_id)
        try:
            _ensure_private_directory(self.journal_root)
            journal_info = self.journal_root.lstat()
            if (not stat.S_ISDIR(journal_info.st_mode) or journal_info.st_uid != os.getuid()
                    or journal_info.st_mode & 0o077):
                return self._unavailable(request, "command_journal_permissions_invalid")
            with self._locked(lock_path):
                prior = self._read(record_path)
                request_digest = provider_request_digest(request).hex
                if prior is not None:
                    if prior.get("format") != _COMMAND_FORMAT or prior.get("request_digest") != request_digest:
                        return self._unknown(request, "command_operation_identity_mismatch")
                    if prior.get("state") != "completed":
                        return self._unknown(request, "command_result_requires_inspection")
                    if prior.get("tree_quiescent") is not True:
                        return self._unknown(request, "command_descendant_quiescence_unconfirmed")
                    observation = self._record_observation(prior)
                    receipt = self._receipt_from(prior["receipt"])
                    return ProviderResult(ProviderStatus.OBSERVED_SUCCESS, request.operation_id,
                                          self.identity, observation, receipt)

                with self._locked(_attempt_lock_path(self.journal_root, request.attempt.entity_id)):
                    if self.attestation_root is not None:
                        attestation = _read_attestation(
                            _attestation_path(self.attestation_root, request.attempt.entity_id)
                        )
                        if attestation is not None:
                            return self._unavailable(request, "external_producer_completion_attested")
                    handed_off = getattr(self.handoff_provider, "has_handoff_marker", None)
                    if callable(handed_off) and handed_off(request.attempt.entity_id):
                        return self._unavailable(request, "attempt_authoring_handed_off")
                    try:
                        workspace, cwd = self._workspace(request)
                    except Exception:
                        return self._unavailable(request, "command_workspace_unqualified")
                    record = {
                        "format": _COMMAND_FORMAT, "version": _COMMAND_VERSION,
                        "operation_id": str(request.operation_id), "attempt_id": str(request.attempt.entity_id),
                        "project_id": str(request.context.project_id), "request_digest": request_digest,
                        "state": "starting", "pid": None, "process_start": None,
                    }
                    self._write(record_path, record)

                    # Keep inherited variables to an explicit host allowlist.
                    # The authored argv is exact; no shell or caller env runs.
                    env = {
                        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                        "HOME": str(Path.home()), "LC_ALL": "C", "LANG": "C",
                        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                        "TMPDIR": os.environ.get("TMPDIR", tempfile.gettempdir()),
                    }
                    started_at = self.clock().astimezone(timezone.utc)
                    repository_root = getattr(self.git_provider.config, "repository_root", None)
                    supervisor_request = json.dumps({
                        "argv": list(request.argv),
                        "timeout_ms": request.timeout_ms,
                        "cleanup_ms": _COMMAND_SUPERVISOR_CLEANUP_MS,
                        "workspace": str(workspace),
                        "cwd": str(cwd),
                        "runtime_prefix": sys.prefix,
                        "repository_root": (
                            None if repository_root is None
                            else str(Path(repository_root).resolve(strict=True))
                        ),
                    }, separators=(",", ":")).encode("utf-8") + b"\n"
                    read_fd, write_fd = os.pipe()
                    try:
                        supervisor_path = Path(__file__).with_name("native_command_supervisor.py")
                        process = subprocess.Popen(
                            [sys.executable, "-S", str(supervisor_path), str(write_fd)],
                            cwd=cwd, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
                            pass_fds=(write_fd,), start_new_session=True, shell=False,
                        )
                    except OSError:
                        os.close(read_fd)
                        os.close(write_fd)
                        record.update(state="not_started", reason="command_launch_failed")
                        self._write(record_path, record)
                        return self._unavailable(request, "command_launch_failed")
                    os.close(write_fd)

                    start, _state = _process_start(process.pid)
                    if start is None or _state in {"Z", "X", "x", "gone", None}:
                        record.update(state="running", pid=process.pid, process_start=start,
                                      process_group=process.pid)
                        try:
                            self._write(record_path, record)
                        except Exception:
                            pass
                        stopped = _stop_supervisor(process)
                        os.close(read_fd)
                        if stopped:
                            record.update(state="not_started", reason="command_supervisor_identity_unavailable")
                            self._write(record_path, record)
                            return self._unavailable(request, "command_supervisor_identity_unavailable")
                        return self._unknown(request, "command_supervisor_identity_unconfirmed")
                    record.update(state="running", pid=process.pid, process_start=start,
                                  process_group=process.pid)
                    try:
                        self._write(record_path, record)
                    except Exception:
                        if process.stdin is not None:
                            try:
                                process.stdin.close()
                            except OSError:
                                pass
                        _stop_supervisor(process)
                        os.close(read_fd)
                        return self._unknown(request, "command_journal_update_failed")

                    try:
                        assert process.stdin is not None
                        process.stdin.write(supervisor_request)
                        process.stdin.close()
                    except (BrokenPipeError, OSError):
                        _stop_supervisor(process)
                        os.close(read_fd)
                        return self._unknown(request, "command_supervisor_request_unconfirmed")

                digests: dict[str, str | None] = {}
                out_thread = threading.Thread(target=_digest_stream, args=(process.stdout, digests, "stdout"), daemon=True)
                err_thread = threading.Thread(target=_digest_stream, args=(process.stderr, digests, "stderr"), daemon=True)
                out_thread.start()
                err_thread.start()
                try:
                    exit_code = process.wait(
                        timeout=(request.timeout_ms + 2 * _COMMAND_SUPERVISOR_CLEANUP_MS) / 1000 + 5
                    )
                except subprocess.TimeoutExpired:
                    _stop_supervisor(process)
                    os.close(read_fd)
                    return self._unknown(request, "command_supervisor_stop_unconfirmed")
                out_thread.join(timeout=2)
                err_thread.join(timeout=2)
                report_bytes = bytearray()
                try:
                    while True:
                        chunk = os.read(read_fd, 4096)
                        if not chunk:
                            break
                        report_bytes.extend(chunk)
                        if len(report_bytes) > 65536:
                            break
                finally:
                    os.close(read_fd)
                if out_thread.is_alive() or err_thread.is_alive():
                    return self._unknown(request, "command_output_stream_unclosed")
                if len(report_bytes) > 65536 or process.returncode is None:
                    return self._unknown(request, "command_supervisor_report_size_unconfirmed")
                try:
                    report = json.loads(report_bytes)
                except (UnicodeError, json.JSONDecodeError):
                    return self._unknown(request, "command_supervisor_report_parse_failed")
                if (not isinstance(report, dict)
                        or report.get("protocol") != _COMMAND_SUPERVISOR_PROTOCOL
                        or report.get("version") != _COMMAND_SUPERVISOR_VERSION
                        or type(report.get("timed_out")) is not bool
                        or type(report.get("tree_quiescent")) is not bool):
                    return self._unknown(request, "command_supervisor_report_shape_invalid")
                supervisor_state = report.get("state")
                if supervisor_state in {"unavailable", "not_started"}:
                    if report.get("tree_quiescent") or report.get("exit_code") is not None:
                        return self._unknown(request, "command_supervisor_report_invalid")
                    record.update(state="not_started", reason=report.get("reason"))
                    self._write(record_path, record)
                    return self._unavailable(
                        request,
                        report.get("reason") if report.get("reason") in {
                            "child_subreaper_unavailable", "command_launch_failed",
                            "bubblewrap_unavailable", "linux_sandbox_unavailable",
                            "command_executable_unavailable", "command_sandbox_configuration_invalid",
                        } else "command_supervisor_unavailable",
                    )
                if (supervisor_state != "completed" or process.returncode != 0
                        or report.get("tree_quiescent") is not True):
                    return self._unknown(request, "command_descendant_quiescence_unconfirmed")
                if any(not isinstance(report.get(name), str)
                       or len(report[name]) != 64
                       or any(ch not in "0123456789abcdef" for ch in report[name])
                       for name in ("stdout_digest", "stderr_digest")):
                    return self._unknown(request, "command_supervisor_output_digest_invalid")
                timed_out = report["timed_out"]
                exit_code = report.get("exit_code")
                if ((timed_out and exit_code is not None)
                        or (not timed_out and type(exit_code) is not int)):
                    return self._unknown(request, "command_supervisor_report_invalid")
                digests["stdout"] = report["stdout_digest"]
                digests["stderr"] = report["stderr_digest"]
                completed_at = self.clock().astimezone(timezone.utc)
                if not timed_out:
                    try:
                        _current_workspace, _current_cwd = self._workspace(request)
                    except Exception:
                        return self._unknown(request, "command_source_changed_during_execution")
                from todo_orchestrator.contracts import Sha256Digest
                from todo_orchestrator.contracts.providers import CommandObservation
                observation = CommandObservation(
                    request.operation_id, exit_code, timed_out, started_at, completed_at,
                    Sha256Digest(digests["stdout"]), Sha256Digest(digests["stderr"]),
                )
                receipt = self._receipt(request, completed_at)
                record.update(
                    state="completed", started_at=started_at.isoformat(timespec="microseconds"),
                    completed_at=completed_at.isoformat(timespec="microseconds"), exit_code=exit_code,
                    timed_out=timed_out, stdout_digest=digests["stdout"], stderr_digest=digests["stderr"],
                    tree_quiescent=True, receipt=self._receipt_value(receipt),
                )
                self._write(record_path, record)
                return ProviderResult(ProviderStatus.OBSERVED_SUCCESS, request.operation_id,
                                      self.identity, observation, receipt)
        except Exception:
            return self._unknown(request, "command_provider_unavailable")

    def inspect(self, request: object):
        from todo_orchestrator.contracts import StableId
        from todo_orchestrator.contracts.providers import (
            CommandExecutionInspection, CommandInspectionState, EffectInspectionRequest,
            ProviderReceipt, ProviderResult, ProviderStatus, provider_request_digest,
        )

        if not isinstance(request, EffectInspectionRequest):
            return ProviderResult(ProviderStatus.UNAVAILABLE, StableId("invalid-command-inspection"),
                                  reason_code=StableId("command_inspection_invalid"))
        if request.context.project_id != self.project_id:
            return ProviderResult(ProviderStatus.UNAVAILABLE, request.correlation_id,
                                  reason_code=StableId("command_project_unavailable"))
        record_path, lock_path = self._paths(request.operation_id)
        try:
            with self._locked(lock_path):
                value = self._read(record_path)
            now = self.clock().astimezone(timezone.utc)
            if value is None:
                inspection = CommandExecutionInspection(request.operation_id, CommandInspectionState.NOT_STARTED)
            elif (value.get("format") != _COMMAND_FORMAT
                  or value.get("request_digest") != request.original_request_digest.hex):
                return ProviderResult(ProviderStatus.UNKNOWN, request.correlation_id, self.identity,
                                      reason_code=StableId("command_operation_identity_mismatch"))
            elif value.get("state") == "not_started":
                inspection = CommandExecutionInspection(request.operation_id, CommandInspectionState.NOT_STARTED)
            elif value.get("state") == "completed":
                if value.get("tree_quiescent") is not True:
                    return ProviderResult(ProviderStatus.UNKNOWN, request.correlation_id, self.identity,
                                          reason_code=StableId("command_descendant_quiescence_unconfirmed"))
                inspection = CommandExecutionInspection(
                    request.operation_id, CommandInspectionState.COMPLETED,
                    self._record_observation(value), self._receipt_from(value["receipt"]),
                )
            elif value.get("state") == "running":
                pid, expected = value.get("pid"), value.get("process_start")
                if type(pid) is int and isinstance(expected, str):
                    actual, state = _process_start(pid)
                    if actual == expected and state not in {"Z", "X", "x"}:
                        inspection = CommandExecutionInspection(request.operation_id, CommandInspectionState.RUNNING)
                    else:
                        return ProviderResult(ProviderStatus.UNKNOWN, request.correlation_id, self.identity,
                                              reason_code=StableId("command_completion_unobserved"))
                else:
                    return ProviderResult(ProviderStatus.UNKNOWN, request.correlation_id, self.identity,
                                          reason_code=StableId("command_start_unobserved"))
            else:
                return ProviderResult(ProviderStatus.UNKNOWN, request.correlation_id, self.identity,
                                      reason_code=StableId("command_effect_unknown"))
            receipt = ProviderReceipt(
                StableId("pc-command-inspect-" + hashlib.sha256(
                    (str(request.operation_id) + str(request.correlation_id) + now.isoformat()).encode()
                ).hexdigest()), self.identity, request.correlation_id,
                provider_request_digest(request), request.operation_id, now,
            )
            return ProviderResult(ProviderStatus.OBSERVED_SUCCESS, request.correlation_id,
                                  self.identity, inspection, receipt)
        except Exception:
            return ProviderResult(ProviderStatus.UNKNOWN, request.correlation_id, self.identity,
                                  reason_code=StableId("command_inspection_unavailable"))

def _private_state_root(project_id: object) -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".local" / "state"
    base = Path(os.path.abspath(base))
    selected = base / "project-control" / "todo-orchestrator" / str(project_id)
    current = Path(base.anchor)
    for component in selected.parts[1:]:
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode):
            raise NativeProviderUnavailable("native Todo host state path must not contain symlinks")
        if not stat.S_ISDIR(info.st_mode):
            raise NativeProviderUnavailable("native Todo host state path contains a non-directory")
        if current == base and info.st_uid != os.getuid():
            raise NativeProviderUnavailable("native Todo state base must be owned by the current user")
        if current.is_relative_to(base) and current != base and (
            info.st_uid != os.getuid() or info.st_mode & 0o077
        ):
            raise NativeProviderUnavailable("native Todo host state must be owner-only")
    return selected


def _ensure_private_directory(path: Path) -> Path:
    """Create one native state directory tree with owner-only descendants."""
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".local" / "state"
    base = Path(os.path.abspath(base))
    selected = Path(os.path.abspath(path))
    try:
        relative = selected.relative_to(base)
    except ValueError as error:
        raise NativeProviderUnavailable("native state directory escaped XDG state home") from error

    current = Path(base.anchor)
    base_parts = base.parts[1:]
    for index, component in enumerate(base_parts):
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise NativeProviderUnavailable("native state path contains a symlink or non-directory")
        if index == len(base_parts) - 1 and info.st_uid != os.getuid():
            raise NativeProviderUnavailable("native state base must be owned by the current user")

    for component in relative.parts:
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            info = current.lstat()
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise NativeProviderUnavailable("native state directory must be owner-only")
    return current


def _current_branch(repository_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository_root), "symbolic-ref", "--quiet", "HEAD"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NativeProviderUnavailable("registered Todo repository branch is unavailable") from error
    branch = result.stdout.decode("ascii", errors="ignore").strip()
    if result.returncode != 0 or not branch.startswith("refs/heads/") or branch.startswith("-"):
        raise NativeProviderUnavailable("native Todo requires a checked out local branch")
    return branch


def _integration_branch_ref(project_id: object) -> StableId:
    """Return the stable Project Control integration ref for this project."""
    from todo_orchestrator.contracts import StableId

    digest = hashlib.sha256(str(project_id).encode("utf-8")).hexdigest()[:20]
    return StableId(f"refs/heads/todo-orchestrator/integration/{digest}")


def ensure_native_integration_ref(config: object, project: str, project_root: Path,
                                 repository: str | None = None):
    """Provision the registered project's integration ref as an explicit action.

    Provider composition and observation are read-only. This operator-side
    helper uses compare-and-set at current HEAD only when the ref is absent;
    it never moves an existing ref or checks the ref out.
    """
    from todo_orchestrator.api import read_project_metadata
    from todo_orchestrator.contracts import StableId

    registry = WorkspaceRegistry(config)
    workspace = registry.workspace(project)
    alias = repository if repository is not None else workspace.authority_repository
    if not alias:
        raise NativeProviderUnavailable("native Todo authority repository is not configured")
    repository = registry.repository(project, alias)
    root = project_root.resolve(strict=True)
    if root != repository.root.resolve(strict=True):
        raise NativeProviderUnavailable("native Todo root differs from the registered authority repository")
    project_id = read_project_metadata(root).project_id
    branch = _integration_branch_ref(project_id)
    command_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}
    found = subprocess.run(
        ["git", "-C", str(root), "show-ref", "--verify", "--hash", str(branch)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=10, check=False, env=command_env,
    )
    if found.returncode != 0:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False, env=command_env,
        )
        if head.returncode != 0:
            raise NativeProviderUnavailable("native Todo repository has no committed base revision")
        head_oid = head.stdout.decode("ascii", errors="strict").strip()
        if len(head_oid) not in {40, 64} or any(char not in "0123456789abcdef" for char in head_oid):
            raise NativeProviderUnavailable("native Todo repository returned an invalid base object ID")
        created = subprocess.run(
            ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null",
             "update-ref", str(branch), head_oid, "0" * len(head_oid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False, env=command_env,
        )
        if created.returncode != 0:
            # Another initializer may have won the compare-and-set race.
            found = subprocess.run(
                ["git", "-C", str(root), "show-ref", "--verify", "--hash", str(branch)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=10, check=False, env=command_env,
            )
            if found.returncode != 0:
                raise NativeProviderUnavailable("native Todo integration branch could not be initialized")
    worktrees = subprocess.run(
        ["git", "-C", str(root), "worktree", "list", "--porcelain"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=10, check=False, env=command_env,
    )
    if worktrees.returncode != 0 or any(
        line == f"branch {branch}" for line in worktrees.stdout.decode("utf-8", errors="replace").splitlines()
    ):
        raise NativeProviderUnavailable("native Todo integration ref must not be checked out")
    return branch


def provision_native_project(config: object, project: str, project_root: Path,
                              repository: str | None = None) -> dict[str, str]:
    """Explicitly provision native host refs and private runtime directories.

    Provider construction stays read-only. Operators call this helper once
    when registering a project, before its first native workspace or command
    effect.
    """
    integration_ref = ensure_native_integration_ref(
        config, project, project_root, repository=repository,
    )
    from todo_orchestrator.api import read_project_metadata

    project_id = read_project_metadata(Path(project_root).resolve(strict=True)).project_id
    state_root = _private_state_root(project_id)
    for path in (
        state_root,
        state_root / "workspaces",
        state_root / "git-journal",
        state_root / "command-journal",
        state_root / "external-producer-attestations",
        state_root / "attempt-credentials",
        state_root / "authoring-handoffs",
    ):
        _ensure_private_directory(path)
    return {
        "project_id": str(project_id),
        "integration_ref": str(integration_ref),
        "state_root": str(state_root),
    }


def _default_host_resources():
    """Open the selected physical authority through the central factory.

    A missing local registry remains UNKNOWN to the process observer; once
    Device Scheduler is selected, ownership reads use its authenticated lease
    records and never fall back to the retired local database.
    """
    from .host_resources.runtime import host_resource_facade

    # Provider construction is observational. Do not initialize the optional
    # local registry just to check whether an attempt has a registered owner.
    return host_resource_facade(create_local=False)


def providers_for_project(config: object, project: str, project_root: Path, *,
                          principal: str, profile: str,
                          repository: str | None = None,
                          host_resources: object | None = None,
                          principal_authenticator: object | None = None):
    """Build trusted standalone providers from exact registered host identity.

    Todo receives local Git workspaces/source observation and authored command
    verification. Physical permits are deliberately omitted: host physical
    capacity remains under Project Control's independent resource admission.
    """
    from todo_orchestrator.api import ProviderComposition, read_project_metadata
    from todo_orchestrator.contracts import ProviderIdentity, StableId
    from todo_orchestrator.providers.execution.command import (
        COMMAND_VERIFIER_IDENTITY, CommandExecutionVerifier,
    )
    from todo_orchestrator.providers.git import GitRepositoryConfig, GitRepositoryProvider

    if not isinstance(principal, str) or not principal.strip() or not isinstance(profile, str) or not profile.strip():
        raise NativeProviderUnavailable("native Todo host identity is unavailable")
    registry = WorkspaceRegistry(config)
    workspace = registry.workspace(project)
    alias = repository if repository is not None else workspace.authority_repository
    if not alias:
        raise NativeProviderUnavailable("native Todo authority repository is not configured")
    repository = registry.repository(project, alias)
    root = project_root.resolve(strict=True)
    if root != repository.root.resolve(strict=True):
        raise NativeProviderUnavailable("native Todo root differs from the registered authority repository")
    metadata = read_project_metadata(root)
    project_id = metadata.project_id
    authenticator_factory = getattr(principal_authenticator, "for_project", None)
    if callable(authenticator_factory):
        principal_authenticator = authenticator_factory(str(project_id))
    repository_id = StableId(alias)
    state_root = _private_state_root(project_id)
    workspace_root = state_root / "workspaces"
    journal_root = state_root / "git-journal"
    command_root = state_root / "command-journal"
    attestation_root = state_root / "external-producer-attestations"
    credential_root = state_root / "attempt-credentials"
    handoff_root = state_root / "authoring-handoffs"
    for path in (workspace_root, journal_root, command_root, credential_root, handoff_root):
        if path.is_symlink():
            raise NativeProviderUnavailable("native Todo provider path must not be a symlink")
        if path.exists():
            info = path.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077):
                raise NativeProviderUnavailable("native Todo provider path must be an owner-only directory")
    if attestation_root.is_symlink():
        raise NativeProviderUnavailable("native Todo attestation path must not be a symlink")
    if attestation_root.exists():
        info = attestation_root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise NativeProviderUnavailable("native Todo provider path must be owner-only")
    branch = StableId(_current_branch(root))
    integration = _integration_branch_ref(project_id)
    integration_check = subprocess.run(
        ["git", "-C", str(root), "show-ref", "--verify", "--hash", str(integration)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=10, check=False, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"},
    )
    if integration_check.returncode != 0:
        raise NativeProviderUnavailable(
            "native Todo integration ref is uninitialized; run ensure_native_integration_ref explicitly"
        )
    config_value = GitRepositoryConfig(
        project_id=project_id, repository_id=repository_id, repository_root=root,
        workspace_root=workspace_root, journal_root=journal_root,
        base_ref=branch, integration_ref=integration,
        # The standalone authority lives at this fixed repository-local path;
        # it is runtime state, not task source or a worktree change.
        excluded_paths=(".todo-orchestrator",),
    )
    # Build Git once so workspace/source and verification share one host
    # configuration. Unknown process/child observations stay unknown.
    host = _default_host_resources() if host_resources is None else host_resources
    process_observer = ExternalProducerProcessObserver(
        project_root=root, host_resources=host, command_journal_root=command_root,
        attestation_root=attestation_root,
    )
    children = AuthenticatedChildObserver(root, principal=principal, profile=profile)
    git = GitRepositoryProvider(config_value, process_observer=process_observer,
                                child_observer=children)
    process_observer.git_provider = git
    from .native_authoring_handoff import (
        NativeAttemptCredentialProvider,
        NativeAuthoringHandoffProvider,
    )

    credential_provider = NativeAttemptCredentialProvider(
        str(project_id), str(metadata.authority_id), credential_root,
    )
    handoff_provider = NativeAuthoringHandoffProvider(
        git, project_id=str(project_id), authority_id=str(metadata.authority_id),
        project_root=root, record_root=handoff_root, host_resources=host,
        command_journal_root=command_root,
        credential_principal=principal, credential_profile=profile,
    )
    # The native engine discovers these typed host hooks on its trusted
    # AttemptHost. They are private Python provider methods, never MCP tools.
    git.handoff_authoring = handoff_provider.handoff_authoring
    git.authoring_handoff_for = handoff_provider.authoring_handoff_for
    git.has_authoring_handoff = handoff_provider.has_handoff_marker
    original_inspect_attempt = git.inspect_attempt

    def inspect_attempt_with_handoff(attempt: object, workspace: object):
        observation = original_inspect_attempt(attempt, workspace)
        proof = handoff_provider.authoring_handoff_for(attempt)
        if proof is None:
            return observation
        from dataclasses import fields, is_dataclass, replace

        if is_dataclass(observation) and "authoring_handoff" in {item.name for item in fields(observation)}:
            if (proof.lease_id == observation.lease_id
                    and proof.workspace_id == observation.workspace.workspace_id
                    and proof.source_digest == observation.source_digest
                    and observation.source.value == "clean"):
                return replace(observation, authoring_handoff=proof)
        return observation

    git.inspect_attempt = inspect_attempt_with_handoff

    original_perform = git.perform
    original_retry = git.retry_not_applied

    def fence_authoring_effect(operation: Callable[[object], object], request: object):
        from todo_orchestrator.contracts import StableId
        from todo_orchestrator.contracts.providers import (
            GitOperationKind, ProviderResult, ProviderStatus,
        )

        attempt = getattr(request, "attempt", None)
        kind = getattr(request, "operation", None)
        if (attempt is not None and kind in {
            GitOperationKind.APPLY_CHANGE, GitOperationKind.COMMIT,
            GitOperationKind.PUBLISH, GitOperationKind.CREATE_WORKSPACE,
        }):
            # Serialize the marker check and effect against handoff_authoring,
            # which takes this same lock before checking that no effect is
            # unresolved and publishing its durable authoring fence.
            lock_path = _attempt_lock_path(command_root, attempt.entity_id)
            with _locked_path(lock_path):
                if handoff_provider.has_handoff_marker(attempt.entity_id):
                    return ProviderResult(
                        ProviderStatus.UNAVAILABLE, request.operation_id, git.identity,
                        reason_code=StableId("attempt_authoring_handed_off"),
                    )
                return operation(request)
        return operation(request)

    git.perform = lambda request: fence_authoring_effect(original_perform, request)
    git.retry_not_applied = lambda request: fence_authoring_effect(original_retry, request)
    command_identity = ProviderIdentity(StableId("project-control.command.local"), "1")
    commands = JournaledCommandExecutor(
        project_id=project_id, identity=command_identity, git_provider=git,
        journal_root=command_root, attestation_root=attestation_root,
        handoff_provider=handoff_provider,
    )
    verifier = CommandExecutionVerifier(
        commands, execution_identity=command_identity, adapter_identity=COMMAND_VERIFIER_IDENTITY,
    )
    return ProviderComposition(
        attempt_host=git, host_identity=git.identity,
        source_observer=git, source_identity=git.identity,
        verification_provider=verifier, verification_identity=COMMAND_VERIFIER_IDENTITY,
        command_execution_provider=commands, command_execution_identity=command_identity,
        declaration_observer=git, declaration_identity=git.identity,
        git_provider=git, principal_authenticator=principal_authenticator,
        attempt_credential_provider=credential_provider,
    )


def record_external_producer_completion(
    config: object,
    project: str,
    project_root: Path,
    *,
    principal: str,
    profile: str,
    attempt_id: str,
    producer_id: str,
    lifecycle_state: str,
    host_resources: object | None = None,
) -> dict[str, str]:
    """Record root-observed Codex completion for one exact Todo attempt.

    This is a host administration function and is not exposed on the MCP
    frontdoor. The trusted root calls it only after observing that exact
    producer as completed through configured Codex collaboration APIs. The
    record says that producer work is quiescent; it does not assert that the
    producer's kernel process has exited.
    """
    if lifecycle_state != "completed":
        raise NativeProviderUnavailable("external producer lifecycle is not completed")
    if not all(isinstance(item, str) and item.strip() for item in (attempt_id, producer_id)):
        raise NativeProviderUnavailable("external producer identity is required")
    providers = providers_for_project(
        config, project, project_root, principal=principal, profile=profile,
        host_resources=host_resources,
    )
    git = providers.git_provider
    process_observer = git.process_observer
    root = Path(project_root).resolve(strict=True)
    project_id = git.config.project_id
    state_root = _private_state_root(project_id)
    command_root = state_root / "command-journal"
    attestation_root = state_root / "external-producer-attestations"
    _ensure_private_directory(command_root)
    _ensure_private_directory(attestation_root)
    attempt_lock = _attempt_lock_path(command_root, attempt_id)

    try:
        from .workflow_binding import open_session_for_host

        with _locked_path(attempt_lock):
            session = open_session_for_host(
                root, principal=principal, profile=profile, providers=providers,
            )
            try:
                inspection = session.inspect_attempt(attempt_id)
                model = session.read_model()
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()

            if inspection.attempt is None or str(inspection.attempt.entity_id) != attempt_id:
                raise NativeProviderUnavailable("authenticated Todo inspection did not identify this attempt")
            if inspection.lease_id is None or inspection.lease_expires_at is None:
                raise NativeProviderUnavailable("external producer attestation requires an active attempt lease")
            rows = model.sections.get("attempts")
            if not isinstance(rows, tuple):
                raise NativeProviderUnavailable("authenticated Todo attempt projection is unavailable")
            matches = [row for row in rows if row.get("attempt_id") == attempt_id]
            if len(matches) != 1:
                raise NativeProviderUnavailable("authenticated Todo attempt projection is ambiguous")
            row = matches[0]
            if row.get("state") in {"accepted", "rejected", "released"}:
                raise NativeProviderUnavailable("terminal Todo attempts cannot receive producer attestations")
            host = inspection.host_observation
            from todo_orchestrator.model import ChildActivity, ProcessLiveness, SourceSafety

            if (host is None or host.attempt != inspection.attempt
                    or host.lease_id != inspection.lease_id
                    or host.observed_lease_expires_at != inspection.lease_expires_at
                    or host.source not in {SourceSafety.CLEAN, SourceSafety.DIRTY}
                    or host.source_digest is None
                    or host.process is not ProcessLiveness.UNKNOWN
                    or host.children is not ChildActivity.NONE
                    or host.workspace.workspace_id != inspection.workspace_id):
                raise NativeProviderUnavailable("Todo host observation is not clean, exact, and child-free")
            if (row.get("lease_id") != str(inspection.lease_id)
                    or row.get("lease_expires_at") != inspection.lease_expires_at.isoformat(timespec="microseconds")
                    or str(host.receipt.correlation_id) != attempt_id
                    or host.receipt.provenance_ref != host.workspace.common_base.ref):
                raise NativeProviderUnavailable("Todo host observation does not match its current lease")

            owners = process_observer.tracked.owners_for_id(attempt_id)
            if owners is None or owners:
                raise NativeProviderUnavailable("registered physical process owners remain or are unavailable")
            if not _command_journal_clear(command_root, attempt_id):
                raise NativeProviderUnavailable("managed command activity remains unresolved")

            project = str(model.project_id)
            actor = {
                "project_id": project, "kind": "actor", "entity_id": row["actor_id"],
                "definition_revision_id": None, "incarnation_id": row["actor_incarnation_id"],
            }
            target = {
                "project_id": project, "kind": "work_item", "entity_id": row["work_item_id"],
                "definition_revision_id": row["definition_revision_id"], "incarnation_id": None,
            }
            observed_at = datetime.now(timezone.utc)
            expires_at = observed_at + timedelta(seconds=_EXTERNAL_ATTESTATION_TTL_SECONDS)
            payload: dict[str, Any] = {
                "format": _EXTERNAL_ATTESTATION_FORMAT,
                "version": _EXTERNAL_ATTESTATION_VERSION,
                "project_id": project,
                "project_root": str(root),
                "attempt_id": attempt_id,
                "actor": actor,
                "target": target,
                "lease_id": str(inspection.lease_id),
                "lease_expires_at": inspection.lease_expires_at.isoformat(timespec="microseconds"),
                "workspace_id": str(host.workspace.workspace_id),
                "base_ref": str(host.workspace.common_base.ref),
                "base_digest": host.workspace.common_base.digest,
                "source_safety": host.source.value,
                "source_digest": host.source_digest.hex,
                "producer_id": producer_id,
                "lifecycle_source": "configured_codex_collaboration",
                "lifecycle_state": lifecycle_state,
                "attested_by": {"principal": principal, "profile": profile},
                "host_receipt": {
                    "receipt_id": str(host.receipt.receipt_id),
                    "request_digest": host.receipt.request_digest.hex,
                    "observed_at": host.receipt.observed_at.isoformat(timespec="microseconds"),
                },
                "observed_at": observed_at.isoformat(timespec="microseconds"),
                "expires_at": expires_at.isoformat(timespec="microseconds"),
            }
            payload["attestation_id"] = _attestation_digest(payload)
            path = _attestation_path(attestation_root, attempt_id)
            prior = _read_attestation(path)
            if prior is not None:
                identity_fields = (
                    "project_id", "project_root", "attempt_id", "actor", "target", "lease_id",
                    "lease_expires_at", "workspace_id", "base_ref", "base_digest", "source_safety",
                    "source_digest",
                    "producer_id", "lifecycle_source", "lifecycle_state",
                )
                same = all(prior.get(key) == payload.get(key) for key in identity_fields)
                try:
                    prior_expiry = datetime.fromisoformat(prior["expires_at"])
                except (KeyError, TypeError, ValueError):
                    prior_expiry = observed_at
                if not same or prior_expiry > observed_at:
                    if same and prior.get("attestation_id") == _attestation_digest(prior):
                        return {
                            "attestation_id": prior["attestation_id"],
                            "attempt_id": attempt_id,
                            "expires_at": prior["expires_at"],
                        }
                    raise NativeProviderUnavailable("a conflicting external producer attestation already exists")
            _write_attestation(path, payload)
            return {
                "attestation_id": payload["attestation_id"],
                "attempt_id": attempt_id,
                "expires_at": payload["expires_at"],
            }
    except NativeProviderUnavailable:
        raise
    except Exception as error:
        raise NativeProviderUnavailable("trusted external producer attestation could not be recorded") from error


__all__ = [
    "AuthenticatedChildObserver", "ExternalProducerProcessObserver", "JournaledCommandExecutor",
    "NativeProviderUnavailable", "TrackedProcessObserver", "ensure_native_integration_ref",
    "provision_native_project",
    "providers_for_project", "record_external_producer_completion",
]
