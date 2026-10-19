"""One-time import of legacy Markdown ledgers as non-authoritative migration input."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .config import utc_now

ROOT_RE = re.compile(r"^- `(?P<slug>[^`]+)` \| status: (?P<status>[^|]+) \| owner: (?P<owner>[^|]+) \| file: `(?P<file>[^`]+)` \| objective: (?P<objective>.+)$")
STATUS_RE = re.compile(r"^- `(?P<slug>[^`]+)` \| status: (?P<status>[^|]+) \| execution: (?P<execution>[^|]+) \| owner: (?P<owner>[^|]+) \| file: `(?P<file>[^`]+)` \| next: (?P<next>.+)$")
FM_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def _frontmatter(text: str) -> dict[str, str]:
    match = FM_RE.match(text)
    result: dict[str, str] = {}
    if not match:
        return result
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip('"')
    return result


def inspect_markdown(repo_root: Path) -> dict[str, object]:
    root_text = (repo_root / "todos.md").read_text(encoding="utf-8") if (repo_root / "todos.md").exists() else ""
    status_text = (repo_root / "todo-status.md").read_text(encoding="utf-8") if (repo_root / "todo-status.md").exists() else ""
    roots = {match.group("slug"): match.groupdict() for line in root_text.splitlines() if (match := ROOT_RE.match(line))}
    statuses = {match.group("slug"): match.groupdict() for line in status_text.splitlines() if (match := STATUS_RE.match(line))}
    records: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    for slug, root in roots.items():
        path = repo_root / root["file"]
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        frontmatter = _frontmatter(text)
        status = statuses.get(slug, {})
        discrepancies = {}
        for field in ("status", "owner"):
            values = {"root": root.get(field), "status": status.get(field), "frontmatter": frontmatter.get(field)}
            present = {value for value in values.values() if value}
            if len(present) > 1:
                discrepancies[field] = values
                warnings.append({"entity_id": slug, "code": f"legacy_{field}_discrepancy", "values": values})
        if status.get("execution") == "claimed" or frontmatter.get("execution") == "claimed":
            warnings.append({"entity_id": slug, "code": "legacy_claim_orphaned", "owner": status.get("owner") or root.get("owner")})
        records.append({"slug": slug, "root": root, "status_entry": status, "frontmatter": frontmatter, "markdown": text, "discrepancies": discrepancies})
    return {"root_markdown": root_text, "status_markdown": status_text, "records": records, "warnings": warnings}


def apply_markdown_import(conn: sqlite3.Connection, repo_root: Path, report: dict[str, object], revision: int) -> dict[str, object]:
    now = utc_now()
    imported = 0
    for record in report["records"]:
        root = record["root"]
        status_entry = record["status_entry"]
        frontmatter = record["frontmatter"]
        legacy_status = frontmatter.get("status") or root.get("status") or "planned"
        normalized_status = {"complete": "done", "archived": "done"}.get(legacy_status, legacy_status)
        claimed = status_entry.get("execution") == "claimed" or frontmatter.get("execution") == "claimed"
        if claimed:
            normalized_status = "attention_required"
        conn.execute(
            "INSERT INTO tasks(id,kind,title,objective,status,priority,tags_json,parallel_policy,next_action,legacy_owner,legacy_payload_json,attention_reason,created_at,updated_at,revision) "
            "VALUES(?,?,?,?,?,0,'[]','serial',?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET objective=excluded.objective,status=excluded.status,next_action=excluded.next_action,legacy_owner=excluded.legacy_owner,legacy_payload_json=excluded.legacy_payload_json,attention_reason=excluded.attention_reason,updated_at=excluded.updated_at,revision=excluded.revision",
            (
                record["slug"],
                "workstream",
                root.get("objective") or record["slug"],
                root.get("objective") or "",
                normalized_status,
                status_entry.get("next", ""),
                root.get("owner") or frontmatter.get("owner"),
                json.dumps(record, sort_keys=True),
                "legacy claimed entry requires recovery/adoption" if claimed else None,
                frontmatter.get("created_at", now),
                now,
                revision,
            ),
        )
        imported += 1
    for warning in report["warnings"]:
        conn.execute(
            "INSERT INTO migration_warnings(entity_id,code,message,payload_json,created_at) VALUES(?,?,?,?,?)",
            (warning.get("entity_id"), warning["code"], warning["code"].replace("_", " "), json.dumps(warning, sort_keys=True), now),
        )
    return {"tasks_imported": imported, "warnings_recorded": len(report["warnings"])}
