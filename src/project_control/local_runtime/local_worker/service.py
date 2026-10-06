from __future__ import annotations

import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import threading
import time
from typing import Any, Iterator, Protocol
import uuid


class AdapterError(RuntimeError):
    pass


class LifecycleAdapter(Protocol):
    def inspect(self) -> dict[str, Any]: ...
    def start(self, context: dict[str, Any]) -> str: ...
    def health(self, handle: str) -> dict[str, Any]: ...
    def run(self, handle: str, request: dict[str, Any]) -> dict[str, Any]: ...
    def cancel(self, handle: str, request_id: str | None = None) -> dict[str, Any]: ...
    def drain(self, handle: str) -> dict[str, Any]: ...
    def evict(self, handle: str) -> dict[str, Any]: ...
    def usage(self, handle: str) -> dict[str, Any]: ...


class ResourceCoordinator(Protocol):
    def reserve_service(self, *, project_root: str | Path, service_id: str,
                        request: dict[str, object], pid: int,
                        priority_class: str = "idle_model_residency") -> tuple[str, list[str]] | None: ...
    def set_priority(self, owner_id: str, priority_class: str) -> bool: ...
    def preempt_requested(self, owner_id: str) -> bool: ...
    def heartbeat(self, owner_id: str, pid: int | None = None) -> None: ...
    def release(self, owner_id: str) -> None: ...


@dataclass(frozen=True)
class _ServiceLease:
    owner_id: str
    resource_ids: tuple[str, ...]


