"""CLI surface for restricted child executions."""

from __future__ import annotations

from ..child_execution import (
    adopt_child_execution,
    authorize_child_execution,
    cancel_child_execution,
    child_execution_status,
    disposition_child_execution,
    heartbeat_child_execution,
    recover_child_execution,
    report_child_result,
)
from ..service import Service
from ..sessions import authenticate_claim, token_hash


def _group(subparsers, helpers):
    root = subparsers.add_parser("child")
    helpers.common(root)
    nested = root.add_subparsers(dest="child_action", required=True)
    return {
        name: nested.add_parser(name)
        for name in ("create", "heartbeat", "report", "accept", "reject", "stale", "supersede", "adopt", "cancel", "recover", "status")
    }


def _mutate(service: Service, *, event: str, entity_id, actor, payload, operation):
    result, revision, projection = service.mutate(
        actor=actor,
        entity_type="child_execution",
        entity_id=entity_id,
        event_type=event,
        payload=payload,
        operation=operation,
    )
    return {**result, "project_revision": revision, "projection": projection}


def register(subparsers, helpers) -> None:
    child = _group(subparsers, helpers)
    for parser in child.values():
        helpers.common(parser)

    create = child["create"]
    create.add_argument("--claim-token", required=True)
    create.add_argument("--objective", required=True)
    create.add_argument("--scope", action="append", required=True)
    create.add_argument("--gate", action="append", default=[])
    create.add_argument("--access", choices=["read", "write"], default="write")
    create.add_argument("--max-attempts", type=int, default=1)
    create.add_argument("--lease-seconds", type=int, default=300)
    def create_child(args):
        service = Service(args.repo_root)
        return _mutate(
            service,
            event="child.authorized",
            entity_id=lambda value: value["child_execution_id"],
            actor=_claim_actor(service, args.claim_token),
            payload={"scopes": args.scope, "gates": args.gate, "access": args.access},
            operation=lambda conn, revision: authorize_child_execution(
                conn,
                service.paths.repo_root,
                args.claim_token,
                objective=args.objective,
                scopes=args.scope,
                gates=args.gate,
                access=args.access,
                max_attempts=args.max_attempts,
                lease_seconds=args.lease_seconds,
            ),
        )
    create.set_defaults(handler=create_child)

    heartbeat = child["heartbeat"]
    heartbeat.add_argument("--child-token", required=True)
    heartbeat.add_argument("--lease-seconds", type=int, default=300)
    heartbeat.set_defaults(handler=lambda args: _child_token_mutation(
        args,
        "child.heartbeat",
        lambda conn: heartbeat_child_execution(conn, args.child_token, lease_seconds=args.lease_seconds),
    ))

    report = child["report"]
    report.add_argument("--child-token", required=True)
    report.add_argument("--status", required=True, choices=["succeeded", "ready_for_acceptance", "failed", "needs_codex"])
    report.add_argument("--summary", default="")
    report.add_argument("--changed-path", action="append", default=[])
    for field in (
        "source-identity", "context-packet", "patch", "candidate-verification",
        "acceptance-verification", "telemetry", "reviewer-evidence", "compact-logs",
    ):
        report.add_argument(f"--{field}-ref")
    report.set_defaults(handler=lambda args: _child_token_mutation(
        args,
        "child.reported",
        lambda conn: report_child_result(
            conn,
            args.child_token,
            status=args.status,
            summary=args.summary,
            changed_paths=args.changed_path,
            references={
                field.replace("-", "_"): getattr(args, field.replace("-", "_") + "_ref")
                for field in (
                    "source-identity", "context-packet", "patch", "candidate-verification",
                    "acceptance-verification", "telemetry", "reviewer-evidence", "compact-logs",
                )
                if getattr(args, field.replace("-", "_") + "_ref")
            },
        ),
    ))

    for action in ("accept", "reject", "stale", "supersede"):
        parser = child[action]
        parser.add_argument("child_execution_id")
        parser.add_argument("--claim-token", required=True)
        parser.set_defaults(handler=lambda args, selected=action: _parent_mutation(
            args,
            f"child.{selected}",
            lambda conn: disposition_child_execution(
                conn, args.claim_token, args.child_execution_id, action=selected,
            ),
        ))

    adopt = child["adopt"]
    adopt.add_argument("child_execution_id")
    adopt.add_argument("--claim-token", required=True)
    adopt.set_defaults(handler=lambda args: _parent_mutation(
        args,
        "child.adopted",
        lambda conn: adopt_child_execution(conn, args.claim_token, args.child_execution_id),
    ))

    cancel = child["cancel"]
    cancel.add_argument("child_execution_id")
    cancel.add_argument("--claim-token", required=True)
    cancel.set_defaults(handler=lambda args: _parent_mutation(
        args,
        "child.canceled",
        lambda conn: cancel_child_execution(conn, args.claim_token, args.child_execution_id),
    ))

    recover = child["recover"]
    recover.add_argument("child_execution_id")
    recover.add_argument("--claim-token", required=True)
    recover.add_argument("--lease-seconds", type=int, default=300)
    recover.set_defaults(handler=lambda args: _parent_mutation(
        args,
        "child.recovered",
        lambda conn: recover_child_execution(
            conn,
            args.claim_token,
            args.child_execution_id,
            lease_seconds=args.lease_seconds,
        ),
    ))

    status = child["status"]
    status.add_argument("child_execution_id")
    status.add_argument("--claim-token", required=True)
    def status_child(args):
        service = Service(args.repo_root)
        with service.db.read() as conn:
            return child_execution_status(conn, args.claim_token, args.child_execution_id)
    status.set_defaults(handler=status_child)


def _claim_actor(service: Service, claim_token: str) -> str:
    with service.db.read() as conn:
        return str(authenticate_claim(conn, claim_token)["session_id"])


def _child_actor(service: Service, child_token: str) -> str | None:
    with service.db.read() as conn:
        row = conn.execute(
            "SELECT p.session_id FROM child_attempts a "
            "JOIN child_executions c ON c.id=a.child_execution_id "
            "JOIN claims p ON p.id=c.parent_claim_id WHERE a.token_hash=?",
            (token_hash(child_token),),
        ).fetchone()
        return str(row[0]) if row else None


def _parent_mutation(args, event: str, operation):
    service = Service(args.repo_root)
    return _mutate(
        service,
        event=event,
        entity_id=args.child_execution_id,
        actor=_claim_actor(service, args.claim_token),
        payload={},
        operation=lambda conn, revision: operation(conn),
    )


def _child_token_mutation(args, event: str, operation):
    service = Service(args.repo_root)
    return _mutate(
        service,
        event=event,
        entity_id=lambda value: value["child_execution_id"],
        actor=_child_actor(service, args.child_token),
        payload={},
        operation=lambda conn, revision: operation(conn),
    )
