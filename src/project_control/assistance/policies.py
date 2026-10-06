"""Trusted, request-local observer turn policies.

Policy IDs select bounded behavior and budgets. They do not grant tools,
workspace access, or other capabilities. Callers receive fresh dictionaries so
neither a request nor an adapter can mutate the canonical registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class TurnPolicy:
    policy_id: str
    version: int
    behavior: str
    instruction: str
    logical_context_tokens: int | None
    reasoning_tokens: int
    visible_tokens: int
    reasoning_mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "behavior": self.behavior,
            "logical_context_tokens": self.logical_context_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "visible_tokens": self.visible_tokens,
            "reasoning_mode": self.reasoning_mode,
        }


_POLICIES: Mapping[str, TurnPolicy] = MappingProxyType({
    "gather-v1": TurnPolicy(
        "gather-v1", 1, "gather",
        "Gather relevant facts from the supplied evidence. Keep observations separate from inferences, state material gaps and uncertainties, and avoid drawing a final synthesis or proposing an experiment unless asked.",
        8_192, 512, 1_024, "auto"),
    "synthesis-v1": TurnPolicy(
        "synthesis-v1", 1, "synthesis",
        "Synthesize the supplied evidence into a direct answer. Connect relevant findings, distinguish established facts from inference, and state the remaining uncertainty and its effect on the conclusion.",
        16_384, 2_048, 2_048, "auto"),
    "experiment-plan-v1": TurnPolicy(
        "experiment-plan-v1", 1, "experiment_plan",
        "Turn the supplied question and evidence into a feasible, testable experiment plan. State the hypothesis, controls, measurements, decision criteria, and key risks; do not claim the experiment has been run.",
        16_384, 2_048, 2_048, "auto"),
    # Existing callers retain their historical generation settings. This ID is
    # for explicit internal routing where a policy ID is required on the wire.
    "legacy-v1": TurnPolicy(
        "legacy-v1", 1, "legacy", "", None, 4_096, 2_048, "auto"),
})


def validate_policy_id(policy_id: object) -> str:
    """Return a known registry key or reject caller-controlled policy data."""
    if not isinstance(policy_id, str) or policy_id not in _POLICIES:
        raise ValueError("observer_turn_policy_unknown")
    return policy_id


def policy_for(policy_id: object) -> TurnPolicy:
    """Return the immutable trusted policy selected by its private ID."""
    return _POLICIES[validate_policy_id(policy_id)]


def generation_settings(policy_id: object) -> dict[str, Any]:
    """Build a fresh, bounded request-local adapter policy."""
    selected = policy_for(policy_id)
    return {
        "reasoning_tokens": selected.reasoning_tokens,
        "preserve_reasoning": True,
        "thinking_temperature": 0.6,
        "direct_temperature": 0.7,
        "top_k": 20,
        "thinking_top_p": 0.95,
        "direct_top_p": 0.8,
        "min_p": 0.0,
        "presence_penalty": 0.0,
        "min_answer_seconds": 15,
    }
