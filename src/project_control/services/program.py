"""Cross-workspace, read-only program context synthesis."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..config import ProjectControlConfig
from ..models import ObservationPreconditions, ProgramContextInput, ProjectSnapshot, VersionedPrecondition
from ..normalize import bounded_payload
from ..registry import WorkspaceRegistry
from ..snapshot import SnapshotBuilder
from ..workflow import workflow_view


BUDGETS = {"compact": 16 * 1024, "standard": 64 * 1024, "expanded": 160 * 1024}
_WORDS = re.compile(r"[A-Za-z][A-Za-z0-9_.:/+-]{2,}")
_TABLE_KINDS = (
    "tasks", "decisions", "interfaces", "interface_consumers", "invariants",
    "checkpoints", "gates", "context_fragments", "handoffs", "artifacts",
)


def _terms(value: str) -> set[str]:
    return {item.lower() for item in _WORDS.findall(value)}


def _record_text(item: dict[str, Any]) -> str:
    fields = ("id", "title", "summary", "description", "decision", "rationale", "name", "path", "state")
    return " ".join(str(item.get(field, "")) for field in fields)


def _public_text(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("/"):
        return "[private path omitted]"
    return value


def _rank_records(snapshot: ProjectSnapshot, question: str, maximum: int) -> list[dict[str, Any]]:
    query = _terms(question)
    candidates: list[tuple[int, str, str, dict[str, Any]]] = []
    for kind in _TABLE_KINDS:
        for raw in snapshot.todo_tables.get(kind, []):
            if not isinstance(raw, dict):
                continue
            overlap = len(query & _terms(_record_text(raw)))
            exact = 1 if any(term in _record_text(raw).lower() for term in query) else 0
            current = 1 if str(raw.get("current_relevance") or raw.get("relevance")) in {"current", "current_attention"} else 0
            score = overlap * 100 + exact * 20 + current * 10
            if score or not query:
                candidates.append((score, kind, str(raw.get("id", "")), raw))
    candidates.sort(key=lambda value: (-value[0], value[1], value[2]))
    result = []
    for score, kind, _, raw in candidates[:maximum]:
        result.append({
            "kind": kind.rstrip("s"),
            "id": raw.get("id"),
            "title": _public_text(raw.get("title") or raw.get("name")),
            "state": raw.get("state") or raw.get("status") or raw.get("effective_state"),
            "summary": _public_text(raw.get("summary") or raw.get("description") or raw.get("decision")),
            "authority": "todo_durable_export",
            "relevance": "heuristic" if score else "unranked",
            "relevance_score": score,
        })
    return result


def _workflow_context(snapshot: ProjectSnapshot, maximum: int) -> dict[str, Any]:
    view = workflow_view(snapshot)
    if not view.get("available"):
        return {
            "available": False,
            "reason": view.get("source_reason") or view.get("reason"),
            "revision": view.get("revision"),
            "authority": "todo_semantic_workflow",
        }
    runs = [item for item in view.get("runs", []) if isinstance(item, dict)]
    agents = [item for item in view.get("first_class_agents", []) if isinstance(item, dict)]
    children = [item for item in view.get("local_children", []) if isinstance(item, dict)]
    messages = [item for item in view.get("blocking_messages", []) if isinstance(item, dict)]
    return {
        "available": True,
        "revision": view.get("revision"),
        "active_run_id": view.get("active_run_id"),
        "runs": [
            {key: item.get(key) for key in ("id", "status", "root_task_id", "active_charter_version") if item.get(key) is not None}
            for item in runs[:maximum]
        ],
        "first_class_agents": [
            {key: item.get(key) for key in ("run_id", "lane_id", "role", "task_id", "context_version", "observable") if item.get(key) is not None}
            for item in agents[:maximum]
        ],
        "subordinate_local_children": [
            {key: item.get(key) for key in ("parent_task_id", "parent_lane_id", "state", "access_mode") if item.get(key) is not None}
            for item in children[:maximum]
        ],
        "blocking_messages": [
            {key: item.get(key) for key in ("id", "run_id", "author_lane_id", "task_id", "kind", "state", "revision") if item.get(key) is not None}
            for item in messages[:maximum]
        ],
        "authority": "todo_semantic_workflow",
    }


def _preconditions(snapshot: ProjectSnapshot) -> ObservationPreconditions:
    base = snapshot.observation_preconditions()
    workflow = workflow_view(snapshot)
    agents = [item for item in workflow.get("first_class_agents", []) if isinstance(item, dict)]
    fragment_rows = snapshot.todo_tables.get("context_fragments", [])
    interface_rows = snapshot.todo_tables.get("interfaces", [])
    return base.model_copy(update={
        "task_ids": sorted({str(item["task_id"]) for item in agents if item.get("task_id")}),
        "lane_ids": sorted({str(item["lane_id"]) for item in agents if item.get("lane_id")}),
        "context_fragments": {
            str(item["id"]): VersionedPrecondition(
                version=item.get("version"), content_hash=item.get("content_hash"), state=item.get("state"),
            )
            for item in fragment_rows if isinstance(item, dict) and item.get("id")
        },
        "interfaces": {
            str(item["id"]): VersionedPrecondition(
                version=item.get("version"), content_hash=item.get("content_hash"), state=item.get("state"),
            )
            for item in interface_rows if isinstance(item, dict) and item.get("id")
        },
    })


def _workspace_context(snapshot: ProjectSnapshot, question: str, maximum: int) -> dict[str, Any]:
    preconditions = _preconditions(snapshot)
    return {
        "workspace_id": snapshot.workspace_id,
        "display_name": snapshot.display_name,
        "identity": {
            "project_uuid": snapshot.project_uuid,
            "repositories": {
                alias: {
                    "commit": identity.commit,
                    "dirty": identity.dirty,
                    "working_tree_fingerprint": identity.working_tree_fingerprint,
                }
                for alias, identity in sorted(snapshot.repositories.items())
            },
        },
        "authority_cursor": {
            "todo_revision": snapshot.todo_revision,
            "todo_semantic_fingerprint": preconditions.todo_semantic_authority_fingerprint,
            "workflow_revision": preconditions.workflow_revision,
            "workflow_fingerprint": preconditions.workflow_authority_fingerprint,
            "observed_at": snapshot.observed_at,
        },
        "relevant_commitments": _rank_records(snapshot, question, maximum),
        "coordination": _workflow_context(snapshot, maximum),
        "observation_preconditions": preconditions.model_dump(mode="json"),
        "warnings": list(dict.fromkeys([*snapshot.warnings, *snapshot.warnings_for("todo")])),
    }


def _cross_project_relationships(snapshots: list[ProjectSnapshot]) -> dict[str, list[dict[str, Any]]]:
    interfaces: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    tasks: dict[str, list[str]] = {}
    for snapshot in snapshots:
        for item in snapshot.todo_tables.get("interfaces", []):
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or item.get("name") or "").casefold()
            if key:
                interfaces.setdefault(key, []).append((snapshot.workspace_id, item))
        for item in snapshot.todo_tables.get("tasks", []):
            if isinstance(item, dict) and item.get("id"):
                tasks.setdefault(str(item["id"]), []).append(snapshot.workspace_id)
    shared = []
    contradictions = []
    for key, records in sorted(interfaces.items()):
        workspaces = sorted({workspace for workspace, _ in records})
        if len(workspaces) < 2:
            continue
        states = sorted({str(item.get("state")) for _, item in records if item.get("state") is not None})
        shared.append({
            "interface": key,
            "workspaces": workspaces,
            "relationship": "same_recorded_interface_identifier",
            "authority": "per_project_todo_export",
            "architectural_dependency_inferred": False,
        })
        if len(states) > 1:
            contradictions.append({
                "subject": key,
                "kind": "interface_state_disagreement",
                "states": states,
                "workspaces": workspaces,
                "basis": "matching recorded interface identifier",
            })
    duplicates = [
        {"task_id": task_id, "workspaces": sorted(set(workspaces)), "classification": "possible_duplicated_ownership"}
        for task_id, workspaces in sorted(tasks.items()) if len(set(workspaces)) > 1
    ]
    return {"cross_project_interfaces": shared, "contradictions": contradictions, "duplicated_ownership": duplicates}


def program_context(
    config: ProjectControlConfig,
    request: ProgramContextInput,
    *,
    builder: SnapshotBuilder | None = None,
) -> dict[str, Any]:
    """Observe configured or explicit workspaces without asserting global atomicity."""

    registry = WorkspaceRegistry(config)
    workspace_ids, program = registry.program_workspaces(
        program_id=request.program_id,
        workspaces=request.workspaces,
    )
    active_builder = builder or SnapshotBuilder(config)
    snapshots: list[ProjectSnapshot] = []
    failures: list[dict[str, str]] = []
    for workspace_id in workspace_ids:
        try:
            snapshots.append(active_builder.build(workspace_id))
        except Exception:
            failures.append({"workspace_id": workspace_id, "error_code": "workspace_observation_unavailable"})

    per_project_limit = max(1, min(request.max_items, request.max_items // max(1, len(workspace_ids))))
    projects = [_workspace_context(item, request.question, per_project_limit) for item in snapshots]
    observed = sorted(item.observed_at for item in snapshots)
    skew_seconds = 0.0
    if len(observed) > 1:
        skew_seconds = (datetime.fromisoformat(observed[-1].replace("Z", "+00:00")) - datetime.fromisoformat(observed[0].replace("Z", "+00:00"))).total_seconds()
    warnings = ["cross_project_observations_not_atomic"] if len(workspace_ids) > 1 else []
    if failures:
        warnings.append("program_workspace_observation_partial")
    relationships = _cross_project_relationships(snapshots)
    data = {
        "program": program,
        "question": request.question,
        "projects": projects,
        "failed_projects": failures,
        "cross_project_synthesis": {
            "basis": "independent_authority_observations_and_query_relevance",
            "architectural_authority_from_membership": False,
            "observation_atomicity": "independent_not_global",
            "observation_skew_seconds": skew_seconds,
            **relationships,
            "missing_links": [
                "program membership does not establish source dependencies, interfaces, or ownership"
            ],
        },
        "observation_preconditions": {
            item.workspace_id: _preconditions(item).model_dump(mode="json") for item in snapshots
        },
        "ranking": {
            "projects_considered": len(workspace_ids),
            "projects_returned": len(projects),
            "items_considered": sum(len(item["relevant_commitments"]) for item in projects),
            "max_items": request.max_items,
            "deterministic": True,
        },
    }
    return {
        "schema_version": 2,
        "tool": "program_context",
        "status": "partial" if failures else "ok",
        "data": bounded_payload(data, BUDGETS[request.detail]),
        "warnings": warnings,
    }
