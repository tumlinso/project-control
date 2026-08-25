from __future__ import annotations

import json
from typing import Any

from ..models import EvidenceInput, ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_payload


def evidence_for(snapshot: ProjectSnapshot, request: EvidenceInput) -> ToolEnvelope:
    kinds = set(request.kinds or ["source", "tests", "gates", "worker", "cuda", "git"])
    support: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    provenance: list[str] = []
    if "gates" in kinds or "tests" in kinds:
        for gate in snapshot.todo_tables.get("gates", []):
            if request.subject not in {str(gate.get("id")), str(gate.get("task_id"))}:
                continue
            item = {key: gate.get(key) for key in ("id", "task_id", "type", "status", "valid", "last_run_at")}
            (support if gate.get("valid") else contradictions).append(item)
            provenance.append(f"todo-gate:{gate.get('id')}")
    if "worker" in kinds:
        for handoff in snapshot.todo_tables.get("handoffs", []):
            if request.subject not in {str(handoff.get("id")), str(handoff.get("task_id"))}:
                continue
            item = {key: handoff.get(key) for key in ("id", "task_id", "kind", "note", "revision", "created_at")}
            support.append(item)
            provenance.append(f"todo-handoff:{handoff.get('id')}")
    if "cuda" in kinds and snapshot.cuda.get("status") == "ok":
        support.extend(snapshot.cuda.get("artifacts", [])[: request.max_items])
        provenance.extend(f"cuda-artifact:{item.get('path')}" for item in snapshot.cuda.get("artifacts", []))
    if "git" in kinds:
        for alias, identity in snapshot.repositories.items():
            support.append({"repository": alias, "commit": identity.commit, "dirty": identity.dirty})
            provenance.append(f"git:{alias}:{identity.commit}")
    confidence = "high" if support and not contradictions else "mixed" if support else "insufficient"
    caveats = list(snapshot.warnings)
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
    return envelope("evidence", snapshot, bounded_payload(data, 18000), warnings=[] if support else ["evidence_unavailable"])
