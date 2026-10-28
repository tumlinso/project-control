"""Small launcher and project registration commands for Project Control."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import config_summary, init_config, load_config, save_config
from .registry import RegistryError, WorkspaceRegistry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project-control",
        description="Register software projects and start a Project Control MCP profile.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create a starter project registry")
    workspace = commands.add_parser("workspace", help="register or list projects")
    actions = workspace.add_subparsers(dest="workspace_action", required=True)
    add = actions.add_parser("add", help="register one repository")
    add.add_argument("project")
    add.add_argument("repository")
    add.add_argument("root", type=Path)
    add.add_argument("--authority", action="store_true",
        help="mark the repository that contains this project's Todo authority")
    add.add_argument("--gitlink-policy", choices=("reject", "opaque_clean"), default=None,
        help="explicit trusted source policy for pinned submodules; never initializes them")
    remove = actions.add_parser("remove", help="unregister a project")
    remove.add_argument("project")
    actions.add_parser("list", help="show registered projects and repository roots")
    serve = commands.add_parser("serve", help="start one MCP profile")
    serve.add_argument("profile", choices=("observer", "codex", "mutator"), nargs="?", default="observer")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    commands.add_parser("codex", help="start the local Codex profile over stdio")
    commands.add_parser("mutator", help="start the local mutator profile over stdio")
    legacy = commands.add_parser("import-legacy-evidence",
        help="read a copied Project Control v0 packet database and import attributed records")
    legacy.add_argument("--source-db", type=Path, required=True,
        help="copied legacy packets.sqlite3; source is opened read-only")
    legacy.add_argument("--evidence-db", type=Path, required=True)
    legacy.add_argument("--artifact-dir", type=Path, required=True)
    legacy.add_argument("--limit", type=int, default=10000)
    return parser


def _serve(profile: str, host: str | None, port: int | None) -> int:
    if profile == "observer":
        from .app import serve
        return serve(host=host, port=port)
    if host is not None or port is not None:
        raise ValueError("local stdio profiles do not accept --host or --port")
    if profile == "codex":
        from .app import serve_codex
        return serve_codex()
    from .app import serve_mutator
    return serve_mutator()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            print(init_config())
            return 0
        if args.command == "serve":
            return _serve(args.profile, args.host, args.port)
        if args.command == "codex":
            return _serve("codex", None, None)
        if args.command == "mutator":
            return _serve("mutator", None, None)
        if args.command == "import-legacy-evidence":
            from .legacy_import import import_legacy_evidence
            print(json.dumps(import_legacy_evidence(args.source_db, args.evidence_db,
                args.artifact_dir, limit=args.limit), sort_keys=True))
            return 0
        config = load_config()
        registry = WorkspaceRegistry(config)
        if args.workspace_action == "add":
            registry.add_workspace(args.project, args.repository, args.root,
                                   authority=args.authority, gitlink_policy=args.gitlink_policy)
            save_config(config)
            print(args.project)
        elif args.workspace_action == "remove":
            registry.remove_workspace(args.project)
            save_config(config)
            print(args.project)
        else:
            print(json.dumps(config_summary(config), sort_keys=True))
        return 0
    except (FileNotFoundError, PermissionError, RegistryError, ValueError, RuntimeError) as error:
        print(f"project-control: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
