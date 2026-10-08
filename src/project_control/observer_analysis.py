"""Bounded, claimless local analysis over immutable observer evidence."""

from __future__ import annotations

import json
import hashlib
import importlib
import csv
import math
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from .call_audit import call_id_var, summarize_messages, write_event
from .runtime_binding import RuntimeBindingError, _source_receiver_files, bind_local_runtime
from .runtime_identity import package_fingerprint
from .security import redact_output_text


_PHYSICAL_RELEASE_SEAL = object()


def _source_supervisor_client(module, state_root: Path, runtime_root: Path, receiver_identity):
    """Build a source-mode client whose release proof uses the live inventory.

    The receiver's checked-in manifest is release metadata. Source mode already
    binds an in-memory inventory, so requiring that historical file map here
    would make source cleanup depend on a manual manifest refresh. Frozen
    candidates continue using the receiver's strict base client unchanged.
    """
    base_client = module.SupervisorClient

    class SourceSupervisorClient(base_client):
        def _validate_owned_release_status(self, status: dict[str, Any]) -> None:
            try:
                receiver = bind_local_runtime(root=receiver_identity.root)
                files = _source_receiver_files(receiver.root)
                if receiver.source_commit != "working-tree":
                    raise RuntimeBindingError("source_receiver_identity_required")
                # Inspect the canonical receiver module captured from the base
                # class; this keeps its public import identity unchanged.
                receiver_module = module
                origin = getattr(getattr(receiver_module, "__spec__", None), "origin", None)
                if not isinstance(origin, str) or not origin:
                    raise RuntimeBindingError("receiver_import_origin_missing")
                imported_path = Path(origin).resolve(strict=True)
                module_file = Path(receiver_module.__file__).resolve(strict=True)
                expected_path = (receiver.package_root / "supervisor.py").resolve(strict=True)
                expected = files.get("local_worker/supervisor.py")
                local_source = hashlib.sha256(expected_path.read_bytes()).hexdigest()
                todo_fingerprint = hashlib.sha256(json.dumps(
                    self.runtime_context, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                    default=str).encode("utf-8")).hexdigest()
            except Exception as error:
                raise module.SupervisorError("receiver_source_identity_unavailable") from error
            if (not isinstance(status, dict) or imported_path != expected_path or
                    module_file != expected_path or not isinstance(expected, str) or len(expected) != 64 or
                    expected != local_source or status.get("source_sha256") != expected or
                    status.get("runtime_identity") != self.runtime_context or
                    status.get("runtime_fingerprint") != todo_fingerprint):
                raise module.SupervisorError("receiver_source_identity_mismatch")
            if (type(status.get("supervisor_pid")) is not int or status["supervisor_pid"] <= 0 or
                    not isinstance(status.get("supervisor_process_start"), str) or
                    not status["supervisor_process_start"] or
                    not isinstance(status.get("daemon_epoch"), str) or len(status["daemon_epoch"]) != 64 or
                    not isinstance(status.get("runtime_fingerprint"), str) or
                    len(status["runtime_fingerprint"]) != 64):
                raise module.SupervisorError("receiver_process_identity_invalid")

    return SourceSupervisorClient(state_root, root=runtime_root)


class _VerifiedPhysicalRelease(dict):
    """Opaque, locally verified proof that one supervisor epoch was stopped."""

    __slots__ = ("_seal", "host")

    def __init__(self, seal: object, value: dict[str, Any], host: str):
        if seal is not _PHYSICAL_RELEASE_SEAL:
            raise RuntimeError("verified_physical_release_is_private")
        super().__init__(value)
        self._seal = seal
        self.host = host


def _is_verified_physical_release(value: object) -> bool:
    return isinstance(value, _VerifiedPhysicalRelease) and value._seal is _PHYSICAL_RELEASE_SEAL


def _proc_start_time(pid: int) -> str | None:
    """Read Linux start ticks without treating an unreadable process as dead."""
    try:
        raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimeError("physical_release_process_identity_unavailable") from None
    close = raw.rfind(")")
    if close < 0:
        raise RuntimeError("physical_release_process_identity_invalid")
    fields = raw[close + 1:].split()
    # fields[0] is stat field 3; starttime is field 22.
    if len(fields) <= 19 or not fields[19].isdigit():
        raise RuntimeError("physical_release_process_identity_invalid")
    return fields[19]


def _source_identity_snapshot() -> dict[str, str]:
    """Bind recovery evidence to this exact source checkout and receiver."""
    try:
        receiver = bind_local_runtime()
        # Match the canonical runtime attestation boundary for the complete
        # ``project_control`` package.
        project_root = Path(__file__).resolve().parent
        project_fingerprint = package_fingerprint(project_root)
        # The HostCoordinator admission fence is implemented in this sibling
        # bundled package, so pin its source independently as well.
        todo_root = project_root.parent / "todo_orchestrator"
        todo_fingerprint = package_fingerprint(todo_root)
    except Exception as error:
        raise RuntimeError("physical_release_source_identity_unavailable") from error
    if receiver.source_commit != "working-tree":
        raise RuntimeError("physical_release_source_mode_required")
    return {
        "format": "PC-SOURCE-IDENTITY/1",
        "project_control_root": str(project_root),
        "project_control_fingerprint": project_fingerprint,
        "todo_orchestrator_root": str(todo_root),
        "todo_orchestrator_fingerprint": todo_fingerprint,
        "receiver_root": str(receiver.root),
        "receiver_manifest_sha256": receiver.manifest_sha256,
        "receiver_fingerprint": receiver.fingerprint,
        "receiver_source_commit": receiver.source_commit,
    }


def _require_process_absent(pid: int, start: str, *, error_prefix: str) -> None:
    """Require kernel-confirmed absence of the recorded process generation.

    A different process start token means PID reuse, not proof that the old
    owner is absent.  Permission and procfs failures remain unverifiable.
    """
    current = _proc_start_time(pid)
    if current is not None:
        raise RuntimeError(f"{error_prefix}_present_or_reused")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    except (PermissionError, OSError) as error:
        raise RuntimeError(f"{error_prefix}_absence_unverifiable") from error
    raise RuntimeError(f"{error_prefix}_absence_unverifiable")


def _inactive_service_state(unit: str) -> dict[str, Any]:
    """Read one fixed user unit without starting, stopping, or reloading it."""
    try:
        result = subprocess.run(["systemctl", "--user", "show", unit,
            "-p", "ActiveState", "-p", "MainPID"], check=True, capture_output=True,
            text=True, timeout=3)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("physical_release_service_state_unavailable") from error
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in {"ActiveState", "MainPID"} or key in fields:
            raise RuntimeError("physical_release_service_state_invalid")
        fields[key] = value
    if fields != {"ActiveState": "inactive", "MainPID": "0"}:
        raise RuntimeError("physical_release_service_not_inactive")
    return {"unit": unit, "active_state": "inactive", "main_pid": 0}


