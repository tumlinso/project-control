from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations


class ProfileConfigurationError(ValueError):
    """Trusted startup configuration does not describe a supported profile."""


class ProfileRegistrationError(RuntimeError):
    """A profile's registered tool surface does not match its contract."""


class MCPProfile(StrEnum):
    OBSERVER = "observer"
    CODEX = "codex"
    MUTATOR = "mutator"


RICH_READ_TOOL_NAMES = (
    "project_overview",
    "project_delta",
    "project_frontier",
    "inspect",
    "evidence",
    "plan_preview",
    "agent_status",
    "performance_status",
    "architecture_context",
    "coordination_view",
    "source_context",
    "history_trace",
    "impact_preview",
    "program_context",
)

WORKFLOW_TOOL_NAMES = (
    "next_task",
    "inspect_task",
    "coordinate_task",
    "delegate_task",
    "collect_delegation",
    "finish_task",
)

TERMINAL_TOOL_NAME = "terminal_capture"
MUTATION_TOOL_NAMES = ("apply_plan",)
OBSERVER_TOOL_NAMES = RICH_READ_TOOL_NAMES + (TERMINAL_TOOL_NAME,)
CODEX_TOOL_NAMES = WORKFLOW_TOOL_NAMES + RICH_READ_TOOL_NAMES
MUTATOR_TOOL_NAMES = WORKFLOW_TOOL_NAMES + RICH_READ_TOOL_NAMES + MUTATION_TOOL_NAMES

CODEX_RICH_READ_DESCRIPTION_PREFIX = (
    "Secondary escalation read: use after the bounded workflow protocol when "
    "current-task context is insufficient. "
)


@dataclass(frozen=True, slots=True)
class ProfilePolicy:
    profile: MCPProfile
    transport: str
    tool_names: tuple[str, ...]

    def allows(self, tool_name: str) -> bool:
        return tool_name in self.tool_names

    def require_allowed(self, tool_name: str) -> None:
        if not self.allows(tool_name):
            raise ToolError(f"tool {tool_name!r} is unavailable in the {self.profile.value} profile")


_PROFILE_POLICIES: Mapping[MCPProfile, ProfilePolicy] = MappingProxyType(
    {
        MCPProfile.OBSERVER: ProfilePolicy(
            profile=MCPProfile.OBSERVER,
            transport="streamable-http",
            tool_names=OBSERVER_TOOL_NAMES,
        ),
        MCPProfile.CODEX: ProfilePolicy(
            profile=MCPProfile.CODEX,
            transport="stdio",
            tool_names=CODEX_TOOL_NAMES,
        ),
        MCPProfile.MUTATOR: ProfilePolicy(
            profile=MCPProfile.MUTATOR,
            transport="stdio",
            tool_names=MUTATOR_TOOL_NAMES,
        ),
    }
)

_KNOWN_TOOL_NAMES = frozenset(OBSERVER_TOOL_NAMES + CODEX_TOOL_NAMES + MUTATOR_TOOL_NAMES)


def profile_policy(profile: MCPProfile | str) -> ProfilePolicy:
    """Resolve only an explicit, trusted startup profile value.

    Request metadata is intentionally absent from this API. In particular,
    clientInfo, user-agent, annotations, and tool arguments cannot influence
    the selected profile.
    """

    try:
        selected = MCPProfile(profile)
    except (TypeError, ValueError) as exc:
        raise ProfileConfigurationError(f"unsupported MCP profile: {profile!r}") from exc
    return _PROFILE_POLICIES[selected]


def require_profile_transport(profile: MCPProfile | str, transport: str) -> None:
    policy = profile_policy(profile)
    if transport != policy.transport:
        raise ProfileConfigurationError(
            f"{policy.profile.value} profile requires {policy.transport} transport, got {transport!r}"
        )


class ProfiledFastMCP(FastMCP):
    """FastMCP with profile-specific registration and a dispatch allowlist.

    Registration filtering controls discovery. The independent ``call_tool``
    check is the second server-side guard and runs before argument conversion or
    a Todo-backed handler can be reached.
    """

    def __init__(self, *args: Any, profile: MCPProfile | str, **kwargs: Any) -> None:
        if kwargs.get("tools"):
            raise ProfileConfigurationError("profiled servers require explicit add_tool registration")
        self._profile_policy = profile_policy(profile)
        super().__init__(*args, **kwargs)

    @property
    def profile(self) -> MCPProfile:
        return self._profile_policy.profile

    @property
    def allowed_tool_names(self) -> tuple[str, ...]:
        return self._profile_policy.tool_names

    def add_tool(
        self,
        fn: Callable[..., Any],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Any] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> None:
        tool_name = name or fn.__name__
        if tool_name not in _KNOWN_TOOL_NAMES:
            raise ProfileRegistrationError(f"unrecognized Project Control tool: {tool_name!r}")
        if not self._profile_policy.allows(tool_name):
            return
        effective_description = description
        if self.profile in {MCPProfile.CODEX, MCPProfile.MUTATOR} and tool_name in RICH_READ_TOOL_NAMES:
            effective_description = CODEX_RICH_READ_DESCRIPTION_PREFIX + (description or "")
        super().add_tool(
            fn,
            name=name,
            title=title,
            description=effective_description,
            annotations=annotations,
            icons=icons,
            meta=meta,
            structured_output=structured_output,
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Sequence[Any] | dict[str, Any]:
        self._profile_policy.require_allowed(name)
        return await super().call_tool(name, arguments)


async def enumerate_tool_schemas(server: FastMCP) -> dict[str, dict[str, Any]]:
    """Return deterministic input schemas for release and compatibility checks."""

    return {
        tool.name: tool.inputSchema
        for tool in sorted(await server.list_tools(), key=lambda item: item.name)
    }


def input_schema_hash(schema: Mapping[str, Any]) -> str:
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def enumerate_tool_schema_hashes(server: FastMCP) -> dict[str, str]:
    return {
        name: input_schema_hash(schema)
        for name, schema in (await enumerate_tool_schemas(server)).items()
    }


async def validate_profile_registration(server: ProfiledFastMCP) -> None:
    actual = frozenset((await enumerate_tool_schemas(server)).keys())
    expected = frozenset(server.allowed_tool_names)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ProfileRegistrationError(
            f"{server.profile.value} profile registration mismatch; "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )
