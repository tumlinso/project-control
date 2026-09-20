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
PUBLISH_PRODUCER_WAVE_CONFIRMATION = "PUBLISH-PRODUCER-WAVE"
INTEGRATION_WAVE_CONFIRMATION = "INTEGRATION-WAVE"
PUBLISH_COMPLETED_INTERFACE_CONFIRMATION = "PUBLISH-COMPLETED-INTERFACE"
RETIRE_RUN_BATCH_CONFIRMATION = "RETIRE-RUN-BATCH"


def _runtime_identity() -> object:
    from .workflow_binding import runtime_identity

    return runtime_identity()


def inspect_recovery(repo: str | Path, task_id: str | None = None) -> dict[str, object]:
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.admin import inspect_owner_recovery
    from todo_orchestrator.workflow.recovery import RecoveryEngine

    service = Service(repo, mutation_mode="self_debug", read_only=True)
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


def issue_root_delegated_recovery(
    repo: str | Path,
    *,
    task_id: str,
    expires_seconds: int = 300,
) -> dict[str, object]:
    """Root/head lifecycle bridge for one opaque, scoped recovery launch.

    This does not relax ordinary owner recovery.  It snapshots one exact safe
    target, then the delegated invocation must still pass the kernel's fresh
    inspection, project lock and canonical transaction checks.
    """
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.recovery import RecoveryEngine
    from .workflow_core.recovery import issue_root_recovery_authorization

    service = Service(repo, mutation_mode="self_debug")
    engine = RecoveryEngine(service.db, service.paths.repo_root, str(service.project["project_uuid"]), actor_identity="root-authorized-delegate")
    return issue_root_recovery_authorization(service, engine, task_id=task_id, expires_seconds=expires_seconds)


def prepare_maintenance_assignment(
    repo: str | Path,
    *,
    task_id: str,
    recipient_principal: str,
    expires_seconds: int = 300,
) -> dict[str, object]:
    """Prepare, but never launch, one principal-bound recovery assignment.

    This host-only helper is deliberately absent from CLI and MCP registration.
    It gives an existing tool-capable host the complete next invocation without
    pretending that Project Control owns a launcher.
    """
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.recovery import RecoveryEngine
    from .workflow_core.recovery import issue_maintenance_recovery_authorization

    service = Service(repo, mutation_mode="self_debug", read_only=True)
    engine = RecoveryEngine(service.db, service.paths.repo_root, str(service.project["project_uuid"]), actor_identity="root-authorized-delegate")
    grant = issue_maintenance_recovery_authorization(
        service, engine, task_id=task_id, recipient_principal=recipient_principal,
        expires_seconds=expires_seconds,
    )
    return {
        "status": "launch_required",
        "executor": {"class": "tool_capable_maintenance_operator", "principal": recipient_principal},
        "assignment": {
            "objective": "Resume the named clean stopped execution through approved recovery.",
            "scope": {"project_uuid": str(service.project["project_uuid"]), "task_id": task_id},
            "constraints": ["preserve_all_no_repository_mutation", "fresh_kernel_inspection_required"],
            "grant_reference": grant["authorization_id"],
            "next_public_call": {
                "tool": "maintain_execution",
                "arguments": {"repo_root": str(Path(repo).resolve()), "authorization_id": grant["authorization_id"]},
            },
        },
    }


def retire_run_batch(repo: str | Path, request_file: str | Path, *, apply: bool, confirmation: str | None) -> dict[str, object]:
    """Root-only exact retirement front door; preview never mutates."""
    _runtime_identity()
    from todo_orchestrator.service import Service
    from .workflow_core.retirement import RetirementRequest, retire_run_batch as kernel_retire

    path = Path(request_file)
    try:
        raw = path.read_bytes()
        request = RetirementRequest.model_validate_json(raw)
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid retirement request file: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    preview = {"status": "preview", "request_sha256": digest, "source_run_id": request.source_run_id,
               "successor_run_id": request.successor_run_id, "task_ids": sorted(request.expected_tasks),
               "apply_confirmation": RETIRE_RUN_BATCH_CONFIRMATION}
    if not apply:
        return preview
    if confirmation != RETIRE_RUN_BATCH_CONFIRMATION:
        raise ValueError("retire-run-batch requires exact confirmation")
    service = Service(repo, mutation_mode="self_debug")
    receipt = kernel_retire(service, request)
    return {**receipt, "request_sha256": digest, "request_file": str(path.resolve())}


