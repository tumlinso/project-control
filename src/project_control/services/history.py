"""Chronological, noise-coalesced history synthesis from read authorities."""

from __future__ import annotations

from typing import Any

from ..graph import ProjectGraph
from ..models import HistoryTraceInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..reconcile import ProjectReconciler
from ..retrieval import authority_label, event_sort_key, material_event, page, records_from_tables


BUDGETS = {"compact": 16 * 1024, "standard": 48 * 1024, "expanded": 96 * 1024}


def _identity(record: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key, value in record.items():
        if key.endswith("_id") or key in {"id", "entity_id", "path", "commit", "subject"}:
            if isinstance(value, str):
                values.add(value.casefold())
            elif isinstance(value, list):
                values.update(str(item).casefold() for item in value)
    return values


def _event(source: str, kind: str, record: dict[str, Any], *, inferred: bool = False) -> dict[str, Any]:
    return {
        "timestamp": record.get("timestamp") or record.get("created_at") or record.get("updated_at") or record.get("observed_at"),
        "revision": record.get("revision"),
        "event_type": record.get("event_type") or record.get("type") or kind,
        "entity_type": record.get("entity_type") or kind,
        "entity_id": record.get("entity_id") or record.get("id") or record.get("task_id") or record.get("commit"),
        "state": record.get("state") or record.get("status") or record.get("result"),
        "summary": record.get("summary") or record.get("rationale") or record.get("title"),
        "source": source,
        "authority_label": authority_label(kind, inferred=inferred),
        "causal_relationship": record.get("caused_by") or record.get("supersedes") or record.get("answer_to"),
        "causal_basis": "explicit_recorded_reference" if any(record.get(key) for key in ("caused_by", "supersedes", "answer_to")) else None,
    }


def history_trace(snapshot: ProjectSnapshot, request: HistoryTraceInput) -> ToolEnvelope:
    reconciled = ProjectReconciler(snapshot).reconcile()
    graph = ProjectGraph(snapshot, reconciled)
    resolution = graph.resolve(request.subject)
    seeds = graph.seed_candidates(request.subject, max_items=16)
    subject_ids = {request.subject.casefold(), *(str(item["id"]).casefold() for item in seeds)}
    if resolution["status"] == "resolved":
        subject_ids.add(str(resolution["entity"]["id"]).casefold())
        subject_ids.update(str(item["id"]).casefold() for item in graph.related(resolution["entity"]["key"], max_items=40))

    events: list[dict[str, Any]] = []
    raw_events = snapshot.todo_tables.get("events", [])
    noise_omitted = 0
    for record in raw_events:
        if not material_event(record):
            noise_omitted += 1
            continue
        if subject_ids.isdisjoint(_identity(record)):
            continue
        events.append(_event("todo_event_log", "event", record))

    tables = {
        "decisions": "decision", "interfaces": "interface", "context_fragments": "context_fragment",
        "run_context_fragments": "context_fragment", "workflow_messages": "message", "messages": "message",
        "rendezvous_arrivals": "rendezvous_arrival", "workspace_events": "workspace",
        "patch_artifacts": "patch_artifact", "integration_requests": "integration", "handoffs": "handoff",
        "checkpoints": "checkpoint", "gates": "gate", "git_commits": "git_commit",
    }
    for table, record in records_from_tables(snapshot.todo_tables, tables):
        if subject_ids.isdisjoint(_identity(record)):
            continue
        events.append(_event(f"todo_export:{table}", tables[table], record))

    workflow = snapshot.todo_workflow if isinstance(snapshot.todo_workflow, dict) else {}
    for name, kind in (("blocking_messages", "message"), ("unresolved_questions", "message"), ("rendezvous", "rendezvous"), ("patch_artifacts", "patch_artifact"), ("integration_queue", "integration")):
        for record in workflow.get(name, []) if isinstance(workflow.get(name), list) else []:
            if not subject_ids.isdisjoint(_identity(record)):
                events.append(_event(f"todo_semantic_workflow:{name}", kind, record))
            if name == "rendezvous":
                for arrival in record.get("arrivals", []):
                    enriched = {**arrival, "id": f"{record.get('id')}:{arrival.get('lane_id')}", "rendezvous_id": record.get("id")}
                    if not subject_ids.isdisjoint(_identity(enriched)):
                        events.append(_event("todo_semantic_workflow:rendezvous_arrivals", "rendezvous_arrival", enriched))

    # Exact task records are useful lifecycle anchors when event retention is bounded.
    for task_id, record in reconciled.tasks.items():
        if task_id.casefold() in subject_ids:
            events.append(_event("todo_semantic_state:task", "task", {**record, "id": task_id}))

    def in_bounds(item: dict[str, Any]) -> bool:
        revision = item.get("revision")
        try:
            numeric = int(revision) if revision is not None else None
        except (TypeError, ValueError):
            numeric = None
        if request.from_revision is not None and numeric is not None and numeric < request.from_revision:
            return False
        if request.to_revision is not None and numeric is not None and numeric > request.to_revision:
            return False
        timestamp = str(item.get("timestamp") or "")
        if request.from_time and timestamp and timestamp < request.from_time:
            return False
        commit = str(item.get("entity_id") or "")
        if request.from_commit and item.get("entity_type") == "git_commit" and commit < request.from_commit:
            return False
        if request.to_commit and item.get("entity_type") == "git_commit" and commit > request.to_commit:
            return False
        return True

    deduped = {(
        item.get("source"), item.get("revision"), item.get("event_type"), item.get("entity_id"), item.get("timestamp")
    ): item for item in events if in_bounds(item)}
    ordered = sorted(deduped.values(), key=event_sort_key)
    query = request.model_dump(mode="json", exclude={"continuation_cursor", "max_events", "detail"})
    selected, pagination = page(ordered, operation="history_trace", query=query, limit=request.max_events, cursor=request.continuation_cursor)
    unsupported = []
    if not any(item.get("causal_relationship") for item in selected):
        unsupported.append({
            "authority_label": "missing_evidence", "kind": "causation",
            "reason": "no_explicit_causal_reference; temporal_adjacency_not_presented_as_causation",
        })
    data = {
        "subject": request.subject,
        "resolution": {"status": resolution["status"], "reason": resolution["reason"], "candidates": resolution.get("candidates", [])},
        "events": selected,
        "causal_relationship_policy": "only_explicit_recorded_references_are_causal; ordering_alone_is_not_rationale",
        "missing_evidence": unsupported,
        "coalescing": {"raw_event_count": len(raw_events), "administrative_or_heartbeat_events_omitted": noise_omitted},
        "pagination": pagination,
        "observation_preconditions": snapshot.observation_preconditions().model_dump(mode="json"),
        "provenance": {"event_log": "todo_readonly_export", "operational_state": "todo_semantic_workflow", "task_semantics": "todo_semantic_state", "git": "git_identity_or_exported_commit_metadata"},
    }
    warnings = snapshot.warnings_for("todo")
    if not ordered:
        warnings.append("history_evidence_unavailable")
    return envelope("history_trace", snapshot, bounded_payload(data, BUDGETS[request.detail]), warnings=list(dict.fromkeys(warnings)))
