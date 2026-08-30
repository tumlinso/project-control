from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from .config import apply_config_migration, config_path, config_summary, init_config, load_config, migrate_config_dry_run, save_config
from .migration import MigrationError
from .registry import RegistryError, WorkspaceRegistry
from .snapshot import SnapshotBuilder, resolve_skills_root, resolve_todo_provider
from .terminal import BubblewrapSandbox


def _terminal_service_constraints() -> dict[str, object]:
    """Inspect only bounded systemd policy fields relevant to bubblewrap."""

    try:
        completed = subprocess.run(
            [
                "systemctl", "--user", "show", "project-control.service",
                "--property=LoadState", "--property=ActiveState",
                "--property=RestrictAddressFamilies", "--property=RestrictNamespaces",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "status": "unavailable", "compatible": None,
            "error_code": "project_control_service_policy_unavailable",
        }
    fields = dict(
        line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line
    )
    if completed.returncode != 0 or fields.get("LoadState") != "loaded":
        return {"status": "not_installed", "compatible": None, "error_code": None}
    address_families = fields.get("RestrictAddressFamilies", "").split()
    if address_families and "AF_NETLINK" not in address_families:
        return {
            "status": "incompatible", "compatible": False,
            "error_code": "bwrap_service_address_family_restricted",
            "required_address_family": "AF_NETLINK",
            "active": fields.get("ActiveState") == "active",
        }
    if fields.get("RestrictNamespaces") in {"yes", "true"}:
        return {
            "status": "incompatible", "compatible": False,
            "error_code": "bwrap_service_namespaces_restricted",
            "active": fields.get("ActiveState") == "active",
        }
    return {
        "status": "compatible", "compatible": True, "error_code": None,
        "active": fields.get("ActiveState") == "active",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project-control")
    commands = parser.add_subparsers(dest="command", required=True)

    config = commands.add_parser("config")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("init")
    migrate = config_commands.add_parser("migrate")
    migration_mode = migrate.add_mutually_exclusive_group(required=True)
    migration_mode.add_argument("--dry-run", action="store_true")
    migration_mode.add_argument("--apply", action="store_true")

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
    serve.add_argument("profile", nargs="?", choices=("observer", "codex"), default="observer")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)

    migrate_repo = commands.add_parser("migrate-repository")
    migrate_repo.add_argument("--repo", type=Path, required=True)
    mode = migrate_repo.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--remove", action="store_true")

    admin = commands.add_parser("admin")
    admin_commands = admin.add_subparsers(dest="admin_command", required=True)
    recover = admin_commands.add_parser("recover")
    recover.add_argument("--repo", required=True)
    recover.add_argument("--task")
    recover.add_argument("--reason", required=True)
    recover.add_argument("--inspect-only", action="store_true")
    return parser


def _serve_profile(profile: str, *, host: str | None, port: int | None) -> int:
    """Start only a profile chosen by trusted process startup arguments."""

    os.environ["PROJECT_CONTROL_PROFILE"] = profile
    if profile == "observer":
        from .app import serve

        return serve(host=host, port=port)
    if host is not None or port is not None:
        raise ValueError("Codex stdio profile does not accept --host or --port")
    from .app import serve_codex

    return serve_codex()


def _doctor(*, tunnel: bool) -> tuple[bool, dict[str, object]]:
    terminal_sandbox = BubblewrapSandbox()
    probe = terminal_sandbox.probe_diagnostics()
    service_constraints = _terminal_service_constraints()
    service_compatible = service_constraints.get("compatible")
    checks: dict[str, object] = {
        "config_path": str(config_path()),
        "terminal_capture": {
            "backend": "bubblewrap",
            "installed": probe["installed"],
            "ready": bool(probe["ready"] and service_compatible is not False),
            "probe": probe,
            "service_constraints": service_constraints,
        },
    }
    try:
        config = load_config()
        registry = WorkspaceRegistry(config)
        checks["config"] = "ok"
        checks["workspaces"] = sorted(config.workspaces)
        checks["ready"] = bool(config.workspaces)
        providers: dict[str, object] = {}
        builder = SnapshotBuilder(config)
        for workspace_id in config.workspaces:
            registry.workspace(workspace_id)
            provider = resolve_todo_provider(config, workspace_id)
            snapshot = builder.build(workspace_id)
            todo_warnings = snapshot.warnings_for("todo")
            providers[workspace_id] = {
                "skills_root": "ok" if resolve_skills_root(config, workspace_id) else "unavailable",
                "todo_provider": provider.local_diagnostics(),
                "todo": {
                    "status": "ok" if snapshot.todo_revision is not None else "unavailable",
                    "revision": snapshot.todo_revision,
                    "cause": todo_warnings[0] if todo_warnings else None,
                    "components": snapshot.todo_status.get("component_authority", {}),
                    "consistency": snapshot.todo_status.get("observation_consistency"),
                },
                "cuda": {
                    "status": snapshot.cuda.get("status", "unavailable"),
                    "cause": (snapshot.warnings_for("cuda") or [None])[0],
                },
                "local_worker": {
                    "status": snapshot.local_worker.get("status", "unavailable"),
                    "cause": (snapshot.warnings_for("worker") or [None])[0],
                },
            }
        checks["providers"] = providers
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        checks.update(config="unavailable", ready=False, error=str(exc))
    if tunnel:
        checks["tunnel"] = "configuration template available; credentials intentionally not inspected"
    return bool(checks.get("config") == "ok"), checks


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "config":
            if args.config_command == "init":
                print(init_config())
            else:
                result = apply_config_migration() if args.apply else migrate_config_dry_run()
                print(json.dumps(result, sort_keys=True))
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
            return _serve_profile(args.profile, host=args.host, port=args.port)
        if args.command == "migrate-repository":
            from .migration import migrate

            result = migrate(args.repo, apply=args.apply or args.remove, remove=args.remove)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "admin":
            from .admin import inspect_recovery, recover

            if args.inspect_only:
                print(json.dumps(inspect_recovery(args.repo, args.task), sort_keys=True, separators=(",", ":")))
            else:
                recover(args.repo, reason=args.reason, task_id=args.task)
            return 0
    except (FileNotFoundError, PermissionError, RegistryError, MigrationError, ValueError) as exc:
        print(f"project-control: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
