"""Explicit first-class-lane rendezvous and idempotent arrivals."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from typing import Any

from ..config import utc_now
from ..models import TodoError
from ..graph import reevaluate_barriers
from .foundation import COORDINATE_TASK_BUDGET_BYTES, WorkflowDatabase, canonical_json, require_bounded_payload
from .messages import _insert_message, _lane, _require_first_class


RENDEZVOUS_MODES = frozenset({"all", "quorum", "producers"})


def _rendezvous_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    participants = [
        {
            "lane_id": item["lane_id"],
            "producer": bool(item["producer"]),
            "required": bool(item["required"]),
            "role": item["role"],
        }
        for item in conn.execute(
            "SELECT p.*,l.role FROM workflow_rendezvous_participants p "
            "JOIN workflow_lanes l ON l.id=p.lane_id WHERE p.rendezvous_id=? ORDER BY p.lane_id",
            (row["id"],),
        )
    ]
    arrivals = [
        {
            "lane_id": item["lane_id"],
            "task_id": item["task_id"],
            "summary": item["summary"],
            "base_source_identity": item["base_source_identity"],
            "final_source_identity": item["final_source_identity"],
            "artifact": json.loads(item["artifact_json"]),
            "interfaces": json.loads(item["interfaces_json"]),
            "evidence": json.loads(item["evidence_json"]),
            "warnings": json.loads(item["warnings_json"]),
            "context_version": int(item["context_version"]),
            "state": item["state"],
            "arrived_at": item["arrived_at"],
            "revision": int(item["revision"]),
        }
        for item in conn.execute(
            "SELECT * FROM workflow_rendezvous_arrivals WHERE rendezvous_id=? ORDER BY lane_id",
            (row["id"],),
        )
    ]
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "barrier_id": row["barrier_id"],
        "mode": row["mode"],
        "quorum": row["quorum"],
        "join_task_id": row["join_task_id"],
        "state": row["state"],
        "required_roles": json.loads(row["required_roles_json"]),
        "participants": participants,
        "arrivals": arrivals,
        "revision": int(row["revision"]),
    }


def _condition(conn: sqlite3.Connection, rendezvous_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM workflow_rendezvous WHERE id=?", (rendezvous_id,)).fetchone()
    if not row:
        raise TodoError("workflow_rendezvous_not_found", f"Unknown rendezvous {rendezvous_id}")
    participants = conn.execute(
        "SELECT p.lane_id,p.producer,p.required,l.role FROM workflow_rendezvous_participants p "
        "JOIN workflow_lanes l ON l.id=p.lane_id WHERE p.rendezvous_id=? ORDER BY p.lane_id",
        (rendezvous_id,),
    ).fetchall()
    arrived = {
        item[0]
        for item in conn.execute(
            "SELECT lane_id FROM workflow_rendezvous_arrivals WHERE rendezvous_id=? AND state='valid'",
            (rendezvous_id,),
        )
    }
    required_roles = set(json.loads(row["required_roles_json"] or "[]"))
    arrived_roles = {item["role"] for item in participants if item["lane_id"] in arrived}
    if row["mode"] == "all":
        required = {item["lane_id"] for item in participants if item["required"]}
        satisfied = bool(required) and required <= arrived
        target = len(required)
        achieved = len(required & arrived)
    elif row["mode"] == "quorum":
        eligible = {item["lane_id"] for item in participants}
        target = int(row["quorum"] or len(eligible))
        achieved = len(eligible & arrived)
        satisfied = achieved >= target
    else:
        producers = {item["lane_id"] for item in participants if item["producer"]}
        target = len(producers)
        achieved = len(producers & arrived)
        satisfied = bool(producers) and producers <= arrived
    if required_roles:
        satisfied = satisfied and required_roles <= arrived_roles
    return {
        "satisfied": satisfied,
        "arrived": sorted(arrived),
        "achieved": achieved,
        "target": target,
        "required_roles": sorted(required_roles),
        "arrived_roles": sorted(arrived_roles),
    }


def _apply_condition(
    conn: sqlite3.Connection,
    rendezvous_id: str,
    revision: int,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM workflow_rendezvous WHERE id=?", (rendezvous_id,)).fetchone()
    condition = _condition(conn, rendezvous_id)
    new_state = "satisfied" if condition["satisfied"] else "open"
    conn.execute(
        "UPDATE workflow_rendezvous SET state=?,opened_at=CASE WHEN ?='satisfied' THEN COALESCE(opened_at,?) ELSE NULL END,revision=? WHERE id=?",
        (new_state, new_state, utc_now(), revision, rendezvous_id),
    )
    barrier_changes = reevaluate_barriers(conn, revision)
    barrier = conn.execute("SELECT state FROM barriers WHERE id=?", (row["barrier_id"],)).fetchone()
    if condition["satisfied"] and barrier and barrier["state"] == "open":
        conn.execute(
            "UPDATE tasks SET attention_reason=NULL,updated_at=?,revision=? "
            "WHERE id=? AND status NOT IN ('done','cancelled','superseded')",
            (utc_now(), revision, row["join_task_id"]),
        )
    return {
        **condition,
        "state": new_state,
        "join_task_id": row["join_task_id"],
        "join_ready": bool(condition["satisfied"] and barrier and barrier["state"] == "open"),
        "barrier_changes": barrier_changes,
    }


class RendezvousService:
    """Fan-in authority for first-class lanes only."""

    def __init__(self, db: WorkflowDatabase):
        self.db = db

    def create(
        self,
        *,
        capability_class: str,
        run_id: str,
        author_lane_id: str,
        mode: str,
        join_task_id: str,
        participants: Iterable[dict[str, Any]],
        quorum: int | None = None,
        barrier_id: str | None = None,
        required_roles: Iterable[str] = (),
        actor_session_id: str | None = None,
        rendezvous_id: str | None = None,
    ) -> dict[str, Any]:
        _require_first_class(capability_class)
        if mode not in RENDEZVOUS_MODES:
            raise TodoError("invalid_rendezvous_mode", f"Unknown rendezvous mode {mode}")
        participant_list = [dict(item) for item in participants]
        if not participant_list:
            raise TodoError("rendezvous_participants_required", "Rendezvous requires first-class lane participants")
        roles = sorted(set(str(role) for role in required_roles))
        identifier = rendezvous_id or str(uuid.uuid4())
        if not barrier_id:
            raise TodoError("rendezvous_barrier_required", "Rendezvous must extend a declarative barrier prerequisite")

        def operation(conn: sqlite3.Connection, revision: int) -> dict[str, Any]:
            author = _lane(conn, run_id, author_lane_id)
            if author["role"] != "coordinator":
                raise TodoError("rendezvous_role_forbidden", "Only a coordinator lane may create a rendezvous")
            if not conn.execute("SELECT 1 FROM tasks WHERE id=?", (join_task_id,)).fetchone():
                raise TodoError("rendezvous_join_task_not_found", f"Unknown join task {join_task_id}")
            if barrier_id and not conn.execute("SELECT 1 FROM barriers WHERE id=?", (barrier_id,)).fetchone():
                raise TodoError("rendezvous_barrier_not_found", f"Unknown bound barrier {barrier_id}")
            if barrier_id and not conn.execute(
                "SELECT 1 FROM task_dependencies WHERE task_id=? AND type='barrier' AND barrier_id=?",
                (join_task_id, barrier_id),
            ).fetchone():
                raise TodoError(
                    "rendezvous_join_contract_mismatch",
                    "A bound barrier must be the declarative prerequisite of the rendezvous join task",
                )
            normalized: list[tuple[str, int, int]] = []
            seen: set[str] = set()
            for participant in participant_list:
                lane_id = str(participant.get("lane_id", ""))
                if not lane_id or lane_id in seen:
                    raise TodoError("invalid_rendezvous_participant", "Participant lanes must be non-empty and unique")
                _lane(conn, run_id, lane_id)
                normalized.append((lane_id, int(bool(participant.get("producer", False))), int(bool(participant.get("required", True)))))
                seen.add(lane_id)
            if mode == "quorum" and (quorum is None or quorum < 1 or quorum > len(normalized)):
                raise TodoError("invalid_rendezvous_quorum", "Quorum must be within the participant count")
            if mode != "quorum" and quorum is not None:
                raise TodoError("invalid_rendezvous_quorum", "Quorum is valid only for quorum rendezvous")
            if mode == "producers" and not any(item[1] for item in normalized):
                raise TodoError("rendezvous_producer_required", "Producer rendezvous requires a designated producer")
            lane_roles = {
                row[0]
                for row in conn.execute(
                    f"SELECT DISTINCT role FROM workflow_lanes WHERE run_id=? AND id IN ({','.join('?' for _ in normalized)})",
                    (run_id, *(item[0] for item in normalized)),
                )
            }
            if not set(roles) <= lane_roles:
                raise TodoError("rendezvous_required_role_missing", "A required producer role has no participant")
            now = utc_now()
            conn.execute(
                "INSERT INTO workflow_rendezvous(id,run_id,barrier_id,mode,quorum,join_task_id,state,"
                "required_roles_json,created_at,revision) VALUES(?,?,?,?,?,?, 'open',?,?,?)",
                (identifier, run_id, barrier_id, mode, quorum, join_task_id, canonical_json(roles), now, revision),
            )
            conn.executemany(
                "INSERT INTO workflow_rendezvous_participants(rendezvous_id,lane_id,producer,required) VALUES(?,?,?,?)",
                [(identifier, *item) for item in normalized],
            )
            conn.execute(
                "INSERT OR IGNORE INTO barrier_requirements(barrier_id,type,entity_id,required_state,dispositions_json) "
                "VALUES(?,'rendezvous',?,'satisfied','[]')",
                (barrier_id, identifier),
            )
            reevaluate_barriers(conn, revision)
            return _rendezvous_dict(conn, conn.execute("SELECT * FROM workflow_rendezvous WHERE id=?", (identifier,)).fetchone())

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_rendezvous",
            entity_id=identifier,
            event_type="workflow_rendezvous.created",
            payload={"run_id": run_id, "mode": mode, "join_task_id": join_task_id},
            operation=operation,
        )
        return {"rendezvous": result, "project_revision": revision}

    def _arrive_in_transaction(
        self,
        conn: sqlite3.Connection,
        revision: int,
        *,
        capability_class: str,
        run_id: str,
        lane_id: str,
        rendezvous_id: str,
        task_id: str,
        summary: str,
        base_source_identity: str | None,
        final_source_identity: str | None,
        artifact: dict[str, Any],
        interfaces: dict[str, Any],
        evidence: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        context_version: int,
    ) -> dict[str, Any]:
        _require_first_class(capability_class)
        _lane(conn, run_id, lane_id)
        rendezvous = conn.execute(
            "SELECT * FROM workflow_rendezvous WHERE id=? AND run_id=?", (rendezvous_id, run_id)
        ).fetchone()
        if not rendezvous:
            raise TodoError("workflow_rendezvous_not_found", f"Unknown rendezvous {rendezvous_id}")
        if rendezvous["state"] not in {"open", "satisfied"}:
            raise TodoError("workflow_rendezvous_inactive", f"Rendezvous {rendezvous_id} is not accepting arrivals")
        if not conn.execute(
            "SELECT 1 FROM workflow_rendezvous_participants WHERE rendezvous_id=? AND lane_id=?",
            (rendezvous_id, lane_id),
        ).fetchone():
            raise TodoError("invalid_rendezvous_participant", "Only declared first-class lanes may arrive")
        if not conn.execute(
            "SELECT 1 FROM workflow_lane_tasks WHERE lane_id=? AND task_id=?", (lane_id, task_id)
        ).fetchone():
            raise TodoError("rendezvous_task_scope_mismatch", "Arrival task is not assigned to its lane")
        task = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task or task["status"] != "done":
            raise TodoError("rendezvous_parent_task_incomplete", "Only an authoritatively completed parent task may arrive")
        unresolved_child = conn.execute(
            "SELECT 1 FROM workflow_child_result_candidates WHERE parent_claim_id IN "
            "(SELECT id FROM claims WHERE task_id=?) AND state='collected' LIMIT 1",
            (task_id,),
        ).fetchone()
        if unresolved_child:
            raise TodoError("rendezvous_child_result_unresolved", "Collected child results require parent acceptance or rejection before arrival")
        if (
            not summary.strip()
            or context_version < 1
            or not base_source_identity
            or not final_source_identity
            or artifact.get("kind") not in {"commit", "patch"}
            or not artifact.get("ref")
            or not evidence
        ):
            raise TodoError(
                "invalid_rendezvous_arrival",
                "Arrival requires summary, source identities, commit/patch artifact, evidence, and context version",
            )
        bounded = {
            "summary": summary,
            "base_source_identity": base_source_identity,
            "final_source_identity": final_source_identity,
            "artifact": artifact,
            "interfaces": interfaces,
            "evidence": evidence,
            "warnings": warnings,
            "context_version": context_version,
        }
        require_bounded_payload(bounded, limit=COORDINATE_TASK_BUDGET_BYTES, code="rendezvous_arrival_too_large")
        existing = conn.execute(
            "SELECT * FROM workflow_rendezvous_arrivals WHERE rendezvous_id=? AND lane_id=?",
            (rendezvous_id, lane_id),
        ).fetchone()
        if existing:
            stored = {
                "summary": existing["summary"],
                "base_source_identity": existing["base_source_identity"],
                "final_source_identity": existing["final_source_identity"],
                "artifact": json.loads(existing["artifact_json"]),
                "interfaces": json.loads(existing["interfaces_json"]),
                "evidence": json.loads(existing["evidence_json"]),
                "warnings": json.loads(existing["warnings_json"]),
                "context_version": int(existing["context_version"]),
            }
            if existing["state"] == "valid" and (
                canonical_json(stored) != canonical_json(bounded) or existing["task_id"] != task_id
            ):
                raise TodoError("rendezvous_arrival_conflict", "Lane already submitted a different arrival")
            if existing["state"] == "valid":
                return {"duplicate": True, "arrival": stored, "condition": _condition(conn, rendezvous_id)}
            now = utc_now()
            conn.execute(
                "UPDATE workflow_rendezvous_arrivals SET task_id=?,summary=?,base_source_identity=?,"
                "final_source_identity=?,artifact_json=?,interfaces_json=?,evidence_json=?,warnings_json=?,"
                "context_version=?,state='valid',arrived_at=?,revision=? WHERE rendezvous_id=? AND lane_id=?",
                (
                    task_id,
                    summary.strip(),
                    base_source_identity,
                    final_source_identity,
                    canonical_json(artifact),
                    canonical_json(interfaces),
                    canonical_json(evidence),
                    canonical_json(warnings),
                    context_version,
                    now,
                    revision,
                    rendezvous_id,
                    lane_id,
                ),
            )
            condition = _apply_condition(conn, rendezvous_id, revision)
            message = _insert_message(
                conn,
                revision,
                run_id=run_id,
                author_lane_id=lane_id,
                task_id=task_id,
                kind="rendezvous_arrival",
                payload={
                    "rendezvous_id": rendezvous_id,
                    "summary": summary.strip(),
                    "condition": condition,
                    "revalidated": True,
                },
                recipients=[{"type": "run", "id": run_id}],
                references=[{"type": "rendezvous", "id": rendezvous_id}],
                blocking=False,
                linked_message_id=None,
            )
            return {
                "duplicate": False,
                "revalidated": True,
                "arrival": bounded,
                "condition": condition,
                "message": message,
            }
        now = utc_now()
        conn.execute(
            "INSERT INTO workflow_rendezvous_arrivals(rendezvous_id,lane_id,task_id,summary,base_source_identity,"
            "final_source_identity,artifact_json,interfaces_json,evidence_json,warnings_json,context_version,state,arrived_at,revision) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,'valid',?,?)",
            (
                rendezvous_id,
                lane_id,
                task_id,
                summary.strip(),
                base_source_identity,
                final_source_identity,
                canonical_json(artifact),
                canonical_json(interfaces),
                canonical_json(evidence),
                canonical_json(warnings),
                context_version,
                now,
                revision,
            ),
        )
        condition = _apply_condition(conn, rendezvous_id, revision)
        message = _insert_message(
            conn,
            revision,
            run_id=run_id,
            author_lane_id=lane_id,
            task_id=task_id,
            kind="rendezvous_arrival",
            payload={"rendezvous_id": rendezvous_id, "summary": summary.strip(), "condition": condition},
            recipients=[{"type": "run", "id": run_id}],
            references=[{"type": "rendezvous", "id": rendezvous_id}],
            blocking=False,
            linked_message_id=None,
        )
        return {"duplicate": False, "arrival": bounded, "condition": condition, "message": message}

    def arrive(
        self,
        *,
        capability_class: str,
        run_id: str,
        lane_id: str,
        rendezvous_id: str,
        task_id: str,
        summary: str,
        context_version: int,
        base_source_identity: str | None = None,
        final_source_identity: str | None = None,
        artifact: dict[str, Any] | None = None,
        interfaces: dict[str, Any] | None = None,
        evidence: Iterable[dict[str, Any]] = (),
        warnings: Iterable[dict[str, Any]] = (),
        actor_session_id: str | None = None,
    ) -> dict[str, Any]:
        evidence_list = [dict(item) for item in evidence]
        warning_list = [dict(item) for item in warnings]

        def operation(conn: sqlite3.Connection, revision: int) -> dict[str, Any]:
            return self._arrive_in_transaction(
                conn,
                revision,
                capability_class=capability_class,
                run_id=run_id,
                lane_id=lane_id,
                rendezvous_id=rendezvous_id,
                task_id=task_id,
                summary=summary,
                base_source_identity=base_source_identity,
                final_source_identity=final_source_identity,
                artifact=dict(artifact or {}),
                interfaces=dict(interfaces or {}),
                evidence=evidence_list,
                warnings=warning_list,
                context_version=context_version,
            )

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_rendezvous",
            entity_id=rendezvous_id,
            event_type="workflow_rendezvous.arrived",
            payload=lambda value: {
                "run_id": run_id,
                "rendezvous_id": rendezvous_id,
                "lane_id": lane_id,
                "task_id": task_id,
                "duplicate": value["duplicate"],
                "satisfied": value["condition"]["satisfied"],
            },
            operation=operation,
        )
        return {**result, "project_revision": revision}

    def invalidate_arrival(
        self,
        *,
        capability_class: str,
        run_id: str,
        author_lane_id: str,
        rendezvous_id: str,
        lane_id: str,
        reason: str,
        actor_session_id: str | None = None,
    ) -> dict[str, Any]:
        _require_first_class(capability_class)
        if not reason.strip():
            raise TodoError("rendezvous_invalidation_reason_required", "Arrival invalidation requires a reason")

        def operation(conn: sqlite3.Connection, revision: int) -> dict[str, Any]:
            author = _lane(conn, run_id, author_lane_id)
            if author["role"] not in {"coordinator", "validator", "integrator"}:
                raise TodoError("rendezvous_invalidation_forbidden", "Lane role cannot invalidate an arrival")
            row = conn.execute(
                "SELECT a.state,r.join_task_id FROM workflow_rendezvous_arrivals a "
                "JOIN workflow_rendezvous r ON r.id=a.rendezvous_id "
                "WHERE a.rendezvous_id=? AND a.lane_id=? AND r.run_id=?",
                (rendezvous_id, lane_id, run_id),
            ).fetchone()
            if not row:
                raise TodoError("rendezvous_arrival_not_found", "Arrival does not exist")
            if row["state"] == "invalid":
                return {"already_invalid": True, "condition": _condition(conn, rendezvous_id)}
            join_task = conn.execute("SELECT status FROM tasks WHERE id=?", (row["join_task_id"],)).fetchone()
            if join_task and join_task["status"] in {"done", "cancelled", "superseded"}:
                raise TodoError(
                    "rendezvous_terminal_invalidation_requires_recovery",
                    "A terminal join cannot be invalidated through ordinary lane coordination",
                )
            conn.execute(
                "UPDATE workflow_rendezvous_arrivals SET state='invalid',revision=? WHERE rendezvous_id=? AND lane_id=?",
                (revision, rendezvous_id, lane_id),
            )
            condition = _apply_condition(conn, rendezvous_id, revision)
            return {"already_invalid": False, "condition": condition, "reason": reason.strip()}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_rendezvous",
            entity_id=rendezvous_id,
            event_type="workflow_rendezvous.arrival_invalidated",
            payload={"run_id": run_id, "rendezvous_id": rendezvous_id, "lane_id": lane_id, "reason": reason.strip()},
            operation=operation,
        )
        return {**result, "project_revision": revision}

    def inspect(self, rendezvous_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM workflow_rendezvous WHERE id=?", (rendezvous_id,)).fetchone()
            if not row:
                raise TodoError("workflow_rendezvous_not_found", f"Unknown rendezvous {rendezvous_id}")
            result = _rendezvous_dict(conn, row)
            result["condition"] = _condition(conn, rendezvous_id)
        require_bounded_payload(result, limit=COORDINATE_TASK_BUDGET_BYTES * 4, code="workflow_inspection_too_large")
        return result
