from __future__ import annotations

from ..models import PerformanceStatusInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload


def performance_status(snapshot: ProjectSnapshot, request: PerformanceStatusInput) -> ToolEnvelope:
    cuda = snapshot.cuda
    artifacts = cuda.get("artifacts", [])
    data = {
        "campaign": request.campaign,
        "campaigns_and_facts": artifacts,
        "latest_comparable": [item for item in artifacts if "comparable" in str(item).lower()],
        "regressions": [item for item in artifacts if "regression" in str(item).lower()],
        "improvements": [item for item in artifacts if "improvement" in str(item).lower()],
        "missing_or_stale": cuda.get("warnings", []),
        "local_worker_capacity": snapshot.local_worker,
        "host_capacity": snapshot.host if request.include_host_capacity else {"status": "not_requested"},
        "execution_performed": False,
    }
    warnings = [] if cuda.get("status") == "ok" else ["performance_evidence_unavailable"]
    return envelope("performance_status", snapshot, bounded_payload(data, 12000), warnings=warnings)
