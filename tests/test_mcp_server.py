from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import ANY, patch

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from project_control.app import CODEX_INSTRUCTIONS, READ_ONLY, SERVER_INSTRUCTIONS, TERMINAL_OBSERVATION, create_asgi_app, create_mcp, serve_codex
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.profiles import CODEX_RICH_READ_DESCRIPTION_PREFIX, CODEX_TOOL_NAMES


EXPECTED = {
    "project_overview", "project_delta", "project_frontier", "inspect",
    "evidence", "plan_preview", "agent_status", "performance_status",
    "architecture_context", "coordination_view", "source_context", "history_trace",
    "impact_preview", "program_context",
    "terminal_capture",
}

INPUT_SCHEMA_SHA256 = {
    "project_overview": "42e86341f8b3869562c721e1cbd7f3e9f025a87f14182feb860b5e2763590b83",
    "project_delta": "cc68d06bd9a500c977ce74628f02a6362f44ac4413d5c43c0603a75a4cbf3214",
    "project_frontier": "ad4c2888841c1395c612fe56427a8a6f6b36454aafe0eea1301ea5d2cea56061",
    "inspect": "b2bc28930f470d4319b0d16d92fad47e2b6fe3ea44c9612f32eb2aef4b967d5c",
    "evidence": "6fcb2f398f87c0517dc4a1324c7f4c89acff7ae4a1e069fe4d4dda52a7edf556",
    "plan_preview": "3b34bfc5c59670fe82df3fac81f746c9b0401093403bad6459895ca31e94dda9",
    "agent_status": "15065af772af4bb13c5a717e55eb122dcc116635a45c9413ce6da21710caa0b2",
    "performance_status": "c50251ba7af1c1ff5659c218e93f489d8826dc68c330ddaa1b68f9c5219547b7",
    "architecture_context": "18d23a7572db9126d06945dda0eff15f98e8ec5983647ddd62144440e281e81e",
    "coordination_view": "470dae037b5460bec0b8c1d8450525be878b04385d235c8cb3cb7e6cb20b39bb",
    "source_context": "7f0f61c5d6116b28e30976cd3c95071eb99fab9fae4d7c22e1318fbc1aa8cc97",
    "history_trace": "2089fda1a35f72b6700b2aded9d521b4b31bdb2c21026438f41f34b57a6d3abe",
    "impact_preview": "fb7581b9cf10d63090eefe5bbbf2d152fc74beb085c81fcf501fb7ee4a095d71",
    "program_context": "8dffbb402cec796026659a94db8e550df00c4baeb6e87050f1aabda84e246263",
}
TERMINAL_INPUT_SCHEMA_SHA256 = "ea82a2b87a65912655f5bcc9c418db4088d8c5b6da4ff586bb4b9246e684bbd4"


class MCPServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Tests"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        terminal = self.root / "screen-demo"
        terminal.write_text(
            "#!/usr/bin/python3\nimport os,time\n"
            "print('MCP-TTY=' + str(os.isatty(1)) + ' sk_abcdefghijklmnopqrstuv', flush=True)\n"
            "for i in range(200):\n"
            " os.write(1, ('\\r\\x1b[2KSCREEN=' + ('A' if i < 5 else 'B')).encode())\n"
            " time.sleep(.05)\n",
            encoding="utf-8",
        )
        terminal.chmod(0o755)
        subprocess.run(["git", "add", "README.md", "screen-demo"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=self.root, check=True, capture_output=True)
        self.skills = Path(self.temporary.name) / "skills"
        fake_todo = self.skills / "todo-orchestrator" / "scripts" / "todo.py"
        fake_todo.parent.mkdir(parents=True)
        fake_todo.write_text("# readiness fixture\n", encoding="utf-8")
        self.config = ProjectControlConfig(skills_root=self.skills, workspaces={
            "demo": WorkspaceConfig(repositories={"source": RepositoryConfig(root=self.root)})
        })

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_tools_annotations_and_schema_budget(self) -> None:
        mcp = create_mcp(self.config)
        tools = asyncio.run(mcp.list_tools())
        self.assertEqual({tool.name for tool in tools}, EXPECTED)
        for tool in tools:
            self.assertEqual(tool.annotations.readOnlyHint, tool.name != "terminal_capture")
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertEqual(tool.annotations.idempotentHint, tool.name != "terminal_capture")
            self.assertFalse(tool.annotations.openWorldHint)
        schema_bytes = len(json.dumps([tool.model_dump(mode="json") for tool in tools], sort_keys=True).encode())
        self.assertLess(schema_bytes, 38000)
        self.assertLess(len(SERVER_INSTRUCTIONS), 1500)
        required_prefix = "Use project-control to inspect live engineering projects through its read-only"
        self.assertTrue(SERVER_INSTRUCTIONS.startswith(required_prefix))
        schemas = {tool.name: tool.inputSchema for tool in tools}
        self.assertEqual({
            name: hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            for name, schema in schemas.items() if name in INPUT_SCHEMA_SHA256
        }, INPUT_SCHEMA_SHA256)
        terminal_schema = schemas["terminal_capture"]
        self.assertEqual(
            hashlib.sha256(json.dumps(terminal_schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            TERMINAL_INPUT_SCHEMA_SHA256,
        )
        self.assertTrue(terminal_schema["properties"]["kill_after_capture"]["default"])
        self.assertEqual(terminal_schema["properties"]["wait_ms"]["maximum"], 30_000)
        self.assertEqual(terminal_schema["properties"]["argv"]["maxItems"], 64)
        self.assertFalse(TERMINAL_OBSERVATION.readOnlyHint)
        self.assertFalse(TERMINAL_OBSERVATION.idempotentHint)
        self.assertEqual(schemas["project_overview"]["properties"]["detail"]["enum"], ["compact", "standard", "expanded"])
        self.assertEqual(schemas["project_overview"]["properties"]["max_items"]["maximum"], 100)
        self.assertIn("worktree", schemas["inspect"]["properties"]["kind"]["enum"])
        self.assertEqual(schemas["inspect"]["properties"]["budget_tokens"]["maximum"], 32768)
        self.assertEqual(schemas["source_context"]["properties"]["targets"]["maxItems"], 32)

    def test_codex_composes_exact_six_and_fourteen_rich_reads(self) -> None:
        mcp = create_mcp(self.config, profile="codex")
        tools = asyncio.run(mcp.list_tools())
        self.assertEqual(set(CODEX_TOOL_NAMES), {tool.name for tool in tools})
        self.assertNotIn("terminal_capture", {tool.name for tool in tools})
        self.assertNotIn("coding-workflow", CODEX_INSTRUCTIONS)
        self.assertIn("workflow tools exposed by the current Project Control Codex profile", CODEX_INSTRUCTIONS)
        descriptions = {tool.name: tool.description for tool in tools}
        for name in EXPECTED - {"terminal_capture"}:
            self.assertTrue(descriptions[name].startswith(CODEX_RICH_READ_DESCRIPTION_PREFIX))

    def test_server_runtime_receives_configured_in_process_read_port_factory(self) -> None:
        factory = lambda _root: None
        with patch("project_control.app.todo_read_port_factory", return_value=factory) as configured:
            mcp = create_mcp(self.config)
        configured.assert_called_once_with()
        runtime = getattr(mcp, "_project_control_runtime")
        self.assertIs(runtime.todo_read_port_factory, factory)
        self.assertIs(runtime.builder.todo_read_port_factory, factory)

    def test_observer_rejects_hidden_workflow_invocation_before_binding(self) -> None:
        mcp = create_mcp(self.config)
        with self.assertRaisesRegex(ToolError, "unavailable in the observer profile"):
            asyncio.run(mcp.call_tool("next_task", {"repo_root": str(self.root)}))

    def test_codex_server_uses_stdio_transport(self) -> None:
        with patch("project_control.app.create_mcp") as create:
            self.assertEqual(serve_codex(), 0)
        create.assert_called_once_with(profile=ANY)
        create.return_value.run.assert_called_once_with(transport="stdio")

    def test_health_ready_version_and_nonloopback_refusal(self) -> None:
        with TestClient(create_asgi_app(self.config)) as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.get("/readyz").status_code, 200)
            self.assertEqual(client.get("/version").json(), {"name": "project-control", "version": "0.3.1", "tool_schema_version": 3})
        with self.assertRaises(ValueError):
            from project_control.config import ServerConfig
            ServerConfig(host="0.0.0.0")

    def test_official_streamable_http_client_discovers_only_tools(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        app = create_asgi_app(self.config)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.02)
        else:
            self.fail("server did not start")

        async def protocol() -> None:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual({tool.name for tool in tools.tools}, EXPECTED)
                    self.assertEqual((await session.list_resources()).resources, [])
                    self.assertEqual((await session.list_prompts()).prompts, [])
                    result = await session.call_tool("project_overview", {"project": "demo", "detail": "compact", "max_items": 5})
                    self.assertFalse(result.isError)
                    new_calls = {
                        "architecture_context": {"project": "demo", "question": "What is the source architecture?", "detail": "compact"},
                        "coordination_view": {"project": "demo", "detail": "compact"},
                        "source_context": {"project": "demo", "repository": "source", "targets": [{"kind": "path", "value": "README.md", "line_start": 1, "line_end": 1}], "detail": "compact"},
                        "history_trace": {"project": "demo", "subject": "README.md", "detail": "compact"},
                        "impact_preview": {"project": "demo", "hypothesis": "Change the documented source contract", "detail": "compact"},
                        "program_context": {"workspaces": ["demo"], "question": "What is current?", "detail": "compact"},
                    }
                    for name, arguments in new_calls.items():
                        result = await session.call_tool(name, arguments)
                        self.assertFalse(result.isError, name)
                    terminal = await session.call_tool("terminal_capture", {
                        "project": "demo", "repository": "source", "executable": "screen-demo", "wait_ms": 500,
                    })
                    self.assertFalse(terminal.isError)
                    payload = json.loads(terminal.content[0].text)
                    self.assertIn("MCP-TTY=True", payload["data"]["screen"])
                    self.assertIn("[REDACTED]", payload["data"]["screen"])
                    self.assertNotIn("sk_abcdefghijklmnopqrstuv", terminal.content[0].text)
                    self.assertFalse(payload["data"]["active"])
                    bonded = await session.call_tool("terminal_capture", {
                        "project": "demo", "repository": "source", "executable": "screen-demo",
                        "label": "mcp-bond", "wait_ms": 50, "kill_after_capture": False,
                    })
                    bonded_payload = json.loads(bonded.content[0].text)
                    self.assertTrue(bonded_payload["data"]["active"])
                    recaptured = await session.call_tool("terminal_capture", {
                        "project": "demo", "session": "mcp-bond", "wait_ms": 350,
                        "kill_after_capture": False,
                    })
                    recaptured_payload = json.loads(recaptured.content[0].text)
                    self.assertEqual(bonded_payload["data"]["session_id"], recaptured_payload["data"]["session_id"])
                    self.assertIn("SCREEN=B", recaptured_payload["data"]["screen"])
                    killed = await session.call_tool("terminal_capture", {
                        "project": "demo", "session": "mcp-bond", "wait_ms": 0,
                        "kill_after_capture": True,
                    })
                    self.assertFalse(json.loads(killed.content[0].text)["data"]["active"])
        try:
            asyncio.run(protocol())
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
