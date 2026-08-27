from __future__ import annotations

from typing import Any

from ..models import ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..reconcile import ProjectReconciler, rank_items
from ..workflow import workflow_summary, workflow_warnings


BUDGETS = {"compact": 1800, "standard": 9000, "expanded": 15000}


def project_overview(snapshot: ProjectSnapshot, *, detail: str = "standard", max_items: int = 20) -> ToolEnvelope:
    reconciled = ProjectReconciler(snapshot).reconcile()
    workflow = workflow_summary(snapshot, max_items=max_items)

    def compact(task: dict[str, Any]) -> dict[str, Any]:
        return {key: task.get(key) for key in (
            "id", "title", "effective_state", "priority", "attention_reason", "raw_result",
            "relevance", "relevance_reason",
        ) if task.get(key) is not None}

    focus: list[str] = []
    if reconciled.semantic_available:
        if reconciled.contradictions:
            focus.extend(str(item.get("task_id") or item.get("checkpoint_id") or item.get("gate_id")) for item in reconciled.contradictions)
        focus.extend(str(item.get("id")) for item in reconciled.performance["current_regressions"])
        focus.extend(str(item.get("id")) for item in [*reconciled.blocked, *reconciled.architectural_attention, *reconciled.validation_attention, *reconciled.active, *reconciled.ready])
    else:
        fallback_focus = reconciled.active or reconciled.ready or reconciled.blocked
        focus.extend(str(item.get("id")) for item in fallback_focus)
    focus = list(dict.fromkeys(item for item in focus if item and item != "None"))[:5]
    recovery_focus = [str(item.get("id")) for item in workflow.get("recovery_needed", []) if item.get("id")]
    message_focus = [str(item.get("task_id")) for item in workflow.get("blocking_messages", []) if item.get("task_id")]
    focus = list(dict.fromkeys([*recovery_focus, *message_focus, *focus]))[:5]

    current_programs = sorted(
        reconciled.programs,
        key=lambda item: (not bool(item.get("has_current_work")), not bool(item.get("complete")), str(item.get("id"))),
    )
    performance_attention = reconciled.performance["current_regressions"][:max_items]
    interfaces = [
        {key: item.get(key) for key in ("id", "state", "version", "owner_task_id") if item.get(key) is not None}
        for item in snapshot.todo_tables.get("interfaces", [])[:max_items]
    ]
    decisions = [
        {key: item.get(key) for key in ("id", "state", "summary", "rationale", "task_id") if item.get(key) is not None}
        for item in snapshot.todo_tables.get("decisions", [])[:max_items]
    ]
    worktrees = {
        alias: [
            {"id": item.id, "branch": item.branch, "head": item.head, "detached": item.detached, "dirty": item.dirty}
            for item in identity.worktrees.values()
        ]
        for alias, identity in snapshot.repositories.items()
    }

    data = {
        "identity": {"display_name": snapshot.display_name, "project_uuid": snapshot.project_uuid},
        "workflow": workflow,
        "provider_health": {
            name: component.model_dump(mode="json")
            for name, component in sorted(snapshot.component_authority.items())
        },
        "repository_worktrees": worktrees,
        "architecture": {
            "active_run_id": workflow.get("active_run_id"),
            "charter_version": (workflow.get("active_run") or {}).get("active_charter_version") if workflow.get("active_run") else None,
            "interfaces": interfaces,
            "decisions": decisions,
        },
        "current_project_state": current_programs[:max_items],
        "active_work": [compact(item) for item in reconciled.active[:max_items]],
        "ready_work": [compact(item) for item in reconciled.ready[:max_items]],
        "current_blockers": [compact(item) for item in reconciled.blocked[:max_items]],
        "architectural_attention": rank_items(reconciled.architectural_attention)[:max_items],
        "validation_attention": rank_items(reconciled.validation_attention)[:max_items],
        "performance_attention": performance_attention,
        "recent_materially_completed": [compact(item) for item in reconciled.completed[:max_items]],
        "cross_authority_warnings": reconciled.contradictions[:max_items],
        "unresolved_questions": workflow.get("unresolved_questions", []),
        "context_staleness": workflow.get("recovery_needed", []),
        "integration_state": workflow.get("integration_queue", []),
        "historical_state_filtered": reconciled.historical_counts,
        "recommended_focus": focus,
        "active_tasks": [compact(item) for item in reconciled.active[:max_items]],
        "ready_tasks": [compact(item) for item in reconciled.ready[:max_items]],
        "attention_tasks": [compact(item) for item in reconciled.blocked[:max_items]],
        "recently_completed": [compact(item) for item in reconciled.completed[:max_items]],
        "ranking": {
            "items_considered": len(reconciled.tasks) + len(reconciled.checkpoints) + len(reconciled.gates),
            "items_returned": sum(len(value) for value in (
                reconciled.active[:max_items], reconciled.ready[:max_items], reconciled.blocked[:max_items],
                reconciled.architectural_attention[:max_items], reconciled.validation_attention[:max_items],
                reconciled.completed[:max_items],
            )),
            "historical_items_omitted": sum(reconciled.historical_counts.values()),
            "budget_bytes": BUDGETS[detail],
        },
        "observation_preconditions": snapshot.observation_preconditions().model_dump(mode="json"),
    }
    if detail == "compact":
        compact_workflow = {
            key: workflow.get(key) for key in ("available", "reason", "revision", "active_run_id", "authority")
            if workflow.get(key) is not None
        }
        compact_workflow["pending_patches"] = [
            {key: item.get(key) for key in ("id", "task_id", "workspace_id", "state") if item.get(key) is not None}
            for item in workflow.get("pending_patches", [])[:3]
        ]
        compact_workflow["blocking_messages"] = [
            {key: item.get(key) for key in ("id", "task_id", "kind", "state") if item.get(key) is not None}
            for item in workflow.get("blocking_messages", [])[:3]
        ]
        compact_workflow["unresolved_questions"] = [
            {key: item.get(key) for key in ("id", "task_id", "state") if item.get(key) is not None}
            for item in workflow.get("unresolved_questions", [])[:3]
        ]
        compact_workflow["collection_counts"] = workflow.get("collection_counts", {})
        tiny = lambda items: [
            {key: item.get(key) for key in ("id", "title", "effective_state") if item.get(key) is not None}
            for item in items[:5]
        ]
        data = {
            "identity": data["identity"],
            "workflow": compact_workflow,
            "provider_health": {
                key: {"status": value.get("status"), "error_code": value.get("error_code"), "revision": value.get("revision")}
                for key, value in data["provider_health"].items()
            },
            "current_project_state": [
                {key: item.get(key) for key in ("id", "complete", "has_current_work", "effective_state_counts") if item.get(key) is not None}
                for item in data["current_project_state"][:3]
            ],
            "active_work": tiny(data["active_work"]),
            "ready_work": tiny(data["ready_work"]),
            "current_blockers": tiny(data["current_blockers"]),
            "architectural_attention": tiny(data["architectural_attention"]),
            "validation_attention": tiny(data["validation_attention"]),
            "performance_attention": tiny(data["performance_attention"]),
            "recent_materially_completed": tiny(data["recent_materially_completed"]),
            "cross_authority_warnings": data["cross_authority_warnings"][:1],
            "recommended_focus": data["recommended_focus"],
            "historical_state_filtered": data["historical_state_filtered"],
            "active_tasks": tiny(data["active_tasks"]),
            "ready_tasks": tiny(data["ready_tasks"]),
            "attention_tasks": tiny(data["attention_tasks"]),
            "recently_completed": tiny(data["recently_completed"]),
            "ranking": {"items_considered": data["ranking"]["items_considered"], "historical_items_omitted": data["ranking"]["historical_items_omitted"]},
        }
    warnings = [*snapshot.warnings_for("todo", "cuda"), *reconciled.warnings, *workflow_warnings(snapshot)]
    return envelope("project_overview", snapshot, bounded_payload(data, BUDGETS[detail]), warnings=list(dict.fromkeys(warnings)))
