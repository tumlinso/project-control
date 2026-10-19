"""Host-only physical resource API for Project Control.

This facade handles machine inventory, process owners, capacity pressure and
preemption signals. It does not read or mutate Todo task authority state.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from itertools import combinations
from pathlib import Path
from typing import Callable, Iterable

from .contracts import ContractError, normalize_resource_request
from .coordinator import HostCoordinator
from .topology import discover_gpu_topology


def _private_resource_request(value: object | None) -> dict:
    normalized = normalize_resource_request(value)
    return {key: item for key, item in normalized.items() if key != "schema_version"}


class HostResourceFacade:
    def __init__(self, coordinator: HostCoordinator):
        self._coordinator = coordinator
        self._drain_callbacks: dict[str, Callable[[], bool | None]] = {}

    def upsert(self, resources: list[dict[str, object]]) -> None:
        normalized = []
        for item in resources:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ContractError("host resources require a non-empty id")
            kind = item.get("kind", "accelerator")
            tags = item.get("tags", {})
            if not isinstance(kind, str) or not kind or not isinstance(tags, dict):
                raise ContractError("host resource kind and tags are invalid")
            normalized.append({"id": item["id"], "kind": kind, "tags": tags, "enabled": bool(item.get("enabled", True))})
        self._coordinator.upsert_resources(normalized)

    def replace(self, kind: str, resources: list[dict[str, object]]) -> None:
        """Replace one runtime-discovered resource kind without retaining ghosts."""
        if not isinstance(kind, str) or not kind:
            raise ContractError("host resource kind must be non-empty")
        normalized = []
        for item in resources:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ContractError("host resources require a non-empty id")
            item_kind = item.get("kind", kind)
            tags = item.get("tags", {})
            if item_kind != kind or not isinstance(tags, dict):
                raise ContractError("replacement resources must share one kind and valid tags")
            normalized.append({"id": item["id"], "kind": kind, "tags": tags, "enabled": bool(item.get("enabled", True))})
        self._coordinator.replace_resources(kind, normalized)

    def list(self, *, kind: str | None = None) -> list[dict[str, object]]:
        try:
            connection = self._coordinator.connect(readonly=True)
        except sqlite3.Error:
            return []
        try:
            if kind is None:
                rows = connection.execute("SELECT * FROM host_resources ORDER BY id").fetchall()
            else:
                rows = connection.execute("SELECT * FROM host_resources WHERE kind=? ORDER BY id", (kind,)).fetchall()
            return [
                {"id": str(row["id"]), "kind": str(row["kind"]), "tags": json.loads(row["tags_json"]), "enabled": bool(row["enabled"])}
                for row in rows
            ]
        finally:
            connection.close()

    def reserve_background(self, *, project_root: str | Path, job_id: str, attempt_id: str,
                           resource_request: object, pid: int | None = None) -> dict[str, object] | None:
        reserved = self._coordinator.reserve_background(
            project_root=project_root,
            job_id=job_id,
            attempt_id=attempt_id,
            request=_private_resource_request(resource_request),
            pid=pid or os.getpid(),
        )
        return None if reserved is None else {"owner_id": reserved[0], "resource_ids": reserved[1]}

    def reserve_service(self, *, project_root: str | Path, service_id: str,
                        resource_request: object, priority_class: str = "idle_model_residency",
                        pid: int | None = None) -> dict[str, object] | None:
        reserved = self._coordinator.reserve_service(
            project_root=project_root,
            service_id=service_id,
            request=_private_resource_request(resource_request),
            pid=pid or os.getpid(),
            priority_class=priority_class,
        )
        return None if reserved is None else {"owner_id": reserved[0], "resource_ids": reserved[1]}

    def begin_foreground(self, *, project_root: str | Path, resource_request: object,
                         priority_class: str = "foreground_gpu",
                         pid: int | None = None) -> dict[str, object]:
        owner_id, resources = self._coordinator.begin_foreground(
            project_root=project_root,
            request=_private_resource_request(resource_request),
            pid=pid or os.getpid(),
            priority_class=priority_class,
        )
        return {"owner_id": owner_id, "resource_ids": resources}

    def activate_foreground(self, reservation: dict[str, object]) -> bool:
        return self._coordinator.activate_foreground(str(reservation["owner_id"]), [str(item) for item in reservation["resource_ids"]])

    def preempt_requested(self, owner_id: str) -> bool:
        return self._coordinator.preempt_requested(owner_id)

    def request_preemption(self, owner_id: str) -> bool:
        return self._coordinator.request_preemption(owner_id)

    def set_priority(self, owner_id: str, priority_class: str) -> bool:
        return self._coordinator.set_priority(owner_id, priority_class)

    def owner(self, owner_id: str) -> dict[str, object] | None:
        return self._coordinator.owner(owner_id)

    def owners_for_attempt(self, project_root: str | Path,
                           attempt_id: str) -> list[dict[str, object]]:
        """Read physical owner records for an exact project attempt."""
        return self._coordinator.owners_for_attempt(project_root, attempt_id)

    def conflicts(self, resource_ids: Iterable[str]) -> list[str]:
        return self._coordinator.conflicts(sorted({str(item) for item in resource_ids}))

    def wait_for_quiescence(self, resource_ids: Iterable[str], *, timeout_seconds: float = 30.0,
                            poll_seconds: float = 0.1) -> bool:
        resources = sorted({str(item) for item in resource_ids})
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while self.conflicts(resources):
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(0.01, poll_seconds))
        return True

    def register_drain_callback(self, owner_id: str, callback: Callable[[], bool | None]) -> None:
        if not callable(callback):
            raise ContractError("drain callback must be callable")
        self._drain_callbacks[owner_id] = callback

    def drain_if_requested(self, owner_id: str) -> bool:
        if not self.preempt_requested(owner_id):
            return False
        callback = self._drain_callbacks.get(owner_id)
        if callback is None or callback() is False:
            return False
        self.release(owner_id)
        return True

    def discover_gpus(self) -> list[dict[str, object]]:
        resources = discover_gpu_topology()
        self.replace("accelerator", resources)
        return resources

    def compound_gpu_bundles(self, count: int) -> list[dict[str, object]]:
        if count < 1:
            raise ContractError("GPU bundle count must be positive")
        # Discovery is a read-like operation, but its conflict filter must not
        # be poisoned by dead owners left by a crashed sidecar.  Reservation
        # still repeats this sweep inside its own transaction before claiming.
        self._coordinator.sweep_stale()
        devices = [item for item in self.list(kind="accelerator") if item["enabled"]]
        groups: dict[str, list[dict[str, object]]] = {}
        for device in devices:
            domain = str(device["tags"].get("nvlink_domain", device["id"]))
            groups.setdefault(domain, []).append(device)
        candidates = [combo for group in groups.values() for combo in combinations(group, count)]
        if not candidates:
            candidates = list(combinations(devices, count))
        bundles = []
        for combo in candidates:
            ids = sorted(str(item["id"]) for item in combo)
            # GPU IDs provide exact ownership and NVLink domains protect a
            # shared fabric.  PCIe-root topology is informative only: it is
            # not an interference domain for bundle reservations.
            exclusive = {
                f"interference:nvlink:{item['tags']['nvlink_domain']}"
                for item in combo if item["tags"].get("nvlink_domain")
            }
            numa_nodes = {str(item["tags"].get("numa_node", "unknown")) for item in combo}
            local_numa = len(numa_nodes) == 1 and "unknown" not in numa_nodes
            free_memory = sum(
                int(item["tags"].get("memory_free_mib", 0))
                for item in combo if str(item["tags"].get("memory_free_mib", "")).isdigit()
            )
            utilization = sum(
                int(item["tags"].get("utilization_percent", 100))
                for item in combo if str(item["tags"].get("utilization_percent", "")).isdigit()
            )
            if not self.conflicts([*ids, *exclusive]):
                bundles.append({
                    "resource_ids": ids, "exclusive_resources": sorted(exclusive),
                    "selection": {"numa_local": local_numa, "numa_nodes": sorted(numa_nodes),
                                  "memory_free_mib": free_memory, "utilization_percent_sum": utilization},
                })
        return sorted(bundles, key=lambda item: (
            not item["selection"]["numa_local"], -int(item["selection"]["memory_free_mib"]),
            int(item["selection"]["utilization_percent_sum"]), item["resource_ids"],
        ))

    def reconcile_current_service_owners(self, *, project_root: str | Path,
                                         pid: int, live_owner_ids: Iterable[str]) -> list[str]:
        return self._coordinator.reconcile_current_service_owners(
            project_root=project_root, pid=pid,
            live_owner_ids={str(item) for item in live_owner_ids},
        )

    def heartbeat(self, owner_id: str, *, pid: int | None = None) -> None:
        self._coordinator.heartbeat(owner_id, pid)

    def protect_residency(self, owner_id: str, *, memory_baseline: dict[str, float]) -> dict[str, object]:
        return self._coordinator.protect_residency(owner_id, memory_baseline=memory_baseline)

    def record_residency_process(self, owner_id: str, *, pid: int, residency_capability: str, generation: str) -> None:
        self._coordinator.record_residency_process(owner_id, pid=pid,
            residency_capability=residency_capability, generation=generation)

    def release(self, owner_id: str, *, residency_quiescence: dict[str, object] | None = None,
                residency_capability: str | None = None, generation: str | None = None) -> None:
        self._coordinator.release(owner_id, residency_quiescence=residency_quiescence,
                                  residency_capability=residency_capability, generation=generation)
        self._drain_callbacks.pop(owner_id, None)
