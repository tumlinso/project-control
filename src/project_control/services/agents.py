from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import AgentStatusInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..workflow import workflow_summary


def _heartbeat_age(expires_at: str | None) -> int | None:
    if not expires_at:
        return None
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return max(0, int((expiry - datetime.now(timezone.utc)).total_seconds()))
    except ValueError:
        return None


def agent_status(snapshot: ProjectSnapshot, request: AgentStatusInput) -> ToolEnvelope:
    workflow = workflow_summary(snapshot, max_items=100)
    first_class_agents = list(workflow.get("first_class_agents", [])) if workflow["available"] else []
    legacy_claims = []
    for claim in snapshot.todo_status.get("active_claims", []):
        if not isinstance(claim, dict):
            continue
        legacy_claims.append({
            "task_id": claim.get("task_id"),
            "observed_state": "active_claim",
            "heartbeat_lease_remaining_seconds": _heartbeat_age(claim.get("expires_at")),
            "source": "todo_status",
            "confidence": "authoritative",
            "classification": "claim_only_not_first_class_agent",
        })
    children = list(workflow.get("subordinate_local_children", [])) if request.include_children and workflow["available"] else []
    legacy_children = []
    if request.include_children:
        for child in snapshot.todo_tables.get("child_executions", []):
            legacy_children.append({
                "id": child.get("id"),
                "task_id": child.get("task_id"),
                "observed_state": child.get("state"),
                "source": "todo_snapshot",
                "confidence": "authoritative",
                "classification": "subordinate_local_child",
            })
    local = snapshot.local_worker if request.include_local_services else {"status": "not_requested"}
    data = {
        "first_class_agents": first_class_agents,
        "subordinate_local_children": children,
        "claim_observations": legacy_claims,
        "legacy_child_observations": legacy_children if not workflow["available"] else [],
        # Compatibility aliases retain their shapes while never flattening children into agents.
        "agents": first_class_agents,
        "children": children,
        "active_run_id": workflow.get("active_run_id"),
        "workflow_authority_available": bool(workflow["available"]),
        "stale_or_orphaned": snapshot.todo_status.get("orphaned_claims", []),
        "local_services": local,
        "observable_only": True,
    }
    warnings = [] if workflow["available"] else [str(workflow.get("reason") or "agent_state_unavailable")]
    if not workflow["available"] and local.get("status") != "ok":
        warnings.append("agent_state_unavailable")
    return envelope("agent_status", snapshot, bounded_payload(data, 10000), warnings=[*snapshot.warnings_for("todo", "worker"), *warnings])
