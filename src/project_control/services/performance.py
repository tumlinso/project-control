from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..adapters.git import GitReadAdapter
from ..models import PerformanceStatusInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..reconcile import ProjectReconciler
from ..workflow import workflow_summary

if TYPE_CHECKING:
    from ..config import ProjectControlConfig
    from ..registry import WorkspaceRegistry


def _registered_architecture_evidence(snapshot: ProjectSnapshot, config: "ProjectControlConfig | None") -> tuple[list[dict], list[str]]:
    artifacts = snapshot.todo_tables.get("task_artifacts", [])
    relevant = [
        item for item in artifacts
        if str(item.get("path", "")).endswith("ce_arch_92_v100_summary.json")
    ]
    if not relevant or config is None:
        has_ce_arch_92 = any(str(item.get("id")) == "CE-ARCH-92" for item in snapshot.todo_semantic.get("tasks", []))
        return [], ["current_architecture_evidence_unregistered"] if has_ce_arch_92 and not relevant else []
    from ..registry import WorkspaceRegistry

    registry = WorkspaceRegistry(config)
    workspace = registry.workspace(snapshot.workspace_id)
    values: list[dict] = []
    warnings: list[str] = []
    for artifact in relevant:
        path = str(artifact["path"])
        parsed = None
        for alias in sorted(workspace.repositories):
            identity = snapshot.repositories.get(alias)
            if identity is None:
                continue
            try:
                candidate = json.loads(GitReadAdapter(registry.repository(snapshot.workspace_id, alias).root).show_text(identity.commit, path))
            except Exception:
                continue
            if isinstance(candidate, dict) and candidate.get("schema") == "CE-ARCH-92-SUMMARY/1":
                parsed = {
                    "schema": candidate["schema"],
                    "task_id": artifact.get("task_id"),
                    "repository": alias,
                    "path": path,
                    "commit": identity.commit,
                    "record_count": candidate.get("record_count"),
                    "trace_count": candidate.get("trace_count"),
                    "widths": candidate.get("widths", []),
                    "timing_basis": candidate.get("timing_basis"),
                    "migration_exit_evidence": candidate.get("migration_exit_evidence", {}),
                    "winners": candidate.get("winners", [])[:16],
                    "relevance": "current",
                    "relevance_reason": "registered_task_artifact_at_current_git_commit",
                }
                break
        if parsed:
            values.append(parsed)
        else:
            warnings.append("current_architecture_evidence_unavailable")
    return values, list(dict.fromkeys(warnings))


def performance_status(snapshot: ProjectSnapshot, request: PerformanceStatusInput, config: "ProjectControlConfig | None" = None) -> ToolEnvelope:
    cuda = snapshot.cuda
    facts = [item for item in cuda.get("facts", []) if isinstance(item, dict)]
    results = [item for item in cuda.get("results", []) if isinstance(item, dict)]
    structured_evidence = [*facts, *results]
    reconciled = ProjectReconciler(snapshot).reconcile()
    retired_evidence_ids = {
        str(item.get("id") or item.get("fact_id"))
        for item in reconciled.performance["historical_evidence"]
        if item.get("linked_task_ids") and (item.get("id") or item.get("fact_id"))
    }
    legacy_evidence = [
        item for item in structured_evidence
        if str(item.get("id") or item.get("fact_id")) not in retired_evidence_ids
    ]
    regressions = [item for item in legacy_evidence if item.get("classification") == "material-regression"]
    improvements = [
        item for item in legacy_evidence
        if item.get("classification") in {"material-improvement", "improvement"}
    ]
    comparable = [
        item for item in legacy_evidence
        if (
            item.get("compatibility") in {"compatible", True}
            and (item.get("measurement", {}) if isinstance(item.get("measurement"), dict) else {}).get("uncontaminated") is not False
        )
        or (item.get("classification") in {"healthy", "no-material-change"} and not item.get("contaminated"))
    ]
    registered, registered_warnings = _registered_architecture_evidence(snapshot, config)
    current = reconciled.performance["current_evidence"]
    current_regressions = reconciled.performance["current_regressions"]
    current_improvements = reconciled.performance["current_improvements"]
    historical_measurements = reconciled.performance["historical_evidence"]
    workflow = workflow_summary(snapshot)
    lane_by_task = {
        str(item.get("task_id")): {"run_id": item.get("run_id"), "lane_id": item.get("lane_id"), "context_version": item.get("context_version")}
        for item in workflow.get("first_class_agents", []) if item.get("task_id")
    }

    def source_context(item: dict) -> dict:
        source = item.get("source", {}) if isinstance(item.get("source"), dict) else {}
        task_ids = item.get("linked_task_ids", [])
        return {
            "source_commit": source.get("commit"),
            "source_worktree_id": source.get("worktree_id"),
            "workflow_links": [lane_by_task[task_id] for task_id in task_ids if task_id in lane_by_task],
            "context_relevance": item.get("relevance", "unknown"),
        }
    data = {
        "campaign": request.campaign,
        "current_architectural_evidence": [*registered, *current[:20]],
        "latest_current_compatible_measurements": current[:20],
        "current_material_regressions": current_regressions[:20],
        "current_improvements": current_improvements[:20],
        "unmeasured_assumptions": registered_warnings,
        "stale_or_superseded_evidence_counts": {
            "campaigns": sum(1 for item in reconciled.performance["campaigns"] if item.get("relevance") in {"historical", "superseded"}),
            "evidence": len(reconciled.performance["historical_evidence"]),
        },
        "active_watches": reconciled.performance["active_watches"][:20],
        "latest_comparable": comparable[:20],
        "regressions": regressions[:20],
        "improvements": improvements[:20],
        "historical_measurements": historical_measurements[:40],
        "missing_or_stale": cuda.get("warnings", []),
        "local_worker_capacity": snapshot.local_worker,
        "host_capacity": snapshot.host if request.include_host_capacity else {"status": "not_requested"},
        "execution_performed": False,
        "source_and_workflow_context": {
            str(item.get("id") or item.get("fact_id")): source_context(item)
            for item in [*current, *historical_measurements]
            if item.get("id") or item.get("fact_id")
        },
        "repository_source_identities": {
            alias: {
                "commit": identity.commit,
                "worktrees": {worktree_id: {"head": worktree.head, "dirty": worktree.dirty} for worktree_id, worktree in identity.worktrees.items()},
            }
            for alias, identity in snapshot.repositories.items()
        },
        "observation_preconditions": snapshot.observation_preconditions().model_dump(mode="json"),
    }
    if request.detail == "expanded":
        data["historical_campaigns_and_evidence"] = {
            "campaigns": reconciled.performance["campaigns"],
            "evidence": reconciled.performance["historical_evidence"],
        }
    warnings = [*snapshot.warnings_for("cuda", "worker", *("host",) if request.include_host_capacity else ()), *registered_warnings]
    if cuda.get("status") != "ok":
        warnings.append("performance_evidence_unavailable")
    return envelope("performance_status", snapshot, bounded_payload(data, 12000 if request.detail != "expanded" else 20000), warnings=list(dict.fromkeys(warnings)))
