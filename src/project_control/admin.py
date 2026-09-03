"""Owner-only administrative forwarding to Todo Orchestrator.

This module deliberately contains no recovery policy.  It verifies the shared
runtime and then invokes Todo Orchestrator's canonical owner recovery API.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Sequence


PREPARE_WORKSPACES_CONFIRMATION = "PREPARE-RUN-WORKSPACES"


def _runtime_identity() -> object:
    from .workflow_binding import runtime_identity

    return runtime_identity()


def inspect_recovery(repo: str | Path, task_id: str | None = None) -> dict[str, object]:
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.admin import inspect_owner_recovery
    from todo_orchestrator.workflow.recovery import RecoveryEngine

    service = Service(repo, mutation_mode="self_debug")
    engine = RecoveryEngine(service.db, service.paths.repo_root, str(service.project["project_uuid"]))
    return inspect_owner_recovery(engine, task_id)


def recover(repo: str | Path, *, reason: str, task_id: str | None = None) -> None:
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.admin import run_owner_recovery
    from todo_orchestrator.workflow.recovery import RecoveryEngine

    service = Service(repo, mutation_mode="self_debug")
    engine = RecoveryEngine(service.db, service.paths.repo_root, str(service.project["project_uuid"]))
    run_owner_recovery(
        engine,
        database_path=service.paths.db_file,
        reason=reason,
        task_id=task_id,
    )


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _workspace_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-.")
    if not name:
        raise ValueError("lane ID cannot produce a safe workspace name")
    return name


def prepare_run_workspaces(
    repo: str | Path,
    plan_path: str | Path,
    run_id: str,
    *,
    apply: bool = False,
    confirmation: str | None = None,
) -> dict[str, object]:
    """Prepare only currently claimable managed lanes from an applied native plan.

    Native schema-v3 plans declare workspace intent but Todo deliberately keeps
    Git materialization outside the plan transaction.  This owner-only Project
    Control operation closes that boundary without exposing a model-facing
    workspace mutation tool or bypassing Todo's WorkspaceService.
    """

    _runtime_identity()
    from todo_orchestrator.plan import load_plan
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.lanes import lane_candidates
    from todo_orchestrator.workflow.service import repository_identity
    from todo_orchestrator.workflow.workspaces import WorkspaceService

    repository = Path(repo).expanduser().resolve()
    plan_file = Path(plan_path).expanduser().resolve()
    plan = load_plan(plan_file)
    runs = [item for item in plan.get("runs", []) if str(item.get("id")) == run_id]
    if len(runs) != 1:
        raise ValueError(f"native plan must contain exactly one run named {run_id}")
    run = runs[0]
    lane_specs = {str(item["id"]): item for item in run.get("lanes", [])}

    if _git(repository, "status", "--porcelain=v1", "-z"):
        raise ValueError("repository must be clean before managed workspaces are prepared")
    base_commit = _git(repository, "rev-parse", "HEAD")
    service = Service(repository, mutation_mode="self_debug")
    project_uuid = str(service.project["project_uuid"])
    repo_identity = repository_identity(repository, project_uuid)
    with service.db.read() as conn:
        active = conn.execute(
            "SELECT 1 FROM workflow_runs WHERE id=? AND status='active'", (run_id,)
        ).fetchone()
        if active is None:
            raise ValueError(f"workflow run is not active: {run_id}")
        candidates = lane_candidates(conn, run_id)
        existing = {
            str(row["lane_id"]): dict(row)
            for row in conn.execute(
                "SELECT * FROM workflow_workspaces WHERE run_id=? AND state IN "
                "('active','artifact_ready','queued','conflict','awaiting_gates','gate_failed','integrated')",
                (run_id,),
            )
        }
        lane_modes = {
            str(row["id"]): str(row["workspace_mode"])
            for row in conn.execute("SELECT id,workspace_mode FROM workflow_lanes WHERE run_id=?", (run_id,))
        }

    pending: list[dict[str, object]] = []
    for candidate in candidates:
        lane_id = str(candidate["lane_id"])
        if lane_id in existing:
            continue
        spec = lane_specs.get(lane_id)
        if spec is None:
            raise ValueError(f"active lane is absent from the supplied plan: {lane_id}")
        workspace = dict(spec.get("workspace", {}))
        mode = str(workspace.get("mode", "exclusive"))
        if lane_modes.get(lane_id) != mode:
            raise ValueError(f"workspace mode differs from live lane contract: {lane_id}")
        if mode not in {"isolated_merge", "contract_split"}:
            continue
        integration_task_id = workspace.get("integration_task_id")
        if mode == "isolated_merge" and not integration_task_id:
            raise ValueError(f"isolated lane lacks integration_task_id: {lane_id}")
        name = _workspace_name(lane_id)
        pending.append({
            "lane_id": lane_id,
            "task_id": str(candidate["task_id"]),
            "mode": mode,
            "integration_task_id": str(integration_task_id) if integration_task_id else None,
            "base_commit": base_commit,
            "worktree_path": str(service.paths.state_dir / "workflow-workspaces" / name),
            "branch": f"codex/{name}",
        })

    result: dict[str, object] = {
        "status": "ready" if pending else "noop",
        "run_id": run_id,
        "base_commit": base_commit,
        "pending": pending,
        "prepared": [],
    }
    if not apply or not pending:
        return result
    if confirmation != PREPARE_WORKSPACES_CONFIRMATION:
        raise ValueError(f"--confirm must equal {PREPARE_WORKSPACES_CONFIRMATION}")

    manager = WorkspaceService(
        service.db,
        managed_root=service.paths.state_dir / "workflow-workspaces",
        repository_identity_resolver=lambda root: repository_identity(root, project_uuid),
    )
    prepared: list[dict[str, object]] = []
    for item in pending:
        prepared.append(manager.create_workspace(
            repository_root=repository,
            repository_identity=repo_identity,
            run_id=run_id,
            lane_id=str(item["lane_id"]),
            mode=str(item["mode"]),
            base_commit=base_commit,
            worktree_path=Path(str(item["worktree_path"])),
            branch=str(item["branch"]),
            integration_task_id=(str(item["integration_task_id"]) if item["integration_task_id"] else None),
        ))
    result["status"] = "prepared"
    result["prepared"] = prepared
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-control-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("recover", help="inspect or safely recover workflow ownership")
    command.add_argument("--repo", required=True)
    command.add_argument("--task")
    command.add_argument("--reason", required=True)
    command.add_argument("--inspect-only", action="store_true")
    prepare = commands.add_parser("prepare-run-workspaces", help="prepare currently claimable managed run lanes")
    prepare.add_argument("--repo", required=True)
    prepare.add_argument("--plan", required=True)
    prepare.add_argument("--run", required=True)
    prepare.add_argument("--apply", action="store_true")
    prepare.add_argument("--confirm")
    args = parser.parse_args(argv)
    if args.command == "recover":
        if args.inspect_only:
            print(json.dumps(inspect_recovery(args.repo, args.task), sort_keys=True, separators=(",", ":")))
        else:
            recover(args.repo, reason=args.reason, task_id=args.task)
    else:
        result = prepare_run_workspaces(
            args.repo, args.plan, args.run, apply=args.apply, confirmation=args.confirm,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
