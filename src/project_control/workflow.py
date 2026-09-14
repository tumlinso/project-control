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


def workflow_summary(snapshot: ProjectSnapshot, *, max_items: int = 50, actionable: bool = False) -> dict[str, Any]:
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
    terminal = {"completed", "complete", "closed", "integrated", "satisfied", "resolved", "cancelled", "superseded"}
    active_run = None
    live_run_id: str | None = None
    live_lanes: set[str] = set()
    live_tasks: set[str] = set()
    if isinstance(active, dict):
        if str(active.get("status") or active.get("state") or "").casefold() not in terminal:
            live_run_id = str(active.get("id")) if active.get("id") else None
            for lane in active.get("lanes", []):
                if not isinstance(lane, dict) or str(lane.get("state") or lane.get("status") or "").casefold() in terminal:
                    continue
                if lane.get("id"):
                    live_lanes.add(str(lane["id"]))
                for queue_item in lane.get("queue", []):
                    if isinstance(queue_item, dict) and str(queue_item.get("state") or queue_item.get("status") or "").casefold() not in terminal and queue_item.get("task_id"):
                        live_tasks.add(str(queue_item["task_id"]))
        active_run = {
            **_pick(active, ("id", "root_task_id", "status", "active_charter_version")),
            "lanes": [_lane(snapshot, item, max_items) for item in list(active.get("lanes", []))[:max_items] if isinstance(item, dict)],
            "lanes_omitted": max(0, len(active.get("lanes", [])) - max_items),
        }

    def current(item: dict[str, Any]) -> bool:
        """Match the compact coordination projection's live workflow scope."""
        if not actionable:
            return True
        run_id = item.get("run_id")
        lane_id = item.get("author_lane_id") or item.get("lane_id") or item.get("parent_lane_id")
        task_id = item.get("task_id") or item.get("parent_task_id")
        if run_id is not None and str(run_id) != live_run_id:
            return False
        if lane_id is not None and str(lane_id) not in live_lanes:
            return False
        if task_id is not None and live_tasks and str(task_id) not in live_tasks:
            return False
        return True

    def records(name: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            _pick(item, fields) for item in list(view.get(name, []))[:max_items]
            if isinstance(item, dict) and current(item)
        ]

    # Ordinary workflow consumers need the live control surface, not a ledger
    # replay.  Keep the complete projection for explicit/history readers.
    def live(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not actionable:
            return items
        return [item for item in items if str(item.get("state") or item.get("status") or "").casefold() not in terminal]

    result = {
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
    if actionable:
        for name in ("blocking_messages", "unresolved_questions", "rendezvous", "patch_artifacts", "pending_patches", "integration_queue", "recovery_needed"):
            result[name] = live(list(result.get(name, [])))
        if isinstance(result.get("active_run"), dict):
            run = result["active_run"]
            run["lanes"] = live(list(run.get("lanes", [])))
            live_members = {str(lane.get("id")) for lane in run["lanes"] if lane.get("id")}
            for lane in run["lanes"]:
                lane["serial_queue"] = live(list(lane.get("serial_queue", [])))
                lane["queue_items_omitted"] = 0
                live_members.update(str(item.get("task_id")) for item in lane["serial_queue"] if item.get("task_id"))
            result["safe_parallel_groups"] = [
                [member for member in group if str(member) in live_members]
                for group in result["safe_parallel_groups"]
            ]
        result["first_class_agents"] = live(list(result["first_class_agents"]))
        result["subordinate_local_children"] = live(list(result["subordinate_local_children"]))
        result["safe_parallel_groups"] = [group for group in result["safe_parallel_groups"] if group]
    return result
