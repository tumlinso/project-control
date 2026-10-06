"""Measured, reversible delegation policy for CORE4 local workers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


ROLES = {"explain", "debug", "review", "test_plan"}
ROLE_PACKET_BUDGETS = {
    "explain": 1200,
    "review": 2500,
    "test_plan": 2500,
    "debug": 10000,
}
PRIORITY_ORDER = (
    "clean_cuda_foreground",
    "active_local_delegation",
    "other_foreground_gpu",
    "background_cuda_campaign",
    "idle_model_residency",
)


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class DelegationPolicy:
    real_local_enabled: bool
    reviewer_enabled: bool
    double_solve_enabled: bool
    hot_idle_seconds: int
    max_real_workers: int
    needs_codex_is_success: bool
    reason: str

    @classmethod
    def from_bakeoff(cls, value: object) -> "DelegationPolicy":
        if not isinstance(value, dict) or value.get("format") != "CORE4-HOST-BAKEOFF/1":
            raise PolicyError("host evidence must use CORE4-HOST-BAKEOFF/1")
        summary = value.get("summary") if isinstance(value.get("summary"), dict) else {}
        selection = value.get("selection")
        promoted = (
            value.get("status") == "completed"
            and isinstance(selection, dict)
            and int(summary.get("phase_b_survivors", 0)) > 0
        )
        if promoted:
            return cls(True, False, False, 0, 1, True,
                       "A host profile was promoted; reviewer and double-solve remain unmeasured.")
        return cls(False, False, False, 0, 0, True,
                   "No CORE4-17 configuration met the accepted-task threshold.")

    def decision(self, role: str, *, backend: str = "real") -> dict[str, Any]:
        if role not in ROLES:
            raise PolicyError(f"unsupported local-worker role: {role}")
        if backend not in {"fake", "real"}:
            raise PolicyError(f"unsupported backend class: {backend}")
        eligible = backend == "fake" or self.real_local_enabled
        reason = "deterministic fake backend remains compatible" if backend == "fake" else self.reason
        return {
            "eligible": eligible,
            "role": role,
            "backend": backend,
            "packet_budget_tokens": ROLE_PACKET_BUDGETS[role],
            "max_workers": 1 if backend == "fake" else self.max_real_workers,
            "reason": reason,
        }

    def as_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["packet_budget_tokens_by_role"] = dict(ROLE_PACKET_BUDGETS)
        record["priority_order"] = list(PRIORITY_ORDER)
        return record
