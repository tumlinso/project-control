"""Provider-neutral validation for native work-profile presentation."""
from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict


class WorkProfile(BaseModel):
    """Planning metadata, separate from execution observations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    difficulty: Literal["trivial", "routine", "complex", "hard", "exceptional"]
    risk: Literal["low", "medium", "high", "critical"]
    work_type: Literal[
        "inspection", "implementation", "testing", "review", "architecture",
        "integration", "performance", "research", "documentation",
    ]
    context_depth: Literal["local", "focused", "deep", "cross_project"]


def normalize_task_work_profile(task: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve absent historical profiles and strictly validate present ones."""
    result = dict(task)
    if "work_profile" in result and result["work_profile"] is not None:
        result["work_profile"] = WorkProfile.model_validate(
            result["work_profile"]
        ).model_dump(mode="json")
    return result
