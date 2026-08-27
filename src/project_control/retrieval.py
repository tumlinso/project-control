"""Deterministic, provenance-preserving helpers for rich read-only synthesis."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterable
from typing import Any


AUTHORITY_LABELS = {
    "task": "authoritative_fact",
    "interface": "authoritative_fact",
    "decision": "authoritative_fact",
    "invariant": "authoritative_fact",
    "checkpoint": "authoritative_fact",
    "gate": "authoritative_fact",
    "run": "authoritative_fact",
    "lane": "authoritative_fact",
    "workflow_dispatch": "authoritative_fact",
    "run_message": "authoritative_fact",
    "rendezvous": "authoritative_fact",
    "workspace": "authoritative_fact",
    "patch_artifact": "authoritative_fact",
    "integration": "authoritative_fact",
    "local_child": "authoritative_fact",
    "git_commit": "authoritative_fact",
    "path": "source_authority",
    "symbol": "source_authority",
    "cuda_campaign": "performance_authority",
    "cuda_result": "performance_authority",
}

MATERIAL_EVENT_WORDS = (
    "task.", "decision", "interface", "fragment", "context", "checkpoint", "gate.",
    "message", "rendezvous", "arrival", "workspace", "patch", "integration", "handoff",
)
NOISE_EVENT_WORDS = ("heartbeat", "pulse", "lease.renew", "receipt", "cursor")


def stable_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def authority_label(kind: str, *, inferred: bool = False, relationship: bool = False) -> str:
    if inferred:
        return "inference"
    if relationship:
        return "derived_relationship"
    return AUTHORITY_LABELS.get(kind, "durable_authority_fact")


def is_current(record: dict[str, Any], scope: str) -> bool:
    if scope == "all":
        return True
    relevance = str(record.get("relevance") or record.get("current_relevance") or "unknown")
    state = str(record.get("effective_state") or record.get("state") or "")
    historical = relevance in {"historical", "superseded"} or state in {
        "superseded", "canceled", "cancelled", "abandoned", "invalidated",
    }
    if historical:
        return False
    if scope == "current":
        return relevance not in {"reference"} and not bool(record.get("terminal"))
    return True


def relevance_priority(record: dict[str, Any]) -> int:
    """Rank current authority ahead of reference and historical material."""
    relevance = str(record.get("relevance") or record.get("current_relevance") or "unknown")
    state = str(record.get("effective_state") or record.get("state") or record.get("status") or "")
    if relevance in {"historical", "superseded"} or state in {
        "superseded", "canceled", "cancelled", "abandoned", "invalidated", "historical_stale",
    }:
        return 3
    if relevance in {"current", "current_attention"} and not bool(record.get("terminal")):
        return 0
    if relevance == "reference":
        return 1
    return 2


def economical_record(record: dict[str, Any], *, expanded: bool) -> dict[str, Any]:
    """Keep authority records useful without carrying large historical prose by default."""
    if expanded:
        return record
    result: dict[str, Any] = {}
    bulky = {"note", "notes", "content", "payload", "raw_payload", "raw_result", "transcript", "log"}
    for key, value in record.items():
        if key in bulky:
            if isinstance(value, str) and value:
                result[f"{key}_summary"] = value[:256] + ("…" if len(value) > 256 else "")
            continue
        if isinstance(value, str) and len(value) > 1024:
            result[key] = value[:1024] + "…"
        else:
            result[key] = value
    return result


def material_event(event: dict[str, Any]) -> bool:
    name = str(event.get("event_type") or event.get("type") or "").casefold()
    if any(word in name for word in NOISE_EVENT_WORDS):
        return False
    return any(word in name for word in MATERIAL_EVENT_WORDS)


def event_sort_key(event: dict[str, Any]) -> tuple[int, int, str, str, str]:
    timestamp = str(event.get("timestamp") or event.get("created_at") or event.get("observed_at") or "")
    try:
        revision = int(event.get("revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    has_revision = event.get("revision") is not None
    return (
        0 if has_revision else 1,
        revision,
        timestamp,
        str(event.get("event_type") or event.get("type") or ""),
        str(event.get("entity_id") or event.get("id") or ""),
    )


def encode_cursor(*, operation: str, query_key: str, offset: int) -> str:
    value = {"v": 1, "operation": operation, "query": query_key, "offset": offset}
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None, *, operation: str, query_key: str) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_continuation_cursor") from exc
    if value != {
        "v": 1,
        "operation": operation,
        "query": query_key,
        "offset": value.get("offset"),
    } or not isinstance(value.get("offset"), int) or value["offset"] < 0:
        raise ValueError("continuation_cursor_mismatch")
    return value["offset"]


def page(
    items: list[dict[str, Any]], *, operation: str, query: Any, limit: int, cursor: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query_key = stable_digest(query)
    offset = decode_cursor(cursor, operation=operation, query_key=query_key)
    selected = items[offset:offset + limit]
    next_offset = offset + len(selected)
    next_cursor = encode_cursor(operation=operation, query_key=query_key, offset=next_offset) if next_offset < len(items) else None
    return selected, {
        "items_considered": len(items),
        "items_returned": len(selected),
        "offset": offset,
        "omitted_count": max(0, len(items) - next_offset),
        "continuation_cursor": next_cursor,
    }


def records_from_tables(tables: dict[str, list[dict[str, Any]]], names: Iterable[str]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for name in names:
        for record in tables.get(name, []):
            if isinstance(record, dict):
                result.append((name, record))
    return result
