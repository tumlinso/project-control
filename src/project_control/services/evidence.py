from __future__ import annotations

from typing import Any

from ..adapters.ctxpp import CtxppReadAdapter
from ..config import DEFAULT_DENY_PATTERNS, ProjectControlConfig
from ..models import EvidenceInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload
from ..registry import WorkspaceRegistry


def _linked(subject: str, item: dict[str, Any], fields: tuple[str, ...]) -> bool:
    wanted = subject.casefold()
    for field in fields:
        value = item.get(field)
        if isinstance(value, str) and value.casefold() == wanted:
            return True
        if isinstance(value, list) and any(isinstance(entry, str) and entry.casefold() == wanted for entry in value):
            return True
    return False


def _cuda_relevant(subject: str, item: dict[str, Any]) -> bool:
    return _linked(subject, item, ("id", "campaign_id", "fact_id", "job_id", "task_id", "task_ids", "symbols", "paths"))


def evidence_for(config: ProjectControlConfig, snapshot: ProjectSnapshot, request: EvidenceInput) -> ToolEnvelope:
    kinds = set(request.kinds or ["source", "tests", "gates", "worker", "cuda", "git"])
    support: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    provenance: list[str] = []
    warnings: list[str] = []

    if "gates" in kinds or "tests" in kinds:
        warnings.extend(snapshot.warnings_for("todo"))
        for gate in snapshot.todo_tables.get("gates", []):
            if not _linked(request.subject, gate, ("id", "task_id", "owner_task_id")):
                continue
            item = {key: gate.get(key) for key in ("id", "task_id", "type", "status", "valid", "last_run_at")}
            (support if gate.get("valid") else contradictions).append({"kind": "gate", **item})
            provenance.append(f"todo-gate:{gate.get('id')}")

    if "worker" in kinds:
        warnings.extend(snapshot.warnings_for("todo", "worker"))
        for table, label in (("handoffs", "handoff"), ("child_executions", "child_execution")):
            for record in snapshot.todo_tables.get(table, []):
                if not _linked(request.subject, record, ("id", "task_id", "parent_task_id")):
                    continue
                support.append({"kind": label, **{key: record.get(key) for key in ("id", "task_id", "state", "status", "result", "revision", "created_at")}})
                provenance.append(f"todo-{label}:{record.get('id')}")
        for claim in snapshot.todo_status.get("active_claims", []):
            if isinstance(claim, dict) and _linked(request.subject, claim, ("id", "task_id")):
                support.append({"kind": "claim", "task_id": claim.get("task_id"), "state": "active"})
                provenance.append(f"todo-claim:{claim.get('task_id')}")

    if kinds.intersection({"source", "tests"}):
        registry = WorkspaceRegistry(config)
        workspace = registry.workspace(request.project)
        for alias in sorted(workspace.repositories):
            repository = registry.repository(request.project, alias)
            try:
                result = CtxppReadAdapter(repository.root, deny_patterns=[*DEFAULT_DENY_PATTERNS, *workspace.deny_patterns]).inspect(request.subject, max_items=request.max_items)
            except Exception:
                warnings.append(f"source_evidence_unavailable:{alias}")
                continue
            for match in result.get("matches", []):
                if not isinstance(match, dict):
                    continue
                path = str(match.get("path") or match.get("file") or "")
                under_tests = path.startswith(("test/", "tests/", "bench/", "benchmark/", "benchmarks/")) or "/tests/" in path
                selected_kind = "test_source" if under_tests else "source"
                if selected_kind == "source" and "source" not in kinds:
                    continue
                if selected_kind == "test_source" and not ({"source", "tests"} & kinds):
                    continue
                entry = {"kind": selected_kind, "repository": alias, **match}
                support.append(entry)
                commit = snapshot.repositories.get(alias)
                if commit:
                    provenance.append(f"source:{alias}:{commit.commit}:{path}")

    if "cuda" in kinds:
        warnings.extend(snapshot.warnings_for("cuda"))
        for collection in ("campaigns", "facts", "results"):
            for record in snapshot.cuda.get(collection, []):
                if isinstance(record, dict) and _cuda_relevant(request.subject, record):
                    support.append({"kind": f"cuda_{collection[:-1]}", **record})
                    provenance.append(f"cuda:{record.get('id') or record.get('fact_id') or record.get('campaign_id')}")

    if "git" in kinds:
        wanted = request.subject.casefold()
        for alias, identity in snapshot.repositories.items():
            commit_match = len(request.subject) >= 7 and identity.commit.casefold().startswith(wanted)
            repository_match = wanted in {alias.casefold(), f"repository:{alias}".casefold()}
            if commit_match or repository_match:
                support.append({"kind": "git_identity", "repository": alias, "commit": identity.commit, "dirty": identity.dirty})
                provenance.append(f"git:{alias}:{identity.commit}")

    confidence = "high" if support and not contradictions else "mixed" if support else "insufficient"
    caveats = list(dict.fromkeys(warnings))
    if not support:
        caveats.append("no_matching_evidence")
    data = {
        "claim": request.subject,
        "confidence": confidence,
        "support": support[: request.max_items],
        "contradictions": contradictions[: request.max_items],
        "caveats": list(dict.fromkeys(caveats)),
        "provenance_ids": list(dict.fromkeys(provenance))[: request.max_items],
    }
    response_warnings = warnings if warnings else ([] if support else ["evidence_unavailable"])
    return envelope("evidence", snapshot, bounded_payload(data, 18000), warnings=list(dict.fromkeys(response_warnings)))
