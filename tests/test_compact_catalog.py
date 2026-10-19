"""Focused MCP registration and schema contract for the compact workflow."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from mcp.server.fastmcp.exceptions import ToolError
from project_control.as1_surface import register_surface
from project_control.profiles import (
    CODEX_TOOL_NAMES,
    MCPProfile,
    MUTATOR_TOOL_NAMES,
    OBSERVER_TOOL_NAMES,
    ProfiledFastMCP,
    WORKFLOW_TOOL_NAMES,
)
from project_control.workflow_tools import register_workflow_tools


class CompactCatalogTests(unittest.TestCase):
    def test_profile_catalogs_and_workflow_schemas(self) -> None:
        class WorkflowStub:
            def next_task(self, **_kwargs):
                return {}

            def inspect_task(self, **_kwargs):
                return {}

            def coordinate_task(self, **_kwargs):
                return {}

            def finish_task(self, **_kwargs):
                return {}

        catalogs = {}
        for profile in (MCPProfile.OBSERVER, MCPProfile.CODER, MCPProfile.MUTATOR):
            server = ProfiledFastMCP("compact-contract", profile=profile)
            register_surface(server, SimpleNamespace())
            register_workflow_tools(server, WorkflowStub())
            catalogs[profile.value] = {
                tool.name: tool for tool in asyncio.run(server.list_tools())
            }

        expected = {
            "observer": (set(OBSERVER_TOOL_NAMES), 11),
            "coder": (set(CODEX_TOOL_NAMES), 12),
            "mutator": (set(MUTATOR_TOOL_NAMES), 16),
        }
        for profile, (names, count) in expected.items():
            with self.subTest(profile=profile):
                tools = catalogs[profile]
                self.assertEqual(set(tools), names)
                self.assertEqual(len(tools), count)
                self.assertFalse(any(name.startswith("todo_") for name in tools))

        observer = catalogs["observer"]
        self.assertTrue(set(WORKFLOW_TOOL_NAMES).isdisjoint(observer))
        self.assertTrue(all(tool.annotations.readOnlyHint for tool in observer.values()))
        observer_server = ProfiledFastMCP("observer-contract", profile="observer")
        register_surface(observer_server, SimpleNamespace())
        register_workflow_tools(observer_server, WorkflowStub())
        with self.assertRaises(ToolError):
            asyncio.run(observer_server.call_tool("next_task", {"project": "demo"}))

        coder = catalogs["coder"]
        schemas = {name: tool.inputSchema for name, tool in coder.items()}
        next_schema = schemas["next_task"]
        self.assertEqual(next_schema["required"], ["project"])
        self.assertEqual(
            set(next_schema["properties"]),
            {"project", "repository", "task_id", "run_id"},
        )
        self.assertNotIn("repo_root", next_schema["properties"])

        for name, required in (
            ("inspect_task", {"workflow_handle", "kind"}),
            ("coordinate_task", {"workflow_handle", "action"}),
            ("finish_task", {"workflow_handle", "action"}),
        ):
            schema = schemas[name]
            with self.subTest(tool=name):
                self.assertTrue(required.issubset(set(schema["required"])))
                self.assertIn("workflow_handle", schema["properties"])
                self.assertTrue(
                    {"project", "repository", "repo_root"}.isdisjoint(
                        schema["properties"]
                    )
                )


if __name__ == "__main__":
    unittest.main()
