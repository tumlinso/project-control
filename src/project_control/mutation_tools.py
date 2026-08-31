"""MCP transport adapter for the explicit Project Control mutator profile."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import ProjectControlConfig
from .mutation import MutationRejected, apply_proposal


MUTATION_TOOL_NAMES = ("apply_plan",)
MAX_MCP_PROPOSAL_BYTES = 256 * 1024

_MUTATING = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


def register_mutation_tools(
    server: FastMCP,
    config: ProjectControlConfig,
    *,
    apply_service: Callable[..., dict[str, object]] = apply_proposal,
    diagnostic_factory: Callable[[], str] | None = None,
) -> tuple[str, ...]:
    """Register the sole broad ledger mutation tool.

    Profile selection and allowlisting are owned by the application.  This
    adapter accepts proposal content only; it cannot read caller-selected host
    paths or execute commands.
    """

    def diagnostic_id() -> str:
        return diagnostic_factory() if diagnostic_factory is not None else "diag_" + secrets.token_urlsafe(12)

    @server.tool(
        description=(
            "Apply one fresh inert ProposalEnvelope containing a native Todo plan through "
            "Todo Orchestrator's transaction authority. Stale proposals fail closed."
        ),
        annotations=_MUTATING,
        structured_output=True,
    )
    def apply_plan(project: str, proposal: dict[str, object]) -> dict[str, object]:
        if len(json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode("utf-8")) > MAX_MCP_PROPOSAL_BYTES:
            return {
                "status": "rejected",
                "reason": "proposal_too_large",
                "details": {"max_bytes": MAX_MCP_PROPOSAL_BYTES},
            }
        try:
            return apply_service(config, project, proposal)
        except MutationRejected as error:
            return {"status": "rejected", "reason": error.code, "details": error.details}
        except Exception:
            return {
                "status": "rejected",
                "reason": "unexpected_internal_failure",
                "diagnostic_id": diagnostic_id(),
                "details": {},
            }

    return MUTATION_TOOL_NAMES
