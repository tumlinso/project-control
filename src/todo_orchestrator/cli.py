"""Unified v2 command-line entry point and stable JSON envelope."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

from . import RESPONSE_SCHEMA_VERSION
from .commands import register_all
from .models import ExitCode, TodoError


@dataclass
class Helpers:
    def common(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--repo-root", default=".")
        parser.add_argument("--json", action="store_true", dest="json_output")
        parser.add_argument("--pretty", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="todo", description="Transactional todo-orchestrator v2")
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_all(subparsers, Helpers())
    return parser


def envelope(*, ok: bool, code: str, data=None, error=None) -> dict[str, object]:
    value: dict[str, object] = {"schema_version": RESPONSE_SCHEMA_VERSION, "ok": ok, "code": code}
    if data is not None:
        value["data"] = data
    if error is not None:
        value["error"] = error
    return value


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        data = args.handler(args)
        response = envelope(ok=True, code="success", data=data)
        exit_code = ExitCode.SUCCESS
    except TodoError as exc:
        response = envelope(ok=False, code=exc.code, error={"message": exc.message, "details": exc.details})
        exit_code = exc.exit_code
    except Exception as exc:
        response = envelope(ok=False, code="internal_error", error={"message": str(exc)})
        exit_code = ExitCode.CONSISTENCY_ERROR
    output = json.dumps(response, indent=2 if args.pretty or not args.json_output else None, sort_keys=True)
    print(output)
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
