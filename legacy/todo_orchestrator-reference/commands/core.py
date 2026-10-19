"""Project bootstrap, status, doctor, export, and cleanup commands."""

from __future__ import annotations

from ..service import Service


def register(subparsers, helpers) -> None:
    for name in ("bootstrap", "init"):
        parser = subparsers.add_parser(name, help="Create or recover the v2 project and live database.")
        helpers.common(parser)
        parser.add_argument("--name")
        parser.set_defaults(handler=lambda args, command=name: Service.bootstrap(args.repo_root, args.name)[1])
    for name in ("status", "doctor", "export", "cleanup"):
        parser = subparsers.add_parser(name)
        helpers.common(parser)
        parser.set_defaults(handler=lambda args, method=name: getattr(Service(args.repo_root), method)())
