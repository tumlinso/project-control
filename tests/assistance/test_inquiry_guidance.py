"""Agentic inquiry tool descriptions preserve the intended reasoning boundary."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from project_control.as1_surface import register_surface
from project_control.profiles import MCPProfile, ProfiledFastMCP


def test_registered_observer_tools_explain_bounded_reasoning_and_retry_contract():
    server = ProfiledFastMCP("guidance-test", profile=MCPProfile.OBSERVER)
    register_surface(server, SimpleNamespace())
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    investigate = tools["investigate"].description
    assert "reasoning is limited" in investigate
    assert "controlled Project Control environment" in investigate
    assert "system architecture, complex inference, strategy and consequential decisions" in investigate
    assert "repeat the identical question later" in investigate
    assert "If busy" in investigate
    assert "source" in investigate and "uncertainty" in investigate

    skill = tools["skill"].description
    assert "reasoning is limited" in skill
    assert "installed native skill" in skill
    assert "controlled Project Control environment" in skill
    assert "complex inference, strategy and consequential decisions" in skill
    assert "repeat the identical question later" in skill
    assert "If busy" in skill
