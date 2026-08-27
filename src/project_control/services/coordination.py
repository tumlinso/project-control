from __future__ import annotations

from typing import Any

from ..coordination import decode_cursor, encode_cursor, observation_identity, parse_json, table_index
from ..models import CoordinationViewInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..workflow import workflow_view


BUDGETS = {"compact": 24 * 1024, "standard": 48 * 1024, "expanded": 96 * 1024}


def _pick(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: row.get(field) for field in fields if row.get(field) is not None}


def _matches(request: CoordinationViewInput, *, run_id: object = None, lane_id: object = None, task_id: object = None) -> bool:
    return not (
        (request.run_id and str(run_id or "") != request.run_id)
        or (request.lane_id and str(lane_id or "") != request.lane_id)
        or (request.task_id and str(task_id or "") != request.task_id)
    )


def _durable_messages(snapshot: ProjectSnapshot, request: CoordinationViewInput) -> list[dict[str, Any]]:
    recipients = table_index(snapshot.todo_tables.get("workflow_message_recipients", []), "message_id")
    messages = []
    for row in snapshot.todo_tables.get("workflow_messages", []):
        message_recipients = [
            _pick(item, ("recipient_type", "recipient_id"))
            for item in recipients.get((str(row.get("id", "")),), [])
        ]
        lane_matches = not request.lane_id or row.get("author_lane_id") == request.lane_id or any(
            item.get("recipient_type") == "lane" and item.get("recipient_id") == request.lane_id
            for item in message_recipients
        )
        if (
            not lane_matches
            or (request.run_id and str(row.get("run_id") or "") != request.run_id)
            or (request.task_id and str(row.get("task_id") or "") != request.task_id)
        ):
            continue
        if request.since_revision is not None and int(row.get("revision", 0)) <= request.since_revision:
            continue
        if not request.include_resolved_messages and row.get("state") != "open":
            continue
        message = {
            **_pick(row, (
                "id", "run_id", "author_lane_id", "task_id", "kind", "blocking", "state",
                "linked_message_id", "revision", "created_at", "resolved_at",
            )),
            "recipients": message_recipients,
            "references": parse_json(row.get("references_json"), []),
            "authority": "durable_export_enrichment",
        }
        if request.detail != "compact":
            message["payload"] = parse_json(row.get("payload_json"), {})
        messages.append(message)
    return sorted(messages, key=lambda item: (int(item.get("revision", 0)), str(item.get("id", ""))))


def _fragments(snapshot: ProjectSnapshot, request: CoordinationViewInput) -> list[dict[str, Any]]:
    result = []
    for row in snapshot.todo_tables.get("workflow_context_fragments", []):
        if not _matches(request, run_id=row.get("run_id"), lane_id=row.get("lane_id"), task_id=row.get("task_id")):
            continue
        fragment = {
            **_pick(row, (
                "id", "run_id", "lane_id", "task_id", "kind", "version", "content_hash",
                "creation_revision", "created_at", "invalidated_at", "invalidation_revision", "superseded_by",
            )),
            "owner_scope": parse_json(row.get("owner_scope_json"), {}),
            "authority": "durable_export_enrichment",
        }
        if request.detail == "expanded":
            fragment["content"] = parse_json(row.get("content_json"), {})
        result.append(fragment)
    return sorted(result, key=lambda item: (
        str(item.get("run_id", "")), str(item.get("lane_id", "")),
        str(item.get("task_id", "")), str(item.get("kind", "")), int(item.get("version", 0)),
    ))


def _rendezvous(snapshot: ProjectSnapshot, semantic: list[dict[str, Any]], request: CoordinationViewInput) -> list[dict[str, Any]]:
    durable = table_index(snapshot.todo_tables.get("workflow_rendezvous_arrivals", []), "rendezvous_id", "lane_id")
    result = []
    for item in semantic:
        if not _matches(request, run_id=item.get("run_id"), task_id=item.get("join_task_id")):
            continue
        enriched = _pick(item, ("id", "run_id", "barrier_id", "mode", "quorum", "join_task_id", "state"))
        arrivals = []
        for arrival in item.get("arrivals", []):
            if request.lane_id and arrival.get("lane_id") != request.lane_id:
                continue
            row = next(iter(durable.get((str(item.get("id", "")), str(arrival.get("lane_id", ""))), [])), {})
            value = {**arrival, "authority": "todo_semantic_workflow"}
            if request.detail != "compact" and row:
                value.update(_pick(row, (
                    "summary", "base_source_identity", "final_source_identity", "arrived_at",
                )))
                value["artifact"] = parse_json(row.get("artifact_json"), {})
                value["interfaces"] = parse_json(row.get("interfaces_json"), {})
                value["evidence"] = parse_json(row.get("evidence_json"), [])
                value["warnings"] = parse_json(row.get("warnings_json"), [])
                value["enrichment_authority"] = "durable_export"
            arrivals.append(value)
        enriched["arrivals"] = arrivals
        result.append(enriched)
    return result


