from __future__ import annotations

from ..lifecycle import is_terminal_historical
from ..models import PerformanceStatusInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload


def performance_status(snapshot: ProjectSnapshot, request: PerformanceStatusInput) -> ToolEnvelope:
    cuda = snapshot.cuda
    campaigns = cuda.get("campaigns", [])
    facts = cuda.get("facts", [])
    results = cuda.get("results", [])
    tasks = {str(item.get("id")): item for item in snapshot.todo_tables.get("tasks", []) if item.get("id")}
    campaign_tasks = {
        str(item.get("id")): [str(task_id) for task_id in item.get("task_ids", [])]
        for item in campaigns if item.get("id")
    }

    def linked_task_ids(item):
        values = []
        if item.get("task_id"):
            values.append(str(item["task_id"]))
        values.extend(campaign_tasks.get(str(item.get("campaign_id") or item.get("id")), []))
        return list(dict.fromkeys(values))

    def historical(item):
        linked = linked_task_ids(item)
        known = [tasks[task_id] for task_id in linked if task_id in tasks]
        return bool(known) and len(known) == len(linked) and all(is_terminal_historical(task) for task in known)

    current_facts = [item for item in facts if not historical(item)]
    current_results = [item for item in results if not historical(item)]
    historical_measurements = [item for item in [*facts, *results] if historical(item)]
    regressions = [item for item in [*current_facts, *current_results] if item.get("classification") == "material-regression"]
    improvements = [item for item in [*current_facts, *current_results] if item.get("classification") in {"material-improvement", "improvement"}]
    comparable = [
        item for item in current_facts
        if item.get("compatibility") in {"compatible", True}
        and item.get("measurement", {}).get("uncontaminated") is not False
    ]
    comparable.extend(item for item in current_results if item.get("classification") in {"healthy", "no-material-change"} and not item.get("contaminated"))
    data = {
        "campaign": request.campaign,
        "campaigns_and_facts": {"campaigns": campaigns, "facts": facts, "results": results},
        "latest_comparable": comparable[:20],
        "regressions": regressions[:20],
        "improvements": improvements[:20],
        "historical_measurements": historical_measurements[:40],
        "missing_or_stale": cuda.get("warnings", []),
        "local_worker_capacity": snapshot.local_worker,
        "host_capacity": snapshot.host if request.include_host_capacity else {"status": "not_requested"},
        "execution_performed": False,
    }
    warnings = snapshot.warnings_for("cuda", "worker", *("host",) if request.include_host_capacity else ())
    if cuda.get("status") != "ok":
        warnings.append("performance_evidence_unavailable")
    return envelope("performance_status", snapshot, bounded_payload(data, 12000), warnings=warnings)
