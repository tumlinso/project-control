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
from typing import Any, Sequence


PREPARE_WORKSPACES_CONFIRMATION = "PREPARE-RUN-WORKSPACES"
RECONCILE_WORKSPACE_BASE_CONFIRMATION = "RECONCILE-WORKSPACE-BASE"
MARK_RUN_WORKSPACES_CLEANUP_ELIGIBLE_CONFIRMATION = "MARK-RUN-WORKSPACES-CLEANUP-ELIGIBLE"


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


def _exclusive_integrator_destinations(
    conn: Any,
    run_id: str,
    existing_lane_ids: set[str],
) -> list[dict[str, str]]:
    """Find missing exclusive destinations needed by pending producer artifacts."""
    rows = conn.execute(
        "SELECT l.id AS lane_id,lt.task_id FROM workflow_lanes l "
        "JOIN workflow_lane_tasks lt ON lt.lane_id=l.id "
        "WHERE l.run_id=? AND l.role IN ('integrator','validator') "
        "AND l.workspace_mode='exclusive' "
        "AND l.state IN ('ready','active') AND lt.state IN ('queued','active') "
        "AND lt.position=(SELECT MIN(head.position) FROM workflow_lane_tasks head "
        "WHERE head.lane_id=l.id AND head.state IN ('queued','active')) ORDER BY l.id",
        (run_id,),
    ).fetchall()
    destinations: list[dict[str, str]] = []
    for row in rows:
        lane_id = str(row["lane_id"])
        if lane_id in existing_lane_ids:
            continue
        task_id = str(row["task_id"])
        artifact_bases = conn.execute(
            "SELECT DISTINCT w.base_commit AS workspace_base,a.base_commit AS artifact_base "
            "FROM workflow_patch_artifacts a JOIN workflow_workspaces w ON w.id=a.workspace_id "
            "WHERE w.run_id=? AND w.integration_task_id=? "
            "AND w.mode IN ('isolated_merge','contract_split') "
            "AND w.state='artifact_ready' AND a.state='pending'",
            (run_id, task_id),
        ).fetchall()
        participant_bases = {
            str(item["base_commit"])
            for item in conn.execute(
                "SELECT DISTINCT base_commit FROM workflow_workspaces WHERE run_id=? "
                "AND integration_task_id=? AND mode IN ('isolated_merge','contract_split')",
                (run_id, task_id),
            ).fetchall()
        }
        if not participant_bases and not artifact_bases:
            continue
        observed_bases = participant_bases | {
            str(item["workspace_base"]) for item in artifact_bases
        } | {str(item["artifact_base"]) for item in artifact_bases}
        if len(observed_bases) != 1:
            raise ValueError(
                f"producer artifacts do not share the exact integration base: {task_id}"
            )
        destinations.append({
            "lane_id": lane_id,
            "task_id": task_id,
            "base_commit": observed_bases.pop(),
        })
    return destinations


