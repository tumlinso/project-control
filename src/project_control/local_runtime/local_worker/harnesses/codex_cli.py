from __future__ import annotations

from typing import Any

from ..service import AdapterError
from .base import OneShotHarnessAdapter, parse_json_lines


class CodexCliAdapter(OneShotHarnessAdapter):
    adapter_name = "codex-cli"

    def __init__(self, binary: str = "codex", **kwargs) -> None:
        super().__init__(binary, **kwargs)

    def build_command(self, session: dict[str, Any], prompt: str) -> tuple[list[str], dict[str, str]]:
        config = session["config"]
        argv = [
            session["binary"], "exec", "--json", "--ephemeral", "--sandbox", "read-only",
            "--ask-for-approval", "never", "--cd", str(session["cwd"]), prompt,
        ]
        if config.get("model"):
            argv[2:2] = ["--model", str(config["model"])]
        for override in config.get("config_overrides", []):
            argv[2:2] = ["--config", str(override)]
        return argv, {}

    def parse_output(self, stdout: str) -> tuple[str, dict[str, Any]]:
        try:
            records = parse_json_lines(stdout)
        except ValueError as error:
            raise AdapterError("codex-cli did not emit JSONL") from error
        messages = [
            str(item.get("item", {}).get("text", ""))
            for item in records
            if item.get("type") == "item.completed" and item.get("item", {}).get("type") == "agent_message"
        ]
        if not messages:
            raise AdapterError("codex-cli did not emit an agent message")
        usage = next((dict(item.get("usage") or {}) for item in reversed(records) if item.get("type") == "turn.completed"), {})
        return messages[-1], usage