def coordination_view(snapshot: ProjectSnapshot, request: CoordinationViewInput) -> ToolEnvelope:
    view = workflow_view(snapshot)
    if not view["available"]:
        data = {
            "available": False,
            "reason": view.get("reason"),
            "source_reason": view.get("source_reason"),
            "component_authority": {
                key: value.model_dump(mode="json") for key, value in snapshot.component_authority.items()
            },
            "observation_preconditions": snapshot.observation_preconditions().model_dump(mode="json"),
        }
        return envelope(
            "coordination_view", snapshot, bounded_payload(data, BUDGETS[request.detail]),
            warnings=["todo_workflow_semantic_unavailable"],
        )

    identity = observation_identity(
        snapshot.workspace_id, view.get("revision"), view.get("read_authority_fingerprint"),
        request.run_id, request.lane_id, request.task_id, request.since_revision,
        request.include_resolved_messages, request.include_historical_arrivals,
    )
    offset = decode_cursor(request.continuation_cursor, tool="coordination_view", identity=identity)
    runs = []
    lane_roles = []
    lane_queues = []
    dispatches = []
    workspaces = []
    for run in view.get("runs", []):
        if request.run_id and run.get("id") != request.run_id:
            continue
        lanes = []
        for lane in run.get("lanes", []):
            task_ids = [str(item.get("task_id")) for item in lane.get("queue", [])]
            if request.lane_id and lane.get("id") != request.lane_id:
                continue
            if request.task_id and request.task_id not in task_ids:
                continue
            lane_summary = _pick(lane, ("id", "parent_lane_id", "role", "state", "workspace_mode", "context_cursor"))
            lane_summary["serial_queue"] = list(lane.get("queue", []))
            lanes.append(lane_summary)
            lane_roles.append(_pick(lane, ("id", "parent_lane_id", "role", "state", "workspace_mode")))
            lane_queues.append({"lane_id": lane.get("id"), "items": list(lane.get("queue", []))})
            if isinstance(lane.get("dispatch"), dict):
                dispatches.append({
                    **_pick(lane["dispatch"], (
                        "dispatch_id", "task_id", "heartbeat_at", "heartbeat_fresh", "context_version", "observable",
                    )),
                    "run_id": run.get("id"), "lane_id": lane.get("id"), "role": lane.get("role"),
                    "authority": "todo_semantic_workflow",
                })
            if isinstance(lane.get("workspace"), dict):
                workspaces.append({
                    **_pick(lane["workspace"], (
                        "id", "run_id", "lane_id", "repository", "base_commit", "branch", "mode", "state",
                        "integration_task_id", "merge_result", "cleanup_eligible",
                    )),
                    "authority": "todo_semantic_workflow",
                })
        if lanes or not (request.lane_id or request.task_id):
            runs.append({**_pick(run, ("id", "root_task_id", "status", "active_charter_version")), "lanes": lanes})

    messages = _durable_messages(snapshot, request)
    fragments = _fragments(snapshot, request)
    rendezvous = _rendezvous(snapshot, list(view.get("rendezvous", [])), request)
    semantic_lists = {
        "first_class_agents": [_pick(item, (
            "run_id", "lane_id", "role", "dispatch_id", "task_id", "heartbeat_at",
            "heartbeat_fresh", "context_version", "observable", "source_freshness",
        )) for item in view.get("first_class_agents", []) if _matches(
            request, run_id=item.get("run_id"), lane_id=item.get("lane_id"), task_id=item.get("task_id")
        )],
        "subordinate_local_children": [_pick(item, (
            "child_execution_id", "parent_task_id", "parent_lane_id", "state", "access_mode",
            "created_at", "updated_at",
        )) for item in view.get("local_children", []) if _matches(
            request, lane_id=item.get("parent_lane_id"), task_id=item.get("parent_task_id")
        )],
        "patch_artifacts": [_pick(item, (
            "id", "workspace_id", "run_id", "lane_id", "task_id", "kind", "artifact_ref",
            "content_hash", "base_commit", "state", "created_at",
        )) for item in view.get("patch_artifacts", []) if _matches(
            request, run_id=item.get("run_id"), lane_id=item.get("lane_id"), task_id=item.get("task_id")
        )],
        "integration_queue": [item for item in view.get("integration_queue", []) if _matches(
            request, run_id=item.get("run_id"), lane_id=item.get("integrator_lane_id"), task_id=item.get("integration_task_id")
        )],
        "recovery_needed": list(view.get("recovery_needed", [])),
        "safe_parallel_groups": list(view.get("safe_parallel_groups", [])),
    }
    collections: dict[str, list[Any]] = {
        "runs": runs, "roles": lane_roles, "lane_queues": lane_queues, "dispatches": dispatches,
        "messages": messages, "context_fragments": fragments, "rendezvous": rendezvous,
        "workspaces": workspaces, **semantic_lists,
    }
    paged = {name: values[offset: offset + request.max_items] for name, values in collections.items()}
    more = any(len(values) > offset + request.max_items for values in collections.values())
    data = {
        "available": True,
        "authority": "todo_semantic_workflow",
        "revision": view.get("revision"),
        "active_run_id": view.get("active_run_id"),
        **paged,
        "decisions": [
            {**_pick(item, ("id", "title", "value", "updated_at", "revision")), "authority": "durable_export_enrichment"}
            for item in snapshot.todo_tables.get("decisions", [])[offset: offset + request.max_items]
        ],
        "interfaces": [
            {**_pick(item, ("id", "owner_task_id", "state", "version", "content_hash", "revision")), "authority": "durable_export_enrichment"}
            for item in snapshot.todo_tables.get("interfaces", [])[offset: offset + request.max_items]
        ],
        "component_authority": {
            key: value.model_dump(mode="json") for key, value in snapshot.component_authority.items()
        },
        "observation_preconditions": snapshot.observation_preconditions().model_dump(mode="json"),
        "ranking": {
            "offset": offset,
            "max_items_per_section": request.max_items,
            "counts_considered": {name: len(values) for name, values in collections.items()},
            "counts_returned": {name: len(paged[name]) for name in collections},
        },
        "continuation_cursor": encode_cursor("coordination_view", identity, offset + request.max_items) if more else None,
    }
    warnings = snapshot.warnings_for("todo")
    return envelope(
        "coordination_view", snapshot, bounded_payload(data, BUDGETS[request.detail]),
        warnings=warnings,
    )
