"""Compact project status, blocker-frontier, and handoff reporting."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .readiness import explain_task, ready_tasks


def current_barriers(conn: sqlite3.Connection, *, unopened_only: bool = False) -> list[dict[str, object]]:
    query = (
        "SELECT DISTINCT b.id,b.state,b.explanation FROM barriers b "
        "JOIN task_dependencies d ON d.barrier_id=b.id "
        "JOIN tasks t ON t.id=d.task_id "
        "WHERE t.status NOT IN ('done','superseded','cancelled','stale') "
    )
    if unopened_only:
        query += "AND b.state<>'open' "
    return [dict(row) for row in conn.execute(query + "ORDER BY b.id")]


def child_results_for_task(conn: sqlite3.Connection, task_id: str) -> list[dict[str, object]]:
    """Return compact terminal child results without granting them task authority."""
    rows = conn.execute(
        "SELECT id,state,objective,gates_json,candidate_gates_json,acceptance_gates_json,"
        "result_json,result_refs_json,completed_at FROM child_executions "
        "WHERE task_id=? AND state IN "
        "('succeeded','ready_for_acceptance','accepted','rejected','stale','needs_codex','failed') "
        "ORDER BY completed_at DESC,id DESC LIMIT 8",
        (task_id,),
    )
    valid_gates = {str(row["id"]) for row in conn.execute("SELECT id FROM gates WHERE task_id=? AND valid=1", (task_id,))}
    task_evidence: list[tuple[sqlite3.Row, dict[str, Any]]] = []
    for evidence in conn.execute(
        "SELECT e.* FROM evidence e JOIN gates g ON g.id=e.gate_id WHERE g.task_id=? ORDER BY e.created_at,e.id",
        (task_id,),
    ):
        try:
            task_evidence.append((evidence, json.loads(evidence["metadata_json"] or "{}")))
        except json.JSONDecodeError:
            continue
    results: list[dict[str, object]] = []
    for row in rows:
        raw_result = json.loads(row["result_json"] or "{}")
        changed_paths = [str(path) for path in raw_result.get("changed_paths", [])]
        visible_status = str(row["state"])
        if visible_status in {"succeeded", "ready_for_acceptance", "accepted"}:
            visible_status = "completed" if changed_paths else "no_change"
        candidates: list[dict[str, object]] = []
        accepted_evidence: set[str] = set()
        for evidence, metadata in task_evidence:
            if metadata.get("accepted_child_execution_id") == row["id"] and metadata.get("accepted_evidence_id"):
                accepted_evidence.add(str(metadata["accepted_evidence_id"]))
            if metadata.get("child_execution_id") != row["id"] or not metadata.get("candidate_evidence"):
                continue
            candidates.append({
                "evidence_id": evidence["id"],
                "gate_id": evidence["gate_id"],
                "status": evidence["status"],
                "accepted": False,
                "artifact": {
                    "schema_version": 1,
                    "artifact_id": evidence["id"],
                    "kind": "gate-evidence",
                    "path": evidence["path"],
                    "content_hash": None,
                    "complete": True,
                },
            })
        for candidate in candidates:
            candidate["accepted"] = candidate["evidence_id"] in accepted_evidence
        pending = sorted({
            str(candidate["gate_id"])
            for candidate in candidates
            if candidate["status"] in {"passed", "evaluated_not_promoted"}
            and not candidate["accepted"]
            and candidate["gate_id"] not in valid_gates
        })
        authorized = sorted(str(gate) for gate in json.loads(row["candidate_gates_json"] or row["gates_json"] or "[]"))
        if str(row["state"]) in {"needs_codex", "failed", "rejected", "stale"}:
            acceptance_state = "not_applicable"
        elif row["state"] == "ready_for_acceptance":
            acceptance_state = "ready"
        elif row["state"] == "accepted":
            acceptance_state = "accepted"
        elif pending:
            acceptance_state = "ready"
        elif candidates and all(candidate["accepted"] for candidate in candidates):
            acceptance_state = "accepted"
        elif candidates:
            acceptance_state = "superseded"
        else:
            acceptance_state = "no_evidence"
        item = {
            "format": "TODO-CHILD-RESULT/1",
            "schema_version": 1,
            "child_execution_id": row["id"],
            "parent_task_id": task_id,
            "status": visible_status,
            "objective": str(row["objective"])[:500],
            "summary": str(raw_result.get("summary", ""))[:500],
            "changed_paths": changed_paths[:64],
            "changed_paths_omitted": max(0, len(changed_paths) - 64),
            "authorized_gates": authorized,
            "evidence": candidates[:32],
            "evidence_omitted": max(0, len(candidates) - 32),
            "acceptance": {"state": acceptance_state, "pending_gates": pending},
            "completed_at": row["completed_at"],
        }
        references = json.loads(row["result_refs_json"] or "{}")
        if references:
            item["artifacts"] = references
        results.append(item)
    return results


def project_status(conn: sqlite3.Connection) -> dict[str, object]:
    revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
    counts = {row["status"]: row["count"] for row in conn.execute("SELECT status,COUNT(*) AS count FROM tasks GROUP BY status")}
    active = [dict(row) for row in conn.execute(
        "SELECT c.id AS claim_id,c.task_id,c.expires_at,s.label FROM claims c JOIN sessions s ON s.id=c.session_id WHERE c.state='active' ORDER BY c.task_id"
    )]
    orphaned = [dict(row) for row in conn.execute("SELECT id AS claim_id,task_id,orphan_reason FROM claims WHERE state='orphaned' ORDER BY task_id")]
    barriers = current_barriers(conn)
    return {"project_revision": revision, "task_counts": counts, "ready": ready_tasks(conn), "active_claims": active, "orphaned_claims": orphaned, "barriers": barriers}


def no_work_frontier(conn: sqlite3.Connection) -> dict[str, object]:
    explanations = []
    for row in conn.execute("SELECT id FROM tasks WHERE status NOT IN ('done','superseded','cancelled','stale') AND kind<>'epic' ORDER BY priority DESC,id LIMIT 100"):
        report = explain_task(conn, row["id"])
        if not report["ready"]:
            explanations.append(report)
    active = [dict(row) for row in conn.execute("SELECT c.task_id,s.label,c.expires_at FROM claims c JOIN sessions s ON s.id=c.session_id WHERE c.state='active' ORDER BY c.task_id")]
    unavailable = [dict(row) for row in conn.execute(
        "SELECT ri.id,ri.class_id,ri.capacity,COUNT(rl.id) AS active FROM resource_instances ri LEFT JOIN resource_leases rl ON rl.instance_id=ri.id AND rl.state='active' GROUP BY ri.id HAVING active>=ri.capacity"
    )]
    barriers = current_barriers(conn, unopened_only=True)
    orphaned = [dict(row) for row in conn.execute("SELECT task_id,orphan_reason FROM claims WHERE state='orphaned' ORDER BY task_id")]
    return {"active_work": active, "blocker_frontier": explanations, "unopened_barriers": barriers, "orphaned_claims": orphaned, "unavailable_resources": unavailable, "most_likely_next_transition": explanations[0] if explanations else None}


def git_diffstat(repo_root: Path) -> str:
    result = subprocess.run(["git", "-C", str(repo_root), "diff", "--stat"], capture_output=True, text=True, check=False)
    return result.stdout.strip()
