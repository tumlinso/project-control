"""Budgeted task-specific model context capsules and on-demand expansion."""

from __future__ import annotations

import json
import shlex
import sqlite3
import sys

from .barriers import barrier_report
from .graph import evaluate_dependencies
from .readiness import explain_task
from .reporting import child_results_for_task


def _json_rows(rows) -> list[dict[str, object]]:
    return [dict(row) for row in rows]


def build_context(
    conn: sqlite3.Connection,
    *,
    project_revision: int,
    session: dict[str, object],
    session_token: str,
    claim: dict[str, object],
    claim_token: str,
    budget: int = 12000,
) -> dict[str, object]:
    task_id = str(claim["task_id"])
    task = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
    task["tags"] = json.loads(task.pop("tags_json"))
    task.pop("legacy_payload_json", None)
    result_policy = json.loads(task.pop("result_policy_json"))
    scopes = _json_rows(conn.execute("SELECT mode,path FROM ownership_scopes WHERE task_id=? ORDER BY mode,path", (task_id,)))
    siblings = _json_rows(
        conn.execute(
            "SELECT c.task_id,c.session_id,s.label,t.title FROM claims c JOIN sessions s ON s.id=c.session_id JOIN tasks t ON t.id=c.task_id WHERE c.state='active' AND c.task_id<>? ORDER BY c.task_id",
            (task_id,),
        )
    )
    dependencies = _json_rows(conn.execute("SELECT * FROM task_dependencies WHERE task_id=? ORDER BY id", (task_id,)))
    _, dependency_explanation = evaluate_dependencies(conn, task_id)
    checkpoints = _json_rows(conn.execute("SELECT id,title,state,reached_at FROM checkpoints WHERE task_id=? ORDER BY id", (task_id,)))
    gates = _json_rows(conn.execute("SELECT id,type,required,status,valid FROM gates WHERE task_id=? ORDER BY id", (task_id,)))
    resources = _json_rows(conn.execute("SELECT phase,selector,amount,mode,required FROM resource_requests WHERE task_id=? ORDER BY phase,selector", (task_id,)))
    invariants = _json_rows(
        conn.execute(
            "SELECT i.id,i.rule,i.severity,i.enforcement FROM task_invariants ti JOIN invariants i ON i.id=ti.invariant_id WHERE ti.task_id=? ORDER BY i.id",
            (task_id,),
        )
    )
    consumed = _json_rows(
        conn.execute(
            "SELECT i.id,i.state,i.version,i.content_hash,ic.required_state,ic.required_version FROM interface_consumers ic JOIN interfaces i ON i.id=ic.interface_id WHERE ic.task_id=? ORDER BY i.id",
            (task_id,),
        )
    )
    forbidden = sorted({row["path"] for row in conn.execute(
        "SELECT os.path FROM ownership_scopes os JOIN claims c ON c.task_id=os.task_id WHERE c.state='active' AND os.mode='exclusive' AND c.task_id<>?",
        (task_id,),
    )})
    base_command = f"{shlex.quote(sys.executable)} -m todo_orchestrator --repo-root ."
    child_results = child_results_for_task(conn, task_id)
    for child_result in child_results:
        child_result["acceptance"]["commands"] = [
            {
                "gate_id": gate_id,
                "command": {
                    "schema_version": 1,
                    "argv": [
                        sys.executable, "-m", "todo_orchestrator", "--repo-root", ".", "gate", "run", gate_id,
                        "--claim-token", "<claim-token>", "--json",
                    ],
                    "cwd": ".",
                    "env": {},
                    "timeout_seconds": 3600.0,
                },
            }
            for gate_id in child_result["acceptance"]["pending_gates"]
        ]
    capsule: dict[str, object] = {
        "schema_version": 2,
        "project_revision": project_revision,
        "session": {**session, "session_token": session_token},
        "claim": {**claim, "claim_token": claim_token},
        "task": {
            "id": task_id,
            "kind": task["kind"],
            "title": task["title"],
            "objective": task["objective"],
            "next_action": task["next_action"],
            "parallel_policy": task["parallel_policy"],
            "priority": task["priority"],
            "result_policy": result_policy,
        },
        "scope": {
            "exclusive_paths": [item["path"] for item in scopes if item["mode"] == "exclusive"],
            "read_paths": [item["path"] for item in scopes if item["mode"] == "read"],
            "shared_locks": [row[0] for row in conn.execute("SELECT lock_name FROM task_locks WHERE task_id=? ORDER BY lock_name", (task_id,))],
            "forbidden_paths": forbidden,
        },
        "prerequisites": {"records": dependencies, "explanation": dependency_explanation, "contracts": consumed},
        "checkpoints": checkpoints,
        "gates": gates,
        "resources": {
            "claim_time": [item for item in resources if item["phase"] == "claim"],
            "on_demand": [item for item in resources if item["phase"] != "claim"],
        },
        "interlocks": invariants,
        "active_siblings": siblings,
        "commands": {
            "pulse": f"{base_command} pulse --claim-token <claim-token> --json",
            "changes": f"{base_command} changes --claim-token <claim-token> --since {project_revision} --json",
            "checkpoint": f"{base_command} checkpoint reach <checkpoint-id> --claim-token <claim-token> --json",
            "complete": f"{base_command} complete --claim-token <claim-token> --disposition implemented --json",
            "block": f"{base_command} block --claim-token <claim-token> --reason <reason> --json",
            "release": f"{base_command} release --claim-token <claim-token> --json",
            "handoff": f"{base_command} handoff --claim-token <claim-token> --json",
        },
        "delta_cursor": project_revision,
    }
    if child_results:
        capsule["child_results"] = child_results
    encoded = json.dumps(capsule, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > budget:
        capsule["prerequisites"] = {
            "records": [{"type": item["type"], "id": item.get("prerequisite_task_id") or item.get("checkpoint_id") or item.get("interface_id") or item.get("barrier_id") or item.get("decision_id")} for item in dependencies],
            "contracts": [{"id": item["id"], "state": item["state"], "version": item["version"]} for item in consumed],
            "expand": f"{base_command} context --claim-token <claim-token> --section dependencies --json",
        }
        capsule["active_siblings"] = [{"task_id": item["task_id"], "label": item["label"]} for item in siblings]
        capsule["context_compacted"] = True
    capsule["context_budget_bytes"] = budget
    capsule["context_size_bytes"] = 0
    for _ in range(3):
        final_size = len(json.dumps(capsule, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        if capsule["context_size_bytes"] == final_size:
            break
        capsule["context_size_bytes"] = final_size
    return capsule


def expand_context(conn: sqlite3.Connection, task_id: str, section: str) -> object:
    if section == "dependencies":
        return {
            "dependencies": _json_rows(conn.execute("SELECT * FROM task_dependencies WHERE task_id=? ORDER BY id", (task_id,))),
            "explanation": explain_task(conn, task_id),
        }
    if section == "interfaces":
        return {
            "consumed": _json_rows(conn.execute("SELECT i.* FROM interface_consumers ic JOIN interfaces i ON i.id=ic.interface_id WHERE ic.task_id=?", (task_id,))),
            "owned": _json_rows(conn.execute("SELECT * FROM interfaces WHERE owner_task_id=?", (task_id,))),
        }
    if section == "history":
        return _json_rows(conn.execute("SELECT * FROM events WHERE entity_id=? ORDER BY revision", (task_id,)))
    if section == "siblings":
        return _json_rows(conn.execute("SELECT c.task_id,c.session_id,s.label FROM claims c JOIN sessions s ON s.id=c.session_id WHERE c.state='active' AND c.task_id<>?", (task_id,)))
    raise ValueError(f"unknown context section {section}")
