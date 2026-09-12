"""Owner-only administrative forwarding to Todo Orchestrator.

This module deliberately contains no recovery policy.  It verifies the shared
runtime and then invokes Todo Orchestrator's canonical owner recovery API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Sequence


PREPARE_WORKSPACES_CONFIRMATION = "PREPARE-RUN-WORKSPACES"
RECONCILE_WORKSPACE_BASE_CONFIRMATION = "RECONCILE-WORKSPACE-BASE"
MARK_RUN_WORKSPACES_CLEANUP_ELIGIBLE_CONFIRMATION = "MARK-RUN-WORKSPACES-CLEANUP-ELIGIBLE"
ADVANCE_PRODUCER_WAVE_CONFIRMATION = "ADVANCE-PRODUCER-WAVE"


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


def _verified_schedule(plan_file: Path) -> dict[str, object] | None:
    schedule_file = plan_file.parent / "integration_schedule.json"
    manifest_file = plan_file.parent.parent / "MANIFEST.sha256"
    if not schedule_file.is_file() or not manifest_file.is_file():
        return None
    if plan_file.is_symlink() or schedule_file.is_symlink() or manifest_file.is_symlink():
        raise ValueError("workflow package inputs must not be symbolic links")
    package_root = manifest_file.parent.resolve()
    try:
        plan_name = plan_file.resolve().relative_to(package_root).as_posix()
        schedule_name = schedule_file.resolve().relative_to(package_root).as_posix()
    except ValueError as exc:
        raise ValueError("workflow package inputs escape the manifest root") from exc
    entries: dict[str, str] = {}
    for line in manifest_file.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64:
            raise ValueError("workflow package manifest is malformed")
        entries[name] = digest
    if plan_name not in entries or schedule_name not in entries:
        raise ValueError("workflow package manifest does not seal plan and schedule")
    for path, name in ((plan_file, plan_name), (schedule_file, schedule_name)):
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        if observed != entries[name]:
            raise ValueError(f"workflow package manifest mismatch: {name}")
    schedule = json.loads(schedule_file.read_text(encoding="utf-8"))
    if not isinstance(schedule, dict) or not isinstance(schedule.get("phases"), list):
        raise ValueError("integration schedule must contain a phases list")
    for phase in schedule["phases"]:
        if not isinstance(phase, dict):
            raise ValueError("integration schedule phases must be objects")
        integration_task = phase.get("integration_task")
        required_tasks = phase.get("required_tasks")
        if not isinstance(integration_task, str) or not integration_task.strip():
            raise ValueError("integration schedule task IDs must be non-empty strings")
        if not isinstance(required_tasks, list) or any(
            not isinstance(task_id, str) or not task_id.strip() for task_id in required_tasks
        ):
            raise ValueError("integration schedule required_tasks must contain task IDs")
    return schedule


def _scheduled_integration_task(
    plan_file: Path, lane: dict[str, object], current_task_id: str | None = None,
) -> str | None:
    """Resolve a single bootstrap integration target from a sealed WF2 schedule.

    Schema-v3 plans may intentionally defer the binding while keeping the
    phase schedule beside the native plan.  Only an unambiguous lane-to-phase
    mapping is safe to materialize here; multi-phase lanes remain a runtime
    lifecycle concern and fail closed.
    """
    schedule = _verified_schedule(plan_file)
    if schedule is None:
        return None
    lane_tasks = {str(task_id) for task_id in lane.get("tasks", [])}
    if current_task_id:
        current_matches = {
            str(phase["integration_task"])
            for phase in schedule.get("phases", [])
            if current_task_id in {str(task_id) for task_id in phase.get("required_tasks", [])}
        }
        if len(current_matches) > 1:
            raise ValueError(
                "current task belongs to multiple integration phases: "
                f"{current_task_id}"
            )
        if current_matches:
            return next(iter(current_matches))
    matches = {
        str(phase["integration_task"])
        for phase in schedule.get("phases", [])
        if lane_tasks.intersection(str(task_id) for task_id in phase.get("required_tasks", []))
    }
    if len(matches) > 1:
        raise ValueError(
            "isolated lane spans multiple integration phases and requires dynamic binding: "
            f"{lane.get('id')}"
        )
    return next(iter(matches), None)


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
        "AND l.state NOT IN ('closed','cancelled') "
        "AND lt.state IN ('queued','active') "
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
    lane_id: str | None = None,
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
    requested_lane_id = lane_id
    if requested_lane_id is not None and requested_lane_id not in lane_specs:
        raise ValueError(f"requested lane is absent from the supplied plan: {requested_lane_id}")

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
        if requested_lane_id is not None:
            candidates = [
                item for item in candidates
                if str(item["lane_id"]) == requested_lane_id
            ]
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
        valid_integration_tasks = {
            str(row["task_id"])
            for row in conn.execute(
                "SELECT lt.task_id FROM workflow_lanes l JOIN workflow_lane_tasks lt "
                "ON lt.lane_id=l.id WHERE l.run_id=? AND l.role IN ('integrator','validator') "
                "AND l.workspace_mode='exclusive'",
                (run_id,),
            )
        }
    if requested_lane_id is not None and requested_lane_id not in lane_modes:
        raise ValueError(f"requested lane is absent from the live run: {requested_lane_id}")
    if requested_lane_id is not None and not candidates and requested_lane_id not in existing:
        raise ValueError(f"requested lane is not ready for workspace preparation: {requested_lane_id}")

    pending: list[dict[str, object]] = []
    for candidate in candidates:
        candidate_lane_id = str(candidate["lane_id"])
        if candidate_lane_id in existing:
            continue
        spec = lane_specs.get(candidate_lane_id)
        if spec is None:
            raise ValueError(f"active lane is absent from the supplied plan: {candidate_lane_id}")
        workspace = dict(spec.get("workspace", {}))
        candidate_task_id = str(candidate["task_id"])
        if candidate_task_id not in {str(task_id) for task_id in spec.get("tasks", [])}:
            raise ValueError(f"live candidate is absent from the sealed lane: {candidate_task_id}")
        mode = str(workspace.get("mode", "exclusive"))
        if lane_modes.get(candidate_lane_id) != mode:
            raise ValueError(f"workspace mode differs from live lane contract: {candidate_lane_id}")
        if mode not in {"isolated_merge", "contract_split"}:
            continue
        integration_task_id = workspace.get("integration_task_id")
        if mode == "isolated_merge" and not integration_task_id:
            integration_task_id = _scheduled_integration_task(
                plan_file, spec, candidate_task_id,
            )
        if mode == "isolated_merge" and not integration_task_id:
            raise ValueError(f"isolated lane lacks integration_task_id: {candidate_lane_id}")
        if integration_task_id and str(integration_task_id) not in valid_integration_tasks:
            raise ValueError(
                "workspace integration task is not owned by an exclusive integrator "
                f"or validator lane in this run: {integration_task_id}"
            )
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
        name = _workspace_name(candidate_lane_id)
        pending.append({
            "lane_id": candidate_lane_id,
            "task_id": candidate_task_id,
            "mode": mode,
            "integration_task_id": str(integration_task_id) if integration_task_id else None,
            "base_commit": workspace_base,
            "worktree_path": str(service.paths.state_dir / "workflow-workspaces" / name),
            "branch": f"codex/{name}",
        })
    scoped_integration_tasks = {
        str(item["integration_task_id"])
        for item in pending
        if item.get("integration_task_id")
    }
    if requested_lane_id is not None and requested_lane_id in existing:
        existing_target = existing[requested_lane_id].get("integration_task_id")
        if existing_target:
            scoped_integration_tasks.add(str(existing_target))
    for destination in exclusive_destinations:
        if requested_lane_id is not None and destination["task_id"] not in scoped_integration_tasks:
            continue
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


def advance_producer_wave(
    repo: str | Path,
    plan_path: str | Path,
    run_id: str,
    lane_id: str,
    base_commit: str,
    integration_task_id: str,
    *,
    reason: str,
    apply: bool = False,
    confirmation: str | None = None,
) -> dict[str, object]:
    """Advance an integrated serial producer to its next integration wave."""
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.plan import load_plan
    from todo_orchestrator.models import TodoError
    from todo_orchestrator.workflow.service import repository_identity
    from todo_orchestrator.workflow.workspaces import WorkspaceService

    if not reason.strip():
        raise ValueError("producer wave advancement requires a reason")
    repository = Path(repo).expanduser().resolve()
    plan_file = Path(plan_path).expanduser().resolve()
    plan = load_plan(plan_file)
    runs = [item for item in plan.get("runs", []) if str(item.get("id")) == run_id]
    if len(runs) != 1:
        raise ValueError(f"native plan must contain exactly one run named {run_id}")
    lanes = [item for item in runs[0].get("lanes", []) if str(item.get("id")) == lane_id]
    if len(lanes) != 1:
        raise ValueError(f"native plan must contain exactly one lane named {lane_id}")
    lane_spec = lanes[0]
    canonical_base = _git(repository, "rev-parse", f"{base_commit}^{{commit}}")
    service = Service(repository, mutation_mode="self_debug")
    project_uuid = str(service.project["project_uuid"])
    with service.db.read() as conn:
        rows = conn.execute(
            "SELECT id,state,base_commit,integration_task_id "
            "FROM workflow_workspaces WHERE run_id=? AND lane_id=?",
            (run_id, lane_id),
        ).fetchall()
        candidates = conn.execute(
            "SELECT task_id FROM workflow_lane_tasks WHERE lane_id=? AND state='queued' "
            "ORDER BY position LIMIT 1",
            (lane_id,),
        ).fetchall()
    if len(rows) != 1:
        raise ValueError("expected exactly one workspace for the requested producer lane")
    if len(candidates) != 1:
        raise ValueError("expected exactly one next queued producer task")
    candidate_task_id = str(candidates[0]["task_id"])
    if candidate_task_id not in {str(task_id) for task_id in lane_spec.get("tasks", [])}:
        raise ValueError(f"live candidate is absent from the sealed lane: {candidate_task_id}")
    expected_integration_task = _scheduled_integration_task(
        plan_file, lane_spec, candidate_task_id,
    )
    if expected_integration_task != integration_task_id:
        raise ValueError(
            "requested integration task differs from the sealed current phase: "
            f"{integration_task_id} != {expected_integration_task}"
        )
    current = dict(rows[0])
    preview: dict[str, object] = {
        "status": "ready",
        "run_id": run_id,
        "lane_id": lane_id,
        "workspace_id": current["id"],
        "old_state": current["state"],
        "old_base_commit": current["base_commit"],
        "old_integration_task_id": current["integration_task_id"],
        "base_commit": canonical_base,
        "integration_task_id": integration_task_id,
        "task_id": candidate_task_id,
    }
    if not apply:
        return preview
    if confirmation != ADVANCE_PRODUCER_WAVE_CONFIRMATION:
        raise ValueError(f"--confirm must equal {ADVANCE_PRODUCER_WAVE_CONFIRMATION}")
    manager = WorkspaceService(
        service.db,
        managed_root=service.paths.state_dir / "workflow-workspaces",
        repository_identity_resolver=lambda root: repository_identity(root, project_uuid),
    )
    try:
        result = manager.advance_producer_wave(
            repository_root=repository,
            workspace_id=str(current["id"]),
            base_commit=canonical_base,
            integration_task_id=integration_task_id,
            reason=reason,
        )
    except TodoError as exc:
        raise ValueError(f"producer wave advancement rejected: {exc}") from exc
    result["status"] = "advanced"
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


def record_contract_split_integration(
    repo: str | Path, workspace_id: str, integration_task_id: str, accepted_commit: str,
    *, reason: str, apply: bool = False, confirmation: str | None = None,
) -> dict[str, object]:
    """Owner-only forwarding; Todo owns every validation and state transition."""
    _runtime_identity()
    if apply and confirmation != "RECORD-CONTRACT-SPLIT-INTEGRATION":
        raise ValueError("--confirm must equal RECORD-CONTRACT-SPLIT-INTEGRATION")
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.service import repository_identity
    from todo_orchestrator.workflow.workspaces import WorkspaceService

    repository = Path(repo).expanduser().resolve()
    service = Service(repository, mutation_mode="self_debug")
    project_uuid = str(service.project["project_uuid"])
    manager = WorkspaceService(
        service.db, managed_root=service.paths.state_dir / "workflow-workspaces",
        repository_identity_resolver=lambda root: repository_identity(root, project_uuid),
    )
    return manager.record_contract_split_integration(
        repository_root=repository, workspace_id=workspace_id,
        integration_task_id=integration_task_id, accepted_commit=accepted_commit,
        reason=reason, apply=apply,
    )


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
    prepare.add_argument("--lane")
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
    contract = commands.add_parser(
        "record-contract-split-integration", help="record terminal contract work already merged into main"
    )
    contract.add_argument("--repo", required=True)
    contract.add_argument("--workspace", required=True)
    contract.add_argument("--integration-task", required=True)
    contract.add_argument("--accepted-commit", required=True)
    contract.add_argument("--reason", required=True)
    contract.add_argument("--apply", action="store_true")
    contract.add_argument("--confirm")
    args = parser.parse_args(argv)
    if args.command == "record-contract-split-integration":
        result = record_contract_split_integration(
            args.repo, args.workspace, args.integration_task, args.accepted_commit,
            reason=args.reason, apply=args.apply, confirmation=args.confirm,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    elif args.command == "recover":
        if args.inspect_only:
            print(json.dumps(inspect_recovery(args.repo, args.task), sort_keys=True, separators=(",", ":")))
        else:
            recover(args.repo, reason=args.reason, task_id=args.task)
    elif args.command == "prepare-run-workspaces":
        result = prepare_run_workspaces(
            args.repo, args.plan, args.run, lane_id=args.lane,
            apply=args.apply, confirmation=args.confirm,
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
