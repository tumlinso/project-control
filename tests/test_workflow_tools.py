from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest

from mcp.server.fastmcp import FastMCP

# Managed workspaces deliberately do not rewrite the shared editable install.
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from project_control.workflow_tools import (
    WORKFLOW_INSTRUCTIONS,
    WORKFLOW_TOOL_NAMES,
    MaintenanceHostContext,
    create_workflow_mcp,
    register_maintenance_tool,
    register_workflow_tools,
)


class FakeProtocol:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _call(self, method: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, arguments))
        return {
            "protocol_version": 2,
            "status": "claimed",
            "method": method,
            "arguments": arguments,
            "workflow_handle": "wfc_opaque" if method == "next_task" else arguments.get("workflow_handle"),
            "allowed_actions": [],
            "recommended_next_call": "next_task",
        }

    def next_task(self, **arguments: object) -> dict[str, object]:
        return self._call("next_task", arguments)

    def inspect_task(self, **arguments: object) -> dict[str, object]:
        return self._call("inspect_task", arguments)

    def coordinate_task(self, **arguments: object) -> dict[str, object]:
        return self._call("coordinate_task", arguments)

    def finish_task(self, **arguments: object) -> dict[str, object]:
        return self._call("finish_task", arguments)


class WorkflowToolTests(unittest.TestCase):
    def test_exact_names_schemas_annotations_and_instructions(self) -> None:
        server = create_workflow_mcp(FakeProtocol())
        tools = asyncio.run(server.list_tools())
        self.assertEqual(tuple(tool.name for tool in tools), WORKFLOW_TOOL_NAMES)
        schemas = {tool.name: tool.inputSchema for tool in tools}
        self.assertEqual(schemas["next_task"]["required"], ["project"])
        self.assertEqual(set(schemas["next_task"]["properties"]),
                         {"project", "repository", "task_id", "run_id"})
        self.assertEqual(set(schemas["inspect_task"]["required"]), {"workflow_handle", "kind"})
        self.assertEqual(set(schemas["inspect_task"]["properties"]),
                         {"workflow_handle", "kind", "target", "budget_bytes"})
        self.assertEqual(set(schemas["coordinate_task"]["required"]), {"workflow_handle", "action"})
        self.assertEqual(set(schemas["coordinate_task"]["properties"]),
                         {"workflow_handle", "action", "payload"})
        self.assertEqual(set(schemas["finish_task"]["required"]), {"workflow_handle", "action"})
        self.assertEqual(set(schemas["finish_task"]["properties"]),
                         {"workflow_handle", "action", "disposition", "note", "reason"})
        self.assertTrue({"complete", "handoff", "block", "release", "accept", "reject", "integrate"}
                        <= set(schemas["finish_task"]["properties"]["action"]["enum"]))
        self.assertIn("reserve_resource", schemas["coordinate_task"]["properties"]["action"]["enum"])
        self.assertIn("verify", schemas["coordinate_task"]["properties"]["action"]["enum"])
        for tool in tools:
            readonly = tool.name == "inspect_task"
            self.assertEqual(tool.annotations.readOnlyHint, readonly)
            self.assertEqual(tool.annotations.idempotentHint, readonly)
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertFalse(tool.annotations.openWorldHint)
        self.assertIn("start with next_task", WORKFLOW_INSTRUCTIONS)
        self.assertIn("opaque workflow handle", WORKFLOW_INSTRUCTIONS)
        self.assertIn("required gates", WORKFLOW_INSTRUCTIONS.lower())
        self.assertIn("accept or reject outcomes", WORKFLOW_INSTRUCTIONS)
        self.assertIn("do not manage Todo attempt IDs", WORKFLOW_INSTRUCTIONS)

    def test_registration_composes_into_an_existing_server(self) -> None:
        server = FastMCP("composition-test")
        registered = register_workflow_tools(server, FakeProtocol())
        self.assertEqual(registered, WORKFLOW_TOOL_NAMES)
        self.assertEqual(tuple(tool.name for tool in asyncio.run(server.list_tools())), WORKFLOW_TOOL_NAMES)

    def test_all_four_tools_call_the_same_in_process_protocol(self) -> None:
        protocol = FakeProtocol()
        server = create_workflow_mcp(protocol)
        manager = server._tool_manager
        claimed = asyncio.run(manager.call_tool("next_task", {
            "project": "workspace", "repository": "source", "task_id": "PCU-1", "run_id": "PCU-RUN-1",
        }))
        handle = claimed["workflow_handle"]
        asyncio.run(manager.call_tool("inspect_task", {"workflow_handle": handle, "kind": "task"}))
        asyncio.run(manager.call_tool("inspect_task", {"workflow_handle": handle, "kind": "context_fragment", "target": "NOTE-1"}))
        asyncio.run(manager.call_tool("coordinate_task", {"workflow_handle": handle, "action": "reserve_resource",
                                                               "payload": {"requirement_id": "GPU-1"}}))
        asyncio.run(manager.call_tool("finish_task", {"workflow_handle": handle, "action": "complete"}))
        self.assertEqual([name for name, _ in protocol.calls], [
            "next_task", "inspect_task", "inspect_task", "coordinate_task", "finish_task",
        ])
        self.assertEqual(protocol.calls[1][1]["budget_bytes"], 8192)
        self.assertEqual(protocol.calls[0][1]["repository"], "source")
        self.assertEqual(protocol.calls[0][1]["run_id"], "PCU-RUN-1")
        self.assertEqual(protocol.calls[2][1]["kind"], "context_fragment")
        self.assertEqual(protocol.calls[2][1]["target"], "NOTE-1")
        self.assertEqual(protocol.calls[3][1]["action"], "reserve_resource")
        self.assertEqual(protocol.calls[3][1]["payload"], {"requirement_id": "GPU-1"})
        self.assertIsNone(protocol.calls[4][1]["disposition"])

    def test_removed_delegation_names_are_not_registered_or_dispatched(self) -> None:
        protocol = FakeProtocol()
        server = create_workflow_mcp(protocol)
        names = tuple(tool.name for tool in asyncio.run(server.list_tools()))
        self.assertEqual(names, ("next_task", "inspect_task", "coordinate_task", "finish_task"))
        manager = server._tool_manager
        for name in ("delegate_task", "collect_delegation"):
            with self.assertRaises(Exception):
                asyncio.run(manager.call_tool(name, {}))
        self.assertEqual(protocol.calls, [])

    def test_adapter_contains_no_mcp_client_subprocess_or_kernel_logic(self) -> None:
        source = (Path(__file__).parents[1] / "src" / "project_control" / "workflow_tools.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("ClientSession", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("sqlite3", source)
        self.assertNotIn("WorkflowKernel", source)

    def test_standalone_maintenance_tool_is_removed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "use_plan_recover"):
            register_maintenance_tool(
                FastMCP("maintenance-test"), host=MaintenanceHostContext("trusted-operator"),
                handler=lambda **_arguments: self.fail("removed tool reached handler"),
            )


if __name__ == "__main__":
    unittest.main()
