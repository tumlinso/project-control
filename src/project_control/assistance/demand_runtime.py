"""Demand-only lifecycle for the unified Project Control inference service.

Importing this module and reading status never start inference. Call
``ensure_demand_runtime_ready`` only at an explicit demand entry point.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable, Mapping

from ..runtime_binding import (
    RELEASE_DIGEST_VARIABLE,
    RELEASE_MANIFEST_VARIABLE,
    bind_local_runtime,
)
from ..runtime_identity import bind_runtime


INFERENCE_SERVICE = "project-control-inference.service"
DEFAULT_STARTUP_SECONDS = 120.0
_START_LOCK = threading.RLock()
_TRANSIENT_SUPERVISOR_TRANSPORT_ERRORS = frozenset({
    "central_supervisor_unavailable",
    "central_supervisor_timeout",
    "central_supervisor_transport_timeout",
    "central_supervisor_transport_error",
})


class DemandRuntimeError(RuntimeError):
    """A demand could not safely start or use the selected inference runtime."""


def _is_transient_supervisor_transport_error(error: BaseException) -> bool:
    """Recognize only the supervisor's documented transient transport codes."""
    return isinstance(error, RuntimeError) and str(error) in _TRANSIENT_SUPERVISOR_TRANSPORT_ERRORS


@dataclass(frozen=True)
class RuntimePin:
    release_root: Path
    release_manifest: Path
    release_digest: str
    receiver_manifest_sha256: str
    receiver_fingerprint: str
    receiver_source_commit: str
    todo_identity: Mapping[str, Any]
    todo_runtime_fingerprint: str


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_runtime_context(receiver: Any) -> dict[str, Any]:
    """Bind the same Todo authority context used by the observer supervisor."""
    module = importlib.import_module("local_worker.canonical_runtime")
    source = Path(str(getattr(module, "__file__", ""))).resolve(strict=True)
    if receiver.package_root != source.parent and receiver.package_root not in source.parents:
        raise DemandRuntimeError("runtime_identity_mismatch")
    from ..observer_analysis import observer_analysis_state_root

    _, context = module.bind(observer_analysis_state_root(create=False))
    if not isinstance(context, dict):
        raise DemandRuntimeError("runtime_identity_mismatch")
    return dict(context)


def capture_runtime_pin() -> RuntimePin:
    """Resolve the selected installed release and both runtime identities."""
    manifest_value = os.environ.get(RELEASE_MANIFEST_VARIABLE)
    digest = os.environ.get(RELEASE_DIGEST_VARIABLE)
    if not manifest_value or not digest or len(digest) != 64:
        raise DemandRuntimeError("runtime_release_pin_missing")
    manifest = Path(manifest_value).expanduser().resolve(strict=True)
    try:
        raw = manifest.read_bytes()
    except OSError as error:
        raise DemandRuntimeError("runtime_release_manifest_unavailable") from error
    if _sha256(raw) != digest:
        raise DemandRuntimeError("runtime_release_digest_mismatch")
    try:
        release = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise DemandRuntimeError("runtime_release_manifest_invalid") from error
    if not isinstance(release, dict) or release.get("schema_version") != 2:
        raise DemandRuntimeError("runtime_release_manifest_invalid")
    release_root = manifest.parent.resolve()
    current_link = Path.home() / ".local/share/project-control/current"
    try:
        selected_root = current_link.resolve(strict=True)
    except OSError as error:
        raise DemandRuntimeError("selected_runtime_unavailable") from error
    if selected_root != release_root:
        raise DemandRuntimeError("selected_runtime_release_mismatch")

    try:
        todo = bind_runtime()
        receiver = bind_local_runtime(expected_release_digest=digest)
        runtime_context = _canonical_runtime_context(receiver)
    except Exception as error:
        raise DemandRuntimeError("runtime_identity_mismatch") from error
    if todo.release_digest != digest or todo.release_manifest is None:
        raise DemandRuntimeError("todo_release_identity_mismatch")
    receiver_manifest = receiver.root / "receiver-manifest.json"
    try:
        receiver_digest = _sha256(receiver_manifest.read_bytes())
    except OSError as error:
        raise DemandRuntimeError("receiver_manifest_unavailable") from error
    identity = runtime_context
    todo_runtime_fingerprint = _sha256(json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str).encode("utf-8"))
    return RuntimePin(
        release_root=release_root,
        release_manifest=manifest,
        release_digest=digest,
        receiver_manifest_sha256=receiver_digest,
        receiver_fingerprint=receiver.fingerprint,
        receiver_source_commit=receiver.source_commit,
        todo_identity=identity,
        todo_runtime_fingerprint=todo_runtime_fingerprint,
    )


