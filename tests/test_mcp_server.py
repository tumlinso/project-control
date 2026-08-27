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

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from project_control.app import READ_ONLY, SERVER_INSTRUCTIONS, create_asgi_app, create_mcp
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig


EXPECTED = {
    "project_overview", "project_delta", "project_frontier", "inspect",
    "evidence", "plan_preview", "agent_status", "performance_status",
}

INPUT_SCHEMA_SHA256 = {
    "project_overview": "42e86341f8b3869562c721e1cbd7f3e9f025a87f14182feb860b5e2763590b83",
    "project_delta": "7bc19453e1f23884df01629936b88a9585794a02311d72e0e1f624d8ca90c708",
    "project_frontier": "ad4c2888841c1395c612fe56427a8a6f6b36454aafe0eea1301ea5d2cea56061",
    "inspect": "b4cc372064f72054ba2bed95bc0ebcbcc285d3aea786d8ebda2c7477c9a56afd",
    "evidence": "2e26d42ebdd8bb4be9dfc170aa18dbdcaef34a9fd5ade8c286c2c8773be1c7f4",
    "plan_preview": "3b34bfc5c59670fe82df3fac81f746c9b0401093403bad6459895ca31e94dda9",
    "agent_status": "15065af772af4bb13c5a717e55eb122dcc116635a45c9413ce6da21710caa0b2",
    "performance_status": "c50251ba7af1c1ff5659c218e93f489d8826dc68c330ddaa1b68f9c5219547b7",
}


class MCPServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Tests"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True)
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
            self.assertTrue(tool.annotations.readOnlyHint)
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertTrue(tool.annotations.idempotentHint)
            self.assertFalse(tool.annotations.openWorldHint)
        schema_bytes = len(json.dumps([tool.model_dump(mode="json") for tool in tools], sort_keys=True).encode())
        self.assertLess(schema_bytes, 32000)
        self.assertLess(len(SERVER_INSTRUCTIONS), 1500)
        required_prefix = "Use project-control to inspect live engineering projects"
        self.assertTrue(SERVER_INSTRUCTIONS.startswith(required_prefix))
        schemas = {tool.name: tool.inputSchema for tool in tools}
        self.assertEqual({
            name: hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            for name, schema in schemas.items()
        }, INPUT_SCHEMA_SHA256)
        self.assertEqual(schemas["project_overview"]["properties"]["detail"]["enum"], ["compact", "standard", "expanded"])
        self.assertEqual(schemas["project_overview"]["properties"]["max_items"]["maximum"], 100)
        self.assertEqual(schemas["inspect"]["properties"]["kind"]["enum"], ["task", "interface", "checkpoint", "decision", "dependency", "symbol", "path", "subsystem"])
        self.assertEqual(schemas["inspect"]["properties"]["budget_tokens"]["maximum"], 7000)

    def test_health_ready_version_and_nonloopback_refusal(self) -> None:
        with TestClient(create_asgi_app(self.config)) as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.get("/readyz").status_code, 200)
            self.assertEqual(client.get("/version").json()["tool_schema_version"], 1)
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
        try:
            asyncio.run(protocol())
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
