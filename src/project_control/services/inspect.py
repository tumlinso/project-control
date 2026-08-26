from __future__ import annotations

from typing import Any

from ..adapters.ctxpp import CtxppReadAdapter
from ..config import DEFAULT_DENY_PATTERNS, ProjectControlConfig
from ..models import InspectInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..graph import ProjectGraph
from ..reconcile import ProjectReconciler
from ..registry import WorkspaceRegistry
from ..security import SecurityError, read_bounded_text


TABLES = {
    "task": "tasks",
    "interface": "interfaces",
    "checkpoint": "checkpoints",
    "decision": "decisions",
    "dependency": "task_dependencies",
}


def inspect_subject(config: ProjectControlConfig, snapshot: ProjectSnapshot, request: InspectInput) -> ToolEnvelope:
    warnings: list[str] = []
    data: dict[str, Any] = {"kind": request.kind, "target": request.target, "intent": request.intent}
    reconciled = ProjectReconciler(snapshot).reconcile()
    graph = ProjectGraph(snapshot, reconciled)
    expected = {request.kind} if request.kind in {"task", "interface", "checkpoint", "decision"} else None
    resolution = graph.resolve(request.target, expected_types=expected)
    if request.kind in TABLES:
        warnings.extend(snapshot.warnings_for("todo"))
        rows = snapshot.todo_tables.get(TABLES[request.kind], [])
        matches = [row for row in rows if request.target in {str(row.get("id")), str(row.get("task_id")), str(row.get("owner_task_id")), str(row.get("checkpoint_id")), str(row.get("interface_id"))}]
        if resolution["status"] == "resolved":
            entity = resolution["entity"]
            matches = [entity["record"]]
            data.update(
                source="reconciled_project_graph", freshness="snapshot", resolution=resolution,
                matches=matches, related=graph.related(entity["key"]),
            )
        else:
            data.update(source="todo_authority", freshness="snapshot", resolution=resolution, matches=matches)
        if not matches:
            warnings.append("todo_subject_ambiguous" if resolution["status"] == "ambiguous" else "todo_subject_not_found")
    elif request.kind in {"path", "symbol", "subsystem"}:
        registry = WorkspaceRegistry(config)
        repository = registry.repository(request.project, request.repository)
        workspace = registry.workspace(request.project)
        if request.kind == "path":
            try:
                text = read_bounded_text(
                    repository.root,
                    request.target,
                    deny_patterns=workspace.deny_patterns,
                    max_bytes=min(2 * 1024 * 1024, request.budget_tokens * 4),
                )
                lines = text.splitlines()
                data.update(
                    source="canonical_file",
                    freshness="working_tree",
                    location={"repository": repository.alias, "path": request.target, "line": 1},
                    excerpt="\n".join(lines[: max(1, request.budget_tokens // 12)]),
                )
            except SecurityError as exc:
                data.update(source="registered_repository", error=str(exc))
                warnings.append("source_inspection_unavailable")
        elif resolution["status"] == "resolved":
            entity = resolution["entity"]
            data.update(
                source="reconciled_project_graph", freshness="snapshot", resolution=resolution,
                subject={key: entity[key] for key in ("type", "id", "title", "relevance")},
                related=graph.related(entity["key"]),
            )
        elif resolution["status"] == "ambiguous":
            data.update(source="reconciled_project_graph", freshness="snapshot", resolution=resolution)
            warnings.append("subject_ambiguous")
        else:
            result = CtxppReadAdapter(repository.root, deny_patterns=[*DEFAULT_DENY_PATTERNS, *workspace.deny_patterns]).inspect(request.target, max_items=30)
            data.update(repository=repository.alias, resolution=resolution, **result)
            warnings.extend(result.get("warnings", []))
    budget = min(28000, request.budget_tokens * 4)
    return envelope("inspect", snapshot, bounded_payload(data, budget), warnings=warnings)
