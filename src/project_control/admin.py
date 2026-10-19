"""Local administrative entry point for native Todo workflow operations.

Authority mutations still pass through the standalone Todo public application
API. This module accepts a native ``todo_*`` operation and JSON arguments;
it never accepts a repository path, bearer, caller-selected principal, SQL, or
legacy administrative request shape.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .config import ProjectControlConfig, load_config
from .native_workflow import invoke_local_mutator

_MAX_ARGUMENT_BYTES = 256 * 1024


def invoke(project: str, operation: str, arguments: dict[str, object], *,
           config: ProjectControlConfig | None = None) -> dict[str, object]:
    """Invoke one allowlisted-by-API native Todo operation under local mutator auth."""
    return invoke_local_mutator(config or load_config(), project, operation, arguments)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project-control-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    native = commands.add_parser("todo-call", help="invoke one native Todo operation")
    native.add_argument("--project", required=True, help="registered Project Control workspace ID")
    native.add_argument("--operation", required=True, help="native todo_* operation name")
    source = native.add_mutually_exclusive_group()
    source.add_argument("--arguments-json", default="{}")
    source.add_argument("--arguments-file", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        raw = (args.arguments_file.read_text(encoding="utf-8")
               if args.arguments_file is not None else args.arguments_json)
        if len(raw.encode("utf-8")) > _MAX_ARGUMENT_BYTES:
            raise ValueError("native_todo_arguments_too_large")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("native_todo_arguments_must_be_object")
        result = invoke(args.project, args.operation, value)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result.get("status") == "ok" else 2
    except (OSError, ValueError, RuntimeError, PermissionError) as error:
        print(json.dumps({"status": "attention_required", "reason": str(error)},
                         sort_keys=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
