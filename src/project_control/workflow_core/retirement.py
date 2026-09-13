"""Typed fail-closed bridge to the kernel's canonical batch retirement."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RetirementRequest(BaseModel):
    """Exact bounded replacement request; no inferred task selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_run_id: str = Field(min_length=1)
    successor_run_id: str = Field(min_length=1)
    expected_project_uuid: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    expected_fingerprint: str = Field(min_length=1)
    expected_tasks: dict[str, dict[str, Any]] = Field(min_length=1)
    dispositions: dict[str, str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)


def retire_run_batch(service: object, request: RetirementRequest | Mapping[str, Any]) -> dict[str, Any]:
    """Use exactly the installed kernel operation, or fail explicitly.

    Project Control must not emulate retirement with SQL or a partial plan.
    Capability detection lets an older runtime read historical state while
    refusing this new mutation until its matching kernel is installed.
    """

    payload = (request if isinstance(request, RetirementRequest) else RetirementRequest.model_validate(request)).model_dump(mode="json")
    operation = getattr(service, "retire_run_batch", None)
    if not callable(operation):
        raise RuntimeError("workflow_retirement_kernel_unavailable")
    result = operation(payload)
    if not isinstance(result, Mapping):
        raise RuntimeError("workflow_retirement_kernel_invalid_receipt")
    return dict(result)
