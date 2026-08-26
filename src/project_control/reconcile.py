"""Shared transient reconciliation and relevance ranking."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .lifecycle import is_terminal_historical
from .models import ProjectSnapshot


RELEVANCE_SCORE = {
    "current_attention": 800,
    "current": 700,
    "reference": 400,
    "unknown": 300,
    "historical": 100,
    "superseded": 0,
}


def relevance_score(item: dict[str, Any]) -> tuple[int, int, str]:
    relevance = str(item.get("relevance") or item.get("current_relevance") or "unknown")
    state = str(item.get("effective_state") or "")
    state_bonus = {"blocked": 90, "active": 80, "ready": 70, "done": 30}.get(state, 0)
    return (RELEVANCE_SCORE.get(relevance, 300) + state_bonus, int(item.get("priority", 0)), str(item.get("id", "")))


def rank_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (-relevance_score(item)[0], -relevance_score(item)[1], relevance_score(item)[2]))


def paths_overlap(left: str, right: str) -> bool:
    a, b = left.rstrip("/"), right.rstrip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


@dataclass
class ReconciledProject:
    tasks: dict[str, dict[str, Any]]
    checkpoints: list[dict[str, Any]]
    gates: list[dict[str, Any]]
    programs: list[dict[str, Any]]
    ready: list[dict[str, Any]]
    active: list[dict[str, Any]]
    blocked: list[dict[str, Any]]
    architectural_attention: list[dict[str, Any]]
    validation_attention: list[dict[str, Any]]
    completed: list[dict[str, Any]]
    performance: dict[str, list[dict[str, Any]]]
    historical_counts: dict[str, int]
    contradictions: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    semantic_available: bool = False


class ProjectReconciler:
    def __init__(self, snapshot: ProjectSnapshot):
        self.snapshot = snapshot

    def _tasks(self) -> tuple[dict[str, dict[str, Any]], bool]:
        semantic = self.snapshot.todo_semantic
        records = semantic.get("tasks", []) if isinstance(semantic, dict) else []
        if isinstance(semantic, dict) and "revision" in semantic and isinstance(records, list):
            return {
                str(item["id"]): {
                    **item,
                    "relevance": item.get("current_relevance", "unknown"),
                    "relevance_reason": ",".join(item.get("reason_codes", [])) or "todo_semantic",
                }
                for item in records if isinstance(item, dict) and item.get("id")
            }, True

        ready_ids = {
            str(item.get("id") or item.get("task_id"))
            for item in self.snapshot.todo_status.get("ready", []) if isinstance(item, dict)
        }
        active_ids = {
            str(item.get("task_id"))
            for item in self.snapshot.todo_status.get("active_claims", []) if isinstance(item, dict)
        }
        dependencies: dict[str, list[str]] = defaultdict(list)
        for row in self.snapshot.todo_tables.get("task_dependencies", []):
            target = row.get("prerequisite_task_id") or row.get("checkpoint_id") or row.get("interface_id") or row.get("barrier_id")
            if target:
                dependencies[str(row.get("task_id"))].append(str(target))
        tasks: dict[str, dict[str, Any]] = {}
        for raw in self.snapshot.todo_tables.get("tasks", []):
            task_id = str(raw.get("id"))
            status = str(raw.get("status", "planned"))
            terminal = is_terminal_historical(raw)
            if status == "superseded":
                effective, relevance = "superseded", "superseded"
            elif terminal:
                effective, relevance = "done", "reference"
            elif task_id in active_ids or status == "in_progress":
                effective, relevance = "active", "current"
            elif task_id in ready_ids:
                effective, relevance = "ready", "current"
            elif status == "blocked" or dependencies.get(task_id):
                effective, relevance = "blocked", "current_attention"
            else:
                effective, relevance = "planned", "current"
            tasks[task_id] = {
                **raw,
                "effective_state": effective,
                "terminal": terminal,
                "frontier_eligible": not terminal and raw.get("kind") != "epic",
                "attention_eligible": effective == "blocked",
                "current_program_eligible": relevance not in {"superseded", "historical"},
                "relevance": relevance,
                "relevance_reason": "legacy_raw_status_fallback",
                "dependencies": [
                    {"type": "legacy", "entity_id": value} for value in dependencies.get(task_id, [])
                ],
            }
        return tasks, False

    def _performance(self, tasks: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        campaigns = [item for item in self.snapshot.cuda.get("campaigns", []) if isinstance(item, dict)]
        campaign_tasks = {
            str(item.get("id")): [str(value) for value in item.get("task_ids", []) if isinstance(value, str)]
            for item in campaigns
        }

        def linked_ids(item: dict[str, Any]) -> list[str]:
            values = []
            if item.get("task_id"):
                values.append(str(item["task_id"]))
            values.extend(campaign_tasks.get(str(item.get("campaign_id") or item.get("id")), []))
            return list(dict.fromkeys(values))

        def current_related(ids: list[str]) -> bool:
            return bool(ids) and any(
                tasks.get(task_id, {}).get("current_program_eligible", False)
                and tasks.get(task_id, {}).get("effective_state") not in {"superseded", "failed", "canceled"}
                for task_id in ids
            )

        projected_campaigns = []
        for item in campaigns:
            ids = linked_ids(item)
            linked = [tasks.get(value, {}) for value in ids]
            if linked and all(task.get("effective_state") == "superseded" for task in linked):
                lifecycle, relevance, reason = "superseded", "superseded", "all_linked_tasks_superseded"
            elif any(not task.get("terminal", True) for task in linked):
                lifecycle, relevance, reason = "active_watch" if item.get("status") == "armed" else "current", "current", "linked_current_task"
            elif current_related(ids):
                lifecycle, relevance, reason = "reference_baseline", "reference", "linked_completed_current_program"
            else:
                lifecycle, relevance, reason = "historical", "historical", "no_current_task_link"
            projected_campaigns.append({**item, "lifecycle": lifecycle, "relevance": relevance, "relevance_reason": reason})

        facts = [item for item in self.snapshot.cuda.get("facts", []) if isinstance(item, dict)]
        results = [item for item in self.snapshot.cuda.get("results", []) if isinstance(item, dict)]
        projected_evidence = []
        for item in [*facts, *results]:
            ids = linked_ids(item)
            source = item.get("source", {}) if isinstance(item.get("source"), dict) else {}
            source_commit = source.get("commit")
            source_compatible = not source_commit or any(
                identity.commit == source_commit for identity in self.snapshot.repositories.values()
            )
            measurement = item.get("measurement", {}) if isinstance(item.get("measurement"), dict) else {}
            compatible = (
                item.get("compatibility") in {"compatible", True, None}
                and item.get("valid") is not False
                and not item.get("contaminated", False)
                and measurement.get("uncontaminated") is not False
                and source_compatible
            )
            current = current_related(ids) and compatible
            projected_evidence.append({
                **item,
                "linked_task_ids": ids,
                "lifecycle": "current" if current else "historical" if ids else "noncomparable",
                "relevance": "current" if current else "historical",
                "relevance_reason": "current_compatible_linked_task" if current else "not_current_comparable",
            })
        current_evidence = [item for item in projected_evidence if item["relevance"] == "current"]
        historical_evidence = [item for item in projected_evidence if item["relevance"] != "current"]
        return {
            "campaigns": rank_items(projected_campaigns),
            "current_evidence": rank_items(current_evidence),
            "historical_evidence": rank_items(historical_evidence),
            "current_regressions": [item for item in rank_items(current_evidence) if item.get("classification") == "material-regression"],
            "historical_regressions": [item for item in rank_items(historical_evidence) if item.get("classification") == "material-regression"],
            "current_improvements": [item for item in rank_items(current_evidence) if item.get("classification") in {"material-improvement", "improvement"}],
            "active_watches": [item for item in projected_campaigns if item["lifecycle"] == "active_watch"],
        }

    def reconcile(self) -> ReconciledProject:
        tasks, semantic_available = self._tasks()
        semantic = self.snapshot.todo_semantic if semantic_available else {}
        current_ids = {
            task_id for task_id, item in tasks.items()
            if item.get("current_program_eligible") and item.get("effective_state") not in {"superseded", "historical"}
            and not item.get("terminal")
        }
        dependencies = self.snapshot.todo_tables.get("task_dependencies", [])
        current_checkpoint_refs = {
            str(item.get("checkpoint_id")) for item in dependencies
            if item.get("checkpoint_id") and str(item.get("task_id")) in current_ids
        }
        current_interface_refs = {
            str(item.get("interface_id")) for item in dependencies
            if item.get("interface_id") and str(item.get("task_id")) in current_ids
        }
        current_interface_refs.update(
            str(item.get("interface_id"))
            for item in self.snapshot.todo_tables.get("interface_consumers", [])
            if item.get("interface_id") and str(item.get("task_id")) in current_ids
        )
        if semantic_available:
            checkpoints = [dict(item) for item in semantic.get("checkpoints", [])]
            gates = [dict(item) for item in semantic.get("gates", [])]
        else:
            checkpoints = []
            for item in self.snapshot.todo_tables.get("checkpoints", []):
                linked_current = str(item.get("task_id")) in current_ids or str(item.get("id")) in current_checkpoint_refs
                pending = item.get("state") != "reached"
                checkpoints.append({
                    **item,
                    "effective_state": "current_pending" if pending and linked_current else "reached" if not pending else "historical_stale",
                    "attention_eligible": bool(pending and linked_current),
                    "relevance": "current_attention" if pending and linked_current else "reference" if not pending else "historical",
                    "relevance_reason": "legacy_raw_state_fallback",
                })
            current_checkpoint_ids = {str(item.get("id")) for item in checkpoints if item.get("attention_eligible")}
            gates = []
            for item in self.snapshot.todo_tables.get("gates", []):
                linked_current = str(item.get("task_id")) in current_ids or str(item.get("checkpoint_id")) in current_checkpoint_ids
                failed = bool(item.get("required") and not item.get("valid"))
                gates.append({
                    **item,
                    "effective_state": "current_failed" if failed and linked_current else "current_valid" if item.get("valid") else "historical_invalid",
                    "attention_eligible": bool(failed and linked_current),
                    "relevance": "current_attention" if failed and linked_current else "reference" if item.get("valid") else "historical",
                    "relevance_reason": "legacy_raw_state_fallback",
                })
        for item in checkpoints + gates:
            item.setdefault("relevance", "historical" if str(item.get("effective_state", "")).startswith("historical") else "current_attention" if item.get("attention_eligible") else "reference")
            item.setdefault("relevance_reason", ",".join(item.get("reason_codes", [])) or "todo_semantic")

        ready = rank_items([item for item in tasks.values() if item.get("effective_state") == "ready"])
        active = rank_items([item for item in tasks.values() if item.get("effective_state") == "active"])
        blocked = rank_items([item for item in tasks.values() if item.get("effective_state") == "blocked"])
        completed = rank_items([item for item in tasks.values() if item.get("effective_state") == "done"])
        interfaces = [
            {
                **item,
                "effective_state": "current_pending",
                "attention_eligible": True,
                "relevance": "current_attention",
                "relevance_reason": "current_owner_or_consumer",
            }
            for item in self.snapshot.todo_tables.get("interfaces", [])
            if item.get("state") != "frozen"
            and (str(item.get("owner_task_id")) in current_ids or str(item.get("id")) in current_interface_refs)
        ]
        architectural_attention = rank_items([item for item in checkpoints if item.get("attention_eligible")] + interfaces)
        validation_attention = rank_items([item for item in gates if item.get("attention_eligible")])
        performance = self._performance(tasks)
        derived_historical_counts = {
            "tasks": sum(1 for item in tasks.values() if item.get("relevance") in {"historical", "superseded"}),
            "checkpoints": sum(1 for item in checkpoints if item.get("relevance") == "historical"),
            "gates": sum(1 for item in gates if item.get("relevance") == "historical"),
        }
        semantic_counts = semantic.get("historical_counts", {}) if semantic_available else {}
        historical_counts = {
            key: int(semantic_counts.get(key, value)) for key, value in derived_historical_counts.items()
        }
        historical_counts["performance_evidence"] = len(performance["historical_evidence"])
        contradictions = list(semantic.get("contradictions", [])) if semantic_available else []
        warnings = []
        if not semantic_available:
            warnings.append("todo_semantic_unavailable")
        if any(historical_counts.values()):
            warnings.append("stale_legacy_state_filtered")
        if contradictions:
            warnings.append("cross_authority_inconsistency")
        return ReconciledProject(
            tasks=tasks,
            checkpoints=checkpoints,
            gates=gates,
            programs=list(semantic.get("programs", [])),
            ready=ready,
            active=active,
            blocked=blocked,
            architectural_attention=architectural_attention,
            validation_attention=validation_attention,
            completed=completed,
            performance=performance,
            historical_counts=historical_counts,
            contradictions=contradictions,
            warnings=warnings,
            semantic_available=semantic_available,
        )
