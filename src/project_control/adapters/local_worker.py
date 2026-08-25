from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..security import redact


class LocalWorkerReadAdapter:
    """Reads an existing owner-only supervisor snapshot without probing/starting it."""

    def __init__(self, state_path: Path | None):
        self.state_path = state_path

    def status(self) -> dict[str, Any]:
        if self.state_path is None or not self.state_path.is_file():
            return {"status": "unavailable", "source": "supervisor_state", "warnings": ["local_worker_state_unavailable"]}
        path = self.state_path.resolve(strict=True)
        if path.stat().st_size > 1024 * 1024:
            return {"status": "partial", "source": "supervisor_state", "warnings": ["local_worker_state_oversized"]}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "partial", "source": "supervisor_state", "warnings": ["local_worker_state_invalid"]}
        slots = []
        for index, slot in enumerate(value.get("slots", [])):
            if not isinstance(slot, dict):
                continue
            slots.append({
                "slot": index,
                "state": slot.get("state", "unknown"),
                "leased": bool(slot.get("leased", False)),
            })
        return {
            "status": "ok",
            "source": "existing_supervisor_state",
            "observed_state": value.get("status", "observable"),
            "active_leases": value.get("active_leases"),
            "slots": slots,
            "confidence": "authoritative_snapshot",
        }
