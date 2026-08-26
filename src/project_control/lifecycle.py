"""Shared current-planning lifecycle predicates."""

from __future__ import annotations

from typing import Any, Iterable


TERMINAL_HISTORICAL_STATES = frozenset({
    "done",
    "superseded",
    "cancelled",
    "canceled",
    "stale",
    "archived",
    "invalidated",
    "abandoned",
    "historical-only",
    "historical_only",
})


def is_terminal_historical(task: dict[str, Any]) -> bool:
    return str(task.get("status", "")).strip().lower() in TERMINAL_HISTORICAL_STATES


def current_task_ids(tasks: Iterable[dict[str, Any]]) -> set[str]:
    return {
        str(task.get("id"))
        for task in tasks
        if task.get("id") and not is_terminal_historical(task)
    }
