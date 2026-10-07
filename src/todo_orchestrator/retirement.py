"""Small, fail-closed replacement of a quiescent workflow run.

This is deliberately a kernel operation: callers submit the exact records
reviewed for retirement, and it neither discovers a frontier nor repairs
claims.  It exists so a root-controlled plan change is one transaction rather
than a collection of ad-hoc database edits.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import utc_now
from .authority import logical_authority_fingerprint
from .models import ExitCode, TodoError
from .runtime.source import capture_source_identity


TERMINAL = frozenset({"done", "superseded", "cancelled", "stale"})
TERMINAL_LANE_TASK_STATES = frozenset({"completed", "cancelled", "skipped"})


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _task_digest(row: sqlite3.Row) -> str:
    # Match Project Control's exported task-row contract, rather than SQLite
    # storage-only columns whose evolution should not invalidate a review.
    fields = ("id", "parent_id", "kind", "title", "objective", "priority", "parallel_policy",
              "next_action", "notes", "status", "result", "revision", "completion_revision",
              "completion_commit")
    return _digest({field: row[field] for field in fields if field in row.keys()})


def _fingerprint(conn: sqlite3.Connection) -> str:
    return logical_authority_fingerprint(conn)


def _require_text(request: Mapping[str, Any], name: str) -> str:
    value = request.get(name)
    if not isinstance(value, str) or not value.strip():
        raise TodoError("retirement_request_invalid", f"{name} is required")
    return value.strip()


def _normalise(request: Mapping[str, Any]) -> dict[str, Any]:
    required = {"source_run_id", "successor_run_id", "expected_project_uuid", "expected_revision",
                "expected_fingerprint", "expected_tasks", "dispositions", "reason"}
    optional = {"preserved_work_handoffs"}
    unknown = set(request) - required - optional
    missing = required - set(request)
    if unknown or missing:
        raise TodoError("retirement_request_invalid", "retirement request fields do not match the canonical contract",
                        details={"unknown": sorted(unknown), "missing": sorted(missing)})
    result = {name: _require_text(request, name) for name in
              ("source_run_id", "successor_run_id", "expected_project_uuid", "expected_fingerprint", "reason")}
    if result["source_run_id"] == result["successor_run_id"]:
        raise TodoError("retirement_request_invalid", "source and successor runs must differ")
    revision = request["expected_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise TodoError("retirement_request_invalid", "expected_revision must be a non-negative integer")
    result["expected_revision"] = revision
    tasks = request["expected_tasks"]
    dispositions = request["dispositions"]
    if not isinstance(tasks, Mapping) or not tasks or not isinstance(dispositions, Mapping) or set(tasks) != set(dispositions):
        raise TodoError("retirement_request_invalid", "expected_tasks and dispositions must have the same non-empty IDs")
    result["expected_tasks"] = {str(key): dict(value) for key, value in tasks.items() if isinstance(key, str) and isinstance(value, Mapping)}
    if set(result["expected_tasks"]) != set(tasks):
        raise TodoError("retirement_request_invalid", "every expected task must have an object expectation")
    result["dispositions"] = {str(key): str(value) for key, value in dispositions.items()}
    if any(value != "superseded" for value in result["dispositions"].values()):
        raise TodoError("retirement_disposition_invalid", "batch retirement only permits the superseded disposition")
    for task_id, expected in result["expected_tasks"].items():
        if set(expected) - {"status", "version", "revision", "row_digest"} or "status" not in expected:
            raise TodoError("retirement_request_invalid", f"invalid expected task record: {task_id}")
        if str(expected["status"]) in TERMINAL:
            raise TodoError("retirement_terminal_task", f"terminal task cannot be retired: {task_id}")
    handoffs = request.get("preserved_work_handoffs", [])
    if not isinstance(handoffs, list):
        raise TodoError("retirement_request_invalid", "preserved_work_handoffs must be a list")
    required_handoff_fields = {
        "source_workspace_id", "source_lane_id", "successor_lane_id", "successor_task_id",
        "repository_identity", "expected_base_commit", "expected_head", "expected_content_fingerprint",
        "adopt_dirty",
    }
    normalised_handoffs: list[dict[str, Any]] = []
    source_workspace_ids: set[str] = set()
    successor_lanes: set[str] = set()
    for handoff in handoffs:
        if not isinstance(handoff, Mapping) or set(handoff) != required_handoff_fields:
            raise TodoError("retirement_request_invalid", "preserved-work handoff fields do not match the canonical contract")
        if not isinstance(handoff["adopt_dirty"], bool):
            raise TodoError("retirement_request_invalid", "preserved-work adopt_dirty must be boolean")
        record = {name: _require_text(handoff, name) for name in required_handoff_fields - {"adopt_dirty"}}
        record["adopt_dirty"] = handoff["adopt_dirty"]
        if record["source_workspace_id"] in source_workspace_ids or record["successor_lane_id"] in successor_lanes:
            raise TodoError("retirement_request_invalid", "preserved-work handoffs must use unique source workspaces and successor lanes")
        source_workspace_ids.add(record["source_workspace_id"])
        successor_lanes.add(record["successor_lane_id"])
        normalised_handoffs.append(record)
    # Keep the legacy normalized payload byte-for-byte equivalent when a
    # caller omits this later optional feature, so historical receipt replay
    # continues to find its original request hash.
    if "preserved_work_handoffs" in request:
        result["preserved_work_handoffs"] = normalised_handoffs
    return result


def retirement_request_hash(request: Mapping[str, Any]) -> str:
    """Validate and identify an exact retry without exposing mutable state."""
    return _digest(_normalise(request))


def prior_retirement_receipt(conn: sqlite3.Connection, request_hash: str) -> dict[str, Any] | None:
    for row in conn.execute("SELECT payload_json FROM events WHERE event_type='workflow.run.retired' ORDER BY revision DESC"):
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("request_hash") == request_hash:
            return {**dict(payload.get("receipt") or {}), "status": "already_retired", "changed": False}
    return None


def _validate_preserved_work_handoffs(
    conn: sqlite3.Connection,
    *,
    source_run_id: str,
    successor_run_id: str,
    selected_task_ids: set[str],
    handoffs: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate only existing ledger facts; source bytes stay owner-controlled.

    The issuing/host path measures material source content and binds that
    fingerprint into this exact request.  The database transaction verifies
    the ledger facts that make a cross-run reference safe, then records those
    measured expectations without altering the retained worktree.
    """
    accepted: list[dict[str, Any]] = []
    for handoff in handoffs:
        source = conn.execute(
            "SELECT * FROM workflow_workspaces WHERE id=? AND run_id=? AND lane_id=?",
            (handoff["source_workspace_id"], source_run_id, handoff["source_lane_id"]),
        ).fetchone()
        if source is None:
            raise TodoError("retirement_preserved_workspace_missing", "preserved source workspace does not match the source run/lane")
        if (source["state"] != "quarantined" or bool(source["cleanup_eligible"]) or
                source["mode"] == "read_shared" or not source["worktree_path"]):
            raise TodoError("retirement_preserved_workspace_ineligible", "preserved source workspace must be quarantined, writable, and cleanup-ineligible")
        if (source["repository_identity"] != handoff["repository_identity"] or
                source["base_commit"] != handoff["expected_base_commit"]):
            raise TodoError("retirement_preserved_workspace_stale", "preserved source workspace identity or base changed")
        selected_placeholders = ",".join("?" for _ in selected_task_ids)
        source_task = conn.execute(
            f"SELECT 1 FROM workflow_lane_tasks WHERE lane_id=? AND task_id IN ({selected_placeholders})",
            (handoff["source_lane_id"], *sorted(selected_task_ids)),
        ).fetchone()
        if source_task is None:
            raise TodoError("retirement_preserved_workspace_task_mismatch", "preserved source lane owns no selected superseded task")
        successor_lane = conn.execute(
            "SELECT l.id,l.workspace_mode FROM workflow_lanes l JOIN workflow_lane_tasks lt ON lt.lane_id=l.id "
            "WHERE l.id=? AND l.run_id=? AND lt.task_id=? "
            "AND lt.state NOT IN ('completed','cancelled','skipped')",
            (handoff["successor_lane_id"], successor_run_id, handoff["successor_task_id"]),
        ).fetchone()
        if successor_lane is None:
            raise TodoError("retirement_preserved_successor_mismatch", "successor lane does not own the exact unfinished handoff task")
        if successor_lane["workspace_mode"] != source["mode"]:
            raise TodoError("retirement_preserved_successor_mismatch", "successor lane workspace contract differs from the preserved source")
        integration_task_id = str(handoff["successor_task_id"])
        if source["mode"] == "isolated_merge":
            integration_candidates = conn.execute(
                "SELECT l.id AS lane_id,lt.task_id FROM workflow_lanes l "
                "JOIN workflow_lane_tasks lt ON lt.lane_id=l.id "
                "WHERE l.run_id=? AND l.role IN ('integrator','validator') "
                "AND lt.state NOT IN ('completed','cancelled','skipped') "
                "ORDER BY l.id,lt.position",
                (successor_run_id,),
            ).fetchall()
            if len(integration_candidates) != 1:
                raise TodoError(
                    "retirement_preserved_integration_ambiguous",
                    "preserved isolated work requires exactly one unfinished successor integration destination",
                    ExitCode.BLOCKED,
                    {"successor_run_id": successor_run_id,
                     "candidates": [{"lane_id": str(row["lane_id"]), "task_id": str(row["task_id"])} for row in integration_candidates]},
                )
            integration_task_id = str(integration_candidates[0]["task_id"])
        existing = conn.execute(
            "SELECT id FROM workflow_workspaces WHERE run_id=? AND lane_id=?",
            (successor_run_id, handoff["successor_lane_id"]),
        ).fetchone()
        if existing is not None:
            raise TodoError("retirement_preserved_successor_workspace_exists", "successor lane already has a workspace")
        active = conn.execute(
            "SELECT 1 FROM workflow_dispatches WHERE workspace_id=? AND state IN ('active','revoking') "
            "UNION SELECT 1 FROM workflow_patch_artifacts WHERE workspace_id=? "
            "UNION SELECT 1 FROM workflow_integration_queue q JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id "
            "WHERE a.workspace_id=?",
            (source["id"], source["id"], source["id"]),
        ).fetchone()
        if active is not None:
            raise TodoError("retirement_preserved_workspace_busy", "preserved source workspace has active ownership or artifact consumption")
        try:
            identity = capture_source_identity(Path(str(source["worktree_path"])))
        except Exception as exc:
            raise TodoError("retirement_preserved_workspace_unavailable", "preserved source Git identity could not be verified") from exc
        if Path(str(identity["repo_root"])).resolve() != Path(str(source["worktree_path"])).resolve():
            raise TodoError("retirement_preserved_workspace_stale", "preserved source workspace no longer resolves to its recorded worktree")
        if (identity["git_head"] != handoff["expected_head"] or
                identity["fingerprint"] != handoff["expected_content_fingerprint"]):
            raise TodoError("retirement_preserved_workspace_stale", "preserved source HEAD or material content changed")
        if identity["dirty_paths"] and not handoff["adopt_dirty"]:
            raise TodoError("retirement_preserved_adoption_required", "dirty preserved source requires explicit adopt_dirty approval")
        accepted.append({**dict(handoff), "integration_task_id": integration_task_id,
                         "source_worktree_path": str(source["worktree_path"]),
                         "source_workspace_mode": str(source["mode"]), "source_branch": source["branch"]})
    return accepted


