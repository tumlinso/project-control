from __future__ import annotations

from typing import Any

from ..adapters.ctxpp import CtxppReadAdapter
from ..config import ProjectControlConfig
from ..models import InspectInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
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
    if request.kind in TABLES:
        rows = snapshot.todo_tables.get(TABLES[request.kind], [])
        matches = [row for row in rows if request.target in {str(row.get("id")), str(row.get("task_id")), str(row.get("owner_task_id")), str(row.get("checkpoint_id")), str(row.get("interface_id"))}]
        data.update(source="todo_authority", freshness="snapshot", matches=matches)
        if not matches:
            warnings.append("todo_subject_not_found")
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
        else:
            result = CtxppReadAdapter(repository.root).inspect(request.target, max_items=30)
            data.update(repository=repository.alias, **result)
            warnings.extend(result.get("warnings", []))
    budget = min(28000, request.budget_tokens * 4)
    return envelope("inspect", snapshot, bounded_payload(data, budget), warnings=warnings)
