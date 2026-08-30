from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


OBSERVER_TOOLS = {
    "project_overview", "project_delta", "project_frontier", "inspect", "evidence",
    "plan_preview", "agent_status", "performance_status", "architecture_context",
    "coordination_view", "source_context", "history_trace", "impact_preview",
    "program_context", "terminal_capture",
}
WORKFLOW_TOOLS = {
    "next_task", "inspect_task", "coordinate_task", "delegate_task",
    "collect_delegation", "finish_task",
}
CODEX_RICH_TOOLS = OBSERVER_TOOLS - {"terminal_capture"}


def _schema_hash(schema: dict[str, Any]) -> str:
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


async def _inventory(session: ClientSession) -> dict[str, str]:
    await session.initialize()
    return {
        tool.name: _schema_hash(tool.inputSchema)
        for tool in (await session.list_tools()).tools
    }


async def inspect_profiles(
    *,
    observer_url: str,
    codex_command: str,
    expected_observer_hashes: dict[str, str],
    expected_codex_hashes: dict[str, str],
) -> dict[str, Any]:
    async with AsyncExitStack() as stack:
        read, write, _ = await stack.enter_async_context(streamablehttp_client(observer_url))
        observer = await stack.enter_async_context(ClientSession(read, write))
        observer_inventory = await _inventory(observer)
    parameters = StdioServerParameters(command=codex_command, args=["codex"])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as codex:
            codex_inventory = await _inventory(codex)
    observer_names = set(observer_inventory)
    codex_names = set(codex_inventory)
    expected_codex = WORKFLOW_TOOLS | CODEX_RICH_TOOLS
    observer_schema_exact = observer_inventory == expected_observer_hashes
    codex_schema_exact = codex_inventory == expected_codex_hashes
    names_exact = observer_names == OBSERVER_TOOLS and codex_names == expected_codex
    return {
        "observer": sorted(observer_names),
        "codex": sorted(codex_names),
        "observer_schema_hashes": observer_inventory,
        "codex_schema_hashes": codex_inventory,
        "observer_exact": observer_names == OBSERVER_TOOLS,
        "codex_exact": codex_names == expected_codex,
        "observer_schema_exact": observer_schema_exact,
        "codex_schema_exact": codex_schema_exact,
        "ready": names_exact and observer_schema_exact and codex_schema_exact,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observer-url", required=True)
    parser.add_argument("--codex-command", required=True)
    parser.add_argument(
        "--schema-contract", type=Path, required=True,
        help="validated release manifest containing observer_schema_hashes and codex_schema_hashes",
    )
    args = parser.parse_args()
    contract = json.loads(args.schema_contract.read_text(encoding="utf-8"))
    result = asyncio.run(
        inspect_profiles(
            observer_url=args.observer_url,
            codex_command=args.codex_command,
            expected_observer_hashes=contract["observer_schema_hashes"],
            expected_codex_hashes=contract["codex_schema_hashes"],
        )
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
