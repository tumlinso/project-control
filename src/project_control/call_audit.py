"""Bounded, private audit of Project Control tool and local model calls."""

from __future__ import annotations

import contextvars
import fcntl
import json
import logging
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .security import redact, redact_text


AUDIT_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "project-control/call-audit"
MAX_FILE_BYTES = 256 * 1024 * 1024
BACKUPS = 5
MAX_EVENT_BYTES = 2 * 1024

call_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("pc_call_id", default=None)
caller_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("pc_caller", default=None)

_LOGGER = logging.getLogger(__name__)
_ROUTING_KEYS = frozenset({
    "project", "repository", "kind", "target", "detail", "effort", "compute_profile",
    "parallelism", "mode", "action", "run_id", "task_id", "lane_id", "campaign", "subject",
})
_TEXT_KEYS = frozenset({"question", "questions", "hypothesis", "objective"})


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _excerpt(value: str, limit: int = 160) -> str:
    # Redact before and after truncation so a boundary cannot expose a partial token.
    return redact_text(redact_text(value)[:limit])


def summarize_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Retain useful routing and question hints, never arbitrary argument values."""
    summary: dict[str, Any] = {"keys": sorted(str(key) for key in arguments)[:32]}
    for key in _ROUTING_KEYS:
        value = arguments.get(key)
        if isinstance(value, (str, int, float, bool)) and not isinstance(value, complex):
            summary[key] = _excerpt(str(value), 96)
    for key in _TEXT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            summary[f"{key}_excerpt"] = _excerpt(value)
            summary[f"{key}_chars"] = len(value)
        elif isinstance(value, list):
            summary[f"{key}_count"] = len(value)
            summary[f"{key}_excerpts"] = [_excerpt(item, 120) for item in value[:2] if isinstance(item, str)]
    targets = arguments.get("targets")
    if isinstance(targets, list):
        summary["targets_count"] = len(targets)
        summary["target_hints"] = [
            {"kind": _excerpt(str(item.get("kind", "")), 32),
             "value_excerpt": _excerpt(str(item.get("value", "")), 96)}
            for item in targets[:2] if isinstance(item, dict)
        ]
    return redact(summary)


def summarize_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize a model request without keeping its message list."""
    summary: dict[str, Any] = {
        "message_count": len(messages),
        "roles": [str(item.get("role", "unknown"))[:16] for item in messages[:24] if isinstance(item, dict)],
        "content_chars": sum(len(item.get("content", "")) for item in messages
                             if isinstance(item, dict) and isinstance(item.get("content"), str)),
    }
    user_contents = [item["content"] for item in messages if isinstance(item, dict)
                     and item.get("role") == "user" and isinstance(item.get("content"), str)]
    if user_contents:
        first = user_contents[0]
        try:
            parsed = json.loads(first)
        except (ValueError, TypeError):
            parsed = None
        question = parsed.get("question") if isinstance(parsed, dict) else None
        summary["question_excerpt"] = _excerpt(question if isinstance(question, str) else first)
        latest = user_contents[-1]
        if latest.startswith("TOOL_RESULTS"):
            summary["latest_user_kind"] = "tool_results"
        elif latest != first:
            summary["latest_user_excerpt"] = _excerpt(latest)
    return redact(summary)


def _encoded_event(event: Mapping[str, Any]) -> bytes:
    record = {"time_utc": utc_timestamp(), **redact(dict(event))}
    encoded = (json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) <= MAX_EVENT_BYTES:
        return encoded
    reduced = {key: record[key] for key in (
        "time_utc", "event", "phase", "call_id", "tool", "profile", "operation",
        "outcome", "duration_ms", "packet_bytes",
    ) if key in record}
    for key in ("call_id", "tool", "profile", "operation"):
        if isinstance(reduced.get(key), str):
            reduced[key] = reduced[key][:128]
    caller = record.get("caller")
    if isinstance(caller, dict):
        reduced["caller"] = {key: str(caller[key])[:96] for key in (
            "transport", "peer_ip", "peer_port", "parent_pid", "upstream_identity",
        ) if key in caller}
    arguments = record.get("arguments")
    if isinstance(arguments, dict):
        reduced["arguments"] = {key: arguments[key] for key in (
            "keys", "question_chars", "questions_count", "hypothesis_chars",
        ) if key in arguments}
        if isinstance(reduced["arguments"].get("keys"), list):
            reduced["arguments"]["keys"] = [str(key)[:32] for key in reduced["arguments"]["keys"][:16]]
    messages = record.get("messages")
    if isinstance(messages, dict):
        reduced["messages"] = {key: messages[key] for key in (
            "message_count", "content_chars", "roles",
        ) if key in messages}
    if isinstance(record.get("packet_keys"), list):
        reduced["packet_keys"] = [str(key)[:32] for key in record["packet_keys"][:16]]
    reduced["truncated"] = True
    encoded = (json.dumps(reduced, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_EVENT_BYTES:
        reduced.pop("packet_keys", None)
        reduced.pop("messages", None)
        reduced.pop("arguments", None)
        encoded = (json.dumps(reduced, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(encoded) > MAX_EVENT_BYTES:
            raise ValueError("audit_event_metadata_too_large")
    return encoded


def _open_private(path: Path) -> int:
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
    return fd


def _write_event(event: Mapping[str, Any]) -> None:
    encoded = _encoded_event(event)
    AUDIT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if AUDIT_DIR.is_symlink():
        raise OSError("audit directory is a symlink")
    AUDIT_DIR.chmod(0o700)
    lock_fd = _open_private(AUDIT_DIR / "calls.lock")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        active = AUDIT_DIR / "calls.jsonl"
        if active.is_symlink():
            raise OSError("audit file is a symlink")
        if active.exists() and active.stat().st_size + len(encoded) > MAX_FILE_BYTES:
            oldest = AUDIT_DIR / f"calls.jsonl.{BACKUPS}"
            oldest.unlink(missing_ok=True)
            for index in range(BACKUPS - 1, 0, -1):
                previous = AUDIT_DIR / f"calls.jsonl.{index}"
                if previous.exists():
                    previous.replace(AUDIT_DIR / f"calls.jsonl.{index + 1}")
            active.replace(AUDIT_DIR / "calls.jsonl.1")
        fd = _open_private(active)
        try:
            if os.write(fd, encoded) != len(encoded):
                raise OSError("short audit write")
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def write_event(event: Mapping[str, Any]) -> None:
    """Never let audit storage availability change tool or model behavior."""
    try:
        _write_event(event)
    except Exception as error:
        _LOGGER.warning("Project Control call audit unavailable (%s)", type(error).__name__)