def _fresh_gpu_release_observation(allowed_gpu_uuids: tuple[str, ...]) -> dict[str, Any]:
    """Read current memory and compute-process state for the fixed GPU scope."""
    try:
        memory_result = subprocess.run(["nvidia-smi", "--query-gpu=uuid,memory.used",
            "--format=csv,noheader,nounits"], check=True, capture_output=True,
            text=True, timeout=8)
        process_result = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,gpu_uuid,used_memory",
            "--format=csv,noheader,nounits"], check=True, capture_output=True,
            text=True, timeout=8)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("physical_release_fresh_gpu_observation_unavailable") from error
    memory: dict[str, float] = {}
    for row in csv.reader(memory_result.stdout.splitlines()):
        if len(row) != 2:
            raise RuntimeError("physical_release_fresh_gpu_observation_invalid")
        try:
            memory[row[0].strip()] = float(row[1].strip())
        except ValueError:
            raise RuntimeError("physical_release_fresh_gpu_observation_invalid") from None
    if any(uuid not in memory or memory[uuid] != 0 for uuid in allowed_gpu_uuids):
        raise RuntimeError("physical_release_gpu_memory_not_zero")
    processes = []
    for row in csv.reader(process_result.stdout.splitlines()):
        if not row:
            continue
        if len(row) < 2:
            raise RuntimeError("physical_release_fresh_gpu_observation_invalid")
        uuid = row[1].strip()
        if uuid in allowed_gpu_uuids:
            processes.append({"pid": row[0].strip(), "gpu_uuid": uuid})
    if processes:
        raise RuntimeError("physical_release_gpu_process_reappeared")
    return {"observed_at": time.time(),
        "devices": [{"uuid": uuid, "memory_used_mib": memory[uuid]}
                    for uuid in sorted(allowed_gpu_uuids)],
        "processes": []}


def _fresh_host_release_observation() -> dict[str, int]:
    """Require no host reservation or pending owner intent before recovery."""
    try:
        from todo_orchestrator.background.host import HostCoordinator
        host = HostCoordinator(create=False)
        connection = host.connect(readonly=True)
        try:
            active_owners = connection.execute(
                "SELECT count(*) FROM host_owners WHERE state IN ('active','intent')").fetchone()[0]
            active_reservations = connection.execute(
                "SELECT count(*) FROM host_reservations WHERE state='active'").fetchone()[0]
            active_foreground_intents = connection.execute(
                "SELECT count(*) FROM host_foreground_intents WHERE state='active'").fetchone()[0]
        finally:
            connection.close()
    except Exception as error:
        raise RuntimeError("physical_release_fresh_host_observation_unavailable") from error
    if active_owners or active_reservations or active_foreground_intents:
        raise RuntimeError("physical_release_host_owner_reappeared")
    return {"active_owners": int(active_owners), "active_reservations": int(active_reservations),
        "active_foreground_intents": int(active_foreground_intents)}


