"""Reconcile operational state with Git, artifacts, interfaces, gates, and projections."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .evidence import gate_input_fingerprint
from .completion import historical_completion_gates, is_successful_terminal
from .git_state import dirty_paths, git_head, path_contains, scope_manifest
from .interfaces import interface_hash
from .models import TodoError


def audit_state(conn: sqlite3.Connection, repo_root: Path, snapshot_file: Path) -> dict[str, object]:
    revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
    discrepancies: list[dict[str, object]] = []
    projection = conn.execute("SELECT * FROM projection_status WHERE name='all'").fetchone()
    if not projection or int(projection["revision"]) != revision or projection["error"]:
        discrepancies.append({"code": "projection_stale", "expected_revision": revision, "actual_revision": projection["revision"] if projection else None, "error": projection["error"] if projection else None})
    if snapshot_file.exists():
        try:
            snapshot_revision = int(json.loads(snapshot_file.read_text(encoding="utf-8"))["project_revision"])
            if snapshot_revision != revision:
                discrepancies.append({"code": "snapshot_revision_mismatch", "expected_revision": revision, "actual_revision": snapshot_revision})
        except Exception as exc:
            discrepancies.append({"code": "snapshot_invalid", "error": str(exc)})
    else:
        discrepancies.append({"code": "snapshot_missing"})
    for interface in conn.execute(
        "SELECT i.*,t.status AS owner_status,t.result AS owner_result,"
        "EXISTS(SELECT 1 FROM interface_consumers c JOIN tasks ct ON ct.id=c.task_id "
        "WHERE c.interface_id=i.id AND ct.status NOT IN ('done','superseded','cancelled')) AS has_current_consumer,"
        "EXISTS(SELECT 1 FROM task_dependencies d JOIN tasks dt ON dt.id=d.task_id "
        "WHERE d.interface_id=i.id AND dt.status NOT IN ('done','superseded','cancelled')) AS has_current_dependency "
        "FROM interfaces i JOIN tasks t ON t.id=i.owner_task_id WHERE i.state='frozen'"
    ):
        if (
            (
                is_successful_terminal(
                    {"status": interface["owner_status"], "result": interface["owner_result"]}
                )
                or interface["owner_status"] in {"superseded", "cancelled"}
            )
            and not interface["has_current_consumer"]
            and not interface["has_current_dependency"]
        ):
            continue
        try:
            digest, _ = interface_hash(repo_root, json.loads(interface["contract_paths_json"]))
            if digest != interface["content_hash"]:
                discrepancies.append({"code": "interface_hash_mismatch", "interface_id": interface["id"], "recorded": interface["content_hash"], "current": digest})
        except Exception as exc:
            discrepancies.append({"code": "interface_inspection_failed", "interface_id": interface["id"], "error": str(exc)})
    for gate in conn.execute(
        "SELECT g.*,t.status AS task_status,t.result AS task_result FROM gates g "
        "LEFT JOIN tasks t ON t.id=g.task_id WHERE g.valid=1"
    ):
        if gate["task_id"] and is_successful_terminal(
            {"status": gate["task_status"], "result": gate["task_result"]}
        ):
            continue
        config = json.loads(gate["config_json"])
        current, _ = gate_input_fingerprint(conn, repo_root, config, gate_type=str(gate["type"]))
        if current != gate["input_fingerprint"]:
            discrepancies.append({"code": "gate_inputs_changed", "gate_id": gate["id"], "recorded": gate["input_fingerprint"], "current": current})
    for task in conn.execute("SELECT * FROM tasks WHERE status='done' AND kind<>'epic'"):
        if is_successful_terminal(task):
            task_gates = conn.execute(
                "SELECT id,status,valid FROM gates WHERE task_id=? AND required=1 ORDER BY id",
                (task["id"],),
            ).fetchall()
            if not task_gates:
                invalid_required = []
            else:
                try:
                    historical = historical_completion_gates(conn, task)
                    invalid_required = [
                        {"id": row.get("gate_id") or row.get("id"), "status": row["status"], "valid": row["valid"]}
                        for row in historical
                        if not row["valid"] or row["status"] not in {"passed", "evaluated_not_promoted"}
                    ]
                except TodoError:
                    # Pre-v9 terminal tasks may not have a completion event or
                    # handoff snapshot. Preserve the legacy audit result here;
                    # tokenless recovery itself remains fail-closed.
                    invalid_required = [
                        dict(row) for row in task_gates
                        if not row["valid"] or row["status"] not in {"passed", "evaluated_not_promoted"}
                    ]
        else:
            invalid_required = [dict(row) for row in conn.execute("SELECT id,status,valid FROM gates WHERE task_id=? AND required=1 AND (valid=0 OR status NOT IN ('passed','evaluated_not_promoted'))", (task["id"],))]
        if invalid_required:
            discrepancies.append({"code": "completed_required_gate_invalid", "task_id": task["id"], "gates": invalid_required})
        for artifact in conn.execute("SELECT * FROM task_artifacts WHERE task_id=?", (task["id"],)):
            if not (repo_root / artifact["path"]).exists():
                discrepancies.append({"code": "completed_artifact_missing", "task_id": task["id"], "path": artifact["path"]})
        if not conn.execute("SELECT 1 FROM gates WHERE task_id=?", (task["id"],)).fetchone():
            discrepancies.append({"code": "code_inspection_required", "task_id": task["id"], "reason": "completed task has no structured gates"})
    current_dirty = dirty_paths(repo_root)
    active_claims = []
    for claim in conn.execute("SELECT * FROM claims WHERE state IN ('active','orphaned')"):
        scopes = [row[0] for row in conn.execute("SELECT path FROM ownership_scopes WHERE task_id=? AND mode='exclusive'", (claim["task_id"],))]
        baseline = json.loads(claim["baseline_manifest_json"] or "{}")
        current = scope_manifest(repo_root, scopes)
        owned_dirty = [path for path in current_dirty if any(path_contains(scope, path) for scope in scopes)]
        active_claims.append({"task_id": claim["task_id"], "claim_id": claim["id"], "state": claim["state"], "baseline_head": claim["baseline_head"], "current_head": git_head(repo_root), "owned_dirty_paths": owned_dirty, "changed_since_baseline": baseline.get("fingerprint") != current.get("fingerprint")})
    owned_by_active = {path for item in active_claims for path in item["owned_dirty_paths"]}
    blocking_discrepancies = [
        item for item in discrepancies if item["code"] != "code_inspection_required"
    ]
    return {
        "project_revision": revision,
        "discrepancies": discrepancies,
        "active_claims": active_claims,
        "pre_existing_or_unowned_dirty_paths": sorted(set(current_dirty) - owned_by_active),
        "clean": not blocking_discrepancies,
    }


def reconcile_state(conn: sqlite3.Connection, repo_root: Path, snapshot_file: Path, revision: int) -> dict[str, object]:
    report = audit_state(conn, repo_root, snapshot_file)
    invalidated: list[str] = []
    for item in report["discrepancies"]:
        if item["code"] == "gate_inputs_changed":
            conn.execute("UPDATE gates SET valid=0,status='invalidated',revision=? WHERE id=?", (revision, item["gate_id"]))
            invalidated.append(item["gate_id"])
        elif item["code"] == "interface_hash_mismatch":
            conn.execute("UPDATE interfaces SET state='revised',revision=? WHERE id=?", (revision, item["interface_id"]))
            for row in conn.execute("SELECT task_id FROM interface_consumers WHERE interface_id=?", (item["interface_id"],)):
                if conn.execute("SELECT 1 FROM claims WHERE task_id=? AND state='active'", (row["task_id"],)).fetchone():
                    conn.execute("UPDATE tasks SET status='attention_required',attention_reason=? WHERE id=?", (f"interface {item['interface_id']} hash changed", row["task_id"]))
    from .graph import reevaluate_barriers

    barrier_changes = reevaluate_barriers(conn, revision)
    return {"invalidated_gates": invalidated, "discrepancies": report["discrepancies"], "barrier_changes": barrier_changes}
