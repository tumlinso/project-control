"""Trusted startup route for a principal-bound maintenance MCP operator.

This module is intentionally a server only.  It never reads an assignment,
selects a task, or invokes ``maintain_execution`` itself: a tool-capable client
must make the exact public MCP call recorded in the owner assignment.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence

from .runtime_identity import (
    CANONICAL_ROOT_VARIABLE,
    RELEASE_DIGEST_VARIABLE,
    RELEASE_MANIFEST_VARIABLE,
    bind_runtime,
)


def operator_launch_command(principal: str) -> dict[str, object]:
    """Return the fixed command for an operator in this verified runtime."""
    if not principal or len(principal) > 160:
        raise ValueError("maintenance_host_principal_invalid")
    # In a release this also validates the sealed Project Control package.  In
    # development it validates the configured live paired runtime.
    identity = bind_runtime()
    import sys

    environment = {CANONICAL_ROOT_VARIABLE: str(identity.skills_root)}
    if identity.release_manifest is not None and identity.release_digest is not None:
        environment[RELEASE_MANIFEST_VARIABLE] = str(identity.release_manifest)
        environment[RELEASE_DIGEST_VARIABLE] = identity.release_digest
    else:
        # Development has no installed paired candidate.  These are the exact
        # import parents for the running Project Control source and its already
        # verified Todo package, not ambient search paths or discovered
        # alternatives.
        environment["PYTHONPATH"] = os.pathsep.join([
            str(Path(__file__).resolve().parent.parent),
            str(identity.package_root.parent),
        ])
    return {
        "executable": sys.executable,
        "arguments": ["-m", "project_control.maintenance_host", "operator", "--principal", principal],
        # This is the complete allowlisted runtime binding for an independently
        # started host. It deliberately carries no inherited search path,
        # secrets, model configuration, or arbitrary executable argument.
        "environment": environment,
    }


def serve_operator(principal: str) -> int:
    """Serve the Codex MCP profile with a startup-bound operator identity."""
    if not principal or len(principal) > 160:
        raise ValueError("maintenance_host_principal_invalid")
    bind_runtime()
    from .app import serve_maintenance_operator

    return serve_maintenance_operator(principal)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-control-maintenance-host")
    commands = parser.add_subparsers(dest="command", required=True)
    operator = commands.add_parser("operator", help="serve one trusted maintenance operator over stdio")
    operator.add_argument("--principal", required=True)
    args = parser.parse_args(argv)
    if args.command == "operator":
        return serve_operator(args.principal)
    raise ValueError("unsupported maintenance host command")


if __name__ == "__main__":  # pragma: no cover - exercised through module execution.
    raise SystemExit(main())
