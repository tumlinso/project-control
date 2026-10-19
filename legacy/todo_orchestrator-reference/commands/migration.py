"""Legacy migration command."""

from __future__ import annotations

from ..service import Service


def register(subparsers, helpers) -> None:
    root = subparsers.add_parser("migrate")
    helpers.common(root)
    kinds = root.add_subparsers(dest="migration_kind", required=True)
    markdown = kinds.add_parser("markdown")
    helpers.common(markdown)
    mode = markdown.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    markdown.set_defaults(handler=lambda args: Service(args.repo_root).migrate_markdown(args.apply))
