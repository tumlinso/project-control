from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys
import unittest

from mcp.server.fastmcp import FastMCP

# Managed workspaces deliberately do not rewrite the shared editable install.
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from project_control.workflow_tools import (
    WORKFLOW_INSTRUCTIONS,
    WORKFLOW_TOOL_NAMES,
    create_workflow_mcp,
    register_workflow_tools,
)


FIXTURE = Path(__file__).parent / "fixtures" / "workflow_tools" / "schema_hashes.json"


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

    def delegate_task(self, **arguments: object) -> dict[str, object]:
        return self._call("delegate_task", arguments)

    def collect_delegation(self, **arguments: object) -> dict[str, object]:
        return self._call("collect_delegation", arguments)

    def finish_task(self, **arguments: object) -> dict[str, object]:
        return self._call("finish_task", arguments)


class WorkflowToolTests(unittest.TestCase):
    def test_exact_names_schemas_annotations_and_instructions(self) -> None:
        server = create_workflow_mcp(protocol_factory=lambda: FakeProtocol())
        tools = asyncio.run(server.list_tools())
        self.assertEqual(tuple(tool.name for tool in tools), WORKFLOW_TOOL_NAMES)
        expected_hashes = json.loads(FIXTURE.read_text(encoding="utf-8"))
        actual_hashes = {
            tool.name: hashlib.sha256(
                json.dumps(tool.inputSchema, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            for tool in tools
        }
        self.assertEqual(actual_hashes, expected_hashes)
        for tool in tools:
            readonly = tool.name in {"inspect_task", "collect_delegation"}
            self.assertEqual(tool.annotations.readOnlyHint, readonly)
            self.assertEqual(tool.annotations.idempotentHint, readonly)
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertFalse(tool.annotations.openWorldHint)
        self.assertIn("Start with next_task", WORKFLOW_INSTRUCTIONS)
        self.assertIn("secondary escalation", WORKFLOW_INSTRUCTIONS)
        self.assertNotIn("coding-workflow as", WORKFLOW_INSTRUCTIONS)

    def test_registration_composes_into_an_existing_server(self) -> None:
        server = FastMCP("composition-test")
        registered = register_workflow_tools(server, protocol=FakeProtocol())
        self.assertEqual(registered, WORKFLOW_TOOL_NAMES)
        self.assertEqual(tuple(tool.name for tool in asyncio.run(server.list_tools())), WORKFLOW_TOOL_NAMES)

    def test_all_six_tools_call_the_same_in_process_protocol(self) -> None:
        protocol = FakeProtocol()
        server = create_workflow_mcp(protocol)
        manager = server._tool_manager
        claimed = asyncio.run(manager.call_tool("next_task", {"repo_root": "/repo", "task_id": "PCU-1"}))
        handle = claimed["workflow_handle"]
        asyncio.run(manager.call_tool("inspect_task", {"workflow_handle": handle, "kind": "task"}))
        asyncio.run(manager.call_tool("coordinate_task", {"workflow_handle": handle, "action": "sync"}))
        asyncio.run(manager.call_tool("delegate_task", {"workflow_handle": handle, "delegated_objective": "bounded"}))
        asyncio.run(manager.call_tool("collect_delegation", {"delegation_handle": "wfd_opaque"}))
        asyncio.run(manager.call_tool("finish_task", {"workflow_handle": handle, "action": "complete"}))
        self.assertEqual([name for name, _ in protocol.calls], list(WORKFLOW_TOOL_NAMES))
        self.assertEqual(protocol.calls[1][1]["budget_bytes"], 8192)
        self.assertEqual(protocol.calls[3][1]["mode"], "auto")
        self.assertIsNone(protocol.calls[5][1]["disposition"])

    def test_construction_is_lazy_sticky_and_internal_failures_are_bounded(self) -> None:
        instances: list[FakeProtocol] = []

        def factory() -> FakeProtocol:
            instance = FakeProtocol()
            instances.append(instance)
            return instance

        server = create_workflow_mcp(
            protocol_factory=factory,
            diagnostic_factory=lambda: "diag_safe",
        )
        self.assertEqual(instances, [])
        manager = server._tool_manager
        asyncio.run(manager.call_tool("next_task", {"repo_root": "/one"}))
        asyncio.run(manager.call_tool("next_task", {"repo_root": "/two"}))
        self.assertEqual(len(instances), 1)

        failed = create_workflow_mcp(
            protocol_factory=lambda: (_ for _ in ()).throw(RuntimeError("secret traceback")),
            diagnostic_factory=lambda: "diag_safe",
        )
        result = asyncio.run(failed._tool_manager.call_tool("next_task", {"repo_root": "/repo"}))
        self.assertEqual(result["reason"], "unexpected_internal_failure")
        self.assertEqual(result["diagnostic_id"], "diag_safe")
        self.assertNotIn("secret", json.dumps(result))

    def test_runtime_identity_error_preserves_only_typed_compatibility(self) -> None:
        class IdentityError(Exception):
            code = "runtime_identity_mismatch"
            details = {"project_uuid": "project-1", "remediation": "restart"}

        server = create_workflow_mcp(protocol_factory=lambda: (_ for _ in ()).throw(IdentityError()))
        result = asyncio.run(server._tool_manager.call_tool("next_task", {"repo_root": "/repo"}))
        self.assertEqual(result["status"], "attention_required")
        self.assertEqual(result["compatibility"], IdentityError.details)
        self.assertNotIn("traceback", json.dumps(result))

    def test_adapter_contains_no_mcp_client_subprocess_or_kernel_logic(self) -> None:
        source = (Path(__file__).parents[1] / "src" / "project_control" / "workflow_tools.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("ClientSession", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("sqlite3", source)
        self.assertNotIn("WorkflowKernel", source)


if __name__ == "__main__":
    unittest.main()
