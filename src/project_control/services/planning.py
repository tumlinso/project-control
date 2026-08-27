from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..adapters.git import GitReadAdapter
from ..adapters.todo import TodoReadAdapter
from ..config import ProjectControlConfig, ensure_private_directory
from ..models import PlanPreviewInput, ProjectSnapshot, ProposalEnvelope, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..graph import ProjectGraph
from ..reconcile import ProjectReconciler
from ..registry import WorkspaceRegistry
from ..snapshot import resolve_skills_root
from ..workflow import workflow_summary
from ..retrieval import economical_record, relevance_priority


class MutationDetected(RuntimeError):
    pass


def _app_temp_directory() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "project-control" / "tmp"
    ensure_private_directory(base)
    return base


def _manifest(root: Path) -> tuple[tuple[str, int, int], ...]:
    result = []
    for directory, directories, files in os.walk(root):
        directories[:] = [name for name in directories if name not in {".git", ".todo-orchestrator", ".ctxpp", "__pycache__", "node_modules"}]
        base = Path(directory)
        for name in files:
            path = base / name
            relative = path.relative_to(root).as_posix()
            if relative in {"todos.md", "todo-status.md"} or relative.startswith("todos/"):
                continue
            stat = path.stat()
            result.append((relative, stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(result))


def _identities(registry: WorkspaceRegistry, project: str) -> dict[str, tuple[str, str, tuple[tuple[str, int, int], ...]]]:
    workspace = registry.workspace(project)
    values = {}
    for alias in workspace.repositories:
        root = registry.repository(project, alias).root
        identity = GitReadAdapter(root).identity()
        values[alias] = (identity.commit, identity.status_fingerprint, _manifest(root))
    return values


def _todo_revision(root: Path, todo_script: Path) -> int | None:
    return TodoReadAdapter(root, todo_script).revision()


def _planning_context(snapshot: ProjectSnapshot, objective: str | None = None) -> dict[str, Any]:
    tasks = snapshot.todo_tables.get("tasks", [])
    workflow = workflow_summary(snapshot)
    accepted_plan_schema_versions = [2, 3] if workflow["available"] else [2]
    if objective:
        reconciled = ProjectReconciler(snapshot).reconcile()
        graph = ProjectGraph(snapshot, reconciled)
        seeds = graph.seed_candidates(objective, max_items=16)
        seeds.sort(key=lambda item: (
            relevance_priority(item.get("record", {})),
            0 if item.get("type") in {"interface", "decision", "invariant", "path", "symbol", "git_commit"} else 1,
            -float(item.get("query_coverage") or 0), str(item.get("type")), str(item.get("id")),
        ))
        broad_seeds: list[dict[str, Any]] = []
        seen_themes: set[str] = set()
        for item in seeds:
            if item["theme"] not in seen_themes:
                broad_seeds.append(item)
                seen_themes.add(item["theme"])
        for item in seeds:
            if item not in broad_seeds:
                broad_seeds.append(item)
            if len(broad_seeds) >= 8:
                break
        expanded = graph.expand_seeds(broad_seeds, max_items=48)
        expanded.sort(key=lambda item: (
            relevance_priority(item.get("record", {})), str(item.get("type")), str(item.get("id")),
        ))
        task_ids = {item["id"] for item in [*seeds, *expanded] if item["type"] == "task"}
        subjects = [
            {
                "type": item["type"], "id": item["id"], "title": item["title"],
                "theme": item["theme"], "match_basis": item["match_basis"],
                "query_coverage": item["query_coverage"], "relevance": item.get("relevance"),
            }
            for item in broad_seeds
        ]
        resolution = {
            "status": "resolved" if subjects else "not_found",
            "retrieval_mode": "multi_seed" if len(subjects) > 1 else "single_seed" if subjects else "none",
            "reason": "multi_seed_objective_retrieval" if len(subjects) > 1 else "single_relevant_subject" if subjects else "no_deterministic_subject_match",
            "subjects": subjects,
        }
        related = [
            {
                key: value for key, value in item.items() if key != "record"
            } | {"record": economical_record(item.get("record", {}), expanded=False)}
            for item in expanded[:32]
        ]
        return {
            "objective": objective,
            "resolution": resolution,
            "tasks": [economical_record(item, expanded=False) for task_id, item in reconciled.tasks.items() if task_id in task_ids],
            "related": related,
            "active_claims": [item for item in snapshot.todo_status.get("active_claims", []) if str(item.get("task_id")) in task_ids],
            "performance_assumptions": [item for item in reconciled.performance["current_evidence"] if set(item.get("linked_task_ids", [])) & task_ids],
            "workflow": workflow,
            "plan_schema_version": 2,
            "accepted_plan_schema_versions": accepted_plan_schema_versions,
            "base_revision": snapshot.todo_revision,
            "base_commits": {key: value.commit for key, value in snapshot.repositories.items()},
            "observation_preconditions": snapshot.observation_preconditions().model_dump(mode="json"),
        }
    prefixes = sorted({str(item.get("id", "")).split("-", 1)[0] for item in tasks if "-" in str(item.get("id", ""))})
    return {
        "task_prefixes": prefixes,
        "ready": snapshot.todo_status.get("ready", []),
        "active_claims": snapshot.todo_status.get("active_claims", []),
        "existing_tasks": [{key: item.get(key) for key in ("id", "title", "status", "priority")} for item in tasks],
        "scope_conflicts": snapshot.todo_tables.get("ownership_scopes", []),
        "interfaces": snapshot.todo_tables.get("interfaces", []),
        "dependencies": snapshot.todo_tables.get("task_dependencies", []),
        "gates": snapshot.todo_tables.get("gates", []),
        "invariants": snapshot.todo_tables.get("invariants", []),
        "workflow": workflow,
        "plan_schema_version": 2,
        "accepted_plan_schema_versions": accepted_plan_schema_versions,
        "base_revision": snapshot.todo_revision,
        "base_commits": {key: value.commit for key, value in snapshot.repositories.items()},
        "observation_preconditions": snapshot.observation_preconditions().model_dump(mode="json"),
    }


def plan_preview(config: ProjectControlConfig, snapshot: ProjectSnapshot, request: PlanPreviewInput) -> ToolEnvelope:
    if request.mode == "context":
        budget = 10000 if request.detail == "standard" else 6000
        return envelope("plan_preview", snapshot, bounded_payload({"mode": "context", **_planning_context(snapshot, request.objective)}, budget), warnings=snapshot.warnings_for("todo"))

    registry = WorkspaceRegistry(config)
    workspace = registry.workspace(request.project)
    if not workspace.authority_repository:
        return envelope("plan_preview", snapshot, {"mode": request.mode, "valid": False}, warnings=["todo_authority_unavailable"])
    skills_root = resolve_skills_root(config, request.project)
    if skills_root is None:
        return envelope("plan_preview", snapshot, {"mode": request.mode, "valid": False}, warnings=["skills_root_unavailable"])
    todo_script = skills_root / "todo-orchestrator" / "scripts" / "todo.py"
    root = registry.repository(request.project, workspace.authority_repository).root
    proposal = request.proposal or {}
    proposal_envelope: ProposalEnvelope | None = None
    proposal_precondition_mismatches: list[str] = []
    if proposal.get("proposal_version") == 1 and isinstance(proposal.get("proposed_change"), dict):
        try:
            proposal_envelope = ProposalEnvelope.model_validate(proposal)
            observed = snapshot.observation_preconditions()
            expected = proposal_envelope.observation_preconditions
            if expected.workspace_id != observed.workspace_id:
                proposal_precondition_mismatches.append("workspace_id")
            if expected.todo_revision is not None and expected.todo_revision != observed.todo_revision:
                proposal_precondition_mismatches.append("todo_revision")
            if expected.workflow_authority_fingerprint and expected.workflow_authority_fingerprint != observed.workflow_authority_fingerprint:
                proposal_precondition_mismatches.append("workflow_authority_fingerprint")
            if expected.repository_commits != observed.repository_commits:
                proposal_precondition_mismatches.append("repository_commits")
            proposal = proposal_envelope.proposed_change
        except ValueError:
            proposal_precondition_mismatches.append("proposal_envelope_invalid")
    encoded = json.dumps(proposal, sort_keys=True, indent=2).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    before_identity = _identities(registry, request.project)
    before_revision = _todo_revision(root, todo_script)
    todo_adapter = TodoReadAdapter(root, todo_script)
    temporary_path: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix="proposal-", suffix=".json", dir=_app_temp_directory())
        temporary_path = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        validate_json = todo_adapter.plan_read("validate", temporary_path)
        diff_json = todo_adapter.plan_read("diff", temporary_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    after_revision = _todo_revision(root, todo_script)
    after_identity = _identities(registry, request.project)
    if before_revision != after_revision or before_identity != after_identity:
        raise MutationDetected("plan preview changed registered project authority")
    diff_data = diff_json.get("data", {}) if isinstance(diff_json, dict) else {}
    valid = bool(validate_json.get("ok") and validate_json.get("data", {}).get("valid"))
    result: dict[str, Any] = {
        "mode": request.mode,
        "valid": valid,
        "would_add": diff_data.get("add", []),
        "would_modify": diff_data.get("update", []),
        "dependency_errors": validate_json.get("data", {}).get("dependency_errors", []),
        "scope_conflicts": validate_json.get("data", {}).get("scope_conflicts", []),
        "interface_errors": validate_json.get("data", {}).get("interface_errors", []),
        "warnings": validate_json.get("data", {}).get("warnings", []),
        "plan_digest": digest,
        "base_revision": before_revision,
        "base_commits": {alias: identity[0] for alias, identity in before_identity.items()},
        "mutation_guard": "unchanged",
        "plan_schema_version_observed": proposal.get("schema_version"),
        "accepted_plan_schema_versions": [2, 3],
        "observation_preconditions": snapshot.observation_preconditions().model_dump(mode="json"),
        "proposal_envelope": {
            "recognized": proposal_envelope is not None,
            "authority_to_apply": False,
            "precondition_mismatches": proposal_precondition_mismatches,
        },
    }
    reconciled = ProjectReconciler(snapshot).reconcile()
    affected = {str(item) for item in [*result["would_add"], *result["would_modify"]]}
    result["prospective_impact"] = {
        "basis": "deterministic todo relations; possible_impact where proposal detail is insufficient",
        "active_tasks_likely_made_stale": [item.get("id") for item in reconciled.active if str(item.get("id")) in affected],
        "interfaces_or_consumers_affected": [item for item in snapshot.todo_tables.get("interfaces", []) if str(item.get("owner_task_id")) in affected],
        "gates_or_checkpoints_likely_invalidated": [item.get("id") for item in [*reconciled.gates, *reconciled.checkpoints] if str(item.get("task_id")) in affected],
        "ownership_conflicts": result["scope_conflicts"],
        "performance_assumptions_needing_reconsideration": [item.get("id") or item.get("fact_id") for item in reconciled.performance["current_evidence"] if set(item.get("linked_task_ids", [])) & affected],
        "safe_parallel_work_unaffected": [item.get("id") for item in reconciled.ready if str(item.get("id")) not in affected],
        "runs_lanes_and_rendezvous": workflow_summary(snapshot),
        "worktree_identities": snapshot.observation_preconditions().model_dump(mode="json")["worktrees"],
    }
    if request.mode == "handoff" and valid:
        result["handoff"] = {
            "handoff_version": 1,
            "base_todo_revision": before_revision,
            "base_commits": result["base_commits"],
            "proposal_sha256": digest,
            "objective": request.objective or "",
            "proposal": proposal,
            "codex_instructions": [
                "Revalidate against the current revision before applying.",
                "Apply through coding-workflow/todo authority.",
                "Do not proceed if base identities changed materially.",
            ],
        }
    warnings = snapshot.warnings_for("todo")
    if not valid:
        warnings.append("proposal_invalid")
    return envelope("plan_preview", snapshot, bounded_payload(result, 32000), warnings=warnings)
