"""Plan validation, diff, apply, and scaffolding commands."""

from __future__ import annotations

from pathlib import Path

from ..config import find_repo_root
from ..plan import load_plan, scaffold, validate_plan
from ..service import Service


def register(subparsers, helpers) -> None:
    root = subparsers.add_parser("plan")
    helpers.common(root)
    actions = root.add_subparsers(dest="plan_action", required=True)
    validate = actions.add_parser("validate")
    helpers.common(validate)
    validate.add_argument("--file", required=True)
    validate.set_defaults(handler=lambda args: validate_plan(load_plan(args.file), find_repo_root(args.repo_root)))
    diff = actions.add_parser("diff")
    helpers.common(diff)
    diff.add_argument("--file", required=True)
    diff.set_defaults(handler=lambda args: Service(args.repo_root).plan_diff(args.file))
    apply = actions.add_parser("apply")
    helpers.common(apply)
    apply.add_argument("--file", required=True)
    apply.set_defaults(handler=lambda args: Service(args.repo_root).plan_apply(args.file))
    selective = actions.add_parser("selective-replan")
    helpers.common(selective)
    selective.add_argument("--file", required=True)
    selective.add_argument("--expected-revision", required=True, type=int)
    selective.set_defaults(handler=lambda args: Service(args.repo_root).selective_replan(load_plan(args.file), args.expected_revision))
    scaffold_parser = actions.add_parser("scaffold")
    helpers.common(scaffold_parser)
    scaffold_parser.add_argument("shape", choices=["fanout", "producer-consumers", "benchmark", "integration-barrier"])
    scaffold_parser.add_argument("--output")
    def handle_scaffold(args):
        value = scaffold(args.shape)
        if args.output:
            from ..projections import atomic_write_json
            atomic_write_json(Path(args.output), value)
            return {"output": str(Path(args.output).resolve()), "plan": value}
        return value
    scaffold_parser.set_defaults(handler=handle_scaffold)
