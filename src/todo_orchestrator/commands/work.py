"""Automatic pickup, task lifecycle, context, and delta commands."""

from __future__ import annotations

from ..service import Service


def register(subparsers, helpers) -> None:
    continue_parser = subparsers.add_parser("continue")
    helpers.common(continue_parser)
    continue_parser.add_argument("--session-token")
    continue_parser.add_argument("--task-id")
    continue_parser.add_argument("--owner-system", choices=["coding-workflow"])
    continue_parser.add_argument("--owner-instance")
    continue_parser.set_defaults(handler=lambda args: Service(args.repo_root).continue_work(
        session_token=args.session_token, task_id=args.task_id,
        owner_system=args.owner_system, owner_instance_id=args.owner_instance,
    ))

    claim = subparsers.add_parser("claim")
    helpers.common(claim)
    claim.add_argument("task_id")
    claim.add_argument("--session-token")
    claim.set_defaults(handler=lambda args: Service(args.repo_root).continue_work(session_token=args.session_token, task_id=args.task_id))

    ready = subparsers.add_parser("ready")
    helpers.common(ready)
    ready.set_defaults(handler=lambda args: Service(args.repo_root).ready())

    explain = subparsers.add_parser("explain")
    helpers.common(explain)
    explain.add_argument("task_id")
    explain.set_defaults(handler=lambda args: Service(args.repo_root).explain(args.task_id))

    context = subparsers.add_parser("context")
    helpers.common(context)
    context.add_argument("--claim-token", required=True)
    context.add_argument("--section", choices=["dependencies", "interfaces", "history", "siblings"])
    context.set_defaults(handler=lambda args: Service(args.repo_root).context(args.claim_token, args.section))

    changes = subparsers.add_parser("changes")
    helpers.common(changes)
    changes.add_argument("--since", type=int, required=True)
    changes.add_argument("--claim-token")
    changes.set_defaults(handler=lambda args: Service(args.repo_root).changes(args.since, args.claim_token))

    pulse = subparsers.add_parser("pulse")
    helpers.common(pulse)
    pulse.add_argument("--claim-token", required=True)
    pulse.set_defaults(handler=lambda args: Service(args.repo_root).pulse(args.claim_token))

    release = subparsers.add_parser("release")
    helpers.common(release)
    release.add_argument("--claim-token", required=True)
    release.add_argument("--status", default="in_progress")
    release.add_argument("--reason")
    release.set_defaults(handler=lambda args: Service(args.repo_root).release(args.claim_token, args.status, args.reason))

    block = subparsers.add_parser("block")
    helpers.common(block)
    block.add_argument("--claim-token", required=True)
    block.add_argument("--reason", required=True)
    block.add_argument("--note", default="")
    block.set_defaults(handler=lambda args: Service(args.repo_root).handoff(args.claim_token, note=args.note, status="blocked", reason=args.reason))

    handoff = subparsers.add_parser("handoff")
    helpers.common(handoff)
    handoff.add_argument("--claim-token", required=True)
    handoff.add_argument("--note", default="")
    handoff.add_argument("--status", default="in_progress", choices=["in_progress", "blocked"])
    handoff.add_argument("--reason")
    handoff.set_defaults(handler=lambda args: Service(args.repo_root).handoff(args.claim_token, note=args.note, status=args.status, reason=args.reason))

    complete = subparsers.add_parser("complete")
    helpers.common(complete)
    complete.add_argument("--claim-token", required=True)
    complete.add_argument("--disposition", required=True)
    complete.add_argument("--note", default="")
    complete.set_defaults(handler=lambda args: Service(args.repo_root).complete(args.claim_token, args.disposition, args.note))
