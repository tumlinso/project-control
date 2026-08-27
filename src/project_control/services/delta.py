from __future__ import annotations

from typing import Any

from ..adapters.git import GitReadAdapter
from ..adapters.todo import TodoReadAdapter, TodoReadError
from ..models import DeltaSince, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..workflow import workflow_summary, workflow_warnings


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


def _git_categories(changes: list[dict[str, str]], snapshot: ProjectSnapshot, related_tasks: set[str]) -> list[dict[str, Any]]:
    scopes = [
        item for item in snapshot.todo_tables.get("ownership_scopes", [])
        if not related_tasks or str(item.get("task_id")) in related_tasks
    ]
    groups: dict[str, dict[str, Any]] = {}
    for change in changes:
        path = str(change.get("path", ""))
        matches = [item for item in scopes if path == str(item.get("path")) or path.startswith(str(item.get("path", "")).rstrip("/") + "/")]
        if matches:
            selected = max(matches, key=lambda item: len(str(item.get("path", ""))))
            group = str(selected.get("path"))
            basis = "task scope + path prefix"
            tasks = {str(item.get("task_id")) for item in matches}
        else:
            parts = path.split("/")
            group = "/".join(parts[: min(3, len(parts))])
            basis = "directory-prefix heuristic"
            tasks = set()
        record = groups.setdefault(group, {
            "group": group, "files_added": 0, "files_modified": 0, "files_deleted": 0,
            "related_tasks": set(), "basis": basis,
        })
        status = str(change.get("status", ""))[:1]
        field = {"A": "files_added", "D": "files_deleted"}.get(status, "files_modified")
        record[field] += 1
        record["related_tasks"].update(tasks)
    return [
        {**item, "related_tasks": sorted(item["related_tasks"])}
        for _, item in sorted(groups.items())
    ]


def project_delta(
    snapshot: ProjectSnapshot, since: DeltaSince, git_adapters: dict[str, GitReadAdapter], *,
    detail: str = "standard", max_items: int = 40, todo_adapter: TodoReadAdapter | None = None,
) -> ToolEnvelope:
    events = []
    warnings = snapshot.warnings_for("todo")
    semantic_delta: dict[str, Any] = {}
    anchor: dict[str, Any] | None = None
    semantic_args: list[str] = []
    anchor_args: list[str] = []
    if since.task:
        semantic_args = ["--since-task", since.task]
        anchor_args = ["--task", since.task]
    elif since.checkpoint:
        semantic_args = ["--since-checkpoint", since.checkpoint]
        anchor_args = ["--checkpoint", since.checkpoint]
    elif since.interface:
        semantic_args = ["--since-interface", since.interface]
        anchor_args = ["--interface", since.interface]
    elif since.todo_revision is not None:
        semantic_args = ["--since-revision", str(since.todo_revision)]
        anchor_args = ["--revision", str(since.todo_revision)]
    if semantic_args and todo_adapter is not None:
        try:
            anchor = todo_adapter.semantic_anchor(*anchor_args)
            semantic_delta = todo_adapter.semantic_delta(*semantic_args)
        except TodoReadError:
            warnings.append("todo_semantic_unavailable")

    effective_revision = anchor.get("todo_revision") if anchor else since.todo_revision
    if since.time and effective_revision is None:
        candidates = [
            int(item.get("revision")) for item in snapshot.todo_tables.get("events", [])
            if str(item.get("timestamp", "")) >= since.time and isinstance(item.get("revision"), int)
        ]
        effective_revision = min(candidates) - 1 if candidates else None
    if effective_revision is None or snapshot.todo_revision is None:
        warnings.append("todo_delta_unavailable")
    elif effective_revision > snapshot.todo_revision:
        warnings.append("todo_cursor_ahead_of_authority")
    elif not semantic_delta:
        for event in snapshot.todo_tables.get("events", []):
            if int(event.get("revision", -1)) <= effective_revision:
                continue
            category = _category(str(event.get("event_type", "")))
            if category in {"administrative", "coordination"} and detail != "implementation":
                continue
            events.append({
                "revision": event.get("revision"),
                "category": category,
                "type": event.get("event_type"),
                "subject": event.get("entity_id"),
                "observed_at": event.get("timestamp"),
            })
    git_changes = []
    related_tasks = set()
    for section in semantic_delta.get("tasks", {}).values() if isinstance(semantic_delta.get("tasks"), dict) else []:
        if isinstance(section, list):
            related_tasks.update(str(item) for item in section)
    baseline_heads = anchor.get("baseline_git_heads", []) if anchor else []
    for alias, current in snapshot.repositories.items():
        previous = since.commits.get(alias) or since.commit
        if not previous and since.time and alias in git_adapters:
            try:
                previous = git_adapters[alias].commit_at_or_before(since.time)
            except Exception:
                previous = None
        if not previous and len(snapshot.repositories) == 1 and len(baseline_heads) == 1 and anchor and anchor.get("confidence") in {"high", "medium"}:
            previous = str(baseline_heads[0])
        if previous and previous != current.commit and alias in git_adapters:
            try:
                names = git_adapters[alias].diff_names(previous, current.commit, max_items=500)
            except Exception:
                names = []
            git_changes.append({
                "repository": alias, "from": previous, "to": current.commit,
                "path_categories": _git_categories(names, snapshot, related_tasks),
                "files_considered": len(names),
                **({"changes": names[:max_items]} if detail == "implementation" else {}),
            })
        elif previous == current.commit and (since.working_tree_fingerprints.get(alias) or since.fingerprints.get(alias)) and (since.working_tree_fingerprints.get(alias) or since.fingerprints.get(alias)) != snapshot.repository_fingerprints.get(alias):
            git_changes.append({
                "repository": alias, "from": previous, "to": current.commit,
                "working_tree_changed": True,
                "working_tree_fingerprint": snapshot.repository_fingerprints.get(alias),
            })
    if anchor and not baseline_heads and not since.commits and not since.commit:
        warnings.append("git_baseline_unavailable")
    semantic_view = dict(semantic_delta)
    if "material_events" in semantic_view:
        semantic_view["material_events"] = semantic_view["material_events"][:max_items] if detail == "implementation" else []
    data = {
        "semantic_todo": semantic_view,
        "anchor": anchor,
        "changes": sorted(events, key=lambda item: (item["category"] == "coordination", -int(item.get("revision") or 0)))[:max_items],
        "git_changes": git_changes,
        "readiness_changed": bool(semantic_delta.get("tasks") or semantic_delta.get("checkpoints")) or any(item["type"].startswith(("task.", "claim.", "checkpoint.")) for item in events),
        "workflow": workflow_summary(snapshot),
        "workflow_changed": bool(
            effective_revision is not None
            and any(
                int(item.get("revision", -1)) > effective_revision
                and str(item.get("event_type", "")).startswith("workflow")
                for item in snapshot.todo_tables.get("events", [])
            )
        ),
        "new_cursor": snapshot.cursor().model_dump(mode="json"),
        "ranking": {
            "items_considered": int(semantic_delta.get("raw_event_count", len(events))),
            "items_returned": int(semantic_delta.get("coalesced_event_count", len(events[:max_items]))),
            "historical_items_omitted": int(semantic_delta.get("heartbeat_events_omitted", 0)),
            "budget_bytes": 16000,
        },
    }
    return envelope("project_delta", snapshot, bounded_payload(data, 16000), warnings=list(dict.fromkeys([*warnings, *workflow_warnings(snapshot)])))
