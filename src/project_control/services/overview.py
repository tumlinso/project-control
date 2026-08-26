from __future__ import annotations

from typing import Any

from ..models import ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..reconcile import ProjectReconciler, rank_items


BUDGETS = {"compact": 6000, "standard": 10000, "expanded": 16000}


def project_overview(snapshot: ProjectSnapshot, *, detail: str = "standard", max_items: int = 20) -> ToolEnvelope:
    reconciled = ProjectReconciler(snapshot).reconcile()

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

    current_programs = sorted(
        reconciled.programs,
        key=lambda item: (not bool(item.get("has_current_work")), not bool(item.get("complete")), str(item.get("id"))),
    )
    performance_attention = reconciled.performance["current_regressions"][:max_items]

    data = {
        "identity": {"display_name": snapshot.display_name, "project_uuid": snapshot.project_uuid},
        "current_project_state": current_programs[:max_items],
        "active_work": [compact(item) for item in reconciled.active[:max_items]],
        "ready_work": [compact(item) for item in reconciled.ready[:max_items]],
        "current_blockers": [compact(item) for item in reconciled.blocked[:max_items]],
        "architectural_attention": rank_items(reconciled.architectural_attention)[:max_items],
        "validation_attention": rank_items(reconciled.validation_attention)[:max_items],
        "performance_attention": performance_attention,
        "recent_materially_completed": [compact(item) for item in reconciled.completed[:max_items]],
        "cross_authority_warnings": reconciled.contradictions[:max_items],
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
    }
    warnings = [*snapshot.warnings_for("todo", "cuda"), *reconciled.warnings]
    return envelope("project_overview", snapshot, bounded_payload(data, BUDGETS[detail]), warnings=list(dict.fromkeys(warnings)))
