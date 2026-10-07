"""Stable, in-process, read-only facade over Todo Orchestrator.

This is the product integration boundary used by Project Control.  It is
deliberately narrower than the CLI: only normalized observations are exposed,
and every database-backed call opens the authority in SQLite read-only mode.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import RESPONSE_SCHEMA_VERSION, SCHEMA_VERSION
from .cli import envelope
from .models import ExitCode, TodoError
from .semantic import SemanticReader
from .service import Service

READ_PORT_CONTRACT = "PCU-TODO-READ-PORT/1"
READ_PORT_VERSION = "1"
READ_PORT_CAPABILITIES = (
    "status",
    "ready",
    "export",
    "explain",
    "changes",
    "semantic.state",
    "semantic.anchor",
    "semantic.delta",
    "semantic.workflow",
    "plan.validate",
    "plan.diff",
)


def _source_identity() -> str:
    package_root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for relative in ("__init__.py", "db.py", "service.py", "semantic/__init__.py"):
        path = package_root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
    return f"todo-orchestrator:{digest.hexdigest()}"


def _options(arguments: Sequence[str]) -> tuple[dict[str, object], list[str]]:
    """Parse the small, stable subset of CLI-style arguments accepted here."""
    values: dict[str, object] = {}
    positional: list[str] = []
    index = 0
    boolean = {"--current-only"}
    while index < len(arguments):
        item = arguments[index]
        if item in boolean:
            values[item[2:].replace("-", "_")] = True
            index += 1
            continue
        if item.startswith("--"):
            if index + 1 >= len(arguments) or arguments[index + 1].startswith("--"):
                raise TodoError(
                    "read_port_invalid_arguments",
                    f"Read option {item} requires a value",
                    ExitCode.VALIDATION_ERROR,
                )
            values[item[2:].replace("-", "_")] = arguments[index + 1]
            index += 2
            continue
        positional.append(item)
        index += 1
    return values, positional


def _integer(value: object, name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except ValueError as exc:
        raise TodoError(
            "read_port_invalid_arguments",
            f"{name} must be an integer",
            ExitCode.VALIDATION_ERROR,
        ) from exc


class TodoReadPort:
    """Fail-closed normalized read facade over the bundled Todo package."""

    def __init__(self, skills_root: str | Path | None = None) -> None:
        # Keep the old argument as a content-root compatibility field. The
        # executable package identity is this import, never a Todo directory
        # discovered under Skills.
        root = Path(skills_root).expanduser().resolve() if skills_root is not None else Path.cwd().resolve()
        if not root.is_dir():
            raise TodoError(
                "read_port_runtime_mismatch",
                "Requested content root is unavailable",
                ExitCode.CONSISTENCY_ERROR,
                {"requested_content_root": str(root)},
            )
        self._skills_root = root
        self._source_identity = _source_identity()

    def identity(self) -> Mapping[str, object]:
        return {
            "contract": READ_PORT_CONTRACT,
            "package_root": str(Path(__file__).resolve().parent),
            "skills_root": str(self._skills_root),
            "source_identity": self._source_identity,
            "version": READ_PORT_VERSION,
            "todo_schema_version": SCHEMA_VERSION,
            "response_schema_version": RESPONSE_SCHEMA_VERSION,
            "capabilities": list(READ_PORT_CAPABILITIES),
        }

    def invoke(
        self,
        operation: str,
        *,
        repo_root: Path,
        arguments: tuple[str, ...] = (),
    ) -> dict[str, object]:
        if operation not in READ_PORT_CAPABILITIES:
            return envelope(
                ok=False,
                code="read_port_operation_denied",
                error={"message": f"Operation {operation!r} is not a read-port capability"},
            )
        try:
            options, positional = _options(arguments)
            data = self._dispatch(operation, Path(repo_root).resolve(), options, positional)
            return envelope(ok=True, code="success", data=data)
        except TodoError as exc:
            return envelope(
                ok=False,
                code=exc.code,
                error={"message": exc.message, "details": exc.details},
            )
        except sqlite3.OperationalError as exc:
            # Keep the public port diagnostic useful without leaking an
            # authority path from SQLite's exception text.  Project Control
            # maps these stable codes into its component diagnostics.
            message = str(exc).lower()
            code = (
                "database_open_failed"
                if "unable to open database file" in message
                else "database_read_failed"
            )
            return envelope(
                ok=False,
                code=code,
                error={"message": "Todo SQLite read failed", "details": {"kind": "sqlite_operational"}},
            )
        except Exception as exc:
            return envelope(
                ok=False,
                code="internal_error",
                error={"message": str(exc)},
            )

    @staticmethod
    def _dispatch(
        operation: str,
        repo_root: Path,
        options: dict[str, object],
        positional: list[str],
    ) -> dict[str, object]:
        if operation.startswith("semantic."):
            reader = SemanticReader(repo_root)
            if operation == "semantic.workflow":
                _require_empty(options, positional)
                return reader.workflow()
            if operation == "semantic.state":
                _require_only(options, {"task", "task_id", "prefix", "program", "current_only"}, positional)
                return reader.state(
                    task_id=options.get("task", options.get("task_id")),
                    prefix=options.get("prefix"),
                    program=options.get("program"),
                    current_only=bool(options.get("current_only", False)),
                )
            if operation == "semantic.anchor":
                _require_only(options, {"task", "task_id", "checkpoint", "checkpoint_id", "interface", "interface_id", "revision", "phase"}, positional)
                return reader.anchor(
                    task_id=options.get("task", options.get("task_id")),
                    checkpoint_id=options.get("checkpoint", options.get("checkpoint_id")),
                    interface_id=options.get("interface", options.get("interface_id")),
                    revision=_integer(options.get("revision"), "revision"),
                    phase=str(options.get("phase", "created")),
                )
            _require_only(options, {"since_revision", "since_task", "since_checkpoint", "since_interface", "until_revision", "task_phase"}, positional)
            return reader.delta(
                since_revision=_integer(options.get("since_revision"), "since-revision"),
                since_task=options.get("since_task"),
                since_checkpoint=options.get("since_checkpoint"),
                since_interface=options.get("since_interface"),
                until_revision=_integer(options.get("until_revision"), "until-revision"),
                task_phase=str(options.get("task_phase", "created")),
            )

        service = Service(repo_root, read_only=True)
        if operation in {"status", "ready", "export"}:
            _require_empty(options, positional)
            return getattr(service, operation)()
        if operation == "explain":
            _require_only(options, {"task", "task_id"}, positional, max_positionals=1)
            task_id = options.get("task", options.get("task_id")) or (positional[0] if positional else None)
            if not task_id:
                raise TodoError("read_port_invalid_arguments", "explain requires a task id")
            return service.explain(str(task_id))
        if operation == "changes":
            _require_only(options, {"since"}, positional)
            since = _integer(options.get("since"), "since")
            if since is None:
                raise TodoError("read_port_invalid_arguments", "changes requires --since")
            # Claim tokens are intentionally absent: their normal CLI path pulses
            # a lease and therefore is not a read-only operation.
            return service.changes(since)
        _require_only(options, {"file"}, positional, max_positionals=1)
        plan_file = options.get("file") or (positional[0] if positional else None)
        if not plan_file:
            raise TodoError("read_port_invalid_arguments", f"{operation} requires --file")
        return service.plan_validate(str(plan_file)) if operation == "plan.validate" else service.plan_diff(str(plan_file))


def _require_empty(options: dict[str, object], positional: list[str]) -> None:
    _require_only(options, set(), positional)


def _require_only(
    options: dict[str, object],
    allowed: set[str],
    positional: list[str],
    *,
    max_positionals: int = 0,
) -> None:
    unexpected = sorted(set(options) - allowed)
    if unexpected or len(positional) > max_positionals:
        raise TodoError(
            "read_port_invalid_arguments",
            "Unsupported read-port arguments",
            ExitCode.VALIDATION_ERROR,
            {"unexpected_options": unexpected, "positionals": positional},
        )


def create_read_port(skills_root: str | Path | None = None) -> TodoReadPort:
    return TodoReadPort(skills_root)


def create_todo_read_port(skills_root: str | Path | None = None) -> TodoReadPort:
    """Explicitly named compatibility factory for product consumers."""
    return create_read_port(skills_root)


__all__ = [
    "READ_PORT_CAPABILITIES",
    "READ_PORT_CONTRACT",
    "READ_PORT_VERSION",
    "TodoReadPort",
    "create_read_port",
    "create_todo_read_port",
]
