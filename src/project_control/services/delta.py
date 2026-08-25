from __future__ import annotations

import json
from typing import Any

from ..adapters.git import GitReadAdapter
from ..models import DeltaSince, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload


def _category(event_type: str) -> str:
    if event_type.startswith(("interface.", "checkpoint.", "decision.")):
        return "architecture"
    if event_type.startswith(("gate.", "evidence.")):
        return "validation"
    if event_type.startswith(("claim.", "child.", "continue.", "handoff.")):
        return "coordination"
    if event_type.startswith(("task.", "plan.")):
        return "implementation"
    return "administrative"


def project_delta(snapshot: ProjectSnapshot, since: DeltaSince, git_adapters: dict[str, GitReadAdapter], *, detail: str = "standard", max_items: int = 40) -> ToolEnvelope:
    events = []
    warnings = snapshot.warnings_for("todo")
    if since.todo_revision is None or snapshot.todo_revision is None:
        warnings.append("todo_delta_unavailable")
    elif since.todo_revision > snapshot.todo_revision:
        warnings.append("todo_cursor_ahead_of_authority")
    else:
        for event in snapshot.todo_tables.get("events", []):
            if int(event.get("revision", -1)) <= since.todo_revision:
                continue
            category = _category(str(event.get("event_type", "")))
            if category == "administrative" and detail != "implementation":
                continue
            events.append({
                "revision": event.get("revision"),
                "category": category,
                "type": event.get("event_type"),
                "subject": event.get("entity_id"),
                "observed_at": event.get("timestamp"),
            })
    git_changes = []
    for alias, current in snapshot.repositories.items():
        previous = since.commits.get(alias)
        if previous and previous != current.commit and alias in git_adapters:
            try:
                names = git_adapters[alias].diff_names(previous, current.commit, max_items=max_items)
            except Exception:
                names = []
            git_changes.append({"repository": alias, "from": previous, "to": current.commit, "changes": names})
        elif previous == current.commit and since.fingerprints.get(alias) and since.fingerprints[alias] != snapshot.repository_fingerprints.get(alias):
            git_changes.append({"repository": alias, "from": previous, "to": current.commit, "working_tree_changed": True})
    data = {
        "changes": events[:max_items],
        "git_changes": git_changes,
        "readiness_changed": any(item["type"].startswith(("task.", "claim.", "checkpoint.")) for item in events),
        "new_cursor": snapshot.cursor().model_dump(mode="json"),
    }
    return envelope("project_delta", snapshot, bounded_payload(data, 16000), warnings=list(dict.fromkeys(warnings)))
