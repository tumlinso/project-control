"""Owner-only administrative forwarding to Todo Orchestrator.

This module deliberately contains no recovery policy.  It verifies the shared
runtime and then invokes Todo Orchestrator's canonical owner recovery API.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence


def _runtime_identity() -> object:
    from .workflow_binding import runtime_identity

    return runtime_identity()


def inspect_recovery(repo: str | Path, task_id: str | None = None) -> dict[str, object]:
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.admin import inspect_owner_recovery
    from todo_orchestrator.workflow.recovery import RecoveryEngine

    service = Service(repo, mutation_mode="self_debug")
    engine = RecoveryEngine(service.db, service.paths.repo_root, str(service.project["project_uuid"]))
    return inspect_owner_recovery(engine, task_id)


def recover(repo: str | Path, *, reason: str, task_id: str | None = None) -> None:
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.admin import run_owner_recovery
    from todo_orchestrator.workflow.recovery import RecoveryEngine

    service = Service(repo, mutation_mode="self_debug")
    engine = RecoveryEngine(service.db, service.paths.repo_root, str(service.project["project_uuid"]))
    run_owner_recovery(
        engine,
        database_path=service.paths.db_file,
        reason=reason,
        task_id=task_id,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-control-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("recover", help="inspect or safely recover workflow ownership")
    command.add_argument("--repo", required=True)
    command.add_argument("--task")
    command.add_argument("--reason", required=True)
    command.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args(argv)
    if args.inspect_only:
        print(json.dumps(inspect_recovery(args.repo, args.task), sort_keys=True, separators=(",", ":")))
    else:
        recover(args.repo, reason=args.reason, task_id=args.task)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
