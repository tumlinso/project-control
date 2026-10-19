"""Additive routing policy for repositories migrated to Project Control.

The project identity is the durable policy source.  This module deliberately
does not carry a token or approval secret: canonical workflow operations are
identified by their private in-process service path, while direct automated
todo mutations fail closed.  Read operations never call this guard.
"""

from __future__ import annotations

import sys
from typing import Mapping, TextIO

from .models import ExitCode, TodoError


WORKFLOW_FRONT_DOOR = "project-control"
COMPATIBLE_WORKFLOW_FRONT_DOORS = frozenset({WORKFLOW_FRONT_DOOR, "coding-workflow"})
MUTATION_MODES = frozenset({"automated", "self_debug", "test"})


def configured_front_door(project: Mapping[str, object]) -> str | None:
    configuration = project.get("configuration")
    if not isinstance(configuration, Mapping):
        return None
    value = configuration.get("workflow_front_door")
    return str(value) if isinstance(value, str) and value else None


def interactive_owner_terminal(
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> bool:
    return bool(stdin.isatty() and stdout.isatty())


def require_mutation_route(
    project: Mapping[str, object],
    *,
    operation: str,
    mutation_mode: str = "automated",
    canonical_workflow: bool = False,
    interactive: bool | None = None,
) -> None:
    """Enforce the configured front door without creating fallback authority."""

    if mutation_mode not in MUTATION_MODES:
        raise TodoError("invalid_mutation_mode", "Todo mutation mode is invalid")
    front_door = configured_front_door(project)
    if front_door is None:
        return
    if front_door not in COMPATIBLE_WORKFLOW_FRONT_DOORS:
        raise TodoError(
            "unsupported_workflow_front_door",
            "The configured workflow front door is not supported",
            ExitCode.BLOCKED,
            {"workflow_front_door": front_door},
        )
    if canonical_workflow or mutation_mode in {"self_debug", "test"}:
        return
    if interactive if interactive is not None else interactive_owner_terminal():
        return
    raise TodoError(
        "workflow_front_door_required",
        "Automated todo mutations for this repository must use Project Control",
        ExitCode.BLOCKED,
        {
            "workflow_front_door": front_door,
            "canonical_workflow_front_door": WORKFLOW_FRONT_DOOR,
            "operation": operation,
            "read_only_commands_available": True,
            "interactive_maintenance_available": True,
        },
    )