class AdapterService:
    """Small registry over replaceable adapters; it owns no work truth."""

    def __init__(self, *, resource_coordinator: ResourceCoordinator | None = None,
                 project_root: str | Path | None = None, pid: int | None = None,
                 resource_poll_seconds: float = 0.25) -> None:
        self._adapters: dict[str, LifecycleAdapter] = {}
        self._resources = resource_coordinator
        self._project_root = Path(project_root).resolve() if project_root is not None else None
        self._pid = pid or os.getpid()
        self._leases: dict[tuple[str, str], _ServiceLease] = {}
        self._preempted: set[tuple[str, str]] = set()
        self._lease_lock = threading.RLock()
        self._resource_poll_seconds = max(0.01, float(resource_poll_seconds))
        self._last_active: dict[tuple[str, str], float] = {}
        self._idle_ttl: dict[tuple[str, str], float] = {}

    def register(self, name: str, adapter: LifecycleAdapter) -> None:
        if not name or name in self._adapters:
            raise AdapterError(f"adapter name is empty or already registered: {name!r}")
        self._adapters[name] = adapter

    def inspect(self) -> dict[str, Any]:
        return {name: adapter.inspect() for name, adapter in sorted(self._adapters.items())}

    def _adapter(self, name: str) -> LifecycleAdapter:
        try:
            return self._adapters[name]
        except KeyError as error:
            raise AdapterError(f"unknown adapter: {name}") from error

    def start(self, name: str, context: dict[str, Any]) -> str:
        adapter = self._adapter(name)
        lease: _ServiceLease | None = None
        request = context.get("resource_request")
        if request is not None:
            if self._resources is None or self._project_root is None:
                raise AdapterError("resource_request requires a host resource coordinator and project_root")
            if not isinstance(request, dict):
                raise AdapterError("resource_request must be an object")
            service_id = str(context.get("service_id") or f"{name}-{uuid.uuid4()}")
            reserved = self._resources.reserve_service(
                project_root=self._project_root, service_id=service_id, request=request,
                pid=self._pid, priority_class="active_local_delegation",
            )
            if reserved is None:
                raise AdapterError("local delegation resources are pending lower-priority owner drain")
            lease = _ServiceLease(reserved[0], tuple(reserved[1]))
        try:
            handle = adapter.start(context)
        except Exception:
            if lease is not None and self._resources is not None:
                self._resources.release(lease.owner_id)
            raise
        if lease is not None:
            key = (name, handle)
            with self._lease_lock:
                self._preempted.discard(key)
                self._leases[key] = lease
                self._last_active[key] = time.monotonic()
                profile = dict(context.get("service_profile") or {})
                self._idle_ttl[key] = max(0.0, float(profile.get("idle_ttl_seconds", 0)))
            self._resources.set_priority(lease.owner_id, "idle_model_residency")
            threading.Thread(
                target=self._monitor_lease, args=(name, handle, lease),
                name=f"local-worker-resource-{name}", daemon=True,
            ).start()
        return handle

    def health(self, name: str, handle: str) -> dict[str, Any]:
        if self.reconcile(name, handle)["evicted"]:
            return {"healthy": False, "state": "evicted_by_resource_policy"}
        return self._adapter(name).health(handle)

    def run(self, name: str, handle: str, request: dict[str, Any]) -> dict[str, Any]:
        if self.reconcile(name, handle)["evicted"]:
            return {"status": "needs_codex", "outcome": "NEEDS_CODEX", "reason": "resource_preempted"}
        with self._lease_lock:
            lease = self._leases.get((name, handle))
        if lease is not None and self._resources is not None:
            if not self._resources.set_priority(lease.owner_id, "active_local_delegation"):
                self._evict_for_policy(name, handle, lease)
                return {"status": "needs_codex", "outcome": "NEEDS_CODEX", "reason": "resource_preempted"}
        result = self._adapter(name).run(handle, request)
        with self._lease_lock:
            self._last_active[(name, handle)] = time.monotonic()
        with self._lease_lock:
            lease = self._leases.get((name, handle))
        if lease is not None and self._resources is not None:
            if self._resources.preempt_requested(lease.owner_id):
                self._evict_for_policy(name, handle, lease)
                result = dict(result)
                result["resource_state"] = "evicted_after_run"
            else:
                self._resources.set_priority(lease.owner_id, "idle_model_residency")
                self._resources.heartbeat(lease.owner_id, self._pid)
        elif (name, handle) in self._preempted:
            result = dict(result)
            result["resource_state"] = "evicted_during_run"
        return result

    def cancel(self, name: str, handle: str, request_id: str | None = None) -> dict[str, Any]:
        return self._adapter(name).cancel(handle, request_id)

    def drain(self, name: str, handle: str) -> dict[str, Any]:
        return self._adapter(name).drain(handle)

    def evict(self, name: str, handle: str) -> dict[str, Any]:
        result = self._adapter(name).evict(handle)
        with self._lease_lock:
            lease = self._leases.pop((name, handle), None)
            self._preempted.discard((name, handle))
            self._last_active.pop((name, handle), None)
            self._idle_ttl.pop((name, handle), None)
        if lease is not None and self._resources is not None:
            self._resources.release(lease.owner_id)
        return result

    def usage(self, name: str, handle: str) -> dict[str, Any]:
        return self._adapter(name).usage(handle)

    def wait_for_quiescence(self, name: str, handle: str, *, timeout_seconds: float = 30.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        adapter = self._adapter(name)
        while time.monotonic() <= deadline:
            probe = getattr(adapter, "quiescent", None)
            if probe is not None and probe(handle):
                return True
            if self.health(name, handle).get("state") in {"evicted", "stopped", "evicted_by_resource_policy"}:
                return True
            time.sleep(min(self._resource_poll_seconds, 0.05))
        return False

    def reconcile(self, name: str, handle: str) -> dict[str, Any]:
        """Apply a host preemption signal without changing todo task state."""
        key = (name, handle)
        with self._lease_lock:
            lease = self._leases.get(key)
            was_preempted = key in self._preempted
        if was_preempted:
            return {"evicted": True}
        if lease is None or self._resources is None:
            return {"evicted": False}
        if not self._resources.preempt_requested(lease.owner_id):
            self._resources.heartbeat(lease.owner_id, self._pid)
            return {"evicted": False, "owner_id": lease.owner_id,
                    "resource_ids": list(lease.resource_ids)}
        self._evict_for_policy(name, handle, lease)
        return {"evicted": True, "owner_id": lease.owner_id,
                "resource_ids": list(lease.resource_ids)}

    def _evict_for_policy(self, name: str, handle: str, lease: _ServiceLease) -> None:
        key = (name, handle)
        with self._lease_lock:
            if self._leases.get(key) != lease:
                return
            self._leases.pop(key)
            self._preempted.add(key)
        adapter = self._adapter(name)
        try:
            adapter.drain(handle)
        finally:
            try:
                adapter.evict(handle)
            finally:
                if self._resources is not None:
                    self._resources.release(lease.owner_id)

    def _monitor_lease(self, name: str, handle: str, lease: _ServiceLease) -> None:
        while True:
            time.sleep(self._resource_poll_seconds)
            with self._lease_lock:
                if self._leases.get((name, handle)) != lease:
                    return
            if self._resources is None:
                return
            try:
                with self._lease_lock:
                    ttl = self._idle_ttl.get((name, handle), 0.0)
                    last_active = self._last_active.get((name, handle), time.monotonic())
                if ttl and time.monotonic() - last_active >= ttl:
                    self._evict_for_policy(name, handle, lease)
                    return
                if self._resources.preempt_requested(lease.owner_id):
                    self._evict_for_policy(name, handle, lease)
                    return
                self._resources.heartbeat(lease.owner_id, self._pid)
            except Exception:
                # The host runtime may disappear during interpreter or test
                # teardown; its stale-owner sweep remains the recovery path.
                return


@contextmanager
def disposable_task_context(prefix: str = "local-worker-task-") -> Iterator[Path]:
    """Yield isolated runtime state and remove it on every exit path."""
    with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
        root = Path(temporary)
        (root / "runtime").mkdir()
        yield root
