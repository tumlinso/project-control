"""Bounded typed communication between first-class workflow lanes.

This module deliberately accepts the existing todo ``Database`` seam.  It
does not own a connection, revision counter, or event store.  Local-worker
children are rejected at the public boundary and continue to communicate only
through parent-mediated child result candidates.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from typing import Any

from ..config import utc_now
from ..models import TodoError
from .foundation import (
    COORDINATE_TASK_BUDGET_BYTES,
    MESSAGE_KINDS,
    MESSAGE_PAYLOAD_BUDGET_BYTES,
    ROLES,
    WorkflowDatabase,
    canonical_json,
    require_bounded_payload,
)


RECIPIENT_TYPES = frozenset({"lane", "role", "task", "run"})
InterfaceChangeHook = Callable[[sqlite3.Connection, dict[str, Any], int], dict[str, Any]]
_BULK_CONTENT_KEYS = frozenset(
    {"log", "logs", "transcript", "transcripts", "stdout", "stderr", "raw_log", "raw_logs", "traceback", "packet", "packet_body", "child_packet"}
)
_SECRET_CONTENT_KEYS = frozenset(
    {
        "token", "claim_token", "session_token", "child_token", "worker_token",
        "approval_token", "recovery_approval", "capability_token", "password",
        "secret", "api_key", "gpu_identifier", "model_endpoint",
        "capability_lineage", "child_lineage",
    }
)
MESSAGE_ENVELOPE_BUDGET_BYTES = COORDINATE_TASK_BUDGET_BYTES - 512


def _require_first_class(capability_class: str) -> None:
    if capability_class != "first_class":
        raise TodoError(
            "child_run_communication_forbidden",
            "Local-worker children cannot publish or resolve run-level messages",
        )


def _json_object(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TodoError("invalid_workflow_message", f"{name} must be an object")
    return dict(value)


def _reject_bulk_content(value: object) -> None:
    if isinstance(value, dict):
        lowered = {str(key).lower() for key in value}
        forbidden = sorted(key for key in lowered if key in _BULK_CONTENT_KEYS)
        if forbidden:
            raise TodoError(
                "workflow_message_bulk_content_forbidden",
                "Run messages must reference bounded artifacts instead of embedding logs or transcripts",
                details={"fields": forbidden},
            )
        secret = sorted(
            key for key in lowered
            if key in _SECRET_CONTENT_KEYS or key.endswith("_token") or key.endswith("_secret")
        )
        if secret:
            raise TodoError(
                "workflow_message_secret_forbidden",
                "Run messages must not persist raw secrets or execution identifiers",
                details={"fields": secret},
            )
        for item in value.values():
            _reject_bulk_content(item)
    elif isinstance(value, list):
        for item in value:
            _reject_bulk_content(item)


def _lane(conn: sqlite3.Connection, run_id: str, lane_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id,run_id,role,context_cursor FROM workflow_lanes WHERE id=? AND run_id=?",
        (lane_id, run_id),
    ).fetchone()
    if not row:
        raise TodoError("workflow_lane_not_found", f"Lane {lane_id} is not a first-class lane in run {run_id}")
    return row


def _normalize_recipients(
    conn: sqlite3.Connection,
    run_id: str,
    recipients: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in recipients:
        if not isinstance(raw, dict):
            raise TodoError("invalid_message_recipient", "Each recipient must be an object")
        recipient_type = str(raw.get("type", ""))
        recipient_id = str(raw.get("id", ""))
        if recipient_type not in RECIPIENT_TYPES or not recipient_id:
            raise TodoError("invalid_message_recipient", "Recipient type or identity is invalid")
        key = (recipient_type, recipient_id)
        if key in seen:
            continue
        if recipient_type == "lane" and not conn.execute(
            "SELECT 1 FROM workflow_lanes WHERE id=? AND run_id=?", (recipient_id, run_id)
        ).fetchone():
            raise TodoError("message_recipient_not_found", f"Recipient lane {recipient_id} is not in run {run_id}")
        if recipient_type == "role" and recipient_id not in ROLES:
            raise TodoError("invalid_message_recipient", f"Unknown recipient role {recipient_id}")
        if recipient_type == "task" and not conn.execute(
            "SELECT 1 FROM workflow_lane_tasks lt JOIN workflow_lanes l ON l.id=lt.lane_id "
            "WHERE lt.task_id=? AND l.run_id=?",
            (recipient_id, run_id),
        ).fetchone():
            raise TodoError("message_recipient_not_found", f"Recipient task {recipient_id} is not queued in run {run_id}")
        if recipient_type == "run" and recipient_id != run_id:
            raise TodoError("message_recipient_not_found", "Run recipient must match the message run")
        normalized.append({"type": recipient_type, "id": recipient_id})
        seen.add(key)
    if not normalized:
        raise TodoError("message_recipient_required", "At least one bounded run recipient is required")
    return sorted(normalized, key=lambda item: (item["type"], item["id"]))


def _message_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    recipients = [
        {"type": item[0], "id": item[1]}
        for item in conn.execute(
            "SELECT recipient_type,recipient_id FROM workflow_message_recipients "
            "WHERE message_id=? ORDER BY recipient_type,recipient_id",
            (row["id"],),
        )
    ]
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "author_lane_id": row["author_lane_id"],
        "task_id": row["task_id"],
        "kind": row["kind"],
        "payload": json.loads(row["payload_json"]),
        "references": json.loads(row["references_json"]),
        "recipients": recipients,
        "blocking": bool(row["blocking"]),
        "state": row["state"],
        "linked_message_id": row["linked_message_id"],
        "revision": int(row["revision"]),
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
    }


def _insert_message(
    conn: sqlite3.Connection,
    revision: int,
    *,
    run_id: str,
    author_lane_id: str,
    task_id: str | None,
    kind: str,
    payload: dict[str, Any],
    recipients: list[dict[str, str]],
    references: list[dict[str, Any]],
    blocking: bool,
    linked_message_id: str | None,
    message_id: str | None = None,
) -> dict[str, Any]:
    now = utc_now()
    identifier = message_id or str(uuid.uuid4())
    conn.execute(
        "INSERT INTO workflow_messages(id,run_id,author_lane_id,task_id,kind,payload_json,references_json,"
        "blocking,state,linked_message_id,revision,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            identifier,
            run_id,
            author_lane_id,
            task_id,
            kind,
            canonical_json(payload),
            canonical_json(references),
            int(blocking),
            "open",
            linked_message_id,
            revision,
            now,
        ),
    )
    conn.executemany(
        "INSERT INTO workflow_message_recipients(message_id,recipient_type,recipient_id) VALUES(?,?,?)",
        [(identifier, item["type"], item["id"]) for item in recipients],
    )
    row = conn.execute("SELECT * FROM workflow_messages WHERE id=?", (identifier,)).fetchone()
    return _message_dict(conn, row)


def _lane_is_recipient(conn: sqlite3.Connection, message_id: str, lane: sqlite3.Row) -> bool:
    task_ids = {
        row[0]
        for row in conn.execute("SELECT task_id FROM workflow_lane_tasks WHERE lane_id=?", (lane["id"],))
    }
    for row in conn.execute(
        "SELECT recipient_type,recipient_id FROM workflow_message_recipients WHERE message_id=?",
        (message_id,),
    ):
        if row["recipient_type"] == "run" and row["recipient_id"] == lane["run_id"]:
            return True
        if row["recipient_type"] == "lane" and row["recipient_id"] == lane["id"]:
            return True
        if row["recipient_type"] == "role" and row["recipient_id"] == lane["role"]:
            return True
        if row["recipient_type"] == "task" and row["recipient_id"] in task_ids:
            return True
    return False


class MessageService:
    """Transactional run-message service consumed by ``WorkflowKernel``."""

    def __init__(self, db: WorkflowDatabase, *, interface_change_hook: InterfaceChangeHook | None = None):
        self.db = db
        self.interface_change_hook = interface_change_hook

    def publish(
        self,
        *,
        capability_class: str,
        run_id: str,
        author_lane_id: str,
        kind: str,
        payload: dict[str, Any],
        recipients: Iterable[dict[str, str]],
        task_id: str | None = None,
        references: Iterable[dict[str, Any]] = (),
        blocking: bool = False,
        actor_session_id: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        _require_first_class(capability_class)
        if kind not in MESSAGE_KINDS or kind in {"answer", "rendezvous_arrival"}:
            raise TodoError("invalid_workflow_message_kind", f"Message kind {kind} requires a dedicated operation or is unknown")
        payload = _json_object(payload, name="payload")
        reference_list = [_json_object(item, name="reference") for item in references]
        _reject_bulk_content(payload)
        _reject_bulk_content(reference_list)
        require_bounded_payload(
            {"kind": kind, "payload": payload, "references": reference_list},
            limit=MESSAGE_PAYLOAD_BUDGET_BYTES,
            code="workflow_message_too_large",
        )

        def operation(conn: sqlite3.Connection, revision: int) -> dict[str, Any]:
            author = _lane(conn, run_id, author_lane_id)
            if task_id and not conn.execute(
                "SELECT 1 FROM workflow_lane_tasks WHERE lane_id=? AND task_id=?", (author_lane_id, task_id)
            ).fetchone():
                raise TodoError("message_task_scope_mismatch", "Message task is not assigned to its author lane")
            normalized = _normalize_recipients(conn, run_id, recipients)
            durable: dict[str, Any] | None = None
            if kind == "decision":
                if author["role"] != "coordinator":
                    raise TodoError("decision_publication_forbidden", "Only a coordinator lane may publish a project decision")
                durable = self._publish_decision(conn, payload, revision)
            elif kind == "interface_change":
                if author["role"] not in {"coordinator", "implementer", "specialist"}:
                    raise TodoError("interface_publication_forbidden", "Lane role cannot publish an interface change")
                if self.interface_change_hook is None:
                    raise TodoError("interface_change_hook_required", "Interface changes require the canonical interface authority")
                durable = self.interface_change_hook(conn, payload, revision)
                if not isinstance(durable, dict):
                    raise TodoError("invalid_interface_change_result", "Interface authority returned an invalid result")
            message = _insert_message(
                conn,
                revision,
                run_id=run_id,
                author_lane_id=author_lane_id,
                task_id=task_id,
                kind=kind,
                payload=payload,
                recipients=normalized,
                references=reference_list,
                blocking=blocking,
                linked_message_id=None,
                message_id=message_id,
            )
            require_bounded_payload(
                message,
                limit=MESSAGE_ENVELOPE_BUDGET_BYTES,
                code="workflow_message_envelope_too_large",
            )
            return {"message": message, "durable_change": durable}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_message",
            entity_id=lambda value: value["message"]["id"],
            event_type=f"workflow_message.{kind}",
            payload=lambda value: {
                "run_id": run_id,
                "lane_id": author_lane_id,
                "task_id": task_id,
                "message_id": value["message"]["id"],
                "blocking": blocking,
            },
            operation=operation,
        )
        return {**result, "project_revision": revision}

    @staticmethod
    def _publish_decision(conn: sqlite3.Connection, payload: dict[str, Any], revision: int) -> dict[str, Any]:
        decision_id = str(payload.get("decision_id", ""))
        if not decision_id or "value" not in payload:
            raise TodoError("invalid_decision_message", "Decision messages require decision_id and value")
        row = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
        if not row:
            raise TodoError("decision_not_found", f"Unknown durable decision {decision_id}")
        value = payload["value"]
        allowed = json.loads(row["allowed_json"] or "[]")
        if allowed and value not in allowed:
            raise TodoError("decision_value_not_allowed", f"Value is not allowed for decision {decision_id}")
        conn.execute(
            "UPDATE decisions SET value_json=?,updated_at=?,revision=? WHERE id=?",
            (canonical_json(value), utc_now(), revision, decision_id),
        )
        return {"decision_id": decision_id, "value": value, "revision": revision}

    def answer(
        self,
        *,
        capability_class: str,
        run_id: str,
        author_lane_id: str,
        question_id: str,
        payload: dict[str, Any],
        references: Iterable[dict[str, Any]] = (),
        actor_session_id: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        _require_first_class(capability_class)
        payload = _json_object(payload, name="payload")
        reference_list = [_json_object(item, name="reference") for item in references]
        _reject_bulk_content(payload)
        _reject_bulk_content(reference_list)
        require_bounded_payload(
            {"kind": "answer", "payload": payload, "references": reference_list},
            limit=MESSAGE_PAYLOAD_BUDGET_BYTES,
            code="workflow_message_too_large",
        )

        def operation(conn: sqlite3.Connection, revision: int) -> dict[str, Any]:
            lane = _lane(conn, run_id, author_lane_id)
            question = conn.execute("SELECT * FROM workflow_messages WHERE id=? AND run_id=?", (question_id, run_id)).fetchone()
            if not question or question["kind"] != "question":
                raise TodoError("workflow_question_not_found", f"Unknown question {question_id}")
            if question["state"] != "open":
                raise TodoError("workflow_question_resolved", f"Question {question_id} is already resolved")
            if not _lane_is_recipient(conn, question_id, lane):
                raise TodoError("workflow_question_not_addressed", "Answering lane is not a recipient of the question")
            recipients = [
                {"type": row["recipient_type"], "id": row["recipient_id"]}
                for row in conn.execute(
                    "SELECT recipient_type,recipient_id FROM workflow_message_recipients WHERE message_id=?",
                    (question_id,),
                )
            ]
            recipients.append({"type": "lane", "id": question["author_lane_id"]})
            recipients = _normalize_recipients(conn, run_id, recipients)
            answer = _insert_message(
                conn,
                revision,
                run_id=run_id,
                author_lane_id=author_lane_id,
                task_id=question["task_id"],
                kind="answer",
                payload=payload,
                recipients=recipients,
                references=reference_list,
                blocking=False,
                linked_message_id=question_id,
                message_id=message_id,
            )
            require_bounded_payload(
                answer,
                limit=MESSAGE_ENVELOPE_BUDGET_BYTES,
                code="workflow_message_envelope_too_large",
            )
            now = utc_now()
            conn.execute(
                "UPDATE workflow_messages SET state='resolved',resolved_at=?,revision=? WHERE id=?",
                (now, revision, question_id),
            )
            return {"message": answer, "resolved_question_id": question_id, "resolved_at": now}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_message",
            entity_id=lambda value: value["message"]["id"],
            event_type="workflow_message.answer",
            payload=lambda value: {
                "run_id": run_id,
                "lane_id": author_lane_id,
                "message_id": value["message"]["id"],
                "resolved_question_id": question_id,
            },
            operation=operation,
        )
        return {**result, "project_revision": revision}

    def sync(
        self,
        *,
        capability_class: str,
        run_id: str,
        lane_id: str,
        actor_session_id: str | None = None,
        budget_bytes: int = COORDINATE_TASK_BUDGET_BYTES,
    ) -> dict[str, Any]:
        _require_first_class(capability_class)
        if budget_bytes < COORDINATE_TASK_BUDGET_BYTES:
            raise TodoError("workflow_sync_budget_too_small", "Sync budget cannot hold one bounded message envelope")

        def unread_rows(conn: sqlite3.Connection, lane: sqlite3.Row) -> list[sqlite3.Row]:
            return conn.execute(
                "SELECT DISTINCT m.* FROM workflow_messages m "
                "JOIN workflow_message_recipients r ON r.message_id=m.id "
                "WHERE m.run_id=? AND ("
                "(r.recipient_type='run' AND r.recipient_id=?) OR "
                "(r.recipient_type='lane' AND r.recipient_id=?) OR "
                "(r.recipient_type='role' AND r.recipient_id=?) OR "
                "(r.recipient_type='task' AND r.recipient_id IN "
                " (SELECT task_id FROM workflow_lane_tasks WHERE lane_id=?))) "
                "AND NOT EXISTS (SELECT 1 FROM workflow_message_receipts x WHERE x.message_id=m.id AND x.lane_id=?) "
                "ORDER BY m.revision,m.id",
                (run_id, run_id, lane_id, lane["role"], lane_id, lane_id),
            ).fetchall()

        with self.db.read() as conn:
            lane = _lane(conn, run_id, lane_id)
            if not unread_rows(conn, lane):
                return {
                    "run_id": run_id,
                    "lane_id": lane_id,
                    "messages": [],
                    "cursor": int(lane["context_cursor"]),
                    "remaining": 0,
                    "blocking": [],
                    "project_revision": int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0]),
                }

        def operation(conn: sqlite3.Connection, revision: int) -> dict[str, Any]:
            lane = _lane(conn, run_id, lane_id)
            rows = unread_rows(conn, lane)
            messages: list[dict[str, Any]] = []
            for row in rows:
                candidate = _message_dict(conn, row)
                envelope = {"messages": [*messages, candidate], "cursor": int(row["revision"])}
                if len(canonical_json(envelope).encode("utf-8")) > budget_bytes:
                    if not messages:
                        raise TodoError("workflow_message_envelope_undeliverable", "Stored message exceeds the negotiated sync budget")
                    break
                messages.append(candidate)
            cursor = int(lane["context_cursor"])
            if messages:
                cursor = max(int(item["revision"]) for item in messages)
                now = utc_now()
                conn.executemany(
                    "INSERT OR IGNORE INTO workflow_message_receipts(message_id,lane_id,received_revision,received_at) "
                    "VALUES(?,?,?,?)",
                    [(item["id"], lane_id, revision, now) for item in messages],
                )
                conn.execute(
                    "UPDATE workflow_lanes SET context_cursor=?,updated_at=?,revision=? WHERE id=?",
                    (cursor, now, revision, lane_id),
                )
            remaining = len(rows) - len(messages)
            return {
                "run_id": run_id,
                "lane_id": lane_id,
                "messages": messages,
                "cursor": cursor,
                "remaining": remaining,
                "blocking": [item["id"] for item in messages if item["blocking"] and item["state"] == "open"],
            }

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_lane",
            entity_id=lane_id,
            event_type="workflow_messages.synced",
            payload=lambda value: {
                "run_id": run_id,
                "lane_id": lane_id,
                "message_ids": [item["id"] for item in value["messages"]],
                "cursor": value["cursor"],
            },
            operation=operation,
        )
        return {**result, "project_revision": revision}

    def inspect(
        self,
        *,
        run_id: str,
        lane_id: str | None = None,
        since_revision: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Read-only bounded message inspection for workflow/project-control adapters."""
        if limit < 1 or limit > 200:
            raise TodoError("invalid_inspection_limit", "Message inspection limit must be between 1 and 200")
        with self.db.read() as conn:
            if lane_id:
                lane = _lane(conn, run_id, lane_id)
                rows = conn.execute(
                    "SELECT DISTINCT m.* FROM workflow_messages m JOIN workflow_message_recipients r ON r.message_id=m.id "
                    "WHERE m.run_id=? AND m.revision>? AND ((r.recipient_type='run' AND r.recipient_id=?) "
                    "OR (r.recipient_type='lane' AND r.recipient_id=?) OR (r.recipient_type='role' AND r.recipient_id=?) "
                    "OR (r.recipient_type='task' AND r.recipient_id IN "
                    "(SELECT task_id FROM workflow_lane_tasks WHERE lane_id=?))) ORDER BY m.revision,m.id LIMIT ?",
                    (run_id, since_revision, run_id, lane_id, lane["role"], lane_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM workflow_messages WHERE run_id=? AND revision>? ORDER BY revision,id LIMIT ?",
                    (run_id, since_revision, limit),
                ).fetchall()
            messages = [_message_dict(conn, row) for row in rows]
        require_bounded_payload(messages, limit=COORDINATE_TASK_BUDGET_BYTES * 4, code="workflow_inspection_too_large")
        return {"run_id": run_id, "lane_id": lane_id, "messages": messages}
