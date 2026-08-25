from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..models import ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload


def project_frontier(snapshot: ProjectSnapshot, *, max_ready: int = 20, include_blocked: bool = True, include_parallel_groups: bool = True) -> ToolEnvelope:
    tasks = {str(item.get("id")): item for item in snapshot.todo_tables.get("tasks", [])}
    ready_items = snapshot.todo_status.get("ready", [])
    ready_ids = [str(item.get("id") or item.get("task_id")) for item in ready_items if isinstance(item, dict)]
    ready = [tasks[item] for item in ready_ids if item in tasks]
    ready.sort(key=lambda item: (-int(item.get("priority", 0)), str(item.get("id"))))
    dependencies: dict[str, list[str]] = defaultdict(list)
    for dependency in snapshot.todo_tables.get("task_dependencies", []):
        prerequisite = dependency.get("prerequisite_task_id") or dependency.get("checkpoint_id") or dependency.get("interface_id")
        if prerequisite:
            dependencies[str(dependency.get("task_id"))].append(str(prerequisite))
    scopes: dict[str, set[str]] = defaultdict(set)
    for scope in snapshot.todo_tables.get("ownership_scopes", []):
        if scope.get("mode") == "exclusive":
            scopes[str(scope.get("task_id"))].add(str(scope.get("path")))
    groups: list[list[str]] = []
    if include_parallel_groups:
        for task in ready:
            task_id = str(task.get("id"))
            placed = False
            for group in groups:
                if all(scopes[task_id].isdisjoint(scopes[other]) and other not in dependencies[task_id] and task_id not in dependencies[other] for other in group):
                    group.append(task_id)
                    placed = True
                    break
            if not placed:
                groups.append([task_id])
    blocked = []
    if include_blocked:
        for task_id, task in tasks.items():
            if task.get("status") == "planned" and task_id not in ready_ids:
                blocked.append({"id": task_id, "title": task.get("title"), "immediate_blockers": dependencies.get(task_id, [])})
    active_claims = [
        {"task_id": item.get("task_id"), "observed_state": "active", "source": "todo_status"}
        for item in snapshot.todo_status.get("active_claims", []) if isinstance(item, dict)
    ]
    critical = []
    remaining = {key for key, item in tasks.items() if item.get("status") != "done"}
    if remaining:
        current = max(remaining, key=lambda key: (len(dependencies.get(key, [])), int(tasks[key].get("priority", 0))))
        critical = [current]
        while dependencies.get(current):
            current = dependencies[current][0]
            critical.append(current)
    data = {
        "ready": [{"id": item.get("id"), "title": item.get("title"), "priority": item.get("priority")} for item in ready[:max_ready]],
        "active_claims": active_claims,
        "blocked": blocked[:max_ready],
        "parallel_groups": groups,
        "critical_path": critical,
        "critical_path_basis": "authoritative dependencies + explicit heuristic",
        "local_worker_suitability": [{"task_id": item.get("id"), "suitable": item.get("kind") == "workstream", "basis": "heuristic"} for item in ready[:max_ready]],
    }
    return envelope("project_frontier", snapshot, bounded_payload(data, 12000))
