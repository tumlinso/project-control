"""Bounded, claimless local analysis over immutable observer evidence."""

from __future__ import annotations

import json
import importlib
import os
import re
import threading
import time
import tomllib
from pathlib import Path
from typing import Any, Protocol

from .call_audit import call_id_var, summarize_messages, write_event
from .security import redact_output_text


def observer_analysis_state_root() -> Path:
    """Return the private service state root, never an observed project root."""
    configured = os.environ.get("PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")).expanduser()
        root = (base / "project-control" / "observer-analysis").resolve()
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
    """Thin read-only bridge to Skills' serialized llama-server backend.

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
                module = importlib.import_module("local_worker.supervisor")
                # The release installer binds this path through a manifest-pinned,
                # path-only .pth. Reject an ambient local-worker package rather
                # than accidentally broadening this observer boundary.
                expected = (Path(os.environ.get("PROJECT_CONTROL_SKILLS_ROOT", "")) /
                            "local-coding-worker").resolve()
                source = Path(str(getattr(module, "__file__", ""))).resolve()
                if not expected.is_dir() or expected not in source.parents:
                    raise RuntimeError("observer_analysis_runtime_binding_invalid")
                ProductionBackend = getattr(module, "ProductionBackend")
                # A local model service has its own private sidecars (SQLite
                # resource state, logs and runtime files).  The observed project
                # is evidence only and must never become its writable root.
                state_root = observer_analysis_state_root()
                options: dict[str, Any] = {"service_state_root": state_root}
                if self._allowed_gpu_uuids is not None:
                    # Load only the bound native profile on first backend use. Keep its
                    # model/server defaults and canonical runtime/global host admission.
                    profile = tomllib.loads((expected / "config/production-profile.toml").read_text(encoding="utf-8"))
                    profile.setdefault("deployment_policy", {})["allowed_gpu_uuids"] = list(self._allowed_gpu_uuids)
                    options["profile"] = profile
                self._backend = ProductionBackend(state_root, **options)
            return self._backend

    def analyze(self, immutable_packet: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        outcome = "unavailable"
        try:
            encoded = json.dumps(immutable_packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            # JSON round-trip makes the backend's input an inert, bounded copy.
            if len(encoded.encode("utf-8")) > 64 * 1024:
                raise ValueError("observer_packet_invalid_or_too_large")
            packet = json.loads(encoded)
            write_event({"event": "model_request", "phase": "started", "call_id": call_id_var.get(),
                         "operation": "observer_analyze", "packet_keys": sorted(packet)[:24],
                         "packet_bytes": len(encoded.encode("utf-8"))})
            result = self._get_backend().analyze_observer_packet(packet)
            if not isinstance(result, dict):
                raise ValueError("observer_provider_invalid_result")
            result["authoritative"] = False
            result["mutation_authority"] = False
            outcome = "returned"
            return result
        except Exception as error:
            return compact_packet_fallback(immutable_packet, str(error))
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
            write_event({"event": "model_request", "phase": "started", "call_id": call_id_var.get(),
                         "operation": "investigate_turn", "messages": summarize_messages(messages)})
            backend_request = {
                "format": "PC-LOCAL-INVESTIGATOR-TURN/2",
                "messages": messages,
                "max_tokens": int(request.get("max_tokens", 2048)),
                "timeout_seconds": float(request.get("timeout_seconds", 90)),
                "compute_profile": request.get("compute_profile", "wide"),
                "parallelism": request.get("parallelism", "default"),
                **({"deadline_epoch": request["deadline_epoch"]} if "deadline_epoch" in request else {}),
                **({"session_id": request["session_id"]} if request.get("session_id") else {}),
            }
            encoded = json.dumps(backend_request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if len(encoded.encode("utf-8")) > 256 * 1024:
                raise ValueError("local_investigator_turn_too_large")
            result = self._get_backend().run_observer_turn(json.loads(encoded))
            if not isinstance(result, dict):
                raise ValueError("local_investigator_invalid_result")
            outcome = "returned" if result.get("status") == "available" else "unavailable"
            return result
        except Exception as error:
            return {"status": "unavailable", "reason": str(error)[:500]}
        finally:
            write_event({"event": "model_request", "phase": "finished", "call_id": call_id_var.get(),
                         "operation": "investigate_turn", "outcome": outcome,
                         "duration_ms": round((time.monotonic() - started) * 1000, 1)})

    def open_sessions(self, count: int, *, compute_profile: str, parallelism: str, deadline_epoch: float | None = None) -> dict[str, Any]:
        try:
            return self._get_backend().open_observer_sessions(
                count, compute_profile=compute_profile, parallelism=parallelism,
                **({"deadline_epoch": deadline_epoch} if deadline_epoch is not None else {}))
        except Exception as error:
            return {"status": "unavailable", "reason": str(error)[:500]}

    def close_session(self, session_id: str) -> None:
        try:
            self._get_backend().close_observer_session(session_id)
        except Exception:
            pass

    def close(self) -> None:
        """Release the one cached model slot during server shutdown."""
        backend, self._backend = self._backend, None
        if backend is not None:
            try:
                backend.poll()
            finally:
                backend.close()


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
        try:
            return provider.analyze(packet)
        finally:
            backend = getattr(provider, "_backend", None)
            if backend is not None:
                # Idle slots are intentionally reusable; polling releases them
                # deterministically after TTL/preemption without a second
                # provider or GPU reservation.
                backend.poll()

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
        try:
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
        finally:
            backend = getattr(provider, "_backend", None)
            if backend is not None:
                backend.poll()

    def investigate_turn(self, repo_root: str | Path, request: dict[str, Any]) -> dict[str, Any]:
        key = "local-observer-service"
        with self._lock:
            provider = self._providers.get(key)
            if provider is None:
                provider = self._factory(str(Path(repo_root).resolve()))
                self._providers[key] = provider
        try:
            return provider.investigate_turn(request)
        finally:
            backend = getattr(provider, "_backend", None)
            if backend is not None:
                backend.poll()

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

    def close_session(self, repo_root: str | Path, session_id: str) -> None:
        key = "local-observer-service"
        with self._lock:
            provider = self._providers.get(key)
        if provider is not None:
            provider.close_session(session_id)

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
        status = getattr(backend, "observer_status", None)
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
            "confidence": "in_process_backend",
        }

    def close(self) -> None:
        with self._lock:
            providers, self._providers = list(self._providers.values()), {}
        for provider in providers:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
