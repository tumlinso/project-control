from __future__ import annotations

import asyncio
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
        self.config = ProjectControlConfig(skills_root=Path("/home/tumlinson/.agents/skills"), workspaces={
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
