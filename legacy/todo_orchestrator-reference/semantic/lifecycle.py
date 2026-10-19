"""Deterministic effective task lifecycle normalization."""

from __future__ import annotations

import json
import re

from ..readiness import explain_task

TERMINAL_EFFECTIVE_STATES = {"done", "failed", "canceled", "superseded"}


def _reason_code(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def normalize_task(conn, row) -> dict[str, object]:
    raw_status = str(row["status"] or "").lower()
    raw_result = str(row["result"] or "").lower() or None
    reasons: list[str] = []
    explanation = explain_task(conn, str(row["id"]))
    active_claim = conn.execute(
        "SELECT id,state,baseline_head,baseline_revision FROM claims "
        "WHERE task_id=? AND state IN ('active','orphaned') ORDER BY created_at DESC LIMIT 1",
        (row["id"],),
    ).fetchone()

    if raw_status == "superseded" or raw_result == "superseded":
        effective = "superseded"
        reasons.append("explicit_status_superseded" if raw_status == "superseded" else "explicit_result_superseded")
    elif raw_status in {"cancelled", "canceled", "rejected"}:
        effective = "canceled" if raw_status in {"cancelled", "canceled"} else "failed"
        reasons.append(f"explicit_status_{_reason_code(raw_status)}")
    elif raw_status in {"failed", "stale"}:
        effective = "failed"
        reasons.append(f"explicit_status_{_reason_code(raw_status)}")
    elif raw_status == "done":
        if raw_result in {"failed", "rejected"}:
            effective = "failed"
            reasons.append(f"terminal_result_{_reason_code(raw_result or 'failed')}")
        else:
            effective = "done"
            reasons.append("successful_terminal_result" if raw_result else "explicit_status_done")
    elif active_claim is not None and active_claim["state"] == "active":
        effective = "active"
        reasons.append("active_claim")
    elif bool(explanation.get("ready")):
        effective = "ready"
        reasons.append("authoritative_ready")
    elif raw_status in {"blocked", "attention_required"}:
        effective = "blocked"
        reasons.append("explicit_attention_required" if raw_status == "attention_required" else "explicit_status_blocked")
    elif explanation.get("execution") in {
        "blocked_dependency", "blocked_barrier", "blocked_scope", "blocked_resource", "orphaned"
    }:
        effective = "blocked"
        reasons.append(f"authoritative_{_reason_code(str(explanation['execution']))}")
    elif raw_status in {"planned", "in_progress", "review"}:
        effective = "planned"
        reasons.append("not_ready_without_unresolved_prerequisite")
    else:
        effective = "planned"
        reasons.extend(["unknown_lifecycle_combination", "conservative_nonattention_default"])

    terminal = effective in TERMINAL_EFFECTIVE_STATES
    kind = str(row["kind"])
    frontier_eligible = (
        not terminal and effective in {"ready", "active", "blocked", "planned"}
        and "unknown_lifecycle_combination" not in reasons
    )
    attention_eligible = not terminal and effective == "blocked"
    current_program_eligible = effective not in {"superseded", "canceled", "failed"}
    if effective == "superseded":
        relevance = "superseded"
    elif effective in {"canceled", "failed"}:
        relevance = "historical"
    elif attention_eligible:
        relevance = "current_attention"
    elif terminal:
        relevance = "reference"
    else:
        relevance = "current"

    return {
        "id": row["id"],
        "parent_id": row["parent_id"],
        "kind": kind,
        "title": row["title"],
        "objective": row["objective"],
        "priority": int(row["priority"]),
        "tags": json.loads(row["tags_json"] or "[]"),
        "parallel_policy": row["parallel_policy"],
        "next_action": row["next_action"],
        "attention_reason": row["attention_reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "revision": int(row["revision"]),
        "raw_status": raw_status,
        "raw_result": raw_result,
        "effective_state": effective,
        "terminal": terminal,
        "current_relevance": relevance,
        "frontier_eligible": frontier_eligible,
        "attention_eligible": attention_eligible,
        "current_program_eligible": current_program_eligible,
        "authoritative_ready": bool(explanation.get("ready")),
        "execution": explanation.get("execution"),
        "reason_codes": reasons,
        "active_claim": dict(active_claim) if active_claim is not None else None,
    }
