"""Inert future-write proposal helpers.

This module deliberately contains comparison and serialization utilities only.
There is no apply path, authority acquisition, or mutation switch.
"""

from __future__ import annotations

from typing import Any

from .models import ObservationPreconditions, ProposalEnvelope


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
