"""Multi-seed architectural orientation without mutation or single-entity collapse."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..graph import ProjectGraph
from ..models import ArchitectureContextInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..reconcile import ProjectReconciler
from ..retrieval import authority_label, is_current, page
from ..workflow import workflow_view, workflow_warnings


BUDGETS = {"compact": 16 * 1024, "standard": 48 * 1024, "expanded": 128 * 1024}


def _compact_entity(item: dict[str, Any]) -> dict[str, Any]:
    record = item.get("record", {}) if isinstance(item.get("record"), dict) else {}
    return {
        "type": item.get("type"), "id": item.get("id"), "title": item.get("title"),
        "theme": item.get("theme"), "authority_label": item.get("authority_label"),
        "match_basis": item.get("match_basis"), "query_coverage": item.get("query_coverage"),
        "matched_terms": item.get("matched_terms", []), "relevance": item.get("relevance"),
        "state": record.get("effective_state") or record.get("state") or record.get("status"),
        "objective": record.get("objective"), "summary": record.get("summary"),
        "path": record.get("path"), "version": record.get("version"),
    }


def architecture_context(snapshot: ProjectSnapshot, request: ArchitectureContextInput) -> ToolEnvelope:
    reconciled = ProjectReconciler(snapshot).reconcile()
    graph = ProjectGraph(snapshot, reconciled)
    seed_limit = min(max(request.max_items, 8), 96)
    seeds = [item for item in graph.seed_candidates(request.question, max_items=seed_limit) if is_current(item.get("record", {}), request.scope)]
    if request.inclusion_categories:
        included = set(request.inclusion_categories)
        seeds = [item for item in seeds if item["theme"] in included or item["type"] in included]
    expanded = [item for item in graph.expand_seeds(seeds, max_items=min(request.max_items * 3, 500)) if is_current(item.get("record", {}), request.scope)]

    clusters: dict[str, dict[str, Any]] = defaultdict(lambda: {"seeds": [], "relationships": []})
    for item in seeds:
        clusters[item["theme"]]["seeds"].append(_compact_entity(item))
    for item in expanded:
        theme = next((seed["theme"] for seed in seeds if seed["id"] == item["seed"]["id"] and seed["type"] == item["seed"]["type"]), item["seed"]["theme"])
        clusters[theme]["relationships"].append({
            "from": item["seed"], "to": {"type": item["type"], "id": item["id"], "title": item["title"]},
            "relation": item["relation"], "basis": item["basis"], "confidence": item["confidence"],
            "authority_label": "derived_relationship", "relevance": item.get("relevance"),
        })

    ordered = [
        {"theme": theme, "seeds": clusters[theme]["seeds"], "relationships": clusters[theme]["relationships"]}
        for theme in sorted(clusters)
    ]
    query = {
        "question": request.question, "repository": request.repository, "worktree_id": request.worktree_id,
        "scope": request.scope, "inclusion_categories": request.inclusion_categories,
    }
    selected, pagination = page(ordered, operation="architecture_context", query=query, limit=request.max_items, cursor=request.continuation_cursor)

    workflow = workflow_view(snapshot)
    exact = graph.resolve(request.question)
    missing: list[dict[str, Any]] = []
    present_themes = {item["theme"] for item in ordered}
    for theme in ("architecture", "planning", "source", "validation", "workflow"):
        if theme not in present_themes:
            missing.append({"category": theme, "authority_label": "missing_evidence", "reason": "no_relevant_observed_candidate"})
    if exact["status"] != "resolved":
        missing.append({"category": "exact_subject", "authority_label": "missing_evidence", "reason": exact["reason"]})

    run_commitments = []
    for run in workflow.get("runs", []) if workflow.get("available") else []:
        charter = run.get("charter") or run.get("run_charter")
        if charter or run.get("active_charter_version"):
            run_commitments.append({
                "run_id": run.get("id"), "charter_version": run.get("active_charter_version"),
                "charter": charter, "authority_label": "authoritative_fact", "source": "todo_semantic_workflow",
            })

    risks = [
        {"kind": "contradiction", "record": item, "authority_label": "authoritative_fact", "source": "todo_semantic_state"}
        for item in reconciled.contradictions[:request.max_items]
    ]
    if not workflow.get("available"):
        risks.append({"kind": "missing_operational_authority", "reason": workflow.get("source_reason") or workflow.get("reason"), "authority_label": "missing_evidence"})

    observed_entities: dict[tuple[str, str], dict[str, Any]] = {}
    for item in seeds:
        observed_entities[(str(item["type"]), str(item["id"]))] = {
            **_compact_entity(item), "record": item.get("record", {}),
        }
    for item in expanded:
        observed_entities.setdefault((str(item["type"]), str(item["id"])), {
            "type": item["type"], "id": item["id"], "title": item["title"],
            "relevance": item.get("relevance"), "authority_label": "derived_relationship",
            "record": item.get("record", {}),
        })

    def entities(*types: str) -> list[dict[str, Any]]:
        return [
            item for (kind, _), item in sorted(observed_entities.items()) if kind in types
        ][:request.max_items]

    data = {
        "question": request.question,
        "retrieval_basis": {
            "algorithm": "multi_seed_exact_lexical_then_structured_graph_expansion",
            "single_subject_resolution": exact["status"],
            "ranking_dimensions": ["source_authority", "current_relevance", "query_coverage", "relationship_confidence", "architectural_centrality", "freshness"],
            "labels": ["authoritative_fact", "source_authority", "performance_authority", "derived_relationship", "heuristic_relevance", "inference", "missing_evidence"],
            "deterministic": True,
        },
        "clusters": selected,
        "architectural_commitments": entities("invariant", "interface", "decision", "run"),
        "decisions_and_rationale": entities("decision"),
        "rejected_alternatives": [
            item for item in entities("decision")
            if str(item.get("record", {}).get("state") or item.get("record", {}).get("status")).casefold() in {"rejected", "superseded"}
        ],
        "interfaces_and_consumers": entities("interface"),
        "source_realization": entities("path", "symbol", "git_commit", "artifact"),
        "tests_gates_and_evidence": entities("gate", "checkpoint", "evidence"),
        "performance_assumptions": entities("cuda_campaign", "cuda_result"),
        "run_and_context_commitments": run_commitments[:request.max_items],
        "boundaries_and_non_goals": [
            {"value": item, "authority_label": "authoritative_fact", "source": "run_charter"}
            for run in workflow.get("runs", []) if isinstance(run, dict)
            for item in (run.get("boundaries") or run.get("non_goals") or [])
        ][:request.max_items],
        "active_coordination": {
            "active_run_id": workflow.get("active_run_id"),
            "first_class_agents": workflow.get("first_class_agents", [])[:request.max_items],
            "blocking_messages": workflow.get("blocking_messages", [])[:request.max_items],
            "rendezvous": workflow.get("rendezvous", [])[:request.max_items],
            "integration_queue": workflow.get("integration_queue", [])[:request.max_items],
            "authority_label": "authoritative_fact" if workflow.get("available") else "missing_evidence",
        },
        "worktree_and_integration_state": {
            "repositories": {
                alias: {
                    "commit": identity.commit,
                    "dirty": identity.dirty,
                    "worktree_ids": sorted(identity.worktrees),
                }
                for alias, identity in sorted(snapshot.repositories.items())
                if request.repository is None or alias == request.repository
            },
            "requested_worktree_id": request.worktree_id,
            "workspaces": [
                {key: item.get(key) for key in ("id", "run_id", "lane_id", "repository", "branch", "mode", "state", "integration_task_id") if item.get(key) is not None}
                for item in workflow.get("workspaces", [])[:request.max_items] if isinstance(item, dict)
            ],
            "patch_artifacts": workflow.get("patch_artifacts", [])[:request.max_items],
            "integration_queue": workflow.get("integration_queue", [])[:request.max_items],
        },
        "risks_and_contradictions": risks,
        "open_assumptions_or_missing_evidence": missing,
        "next_inspection_targets": [
            {"kind": item["type"], "target": item["id"], "reason": item["match_basis"]}
            for item in seeds[: min(12, request.max_items)]
        ],
        "observation_preconditions": snapshot.observation_preconditions().model_dump(mode="json"),
        "provenance": {
            "workflow": "todo_semantic_workflow",
            "task_semantics": "todo_semantic_state",
            "durable_enrichment": "todo_readonly_export",
            "source": "git_and_working_tree_identity",
            "provider_components": {key: value.model_dump(mode="json") for key, value in sorted(snapshot.component_authority.items())},
        },
        "pagination": pagination,
    }
    warnings = [*snapshot.warnings_for("todo"), *reconciled.warnings, *workflow_warnings(snapshot)]
    return envelope("architecture_context", snapshot, bounded_payload(data, BUDGETS[request.detail]), warnings=list(dict.fromkeys(warnings)))
