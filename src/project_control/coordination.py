from __future__ import annotations

import base64
import hashlib
import json
from typing import Any


def parse_json(value: object, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return fallback
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return fallback
    return parsed


def table_index(rows: list[dict[str, Any]], *keys: str) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    result: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        identity = tuple(str(row.get(key, "")) for key in keys)
        result.setdefault(identity, []).append(row)
    return result


def observation_identity(*values: object) -> str:
    return hashlib.sha256("\0".join(str(value) for value in values).encode()).hexdigest()


def encode_cursor(tool: str, identity: str, offset: int) -> str:
    payload = {"v": 1, "tool": tool, "identity": identity, "offset": offset}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    envelope = {"payload": payload, "digest": hashlib.sha256(canonical.encode()).hexdigest()}
    return base64.urlsafe_b64encode(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")


def decode_cursor(value: str | None, *, tool: str, identity: str) -> int:
    if not value:
        return 0
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        envelope = json.loads(raw)
        payload = envelope["payload"]
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        valid = (
            envelope["digest"] == hashlib.sha256(canonical.encode()).hexdigest()
            and payload["v"] == 1
            and payload["tool"] == tool
            and payload["identity"] == identity
            and isinstance(payload["offset"], int)
            and payload["offset"] >= 0
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        valid = False
    if not valid:
        raise ValueError("continuation cursor is invalid or stale")
    return int(payload["offset"])
