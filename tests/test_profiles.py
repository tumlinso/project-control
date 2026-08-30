from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

from mcp.server.fastmcp.exceptions import ToolError

# Managed workflow worktrees coexist with an editable installation of the main
# checkout. Put this test's adjacent source tree first so the gate always tests
# the immutable lane artifact rather than the integration destination.
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))
import project_control  # noqa: E402

project_control.__path__.insert(0, str(SOURCE_ROOT / "project_control"))

from project_control.profiles import (
    CODEX_RICH_READ_DESCRIPTION_PREFIX,
    CODEX_TOOL_NAMES,
    OBSERVER_TOOL_NAMES,
    RICH_READ_TOOL_NAMES,
    WORKFLOW_TOOL_NAMES,
    MCPProfile,
    ProfileConfigurationError,
    ProfileRegistrationError,
    ProfiledFastMCP,
    enumerate_tool_schema_hashes,
    enumerate_tool_schemas,
    profile_policy,
    require_profile_transport,
    validate_profile_registration,
)


def _handler(value: str = "ok") -> dict[str, str]:
    return {"value": value}


def _server(profile: MCPProfile) -> ProfiledFastMCP:
    server = ProfiledFastMCP("project-control", profile=profile)
    for name in OBSERVER_TOOL_NAMES + WORKFLOW_TOOL_NAMES:
        server.add_tool(_handler, name=name, description=f"{name} description", structured_output=True)
    return server


class ProfilePolicyTests(unittest.TestCase):
    def test_contract_tool_sets_are_exact_and_distinct(self) -> None:
        self.assertEqual(15, len(OBSERVER_TOOL_NAMES))
        self.assertEqual(20, len(CODEX_TOOL_NAMES))
        self.assertEqual(14, len(RICH_READ_TOOL_NAMES))
        self.assertEqual(6, len(WORKFLOW_TOOL_NAMES))
        self.assertEqual(set(RICH_READ_TOOL_NAMES), set(OBSERVER_TOOL_NAMES) - {"terminal_capture"})
        self.assertEqual(set(CODEX_TOOL_NAMES), set(RICH_READ_TOOL_NAMES) | set(WORKFLOW_TOOL_NAMES))

    def test_profile_and_transport_are_explicit_startup_configuration(self) -> None:
        self.assertEqual("streamable-http", profile_policy("observer").transport)
        self.assertEqual("stdio", profile_policy("codex").transport)
        require_profile_transport("observer", "streamable-http")
        require_profile_transport("codex", "stdio")
        with self.assertRaises(ProfileConfigurationError):
            profile_policy("clientInfo:codex")
        with self.assertRaises(ProfileConfigurationError):
            require_profile_transport("observer", "stdio")

    def test_profile_cannot_be_changed_by_client_metadata_or_tool_arguments(self) -> None:
        server = _server(MCPProfile.OBSERVER)
        forged_metadata = {"clientInfo": {"name": "codex"}, "user-agent": "codex", "profile": "codex"}
        self.assertEqual(MCPProfile.OBSERVER, server.profile)
        self.assertNotIn(forged_metadata["profile"], server.__dict__)
        with self.assertRaises(AttributeError):
            server.profile = MCPProfile.CODEX  # type: ignore[misc]
        with self.assertRaises(ToolError):
            asyncio.run(server.call_tool("next_task", forged_metadata))


class ProfileRegistrationTests(unittest.TestCase):
    def test_observer_registers_only_fifteen_observer_tools(self) -> None:
        server = _server(MCPProfile.OBSERVER)
        tools = asyncio.run(server.list_tools())
        self.assertEqual(set(OBSERVER_TOOL_NAMES), {tool.name for tool in tools})
        asyncio.run(validate_profile_registration(server))

    def test_codex_registers_six_workflow_and_fourteen_secondary_reads(self) -> None:
        server = _server(MCPProfile.CODEX)
        tools = asyncio.run(server.list_tools())
        self.assertEqual(set(CODEX_TOOL_NAMES), {tool.name for tool in tools})
        descriptions = {tool.name: tool.description for tool in tools}
        for name in RICH_READ_TOOL_NAMES:
            self.assertTrue(descriptions[name].startswith(CODEX_RICH_READ_DESCRIPTION_PREFIX))
        for name in WORKFLOW_TOOL_NAMES:
            self.assertEqual(f"{name} description", descriptions[name])
        asyncio.run(validate_profile_registration(server))

    def test_hidden_invocation_is_denied_before_handler(self) -> None:
        called = False

        def workflow_handler() -> None:
            nonlocal called
            called = True

        server = ProfiledFastMCP("project-control", profile="observer")
        server.add_tool(workflow_handler, name="next_task")
        with self.assertRaisesRegex(ToolError, "unavailable in the observer profile"):
            asyncio.run(server.call_tool("next_task", {}))
        self.assertFalse(called)

    def test_terminal_is_absent_and_denied_in_codex(self) -> None:
        server = _server(MCPProfile.CODEX)
        self.assertNotIn("terminal_capture", {tool.name for tool in asyncio.run(server.list_tools())})
        with self.assertRaisesRegex(ToolError, "unavailable in the codex profile"):
            asyncio.run(server.call_tool("terminal_capture", {}))

    def test_unknown_registration_fails_closed(self) -> None:
        server = ProfiledFastMCP("project-control", profile="observer")
        with self.assertRaises(ProfileRegistrationError):
            server.add_tool(_handler, name="arbitrary_shell")

    def test_registration_validation_reports_missing_tools(self) -> None:
        server = ProfiledFastMCP("project-control", profile="observer")
        server.add_tool(_handler, name="project_overview")
        with self.assertRaisesRegex(ProfileRegistrationError, "missing="):
            asyncio.run(validate_profile_registration(server))

    def test_schema_enumeration_and_hashing_are_deterministic(self) -> None:
        first = _server(MCPProfile.OBSERVER)
        second = _server(MCPProfile.OBSERVER)
        schemas = asyncio.run(enumerate_tool_schemas(first))
        first_hashes = asyncio.run(enumerate_tool_schema_hashes(first))
        second_hashes = asyncio.run(enumerate_tool_schema_hashes(second))
        self.assertEqual(sorted(OBSERVER_TOOL_NAMES), list(schemas))
        self.assertEqual(first_hashes, second_hashes)
        self.assertTrue(all(len(value) == 64 for value in first_hashes.values()))


if __name__ == "__main__":
    unittest.main()
