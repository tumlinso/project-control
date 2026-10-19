"""Generic resource inventory, deterministic leases, and optional NVIDIA discovery."""

from __future__ import annotations

import json
import os
import secrets
import socket
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import utc_now
from .models import ExitCode, TodoError
from .sessions import token_hash


def process_start(pid: int | None = None) -> str | None:
    target = pid or os.getpid()
    try:
        return Path(f"/proc/{target}/stat").read_text(encoding="utf-8").split()[21]
    except (OSError, IndexError):
        return None


def local_process_alive(pid: int | None, expected_start: str | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    current = process_start(int(pid))
    return not expected_start or not current or current == expected_start


def sweep_resource_leases(conn: sqlite3.Connection) -> list[str]:
    now = utc_now()
    released: list[str] = []
    for lease in conn.execute("SELECT * FROM resource_leases WHERE state='active' AND expires_at<=?", (now,)).fetchall():
        if lease["hostname"] == socket.gethostname() and local_process_alive(lease["pid"], lease["process_start"]):
            continue
        conn.execute("UPDATE resource_leases SET state='expired',released_at=? WHERE id=?", (now, lease["id"]))
        released.append(lease["id"])
    return released


def discover_nvidia() -> list[dict[str, object]]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    devices: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        parts = [piece.strip() for piece in line.split(",", 3)]
        if len(parts) != 4:
            continue
        index, gpu_uuid, model, memory = parts
        devices.append(
            {
                "id": f"gpu:{index}",
                "class_id": "gpu",
                "capacity": 1,
                "hostname": socket.gethostname(),
                "metadata": {"physical_index": int(index), "uuid": gpu_uuid, "model": model, "memory_mib": int(memory)},
            }
        )
    return devices


def upsert_inventory(conn: sqlite3.Connection, resources: list[dict[str, object]]) -> int:
    for item in resources:
        class_id = str(item["class_id"])
        conn.execute("INSERT OR IGNORE INTO resource_classes(id,mode,metadata_json) VALUES(?,'exclusive','{}')", (class_id,))
        conn.execute(
            "INSERT INTO resource_instances(id,class_id,capacity,hostname,metadata_json,enabled) VALUES(?,?,?,?,?,1) "
            "ON CONFLICT(id) DO UPDATE SET class_id=excluded.class_id,capacity=excluded.capacity,hostname=excluded.hostname,metadata_json=excluded.metadata_json,enabled=1",
            (
                str(item["id"]),
                class_id,
                int(item.get("capacity", 1)),
                item.get("hostname"),
                json.dumps(item.get("metadata", {}), sort_keys=True),
            ),
        )
    return len(resources)


def matching_instances(conn: sqlite3.Connection, selector: str) -> list[sqlite3.Row]:
    if selector.endswith(":any"):
        class_id = selector[:-4]
        return conn.execute("SELECT * FROM resource_instances WHERE class_id=? AND enabled=1 AND (hostname IS NULL OR hostname=?) ORDER BY id", (class_id, socket.gethostname())).fetchall()
    row = conn.execute("SELECT * FROM resource_instances WHERE id=? AND enabled=1 AND (hostname IS NULL OR hostname=?)", (selector, socket.gethostname())).fetchone()
    return [row] if row else []


def acquire_resource(
    conn: sqlite3.Connection,
    *,
    selector: str,
    session_id: str,
    claim_id: str | None,
    request_id: str | None,
    lease_seconds: int,
    command: list[str] | None = None,
) -> tuple[dict[str, object], str]:
    sweep_resource_leases(conn)
    for instance in matching_instances(conn, selector):
        active = conn.execute(
            "SELECT COUNT(*) FROM resource_leases WHERE instance_id=? AND state='active'",
            (instance["id"],),
        ).fetchone()[0]
        if int(active) >= int(instance["capacity"]):
            continue
        raw = "tor_" + secrets.token_urlsafe(28)
        lease_id = str(uuid.uuid4())
        expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        conn.execute(
            "INSERT INTO resource_leases(id,instance_id,claim_id,session_id,request_id,token_hash,state,hostname,pid,process_start,command_json,acquired_at,heartbeat_at,expires_at) "
            "VALUES(?,?,?,?,?,?,'active',?,?,?,?,?,?,?)",
            (
                lease_id,
                instance["id"],
                claim_id,
                session_id,
                request_id,
                token_hash(raw),
                socket.gethostname(),
                os.getpid(),
                process_start(),
                json.dumps(command or []),
                utc_now(),
                utc_now(),
                expires,
            ),
        )
        return {
            "lease_id": lease_id,
            "instance_id": instance["id"],
            "class_id": instance["class_id"],
            "metadata": json.loads(instance["metadata_json"]),
            "expires_at": expires,
        }, raw
    raise TodoError("resource_unavailable", f"No free resource matches {selector}", ExitCode.CONTENTION, {"selector": selector})


def release_resource(conn: sqlite3.Connection, lease_token: str) -> dict[str, object]:
    lease = conn.execute("SELECT * FROM resource_leases WHERE token_hash=? AND state='active'", (token_hash(lease_token),)).fetchone()
    if not lease:
        raise TodoError("invalid_resource_token", "Resource lease token is invalid or inactive", ExitCode.INVALID_TOKEN)
    conn.execute("UPDATE resource_leases SET state='released',released_at=? WHERE id=?", (utc_now(), lease["id"]))
    return {"lease_id": lease["id"], "instance_id": lease["instance_id"], "session_id": lease["session_id"], "state": "released"}


def resource_environment(leases: list[dict[str, object]]) -> dict[str, str]:
    gpu_indices = [str(item.get("metadata", {}).get("physical_index")) for item in leases if item.get("class_id") == "gpu"]
    gpu_indices = [value for value in gpu_indices if value != "None"]
    return {"CUDA_VISIBLE_DEVICES": ",".join(gpu_indices)} if gpu_indices else {}


def list_resources(conn: sqlite3.Connection) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in conn.execute("SELECT * FROM resource_instances ORDER BY class_id,id"):
        active = conn.execute("SELECT COUNT(*) FROM resource_leases WHERE instance_id=? AND state='active'", (row["id"],)).fetchone()[0]
        result.append(
            {
                "id": row["id"],
                "class_id": row["class_id"],
                "capacity": row["capacity"],
                "active": active,
                "available": max(0, int(row["capacity"]) - int(active)),
                "metadata": json.loads(row["metadata_json"]),
            }
        )
    return result
