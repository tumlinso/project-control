"""Additive read-only semantic work-state and history commands."""

from __future__ import annotations

from ..semantic import SemanticReader


def register(subparsers, helpers) -> None:
    root = subparsers.add_parser("semantic", help="Read-only normalized work semantics")
    actions = root.add_subparsers(dest="semantic_action", required=True)

    state = actions.add_parser("state")
    helpers.common(state)
    state.add_argument("--task", dest="task_id")
    state.add_argument("--prefix")
    state.add_argument("--program")
    state.add_argument("--current-only", action="store_true")
    state.set_defaults(handler=lambda args: SemanticReader(args.repo_root).state(
        task_id=args.task_id, prefix=args.prefix, program=args.program, current_only=args.current_only,
    ))

    anchor = actions.add_parser("anchor")
    helpers.common(anchor)
    anchor.add_argument("--task", dest="task_id")
    anchor.add_argument("--checkpoint", dest="checkpoint_id")
    anchor.add_argument("--interface", dest="interface_id")
    anchor.add_argument("--revision", type=int)
    anchor.add_argument("--phase", choices=["created", "first_claim", "completed"], default="created")
    anchor.set_defaults(handler=lambda args: SemanticReader(args.repo_root).anchor(
        task_id=args.task_id, checkpoint_id=args.checkpoint_id, interface_id=args.interface_id,
        revision=args.revision, phase=args.phase,
    ))

    delta = actions.add_parser("delta")
    helpers.common(delta)
    delta.add_argument("--since-revision", type=int)
    delta.add_argument("--since-task")
    delta.add_argument("--since-checkpoint")
    delta.add_argument("--since-interface")
    delta.add_argument("--until-revision", type=int)
    delta.add_argument("--task-phase", choices=["created", "first_claim", "completed"], default="created")
    delta.set_defaults(handler=lambda args: SemanticReader(args.repo_root).delta(
        since_revision=args.since_revision, since_task=args.since_task,
        since_checkpoint=args.since_checkpoint, since_interface=args.since_interface,
        until_revision=args.until_revision, task_phase=args.task_phase,
    ))

    workflow = actions.add_parser("workflow")
    helpers.common(workflow)
    workflow.set_defaults(handler=lambda args: SemanticReader(args.repo_root).workflow())
