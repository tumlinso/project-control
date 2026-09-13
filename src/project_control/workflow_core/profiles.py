"""Strict, provider-neutral task work-profile presentation."""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict


class WorkProfile(BaseModel):
    """Planning metadata, intentionally separate from execution observations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    difficulty: Literal["trivial", "routine", "complex", "hard", "exceptional"]
    risk: Literal["low", "medium", "high", "critical"]
    work_type: Literal[
        "inspection", "implementation", "testing", "review", "architecture",
        "integration", "performance", "research", "documentation",
    ]
    context_depth: Literal["local", "focused", "deep", "cross_project"]


def normalize_task_work_profile(task: Mapping[str, Any]) -> dict[str, Any]:
    """Round-trip profile metadata without conflating it with execution state.

    Missing profiles remain valid historical records.  Present profiles are
    strict: a provider cannot silently drop or reinterpret a malformed field.
    """
    result = dict(task)
    if "work_profile" in result and result["work_profile"] is not None:
        result["work_profile"] = WorkProfile.model_validate(result["work_profile"]).model_dump(mode="json")
    return result