def _orphan_cleanup_slots_from_status(before: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept only exact failed broker slots whose recorded process generation is gone.

    ``active_work`` is independently required to be complete and empty by the
    caller.  This allows a terminal job's failed cleanup marker to remain in
    the broker snapshot while physical resources are reconciled, without
    treating a live borrower as stopped.
    """
    slots = before.get("active_execution_slots")
    if (before.get("active_execution_slots_truncated") is not False or
            not isinstance(slots, list) or len(slots) > 16):
        raise RuntimeError("physical_release_controller_not_quiescent")
    accepted = []
    seen = set()
    for slot in slots:
        if (not isinstance(slot, dict) or set(slot) != {
                "job_id", "attempt", "cleanup_pending", "owner_pid", "owner_process_start"}):
            raise RuntimeError("physical_release_execution_slot_invalid")
        job_id, attempt = slot.get("job_id"), slot.get("attempt")
        owner_pid, owner_start = slot.get("owner_pid"), slot.get("owner_process_start")
        if (not isinstance(job_id, str) or not job_id or len(job_id) > 256 or
                type(attempt) is not int or attempt <= 0 or slot.get("cleanup_pending") is not True or
                type(owner_pid) is not int or owner_pid <= 0 or
                not isinstance(owner_start, str) or not owner_start.isdigit() or len(owner_start) > 128 or
                (job_id, attempt) in seen):
            raise RuntimeError("physical_release_execution_slot_invalid")
        try:
            current_start = _proc_start_time(owner_pid)
        except RuntimeError:
            raise RuntimeError("physical_release_execution_slot_owner_identity_unavailable") from None
        if current_start == owner_start:
            raise RuntimeError("physical_release_execution_slot_owner_still_running")
        seen.add((job_id, attempt))
        accepted.append({"job_id": job_id, "attempt": attempt,
            "cleanup_pending": True, "owner_pid": owner_pid,
            "owner_process_start": owner_start})
    return accepted


def observer_analysis_state_root(*, create: bool = True) -> Path:
    """Return the private service state root, never an observed project root."""
    configured = os.environ.get("PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")).expanduser()
        root = (base / "project-control" / "observer-analysis").resolve()
    if not create:
        return root
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def compact_packet_fallback(packet: dict[str, Any], reason: str) -> dict[str, Any]:
    """Return useful, bounded packet-derived content when inference is absent."""
    evidence = packet.get("evidence") if isinstance(packet, dict) else None
    rows = evidence if isinstance(evidence, list) else []
    excerpts: list[str] = []
    ids: list[str] = []
    for item in rows[:8]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        evidence_id = item["id"]
        ids.append(evidence_id)
        content = next((item.get(key) for key in ("summary", "excerpt", "content", "text", "title") if isinstance(item.get(key), str)), "")
        excerpts.append(f"[{evidence_id}] {content[:240]}".strip())
    summary = "Packet evidence extract: " + ("; ".join(excerpts) if excerpts else "no usable evidence entries supplied")
    return {"status": "unavailable", "authoritative": False, "mutation_authority": False,
            "provider": "llama-server", "reason": reason[:500], "fallback": "authoritative_compact_envelope",
            "summary": summary[:2000], "evidence_ids": ids, "uncertainty": "deterministic packet extract; local model analysis unavailable"}


class ObserverAnalysisProvider(Protocol):
    @property
    def available(self) -> bool: ...

    def analyze(self, immutable_packet: dict[str, Any]) -> dict[str, Any]: ...

    def investigate_turn(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def open_sessions(self, count: int, *, compute_profile: str, parallelism: str, deadline_epoch: float | None = None) -> dict[str, Any]: ...
    def close_session(self, session_id: str) -> None: ...


class DisabledObserverAnalysisProvider:
    available = False

    def analyze(self, immutable_packet: dict[str, Any]) -> dict[str, Any]:
        return compact_packet_fallback(immutable_packet, "observer_analysis_disabled")

    def investigate_turn(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"status": "unavailable", "reason": "local_investigator_disabled"}

    def open_sessions(self, count: int, *, compute_profile: str, parallelism: str, deadline_epoch: float | None = None) -> dict[str, Any]:
        return {"status": "unavailable", "reason": "local_investigator_disabled"}

    def close_session(self, session_id: str) -> None:
        return None


class SkillsObserverAnalysisProvider:
    """Thin read-only client of the operator-owned central model supervisor.

    Project Control supplies data, never a repository handle, tool, claim, or
    execution context.  The Skills backend owns model cache, topology and GPU
    reservation; any unavailable/busy condition has a deterministic fallback.
    """

    available = True

    def __init__(self, repo_root: str | Path | None = None):
        # Kept for compatibility; observed roots never initialize the backend.
        self._repo_root = Path(repo_root) if repo_root is not None else None
        self._backend: Any | None = None
        self._backend_lock = threading.RLock()
        self._state_root = observer_analysis_state_root(create=False)
        self._source_sha256: str | None = None
        self._operator_source_sha256 = os.environ.get("PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256")
        # Only a trusted operator startup setting grants this resource restriction.
        # Snapshot it before discovery; later environment changes cannot widen it.
        raw = os.environ.get("PROJECT_CONTROL_OBSERVER_GPU_UUIDS")
        self._allowed_gpu_uuids: tuple[str, ...] | None = None
        if raw is not None:
            try:
                values = json.loads(raw)
            except (ValueError, TypeError):
                raise ValueError("PROJECT_CONTROL_OBSERVER_GPU_UUIDS must be a nonempty JSON list of GPU UUIDs") from None
            if not isinstance(values, list) or not values or any(
                    not isinstance(value, str) or re.fullmatch(
                        r"GPU-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value) is None
                    for value in values):
                raise ValueError("PROJECT_CONTROL_OBSERVER_GPU_UUIDS must be a nonempty JSON list of GPU UUIDs")
            self._allowed_gpu_uuids = tuple(dict.fromkeys(values))

    def _get_backend(self) -> Any:
        with self._backend_lock:
            if self._backend is None:
                identity = bind_local_runtime()
                module = importlib.import_module("local_worker.supervisor")
                source = Path(str(getattr(module, "__file__", ""))).resolve()
                if identity.package_root != source.parent and identity.package_root not in source.parents:
                    raise RuntimeBindingError("imported_runtime_source_mismatch")
                self._source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
                if self._operator_source_sha256 and self._operator_source_sha256 != self._source_sha256:
                    raise RuntimeError("central_supervisor_source_mismatch")
                self._state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
                self._state_root.chmod(0o700)
                if identity.source_commit == "working-tree":
                    self._backend = _source_supervisor_client(
                        module, self._state_root, self._state_root / "runtime", identity)
                else:
                    self._backend = module.SupervisorClient(self._state_root, root=self._state_root / "runtime")
            return self._backend

    def central_status(self, *, deadline_epoch: float | None = None) -> dict[str, Any]:
        """Query cheap owner metadata without starting a daemon or probing GPUs."""
        client = self._get_backend()
        deadline = min(deadline_epoch, time.time() + 2) if deadline_epoch is not None else time.time() + 2
        value = client.observer_status(deadline_epoch=deadline)
        if not isinstance(value, dict):
            raise RuntimeError("central_supervisor_status_invalid")
        if value.get("observer_contract") != "PC-OBSERVER-SUPERVISOR/1":
            raise RuntimeError("central_supervisor_contract_mismatch")
        if value.get("source_sha256") != self._source_sha256:
            raise RuntimeError("central_supervisor_source_mismatch")
        if (value.get("service_state_root") != str(self._state_root)
                or value.get("runtime_root") != str(self._state_root / "runtime")):
            raise RuntimeError("central_supervisor_root_mismatch")
        if value.get("observer_only") is not True:
            raise RuntimeError("central_supervisor_ownership_mismatch")
        pid, start = value.get("supervisor_pid"), value.get("supervisor_process_start")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or not start:
            raise RuntimeError("central_supervisor_ownership_invalid")
        if self._allowed_gpu_uuids is not None and set(value.get("allowed_gpu_uuids") or []) != set(self._allowed_gpu_uuids):
            raise RuntimeError("central_supervisor_gpu_policy_mismatch")
        return value

    def _checked_client(self, deadline_epoch: float | None = None) -> Any:
        self.central_status(deadline_epoch=deadline_epoch)
        return self._get_backend()

    @staticmethod
    def _failure(error: Exception) -> str:
        if isinstance(error, RuntimeBindingError):
            return "observer_analysis_runtime_binding_invalid"
        if isinstance(error, TimeoutError) or str(error) == "central_supervisor_timeout":
            return "central_supervisor_transport_timeout"
        if isinstance(error, FileNotFoundError) or isinstance(error, ConnectionRefusedError):
            return "central_supervisor_unavailable"
        if isinstance(error, OSError):
            return "central_supervisor_transport_error"
        return str(error)[:500]

    def analyze(self, immutable_packet: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        outcome = "unavailable"
        try:
            encoded = json.dumps(immutable_packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            # JSON round-trip makes the backend's input an inert, bounded copy.
            if len(encoded.encode("utf-8")) > 64 * 1024:
                raise ValueError("observer_packet_invalid_or_too_large")
            packet = json.loads(encoded)
            packet["deadline_epoch"] = min(float(packet.get("deadline_epoch", time.time() + 60)), time.time() + 60)
            write_event({"event": "model_request", "phase": "started", "call_id": call_id_var.get(),
                         "operation": "observer_analyze", "packet_keys": sorted(packet)[:24],
                         "packet_bytes": len(encoded.encode("utf-8"))})
            result = self._checked_client(packet.get("deadline_epoch")).analyze_observer_packet(packet)
            if not isinstance(result, dict):
                raise ValueError("observer_provider_invalid_result")
            result["authoritative"] = False
            result["mutation_authority"] = False
            outcome = "returned"
            return result
        except Exception as error:
            return compact_packet_fallback(immutable_packet, self._failure(error))
        finally:
            write_event({"event": "model_request", "phase": "finished", "call_id": call_id_var.get(),
                         "operation": "observer_analyze", "outcome": outcome,
                         "duration_ms": round((time.monotonic() - started) * 1000, 1)})

    def investigate_turn(self, request: dict[str, Any]) -> dict[str, Any]:
        """Forward one bounded Project-Control-owned conversational turn."""
        started = time.monotonic()
        outcome = "unavailable"
        try:
            messages = request.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError("local_investigator_messages_missing")
            turn_policy_id = request.get("turn_policy_id")
            if turn_policy_id is None:
                reasoning_mode = request.get("reasoning_mode", "auto")
                if reasoning_mode not in {"auto", "off"}:
                    raise ValueError("local_investigator_reasoning_mode_invalid")
            else:
                reasoning_mode = None
            response_format = request.get("response_format")
            if "response_format" in request and not isinstance(response_format, dict):
                raise ValueError("local_investigator_response_format_invalid")
            # The broker selects a trusted policy; the supervisor resolves its
            # budgets and instructions from its own registry. Never translate
            # that selector into caller-provided numeric generation settings.
            if turn_policy_id is not None:
                from .assistance.policies import validate_policy_id
                turn_policy_id = validate_policy_id(turn_policy_id)
            write_event({"event": "model_request", "phase": "started", "call_id": call_id_var.get(),
                         "operation": "investigate_turn", "messages": summarize_messages(messages)})
            backend_request = {
                "format": "PC-LOCAL-INVESTIGATOR-TURN/2",
                "messages": messages,
                "timeout_seconds": min(60.0, float(request.get("timeout_seconds", 60))),
                "compute_profile": request.get("compute_profile", "narrow"),
                "parallelism": request.get("parallelism", "default"),
                **({"response_format": response_format} if "response_format" in request else {}),
                **({"deadline_epoch": request["deadline_epoch"]} if "deadline_epoch" in request else {}),
                **({"session_id": request["session_id"]} if request.get("session_id") else {}),
            }
            if turn_policy_id is None:
                # Keep the established generation contract for legacy callers.
                backend_request["max_tokens"] = int(request.get("max_tokens", 2048))
                backend_request["reasoning_mode"] = reasoning_mode
            else:
                backend_request["turn_policy_id"] = turn_policy_id
            backend_request["deadline_epoch"] = min(float(backend_request.get("deadline_epoch", time.time() + backend_request["timeout_seconds"])), time.time() + backend_request["timeout_seconds"])
            encoded = json.dumps(backend_request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if len(encoded.encode("utf-8")) > 1024 * 1024:
                raise ValueError("local_investigator_turn_too_large")
            result = self._checked_client(backend_request.get("deadline_epoch")).run_observer_turn(json.loads(encoded))
            if not isinstance(result, dict):
                raise ValueError("local_investigator_invalid_result")
            outcome = "returned" if result.get("status") == "available" else "unavailable"
            return result
        except Exception as error:
            return {"status": "unavailable", "reason": self._failure(error)}
        finally:
            write_event({"event": "model_request", "phase": "finished", "call_id": call_id_var.get(),
                         "operation": "investigate_turn", "outcome": outcome,
                         "duration_ms": round((time.monotonic() - started) * 1000, 1)})

    def open_sessions(self, count: int, *, compute_profile: str, parallelism: str, deadline_epoch: float | None = None) -> dict[str, Any]:
        try:
            # This deadline is also the borrower lease lifetime, spanning turns.
            # Only individual model turns have the shorter 60-second budget.
            deadline_epoch = deadline_epoch if deadline_epoch is not None else time.time() + 300
            return self._checked_client(deadline_epoch).open_observer_sessions(
                count, compute_profile=compute_profile, parallelism=parallelism,
                **({"deadline_epoch": deadline_epoch} if deadline_epoch is not None else {}))
        except Exception as error:
            return {"status": "unavailable", "reason": self._failure(error)}

    def close_session(self, session_id: str) -> dict[str, Any]:
        # The broker retains the dispatch slot until owned cleanup succeeds.
        # A failed cleanup must remain observable and retryable.
        deadline_epoch = time.time() + 10
        result = self._checked_client(deadline_epoch).close_observer_session(session_id, deadline_epoch=deadline_epoch)
        if not isinstance(result, dict) or result.get("released") is not True:
            raise RuntimeError("observer_session_not_quiescent")
        return result

    def reclaim_orphaned_sessions(self, resources: Any, release_request_id: str) -> list[str]:
        """Attach daemon-minted receipts for sessions whose borrower died."""
        select_reclaimable = getattr(resources, "reclaimable_session_ids", None)
        if not callable(select_reclaimable):
            raise RuntimeError("observer_reclaim_state_unavailable")
        session_ids = select_reclaimable(release_request_id)
        if not session_ids:
            return []
        if (not isinstance(session_ids, list) or
                any(not isinstance(item, str) for item in session_ids) or
                session_ids != sorted(set(session_ids))):
            raise RuntimeError("observer_reclaim_release_intent_mismatch")
        if len(session_ids) > 64:
            raise RuntimeError("observer_reclaim_session_batch_too_large")
        deadline = time.time() + 5
        client = self._checked_client(deadline)
        request_id = hashlib.sha256(("PC-OBSERVER-RECLAIM/1\0" + release_request_id + "\0" +
            "\n".join(session_ids)).encode("utf-8")).hexdigest()[:32]
        result = client.reclaim_closed_observer_sessions(session_ids,
            request_id=request_id, deadline_epoch=deadline)
        receipts = result.get("receipts") if isinstance(result, dict) else None
        if (not isinstance(receipts, list) or len(receipts) != len(session_ids) or
                result.get("status") != "available"):
            return []
        observed = set()
        for receipt in receipts:
            if not isinstance(receipt, dict) or not isinstance(receipt.get("session_id"), str):
                return []
            resources.record_session(receipt["session_id"], receipt)
            observed.add(receipt["session_id"])
        if observed != set(session_ids):
            raise RuntimeError("observer_reclaim_receipt_set_mismatch")
        return session_ids

    def release_idle_runtime(self, *, deadline_epoch: float) -> dict[str, Any]:
        """Physically release verified idle model slots before broker stop proof.

        This is deliberately not a general stop API. The broker must first set
        the durable release veto; then this method requires an authenticated
        central owner snapshot with no leases/admissions and only known idle,
        unleased slots. A missing daemon is never started to perform cleanup.
        """
        if isinstance(deadline_epoch, bool) or not isinstance(deadline_epoch, (int, float)):
            raise ValueError("observer_runtime_release_deadline_invalid")
        deadline_epoch = float(deadline_epoch)
        if deadline_epoch <= time.time():
            raise TimeoutError("observer_runtime_release_deadline_expired")

        client = self._checked_client(deadline_epoch)
        status = self.central_status(deadline_epoch=deadline_epoch)
        owner = (status.get("supervisor_pid"), status.get("supervisor_process_start"))
        if (getattr(client, "_observer_owner", None) != owner or
                getattr(client, "_observer_daemon_epoch", None) != status.get("daemon_epoch")):
            raise RuntimeError("observer_runtime_release_owner_changed")
        runtime_fingerprint = status.get("runtime_fingerprint")
        daemon_epoch = status.get("daemon_epoch")
        if (not isinstance(runtime_fingerprint, str) or len(runtime_fingerprint) != 64 or
                not isinstance(daemon_epoch, str) or len(daemon_epoch) != 64):
            raise RuntimeError("observer_runtime_release_owner_identity_invalid")
        if (type(status.get("active_leases")) is not int or status["active_leases"] != 0 or
                type(status.get("active_admissions")) is not int or status["active_admissions"] != 0):
            raise RuntimeError("observer_runtime_release_not_quiescent")
        slots = status.get("slots")
        if not isinstance(slots, list):
            raise RuntimeError("observer_runtime_release_slots_invalid")
        for slot in slots:
            if (not isinstance(slot, dict) or slot.get("state") != "idle" or
                    slot.get("leased") is not False or not isinstance(slot.get("slot_id"), str) or
                    not isinstance(slot.get("owner_id"), str) or
                    isinstance(slot.get("server_pid"), bool) or not isinstance(slot.get("server_pid"), int) or
                    slot.get("server_pid") <= 0 or not isinstance(slot.get("gpu_uuids"), list) or
                    not slot["gpu_uuids"] or any(not isinstance(uuid, str) or not uuid for uuid in slot["gpu_uuids"])):
                raise RuntimeError("observer_runtime_release_slot_not_idle")
        if not slots:
            return {"status": "released", "quiescent": True, "evicted": True,
                    "stopped": False, "already_empty": True, "cleanup_receipts": []}

        stop = getattr(client, "stop_if_quiescent", None)
        if not callable(stop):
            raise RuntimeError("observer_runtime_release_operation_unavailable")
        receipt = stop(deadline_epoch=deadline_epoch)
        if (not isinstance(receipt, dict) or receipt.get("stopped") is not True or
                receipt.get("quiescent") is not True or receipt.get("evicted") is not True):
            raise RuntimeError("observer_runtime_release_not_quiescent")
        if (receipt.get("supervisor_pid") != status.get("supervisor_pid") or
                receipt.get("supervisor_process_start") != status.get("supervisor_process_start") or
                receipt.get("daemon_epoch") != daemon_epoch):
            raise RuntimeError("observer_runtime_release_owner_changed")
        cleanup = receipt.get("cleanup_receipts")
        if not isinstance(cleanup, list) or len(cleanup) != len(slots):
            raise RuntimeError("observer_runtime_release_receipts_incomplete")
        expected = {(slot["owner_id"], slot["server_pid"], tuple(sorted(set(slot["gpu_uuids"]))))
                    for slot in slots}
        observed = set()
        for item in cleanup:
            if not isinstance(item, dict):
                raise RuntimeError("observer_runtime_release_receipt_invalid")
            key = (item.get("owner_id"), item.get("owned_pid"),
                   tuple(sorted(set(item.get("gpu_uuids", []))))
                   if isinstance(item.get("gpu_uuids"), list) and
                   all(isinstance(uuid, str) for uuid in item["gpu_uuids"]) else ())
            if (item.get("released") is not True or item.get("process_released") is not True or
                    item.get("memory_released") is not True or key not in expected or key in observed):
                raise RuntimeError("observer_runtime_release_receipt_invalid")
            observed.add(key)
        if observed != expected:
            raise RuntimeError("observer_runtime_release_receipts_incomplete")
        value = {"format": "PA1-PHYSICAL-RELEASE/1", "captured_at": time.time(),
                "status": "released_verified", "released_verified": True,
                "supervisor_pid": status["supervisor_pid"],
                "supervisor_process_start": status["supervisor_process_start"],
                "daemon_epoch": daemon_epoch, "runtime_fingerprint": runtime_fingerprint,
                "quiescent": True, "evicted": True, "stopped": True,
                "gpu_uuids": sorted({uuid for item in cleanup for uuid in item.get("gpu_uuids", [])}),
                "cleanup_receipts": cleanup}
        return _VerifiedPhysicalRelease(_PHYSICAL_RELEASE_SEAL, value, socket.gethostname())

    def verify_saved_physical_release(self, record: dict[str, Any]) -> _VerifiedPhysicalRelease:
        """Revalidate a private saved stop receipt before reconciling orphan rows.

        This is intentionally stricter than accepting a JSON receipt: it checks
        exact daemon and server process generations, the receipt's fresh native
        GPU observations, and the operator-configured UUID scope on this host.
        """
        if not isinstance(record, dict) or set(record) != {
                "captured_at", "selected_manifest_sha256", "before", "release", "broker_before"}:
            raise RuntimeError("physical_release_record_invalid")
        now = time.time()
        captured = record.get("captured_at")
        if (isinstance(captured, bool) or not isinstance(captured, (int, float)) or
                now < float(captured)):
            raise RuntimeError("physical_release_record_invalid")
        if (not isinstance(record.get("selected_manifest_sha256"), str) or
                re.fullmatch(r"[0-9a-f]{64}", record["selected_manifest_sha256"]) is None):
            raise RuntimeError("physical_release_record_identity_invalid")
        before = record.get("broker_before")
        if (not isinstance(before, dict) or before.get("active_work_truncated") is not False or
                before.get("active_work") != []):
            raise RuntimeError("physical_release_controller_not_quiescent")
        orphan_execution_slots = _orphan_cleanup_slots_from_status(before)
        release = record.get("release")
        if (not isinstance(release, dict) or release.get("status") != "released_verified" or
                release.get("released_verified") is not True or release.get("quiescent") is not True or
                release.get("evicted") is not True or release.get("stopped") is not True or
                type(release.get("supervisor_pid")) is not int or release["supervisor_pid"] <= 0 or
                not isinstance(release.get("supervisor_process_start"), str) or
                not isinstance(release.get("daemon_epoch"), str) or
                re.fullmatch(r"[0-9a-f]{64}", release["daemon_epoch"]) is None or
                not isinstance(release.get("runtime_fingerprint"), str) or
                re.fullmatch(r"[0-9a-f]{64}", release["runtime_fingerprint"]) is None):
            raise RuntimeError("physical_release_receipt_invalid")
        before = record.get("before")
        if (not isinstance(before, dict) or before.get("format") != "CORE4-OBSERVER-STATUS/1" or
                before.get("running") is not True or before.get("healthy") is not True or
                before.get("active_leases") != 0 or before.get("active_admissions") != 0 or
                before.get("supervisor_pid") != release["supervisor_pid"] or
                before.get("supervisor_process_start") != release["supervisor_process_start"] or
                before.get("daemon_epoch") != release["daemon_epoch"] or
                before.get("runtime_fingerprint") != release["runtime_fingerprint"]):
            raise RuntimeError("physical_release_pre_stop_identity_invalid")
        if self._allowed_gpu_uuids is None:
            raise RuntimeError("physical_release_gpu_scope_unbound")
        if _proc_start_time(release["supervisor_pid"]) == release["supervisor_process_start"]:
            raise RuntimeError("physical_release_supervisor_still_running")
        cleanup = release.get("cleanup_receipts")
        prior_slots = before.get("slots")
        if (not isinstance(prior_slots, list) or not 1 <= len(prior_slots) <= 4 or
                any(not isinstance(slot, dict) or slot.get("state") != "idle" or
                    slot.get("leased") is not False or slot.get("service_lease_id") is not None
                    for slot in prior_slots)):
            raise RuntimeError("physical_release_pre_stop_slots_invalid")
        if not isinstance(cleanup, list) or not 1 <= len(cleanup) <= 4:
            raise RuntimeError("physical_release_receipts_invalid")
        seen_slots: set[tuple[str, int, str]] = set()
        seen_gpus: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for item in cleanup:
            if not isinstance(item, dict):
                raise RuntimeError("physical_release_receipt_invalid")
            owner_id, pid, start = item.get("owner_id"), item.get("owned_pid"), item.get("server_process_start")
            uuids = item.get("gpu_uuids")
            if (not isinstance(owner_id, str) or not owner_id or type(pid) is not int or pid <= 0 or
                    not isinstance(start, str) or not start or not isinstance(item.get("generation"), str) or
                    not item["generation"] or not isinstance(uuids, list) or not 1 <= len(uuids) <= 4 or
                    any(not isinstance(uuid, str) or uuid not in self._allowed_gpu_uuids for uuid in uuids) or
                    len(set(uuids)) != len(uuids) or seen_gpus.intersection(uuids) or
                    item.get("released") is not True or item.get("process_released") is not True or
                    item.get("memory_released") is not True):
                raise RuntimeError("physical_release_receipt_invalid")
            seen_slots.add((owner_id, pid, start))
            seen_gpus.update(uuids)
            if _proc_start_time(pid) == start:
                raise RuntimeError("physical_release_server_still_running")
            observation = item.get("observation")
            observed_at = observation.get("observed_unix") if isinstance(observation, dict) else None
            devices = observation.get("devices") if isinstance(observation, dict) else None
            if (not isinstance(observation, dict) or observation.get("available") is not True or
                    not isinstance(observed_at, (int, float)) or isinstance(observed_at, bool) or
                    float(observed_at) > float(captured) or float(captured) - float(observed_at) > 60 or
                    observation.get("processes") != [] or not isinstance(devices, list)):
                raise RuntimeError("physical_release_observation_invalid")
            observed_devices = {}
            for device in devices:
                if (not isinstance(device, dict) or not isinstance(device.get("uuid"), str) or
                        isinstance(device.get("memory_used_mib"), bool) or
                        not isinstance(device.get("memory_used_mib"), (int, float))):
                    raise RuntimeError("physical_release_observation_invalid")
                observed_devices[device["uuid"]] = float(device["memory_used_mib"])
            if set(observed_devices) != set(uuids) or any(value != 0 for value in observed_devices.values()):
                raise RuntimeError("physical_release_gpu_memory_not_zero")
            normalized.append({"owner_id": owner_id, "owned_pid": pid,
                "server_process_start": start, "generation": item["generation"],
                "gpu_uuids": list(uuids), "observation": observation})
        if len(seen_slots) != len(cleanup) or not seen_gpus:
            raise RuntimeError("physical_release_receipts_duplicate_or_empty")
        prior_identity = {(slot.get("owner_id"), slot.get("server_pid"), tuple(slot.get("gpu_uuids", [])))
                          for slot in prior_slots}
        cleanup_identity = {(item.get("owner_id"), item.get("owned_pid"), tuple(item.get("gpu_uuids", [])))
                            for item in cleanup}
        if cleanup_identity != prior_identity:
            raise RuntimeError("physical_release_slot_set_mismatch")
        # Validate that the historical physical receipt still describes the
        # current host: exact GPUs are empty, and none of the released process
        # generations or host reservations have reappeared.
        try:
            gpu_result = subprocess.run(["nvidia-smi", "--query-gpu=uuid,memory.used",
                "--format=csv,noheader,nounits"], check=True, capture_output=True, text=True, timeout=8)
            app_result = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,gpu_uuid,used_memory",
                "--format=csv,noheader,nounits"], check=True, capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError("physical_release_fresh_gpu_observation_unavailable") from None
        current_gpu_memory: dict[str, float] = {}
        for row in csv.reader(gpu_result.stdout.splitlines()):
            if len(row) != 2:
                raise RuntimeError("physical_release_fresh_gpu_observation_invalid")
            try:
                current_gpu_memory[row[0].strip()] = float(row[1].strip())
            except ValueError:
                raise RuntimeError("physical_release_fresh_gpu_observation_invalid") from None
        if any(uuid not in current_gpu_memory or current_gpu_memory[uuid] != 0 for uuid in seen_gpus):
            raise RuntimeError("physical_release_gpu_memory_not_zero")
        for row in csv.reader(app_result.stdout.splitlines()):
            if len(row) >= 2 and row[1].strip() in seen_gpus:
                raise RuntimeError("physical_release_gpu_process_reappeared")
        try:
            from todo_orchestrator.background.host import HostCoordinator
            host = HostCoordinator(create=False)
            connection = host.connect(readonly=True)
            try:
                owner_ids = sorted(item["owner_id"] for item in cleanup)
                placeholders = ",".join("?" for _ in owner_ids)
                active_owners = connection.execute(
                    f"SELECT count(*) FROM host_owners WHERE id IN ({placeholders}) AND state='active'",
                    owner_ids).fetchone()[0]
                active_reservations = connection.execute(
                    f"SELECT count(*) FROM host_reservations WHERE owner_id IN ({placeholders}) AND state='active'",
                    owner_ids).fetchone()[0]
            finally:
                connection.close()
        except Exception:
            raise RuntimeError("physical_release_fresh_host_observation_unavailable") from None
        if active_owners or active_reservations:
            raise RuntimeError("physical_release_host_owner_reappeared")
        token_value = {"format": "PA1-PHYSICAL-RELEASE/1", "captured_at": float(captured),
            "status": "released_verified", "released_verified": True,
            "selected_manifest_sha256": record["selected_manifest_sha256"],
            "daemon_epoch": release["daemon_epoch"], "supervisor_pid": release["supervisor_pid"],
            "supervisor_process_start": release["supervisor_process_start"],
            "runtime_fingerprint": release["runtime_fingerprint"], "quiescent": True,
            "evicted": True, "stopped": True, "gpu_uuids": sorted(seen_gpus),
            "cleanup_receipts": normalized, "orphan_execution_slots": orphan_execution_slots}
        token_value["captured_at"] = now
        token_value["source_proof_captured_at"] = float(captured)
        return _VerifiedPhysicalRelease(_PHYSICAL_RELEASE_SEAL, token_value, socket.gethostname())

    def verify_archived_physical_release(self, archived: dict[str, Any], *,
                                         broker_status: dict[str, Any]) -> _VerifiedPhysicalRelease:
        """Revalidate an archived stop receipt using fresh, local host evidence.

        This path supports only operator-directed source-mode recovery.  The
        archived supervisor receipt contributes the old epoch and exact owned
        process identities; it is not accepted as fresh physical evidence.
        Current source identity, both stopped user units, exact process absence,
        GPU state, host reservations, and failed broker slots are checked here
        before a private release token is minted.
        """
        # Bind the current receiver without constructing a supervisor client
        # or creating runtime state; this verifier is deliberately read-only.
        source_identity = _source_identity_snapshot()
        if not isinstance(archived, dict):
            raise RuntimeError("archived_physical_release_invalid")
        captured = archived.get("captured_at")
        if (isinstance(captured, bool) or not isinstance(captured, (int, float)) or
                not math.isfinite(float(captured)) or float(captured) <= 0 or
                float(captured) > time.time() + 5):
            raise RuntimeError("archived_physical_release_timestamp_invalid")
        daemon_epoch = archived.get("daemon_epoch")
        supervisor_pid = archived.get("supervisor_pid")
        supervisor_start = archived.get("supervisor_process_start")
        runtime_fingerprint = archived.get("runtime_fingerprint")
        if (archived.get("format") != "PA1-PHYSICAL-RELEASE/1" or
                archived.get("released_verified") is not True or archived.get("stopped") is not True or
                archived.get("evicted") is not True or archived.get("quiescent") is not True or
                archived.get("status", "released_verified") != "released_verified" or
                not isinstance(daemon_epoch, str) or re.fullmatch(r"[0-9a-f]{64}", daemon_epoch) is None or
                type(supervisor_pid) is not int or supervisor_pid <= 0 or
                not isinstance(supervisor_start, str) or not supervisor_start.isdigit() or
                not isinstance(runtime_fingerprint, str) or
                re.fullmatch(r"[0-9a-f]{64}", runtime_fingerprint) is None):
            raise RuntimeError("archived_physical_release_identity_invalid")
        allowed = self._allowed_gpu_uuids
        if (not isinstance(allowed, tuple) or not allowed or
                any(not isinstance(uuid, str) or not uuid.startswith("GPU-") for uuid in allowed)):
            raise RuntimeError("physical_release_gpu_scope_unbound")
        archive_gpu_uuids = archived.get("gpu_uuids")
        cleanup = archived.get("cleanup_receipts")
        if (not isinstance(archive_gpu_uuids, list) or len(archive_gpu_uuids) != len(set(archive_gpu_uuids)) or
                set(archive_gpu_uuids) != set(allowed) or not isinstance(cleanup, list) or
                not 1 <= len(cleanup) <= 4):
            raise RuntimeError("archived_physical_release_scope_invalid")
        normalized = []
        seen_slots: set[tuple[str, int, str]] = set()
        seen_gpus: set[str] = set()
        for item in cleanup:
            if not isinstance(item, dict):
                raise RuntimeError("archived_physical_release_receipt_invalid")
            owner_id, pid, start = item.get("owner_id"), item.get("owned_pid"), item.get("server_process_start")
            generation, uuids = item.get("generation"), item.get("gpu_uuids")
            if (not isinstance(owner_id, str) or not owner_id or type(pid) is not int or pid <= 0 or
                    not isinstance(start, str) or not start.isdigit() or
                    not isinstance(generation, str) or not generation or
                    not isinstance(uuids, list) or not 1 <= len(uuids) <= 4 or
                    any(not isinstance(uuid, str) or uuid not in allowed for uuid in uuids) or
                    len(set(uuids)) != len(uuids) or seen_gpus.intersection(uuids) or
                    item.get("released") is not True or item.get("process_released") is not True or
                    item.get("memory_released") is not True or
                    not isinstance(item.get("observation"), dict) or
                    not isinstance(item.get("memory_baseline"), dict)):
                raise RuntimeError("archived_physical_release_receipt_invalid")
            identity = (owner_id, pid, start)
            if identity in seen_slots:
                raise RuntimeError("archived_physical_release_receipt_duplicate")
            seen_slots.add(identity)
            seen_gpus.update(uuids)
            normalized.append({"owner_id": owner_id, "owned_pid": pid,
                "server_process_start": start, "generation": generation,
                "gpu_uuids": list(uuids), "released": True,
                "process_released": True, "memory_released": True})
        if seen_gpus != set(allowed):
            raise RuntimeError("archived_physical_release_scope_invalid")

        # Do not infer absence from stale status booleans.  Kernel process
        # identity and ESRCH must agree for the old supervisor and every owner.
        _require_process_absent(supervisor_pid, supervisor_start,
                                error_prefix="physical_release_supervisor")
        for item in normalized:
            _require_process_absent(item["owned_pid"], item["server_process_start"],
                                    error_prefix="physical_release_server")

        units = [_inactive_service_state("project-control.service"),
                 _inactive_service_state("project-control-inference.service")]
        gpu_observation = _fresh_gpu_release_observation(allowed)

        if (not isinstance(broker_status, dict) or broker_status.get("active_work") != [] or
                broker_status.get("active_work_truncated") is not False):
            raise RuntimeError("physical_release_controller_not_quiescent")
        orphan_slots = _orphan_cleanup_slots_from_status(broker_status)
        for item in orphan_slots:
            _require_process_absent(item["owner_pid"], item["owner_process_start"],
                                    error_prefix="physical_release_execution_owner")
        host_observation = _fresh_host_release_observation()

        if _source_identity_snapshot() != source_identity:
            raise RuntimeError("physical_release_source_identity_changed")
        captured_now = time.time()
        value = {"format": "PA1-PHYSICAL-RELEASE/1", "captured_at": captured_now,
            "status": "released_verified", "released_verified": True,
            "supervisor_pid": supervisor_pid, "supervisor_process_start": supervisor_start,
            "daemon_epoch": daemon_epoch, "runtime_fingerprint": runtime_fingerprint,
            "quiescent": True, "evicted": True, "stopped": True,
            "gpu_uuids": sorted(allowed), "cleanup_receipts": normalized,
            "orphan_execution_slots": orphan_slots,
            "recovery_kind": "source_mode_archived_whole_epoch",
            "source_identity": source_identity,
            "source_proof_captured_at": float(captured),
            "fresh_observations": {"service_units": units,
                "gpu": gpu_observation, "host": host_observation}}
        return _VerifiedPhysicalRelease(_PHYSICAL_RELEASE_SEAL, value, socket.gethostname())

    def close(self) -> None:
        """Disconnect this frontend; global residency belongs to the operator."""
        with self._backend_lock:
            client, self._backend = self._backend, None
        if client is not None:
            client.close()


class ObserverAnalysisRegistry:
    """One process-lifetime serialized local provider for inert project packets."""

    def __init__(self, factory: Any = SkillsObserverAnalysisProvider):
        self._factory = factory
        self._providers: dict[str, Any] = {}
        self._lock = threading.Lock()

    def analyze(self, repo_root: str | Path, packet: dict[str, Any]) -> dict[str, Any]:
        key = "local-observer-service"
        with self._lock:
            provider = self._providers.get(key)
            if provider is None:
                provider = self._factory(str(Path(repo_root).resolve()))
                self._providers[key] = provider
        return provider.analyze(packet)

    def analyze_packet(self, packet: dict[str, Any]) -> dict[str, Any]:
        """Analyze inert skill evidence without a repository or workflow binding."""
        try:
            if not isinstance(packet, dict) or not isinstance(packet.get("source_identity"), dict):
                raise ValueError("observer_packet_invalid")
            encoded = json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if len(encoded.encode("utf-8")) > 64 * 1024:
                raise ValueError("observer_packet_invalid_or_too_large")
            allowed = {"format", "query", "source_identity", "evidence", "origin", "authority", "mutation_authority"}
            if set(packet) - allowed:
                raise ValueError("observer_packet_invalid")
            forbidden = {"root", "repo_root", "path", "command", "command_line", "tools", "workflow_handle", "capabilities"}
            def validate(value: Any) -> None:
                if isinstance(value, dict):
                    if forbidden.intersection(value):
                        raise ValueError("observer_packet_capability_forbidden")
                    for child in value.values():
                        validate(child)
                elif isinstance(value, list):
                    for child in value:
                        validate(child)
            validate(packet)
            if any(isinstance(value, str) and Path(value).is_absolute()
                   for value in packet["source_identity"].values()):
                raise ValueError("observer_packet_private_identity_forbidden")
            rows = packet.get("evidence")
            if not isinstance(rows, list) or not rows or len(rows) > 64 or any(
                not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows
            ):
                raise ValueError("observer_packet_evidence_invalid")
            if len({row["id"] for row in rows}) != len(rows):
                raise ValueError("observer_packet_evidence_invalid")
            frozen = json.loads(encoded)
        except (TypeError, ValueError):
            return {"status": "unavailable", "reason": "observer_packet_invalid",
                    "authoritative": False, "mutation_authority": False}
        key = "local-observer-service"
        with self._lock:
            provider = self._providers.get(key)
            if provider is None:
                provider = self._factory(None)
                self._providers[key] = provider
        result = provider.analyze(frozen)
        if isinstance(result, dict) and result.get("status") == "unavailable":
            reason = result.get("reason")
            safe_reason = redact_output_text(reason)[:500] if isinstance(reason, str) else "observer_provider_unavailable"
            return compact_packet_fallback(frozen, safe_reason)
        ids = {row["id"] for row in rows}
        if not isinstance(result, dict) or not isinstance(result.get("evidence_ids"), list) or any(
            not isinstance(item, str) or item not in ids for item in result["evidence_ids"]
        ) or (result.get("status") == "available" and not result["evidence_ids"]):
            return compact_packet_fallback(frozen, "observer_provider_invalid_evidence")
        return {**result, "authoritative": False, "mutation_authority": False}

    def investigate_turn(self, repo_root: str | Path, request: dict[str, Any]) -> dict[str, Any]:
        key = "local-observer-service"
        with self._lock:
            provider = self._providers.get(key)
            if provider is None:
                provider = self._factory(str(Path(repo_root).resolve()))
                self._providers[key] = provider
        return provider.investigate_turn(request)

    def open_sessions(self, repo_root: str | Path, *, count: int, compute_profile: str,
                      parallelism: str, deadline_epoch: float | None = None) -> dict[str, Any]:
        key = "local-observer-service"
        with self._lock:
            provider = self._providers.get(key)
            if provider is None:
                provider = self._factory(str(Path(repo_root).resolve()))
                self._providers[key] = provider
        return provider.open_sessions(count, compute_profile=compute_profile, parallelism=parallelism,
                **({"deadline_epoch": deadline_epoch} if deadline_epoch is not None else {}))

    def close_session(self, repo_root: str | Path, session_id: str) -> dict[str, Any] | None:
        key = "local-observer-service"
        with self._lock:
            provider = self._providers.get(key)
        if provider is not None:
            return provider.close_session(session_id)
        return None

    def status(self) -> dict[str, Any]:
        """Return a compact snapshot of an already-created observer backend.

        Status must never cause local-worker import, backend construction, model
        admission, or service start.  The daemon snapshot remains the fallback
        for callers that have not used observer analysis in this process.
        """
        with self._lock:
            provider = self._providers.get("local-observer-service")
            backend = getattr(provider, "_backend", None) if provider is not None else None
        if backend is None:
            return {
                "status": "ok",
                "source": "observer_analysis_registry",
                "observed_state": "not_started",
                "running": False,
                "healthy": False,
                "draining": False,
                "capacity": None,
                "active_leases": 0,
                "active_admissions": 0,
                "slots": [],
                "confidence": "in_process_registry",
            }
        # Newer backends may expose a cheap, non-probing observer snapshot;
        # retain ``status`` for installed/runtime compatibility.
        status = getattr(provider, "central_status", None) or getattr(backend, "observer_status", None)
        if not callable(status):
            status = getattr(backend, "status", None)
        if not callable(status):
            return {"status": "partial", "source": "observer_analysis_backend",
                    "warnings": ["observer_backend_status_unavailable"]}
        try:
            value = status()
        except Exception:
            return {"status": "partial", "source": "observer_analysis_backend",
                    "warnings": ["observer_backend_status_unavailable"]}
        if not isinstance(value, dict):
            return {"status": "partial", "source": "observer_analysis_backend",
                    "warnings": ["observer_backend_status_invalid"]}
        slots = []
        for index, slot in enumerate(value.get("slots", [])):
            if not isinstance(slot, dict):
                continue
            slots.append({
                "slot": index,
                "state": slot.get("state", "unknown"),
                "leased": bool(slot.get("leased", False)),
            })
        return {
            "status": "ok",
            "source": "observer_analysis_backend",
            "observed_state": "running" if value.get("running") else "not_running",
            "running": bool(value.get("running", False)),
            "healthy": bool(value.get("healthy", False)),
            "draining": bool(value.get("draining", False)),
            "capacity": value.get("capacity") if isinstance(value.get("capacity"), int) else None,
            "active_leases": value.get("active_leases"),
            "active_admissions": (value.get("active_admissions")
                                  if isinstance(value.get("active_admissions"), int) else None),
            "slots": slots,
            "confidence": "central_supervisor" if hasattr(provider, "central_status") else "in_process_backend",
            **{key: value[key] for key in ("observer_contract", "supervisor_pid", "supervisor_process_start", "source_sha256") if key in value},
        }

    def close(self) -> None:
        with self._lock:
            providers, self._providers = list(self._providers.values()), {}
        for provider in providers:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
