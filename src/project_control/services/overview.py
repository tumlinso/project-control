from __future__ import annotations

from typing import Any

from ..models import ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload, stable_unique


BUDGETS = {"compact": 6000, "standard": 10000, "expanded": 16000}


def project_overview(snapshot: ProjectSnapshot, *, detail: str = "standard", max_items: int = 20) -> ToolEnvelope:
    tasks = snapshot.todo_tables.get("tasks", [])
    active = [task for task in tasks if task.get("status") == "in_progress"]
    ready_ids = {item.get("id") or item.get("task_id") for item in snapshot.todo_status.get("ready", []) if isinstance(item, dict)}
    ready = [task for task in tasks if task.get("id") in ready_ids]
    attention = [task for task in tasks if task.get("attention_reason") or task.get("status") == "blocked"]
    recent = sorted(
        [task for task in tasks if task.get("status") == "done"],
        key=lambda item: str(item.get("updated_at", "")), reverse=True,
    )
    interfaces = [item for item in snapshot.todo_tables.get("interfaces", []) if item.get("state") != "frozen"]
    checkpoints = [item for item in snapshot.todo_tables.get("checkpoints", []) if item.get("state") != "reached"]

    def compact(task: dict[str, Any]) -> dict[str, Any]:
        return {key: task.get(key) for key in ("id", "title", "status", "priority", "attention_reason", "result") if task.get(key) is not None}

    data = {
        "identity": {"display_name": snapshot.display_name, "project_uuid": snapshot.project_uuid},
        "active_tasks": [compact(item) for item in active[:max_items]],
        "ready_tasks": [compact(item) for item in ready[:max_items]],
        "attention_tasks": [compact(item) for item in attention[:max_items]],
        "recently_completed": [compact(item) for item in recent[:max_items]],
        "architectural_attention": stable_unique((interfaces + checkpoints)[:max_items]),
        "validation_attention": [item for item in snapshot.todo_tables.get("gates", []) if item.get("required") and not item.get("valid")][:max_items],
        "performance_attention": snapshot.cuda.get("warnings", []),
        "recommended_focus": [item.get("id") for item in (active or ready or attention)[:5]],
    }
    return envelope("project_overview", snapshot, bounded_payload(data, BUDGETS[detail]), warnings=snapshot.warnings_for("todo", "cuda"))