def prepare_retire_run_batch(repo: str | Path, intent_file: str | Path, output_file: str | Path | None = None) -> dict[str, object]:
    """Derive one stale-safe exact request from a small declarative intent.

    This is a read-only owner operation.  It keeps raw authority fingerprints
    and row digests in the kernel boundary instead of asking a root model to
    reinterpret a ledger or inspect SQLite itself.
    """
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.retirement import _fingerprint, _task_digest
    from .workflow_core.retirement import RetirementRequest

    try:
        intent = json.loads(Path(intent_file).read_text(encoding="utf-8"))
        required = {"source_run_id", "successor_run_id", "task_ids", "dispositions", "reason"}
        if not isinstance(intent, dict) or set(intent) != required or not isinstance(intent["task_ids"], list):
            raise ValueError("intent must contain exactly source_run_id, successor_run_id, task_ids, dispositions, reason")
        task_ids = sorted({str(item) for item in intent["task_ids"]})
        if not task_ids or set(intent["dispositions"]) != set(task_ids):
            raise ValueError("intent task_ids and dispositions must match exactly")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid retirement intent file: {exc}") from exc
    service = Service(repo, mutation_mode="self_debug")
    with service.db.read() as conn:
        rows = {str(row["id"]): row for row in conn.execute(
            "SELECT * FROM tasks WHERE id IN (" + ",".join("?" for _ in task_ids) + ")", task_ids
        )}
        if set(rows) != set(task_ids):
            raise ValueError("retirement intent references missing task")
        request = RetirementRequest(
            source_run_id=str(intent["source_run_id"]), successor_run_id=str(intent["successor_run_id"]),
            expected_project_uuid=str(service.project["project_uuid"]),
            expected_revision=int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0]),
            expected_fingerprint=_fingerprint(conn),
            expected_tasks={task_id: {"status": str(rows[task_id]["status"]), "version": int(rows[task_id]["version"]),
                                      "revision": int(rows[task_id]["revision"]), "row_digest": _task_digest(rows[task_id])}
                            for task_id in task_ids},
            dispositions={str(key): str(value) for key, value in intent["dispositions"].items()}, reason=str(intent["reason"]),
        )
    canonical = request.model_dump_json(indent=2) + "\n"
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    if output_file is not None:
        target = Path(output_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(canonical, encoding="utf-8")
    return {"status": "prepared", "request": request.model_dump(mode="json"), "request_sha256": digest,
            "output_file": str(Path(output_file).resolve()) if output_file is not None else None,
            "apply_confirmation": RETIRE_RUN_BATCH_CONFIRMATION}


def recover_authorized(repo: str | Path, *, authorization_id: str, reason: str) -> dict[str, object]:
    """Run the single-use authorization; no model-held root capability exists."""
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.recovery import RecoveryEngine
    from .workflow_core.recovery import run_authorized_recovery

    service = Service(repo, mutation_mode="self_debug")
    engine = RecoveryEngine(service.db, service.paths.repo_root, str(service.project["project_uuid"]), actor_identity="root-authorized-delegate")
    return run_authorized_recovery(service, engine, authorization_id=authorization_id, reason=reason)


def maintain_execution(
    repo: str | Path,
    *,
    authorization_id: str,
    recipient_principal: str,
) -> dict[str, object]:
    """Run one host-bound maintenance mandate and return the ordinary resume call."""
    _runtime_identity()
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.recovery import RecoveryEngine
    from .workflow_core.recovery import inspect_maintenance_recovery_authorization, run_authorized_recovery

    # A foreign, absent or stale target must fail before writable Service
    # construction, which otherwise initializes its authority database.
    readonly_service = Service(repo, mutation_mode="self_debug", read_only=True)
    readonly_engine = RecoveryEngine(readonly_service.db, readonly_service.paths.repo_root, str(readonly_service.project["project_uuid"]), actor_identity="root-authorized-delegate")
    preflight = inspect_maintenance_recovery_authorization(
        readonly_service, readonly_engine, authorization_id=authorization_id,
        recipient_principal=recipient_principal,
    )
    if "completed_receipt" in preflight:
        receipt = dict(preflight["completed_receipt"])
        return {
            "status": "maintained", "receipt": receipt,
            "recommended_next_call": {"tool": "next_task", "arguments": {"repo_root": str(Path(repo).resolve())}},
        }
    service = Service(repo, mutation_mode="self_debug")
    engine = RecoveryEngine(service.db, service.paths.repo_root, str(service.project["project_uuid"]), actor_identity="root-authorized-delegate")
    receipt = run_authorized_recovery(
        service, engine, authorization_id=authorization_id,
        reason="authorized delegated maintenance", recipient_principal=recipient_principal,
    )
    return {
        "status": "maintained",
        "receipt": receipt,
        "recommended_next_call": {
            "tool": "next_task",
            "arguments": {"repo_root": str(Path(repo).resolve())},
        },
    }


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


def _verified_generated_projection_paths(repository: Path, service: Any) -> list[str]:
    """Return only dirty files that exactly equal current Todo projections.

    Root workspace preparation must never absorb source edits. Todo's durable
    projections are the sole exception: verify their bytes against the live
    authoritative database before allowing them.
    """
    from todo_orchestrator.git_state import dirty_paths, is_generated_projection
    from todo_orchestrator.projections import build_snapshot, project_markdown, replace_managed

    observed = dirty_paths(repository)
    material = [path for path in observed if not is_generated_projection(path)]
    if material:
        raise ValueError(
            "repository has non-projection changes that must be preserved: "
            + ", ".join(material)
        )
    if not observed:
        return []
    with service.db.read() as conn:
        snapshot = build_snapshot(conn, service.project)
        revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
        root, status, tasks = project_markdown(conn, revision)
    expected: dict[str, bytes] = {
        ".todo-orchestrator/state.snapshot.json": (
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
        "todos.md": replace_managed("", root).encode("utf-8"),
        "todo-status.md": replace_managed("", status).encode("utf-8"),
    }
    expected.update({
        f"todos/{task_id.lower()}.md": replace_managed("", body).encode("utf-8")
        for task_id, body in tasks.items()
    })
    for relative in observed:
        target = repository / relative
        if relative not in expected or not target.is_file() or target.read_bytes() != expected[relative]:
            raise ValueError(f"generated projection differs from authoritative Todo state: {relative}")
    return observed


def _verified_native_plan(plan_file: Path) -> dict[str, object]:
    """Load only a manifest-sealed native plan from its package directory."""
    manifest_file = plan_file.parent.parent / "MANIFEST.sha256"
    if not plan_file.is_file() or not manifest_file.is_file():
        raise ValueError("sealed native plan and package manifest are required")
    if plan_file.is_symlink() or manifest_file.is_symlink():
        raise ValueError("sealed native plan inputs must not be symbolic links")
    package_root = manifest_file.parent.resolve()
    try:
        plan_name = plan_file.resolve().relative_to(package_root).as_posix()
    except ValueError as exc:
        raise ValueError("native plan escapes the package manifest root") from exc
    entries: dict[str, str] = {}
    for line in manifest_file.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name or name in entries:
            raise ValueError("workflow package manifest is malformed")
        entries[name] = digest
    if plan_name not in entries:
        raise ValueError("workflow package manifest does not seal native plan")
    if hashlib.sha256(plan_file.read_bytes()).hexdigest() != entries[plan_name]:
        raise ValueError(f"workflow package manifest mismatch: {plan_name}")
    from todo_orchestrator.plan import load_plan

    plan = load_plan(plan_file)
    if not isinstance(plan, dict):
        raise ValueError("native plan root must be an object")
    return plan


def _git_common_directory(repo: Path) -> Path:
    return Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()


def _owned_contract_paths(conn: Any, owner_task_id: str, source_root: Path, paths: list[str]) -> list[dict[str, str]]:
    scopes = [str(row["path"]) for row in conn.execute(
        "SELECT path FROM ownership_scopes WHERE task_id=? AND mode='exclusive'", (owner_task_id,)
    )]
    if not scopes:
        raise ValueError("interface owner has no exclusive write scope")
    records: list[dict[str, str]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("interface contract path is not repository-relative")
        relative = path.as_posix()
        if not any(relative == scope or relative.startswith(scope.rstrip("/") + "/") for scope in scopes):
            raise ValueError(f"interface contract path is outside owner write scope: {relative}")
        target = (source_root / path).resolve()
        if not target.is_relative_to(source_root) or not target.is_file():
            raise ValueError(f"interface contract path is missing from source worktree: {relative}")
        records.append({"path": relative, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    return records


def publish_completed_interface(
    repo: str | Path,
    plan_path: str | Path,
    interface_id: str,
    source_worktree: str | Path,
    *,
    apply: bool = False,
    confirmation: str | None = None,
) -> dict[str, object]:
    """Freeze one draft interface from a clean completed owner worktree.

    This root-only lifecycle bridge intentionally has no claim token: a normal
    producer is already terminal and cannot publish after completing itself.
    Preview performs every validation without a Todo transaction; apply repeats
    source checks inside the transaction before delegating the state change to
    Todo's canonical interface freezer.
    """
    _runtime_identity()
    from todo_orchestrator import interfaces
    from todo_orchestrator.service import Service

    repository = Path(repo).expanduser().resolve()
    plan_file = Path(plan_path).expanduser().resolve()
    source = Path(source_worktree).expanduser().resolve()
    plan = _verified_native_plan(plan_file)
    declared = [item for item in plan.get("interfaces", []) if isinstance(item, dict) and item.get("id") == interface_id]
    if len(declared) != 1:
        raise ValueError("sealed native plan must declare exactly one requested interface")
    spec = declared[0]
    owner_task_id = spec.get("owner_task_id")
    version = spec.get("version")
    paths = spec.get("contract_paths")
    if not isinstance(owner_task_id, str) or not owner_task_id or not isinstance(version, str) or not version:
        raise ValueError("sealed interface requires owner_task_id and version")
    if not isinstance(paths, list) or not paths or any(not isinstance(item, str) or not item for item in paths):
        raise ValueError("sealed interface requires nonempty contract_paths")
    if spec.get("state", "draft") != "draft":
        raise ValueError("sealed interface publication requires a draft declaration")
    if _git_common_directory(repository) != _git_common_directory(source):
        raise ValueError("source worktree does not belong to the repository git common directory")
    if Path(_git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise ValueError("source worktree must be its Git worktree root")

    service = Service(repository, mutation_mode="self_debug")

    def validate_source(conn: Any) -> tuple[str, list[dict[str, str]]]:
        if _git(source, "status", "--porcelain=v1", "-z"):
            raise ValueError("source worktree must be clean before interface publication")
        source_commit = _git(source, "rev-parse", "HEAD")
        interface = conn.execute("SELECT * FROM interfaces WHERE id=?", (interface_id,)).fetchone()
        if interface is None or str(interface["state"]) != "draft":
            raise ValueError("authoritative interface must exist and remain draft")
        if str(interface["owner_task_id"]) != owner_task_id or str(interface["version"]) != version:
            raise ValueError("authoritative interface owner or version differs from sealed plan")
        authoritative_paths = json.loads(str(interface["contract_paths_json"]))
        if authoritative_paths != paths:
            raise ValueError("authoritative interface contract paths differ from sealed plan")
        task = conn.execute("SELECT status FROM tasks WHERE id=?", (owner_task_id,)).fetchone()
        if task is None or str(task["status"]) != "done":
            raise ValueError("interface owner task must be completed/done")
        if conn.execute("SELECT 1 FROM claims WHERE task_id=? AND state='active'", (owner_task_id,)).fetchone():
            raise ValueError("interface owner task has an active claim")
        return source_commit, _owned_contract_paths(conn, owner_task_id, source, paths)

    with service.db.read() as conn:
        source_commit, records = validate_source(conn)
    result: dict[str, object] = {
        "status": "ready",
        "interface_id": interface_id,
        "owner_task_id": owner_task_id,
        "version": version,
        "source_worktree": str(source),
        "source_commit": source_commit,
        "contracts": records,
    }
    if not apply:
        return result
    if confirmation != PUBLISH_COMPLETED_INTERFACE_CONFIRMATION:
        raise ValueError(f"--confirm must equal {PUBLISH_COMPLETED_INTERFACE_CONFIRMATION}")

    def operation(conn: Any, revision: int) -> dict[str, object]:
        current_commit, current_records = validate_source(conn)
        if current_commit != source_commit or current_records != records:
            raise ValueError("source worktree changed after publication preview")
        frozen = interfaces.freeze(conn, source, interface_id, version, revision)
        return {**frozen, "owner_task_id": owner_task_id, "source_worktree": str(source), "source_commit": current_commit}

    published, revision, _projection = service.mutate(
        actor=None,
        entity_type="interface",
        entity_id=interface_id,
        event_type="interface.published_completed_owner",
        payload={"owner_task_id": owner_task_id, "source_worktree": str(source), "source_commit": source_commit, "contracts": records},
        operation=operation,
        full_projection=True,
        canonical_workflow=True,
    )
    return {"status": "published", **dict(published), "project_revision": int(revision)}


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

    service = Service(repository, mutation_mode="self_debug")
    if _git(repository, "status", "--porcelain=v1", "-z"):
        _verified_generated_projection_paths(repository, service)
    base_commit = _git(repository, "rev-parse", "HEAD")
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


def publish_producer_wave(
    repo: str | Path,
    plan_path: str | Path,
    run_id: str,
    lane_id: str,
    *,
    apply: bool = False,
    confirmation: str | None = None,
) -> dict[str, object]:
    """Publish one completed nonterminal producer task at a sealed wave boundary."""
    _runtime_identity()
    from todo_orchestrator.plan import load_plan
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.service import repository_identity
    from todo_orchestrator.workflow.workspaces import WorkspaceService

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
    if _git(repository, "status", "--porcelain=v1", "-z"):
        raise ValueError("repository must be clean before a producer wave is published")
    service = Service(repository, mutation_mode="self_debug")
    project_uuid = str(service.project["project_uuid"])
    with service.db.read() as conn:
        workspaces = conn.execute(
            "SELECT * FROM workflow_workspaces WHERE run_id=? AND lane_id=?",
            (run_id, lane_id),
        ).fetchall()
        completed = conn.execute(
            "SELECT lt.task_id FROM workflow_lane_tasks lt JOIN tasks t ON t.id=lt.task_id "
            "WHERE lt.lane_id=? AND lt.state='completed' AND t.status='done' "
            "AND NOT EXISTS (SELECT 1 FROM workflow_patch_artifacts a "
            "JOIN workflow_workspaces w ON w.id=a.workspace_id "
            "WHERE w.run_id=? AND w.lane_id=? AND a.task_id=lt.task_id) "
            "ORDER BY lt.position",
            (lane_id, run_id, lane_id),
        ).fetchall()
        remaining = conn.execute(
            "SELECT task_id FROM workflow_lane_tasks WHERE lane_id=? AND state='queued' "
            "ORDER BY position LIMIT 1",
            (lane_id,),
        ).fetchall()
        integrators = conn.execute(
            "SELECT l.id FROM workflow_lanes l JOIN workflow_lane_tasks lt ON lt.lane_id=l.id "
            "WHERE l.run_id=? AND l.role IN ('integrator','validator') AND lt.task_id=?",
            (run_id, str(workspaces[0]["integration_task_id"]) if len(workspaces) == 1 else ""),
        ).fetchall()
    if len(workspaces) != 1 or str(workspaces[0]["mode"]) != "isolated_merge":
        raise ValueError("expected exactly one isolated producer workspace")
    if len(completed) != 1 or len(remaining) != 1:
        raise ValueError("producer wave publication requires exactly one unpublished completed task and remaining serial work")
    workspace = dict(workspaces[0])
    task_id = str(completed[0]["task_id"])
    if task_id not in {str(item) for item in lane_spec.get("tasks", [])}:
        raise ValueError(f"completed task is absent from the sealed lane: {task_id}")
    expected_target = _scheduled_integration_task(plan_file, lane_spec, task_id)
    if expected_target != str(workspace.get("integration_task_id") or ""):
        raise ValueError("producer workspace target differs from the sealed completed-task phase")
    if len(integrators) != 1:
        raise ValueError("expected exactly one integrator lane for the producer wave")
    producer_root = Path(str(workspace["worktree_path"]))
    if _git(producer_root, "status", "--porcelain=v1", "-z"):
        raise ValueError("producer workspace must be clean before wave publication")
    head = _git(producer_root, "rev-parse", "HEAD")
    preview: dict[str, object] = {
        "status": "ready",
        "run_id": run_id,
        "lane_id": lane_id,
        "task_id": task_id,
        "workspace_id": str(workspace["id"]),
        "artifact_ref": head,
        "integration_task_id": str(workspace["integration_task_id"]),
        "integrator_lane_id": str(integrators[0]["id"]),
    }
    if not apply:
        return preview
    if confirmation != PUBLISH_PRODUCER_WAVE_CONFIRMATION:
        raise ValueError(f"--confirm must equal {PUBLISH_PRODUCER_WAVE_CONFIRMATION}")
    manager = WorkspaceService(
        service.db,
        managed_root=service.paths.state_dir / "workflow-workspaces",
        repository_identity_resolver=lambda root: repository_identity(root, project_uuid),
    )
    published = manager.publish_completed_wave(
        workspace_id=str(workspace["id"]), task_id=task_id, artifact_ref=head,
        integrator_lane_id=str(integrators[0]["id"]),
        integration_task_id=str(workspace["integration_task_id"]),
    )
    return {**preview, "status": "published", "publication": published}


def manage_integration_wave(
    repo: str | Path,
    plan_path: str | Path,
    run_id: str,
    integration_task_id: str,
    *,
    action: str,
    wave_id: str | None = None,
    adopt_gate_failed: bool = False,
    legacy_provenance_reason: str | None = None,
    reason: str | None = None,
    apply: bool = False,
    confirmation: str | None = None,
) -> dict[str, object]:
    """Root-only sealed-schedule control for one complete integration wave.

    An integration lane remains the durable task owner.  This administrative
    front door deliberately owns the privileged batch lifecycle: it derives
    the complete producer membership from the sealed schedule before asking
    the Todo kernel to declare, recover, apply, or finalise the wave.
    """
    _runtime_identity()
    from todo_orchestrator.plan import load_plan
    from todo_orchestrator.service import Service
    from todo_orchestrator.workflow.service import repository_identity
    from todo_orchestrator.workflow.workspaces import WorkspaceService

    if action not in {"declare", "apply", "gate-finalize", "recover-finalization"}:
        raise ValueError("integration wave action must be declare, apply, gate-finalize, or recover-finalization")
    if action in {"apply", "gate-finalize", "recover-finalization"} and not wave_id:
        raise ValueError(f"integration wave {action} requires a wave ID")
    if adopt_gate_failed and action != "declare":
        raise ValueError("gate-failed adoption is permitted only while declaring a wave")
    if legacy_provenance_reason is not None and not adopt_gate_failed:
        raise ValueError("legacy provenance reason is permitted only while adopting a gate failure")
    if action == "recover-finalization" and not (reason and reason.strip()):
        raise ValueError("interrupted integration finalization recovery requires a reason")

    repository = Path(repo).expanduser().resolve()
    plan_file = Path(plan_path).expanduser().resolve()
    plan = load_plan(plan_file)
    runs = [item for item in plan.get("runs", []) if str(item.get("id")) == run_id]
    if len(runs) != 1:
        raise ValueError(f"native plan must contain exactly one run named {run_id}")
    schedule = _verified_schedule(plan_file)
    if schedule is None:
        raise ValueError("batch integration requires a sealed integration schedule")
    phases = [
        item for item in schedule["phases"]
        if str(item["integration_task"]) == integration_task_id
    ]
    if len(phases) != 1:
        raise ValueError("sealed schedule must contain exactly one requested integration phase")
    required = [str(item) for item in phases[0]["required_tasks"]]
    if len(required) != len(set(required)):
        raise ValueError("sealed integration phase contains duplicate producer tasks")
    phase_index = schedule["phases"].index(phases[0])
    inherited_task_id: str | None = None
    if phase_index:
        previous_task_id = str(schedule["phases"][phase_index - 1]["integration_task"])
        if previous_task_id in required:
            inherited_task_id = previous_task_id
    producer_required = [task_id for task_id in required if task_id != inherited_task_id]

    service = Service(repository, mutation_mode="self_debug")
    project_uuid = str(service.project["project_uuid"])
    with service.db.read() as conn:
        queues = conn.execute(
            """SELECT q.id,q.position,q.state,q.integrator_lane_id,q.patch_artifact_id,
                      a.task_id,a.state AS artifact_state,a.artifact_ref,a.content_hash,a.base_commit AS artifact_base,
                      w.id AS producer_workspace_id,w.run_id AS producer_run_id,w.lane_id AS producer_lane_id,
                      w.state AS producer_state,w.integration_task_id AS producer_task,w.base_commit AS producer_base,
                      d.id AS destination_workspace_id,d.mode AS destination_mode,
                      d.integration_task_id AS destination_task,d.base_commit AS destination_base
                 FROM workflow_integration_queue q
                 JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id
                 JOIN workflow_workspaces w ON w.id=a.workspace_id
                 JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id
                WHERE q.run_id=? AND q.integration_task_id=?
                ORDER BY q.position,q.id""",
            (run_id, integration_task_id),
        ).fetchall()
        owned = conn.execute(
            """SELECT lt.task_id,lt.state FROM workflow_lane_tasks lt
                 JOIN workflow_lanes l ON l.id=lt.lane_id
                WHERE l.run_id=? AND l.id IN (
                    SELECT DISTINCT integrator_lane_id FROM workflow_integration_queue
                     WHERE run_id=? AND integration_task_id=?
                ) AND lt.state IN ('active','queued')
                ORDER BY lt.position LIMIT 1""",
            (run_id, run_id, integration_task_id),
        ).fetchone()
        dispatches = conn.execute(
            """SELECT d.id FROM workflow_dispatches d
                 JOIN workflow_integration_queue q ON q.integrator_lane_id=d.lane_id
                WHERE q.run_id=? AND q.integration_task_id=? AND d.state='active'
                GROUP BY d.id""",
            (run_id, integration_task_id),
        ).fetchall()
        inherited = None
        if inherited_task_id is not None:
            inherited = conn.execute(
                """SELECT w.id AS workspace_id,w.state AS workspace_state,w.artifact_kind,
                          w.artifact_ref,w.diff_hash,w.merge_result_json,t.status AS task_status
                     FROM workflow_workspaces w JOIN tasks t ON t.id=w.integration_task_id
                    WHERE w.run_id=? AND w.integration_task_id=? AND w.mode='exclusive'""",
                (run_id, inherited_task_id),
            ).fetchall()
    if not queues:
        raise ValueError("integration wave has no queued producer artifacts")
    lane_ids = {str(row["integrator_lane_id"]) for row in queues}
    destination_ids = {str(row["destination_workspace_id"]) for row in queues}
    bases = {str(row["artifact_base"]) for row in queues} | {str(row["producer_base"]) for row in queues}
    task_ids = [str(row["task_id"]) for row in queues]
    if len(lane_ids) != 1 or len(destination_ids) != 1:
        raise ValueError("integration wave requires one exclusive destination and integrator lane")
    if any(
        str(row["producer_run_id"]) != run_id
        or str(row["producer_task"] or "") != integration_task_id
        for row in queues
    ):
        raise ValueError("integration wave artifact provenance is no longer immutable")
    if task_ids != sorted(task_ids, key=lambda task_id: producer_required.index(task_id) if task_id in producer_required else len(producer_required)):
        # Queue ordering is sealed by producer membership, not timing of publication.
        raise ValueError("integration queue order differs from the sealed producer wave")
    if set(task_ids) != set(producer_required) or len(task_ids) != len(producer_required):
        raise ValueError("integration queue membership differs from the sealed producer wave")
    if len(bases) != 1:
        raise ValueError("integration wave producer artifacts do not share one immutable base")
    destination = queues[0]
    if (str(destination["destination_mode"]) != "exclusive"
            or str(destination["destination_task"] or "") != integration_task_id
            or str(destination["destination_base"]) not in bases):
        raise ValueError("integration wave destination is not the sealed exclusive workspace")
    inherited_base: dict[str, str] | None = None
    if inherited_task_id is not None:
        if inherited is None or len(inherited) != 1:
            raise ValueError("inherited integration base must have one exclusive destination receipt")
        receipt = inherited[0]
        try:
            completion = json.loads(str(receipt["merge_result_json"] or "{}"))
            integrated = dict(completion["integrated_artifact"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("inherited integration base has no frozen completion receipt") from error
        completion_commit = str(receipt["artifact_ref"] or "")
        content_hash = str(receipt["diff_hash"] or "")
        if (
            str(receipt["task_status"]) != "done"
            or str(receipt["workspace_state"]) != "integrated"
            or str(receipt["artifact_kind"] or "") != "commit"
            or not completion_commit
            or not content_hash
            or integrated.get("kind") != "commit"
            or integrated.get("ref") != completion_commit
            or integrated.get("content_hash") != content_hash
        ):
            raise ValueError("inherited integration base is not a frozen completed receipt")
        if bases != {completion_commit} or str(destination["destination_base"]) != completion_commit:
            raise ValueError("integration wave producers and destination must inherit the frozen completion base")
        inherited_base = {
            "task_id": inherited_task_id,
            "completion_commit": completion_commit,
            "content_hash": content_hash,
            "workspace_id": str(receipt["workspace_id"]),
        }
    states = [str(row["state"]) for row in queues]
    if action == "declare":
        allowed = {"queued"} if not adopt_gate_failed else {"queued", "gate_failed"}
    elif action == "apply":
        allowed = {"queued", "applied_pending_wave"}
    elif action == "gate-finalize":
        allowed = {"applied_pending_wave"}
    else:
        allowed = {"finalizing"}
    if any(state not in allowed for state in states):
        raise ValueError("integration wave members are not all pending or explicitly adoptable")
    if "gate_failed" in states and (states[0] != "gate_failed" or states.count("gate_failed") != 1):
        raise ValueError("only the first gate-failed integration member is adoptable")
    for row in queues:
        queue_state = str(row["state"])
        artifact_state = str(row["artifact_state"])
        expected_artifact_state = "gate_failed" if queue_state == "gate_failed" else "queued"
        legacy_adopted = (
            queue_state == "applied_pending_wave"
            and artifact_state == "gate_failed"
            and row is queues[0]
        )
        if artifact_state != expected_artifact_state and not legacy_adopted:
            raise ValueError("integration wave queue and immutable artifact states differ")
    if owned is None or str(owned["task_id"]) != integration_task_id:
        raise ValueError("integration task is not the current owned task for its lane")
    # A normal batch action is performed while the durable integration owner
    # remains live.  Interrupted finalization is different: its whole point
    # is recovery after that process may have died, so it retains every
    # sealed-membership/destination check above but cannot demand a dispatch.
    if action != "recover-finalization" and len(dispatches) != 1:
        raise ValueError("integration task must have exactly one active durable owner")

    membership = [
        {"queue_id": str(row["id"]), "position": int(row["position"]),
         "task_id": str(row["task_id"]), "artifact_id": str(row["patch_artifact_id"]),
         "artifact_ref": str(row["artifact_ref"]), "content_hash": str(row["content_hash"])}
        for row in queues
    ]
    preview: dict[str, object] = {
        "status": "ready", "action": action, "run_id": run_id,
        "integration_task_id": integration_task_id, "integration_lane_id": next(iter(lane_ids)),
        "destination_workspace_id": next(iter(destination_ids)), "base_commit": next(iter(bases)),
        "members": membership, "wave_id": wave_id, "adopt_gate_failed": adopt_gate_failed,
    }
    if inherited_base is not None:
        preview["inherited_base"] = inherited_base
    if not apply:
        return preview
    if confirmation != INTEGRATION_WAVE_CONFIRMATION:
        raise ValueError(f"--confirm must equal {INTEGRATION_WAVE_CONFIRMATION}")
    manager = WorkspaceService(
        service.db, managed_root=service.paths.state_dir / "workflow-workspaces",
        repository_identity_resolver=lambda root: repository_identity(root, project_uuid),
    )
    queue_ids = [str(row["id"]) for row in queues]
    if action == "declare":
        declaration_args: dict[str, object] = {
            "queue_ids": queue_ids,
            "legacy_provenance_reason": legacy_provenance_reason,
        }
        if inherited_base is not None:
            declaration_args["inherited_base"] = inherited_base
        declared = manager.declare_and_adopt_integration_wave(**declaration_args)
        return {**preview, "status": "declared", "declaration": declared}
    if action == "apply":
        applied = manager.apply_declared_integration_wave(
            integration_task_id=integration_task_id, wave_id=wave_id,
        )
        return {**preview, "status": "applied", "application": applied}
    if action == "recover-finalization":
        recovered = manager.recover_interrupted_integration_wave_finalization(
            integration_task_id=integration_task_id, wave_id=wave_id, reason=str(reason),
        )
        return {**preview, "status": "recovered", "recovery": recovered}
    # Batch gates are root-owned: use the active integration claim internally
    # against the cumulative destination source, then immediately bind that
    # fresh evidence into the same declared wave.
    from todo_orchestrator.gates import run_gate
    with service.db.read() as conn:
        required_gates = conn.execute(
            "SELECT id FROM gates WHERE task_id=? AND required=1 ORDER BY id",
            (integration_task_id,),
        ).fetchall()
        owner = conn.execute(
            """SELECT d.claim_id FROM workflow_dispatches d
                 JOIN workflow_integration_queue q ON q.integrator_lane_id=d.lane_id
                WHERE q.run_id=? AND q.integration_task_id=? AND d.state='active'
                GROUP BY d.claim_id""",
            (run_id, integration_task_id),
        ).fetchall()
    if not required_gates:
        raise ValueError("integration wave requires at least one required gate")
    if len(owner) != 1:
        raise ValueError("integration task must retain exactly one active claim for root gate execution")
    # Resolve from the authoritative workspace row rather than accepting a
    # caller path.  The ID was already bound to the sealed exclusive target.
    with service.db.read() as conn:
        destination_row = conn.execute(
            "SELECT worktree_path FROM workflow_workspaces WHERE id=?",
            (str(destination["destination_workspace_id"]),),
        ).fetchone()
    if destination_row is None:
        raise ValueError("integration wave destination disappeared before gates")
    destination_path = Path(str(destination_row["worktree_path"])).resolve()
    gate_results: list[dict[str, object]] = []
    for gate in required_gates:
        result, revision = run_gate(
            service.db, service.paths, service.project, str(gate["id"]), None,
            authorized_claim_id=str(owner[0]["claim_id"]), execution_root=destination_path,
            workspace_base_commit=next(iter(bases)),
        )
        gate_results.append({**result, "project_revision": revision})
    if any(str(item.get("status")) != "passed" for item in gate_results):
        return {**preview, "status": "gate_failed", "gates": [
            {key: item.get(key) for key in ("gate_id", "status", "evidence_id")}
            for item in gate_results
        ]}
    finalized = manager.record_integration_wave_gates(
        integration_task_id=integration_task_id, wave_id=wave_id,
        gate_results=[
            {"gate_id": str(item["gate_id"]), "evidence_id": str(item["evidence_id"])}
            for item in gate_results
        ],
    )
    return {**preview, "status": "finalized", "gates": [
        {key: item.get(key) for key in ("gate_id", "status", "evidence_id")}
        for item in gate_results
    ], "finalization": finalized}


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
    publish_interface = commands.add_parser(
        "publish-completed-interface", help="freeze one completed owner's sealed draft interface"
    )
    publish_interface.add_argument("--repo", required=True)
    publish_interface.add_argument("--plan", required=True)
    publish_interface.add_argument("--interface", required=True)
    publish_interface.add_argument("--source-worktree", required=True)
    publish_interface.add_argument("--apply", action="store_true")
    publish_interface.add_argument("--confirm")
    wave = commands.add_parser("integration-wave", help="root-owned sealed batch integration lifecycle")
    wave.add_argument("--repo", required=True)
    wave.add_argument("--plan", required=True)
    wave.add_argument("--run", required=True)
    wave.add_argument("--integration-task", required=True)
    wave.add_argument("--action", required=True, choices=("declare", "apply", "gate-finalize", "recover-finalization"))
    wave.add_argument("--wave")
    wave.add_argument("--adopt-gate-failed", action="store_true")
    wave.add_argument("--legacy-provenance-reason")
    wave.add_argument("--reason")
    wave.add_argument("--apply", action="store_true")
    wave.add_argument("--confirm")
    args = parser.parse_args(argv)
    if args.command == "publish-completed-interface":
        result = publish_completed_interface(
            args.repo, args.plan, args.interface, args.source_worktree,
            apply=args.apply, confirmation=args.confirm,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    elif args.command == "record-contract-split-integration":
        result = record_contract_split_integration(
            args.repo, args.workspace, args.integration_task, args.accepted_commit,
            reason=args.reason, apply=args.apply, confirmation=args.confirm,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    elif args.command == "integration-wave":
        result = manage_integration_wave(
            args.repo, args.plan, args.run, args.integration_task,
            action=args.action, wave_id=args.wave, adopt_gate_failed=args.adopt_gate_failed,
            legacy_provenance_reason=args.legacy_provenance_reason, reason=args.reason,
            apply=args.apply, confirmation=args.confirm,
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
