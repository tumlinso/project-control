from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import AgentStatusInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload


def _heartbeat_age(expires_at: str | None) -> int | None:
    if not expires_at:
        return None
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return max(0, int((expiry - datetime.now(timezone.utc)).total_seconds()))
    except ValueError:
        return None


def agent_status(snapshot: ProjectSnapshot, request: AgentStatusInput) -> ToolEnvelope:
    agents = []
    for claim in snapshot.todo_status.get("active_claims", []):
        if not isinstance(claim, dict):
            continue
        agents.append({
            "task_id": claim.get("task_id"),
            "observed_state": "active_claim",
            "heartbeat_lease_remaining_seconds": _heartbeat_age(claim.get("expires_at")),
            "source": "todo_status",
            "confidence": "authoritative",
        })
    children = []
    if request.include_children:
        for child in snapshot.todo_tables.get("child_executions", []):
            children.append({
                "id": child.get("id"),
                "task_id": child.get("task_id"),
                "observed_state": child.get("state"),
                "source": "todo_snapshot",
                "confidence": "authoritative",
            })
    local = snapshot.local_worker if request.include_local_services else {"status": "not_requested"}
    data = {
        "agents": agents,
        "children": children,
        "stale_or_orphaned": snapshot.todo_status.get("orphaned_claims", []),
        "local_services": local,
        "observable_only": True,
    }
    warnings = [] if agents or local.get("status") == "ok" else ["agent_state_unavailable"]
    return envelope("agent_status", snapshot, bounded_payload(data, 10000), warnings=[*snapshot.warnings_for("todo", "worker"), *warnings])
