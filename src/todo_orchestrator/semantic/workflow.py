"""Normalized additive read model for first-class workflow and subordinate children."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ..completion import is_successful_terminal
from ..graph import evaluate_dependencies
from ..resources import matching_instances


def _exists(conn, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _fresh(value: str | None, seconds: int) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed >= datetime.now(timezone.utc) - timedelta(seconds=seconds)


def _expired(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return True
    return parsed <= datetime.now(timezone.utc)


def _paths_overlap(left: str, right: str) -> bool:
    first = left.strip("/")
    second = right.strip("/")
    return first == second or first.startswith(second + "/") or second.startswith(first + "/")


def _task_scopes(conn, task_id: str) -> list[tuple[str, str]]:
    return [
        (str(row["mode"]), str(row["path"]))
        for row in conn.execute(
            "SELECT mode,path FROM ownership_scopes WHERE task_id=? ORDER BY mode,path", (task_id,)
        )
    ]


def _isolated_overlap_is_safe(conn, run_id: str, left_lane: str, right_lane: str) -> bool:
    rows = conn.execute(
        "SELECT lane_id,mode,base_commit,integration_task_id,state FROM workflow_workspaces "
        "WHERE run_id=? AND lane_id IN (?,?) ORDER BY lane_id",
        (run_id, left_lane, right_lane),
    ).fetchall()
    return bool(
        len(rows) == 2
        and all(row["mode"] == "isolated_merge" and row["state"] not in {"failed", "rejected"} for row in rows)
        and rows[0]["base_commit"] == rows[1]["base_commit"]
        and rows[0]["integration_task_id"]
        and rows[0]["integration_task_id"] == rows[1]["integration_task_id"]
    )


def _scope_safe(conn, run_id: str, left: dict[str, str], right: dict[str, str]) -> bool:
    overlaps = [
        (left_scope, right_scope)
        for left_scope in _task_scopes(conn, left["task_id"])
        for right_scope in _task_scopes(conn, right["task_id"])
        if _paths_overlap(left_scope[1], right_scope[1])
    ]
    if not overlaps:
        return True
    if all(left_scope[0] == right_scope[0] == "read" for left_scope, right_scope in overlaps):
        return True
    return _isolated_overlap_is_safe(conn, run_id, left["lane_id"], right["lane_id"])


def _lock_safe(conn, task_ids: list[str]) -> bool:
    placeholders = ",".join("?" for _ in task_ids)
    rows = conn.execute(
        f"SELECT tl.lock_name,n.capacity,COUNT(*) AS demand FROM task_locks tl "
        f"JOIN named_locks n ON n.name=tl.lock_name WHERE tl.phase='claim' "
        f"AND tl.task_id IN ({placeholders}) GROUP BY tl.lock_name,n.capacity",
        task_ids,
    ).fetchall()
    for row in rows:
        active = int(conn.execute(
            "SELECT COUNT(*) FROM lock_leases WHERE lock_name=? AND state='active'", (row["lock_name"],)
        ).fetchone()[0])
        if active + int(row["demand"]) > int(row["capacity"]):
            return False
    return True


def _resource_safe(conn, task_ids: list[str]) -> bool:
    demands: list[tuple[set[str], int]] = []
    for task_id in task_ids:
        for row in conn.execute(
            "SELECT selector,amount FROM resource_requests "
            "WHERE task_id=? AND phase='claim' AND required=1 ORDER BY id", (task_id,)
        ):
            instances = {str(item["id"]) for item in matching_instances(conn, str(row["selector"]))}
            if not instances:
                return False
            demands.append((instances, int(row["amount"])))
    for index, (instances, amount) in enumerate(demands):
        related = [(pool, value) for pool, value in demands[index:] if pool.intersection(instances)]
        required = amount + sum(value for pool, value in related[1:] if pool == instances)
        capacity = sum(
            int(row["capacity"]) - int(conn.execute(
                "SELECT COUNT(*) FROM resource_leases WHERE instance_id=? AND state='active'", (instance_id,)
            ).fetchone()[0])
            for instance_id in instances
            for row in [conn.execute("SELECT capacity FROM resource_instances WHERE id=?", (instance_id,)).fetchone()]
            if row
        )
        if required > capacity or any(pool != instances for pool, _ in related[1:]):
            return False
    return True


def _safe_parallel_group(conn, run_id: str, candidates: list[dict[str, str]]) -> list[str]:
    selected: list[dict[str, str]] = []
    for candidate in sorted(candidates, key=lambda item: item["lane_id"]):
        proposed = selected + [candidate]
        task_ids = [item["task_id"] for item in proposed]
        if not _lock_safe(conn, task_ids) or not _resource_safe(conn, task_ids):
            continue
        if any(not _scope_safe(conn, run_id, candidate, existing) for existing in selected):
            continue
        selected.append(candidate)
    return [item["lane_id"] for item in selected] if len(selected) > 1 else []


def workflow_state(conn, project: dict[str, object]) -> dict[str, object]:
    revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
    if not _exists(conn, "workflow_runs"):
        return {
            "available": False,
            "reason": "workflow_semantic_read_unavailable",
            "revision": revision,
            "first_class_agents": [],
            "local_children": [],
        }
    freshness = int(project.get("configuration", {}).get("workflow_heartbeat_fresh_seconds", 180))
    runs: list[dict[str, object]] = []
    first_class: list[dict[str, object]] = []
    safe_parallel: list[list[str]] = []
    actionable_run_ids: list[str] = []
    for run in conn.execute("SELECT * FROM workflow_runs ORDER BY created_at,id"):
        lanes: list[dict[str, object]] = []
        ready_candidates: list[dict[str, str]] = []
        for lane in conn.execute("SELECT * FROM workflow_lanes WHERE run_id=? ORDER BY parent_lane_id,id", (run["id"],)):
            queue = [
                {"position": int(row["position"]), "task_id": row["task_id"], "state": row["state"]}
                for row in conn.execute("SELECT position,task_id,state FROM workflow_lane_tasks WHERE lane_id=? ORDER BY position", (lane["id"],))
            ]
            dispatch = conn.execute(
                "SELECT d.*,s.state AS session_state,c.state AS claim_state,c.task_id AS claim_task "
                "FROM workflow_dispatches d JOIN sessions s ON s.id=d.session_id JOIN claims c ON c.id=d.claim_id "
                "WHERE d.lane_id=? AND d.state='active' ORDER BY d.created_at DESC LIMIT 1",
                (lane["id"],),
            ).fetchone()
            active_task = next((item for item in queue if item["state"] == "active"), None)
            workspace = conn.execute("SELECT * FROM workflow_workspaces WHERE run_id=? AND lane_id=?", (run["id"], lane["id"])).fetchone()
            observable = bool(
                dispatch
                and dispatch["session_state"] == "active"
                and dispatch["claim_state"] == "active"
                and active_task
                and dispatch["claim_task"] == active_task["task_id"]
                and int(dispatch["context_version"]) > 0
                and _fresh(dispatch["heartbeat_at"], freshness)
                and (lane["workspace_mode"] not in {"isolated_merge", "contract_split"} or workspace)
            )
            dispatch_view = None
            if dispatch:
                dispatch_view = {
                    "dispatch_id": dispatch["id"],
                    "session_id": dispatch["session_id"],
                    "claim_id": dispatch["claim_id"],
                    "task_id": dispatch["claim_task"],
                    "heartbeat_at": dispatch["heartbeat_at"],
                    "heartbeat_fresh": _fresh(dispatch["heartbeat_at"], freshness),
                    "hostname": dispatch["hostname"],
                    "pid": dispatch["pid"],
                    "process_start": dispatch["process_start"],
                    "context_version": int(dispatch["context_version"]),
                    "observable": observable,
                }
                if observable:
                    first_class.append({
                        "run_id": run["id"], "lane_id": lane["id"], "role": lane["role"], **dispatch_view,
                    })
            queued_task = next((item for item in queue if item["state"] == "queued"), None)
            dependencies_ready = bool(queued_task and evaluate_dependencies(conn, str(queued_task["task_id"]))[0])
            task_claimable = bool(queued_task and conn.execute(
                "SELECT 1 FROM tasks t WHERE t.id=? AND t.status IN ('planned','in_progress') "
                "AND NOT EXISTS(SELECT 1 FROM claims c WHERE c.task_id=t.id AND c.state='active')",
                (str(queued_task["task_id"]),),
            ).fetchone())
            if lane["state"] == "ready" and queued_task and dependencies_ready and task_claimable and not dispatch:
                ready_candidates.append({"lane_id": str(lane["id"]), "task_id": str(queued_task["task_id"])})
            lanes.append({
                "id": lane["id"], "parent_lane_id": lane["parent_lane_id"], "role": lane["role"],
                "state": lane["state"], "workspace_mode": lane["workspace_mode"],
                "context_cursor": int(lane["context_cursor"]), "queue": queue,
                "dispatch": dispatch_view, "workspace": dict(workspace) if workspace else None,
            })
        group = _safe_parallel_group(conn, str(run["id"]), ready_candidates)
        if run["status"] == "active" and ready_candidates:
            actionable_run_ids.append(str(run["id"]))
        if group:
            safe_parallel.append(group)
        runs.append({
            "id": run["id"], "root_task_id": run["root_task_id"], "status": run["status"],
            "active_charter_version": int(run["active_charter_version"]), "lanes": lanes,
        })
    messages = [
        {
            "id": row["id"], "run_id": row["run_id"], "author_lane_id": row["author_lane_id"],
            "task_id": row["task_id"], "kind": row["kind"], "blocking": bool(row["blocking"]),
            "state": row["state"], "revision": int(row["revision"]),
        }
        for row in conn.execute("SELECT * FROM workflow_messages WHERE state='open' AND (blocking=1 OR kind='question') ORDER BY revision,id")
    ]
    rendezvous = []
    for row in conn.execute("SELECT * FROM workflow_rendezvous ORDER BY run_id,id"):
        arrivals = [dict(item) for item in conn.execute(
            "SELECT lane_id,task_id,state,context_version,revision FROM workflow_rendezvous_arrivals WHERE rendezvous_id=? ORDER BY lane_id",
            (row["id"],),
        )]
        rendezvous.append({
            "id": row["id"], "run_id": row["run_id"], "barrier_id": row["barrier_id"],
            "mode": row["mode"], "quorum": row["quorum"], "join_task_id": row["join_task_id"],
            "state": row["state"], "arrivals": arrivals,
        })
    integrations = [
        {
            "id": row["id"], "run_id": row["run_id"], "integration_task_id": row["integration_task_id"],
            "integrator_lane_id": row["integrator_lane_id"], "position": int(row["position"]),
            "state": row["state"], "conflict": json.loads(row["conflict_json"] or "{}"),
        }
        for row in conn.execute("SELECT * FROM workflow_integration_queue ORDER BY run_id,integration_task_id,position")
    ]
    patch_artifacts = [
        {
            "id": row["id"], "workspace_id": row["workspace_id"], "run_id": row["run_id"],
            "lane_id": row["lane_id"], "task_id": row["task_id"], "kind": row["kind"],
            "artifact_ref": row["artifact_ref"], "content_hash": row["content_hash"],
            "base_commit": row["base_commit"], "state": row["state"], "created_at": row["created_at"],
        }
        for row in conn.execute(
            "SELECT a.*,w.run_id,w.lane_id FROM workflow_patch_artifacts a "
            "JOIN workflow_workspaces w ON w.id=a.workspace_id ORDER BY w.run_id,w.lane_id,a.created_at,a.id"
        )
    ]
    local_children = [
        {
            "child_execution_id": row["id"], "parent_claim_id": row["parent_claim_id"],
            "parent_task_id": row["task_id"], "parent_lane_id": row["parent_lane_id"],
            "state": row["state"], "access_mode": row["access_mode"],
        }
        for row in conn.execute(
            "SELECT c.*,d.lane_id AS parent_lane_id FROM child_executions c "
            "LEFT JOIN workflow_dispatches d ON d.claim_id=c.parent_claim_id AND d.state='active' ORDER BY c.created_at,c.id"
        )
    ]
    recovery_needed = [
        {"kind": "dispatch", "id": row["id"], "reason": "stale_heartbeat"}
        for row in conn.execute("SELECT id,heartbeat_at FROM workflow_dispatches WHERE state='active'")
        if not _fresh(row["heartbeat_at"], freshness)
    ]
    recovery_needed.extend(
        {"kind": "workspace", "id": row["id"], "reason": row["state"]}
        for row in conn.execute(
            "SELECT id,state FROM workflow_workspaces WHERE state IN "
            "('conflict','applying','apply_failed','applied_pending_wave','finalizing',"
            "'finalization_failed','provisioning_failed')"
        )
    )
    recovery_needed.extend(
        {"kind": "claim", "id": row["id"], "task_id": row["task_id"], "reason": "expired_lease"}
        for row in conn.execute("SELECT id,task_id,expires_at FROM claims WHERE state='active' ORDER BY id")
        if _expired(row["expires_at"])
    )
    recovery_needed.extend(
        {"kind": "session", "id": row["id"], "reason": "stale_heartbeat"}
        for row in conn.execute(
            "SELECT DISTINCT s.id,s.last_seen_at FROM sessions s "
            "JOIN claims c ON c.session_id=s.id AND c.state='active' "
            "WHERE s.state='active' ORDER BY s.id"
        )
        if not _fresh(row["last_seen_at"], freshness)
    )
    recovery_needed.extend(
        {"kind": "local_child", "id": row["child_execution_id"], "attempt_id": row["id"], "reason": "expired_attempt"}
        for row in conn.execute("SELECT id,child_execution_id,expires_at FROM child_attempts WHERE state='active' ORDER BY id")
        if _expired(row["expires_at"])
    )
    recovery_needed.extend(
        {"kind": "gate", "id": row["id"], "task_id": row["task_id"], "reason": row["status"]}
        for row in conn.execute(
            "SELECT g.id,g.task_id,g.status,t.status AS task_status,t.result AS task_result "
            "FROM gates g JOIN tasks t ON t.id=g.task_id "
            "WHERE g.required=1 AND g.status IN ('running','failed','invalidated') "
            "AND t.status IN ('in_progress','done') ORDER BY g.id"
        )
        if not is_successful_terminal(
            {"status": row["task_status"], "result": row["task_result"]}
        )
    )
    recovery_needed.extend(
        {"kind": "lock", "id": row["id"], "reason": "expired_lease"}
        for row in conn.execute("SELECT id,expires_at FROM lock_leases WHERE state='active' ORDER BY id")
        if _expired(row["expires_at"])
    )
    recovery_needed.extend(
        {"kind": "resource", "id": row["id"], "reason": "expired_lease"}
        for row in conn.execute("SELECT id,expires_at FROM resource_leases WHERE state='active' ORDER BY id")
        if _expired(row["expires_at"])
    )
    recovery_needed.extend(
        {"kind": "integration", "id": row["id"], "reason": row["state"]}
        for row in conn.execute(
            "SELECT id,state FROM workflow_integration_queue "
            "WHERE state IN ('conflict','applying','apply_failed','applied_pending_wave',"
            "'finalizing','finalization_failed') ORDER BY id"
        )
    )
    recovery_needed = sorted(
        {json.dumps(item, sort_keys=True): item for item in recovery_needed}.values(),
        key=lambda item: (str(item["kind"]), str(item["id"]), str(item.get("reason", ""))),
    )
    return {
        "available": True,
        "revision": revision,
        "active_run_id": (
            actionable_run_ids[0]
            if actionable_run_ids
            else next((str(run["id"]) for run in runs if run["status"] == "active"), None)
        ),
        "runs": runs,
        "first_class_agents": first_class,
        "local_children": local_children,
        "blocking_messages": messages,
        "unresolved_questions": [item for item in messages if item["kind"] == "question"],
        "rendezvous": rendezvous,
        "patch_artifacts": patch_artifacts,
        "pending_patches": [item for item in patch_artifacts if item["state"] in {"pending", "queued"}],
        "integration_queue": integrations,
        "recovery_needed": recovery_needed,
        "safe_parallel_groups": safe_parallel,
    }
