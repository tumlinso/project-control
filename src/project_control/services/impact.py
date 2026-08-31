"""Read-only architectural counterfactual synthesis and inert proposal envelopes."""

from __future__ import annotations

from typing import Any

from ..graph import ProjectGraph
from ..models import ImpactPreviewInput, ProjectSnapshot, ProposalEnvelope, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..reconcile import ProjectReconciler
from ..retrieval import page, relevance_priority
from ..workflow import workflow_view, workflow_warnings
from ..proposals import observation_preconditions


BUDGETS = {"compact": 16 * 1024, "standard": 48 * 1024, "expanded": 96 * 1024}


def impact_preview(snapshot: ProjectSnapshot, request: ImpactPreviewInput) -> ToolEnvelope:
    reconciled = ProjectReconciler(snapshot).reconcile()
    graph = ProjectGraph(snapshot, reconciled)
    explicit: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for target in request.target_entities:
        resolved = graph.resolve(target)
        if resolved["status"] == "resolved":
            item = resolved["entity"]
            explicit.append({"type": item["type"], "id": item["id"], "title": item["title"], "key": item["key"]})
        else:
            unknown.append({"target": target, "reason": resolved["reason"], "candidates": resolved.get("candidates", []), "authority_label": "missing_evidence"})
    heuristic = graph.seed_candidates(request.hypothesis, max_items=min(request.max_items, 64))
    if request.detail != "expanded":
        heuristic = [item for item in heuristic if relevance_priority(item.get("record", {})) < 3]
    proven_keys = {item["key"] for item in explicit}
    proven = [{**item, "impact_basis": "explicit_target", "authority_label": "authoritative_fact"} for item in explicit]
    possible = []
    for item in [*explicit, *heuristic]:
        key = item["key"]
        related_items = graph.related(key, max_hops=1, max_items=request.max_items)
        related_items.sort(key=lambda related: (
            relevance_priority(related.get("record", {})), str(related.get("type")), str(related.get("id")),
        ))
        for related in related_items:
            if request.detail != "expanded" and relevance_priority(related.get("record", {})) >= 3:
                continue
            identity = f"{related['type']}:{related['id']}"
            if identity in proven_keys:
                continue
            possible.append({
                "type": related["type"], "id": related["id"], "title": related["title"],
                "relation": related["relation"], "relationship_basis": related["basis"],
                "confidence": related["confidence"], "impact_basis": "structured_neighbor_of_target_or_lexical_seed",
                "authority_label": "possible_impact", "relevance": related.get("relevance"),
            })
    possible_by_id = {(item["type"], item["id"]): item for item in possible}
    possible = [possible_by_id[key] for key in sorted(possible_by_id)]

    workflow = workflow_view(snapshot)
    affected_task_ids = {item["id"] for item in [*proven, *possible] if item["type"] == "task"}
    stale_agents = []
    for agent in workflow.get("first_class_agents", []):
        if str(agent.get("task_id")) in affected_task_ids:
            stale_agents.append({
                "run_id": agent.get("run_id"), "lane_id": agent.get("lane_id"), "task_id": agent.get("task_id"),
                "context_version": agent.get("context_version"), "impact": "context_may_become_stale",
                "authority_label": "possible_impact", "basis": "authoritative_dispatch_task_matches_affected_task",
            })
    unaffected = []
    for group in workflow.get("safe_parallel_groups", []):
        members = [str(item) for item in group]
        if set(members).isdisjoint(affected_task_ids):
            unaffected.append({"task_ids": members, "authority_label": "derived_relationship", "basis": "authoritative_safe_parallel_group_disjoint_from_affected_tasks"})

    ordered = [*proven, *possible]
    query = request.model_dump(mode="json", exclude={"max_items", "detail", "include_proposal_envelope"})
    selected, pagination = page(ordered, operation="impact_preview", query=query, limit=request.max_items, cursor=None)
    selected_proven = [item for item in selected if item.get("impact_basis") == "explicit_target"]
    selected_possible = [item for item in selected if item.get("impact_basis") != "explicit_target"]
    preconditions = observation_preconditions(snapshot)
    change = request.proposed_change or {"hypothesis": request.hypothesis, "target_entities": request.target_entities}
    proposal = ProposalEnvelope.create(
        intent=request.hypothesis,
        proposed_change=change,
        observation_preconditions=preconditions,
        created_at=snapshot.observed_at,
    ) if request.include_proposal_envelope else None
    if not explicit:
        unknown.append({"target": request.hypothesis, "reason": "no_explicit_target; lexical_relationships_are_possible_not_proven", "authority_label": "missing_evidence"})

    categorized: dict[str, list[dict[str, Any]]] = {
        "tasks": [], "runs_and_lanes": [], "interfaces_consumers_decisions_invariants": [],
        "context_fragments": [], "source_paths_and_symbols": [], "tests_and_gates": [],
        "workspaces_and_patches": [], "integration": [], "performance": [],
    }
    categories = {
        "task": "tasks", "run": "runs_and_lanes", "lane": "runs_and_lanes",
        "interface": "interfaces_consumers_decisions_invariants", "decision": "interfaces_consumers_decisions_invariants",
        "invariant": "interfaces_consumers_decisions_invariants", "context_fragment": "context_fragments",
        "path": "source_paths_and_symbols", "symbol": "source_paths_and_symbols", "artifact": "source_paths_and_symbols",
        "test": "tests_and_gates", "gate": "tests_and_gates", "checkpoint": "tests_and_gates",
        "workspace": "workspaces_and_patches", "patch_artifact": "workspaces_and_patches",
        "integration": "integration", "cuda_campaign": "performance", "cuda_result": "performance",
    }
    for item in [*proven, *possible]:
        destination = categories.get(str(item.get("type")))
        if destination:
            categorized[destination].append(item)

    data = {
        "hypothesis": request.hypothesis,
        "proven_impacts": selected_proven,
        "possible_impacts": selected_possible,
        "unknown_impacts": unknown,
        "affected_by_category": {key: value[:request.max_items] for key, value in categorized.items()},
        "active_lane_context_staleness": stale_agents[:request.max_items],
        "safe_unaffected_work": unaffected[:request.max_items],
        "required_preconditions": preconditions.model_dump(mode="json"),
        "planning_guidance": {
            "skeleton": ["revalidate_observation_preconditions", "resolve_unknown_impacts", "revise_affected_interfaces_and_context", "define_tests_and_gates"],
            "questions": [item["target"] for item in unknown],
            "authority_label": "inference",
        },
        "proposal_envelope": proposal.model_dump(mode="json") if proposal else None,
        "read_only": True,
        "application_authority": False,
        "pagination": pagination,
        "observation_preconditions": preconditions.model_dump(mode="json"),
        "provenance": {"relationships": "project_control_graph_over_authoritative_reads", "operational_state": "todo_semantic_workflow", "task_semantics": "todo_semantic_state", "source": "git_identity"},
    }
    warnings = [*snapshot.warnings_for("todo"), *workflow_warnings(snapshot)]
    return envelope("impact_preview", snapshot, bounded_payload(data, BUDGETS[request.detail]), warnings=list(dict.fromkeys(warnings)))
