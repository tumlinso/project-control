from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..models import ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..reconcile import ProjectReconciler, paths_overlap


def project_frontier(snapshot: ProjectSnapshot, *, max_ready: int = 20, include_blocked: bool = True, include_parallel_groups: bool = True) -> ToolEnvelope:
    reconciled = ProjectReconciler(snapshot).reconcile()
    tasks = reconciled.tasks
    ready = reconciled.ready
    ready_ids = [str(item.get("id")) for item in ready]
    current_ids = {task_id for task_id, item in tasks.items() if item.get("frontier_eligible")}
    dependencies: dict[str, list[str]] = defaultdict(list)
    for dependency in snapshot.todo_tables.get("task_dependencies", []):
        prerequisite = dependency.get("prerequisite_task_id") or dependency.get("checkpoint_id") or dependency.get("interface_id")
        if prerequisite:
            dependencies[str(dependency.get("task_id"))].append(str(prerequisite))
    scopes: dict[str, set[str]] = defaultdict(set)
    for scope in snapshot.todo_tables.get("ownership_scopes", []):
        if scope.get("mode") == "exclusive":
            scopes[str(scope.get("task_id"))].add(str(scope.get("path")))
    locks: dict[str, set[str]] = defaultdict(set)
    for item in snapshot.todo_tables.get("task_locks", []):
        locks[str(item.get("task_id"))].add(str(item.get("lock_name")))
    interface_owners = {
        str(item.get("id")): str(item.get("owner_task_id"))
        for item in snapshot.todo_tables.get("interfaces", []) if item.get("id") and item.get("owner_task_id")
    }
    interface_relations = {
        (interface_owners.get(str(item.get("interface_id"))), str(item.get("task_id")))
        for item in snapshot.todo_tables.get("interface_consumers", [])
    }
    checkpoint_owners = {
        str(item.get("id")): str(item.get("task_id"))
        for item in snapshot.todo_tables.get("checkpoints", []) if item.get("id") and item.get("task_id")
    }
    checkpoint_relations = {
        (checkpoint_owners.get(str(item.get("checkpoint_id"))), str(item.get("task_id")))
        for item in snapshot.todo_tables.get("task_dependencies", []) if item.get("checkpoint_id")
    }

    def conflict(left: str, right: str) -> bool:
        return bool(
            any(paths_overlap(a, b) for a in scopes[left] for b in scopes[right])
            or locks[left] & locks[right]
            or right in dependencies[left] or left in dependencies[right]
            or (left, right) in interface_relations or (right, left) in interface_relations
            or (left, right) in checkpoint_relations or (right, left) in checkpoint_relations
        )
    groups: list[list[str]] = []
    if include_parallel_groups:
        for task in ready:
            task_id = str(task.get("id"))
            placed = False
            for group in groups:
                if all(not conflict(task_id, other) for other in group):
                    group.append(task_id)
                    placed = True
                    break
            if not placed:
                groups.append([task_id])
    blocked = []
    if include_blocked:
        for task_id, task in tasks.items():
            if task.get("effective_state") == "blocked":
                blocked.append({"id": task_id, "title": task.get("title"), "immediate_blockers": dependencies.get(task_id, [])})
    active_claims = [
        {"task_id": item.get("task_id"), "observed_state": "active", "source": "todo_status"}
        for item in snapshot.todo_status.get("active_claims", [])
        if isinstance(item, dict) and str(item.get("task_id")) in current_ids
    ]
    critical = []
    remaining = {key for key, item in tasks.items() if item.get("frontier_eligible")}
    if remaining:
        current = max(
            remaining,
            key=lambda key: (
                len([dependency for dependency in dependencies.get(key, []) if dependency in remaining]),
                int(tasks[key].get("priority", 0)),
            ),
        )
        critical = [current]
        while [item for item in dependencies.get(current, []) if item in remaining]:
            current = next(item for item in dependencies[current] if item in remaining)
            critical.append(current)
    data = {
        "ready": [{"id": item.get("id"), "title": item.get("title"), "priority": item.get("priority")} for item in ready[:max_ready]],
        "active_claims": active_claims,
        "blocked": blocked[:max_ready],
        "parallel_groups": groups,
        "critical_path": critical,
        "critical_path_basis": "authoritative dependencies + explicit heuristic",
        "local_worker_suitability": [{
            "task_id": task.get("id"),
            "suitable": bool(task.get("frontier_eligible") and len(scopes[str(task.get("id"))]) <= 2),
            "basis": "heuristic: bounded scope and todo-authoritative frontier eligibility",
            "verification_clues": [gate.get("id") for gate in reconciled.gates if gate.get("task_id") == task.get("id")],
        } for task in ready[:max_ready]],
        "historical_state_filtered": reconciled.historical_counts,
    }
    return envelope("project_frontier", snapshot, bounded_payload(data, 12000), warnings=list(dict.fromkeys([*snapshot.warnings_for("todo"), *reconciled.warnings])))
