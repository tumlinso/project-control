from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from ..service import AdapterError
from ..result_validation import ModelOutcomeError, validate_model_outcome
from .base import OneShotHarnessAdapter
from ..telemetry import qwen_harness_telemetry


READONLY_CORE_TOOLS = ["read_file", "list_directory", "glob", "grep_search"]
WRITABLE_CORE_TOOLS = [*READONLY_CORE_TOOLS, "edit", "write_file"]
STRUCTURED_RETRY_MARGIN = 2
NO_CORE_TOOLS_ANCHOR = "read_file"
NON_TERMINAL_TOOLS = {
    *WRITABLE_CORE_TOOLS, "zoom_image", "notebook_edit", "run_shell_command", "read_mcp_resource",
    "web_fetch", "web_search", "todo_write", "save_memory", "lsp", "cron_create", "cron_list",
    "cron_delete", "loop_wakeup", "create_sub_session", "monitor", "agent", "ask_user_question",
    "exit_plan_mode", "task_stop", "send_message", "create_goal", "update_goal", "get_goal",
    "enter_worktree", "exit_worktree",
}


def structured_output_instruction() -> str:
    return (
        "When the bounded work is complete, your final action MUST be a call to the tool named "
        "structured_output using the required schema. Do not finish with prose or printed JSON. "
        "If evidence is insufficient or the task requires judgment outside the authorized contract, "
        "call structured_output with outcome=needs_codex."
    )


def qwen_system_instruction() -> str:
    return (
        "You are a bounded local coding worker. Follow only the supplied objective and authorized paths. "
        "Use only tools present in the registry; never use shell, agents, or unlisted tools. "
        + structured_output_instruction()
    )


def qwen_task_prompt(prompt: str) -> str:
    return (
        f"{prompt.rstrip()}\n\n"
        "Perform only the file/tool actions explicitly required above; do not inspect related files or "
        "expand the task. " + structured_output_instruction()
    )