def prepare_run_workspaces(
    repo: str | Path,
    plan_path: str | Path,
    run_id: str,
    *,
    apply: bool = False,
    confirmation: str | None = None,
) -> dict[str, object]:
    """Prepare claimable managed lanes and required exclusive destinations.

    Native schema-v3 plans declare workspace intent but Todo deliberately keeps
    Git materialization outside the plan transaction.  An exclusive integrator
    destination becomes preparable when its live queue head has a pending
    producer artifact, even if the integration task is not claimable yet. This
    owner-only Project Control operation closes that boundary without exposing
    a model-facing workspace mutation tool or bypassing Todo's WorkspaceService.
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
        all_existing_lane_ids = {
            str(row["lane_id"])
            for row in conn.execute(
                "SELECT lane_id FROM workflow_workspaces WHERE run_id=?", (run_id,)
            )
        }
        lane_modes = {
            str(row["id"]): str(row["workspace_mode"])
            for row in conn.execute("SELECT id,workspace_mode FROM workflow_lanes WHERE run_id=?", (run_id,))
        }
        exclusive_destinations = _exclusive_integrator_destinations(
            conn, run_id, all_existing_lane_ids
        )
        participant_integration_bases: dict[str, set[str]] = {}
        for row in conn.execute(
            "SELECT integration_task_id,base_commit FROM workflow_workspaces "
            "WHERE run_id=? AND integration_task_id IS NOT NULL "
            "AND mode IN ('isolated_merge','contract_split')",
            (run_id,),
        ).fetchall():
            participant_integration_bases.setdefault(
                str(row["integration_task_id"]), set()
            ).add(str(row["base_commit"]))

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
        workspace_base = base_commit
        if integration_task_id:
            observed_bases = participant_integration_bases.get(str(integration_task_id), set())
            if len(observed_bases) > 1:
                raise ValueError(
                    "existing participants do not share the exact integration base: "
                    f"{integration_task_id}"
                )
            if observed_bases:
                workspace_base = next(iter(observed_bases))
        name = _workspace_name(lane_id)
        pending.append({
            "lane_id": lane_id,
            "task_id": str(candidate["task_id"]),
            "mode": mode,
            "integration_task_id": str(integration_task_id) if integration_task_id else None,
            "base_commit": workspace_base,
            "worktree_path": str(service.paths.state_dir / "workflow-workspaces" / name),
            "branch": f"codex/{name}",
        })
    for destination in exclusive_destinations:
        lane_id = destination["lane_id"]
        spec = lane_specs.get(lane_id)
        if spec is None:
            raise ValueError(f"active lane is absent from the supplied plan: {lane_id}")
        workspace = dict(spec.get("workspace", {}))
        mode = str(workspace.get("mode", "exclusive"))
        if lane_modes.get(lane_id) != mode:
            raise ValueError(f"workspace mode differs from live lane contract: {lane_id}")
        if mode != "exclusive":
            raise ValueError(f"integrator destination must be exclusive: {lane_id}")
        name = _workspace_name(lane_id)
        pending.append({
            "lane_id": lane_id,
            "task_id": destination["task_id"],
            "mode": mode,
            "integration_task_id": destination["task_id"],
            "base_commit": destination["base_commit"],
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
            base_commit=str(item["base_commit"]),
            worktree_path=Path(str(item["worktree_path"])),
            branch=str(item["branch"]),
            integration_task_id=(str(item["integration_task_id"]) if item["integration_task_id"] else None),
        ))
    result["status"] = "prepared"
    result["prepared"] = prepared
    return result


def reconcile_workspace_base(
    repo: str | Path,
    run_id: str,
    lane_id: str,
    base_commit: str,
    *,
    reason: str,
    apply: bool = False,
    confirmation: str | None = None,
) -> dict[str, object]:
    """Reconcile a clean lane workspace after it incorporated a newer base."""
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.service import repository_identity
    from todo_orchestrator.workflow.workspaces import WorkspaceService

    repository = Path(repo).expanduser().resolve()
    service = Service(repository, mutation_mode="self_debug")
    project_uuid = str(service.project["project_uuid"])
    canonical_base = _git(repository, "rev-parse", "--verify", f"{base_commit}^{{commit}}")
    with service.db.read() as conn:
        rows = conn.execute(
            "SELECT id,base_commit,worktree_path,state FROM workflow_workspaces "
            "WHERE run_id=? AND lane_id=? AND state IN "
            "('active','artifact_ready','queued','conflict','awaiting_gates','gate_failed','quarantined')",
            (run_id, lane_id),
        ).fetchall()
    if len(rows) != 1:
        raise ValueError("expected exactly one active workspace for the requested run lane")
    current = dict(rows[0])
    preview: dict[str, object] = {
        "status": "ready",
        "run_id": run_id,
        "lane_id": lane_id,
        "workspace_id": current["id"],
        "old_base_commit": current["base_commit"],
        "base_commit": canonical_base,
        "worktree_path": current["worktree_path"],
    }
    if not apply:
        return preview
    if confirmation != RECONCILE_WORKSPACE_BASE_CONFIRMATION:
        raise ValueError(f"--confirm must equal {RECONCILE_WORKSPACE_BASE_CONFIRMATION}")
    manager = WorkspaceService(
        service.db,
        managed_root=service.paths.state_dir / "workflow-workspaces",
        repository_identity_resolver=lambda root: repository_identity(root, project_uuid),
    )
    result = manager.reconcile_workspace_base(
        repository_root=repository,
        run_id=run_id,
        lane_id=lane_id,
        base_commit=canonical_base,
        reason=reason,
    )
    result["status"] = "reconciled"
    return result


def mark_run_workspaces_cleanup_eligible(
    repo: str | Path,
    run_id: str,
    *,
    apply: bool = False,
    confirmation: str | None = None,
) -> dict[str, object]:
    """Mark every terminal workspace in a completed run safe for cleanup."""
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.service import repository_identity
    from todo_orchestrator.workflow.workspaces import WorkspaceService, material_dirty_paths

    repository = Path(repo).expanduser().resolve()
    service = Service(repository, mutation_mode="self_debug")
    project_uuid = str(service.project["project_uuid"])
    with service.db.read() as conn:
        run = conn.execute("SELECT status FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"workflow run does not exist: {run_id}")
        if str(run["status"]) != "completed":
            raise ValueError(f"workflow run is not completed: {run_id}")
        nonclosed_lane = conn.execute(
            "SELECT id FROM workflow_lanes WHERE run_id=? AND state!='closed' ORDER BY id LIMIT 1",
            (run_id,),
        ).fetchone()
        if nonclosed_lane is not None:
            raise ValueError(f"workflow run still has an open lane: {nonclosed_lane['id']}")
        rows = [dict(row) for row in conn.execute(
            "SELECT id,lane_id,state,worktree_path,branch,cleanup_eligible "
            "FROM workflow_workspaces WHERE run_id=? ORDER BY lane_id,id",
            (run_id,),
        ).fetchall()]
        active_dispatch = conn.execute(
            "SELECT d.id FROM workflow_dispatches d JOIN workflow_workspaces w ON w.id=d.workspace_id "
            "WHERE w.run_id=? AND d.state='active' ORDER BY d.id LIMIT 1",
            (run_id,),
        ).fetchone()
    if active_dispatch is not None:
        raise ValueError(f"workflow run still has an active workspace owner: {active_dispatch['id']}")
    if not rows:
        raise ValueError(f"workflow run has no managed workspaces: {run_id}")
    for row in rows:
        if str(row["state"]) not in {"integrated", "rejected"}:
            raise ValueError(f"workspace is not terminal: {row['id']} ({row['state']})")
        path = Path(str(row["worktree_path"])) if row.get("worktree_path") else None
        if path is None or not path.exists():
            raise ValueError(f"workspace path is unavailable: {row['id']}")
        if material_dirty_paths(path):
            raise ValueError(f"workspace is dirty and must be preserved: {row['id']}")

    pending = [row for row in rows if not bool(row["cleanup_eligible"])]
    result: dict[str, object] = {
        "status": "ready" if pending else "noop",
        "run_id": run_id,
        "pending": [
            {"workspace_id": row["id"], "lane_id": row["lane_id"], "branch": row["branch"]}
            for row in pending
        ],
        "marked": [],
    }
    if not apply or not pending:
        return result
    if confirmation != MARK_RUN_WORKSPACES_CLEANUP_ELIGIBLE_CONFIRMATION:
        raise ValueError(
            f"--confirm must equal {MARK_RUN_WORKSPACES_CLEANUP_ELIGIBLE_CONFIRMATION}"
        )
    manager = WorkspaceService(
        service.db,
        managed_root=service.paths.state_dir / "workflow-workspaces",
        repository_identity_resolver=lambda root: repository_identity(root, project_uuid),
    )
    result["marked"] = [
        manager.mark_cleanup_eligible(workspace_id=str(row["id"])) for row in pending
    ]
    result["status"] = "marked"
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
    reconcile = commands.add_parser(
        "reconcile-workspace-base", help="reconcile a clean workspace after an earlier integration"
    )
    reconcile.add_argument("--repo", required=True)
    reconcile.add_argument("--run", required=True)
    reconcile.add_argument("--lane", required=True)
    reconcile.add_argument("--base", required=True)
    reconcile.add_argument("--reason", required=True)
    reconcile.add_argument("--apply", action="store_true")
    reconcile.add_argument("--confirm")
    cleanup = commands.add_parser(
        "mark-run-workspaces-cleanup-eligible",
        help="mark clean terminal workspaces in a completed run safe for cleanup",
    )
    cleanup.add_argument("--repo", required=True)
    cleanup.add_argument("--run", required=True)
    cleanup.add_argument("--apply", action="store_true")
    cleanup.add_argument("--confirm")
    args = parser.parse_args(argv)
    if args.command == "recover":
        if args.inspect_only:
            print(json.dumps(inspect_recovery(args.repo, args.task), sort_keys=True, separators=(",", ":")))
        else:
            recover(args.repo, reason=args.reason, task_id=args.task)
    elif args.command == "prepare-run-workspaces":
        result = prepare_run_workspaces(
            args.repo, args.plan, args.run, apply=args.apply, confirmation=args.confirm,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    elif args.command == "reconcile-workspace-base":
        result = reconcile_workspace_base(
            args.repo, args.run, args.lane, args.base, reason=args.reason,
            apply=args.apply, confirmation=args.confirm,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        result = mark_run_workspaces_cleanup_eligible(
            args.repo, args.run, apply=args.apply, confirmation=args.confirm,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
