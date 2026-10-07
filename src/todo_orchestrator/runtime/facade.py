"""Supported additive facade over todo-orchestrator's private sidecars."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Iterable

from ..background.host import HostCoordinator
from ..background.store import BackgroundStore
from .contracts import (
    ContractError,
    normalize_artifact_ref,
    normalize_command_spec,
    normalize_evidence_summary,
    normalize_resource_request,
    normalize_source_identity,
)
from .source import capture_source_identity
from .topology import discover_gpu_topology


def _private_resource_request(value: object | None) -> dict[str, Any]:
    normalized = normalize_resource_request(value)
    return {key: item for key, item in normalized.items() if key != "schema_version"}


class JobFacade:
    def __init__(self, store: BackgroundStore):
        self._store = store

    def enqueue(
        self,
        *,
        kind: str,
        command: object,
        source_identity: object,
        resource_request: object | None = None,
        dependencies: Iterable[str] = (),
        priority: int = 40,
        retry_limit: int = 0,
        dedup_key: str | None = None,
        watch_id: str | None = None,
        task_id: str | None = None,
        todo_revision: int | None = None,
    ) -> dict[str, object]:
        if not isinstance(kind, str) or not kind:
            raise ContractError("job kind must be a non-empty string")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ContractError("job priority must be an integer")
        if isinstance(retry_limit, bool) or not isinstance(retry_limit, int) or retry_limit < 0:
            raise ContractError("job retry_limit must be a non-negative integer")
        spec = normalize_command_spec(command)
        source = normalize_source_identity(source_identity)
        job: dict[str, object] = {
            "kind": kind,
            "argv": spec["argv"],
            "cwd": spec["cwd"],
            "env": spec["env"],
            "timeout": spec["timeout_seconds"],
            "resources": _private_resource_request(resource_request),
            "source_fingerprint": source["fingerprint"],
            "snapshot": source,
            "priority": priority,
            "retry_limit": retry_limit,
            "task_id": task_id,
            "todo_revision": todo_revision,
        }
        if dedup_key is not None:
            job["dedup_key"] = dedup_key
        if watch_id is not None:
            job["watch_id"] = watch_id
        job_id, created = self._store.enqueue(job, dependencies)
        return {"job_id": job_id, "created": created}

    def result(self, identifier: str) -> dict[str, object] | None:
        raw = self._store.result(identifier)
        if raw is None:
            return None
        stored = raw.get("summary")
        if isinstance(stored, dict) and stored.get("schema_version") == 1:
            value = dict(stored)
        else:
            value = {
                "schema_version": 1,
                "status": str(raw["status"]),
                "valid": bool(raw["valid"]),
                "contaminated": bool(raw["contaminated"]),
                "severity": int(raw["severity"]),
                "summary": stored if isinstance(stored, dict) else {"value": stored},
                "artifacts": [],
            }
        value.update({
            "job_id": str(raw["job_id"]),
            "result_id": str(raw["id"]),
            "status": str(raw["status"]),
            "valid": bool(raw["valid"]),
            "contaminated": bool(raw["contaminated"]),
            "severity": int(raw["severity"]),
            "classification": raw.get("classification"),
            "parser_version": raw.get("parser_version"),
            "source_identity": raw.get("snapshot", {}),
            "artifacts": [
                normalize_artifact_ref({
                    "schema_version": 1,
                    "artifact_id": item["id"],
                    "job_id": item["job_id"],
                    "kind": item["kind"],
                    "path": item["path"],
                    "content_hash": item["content_hash"],
                    "complete": bool(item["complete"]),
                })
                for item in raw.get("artifacts", [])
            ],
        })
        return normalize_evidence_summary(value)

    def record_external(
        self,
        *,
        kind: str,
        command: object,
        source_identity: object,
        evidence: object,
    ) -> dict[str, str]:
        spec = normalize_command_spec(command)
        source = normalize_source_identity(source_identity)
        summary = normalize_evidence_summary(evidence)
        artifacts = summary.pop("artifacts")
        summary.pop("source_identity", None)
        summary.pop("job_id", None)
        summary.pop("result_id", None)
        job_id, result_id = self._store.record_external_result(
            kind=kind,
            argv=spec["argv"],
            cwd=spec["cwd"],
            source_fingerprint=source["fingerprint"],
            snapshot=source,
            result=summary,
            artifacts=[{key: item.get(key) for key in ("kind", "path", "content_hash", "complete")} for item in artifacts],
        )
        return {"job_id": job_id, "result_id": result_id}


class ArtifactFacade:
    def __init__(self, store: BackgroundStore):
        self._store = store

    def record(self, *, job_id: str, artifact: object, attempt_id: str | None = None) -> str:
        value = normalize_artifact_ref(artifact)
        return self._store.record_artifact(
            job_id,
            attempt_id,
            str(value["kind"]),
            str(value["path"]),
            value.get("content_hash"),
            bool(value["complete"]),
        )


class SnapshotFacade:
    @staticmethod
    def capture(repo_root: str | Path) -> dict[str, object]:
        return capture_source_identity(repo_root)


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


class RuntimeFacade:
    """One supported namespace; each component remains independently usable."""

    def __init__(self, project_root: str | Path, *, store: BackgroundStore | None = None,
                 host: HostCoordinator | None = None):
        private_store = store or BackgroundStore(project_root)
        self.jobs = JobFacade(private_store)
        self.artifacts = ArtifactFacade(private_store)
        self.snapshots = SnapshotFacade()
        self.host = HostResourceFacade(host or HostCoordinator())
