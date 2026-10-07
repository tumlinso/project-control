"""Persistent session identities and credentials."""

from __future__ import annotations

import hashlib
import os
import secrets
import socket
import sqlite3
import uuid
from pathlib import Path

from .config import utc_now
from .models import ExitCode, TodoError


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn: sqlite3.Connection, repo_root: Path, metadata: dict[str, object] | None = None) -> tuple[dict[str, object], str]:
    session_id = str(uuid.uuid4())
    token = "tos_" + secrets.token_urlsafe(32)
    label = f"codex-{session_id.replace('-', '')[:6]}"
    now = utc_now()
    external_id = os.environ.get("CODEX_THREAD_ID") or os.environ.get("OPENAI_CODEX_THREAD_ID")
    conn.execute(
        "INSERT INTO sessions(id,label,token_hash,external_id,hostname,pid,repo_root,worktree_root,metadata_json,created_at,last_seen_at,state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            session_id,
            label,
            token_hash(token),
            external_id,
            socket.gethostname(),
            os.getpid(),
            str(repo_root),
            str(repo_root),
            __import__("json").dumps(metadata or {}, sort_keys=True),
            now,
            now,
            "active",
        ),
    )
    return {"agent_id": session_id, "label": label, "hostname": socket.gethostname()}, token


def authenticate_session(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row:
    if not token:
        raise TodoError("invalid_session_token", "A session token is required", ExitCode.INVALID_TOKEN)
    row = conn.execute("SELECT * FROM sessions WHERE token_hash=? AND state='active'", (token_hash(token),)).fetchone()
    if not row:
        raise TodoError("invalid_session_token", "Session token is invalid or inactive", ExitCode.INVALID_TOKEN)
    conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (utc_now(), row["id"]))
    return row


def authenticate_claim(conn: sqlite3.Connection, token: str | None, *, allow_orphaned: bool = False) -> sqlite3.Row:
    if not token:
        raise TodoError("invalid_claim_token", "A claim token is required", ExitCode.INVALID_TOKEN)
    states = ("active", "orphaned") if allow_orphaned else ("active",)
    placeholders = ",".join("?" for _ in states)
    row = conn.execute(
        f"SELECT * FROM claims WHERE token_hash=? AND state IN ({placeholders})",
        (token_hash(token), *states),
    ).fetchone()
    if not row:
        raise TodoError("invalid_claim_token", "Claim token is invalid, expired, or released", ExitCode.INVALID_TOKEN)
    return row
