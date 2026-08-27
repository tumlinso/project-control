"""Bounded helpers for todo's authoritative additive workflow read model."""

from __future__ import annotations

from typing import Any

from .models import ProjectSnapshot


def workflow_view(snapshot: ProjectSnapshot) -> dict[str, Any]:
    value = snapshot.todo_workflow
    if not isinstance(value, dict) or value.get("available") is not True:
        return {
            "available": False,
            "reason": "todo_workflow_semantic_unavailable",
            "source_reason": value.get("reason") if isinstance(value, dict) else None,
            "revision": value.get("revision") if isinstance(value, dict) else snapshot.todo_revision,
            "active_run_id": None,
            "runs": [],
            "first_class_agents": [],
            "local_children": [],
            "blocking_messages": [],
            "unresolved_questions": [],
            "rendezvous": [],
            "integration_queue": [],
            "recovery_needed": [],
            "safe_parallel_groups": [],
        }
    return value


def workflow_warnings(snapshot: ProjectSnapshot) -> list[str]:
    view = workflow_view(snapshot)
    return [] if view["available"] else [str(view["reason"])]


def _pick(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: record.get(field) for field in fields if record.get(field) is not None}


def _worktree_id(snapshot: ProjectSnapshot, workspace: dict[str, Any]) -> str | None:
    explicit = workspace.get("worktree_id")
    if explicit:
        return str(explicit)
    repository = str(workspace.get("repository") or "")
    candidates = snapshot.repositories.get(repository).worktrees if repository in snapshot.repositories else {}
    branch = workspace.get("branch")
    head = workspace.get("head") or workspace.get("source_commit")
    matches = [
        item.id for item in candidates.values()
        if (not branch or item.branch == branch) and (not head or item.head == head)
    ]
    return matches[0] if len(matches) == 1 else None


def _lane(snapshot: ProjectSnapshot, record: dict[str, Any], max_items: int) -> dict[str, Any]:
    dispatch = record.get("dispatch")
    workspace = record.get("workspace")
    return {
        **_pick(record, ("id", "parent_lane_id", "role", "state", "workspace_mode", "context_cursor")),
        "serial_queue": [
            _pick(item, ("position", "task_id", "state"))
            for item in list(record.get("queue", []))[:max_items] if isinstance(item, dict)
        ],
        "queue_items_omitted": max(0, len(record.get("queue", [])) - max_items),
        "dispatch": _pick(dispatch, (
            "task_id", "heartbeat_at", "heartbeat_fresh", "context_version", "observable",
        )) if isinstance(dispatch, dict) else None,
        "workspace": {
            **_pick(workspace, (
                "id", "run_id", "lane_id", "repository", "base_commit", "branch", "mode", "state",
                "integration_task_id", "merge_result", "cleanup_eligible",
            )),
            **({"worktree_id": _worktree_id(snapshot, workspace)} if _worktree_id(snapshot, workspace) else {}),
        } if isinstance(workspace, dict) else None,
    }


def workflow_summary(snapshot: ProjectSnapshot, *, max_items: int = 50) -> dict[str, Any]:
    view = workflow_view(snapshot)
    if not view["available"]:
        return {
            "available": False,
            "reason": view.get("reason"),
            "source_reason": view.get("source_reason"),
            "revision": view.get("revision"),
            "active_run_id": None,
            "authority": "compatibility_fallback",
        }
    runs = view.get("runs", []) if isinstance(view.get("runs"), list) else []
    active = next((item for item in runs if item.get("id") == view.get("active_run_id")), None)
    active_run = None
    if isinstance(active, dict):
        active_run = {
            **_pick(active, ("id", "root_task_id", "status", "active_charter_version")),
            "lanes": [_lane(snapshot, item, max_items) for item in list(active.get("lanes", []))[:max_items] if isinstance(item, dict)],
            "lanes_omitted": max(0, len(active.get("lanes", [])) - max_items),
        }

    def records(name: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            _pick(item, fields) for item in list(view.get(name, []))[:max_items]
            if isinstance(item, dict)
        ]

    return {
        "available": bool(view["available"]),
        "reason": view.get("reason"),
        "revision": view.get("revision"),
        "active_run_id": view.get("active_run_id"),
        "active_run": active_run,
        "first_class_agents": records("first_class_agents", (
            "run_id", "lane_id", "role", "task_id",
            "heartbeat_at", "heartbeat_fresh", "context_version", "observable",
        )),
        "subordinate_local_children": records("local_children", (
            "child_execution_id", "parent_task_id", "parent_lane_id", "state", "access_mode",
        )),
        "blocking_messages": records("blocking_messages", (
            "id", "run_id", "author_lane_id", "task_id", "kind", "blocking", "state", "revision",
        )),
        "unresolved_questions": records("unresolved_questions", (
            "id", "run_id", "author_lane_id", "task_id", "kind", "blocking", "state", "revision",
        )),
        "rendezvous": [
            {
                **_pick(item, ("id", "run_id", "barrier_id", "mode", "quorum", "join_task_id", "state")),
                "arrivals": [
                    _pick(arrival, ("lane_id", "task_id", "state", "context_version", "revision"))
                    for arrival in list(item.get("arrivals", []))[:max_items] if isinstance(arrival, dict)
                ],
            }
            for item in list(view.get("rendezvous", []))[:max_items] if isinstance(item, dict)
        ],
        "patch_artifacts": records("patch_artifacts", (
            "id", "workspace_id", "run_id", "lane_id", "task_id", "kind", "artifact_ref",
            "content_hash", "base_commit", "state", "created_at",
        )),
        "pending_patches": records("pending_patches", (
            "id", "workspace_id", "run_id", "lane_id", "task_id", "kind", "artifact_ref",
            "content_hash", "base_commit", "state", "created_at",
        )),
        "integration_queue": records("integration_queue", (
            "id", "run_id", "integration_task_id", "integrator_lane_id", "position", "state", "conflict",
        )),
        "recovery_needed": records("recovery_needed", ("kind", "id", "task_id", "attempt_id", "reason")),
        "safe_parallel_groups": [list(group)[:max_items] for group in list(view.get("safe_parallel_groups", []))[:max_items]],
        "collection_counts": {
            name: len(view.get(name, [])) for name in (
                "first_class_agents", "local_children", "blocking_messages", "unresolved_questions",
                "rendezvous", "patch_artifacts", "pending_patches", "integration_queue", "recovery_needed",
                "safe_parallel_groups",
            )
        },
        "authority": "todo_semantic_workflow" if view["available"] else "compatibility_fallback",
    }
