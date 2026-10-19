"""Native Todo mutation helpers for Project Control's local CLI.

MCP workflow operations are registered directly from :mod:`workflow_tools`.
This module only preserves a small typed CLI boundary and never implements a
second plan transaction or reads Todo storage directly.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .config import ProjectControlConfig
from .native_workflow import invoke_local_mutator


class MutationRejected(RuntimeError):
    """A native Todo operation returned a bounded rejection."""

    def __init__(self, code: str, message: str = "native Todo operation rejected"):
        super().__init__(message)
        self.code = code


def _invoke(config: ProjectControlConfig, project: str, tool: str,
            arguments: Mapping[str, object]) -> dict[str, object]:
    result = invoke_local_mutator(config, project, tool, arguments)
    if result.get("status") != "ok":
        reason = result.get("reason")
        raise MutationRejected(reason if isinstance(reason, str) else "native_operation_rejected")
    data = result.get("data")
    if not isinstance(data, dict):
        raise MutationRejected("native_result_invalid")
    return data


def preview_native_plan(config: ProjectControlConfig, project: str,
                        package_json: str | Mapping[str, Any]) -> dict[str, object]:
    if isinstance(package_json, Mapping):
        package_json = json.dumps(package_json, ensure_ascii=False, allow_nan=False,
                                  sort_keys=True, separators=(",", ":"))
    if not isinstance(package_json, str):
        raise MutationRejected("plan_package_invalid")
    return _invoke(config, project, "todo_preview_plan", {"package_json": package_json})


def commit_native_plan(config: ProjectControlConfig, project: str, package_json: str,
                       preview: Mapping[str, object], idempotency_key: str) -> dict[str, object]:
    if not isinstance(package_json, str) or not isinstance(preview, Mapping):
        raise MutationRejected("plan_commit_input_invalid")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise MutationRejected("idempotency_key_required")
    return _invoke(config, project, "todo_commit_plan", {
        "package_json": package_json,
        "preview": dict(preview),
        "idempotency_key": idempotency_key,
    })
