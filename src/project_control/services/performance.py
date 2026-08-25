from __future__ import annotations

from ..models import PerformanceStatusInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload


def performance_status(snapshot: ProjectSnapshot, request: PerformanceStatusInput) -> ToolEnvelope:
    cuda = snapshot.cuda
    campaigns = cuda.get("campaigns", [])
    facts = cuda.get("facts", [])
    results = cuda.get("results", [])
    regressions = [item for item in [*facts, *results] if item.get("classification") == "material-regression"]
    improvements = [item for item in [*facts, *results] if item.get("classification") in {"material-improvement", "improvement"}]
    comparable = [
        item for item in facts
        if item.get("compatibility") in {"compatible", True}
        and item.get("measurement", {}).get("uncontaminated") is not False
    ]
    comparable.extend(item for item in results if item.get("classification") in {"healthy", "no-material-change"} and not item.get("contaminated"))
    data = {
        "campaign": request.campaign,
        "campaigns_and_facts": {"campaigns": campaigns, "facts": facts, "results": results},
        "latest_comparable": comparable[:20],
        "regressions": regressions[:20],
        "improvements": improvements[:20],
        "missing_or_stale": cuda.get("warnings", []),
        "local_worker_capacity": snapshot.local_worker,
        "host_capacity": snapshot.host if request.include_host_capacity else {"status": "not_requested"},
        "execution_performed": False,
    }
    warnings = snapshot.warnings_for("cuda", "worker", *("host",) if request.include_host_capacity else ())
    if cuda.get("status") != "ok":
        warnings.append("performance_evidence_unavailable")
    return envelope("performance_status", snapshot, bounded_payload(data, 12000), warnings=warnings)