def _tool_name_counts(records: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    def visit(value: object) -> None:
        if isinstance(value, list):
            for item in value: visit(item)
        elif isinstance(value, dict):
            if value.get("type") == "tool_use" and value.get("name"):
                name = str(value["name"])
                counts[name] = counts.get(name, 0) + 1
            for item in value.values(): visit(item)
    visit(records)
    return counts


class QwenCodeAdapter(OneShotHarnessAdapter):
    adapter_name = "qwen-code"

    def __init__(self, binary: str = "qwen", **kwargs) -> None:
        super().__init__(binary, **kwargs)
        self._core_tools_supported: bool | None = None

    def _require_core_tools(self, binary: str) -> None:
        if self._core_tools_supported is None:
            process = subprocess.run(
                [binary, "--core-tools", "read_file", "--core4-unsupported-flag-probe"],
                text=True, capture_output=True, timeout=15, check=False,
            )
            first_line = (process.stderr or process.stdout).splitlines()[:1]
            diagnostic = " ".join(first_line).casefold()
            self._core_tools_supported = "unknown arguments" in diagnostic and "core-tools" not in diagnostic
        if not self._core_tools_supported:
            raise AdapterError("installed qwen-code does not support required --core-tools isolation")

    def build_command(self, session: dict[str, Any], prompt: str) -> tuple[list[str], dict[str, str]]:
        config = session["config"]
        base_url = config.get("base_url")
        model = config.get("model")
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise AdapterError("qwen-code requires an explicit local base_url")
        if not isinstance(model, str) or not model:
            raise AdapterError("qwen-code requires an explicit local model id")
        self._require_core_tools(session["binary"])
        qwen_home = Path(tempfile.mkdtemp(prefix="qwen-home-", dir=session["runtime"]))
        api_key_name = "CORE4_LOCAL_API_KEY"
        mode = str(config.get("mode", "readonly"))
        writable = mode == "writable"
        approval = "auto-edit" if writable else "plan"
        permitted = WRITABLE_CORE_TOOLS if writable else READONLY_CORE_TOOLS
        requested = config.get("core_tools", config.get("allowed_tools"))
        if requested is None:
            core_tools = list(permitted)
        else:
            if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
                raise AdapterError("qwen-code core_tools must be a string array")
            core_tools = [item for item in requested if item != "structured_output"]
            if any(item not in permitted for item in core_tools):
                raise AdapterError("qwen-code core_tools exceed the mode policy")
        allowed_tools = list(core_tools)
        # Qwen treats an empty --core-tools value as unrestricted. A real anchor
        # that is also present in --exclude-tools yields an empty effective core
        # registry without introducing an unknown name into llama.cpp's grammar.
        registered_argument = core_tools or [NO_CORE_TOOLS_ANCHOR]
        work_tool_budget = int(config.get("max_tool_calls", 8 if writable else 6))
        configured_turn_floor = int(config.get("max_session_turns", 0))
        retry_margin = int(config.get("structured_retry_margin", STRUCTURED_RETRY_MARGIN))
        if work_tool_budget < 0 or configured_turn_floor < 0 or not 0 <= retry_margin <= 4:
            raise AdapterError("qwen-code turn and tool budgets must be bounded non-negative integers")
        terminal_budget = 1 + retry_margin
        max_tool_calls = work_tool_budget + terminal_budget
        max_session_turns = max(configured_turn_floor, work_tool_budget + terminal_budget)
        session["qwen_effective"] = {"core_tools": core_tools, "work_tool_budget": work_tool_budget,
                                     "max_tool_calls": max_tool_calls,
                                     "max_session_turns": max_session_turns}
        settings = {
            "modelProviders": {"openai": [{
                "id": model, "name": model, "envKey": api_key_name, "baseUrl": base_url,
            }]},
            "security": {"auth": {"selectedType": "openai"}},
            "model": {"name": model},
            "tools": {"approvalMode": approval},
            "skills": {"disabled": ["*"]},
            "context": {"fileName": ".core4-no-project-context", "loadFromIncludeDirectories": False},
            "disableAllHooks": True,
            "mcpServers": {},
        }
        (qwen_home / "settings.json").write_text(json.dumps(settings, sort_keys=True) + "\n", encoding="utf-8")
        schema = Path(__file__).resolve().parents[2] / "schemas" / "model-outcome-v1.schema.json"
        excluded = ",".join(sorted(NON_TERMINAL_TOOLS.difference(core_tools)))
        argv = [
            session["binary"], "--prompt", qwen_task_prompt(prompt),
            "--system-prompt", qwen_system_instruction(),
            "--output-format", "json",
            "--auth-type", "openai", "--openai-api-key", "core4-local", "--openai-base-url", base_url,
            "--approval-mode", approval, "--core-tools", ",".join(registered_argument),
            "--exclude-tools", excluded, "--json-schema", f"@{schema}",
            "--max-session-turns", str(max_session_turns),
            "--max-wall-time", str(int(config.get("max_wall_time_seconds", 180))),
            "--max-tool-calls", str(max_tool_calls),
            "--max-subagent-depth", "1",
            "--model", model,
        ]
        if allowed_tools:
            model_index = argv.index("--model")
            argv[model_index:model_index] = ["--allowed-tools", ",".join(allowed_tools)]
        return argv, {
            "HOME": str(qwen_home), "QWEN_RUNTIME_DIR": str(session["runtime"]),
            "QWEN_HOME": str(qwen_home), api_key_name: "core4-local",
            "CORE4_LOCAL_BASE_URL": base_url, "CORE4_LOCAL_MODEL": model,
        }

    def build_environment(self, session: dict[str, Any], adapter_env: dict[str, str]) -> dict[str, str]:
        allowed_names = {"PATH", "LANG", "TMPDIR", "CUDA_VISIBLE_DEVICES"}
        configured = session["config"].get("environment_allowlist", [])
        if not isinstance(configured, list) or any(not isinstance(item, str) for item in configured):
            raise AdapterError("environment_allowlist must be a string array")
        allowed_names.update(configured)
        environment = {
            key: value for key, value in os.environ.items()
            if key in allowed_names or key.startswith("LC_")
        }
        environment.update(adapter_env)
        return environment

    def run(self, handle: str, request: dict[str, Any]) -> dict[str, Any]:
        try:
            return super().run(handle, request)
        except AdapterError as error:
            session = self._session(handle)
            effective = dict(session.get("qwen_effective") or {})
            terminal_failure = any(marker in str(error) for marker in (
                "turn limit", "work budget limit", "plain text instead of calling",
            ))
            if not terminal_failure or effective.get("core_tools") != []:
                raise
            prompt = request.get("prompt")
            if not isinstance(prompt, str):
                raise
            retry = dict(request)
            retry["prompt"] = (
                "The bounded work requires no file or core-tool action. Immediately call structured_output "
                "exactly once with a schema-valid result; do not emit prose or invoke any other tool.\n\n"
                f"Original objective: {prompt}"
            )
            return super().run(handle, retry)

    def normalize_process_error(
        self, returncode: int, stdout: str, stderr: str, session: dict[str, Any],
    ) -> dict[str, Any] | None:
        if returncode not in {53, 55}:
            return None
        try:
            records = json.loads(stdout)
        except json.JSONDecodeError:
            records = []
        telemetry = qwen_harness_telemetry(records)
        telemetry["tool_name_counts"] = _tool_name_counts(records)
        effective = dict(session.get("qwen_effective") or {})
        diagnostic = {
            "exit_code": returncode,
            "effective_core_tools": effective.get("core_tools", []),
            "effective_work_tool_budget": effective.get("work_tool_budget"),
            "effective_max_tool_calls": effective.get("max_tool_calls"),
            "effective_max_session_turns": effective.get("max_session_turns"),
            "observed_tool_calls": telemetry.get("tool_calls", 0),
            "observed_tool_names": telemetry.get("tool_names", []),
            "structured_output_attempted": "structured_output" in telemetry.get("tool_names", []),
            "stderr_hint": " ".join(stderr.strip().split())[-500:],
        }
        if returncode == 55:
            raise AdapterError(f"qwen-code work budget limit: {json.dumps(diagnostic, sort_keys=True)}")
        if session["config"].get("turn_limit_is_error") is True:
            raise AdapterError(f"qwen-code turn limit: {json.dumps(diagnostic, sort_keys=True)}")
        return {"status": "needs_codex", "outcome": "NEEDS_CODEX", "reason": "harness_turn_limit",
                "retryable": False, "diagnostic": diagnostic, "usage": {"core4": telemetry}}

    def parse_output(self, stdout: str) -> tuple[str, dict[str, Any]]:
        try:
            records = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise AdapterError("qwen-code did not emit JSON") from error
        if not isinstance(records, list):
            raise AdapterError("qwen-code JSON output must be an array")
        result = next((item for item in reversed(records) if isinstance(item, dict) and item.get("type") == "result"), None)
        if result is None or result.get("is_error") is True:
            raise AdapterError("qwen-code did not emit a successful result")
        payload = result.get("result", "")
        text = json.dumps(payload, separators=(",", ":")) if isinstance(payload, dict) else str(payload)
        usage = dict(result.get("usage") or {})
        usage["core4"] = qwen_harness_telemetry(records)
        usage["core4"]["tool_name_counts"] = _tool_name_counts(records)
        return text, usage

    def normalize_outcome(
        self, text: str, usage: dict[str, Any], session: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        telemetry = dict(usage.get("core4") or {})
        terminal = str(telemetry.get("terminal_reason", "")).lower()
        if telemetry.get("budget_exhausted"):
            return {"status": "needs_codex", "outcome": "NEEDS_CODEX", "reason": "harness_budget_exhausted"}
        if telemetry.get("preempted"):
            return {"status": "preempted", "outcome": "NEEDS_CODEX", "reason": "resource_preempted"}
        config = {} if session is None else session["config"]
        try:
            value = json.loads(text)
            if isinstance(value, dict):
                root = Path(config.get("repository_root", config.get("cwd", "."))).resolve()
                claims = value.get("claims")
                if isinstance(claims, list):
                    for claim in claims:
                        evidence = claim.get("evidence") if isinstance(claim, dict) else None
                        if not isinstance(evidence, list):
                            continue
                        for item in evidence:
                            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                                continue
                            candidate = Path(item["path"])
                            if candidate.is_absolute():
                                try:
                                    item["path"] = candidate.resolve().relative_to(root).as_posix()
                                except ValueError:
                                    pass
                if str(config.get("mode", "readonly")) == "readonly":
                    value["changed_paths"] = []
            outcome = validate_model_outcome(
                value,
                repository_root=config.get("repository_root", config.get("cwd", ".")),
                authorized_read_paths=list(config.get("authorized_read_paths", ["."])),
                write_paths=list(config.get("write_paths", [])),
                actual_changed_paths=list(config.get("actual_changed_paths", [])),
                mode=str(config.get("mode", "readonly")),
                pure_test_plan=bool(config.get("pure_test_plan", False)),
            )
        except (json.JSONDecodeError, ModelOutcomeError, TypeError, ValueError) as error:
            return {"status": "needs_codex", "outcome": "NEEDS_CODEX", "reason": f"invalid_model_outcome: {error}"}
        status = {"completed": "succeeded", "needs_codex": "needs_codex", "failed": "failed"}[outcome["outcome"]]
        return {"status": status, "outcome": outcome["outcome"].upper(), "model_outcome": outcome}
