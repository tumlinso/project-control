from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..security import redact


class CudaReadAdapter:
    """Reads the CUDA controller's existing project-local SQLite state read-only."""

    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        self.database = self.root / ".todo-orchestrator" / "runtime" / "background.sqlite3"

    def status(self, campaign: str | None = None) -> dict[str, Any]:
        if not self.database.is_file():
            return {"status": "unavailable", "source": "cuda_runtime_state", "campaigns": [], "facts": [], "results": [], "warnings": ["cuda_evidence_unavailable"]}
        try:
            connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True, timeout=2.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            campaigns = self._campaigns(connection, campaign)
            campaign_ids = {str(item["id"]) for item in campaigns}
            results = self._results(connection, campaign_ids)
            facts = self._facts(connection, campaign_ids)
        except (sqlite3.Error, OSError, ValueError, json.JSONDecodeError):
            return {"status": "partial", "source": "cuda_runtime_state", "campaigns": [], "facts": [], "results": [], "warnings": ["cuda_runtime_state_unavailable"]}
        finally:
            if "connection" in locals():
                connection.close()
        found = bool(campaigns or facts or results)
        warnings = [] if found else (["cuda_campaign_not_found"] if campaign else ["cuda_evidence_unavailable"])
        return {"status": "ok" if found else "unavailable", "source": "todo_runtime_background_store", "campaigns": campaigns, "facts": facts, "results": results, "warnings": warnings}

    def _campaigns(self, connection: sqlite3.Connection, selected: str | None) -> list[dict[str, Any]]:
        query = "SELECT id,state,spec_json,event_cursor,created_at,updated_at FROM background_watches"
        parameters: tuple[Any, ...] = ()
        if selected:
            query += " WHERE id=?"
            parameters = (selected,)
        query += " ORDER BY updated_at DESC LIMIT 100"
        values = []
        for row in connection.execute(query, parameters):
            spec = json.loads(row["spec_json"] or "{}")
            watch = spec.get("watch", {}) if isinstance(spec, dict) else {}
            benchmark = spec.get("benchmark", {}) if isinstance(spec, dict) else {}
            values.append(redact({
                "id": row["id"], "campaign_id": row["id"], "status": row["state"],
                "event_cursor": row["event_cursor"],
                "task_ids": self._strings(watch.get("task_ids")),
                "paths": self._strings(watch.get("paths")),
                "symbols": self._strings(watch.get("symbols")),
                "benchmark": {key: benchmark.get(key) for key in ("metric", "direction", "target") if benchmark.get(key) is not None},
                "created_at": row["created_at"], "updated_at": row["updated_at"],
            }))
        return values

    def _results(self, connection: sqlite3.Connection, campaign_ids: set[str]) -> list[dict[str, Any]]:
        if not campaign_ids:
            return []
        placeholders = ",".join("?" for _ in campaign_ids)
        query = f"""SELECT r.id,r.job_id,r.status,r.classification,r.severity,r.valid,r.contaminated,r.summary_json,r.created_at,
                           j.watch_id,j.task_id,j.todo_revision,j.kind
                    FROM background_results r JOIN background_jobs j ON j.id=r.job_id
                    WHERE j.watch_id IN ({placeholders}) ORDER BY r.created_at DESC LIMIT 200"""
        values = []
        for row in connection.execute(query, tuple(campaign_ids)):
            try:
                summary = json.loads(row["summary_json"] or "{}")
            except json.JSONDecodeError:
                summary = {}
            statistics = summary.get("statistics", {}) if isinstance(summary, dict) else {}
            baseline = summary.get("baseline", {}) if isinstance(summary, dict) else {}
            values.append(redact({
                "id": row["id"], "job_id": row["job_id"], "campaign_id": row["watch_id"],
                "task_id": row["task_id"], "todo_revision": row["todo_revision"], "kind": row["kind"],
                "status": row["status"], "classification": row["classification"], "severity": row["severity"],
                "valid": bool(row["valid"]), "contaminated": bool(row["contaminated"]),
                "measurement": {
                    "metric": summary.get("metric") if isinstance(summary, dict) else None,
                    "direction": summary.get("direction") if isinstance(summary, dict) else None,
                    "target": summary.get("target") if isinstance(summary, dict) else None,
                    "comparison_percent": summary.get("comparison_percent") if isinstance(summary, dict) else None,
                    "statistics": {key: statistics.get(key) for key in ("median", "mean", "minimum", "maximum", "samples", "unit") if isinstance(statistics, dict) and statistics.get(key) is not None},
                    "baseline": {key: baseline.get(key) for key in ("median", "valid") if isinstance(baseline, dict) and baseline.get(key) is not None},
                },
                "created_at": row["created_at"],
            }))
        return values

    def _facts(self, connection: sqlite3.Connection, campaign_ids: set[str]) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        for campaign_id in campaign_ids:
            row = connection.execute("SELECT value FROM background_meta WHERE key=?", (f"performance-facts:{campaign_id}",)).fetchone()
            if row is None:
                continue
            value = json.loads(row["value"])
            records = value if isinstance(value, list) else [value]
            for record in records:
                if isinstance(record, dict):
                    facts.append(self._compact_fact(record, campaign_id))
        return facts[:200]

    @staticmethod
    def _compact_fact(record: dict[str, Any], campaign_id: str) -> dict[str, Any]:
        measurement = record.get("measurement", {}) if isinstance(record.get("measurement"), dict) else {}
        statistics = measurement.get("statistics", {}) if isinstance(measurement.get("statistics"), dict) else {}
        source = record.get("source", {}) if isinstance(record.get("source"), dict) else {}
        return redact({
            "fact_id": record.get("fact_id"), "campaign_id": record.get("campaign_id") or campaign_id,
            "role": record.get("role"), "classification": record.get("classification"),
            "compatibility": record.get("compatibility"),
            "source": {key: source.get(key) for key in ("fingerprint", "commit", "dirty") if source.get(key) is not None},
            "measurement": {
                "metric": measurement.get("metric"), "direction": measurement.get("direction"),
                "statistics": {key: statistics.get(key) for key in ("median", "mean", "minimum", "maximum", "samples", "unit") if statistics.get(key) is not None},
                "uncontaminated": measurement.get("uncontaminated"),
            },
            "created_at": record.get("created_at"),
        })

    @staticmethod
    def _strings(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)][:100]
