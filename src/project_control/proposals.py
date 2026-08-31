"""Inert future-write proposal helpers.

This module deliberately contains comparison and serialization utilities only.
There is no apply path, authority acquisition, or mutation switch.
"""

from __future__ import annotations

from typing import Any

from .models import ObservationPreconditions, ProjectSnapshot, ProposalEnvelope, VersionedPrecondition


def observation_preconditions(snapshot: ProjectSnapshot) -> ObservationPreconditions:
    """Build the complete proposal freshness contract from one snapshot."""

    value = snapshot.observation_preconditions()
    for record in snapshot.todo_tables.get("context_fragments", []):
        fragment_id = record.get("id") or record.get("fragment_id")
        if fragment_id:
            value.context_fragments[str(fragment_id)] = VersionedPrecondition(
                version=record.get("version"),
                content_hash=record.get("content_hash") or record.get("hash"),
                state=record.get("state"),
            )
    for record in snapshot.todo_tables.get("interfaces", []):
        interface_id = record.get("id") or record.get("interface_id")
        if interface_id:
            value.interfaces[str(interface_id)] = VersionedPrecondition(
                version=record.get("version"),
                content_hash=record.get("content_hash") or record.get("hash"),
                state=record.get("state"),
            )
    from .workflow import workflow_view

    workflow = workflow_view(snapshot)
    value.task_ids = sorted({str(item.get("task_id")) for item in workflow.get("first_class_agents", []) if item.get("task_id")})
    value.lane_ids = sorted({str(item.get("lane_id")) for item in workflow.get("first_class_agents", []) if item.get("lane_id")})
    return value


def create_proposal(
    *,
    intent: str,
    proposed_change: dict[str, Any],
    observation_preconditions: ObservationPreconditions,
    created_at: str | None = None,
) -> ProposalEnvelope:
    return ProposalEnvelope.create(
        intent=intent,
        proposed_change=proposed_change,
        observation_preconditions=observation_preconditions,
        created_at=created_at,
    )


def stale_preconditions(
    expected: ObservationPreconditions,
    current: ObservationPreconditions,
) -> dict[str, Any]:
    """Return deterministic, field-level mismatches requiring revalidation."""

    left = expected.model_dump(mode="json")
    right = current.model_dump(mode="json")
    ignored = {"observed_at", "provider_skew"}
    mismatches: list[dict[str, Any]] = []

    def compare(path: str, before: Any, after: Any) -> None:
        if path in ignored:
            return
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                child = f"{path}.{key}" if path else key
                compare(child, before.get(key), after.get(key))
            return
        if before != after:
            mismatches.append({"field": path, "expected": before, "current": after})

    compare("", left, right)
    return {
        "stale": bool(mismatches),
        "mismatches": mismatches,
        "revalidation_required": bool(mismatches),
    }


def validate_proposal_preconditions(
    proposal: ProposalEnvelope,
    current: ObservationPreconditions,
) -> dict[str, Any]:
    result = stale_preconditions(proposal.observation_preconditions, current)
    return {
        **result,
        "proposal_digest_valid": proposal.deterministic_digest
        == ProposalEnvelope.digest(
            proposal.intent,
            proposal.proposed_change,
            proposal.observation_preconditions,
        ),
        "authority_to_apply": False,
    }
