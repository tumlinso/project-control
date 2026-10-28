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
    INTERNAL_TOOL_NAMES,
    MUTATION_TOOL_NAMES,
    MUTATOR_TOOL_NAMES,
    TODO_ADMIN_TOOL_NAMES,
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
from project_control.config import ProjectControlConfig


def _handler(value: str = "ok") -> dict[str, str]:
    return {"value": value}


def _server(profile: MCPProfile) -> ProfiledFastMCP:
    server = ProfiledFastMCP("project-control", profile=profile)
    all_tools = dict.fromkeys((*OBSERVER_TOOL_NAMES, *CODEX_TOOL_NAMES, *MUTATOR_TOOL_NAMES,
                               *INTERNAL_TOOL_NAMES, *TODO_ADMIN_TOOL_NAMES, *MUTATION_TOOL_NAMES))
    for name in all_tools:
        server.add_tool(_handler, name=name, description=f"{name} description", structured_output=True)
    return server


class ProfilePolicyTests(unittest.TestCase):
    def test_contract_tool_sets_are_exact_and_distinct(self) -> None:
        self.assertTrue(set(RICH_READ_TOOL_NAMES) <= set(OBSERVER_TOOL_NAMES))
        self.assertTrue({"read", "inspect"} <= set(OBSERVER_TOOL_NAMES))
        self.assertEqual(set(CODEX_TOOL_NAMES), set(RICH_READ_TOOL_NAMES) | set(WORKFLOW_TOOL_NAMES))
        self.assertEqual(set(MUTATOR_TOOL_NAMES), set(RICH_READ_TOOL_NAMES) | set(WORKFLOW_TOOL_NAMES)
                         | set(MUTATION_TOOL_NAMES))
        self.assertEqual(len(OBSERVER_TOOL_NAMES), 10)
        self.assertEqual(len(CODEX_TOOL_NAMES), 12)
        self.assertEqual(len(MUTATOR_TOOL_NAMES), 15)
        self.assertEqual(len(INTERNAL_TOOL_NAMES), 0)
        self.assertIn("plan", MUTATOR_TOOL_NAMES)
        self.assertIn("maintain_execution", MUTATOR_TOOL_NAMES)
        self.assertNotIn("terminal_capture", MUTATOR_TOOL_NAMES)

    def test_profile_and_transport_are_explicit_startup_configuration(self) -> None:
        self.assertEqual("streamable-http", profile_policy("observer").transport)
        self.assertEqual("stdio", profile_policy("codex").transport)
        self.assertEqual("stdio", profile_policy("mutator").transport)
        require_profile_transport("observer", "streamable-http")
        require_profile_transport("codex", "stdio")
        require_profile_transport("mutator", "stdio")
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
    def test_observer_registers_exact_read_surface(self) -> None:
        server = _server(MCPProfile.OBSERVER)
        tools = asyncio.run(server.list_tools())
        self.assertEqual(set(OBSERVER_TOOL_NAMES), {tool.name for tool in tools})
        asyncio.run(validate_profile_registration(server))

    def test_codex_registers_compact_workflow_and_secondary_reads(self) -> None:
        server = _server(MCPProfile.CODEX)
        tools = asyncio.run(server.list_tools())
        self.assertEqual(set(CODEX_TOOL_NAMES), {tool.name for tool in tools})
        descriptions = {tool.name: tool.description for tool in tools}
        for name in RICH_READ_TOOL_NAMES:
            self.assertTrue(descriptions[name].startswith(CODEX_RICH_READ_DESCRIPTION_PREFIX))
        for name in WORKFLOW_TOOL_NAMES:
            self.assertEqual(f"{name} description", descriptions[name])
        self.assertNotIn("amend_project", descriptions)
        asyncio.run(validate_profile_registration(server))

    def test_mutator_registers_compact_surface_and_admin_routes(self) -> None:
        server = _server(MCPProfile.MUTATOR)
        tools = asyncio.run(server.list_tools())
        self.assertEqual(set(MUTATOR_TOOL_NAMES), {tool.name for tool in tools})
        descriptions = {tool.name: tool.description for tool in tools}
        for name in RICH_READ_TOOL_NAMES:
            self.assertTrue(descriptions[name].startswith(CODEX_RICH_READ_DESCRIPTION_PREFIX))
        asyncio.run(validate_profile_registration(server))

    def test_raw_native_todo_tools_are_not_registered_in_any_profile(self) -> None:
        for profile in MCPProfile:
            with self.subTest(profile=profile):
                server = _server(profile)
                self.assertFalse(any(name.startswith("todo_") for name in
                                     (tool.name for tool in asyncio.run(server.list_tools()))))
                with self.assertRaises(ToolError):
                    asyncio.run(server.call_tool("todo_import_legacy_snapshot", {"value": "ok"}))

    def test_hidden_invocation_is_denied_before_handler(self) -> None:
        called = False

        def workflow_handler() -> None:
            nonlocal called
            called = True

        server = ProfiledFastMCP("project-control", profile="observer")
        with self.assertRaises(ProfileRegistrationError):
            server.add_tool(workflow_handler, name="todo_preview_plan")
        with self.assertRaisesRegex(ToolError, "unavailable in the observer profile"):
            asyncio.run(server.call_tool("todo_preview_plan", {}))
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
        for removed in ("delegate_task", "collect_delegation"):
            with self.subTest(removed=removed):
                with self.assertRaises(ProfileRegistrationError):
                    server.add_tool(_handler, name=removed)
                with self.assertRaisesRegex(ToolError, "unavailable in the observer profile"):
                    asyncio.run(server.call_tool(removed, {}))
        self.assertNotIn("temporarily_inactive", server.feature_metadata)

    def test_registration_validation_reports_missing_tools(self) -> None:
        server = ProfiledFastMCP("project-control", profile="observer")
        server.add_tool(_handler, name="overview")
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
