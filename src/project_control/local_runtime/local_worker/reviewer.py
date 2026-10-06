"""Reviewer and independent-double-solve decisions with measured-value guards."""

from __future__ import annotations

from typing import Any

from .policy import DelegationPolicy, PolicyError, ROLES


def review_plan(policy: DelegationPolicy, *, role: str, primary_outcome: str,
                request_reviewer: bool = False, request_double_solve: bool = False) -> dict[str, Any]:
    if role not in ROLES:
        raise PolicyError(f"unsupported local-worker role: {role}")
    needs_codex = primary_outcome.casefold() == "needs_codex"
    reviewer = bool(request_reviewer and policy.reviewer_enabled and not needs_codex)
    double_solve = bool(request_double_solve and policy.double_solve_enabled and not needs_codex)
    return {
        "mode": "double_solve" if double_solve else "review" if reviewer else "single",
        "reviewer_enabled": reviewer,
        "double_solve_enabled": double_solve,
        "outcome": "NEEDS_CODEX" if needs_codex else primary_outcome,
        "successful_handoff": bool(needs_codex and policy.needs_codex_is_success),
        "additional_local_calls": int(reviewer) + int(double_solve),
        "reason": (
            "NEEDS_CODEX is a successful terminal hand-back"
            if needs_codex else
            "additional local calls require measured marginal frontier savings"
        ),
    }
