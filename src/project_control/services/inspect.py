from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..adapters.ctxpp import CtxppReadAdapter
from ..adapters.git import GitReadAdapter
from ..config import DEFAULT_DENY_PATTERNS, ProjectControlConfig
from ..models import InspectInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..graph import ProjectGraph
from ..reconcile import ProjectReconciler
from ..registry import WorkspaceRegistry
from ..security import SecurityError, read_bounded_text, resolve_registered_path


TABLES = {
    "task": "tasks",
    "interface": "interfaces",
    "checkpoint": "checkpoints",
    "decision": "decisions",
    "dependency": "task_dependencies",
    "gate": "gates", "invariant": "invariants", "artifact": "task_artifacts",
    "message": "workflow_messages", "context_fragment": "context_fragments",
    "workspace": "workflow_workspaces", "patch": "workflow_patch_artifacts",
    "integration": "workflow_integration_queue",
}

GRAPH_KIND = {
    "dispatch": "workflow_dispatch", "message": "run_message", "patch": "patch_artifact",
    "commit": "git_commit",
}


def _file_identity(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha256(f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()


def _working_tree_range(root: Path, relative: str, *, deny_patterns: list[str], start: int, end: int) -> tuple[str, str, str]:
    target = resolve_registered_path(root, relative, deny_patterns=deny_patterns)
    for attempt in range(2):
        before = _file_identity(target)
        selected: list[str] = []
        with target.open("r", encoding="utf-8") as stream:
            for number, line in enumerate(stream, start=1):
                if number > end:
                    break
                if number >= start:
                    selected.append(line.rstrip("\n"))
        after = _file_identity(target)
        if before == after:
            return "\n".join(selected), before, after
        if attempt:
            raise SecurityError("racy_source_read")
    raise SecurityError("racy_source_read")


def inspect_subject(config: ProjectControlConfig, snapshot: ProjectSnapshot, request: InspectInput) -> ToolEnvelope:
    warnings: list[str] = []
    data: dict[str, Any] = {"kind": request.kind, "target": request.target, "intent": request.intent}
    reconciled = ProjectReconciler(snapshot).reconcile()
    graph = ProjectGraph(snapshot, reconciled)
    expected_kind = GRAPH_KIND.get(request.kind, request.kind)
    expected = {expected_kind} if request.kind not in {"path", "symbol", "subsystem", "dependency", "test"} else None
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
    elif resolution["status"] == "resolved" and request.kind not in {"path", "symbol", "subsystem"}:
        entity = resolution["entity"]
        data.update(
            source="reconciled_project_graph", freshness="snapshot", resolution=resolution,
            subject={key: entity[key] for key in ("type", "id", "title", "relevance")},
            matches=[entity["record"]], related=graph.related(entity["key"]),
            observation_preconditions=snapshot.observation_preconditions().model_dump(mode="json"),
        )
        warnings.extend(snapshot.warnings_for("todo"))
    elif request.kind in {"path", "symbol", "subsystem"}:
        registry = WorkspaceRegistry(config)
        repository = registry.repository(request.project, request.repository)
        workspace = registry.workspace(request.project)
        source_root = repository.root
        selected_worktree_id = None
        if request.worktree_id:
            matches = [item for item in GitReadAdapter(repository.root).worktrees() if item.worktree_id == request.worktree_id]
            if len(matches) != 1:
                return envelope("inspect", snapshot, data, warnings=["worktree_not_found"])
            source_root = matches[0].root
            selected_worktree_id = matches[0].worktree_id
        if request.kind == "path":
            try:
                line_start = request.line_start or 1
                line_end = request.line_end or (line_start + max(1, request.budget_tokens // 12) - 1)
                if request.source_selector == "working_tree":
                    if request.line_start or request.line_end:
                        excerpt, before, after = _working_tree_range(
                            source_root, request.target, deny_patterns=workspace.deny_patterns,
                            start=line_start, end=line_end,
                        )
                    else:
                        text = read_bounded_text(
                            source_root, request.target, deny_patterns=workspace.deny_patterns,
                            max_bytes=min(2 * 1024 * 1024, request.budget_tokens * 4),
                        )
                        excerpt = "\n".join(text.splitlines()[: max(1, request.budget_tokens // 12)])
                        target = resolve_registered_path(source_root, request.target, deny_patterns=workspace.deny_patterns)
                        before = after = _file_identity(target)
                    freshness = "working_tree"
                    try:
                        source_commit = GitReadAdapter(source_root).identity().commit
                    except Exception:
                        source_commit = snapshot.repositories.get(repository.alias).commit if repository.alias in snapshot.repositories else None
                else:
                    selector = "HEAD" if request.source_selector == "HEAD" else request.source_selector
                    if selector.startswith("-") or len(selector) > 128:
                        raise SecurityError("invalid source selector")
                    text = GitReadAdapter(source_root).show_text(selector, request.target, max_bytes=8 * 1024 * 1024)
                    lines = text.splitlines()
                    excerpt = "\n".join(lines[line_start - 1:line_end])
                    before = after = hashlib.sha256(text.encode()).hexdigest()
                    freshness = "git_object"
                    source_commit = selector
                data.update(
                    source="canonical_file", freshness=freshness,
                    location={"repository": repository.alias, "worktree_id": selected_worktree_id, "path": request.target, "line_start": line_start, "line_end": line_end},
                    excerpt=excerpt, source_commit=source_commit,
                    file_identity_before=before, file_identity_after=after,
                    observation_preconditions=snapshot.observation_preconditions().model_dump(mode="json"),
                )
            except Exception as exc:
                code = "racy_source_read" if str(exc) == "racy_source_read" else "source_inspection_unavailable"
                data.update(source="registered_repository", error=code)
                warnings.append(code)
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
            result = CtxppReadAdapter(source_root, deny_patterns=[*DEFAULT_DENY_PATTERNS, *workspace.deny_patterns]).inspect(request.target, max_items=30)
            data.update(repository=repository.alias, resolution=resolution, **result)
            warnings.extend(result.get("warnings", []))
    budget = min(28000, request.budget_tokens * 4)
    data.setdefault("observation_preconditions", snapshot.observation_preconditions().model_dump(mode="json"))
    return envelope("inspect", snapshot, bounded_payload(data, budget), warnings=warnings)