def _power_release_veto() -> bool:
    # This is a private read-only state inspection. It does not create the
    # assistance store or initialize a broker.
    from .operator import AssistanceOperator

    return bool(AssistanceOperator().status().get("power", {}).get("release_veto_active"))


def _request_release_veto() -> Mapping[str, Any]:
    """Persist the explicit stop veto before cancelling or stopping anything."""
    from .operator import AssistanceOperator

    return AssistanceOperator().request_release(reason="operator-requested")


def _admission_guard(deadline_epoch: float):
    from .operator import AssistanceOperator

    return AssistanceOperator().admission_guard(deadline_epoch)


def _systemctl(action: str, *, timeout: float) -> subprocess.CompletedProcess[str]:
    if timeout <= 0:
        raise DemandRuntimeError("demand_deadline_exhausted_before_start")
    try:
        return subprocess.run(
            ["systemctl", "--user", action, INFERENCE_SERVICE],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise DemandRuntimeError("inference_service_start_timeout") from error
    except OSError as error:
        raise DemandRuntimeError("inference_service_manager_unavailable") from error


def _systemd_state(*, timeout: float = 2.0) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", INFERENCE_SERVICE,
             "--property=LoadState", "--property=ActiveState", "--property=SubState", "--property=MainPID"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        return {"status": "unavailable", "reason": "inference_service_status_unavailable"}
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or values.get("LoadState") != "loaded":
        return {"status": "unavailable", "reason": "inference_service_not_installed"}
    return {"status": "ok", **values}


def _process_release_matches(pid: int, pin: RuntimePin) -> bool:
    """Check only non-secret identity variables from the live service process."""
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return False
    environment: dict[str, str] = {}
    for item in raw.split(b"\0"):
        key, separator, value = item.partition(b"=")
        if separator and key in {b"PROJECT_CONTROL_RELEASE_MANIFEST", b"PROJECT_CONTROL_RELEASE_DIGEST",
                                 b"PROJECT_CONTROL_SKILLS_ROOT"}:
            environment[key.decode()] = value.decode(errors="strict")
    return (
        Path(environment.get(RELEASE_MANIFEST_VARIABLE, "/missing")).resolve() == pin.release_manifest
        and environment.get(RELEASE_DIGEST_VARIABLE) == pin.release_digest
        and Path(environment.get("PROJECT_CONTROL_SKILLS_ROOT", "/missing")).resolve()
        == (pin.release_root / "runtime-skills").resolve()
    )


def _central_ready(provider: Any, pin: RuntimePin,
                   process_matches: Callable[[int, RuntimePin], bool], *,
                   deadline_epoch: float, clock: Callable[[], float],
                   expected_main_pid: int) -> dict[str, Any]:
    status = provider.central_status(deadline_epoch=min(deadline_epoch, clock() + 2))
    pid = status.get("supervisor_pid")
    process_start = status.get("supervisor_process_start")
    daemon_epoch = status.get("daemon_epoch")
    if (isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0
            or not isinstance(process_start, str) or not process_start
            or not isinstance(daemon_epoch, str) or len(daemon_epoch) != 64
            or any(character not in "0123456789abcdef" for character in daemon_epoch)):
        raise DemandRuntimeError("inference_supervisor_ownership_invalid")
    if pid != expected_main_pid:
        raise DemandRuntimeError("inference_supervisor_service_pid_mismatch")
    if (status.get("receiver_manifest_sha256") != pin.receiver_manifest_sha256
            or status.get("receiver_fingerprint") != pin.receiver_fingerprint
            or status.get("receiver_source_commit") != pin.receiver_source_commit):
        raise DemandRuntimeError("inference_receiver_identity_mismatch")
    if status.get("runtime_identity") != dict(pin.todo_identity):
        raise DemandRuntimeError("inference_todo_identity_mismatch")
    if status.get("runtime_fingerprint") != pin.todo_runtime_fingerprint:
        raise DemandRuntimeError("inference_todo_fingerprint_mismatch")
    if not process_matches(pid, pin):
        raise DemandRuntimeError("inference_release_identity_mismatch")
    return status


class DemandRuntime:
    """Injectable service lifecycle, suitable for CLI and LAB callers."""

    def __init__(self, *, provider_factory: Callable[[], Any] | None = None,
                 pin_factory: Callable[[], RuntimePin] = capture_runtime_pin,
                 systemctl: Callable[..., subprocess.CompletedProcess[str]] = _systemctl,
                 state_reader: Callable[..., dict[str, str]] = _systemd_state,
                 release_veto: Callable[[], bool] = _power_release_veto,
                 release_request: Callable[[], Mapping[str, Any]] = _request_release_veto,
                 admission_guard_factory: Callable[[float], Any] = _admission_guard,
                 process_matches: Callable[[int, RuntimePin], bool] = _process_release_matches,
                 clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep):
        self._provider_factory = provider_factory
        self._pin_factory = pin_factory
        self._systemctl = systemctl
        self._state_reader = state_reader
        self._release_veto = release_veto
        self._release_request = release_request
        self._admission_guard_factory = admission_guard_factory
        self._process_matches = process_matches
        self._clock = clock
        self._sleep = sleeper

    def status(self) -> dict[str, Any]:
        """Read service state; inspect an active owner without starting inference."""
        state = self._state_reader()
        try:
            veto = self._release_veto()
        except Exception:
            veto = None
        result: dict[str, Any] = {"status": state.get("status", "unavailable"),
                                  "service": INFERENCE_SERVICE,
                                  "active_state": state.get("ActiveState"),
                                  "sub_state": state.get("SubState"),
                                  "main_pid": _int_or_none(state.get("MainPID")),
                                  "release_veto_active": veto,
                                  "readiness": "inactive"}
        if veto is True:
            result["next_action"] = ("project-control assistance stop" if result["active_state"] == "active"
                                      else "project-control assistance resume --release")
        if result["active_state"] != "active":
            return result
        if result["main_pid"] is None or result["main_pid"] <= 0:
            result["readiness"] = "starting"
            return result
        # central_status is a bounded metadata RPC. It neither starts the
        # supervisor nor admits a model, so status stays model-free.
        try:
            pin = self._pin_factory()
            provider = (self._provider_factory() if self._provider_factory is not None else None)
            if provider is None:
                from ..observer_analysis import SkillsObserverAnalysisProvider
                provider = SkillsObserverAnalysisProvider()
            owner = _central_ready(provider, pin, self._process_matches,
                                   deadline_epoch=self._clock() + 2, clock=self._clock,
                                   expected_main_pid=result["main_pid"])
            raw_slots = owner.get("slots", [])
            slots = [{"state": slot.get("state"), "leased": bool(slot.get("leased")),
                      "resident": slot.get("state") in {"ready", "warm"}}
                     for slot in raw_slots if isinstance(slot, dict)] if isinstance(raw_slots, list) else []
            result.update({
                "readiness": "verified_ready",
                "release_digest": pin.release_digest,
                "receiver_fingerprint": pin.receiver_fingerprint,
                "todo_runtime_fingerprint": pin.todo_runtime_fingerprint,
                "supervisor_pid": owner["supervisor_pid"],
                "supervisor_process_start": owner["supervisor_process_start"],
                "daemon_epoch": owner["daemon_epoch"],
                "healthy": owner.get("healthy"),
                "draining": owner.get("draining"),
                "capacity": owner.get("capacity"),
                "active_leases": owner.get("active_leases"),
                "active_admissions": owner.get("active_admissions"),
                "resident_slots": sum(item["resident"] for item in slots),
                "slots": slots,
            })
        except Exception as error:
            identity_failure = isinstance(error, DemandRuntimeError) or any(
                word in str(error).lower() for word in ("identity", "fingerprint", "release mismatch", "source mismatch"))
            result["readiness"] = "identity_mismatch" if identity_failure else "unavailable"
            if isinstance(error, DemandRuntimeError):
                result["readiness_reason"] = str(error)[:120]
            elif identity_failure:
                result["readiness_reason"] = "inference_runtime_identity_mismatch"
            elif (isinstance(error, (FileNotFoundError, ConnectionRefusedError, TimeoutError, OSError))
                  or _is_transient_supervisor_transport_error(error)):
                result["readiness_reason"] = "inference_supervisor_unavailable"
            else:
                result["readiness_reason"] = "inference_supervisor_status_invalid"
        return result

    def start(self, *, deadline_epoch: float | None = None) -> dict[str, Any]:
        return self.ensure_ready(deadline_epoch=deadline_epoch)

    def ensure_ready(self, *, deadline_epoch: float | None = None,
                     cancelled: Callable[[], bool] | None = None,
                     provider: Any | None = None) -> dict[str, Any]:
        """Start once, then wait for a matching supervisor within the deadline."""
        began = self._clock()
        startup_deadline = min(began + DEFAULT_STARTUP_SECONDS,
                               deadline_epoch if deadline_epoch is not None else began + DEFAULT_STARTUP_SECONDS)
        lock_timeout = startup_deadline - self._clock()
        if lock_timeout <= 0:
            raise DemandRuntimeError("demand_deadline_exhausted_before_start")
        if not _START_LOCK.acquire(timeout=lock_timeout):
            raise DemandRuntimeError("inference_start_lock_timeout")
        try:
            _remaining(startup_deadline, DEFAULT_STARTUP_SECONDS, self._clock)
            pin = self._pin_factory()
            _remaining(startup_deadline, DEFAULT_STARTUP_SECONDS, self._clock)
            release_veto = self._release_veto()
            _remaining(startup_deadline, DEFAULT_STARTUP_SECONDS, self._clock)
            if release_veto:
                raise DemandRuntimeError("assistance_release_veto_active; next_action=project-control assistance resume --release")
            is_cancelled = cancelled is not None and cancelled()
            _remaining(startup_deadline, DEFAULT_STARTUP_SECONDS, self._clock)
            if is_cancelled:
                raise DemandRuntimeError("demand_cancelled_before_start")
            remaining = _remaining(startup_deadline, DEFAULT_STARTUP_SECONDS, self._clock)
            try:
                with self._admission_guard_factory(startup_deadline):
                    remaining = _remaining(startup_deadline, DEFAULT_STARTUP_SECONDS, self._clock)
                    started = self._systemctl("start", timeout=remaining)
                    _remaining(startup_deadline, DEFAULT_STARTUP_SECONDS, self._clock)
            except Exception as error:
                if "assistance_release_veto_active" in str(error):
                    raise DemandRuntimeError("assistance_release_veto_active; next_action=project-control assistance resume --release") from None
                if isinstance(error, TimeoutError) or "deadline_invalid" in str(error):
                    raise DemandRuntimeError("demand_deadline_exhausted_before_start") from None
                raise
            if started.returncode != 0:
                detail = (started.stderr or started.stdout or "").strip()[:300]
                raise DemandRuntimeError("inference_service_start_failed" + (f": {detail}" if detail else ""))
        finally:
            _START_LOCK.release()

        active_provider = provider
        if active_provider is None:
            if self._provider_factory is None:
                from ..observer_analysis import SkillsObserverAnalysisProvider
                active_provider = SkillsObserverAnalysisProvider()
            else:
                active_provider = self._provider_factory()
        deadline = startup_deadline
        _remaining(deadline, DEFAULT_STARTUP_SECONDS, self._clock)
        last_transient = "inference_supervisor_not_ready"
        while self._clock() < deadline:
            if cancelled is not None and cancelled():
                raise DemandRuntimeError("demand_cancelled_during_startup")
            _remaining(deadline, DEFAULT_STARTUP_SECONDS, self._clock)
            if self._release_veto():
                raise DemandRuntimeError("assistance_release_veto_active; service_may_be_active=true; cleanup_required=true; next_action=project-control assistance stop")
            _remaining(deadline, DEFAULT_STARTUP_SECONDS, self._clock)
            try:
                state_timeout = min(2.0, _remaining(deadline, DEFAULT_STARTUP_SECONDS, self._clock))
                unit = self._state_reader(timeout=state_timeout)
                _remaining(deadline, DEFAULT_STARTUP_SECONDS, self._clock)
                if unit.get("status") != "ok":
                    last_transient = unit.get("reason", "inference_service_status_unavailable")
                    self._sleep(min(0.25, _remaining(deadline, DEFAULT_STARTUP_SECONDS, self._clock)))
                    continue
                if unit.get("ActiveState") != "active":
                    last_transient = "inference_service_not_active"
                    self._sleep(min(0.25, _remaining(deadline, DEFAULT_STARTUP_SECONDS, self._clock)))
                    continue
                main_pid = _int_or_none(unit.get("MainPID"))
                if main_pid is None or main_pid <= 0:
                    last_transient = "inference_service_starting"
                    self._sleep(min(0.25, _remaining(deadline, DEFAULT_STARTUP_SECONDS, self._clock)))
                    continue
                status = _central_ready(active_provider, pin, self._process_matches,
                                        deadline_epoch=deadline, clock=self._clock,
                                        expected_main_pid=main_pid)
                _remaining(deadline, DEFAULT_STARTUP_SECONDS, self._clock)
                if self._pin_factory() != pin:
                    raise DemandRuntimeError("selected_runtime_changed_during_startup")
                _remaining(deadline, DEFAULT_STARTUP_SECONDS, self._clock)
                return {"status": "ready", "service": INFERENCE_SERVICE,
                        "release_digest": pin.release_digest,
                        "receiver_fingerprint": pin.receiver_fingerprint,
                        "todo_runtime_fingerprint": pin.todo_runtime_fingerprint,
                        "supervisor_pid": status["supervisor_pid"],
                        "supervisor_process_start": status["supervisor_process_start"],
                        "daemon_epoch": status["daemon_epoch"]}
            except DemandRuntimeError:
                raise
            except (FileNotFoundError, ConnectionRefusedError, TimeoutError, OSError) as error:
                last_transient = str(error)[:200] or last_transient
            except RuntimeError as error:
                if not _is_transient_supervisor_transport_error(error):
                    raise
                last_transient = str(error)[:200] or last_transient
            if self._clock() >= deadline:
                break
            self._sleep(min(0.25, _remaining(deadline, DEFAULT_STARTUP_SECONDS, self._clock)))
        raise DemandRuntimeError(f"inference_supervisor_readiness_timeout: {last_transient}")

    def stop(self, *, coordinate_stop: Callable[[], Mapping[str, Any]] | None = None,
             timeout: float = 30.0) -> dict[str, Any]:
        """Persist the veto, then stop only after cancel/release is verified."""
        release = self._release_request()
        if not isinstance(release, Mapping) or release.get("release_veto_active") is not True:
            return {"status": "not_stopped", "reason": "release_veto_not_persisted",
                    "release_veto_active": False}
        next_action = "project-control assistance resume --release"
        if coordinate_stop is None:
            return {"status": "needs_coordination", "reason": "cancel_and_release_owned_work_first",
                    "release_veto_active": True, "next_action": next_action}
        proof = coordinate_stop()
        if (not isinstance(proof, Mapping) or proof.get("active_work_cancelled") is not True
                or proof.get("owned_resources_released") is not True):
            return {"status": "not_stopped", "reason": "owned_work_not_quiescent",
                    "release_veto_active": True, "next_action": next_action}
        result = self._systemctl("stop", timeout=min(30.0, max(0.1, timeout)))
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:300]
            raise DemandRuntimeError("inference_service_stop_failed" + (f": {detail}" if detail else ""))
        return {"status": "stopped", "service": INFERENCE_SERVICE,
                "release_veto_active": True, "next_action": next_action}


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _remaining(deadline_epoch: float | None, default: float, clock: Callable[[], float]) -> float:
    value = min(default, deadline_epoch - clock()) if deadline_epoch is not None else default
    if value <= 0:
        raise DemandRuntimeError("demand_deadline_exhausted_before_start")
    return value


def ensure_demand_runtime_ready(*, deadline_epoch: float | None = None,
                                cancelled: Callable[[], bool] | None = None,
                                provider: Any | None = None) -> dict[str, Any]:
    return DemandRuntime().ensure_ready(deadline_epoch=deadline_epoch, cancelled=cancelled,
                                        provider=provider)


def demand_runtime_status() -> dict[str, Any]:
    return DemandRuntime().status()


def start_demand_runtime(*, deadline_epoch: float | None = None) -> dict[str, Any]:
    return DemandRuntime().start(deadline_epoch=deadline_epoch)


def stop_demand_runtime(*, coordinate_stop: Callable[[], Mapping[str, Any]] | None = None) -> dict[str, Any]:
    return DemandRuntime().stop(coordinate_stop=coordinate_stop)
