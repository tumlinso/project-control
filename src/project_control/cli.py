from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import config_path, config_summary, init_config, load_config, save_config
from .registry import RegistryError, WorkspaceRegistry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project-control")
    commands = parser.add_subparsers(dest="command", required=True)

    config = commands.add_parser("config")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("init")

    workspace = commands.add_parser("workspace")
    workspace_commands = workspace.add_subparsers(dest="workspace_command", required=True)
    add = workspace_commands.add_parser("add")
    add.add_argument("workspace")
    add.add_argument("repository")
    add.add_argument("root", type=Path)
    add.add_argument("--authority", action="store_true")
    add.add_argument("--display-name")
    remove = workspace_commands.add_parser("remove")
    remove.add_argument("workspace")
    workspace_commands.add_parser("list")

    doctor = commands.add_parser("doctor")
    doctor.add_argument("--json", action="store_true", dest="as_json")
    doctor.add_argument("--tunnel", action="store_true")

    serve = commands.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    return parser


def _doctor(*, tunnel: bool) -> tuple[bool, dict[str, object]]:
    checks: dict[str, object] = {"config_path": str(config_path())}
    try:
        config = load_config()
        registry = WorkspaceRegistry(config)
        checks["config"] = "ok"
        checks["workspaces"] = sorted(config.workspaces)
        checks["ready"] = bool(config.workspaces)
        for workspace_id in config.workspaces:
            registry.workspace(workspace_id)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        checks.update(config="unavailable", ready=False, error=str(exc))
    if tunnel:
        checks["tunnel"] = "configuration template available; credentials intentionally not inspected"
    return bool(checks.get("config") == "ok"), checks


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "config":
            target = init_config()
            print(target)
            return 0
        if args.command == "workspace":
            config = load_config()
            registry = WorkspaceRegistry(config)
            if args.workspace_command == "add":
                registry.add_workspace(
                    args.workspace,
                    args.repository,
                    args.root,
                    authority=args.authority,
                    display_name=args.display_name,
                )
                save_config(config)
                print(args.workspace)
            elif args.workspace_command == "remove":
                registry.remove_workspace(args.workspace)
                save_config(config)
                print(args.workspace)
            else:
                print(json.dumps(config_summary(config), sort_keys=True))
            return 0
        if args.command == "doctor":
            ok, result = _doctor(tunnel=args.tunnel)
            print(json.dumps(result, sort_keys=True) if args.as_json else result)
            return 0 if ok else 1
        if args.command == "serve":
            from .app import serve

            return serve(host=args.host, port=args.port)
    except (FileNotFoundError, PermissionError, RegistryError, ValueError) as exc:
        print(f"project-control: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
