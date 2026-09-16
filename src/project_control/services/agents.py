from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import AgentStatusInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_envelope
from ..workflow import workflow_summary


def _heartbeat_age(expires_at: str | None) -> int | None:
    if not expires_at:
        return None
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return max(0, int((expiry - datetime.now(timezone.utc)).total_seconds()))
    except ValueError:
        return None


def agent_status(snapshot: ProjectSnapshot, request: AgentStatusInput,
                 *, observer_backend: dict[str, Any] | None = None) -> ToolEnvelope:
    workflow = workflow_summary(snapshot, max_items=100)
    first_class_agents = list(workflow.get("first_class_agents", [])) if workflow["available"] else []
    lane_worktrees = {
        str(lane.get("id")): (lane.get("workspace") or {}).get("worktree_id")
        for run in [workflow.get("active_run")] if isinstance(run, dict)
        for lane in run.get("lanes", []) if isinstance(lane, dict)
    }
    first_class_agents = [
        {**item, **({"worktree_id": lane_worktrees.get(str(item.get("lane_id")))} if lane_worktrees.get(str(item.get("lane_id"))) else {})}
        for item in first_class_agents
    ]
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
    data = {
        "first_class_agents": first_class_agents,
        "claim_observations": legacy_claims,
        "active_run_id": workflow.get("active_run_id"),
        "workflow_authority_available": bool(workflow["available"]),
        "stale_or_orphaned": snapshot.todo_status.get("orphaned_claims", []),
        "observer_jobs": [],
        "observable_only": True,
    }
    if request.include_children:
        data["subordinate_local_children"] = children
        data["legacy_child_observations"] = legacy_children if not workflow["available"] else []
    if request.include_local_services:
        # The observer backend is in-process and deliberately has no daemon
        # snapshot.  Prefer its existing (never started for status) state over
        # a missing legacy supervisor file, while retaining a healthy daemon
        # snapshot when one is present.
        local_services = observer_backend or snapshot.local_worker
        if observer_backend is not None and snapshot.local_worker.get("status") == "ok":
            local_services = {
                **observer_backend,
                "daemon_supervisor": snapshot.local_worker,
            }
        data["local_services"] = local_services
    warnings = [] if workflow["available"] else [str(workflow.get("reason") or "agent_state_unavailable")]
    local_status = (observer_backend or snapshot.local_worker).get("status")
    if not workflow["available"] and request.include_local_services and local_status != "ok":
        warnings.append("agent_state_unavailable")
    provider_warnings = snapshot.warnings_for("todo")
    if request.include_local_services:
        worker_warnings = snapshot.warnings_for("worker")
        # A missing daemon state file is not a warning when an already-live
        # in-process observer backend supplied the local service state.
        if observer_backend is not None and observer_backend.get("status") == "ok":
            worker_warnings = [item for item in worker_warnings
                               if item != "local_worker_state_unavailable"]
        provider_warnings.extend(worker_warnings)
    return bounded_envelope(
        envelope("agent_status", snapshot, data, warnings=[*provider_warnings, *warnings], compact_identity=True),
        10000,
    )
