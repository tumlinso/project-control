"""Bounded, claimless local analysis over immutable observer evidence."""

from __future__ import annotations

import json
import importlib
import os
import threading
from pathlib import Path
from typing import Any, Protocol


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


class DisabledObserverAnalysisProvider:
    available = False

    def analyze(self, immutable_packet: dict[str, Any]) -> dict[str, Any]:
        return compact_packet_fallback(immutable_packet, "observer_analysis_disabled")


class SkillsObserverAnalysisProvider:
    """Thin read-only bridge to Skills' serialized llama-server backend.

    Project Control supplies data, never a repository handle, tool, claim, or
    execution context.  The Skills backend owns model cache, topology and GPU
    reservation; any unavailable/busy condition has a deterministic fallback.
    """

    available = True

    def __init__(self, repo_root: str | Path):
        self._repo_root = Path(repo_root)
        self._backend: Any | None = None

    def _get_backend(self) -> Any:
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
            self._backend = ProductionBackend(state_root, service_state_root=state_root)
        return self._backend

    def analyze(self, immutable_packet: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(immutable_packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            # JSON round-trip makes the backend's input an inert, bounded copy.
            if len(encoded.encode("utf-8")) > 64 * 1024:
                raise ValueError("observer_packet_invalid_or_too_large")
            packet = json.loads(encoded)
            result = self._get_backend().analyze_observer_packet(packet)
            if not isinstance(result, dict):
                raise ValueError("observer_provider_invalid_result")
            result["authoritative"] = False
            result["mutation_authority"] = False
            return result
        except Exception as error:
            return compact_packet_fallback(immutable_packet, str(error))

    def close(self) -> None:
        """Release the one cached model slot during server shutdown."""
        backend, self._backend = self._backend, None
        if backend is not None:
            try:
                backend.poll()
            finally:
                backend.close()


class ObserverAnalysisRegistry:
    """Process-lifetime, per-repository serialized observer providers."""

    def __init__(self, factory: Any = SkillsObserverAnalysisProvider):
        self._factory = factory
        self._providers: dict[str, Any] = {}
        self._lock = threading.Lock()

    def analyze(self, repo_root: str | Path, packet: dict[str, Any]) -> dict[str, Any]:
        key = str(Path(repo_root).resolve())
        with self._lock:
            provider = self._providers.get(key)
            if provider is None:
                provider = self._factory(key)
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

    def close(self) -> None:
        with self._lock:
            providers, self._providers = list(self._providers.values()), {}
        for provider in providers:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
