"""Successful terminal completion provenance and owned-checkpoint finalization."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .checkpoints import reach_checkpoint
from .evidence import gate_is_satisfied, required_gates
from .models import ExitCode, TodoError


SUCCESSFUL_DISPOSITIONS = {
    "implemented", "validated", "evaluated_not_promoted", "no_change_required",
}


def is_successful_terminal(task: sqlite3.Row | dict[str, object]) -> bool:
    return task["status"] == "done" and task["result"] in SUCCESSFUL_DISPOSITIONS


def _completion_event(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM events WHERE entity_id=? AND event_type='task.completed' "
        "ORDER BY revision DESC LIMIT 1",
        (task_id,),
    ).fetchone()


def _completion_handoff(conn: sqlite3.Connection, task_id: str, completion_revision: int) -> dict[str, object]:
    row = conn.execute(
        "SELECT payload_json FROM handoffs WHERE task_id=? AND kind='complete' AND revision<=? "
        "ORDER BY revision DESC LIMIT 1",
        (task_id, completion_revision),
    ).fetchone()
    if not row:
        return {}
    try:
        value = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def completion_identity(conn: sqlite3.Connection, task: sqlite3.Row) -> dict[str, object]:
    event = _completion_event(conn, str(task["id"]))
    revision = task["completion_revision"] or (event["revision"] if event else None)
    if revision is None:
        raise TodoError(
            "terminal_completion_provenance_missing",
            f"Task {task['id']} has no recorded successful completion revision",
            ExitCode.BLOCKED,
        )
    handoff = _completion_handoff(conn, str(task["id"]), int(revision))
    completion_head = task["completion_git_head"] or handoff.get("git_head")
    completion_commit = task["completion_commit"] or handoff.get("completion_commit") or completion_head
    return {
        "revision": int(revision),
        "git_head": completion_head,
        "commit": completion_commit,
        "handoff": handoff,
    }


def _latest_gate_evidence(
    conn: sqlite3.Connection, gate_id: str, completion_revision: int,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM evidence WHERE gate_id=? AND revision<=? ORDER BY revision DESC,created_at DESC LIMIT 1",
        (gate_id, completion_revision),
    ).fetchone()


def _validation_head(metadata: dict[str, object]) -> str | None:
    inputs = metadata.get("inputs")
    if isinstance(inputs, dict) and isinstance(inputs.get("recorded_git_head"), str):
        return str(inputs["recorded_git_head"])
    return None


def snapshot_completion_gates(
    conn: sqlite3.Connection,
    task: sqlite3.Row,
    completion_revision: int,
    completion_git_head: str | None,
    *,
    legacy_handoff: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """Freeze required gates, or reconstruct them fail-closed from a legacy handoff."""
    handoff_gates: dict[str, dict[str, object]] = {}
    if legacy_handoff:
        for item in legacy_handoff.get("gates", []):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                handoff_gates[str(item["id"])] = item
    result: list[dict[str, object]] = []
    for current in required_gates(conn, str(task["id"])):
        historical = handoff_gates.get(str(current["id"]), current)
        status = str(historical.get("status") or "pending")
        valid = bool(historical.get("valid"))
        evidence = _latest_gate_evidence(conn, str(current["id"]), completion_revision)
        metadata: dict[str, object] = {}
        if evidence:
            try:
                decoded = json.loads(evidence["metadata_json"] or "{}")
                if isinstance(decoded, dict):
                    metadata = decoded
            except json.JSONDecodeError:
                pass
        fingerprint = historical.get("input_fingerprint") or metadata.get("input_fingerprint") or current.get("input_fingerprint")
        provenance = {
            "source": "legacy_completion_handoff" if legacy_handoff else "completion_transaction",
            "claim_id": evidence["claim_id"] if evidence else None,
            "evidence_kind": evidence["kind"] if evidence else None,
            "evidence_path": evidence["path"] if evidence else None,
            "evidence_content_hash": evidence["content_hash"] if evidence else None,
            "evidence_metadata": metadata,
        }
        row = {
            "task_id": str(task["id"]), "gate_id": str(current["id"]),
            "status": status, "valid": valid, "input_fingerprint": fingerprint,
            "evidence_id": evidence["id"] if evidence else None,
            "evidence_revision": int(evidence["revision"]) if evidence else None,
            "validation_git_head": _validation_head(metadata),
            "completion_revision": completion_revision,
            "completion_git_head": completion_git_head,
            "provenance": provenance,
        }
        conn.execute(
            "INSERT OR REPLACE INTO task_completion_gates("
            "task_id,gate_id,status,valid,input_fingerprint,evidence_id,evidence_revision,"
            "validation_git_head,completion_revision,completion_git_head,provenance_json"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["task_id"], row["gate_id"], row["status"], int(row["valid"]),
                row["input_fingerprint"], row["evidence_id"], row["evidence_revision"],
                row["validation_git_head"], row["completion_revision"],
                row["completion_git_head"], json.dumps(provenance, sort_keys=True),
            ),
        )
        result.append(row)
    return result


def historical_completion_gates(conn: sqlite3.Connection, task: sqlite3.Row) -> list[dict[str, object]]:
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM task_completion_gates WHERE task_id=? ORDER BY gate_id", (task["id"],)
    )]
    if rows:
        return rows
    identity = completion_identity(conn, task)
    handoff = identity["handoff"]
    handoff_gates = {
        str(item["id"]): item for item in handoff.get("gates", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    result = []
    for gate in required_gates(conn, str(task["id"])):
        snapshot = handoff_gates.get(str(gate["id"]))
        if snapshot is None:
            result.append({**gate, "status": "provenance_missing", "valid": False})
            continue
        evidence = _latest_gate_evidence(conn, str(gate["id"]), int(identity["revision"]))
        metadata = {}
        if evidence:
            try:
                metadata = json.loads(evidence["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
        result.append({
            **gate,
            "status": str(snapshot.get("status") or "pending"),
            "valid": bool(snapshot.get("valid")),
            "input_fingerprint": metadata.get("input_fingerprint") or gate.get("input_fingerprint"),
            "evidence_id": evidence["id"] if evidence else None,
            "evidence_revision": int(evidence["revision"]) if evidence else None,
            "validation_git_head": _validation_head(metadata) if isinstance(metadata, dict) else None,
            "completion_revision": int(identity["revision"]),
            "completion_git_head": identity["git_head"],
        })
    return result


def reach_eligible_owned_checkpoints(
    conn: sqlite3.Connection, repo_root: Path, task_id: str, revision: int,
) -> dict[str, list[object]]:
    reached: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for checkpoint in conn.execute(
        "SELECT * FROM checkpoints WHERE task_id=? AND state='pending' ORDER BY id", (task_id,)
    ):
        gates = conn.execute(
            "SELECT g.* FROM checkpoint_gates cg JOIN gates g ON g.id=cg.gate_id "
            "WHERE cg.checkpoint_id=? AND g.required=1 ORDER BY g.id",
            (checkpoint["id"],),
        ).fetchall()
        failed = [str(gate["id"]) for gate in gates if not gate_is_satisfied(gate)]
        if failed:
            skipped.append({"checkpoint_id": checkpoint["id"], "unsatisfied_gate_ids": failed})
            continue
        reached.append(reach_checkpoint(conn, repo_root, str(checkpoint["id"]), revision))
    return {"reached": reached, "skipped": skipped}


def terminal_finalization_report(
    conn: sqlite3.Connection, task_id: str, checkpoint_id: str | None = None,
) -> dict[str, object]:
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        raise TodoError("task_not_found", f"Unknown task {task_id}")
    if not is_successful_terminal(task):
        raise TodoError(
            "terminal_checkpoint_owner_not_successful",
            f"Task {task_id} is not a successfully completed terminal owner",
            ExitCode.BLOCKED,
            {"status": task["status"], "result": task["result"]},
        )
    if conn.execute("SELECT 1 FROM claims WHERE task_id=? AND state='active'", (task_id,)).fetchone():
        raise TodoError("terminal_checkpoint_live_claim_present", "Terminal checkpoint recovery requires no live owner claim", ExitCode.BLOCKED)
    identity = completion_identity(conn, task)
    gates = historical_completion_gates(conn, task)
    historical = {str(item["gate_id"] if "gate_id" in item else item["id"]): item for item in gates}
    failed_owner_gates = sorted(
        gate_id for gate_id, gate in historical.items() if not gate_is_satisfied(gate)
    )
    if checkpoint_id:
        checkpoint = conn.execute("SELECT * FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
        if not checkpoint:
            raise TodoError("checkpoint_not_found", f"Unknown checkpoint {checkpoint_id}")
        if checkpoint["task_id"] != task_id:
            raise TodoError("terminal_checkpoint_owner_mismatch", "Checkpoint belongs to another task", ExitCode.INVALID_TOKEN)
        candidates = [checkpoint]
    else:
        candidates = conn.execute("SELECT * FROM checkpoints WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
    eligible: list[str] = []
    already_reached: list[str] = []
    revoked: list[str] = []
    rejected: list[dict[str, object]] = []
    for checkpoint in candidates:
        if checkpoint["state"] == "reached":
            already_reached.append(str(checkpoint["id"]))
            continue
        if checkpoint["state"] == "revoked":
            revoked.append(str(checkpoint["id"]))
            continue
        required = conn.execute(
            "SELECT g.id FROM checkpoint_gates cg JOIN gates g ON g.id=cg.gate_id "
            "WHERE cg.checkpoint_id=? AND g.required=1 ORDER BY g.id", (checkpoint["id"],)
        ).fetchall()
        failed = [
            str(row["id"]) for row in required
            if str(row["id"]) not in historical or not gate_is_satisfied(historical[str(row["id"])])
        ]
        failed = sorted(set(failed_owner_gates + failed))
        if failed:
            rejected.append({"checkpoint_id": checkpoint["id"], "unsatisfied_gate_ids": failed})
        else:
            eligible.append(str(checkpoint["id"]))
    if checkpoint_id and revoked:
        raise TodoError("terminal_checkpoint_revoked", "Revoked checkpoints cannot be recovered", ExitCode.BLOCKED, {"checkpoints": revoked})
    if checkpoint_id and rejected:
        raise TodoError(
            "terminal_checkpoint_prerequisites_unsatisfied",
            "Checkpoint prerequisites were not satisfied at owner completion",
            ExitCode.GATE_FAILURE,
            {"checkpoints": rejected, "completion_revision": identity["revision"]},
        )
    return {
        "task_id": task_id, "checkpoint_id": checkpoint_id,
        "completion_revision": identity["revision"],
        "completion_git_head": identity["git_head"],
        "eligible": eligible, "already_reached": already_reached,
        "revoked": revoked, "rejected": rejected,
    }


def recover_terminal_checkpoints(
    conn: sqlite3.Connection, repo_root: Path, task_id: str,
    checkpoint_id: str | None, revision: int,
) -> dict[str, object]:
    report = terminal_finalization_report(conn, task_id, checkpoint_id)
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    identity = completion_identity(conn, task)
    gates = snapshot_completion_gates(
        conn, task, int(identity["revision"]), identity["git_head"],
        legacy_handoff=identity["handoff"],
    )
    historical = {str(item["gate_id"]): item for item in gates}
    for gate_id, item in historical.items():
        if gate_is_satisfied(item):
            conn.execute(
                "UPDATE gates SET status=?,valid=1,input_fingerprint=COALESCE(?,input_fingerprint),revision=? WHERE id=?",
                (item["status"], item["input_fingerprint"], revision, gate_id),
            )
    reached = []
    for target in report["eligible"]:
        reached.append(reach_checkpoint(conn, repo_root, str(target), revision))
    barriers = []
    for item in reached:
        barriers.extend(item.get("barrier_changes", []))
    return {
        **report, "reached": reached, "barrier_changes": barriers,
        "historical_gates": [
            {"id": item["gate_id"], "status": item["status"], "valid": bool(item["valid"]),
             "validation_git_head": item["validation_git_head"]}
            for item in gates
        ],
    }
