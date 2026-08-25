from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..security import SecurityError, read_bounded_text, redact


class CudaReadAdapter:
    """Reads existing campaign/evidence JSON and never invokes the CUDA controller."""

    CANDIDATE_NAMES = (
        "cuda_background_contract.json",
        "cuda-campaigns.json",
        "cuda_campaigns.json",
        "performance-facts.json",
        "performance_facts.json",
    )

    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)

    def status(self, campaign: str | None = None) -> dict[str, Any]:
        artifacts = []
        warnings: list[str] = []
        for name in self.CANDIDATE_NAMES:
            candidates = [self.root / name, self.root / "docs" / name, self.root / "benchmarks" / name]
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                relative = candidate.relative_to(self.root).as_posix()
                try:
                    value = json.loads(read_bounded_text(self.root, relative))
                except (SecurityError, json.JSONDecodeError):
                    warnings.append(f"invalid_cuda_artifact:{relative}")
                    continue
                compact = self._compact(value, campaign)
                artifacts.append({"path": relative, "data": compact})
        return {
            "status": "ok" if artifacts else "unavailable",
            "source": "existing_cuda_artifacts",
            "artifacts": artifacts,
            "warnings": warnings or ([] if artifacts else ["cuda_evidence_unavailable"]),
        }

    @staticmethod
    def _compact(value: Any, campaign: str | None) -> Any:
        value = CudaReadAdapter._sanitize(redact(value))
        if not isinstance(value, dict):
            return value
        allowed = {
            "schema_version", "format", "campaigns", "campaign_id", "id", "status",
            "facts", "baselines", "measurements", "regressions", "improvements",
            "contamination", "comparable", "observed_at", "revision", "commit",
        }
        compact = {key: item for key, item in value.items() if key in allowed}
        if campaign and isinstance(compact.get("campaigns"), list):
            compact["campaigns"] = [
                item for item in compact["campaigns"]
                if isinstance(item, dict) and str(item.get("id") or item.get("campaign_id")) == campaign
            ]
        return compact

    @staticmethod
    def _sanitize(value: Any) -> Any:
        denied = {"gpu_uuid", "uuid", "topology", "endpoint", "environment", "raw_log", "stdout", "stderr"}
        if isinstance(value, dict):
            return {key: CudaReadAdapter._sanitize(item) for key, item in value.items() if key.casefold() not in denied}
        if isinstance(value, list):
            return [CudaReadAdapter._sanitize(item) for item in value]
        return value