def retire_run_batch_in_transaction(conn: sqlite3.Connection, revision: int, *, project: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    """Retire only an exact, quiescent run.  Caller owns the DB transaction."""
    value = _normalise(request)
    request_hash = _digest(value)
    prior = prior_retirement_receipt(conn, request_hash)
    if prior is not None:
        return prior
    if value["expected_project_uuid"] != str(project.get("project_uuid")):
        raise TodoError("retirement_project_mismatch", "retirement project UUID differs from authority", ExitCode.CONSISTENCY_ERROR)
    current_revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
    if current_revision != value["expected_revision"] or _fingerprint(conn) != value["expected_fingerprint"]:
        raise TodoError("retirement_authority_stale", "authority changed since retirement review", ExitCode.CONTENTION)
    source = conn.execute("SELECT * FROM workflow_runs WHERE id=?", (value["source_run_id"],)).fetchone()
    successor = conn.execute("SELECT * FROM workflow_runs WHERE id=?", (value["successor_run_id"],)).fetchone()
    if source is None or successor is None:
        raise TodoError("retirement_run_missing", "source or successor run is absent")
    if source["status"] not in {"active", "attention_required"}:
        raise TodoError("retirement_source_inactive", "source run is not eligible for retirement")
    if successor["status"] != "active":
        raise TodoError("retirement_successor_ineligible", "successor run is not active")
    task_ids = sorted(value["expected_tasks"])
    placeholders = ",".join("?" for _ in task_ids)
    rows = {str(row["id"]): row for row in conn.execute(f"SELECT * FROM tasks WHERE id IN ({placeholders})", task_ids)}
    if set(rows) != set(task_ids):
        raise TodoError("retirement_task_missing", "one or more exact retirement tasks are absent")
    source_memberships = conn.execute(
        "SELECT lt.lane_id,lt.position,lt.task_id,lt.state,t.status AS task_status "
        "FROM workflow_lane_tasks lt JOIN workflow_lanes l ON l.id=lt.lane_id "
        "JOIN tasks t ON t.id=lt.task_id WHERE l.run_id=? ORDER BY lt.lane_id,lt.position",
        (source["id"],),
    ).fetchall()
    run_tasks = {str(row["task_id"]) for row in source_memberships}
    if not set(task_ids) <= run_tasks:
        raise TodoError("retirement_foreign_task", "retirement request includes a task outside the source run")
    # Cancelling a run is a whole-run transition.  Every member whose queue
    # work or task outcome remains unfinished must be reviewed and selected;
    # completed historical membership remains intact and is deliberately not
    # rewritten as superseded.
    unfinished_source = {
        str(row["task_id"])
        for row in source_memberships
        if str(row["state"]) not in TERMINAL_LANE_TASK_STATES or str(row["task_status"]) not in TERMINAL
    }
    selected = set(task_ids)
    if selected != unfinished_source:
        raise TodoError(
            "retirement_source_membership_incomplete",
            "retirement must select every unfinished source-run task before cancelling the run",
            ExitCode.BLOCKED,
            {"omitted_task_ids": sorted(unfinished_source - selected), "unexpected_task_ids": sorted(selected - unfinished_source)},
        )
    foreign_memberships = conn.execute(
        f"SELECT l.run_id,lt.lane_id,lt.position,lt.task_id,lt.state,r.status AS run_status "
        f"FROM workflow_lane_tasks lt JOIN workflow_lanes l ON l.id=lt.lane_id "
        f"JOIN workflow_runs r ON r.id=l.run_id WHERE lt.task_id IN ({placeholders}) "
        "AND l.run_id<>? AND r.status IN ('active','attention_required') "
        "AND lt.state NOT IN ('completed','cancelled','skipped') ORDER BY l.run_id,lt.lane_id,lt.position",
        (*task_ids, source["id"]),
    ).fetchall()
    if foreign_memberships:
        raise TodoError(
            "retirement_foreign_membership",
            "selected tasks still have active foreign run memberships",
            ExitCode.BLOCKED,
            [dict(row) for row in foreign_memberships],
        )
    successor_memberships = conn.execute(
        "SELECT lt.lane_id,lt.position,lt.task_id,lt.state FROM workflow_lane_tasks lt "
        "JOIN workflow_lanes l ON l.id=lt.lane_id WHERE l.run_id=? "
        "AND lt.state NOT IN ('completed','cancelled','skipped') ORDER BY lt.lane_id,lt.position",
        (successor["id"],),
    ).fetchall()
    if not successor_memberships:
        raise TodoError(
            "retirement_successor_ineligible",
            "successor run has no unfinished lane membership to become the intended execution",
            ExitCode.BLOCKED,
            {"successor_run_id": str(successor["id"])},
        )
    accepted_handoffs = _validate_preserved_work_handoffs(
        conn,
        source_run_id=str(source["id"]),
        successor_run_id=str(successor["id"]),
        selected_task_ids=selected,
        handoffs=value.get("preserved_work_handoffs", []),
    )
    blockers: list[dict[str, str]] = []
    for table, column, states in (("claims", "task_id", "state='active'"), ("workflow_dispatches d JOIN claims c ON c.id=d.claim_id", "c.task_id", "d.state='active'"),
                                  ("child_executions", "task_id", "state IN ('authorized','running','recovery_required','ready_for_acceptance','succeeded')"),
                                  ("resource_leases r JOIN claims c ON c.id=r.claim_id", "c.task_id", "r.state='active'"),
                                  ("gates", "task_id", "status='running'")):
        try:
            found = conn.execute(f"SELECT {column} AS task_id FROM {table} WHERE {column} IN ({placeholders}) AND {states}", task_ids).fetchall()
        except sqlite3.OperationalError:
            continue
        blockers.extend({"kind": table.split()[0], "task_id": str(item["task_id"])} for item in found)
    # A pending/running queue entry may be retired; an active entry is a live lane.
    active = conn.execute(
        f"SELECT lt.task_id FROM workflow_lane_tasks lt JOIN workflow_lanes l ON l.id=lt.lane_id "
        f"WHERE l.run_id=? AND lt.task_id IN ({placeholders}) AND lt.state='active'",
        (source["id"], *task_ids),
    ).fetchall()
    blockers.extend({"kind": "lane_task", "task_id": str(item["task_id"])} for item in active)
    if blockers:
        raise TodoError("retirement_not_quiescent", "live claims, dispatches, or children block retirement", ExitCode.BLOCKED, blockers)
    consumers = conn.execute(f"SELECT task_id,prerequisite_task_id FROM task_dependencies WHERE prerequisite_task_id IN ({placeholders}) AND task_id NOT IN ({placeholders})", (*task_ids, *task_ids)).fetchall()
    nonterminal: list[dict[str, str]] = []
    for consumer in consumers:
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (consumer["task_id"],)).fetchone()
        if row and row["status"] not in TERMINAL:
            nonterminal.append({"kind": "task_dependency", "task_id": str(consumer["task_id"]), "prerequisite_task_id": str(consumer["prerequisite_task_id"])})
    interface_consumers = conn.execute(
        f"SELECT i.id AS interface_id,ic.task_id,i.owner_task_id FROM interfaces i "
        "JOIN interface_consumers ic ON ic.interface_id=i.id JOIN tasks t ON t.id=ic.task_id "
        f"WHERE i.owner_task_id IN ({placeholders}) AND ic.task_id NOT IN ({placeholders}) "
        "AND t.status NOT IN ('done','superseded','cancelled','stale')",
        (*task_ids, *task_ids),
    ).fetchall()
    nonterminal.extend(
        {"kind": "interface_consumer", "interface_id": str(consumer["interface_id"]),
         "task_id": str(consumer["task_id"]), "owner_task_id": str(consumer["owner_task_id"])}
        for consumer in interface_consumers
    )
    typed_consumers = conn.execute(
        f"SELECT d.task_id,d.type,d.checkpoint_id,d.interface_id,d.barrier_id FROM task_dependencies d "
        "JOIN tasks t ON t.id=d.task_id "
        "LEFT JOIN checkpoints c ON d.type='checkpoint' AND c.id=d.checkpoint_id "
        "LEFT JOIN interfaces i ON d.type='interface' AND i.id=d.interface_id "
        "LEFT JOIN barrier_requirements br ON d.type='barrier' AND br.barrier_id=d.barrier_id "
        "LEFT JOIN checkpoints bc ON br.type='checkpoint' AND bc.id=br.entity_id "
        "LEFT JOIN interfaces bi ON br.type='interface' AND bi.id=br.entity_id "
        f"WHERE d.task_id NOT IN ({placeholders}) AND t.status NOT IN ('done','superseded','cancelled','stale') AND ("
        f"(d.type='checkpoint' AND c.task_id IN ({placeholders})) OR "
        f"(d.type='interface' AND i.owner_task_id IN ({placeholders})) OR "
        f"(d.type='barrier' AND ((br.type='task' AND br.entity_id IN ({placeholders})) "
        f"OR (br.type='checkpoint' AND bc.task_id IN ({placeholders})) "
        f"OR (br.type='interface' AND bi.owner_task_id IN ({placeholders}))))"
        ")",
        (*task_ids, *task_ids, *task_ids, *task_ids, *task_ids, *task_ids),
    ).fetchall()
    nonterminal.extend(
        {"kind": f"typed_{consumer['type']}_consumer", "task_id": str(consumer["task_id"]),
         "checkpoint_id": str(consumer["checkpoint_id"]) if consumer["checkpoint_id"] else "",
         "interface_id": str(consumer["interface_id"]) if consumer["interface_id"] else "",
         "barrier_id": str(consumer["barrier_id"]) if consumer["barrier_id"] else ""}
        for consumer in typed_consumers
    )
    if nonterminal:
        raise TodoError("retirement_external_consumers", "nonterminal external consumers require an explicit new plan", ExitCode.BLOCKED, nonterminal)
    for task_id in task_ids:
        row, expected = rows[task_id], value["expected_tasks"][task_id]
        if str(row["status"]) != str(expected["status"]) or ("version" in expected and int(row["version"]) != int(expected["version"])) or ("revision" in expected and int(row["revision"]) != int(expected["revision"])) or ("row_digest" in expected and _task_digest(row) != str(expected["row_digest"])):
            raise TodoError("retirement_task_stale", f"task changed since retirement review: {task_id}", ExitCode.CONTENTION)
    now = utc_now()
    for task_id in task_ids:
        conn.execute("UPDATE tasks SET status='superseded',result='superseded',attention_reason=NULL,updated_at=?,version=version+1,revision=? WHERE id=?", (now, revision, task_id))
    conn.execute(
        f"UPDATE workflow_lane_tasks SET state='skipped',completed_at=?,revision=? "
        f"WHERE lane_id IN (SELECT id FROM workflow_lanes WHERE run_id=?) "
        f"AND task_id IN ({placeholders}) AND state='queued'",
        (now, revision, source["id"], *task_ids),
    )
    successor_workspace_ids: list[str] = []
    for handoff in accepted_handoffs:
        workspace_id = str(uuid.uuid4())
        provenance = {
            "preserved_work_handoff": {
                "source_workspace_id": handoff["source_workspace_id"],
                "source_lane_id": handoff["source_lane_id"],
                "expected_head": handoff["expected_head"],
                "expected_content_fingerprint": handoff["expected_content_fingerprint"],
                "adopt_dirty": handoff["adopt_dirty"],
                "retirement_request_hash": request_hash,
            }
        }
        conn.execute(
            "INSERT INTO workflow_workspaces("
            "id,repository_identity,run_id,lane_id,mode,base_commit,worktree_path,branch,state,"
            "integration_task_id,merge_result_json,cleanup_eligible,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (workspace_id, handoff["repository_identity"], successor["id"], handoff["successor_lane_id"],
             handoff["source_workspace_mode"], handoff["expected_base_commit"], handoff["source_worktree_path"],
             handoff["source_branch"], "active", handoff["integration_task_id"], _json(provenance), 0, now, now),
        )
        successor_workspace_ids.append(workspace_id)
    conn.execute("UPDATE workflow_runs SET status='cancelled',updated_at=?,completed_at=?,revision=? WHERE id=?", (now, now, revision, source["id"]))
    preserved_task_ids = sorted(run_tasks - selected)
    receipt = {"status": "retired", "changed": True, "source_run_id": value["source_run_id"], "successor_run_id": value["successor_run_id"], "intended_run_id": value["successor_run_id"], "changed_task_ids": task_ids, "preserved_task_ids": preserved_task_ids, "preserved_workspace_ids": successor_workspace_ids, "pre_revision": current_revision, "post_revision": revision, "reason": value["reason"]}
    conn.execute("INSERT INTO workflow_recovery_audit(id,project_uuid,run_id,lane_id,task_id,reason,proposed_plan_json,result_json,actor_identity,created_at,completed_at,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (__import__('uuid').uuid4().hex, str(project["project_uuid"]), source["id"], None, None, value["reason"], _json(value), _json(receipt), "root_retirement", now, now, revision))
    return {**receipt, "request_hash": request_hash}
