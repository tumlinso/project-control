from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..security import redact


class LocalWorkerReadAdapter:
    """Reads an existing owner-only supervisor snapshot without probing/starting it."""

    def __init__(self, state_path: Path | None):
        self.state_path = state_path

    @staticmethod
    def current_state_path() -> Path:
        override = os.environ.get("CORE4_SUPERVISOR_RUNTIME_DIR")
        if override:
            root = Path(override).expanduser()
        elif os.environ.get("XDG_RUNTIME_DIR"):
            root = Path(os.environ["XDG_RUNTIME_DIR"]) / "core4-local-worker"
        else:
            root = Path("/tmp") / f"core4-local-worker-{os.getuid()}"
        return root / "supervisor-state.json"

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
            "observed_state": value.get("status") or ("running" if value.get("running") else "not_running"),
            "running": bool(value.get("running", False)),
            "healthy": bool(value.get("healthy", False)),
            "draining": bool(value.get("draining", False)),
            "capacity": value.get("capacity") if isinstance(value.get("capacity"), int) else None,
            "active_leases": value.get("active_leases"),
            "active_admissions": value.get("active_admissions") if isinstance(value.get("active_admissions"), int) else None,
            "slots": slots,
            "confidence": "authoritative_snapshot",
        }
