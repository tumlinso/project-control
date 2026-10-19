"""The legacy broad ``apply_plan`` surface has been retired.

Native Todo workflow operations are registered by ``workflow_tools`` under
explicit profile allowlists; this module intentionally registers no competing
Project Control mutation envelope.
"""
from __future__ import annotations

MUTATION_TOOL_NAMES: tuple[str, ...] = ()


def register_mutation_tools(*_args, **_kwargs) -> tuple[str, ...]:
    """Return no legacy tools; callers must register native workflow tools."""
    return MUTATION_TOOL_NAMES
