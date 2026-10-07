"""Canonical in-process Project Control workflow over Todo semantic authority."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from ..child_execution import (
    authorize_child_execution_for_claim,
    child_execution_status_for_claim,
    disposition_child_execution_for_claim,
)
from ..claims import CANONICAL_WORKFLOW_OWNER, claim_best, release_claim_id, renew_claim_for_session, sweep_expired
from ..config import utc_now
from ..evidence import gate_input_fingerprint, required_gates
from ..gates import bind_required_gates, run_gate
from ..git_state import integration_diff_args
from ..interfaces import freeze as freeze_interface
from ..interfaces import revise as revise_interface
from ..models import ExitCode, TodoError
from ..git_state import canonical_relative
from ..ownership import scopes_for
from ..readiness import explain_task, ready_tasks
from ..service import Service
from ..sessions import create_session
from .capabilities import (
    AuthorizedCapability,
    WorkflowCapabilityLocator,
    WorkflowCapabilityStore,
    child_operations,
    default_first_class_operations,
)
from .context_fragments import (
    ContextFragmentStore, FragmentOwner, compose_child_packet,
    validate_context_note_invalidation_scope, validate_context_note_publication_scope,
)
from .foundation import CapabilityLineage, CHILD_RESULT_KINDS, content_hash, require_bounded_payload
from .lanes import (
    advance_lane_in_transaction,
    create_lane_in_transaction,
    deterministic_lane_assignment,
    dispatch_claim_in_transaction,
    enqueue_tasks_in_transaction,
    lane_candidates,
)
from .messages import MessageService
from .rendezvous import RendezvousService
from .roles import require_role_action
from .runs import RunService
from .workspaces import WorkspaceService


ServiceFactory = Callable[[str | Path], Service]


def repository_identity(repo_root: Path, project_uuid: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    common = result.stdout.strip() if result.returncode == 0 else str(repo_root)
    candidate = Path(common)
    if not candidate.is_absolute():
        candidate = (repo_root / candidate).resolve()
    return hashlib.sha256(f"{project_uuid}\0{candidate}".encode()).hexdigest()


def assess_continuation(
    conn: Any,
    *,
    run_id: str,
    lane_id: str,
    task_id: str,
    workspace_id: str | None,
) -> dict[str, object]:
    """Read one exact continuation without creating a session or claim.

    The caller supplies a connection from ``Service(..., read_only=True)``.
    It deliberately reports an observation, not a reservation: ``next_task``
    still performs the authoritative claim and dispatch transaction.
    """
    revision_row = conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()
    revision = int(revision_row[0]) if revision_row is not None else None
    blockers: list[dict[str, object]] = []
    run = conn.execute("SELECT status FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
    lane = conn.execute(
        "SELECT id,run_id,role,workspace_mode,state FROM workflow_lanes WHERE id=? AND run_id=?",
        (lane_id, run_id),
    ).fetchone()
    membership = conn.execute(
        "SELECT position,state FROM workflow_lane_tasks WHERE lane_id=? AND task_id=?",
        (lane_id, task_id),
    ).fetchone()
    if run is None or run["status"] != "active":
        blockers.append({"kind": "run", "state": "run_focus_unavailable"})
    if lane is None:
        blockers.append({"kind": "lane", "state": "lane_focus_unavailable"})
    elif membership is None or membership["state"] != "queued":
        blockers.append({"kind": "lane_task", "state": "task_not_queued"})
    else:
        candidates = lane_candidates(conn, run_id, role=str(lane["role"]))
        if not any(candidate.get("lane_id") == lane_id and candidate.get("task_id") == task_id for candidate in candidates):
            # An explicit next_task request selects this lane/task before the
            # dispatch transaction validates its own serial queue head.  Keep
            # the read-only assessment aligned without reserving that claim.
            blockers.append({"kind": "lane_task", "state": "not_serial_lane_head"})
    ready = {str(item["task_id"]): item for item in ready_tasks(conn)}.get(task_id)
    if ready is None:
        blockers.append({"kind": "task", "state": "not_ready", "assessment": explain_task(conn, task_id)})
    if lane is not None and lane["workspace_mode"] in {"isolated_merge", "contract_split"}:
        workspace = conn.execute(
            "SELECT id,mode,state FROM workflow_workspaces WHERE id=? AND run_id=? AND lane_id=?",
            (workspace_id, run_id, lane_id),
        ).fetchone() if workspace_id else None
        if workspace is None:
            blockers.append({"kind": "workspace", "state": "workflow_workspace_required"})
        elif workspace["mode"] != lane["workspace_mode"] or workspace["state"] not in {"active", "artifact_ready", "queued"}:
            blockers.append({"kind": "workspace", "state": "workflow_workspace_inactive"})
    observed = {
        "run_id": run_id, "lane_id": lane_id, "task_id": task_id,
        "workspace_id": workspace_id, "observed_revision": revision,
        "status": "ready" if not blockers else "blocked", "blockers": blockers,
    }
    if ready is not None:
        observed["readiness"] = ready["explanation"]
    return observed


class WorkflowKernel:
    """One semantic service shared by Todo and the Project Control Codex profile."""

    def __init__(
        self,
        *,
        service_factory: ServiceFactory = Service,
        locator: WorkflowCapabilityLocator | None = None,
        ctxpp_adapter: Any | None = None,
        local_worker_adapter: Any | None = None,
        runtime_guard: Callable[[Path], None] | None = None,
    ):
        self.service_factory = service_factory
        self.locator = locator or WorkflowCapabilityLocator()
        self.ctxpp_adapter = ctxpp_adapter
        self.local_worker_adapter = local_worker_adapter
        self.runtime_guard = runtime_guard

    def _service(self, repo_root: str | Path) -> Service:
        root = Path(repo_root).resolve()
        if self.runtime_guard is not None:
            self.runtime_guard(root)
        return self.service_factory(root)

    def _resolve_service(self, capability: AuthorizedCapability) -> Service:
        # The locator resolves and validates one exact authority before the
        # kernel is called. Never probe arbitrary hint files: they can describe
        # stale projects and opening them writable can initialize an authority.
        if capability.repository_root is None:
            raise TodoError("invalid_workflow_capability", "Capability authority provenance is absent")
        service = self._service(capability.repository_root)
        with service.db.read() as conn:
            row = conn.execute(
                "SELECT s.repo_root FROM workflow_capabilities c "
                "JOIN sessions s ON s.id=c.session_id WHERE c.id=?",
                (capability.id,),
            ).fetchone()
        if row is None:
            raise TodoError("invalid_workflow_capability", "Capability project is no longer locatable")
        exact = self._service(Path(str(row["repo_root"])).resolve())
        if repository_identity(
            exact.paths.repo_root, str(exact.project["project_uuid"])
        ) != capability.lineage.repository_identity:
            raise TodoError("repository_identity_mismatch", "Capability dispatch repository identity changed")
        return exact

    @staticmethod
    def _workspace_for_dispatch(service: Service, lineage: CapabilityLineage) -> dict[str, Any] | None:
        """Resolve and verify the managed workspace bound to this exact dispatch."""

        with service.db.read() as conn:
            row = conn.execute(
                "SELECT w.* FROM workflow_dispatches d "
                "LEFT JOIN workflow_workspaces w ON w.id=d.workspace_id "
                "WHERE d.claim_id=? AND w.run_id=? AND d.lane_id=? AND d.state='active'",
                (lineage.claim_id, lineage.run_id, lineage.lane_id),
            ).fetchone()
        if row is None or row["id"] is None:
            return None
        workspace = dict(row)
        allowed_states = {"active", "artifact_ready", "queued"}
        if lineage.role in {"integrator", "validator"}:
            allowed_states.update({
                "apply_failed", "conflict", "awaiting_gates", "gate_failed",
                "applied_pending_wave", "finalization_failed", "integrated",
            })
        if workspace["state"] not in allowed_states:
            raise TodoError("workflow_workspace_inactive", "The dispatch workspace is not active")
        if workspace["run_id"] != lineage.run_id or workspace["lane_id"] != lineage.lane_id:
            raise TodoError("workflow_workspace_scope_mismatch", "The dispatch workspace does not belong to this lane")
        expected_identity = repository_identity(service.paths.repo_root, str(service.project["project_uuid"]))
        if workspace["mode"] == "read_shared":
            return workspace
        raw_path = workspace.get("worktree_path")
        if not raw_path:
            raise TodoError("workspace_path_required", "Writable dispatch workspace has no managed path")
        root = Path(str(raw_path)).resolve()
        if not root.is_dir():
            raise TodoError("workflow_workspace_missing", "The managed dispatch workspace is unavailable")
        if repository_identity(root, str(service.project["project_uuid"])) != expected_identity:
            raise TodoError("repository_identity_mismatch", "The managed worktree is not attached to the authority repository")
        if not workspace.get("base_commit"):
            raise TodoError("workspace_gate_base_required", "Managed workspace has no frozen base commit")
        workspace["worktree_path"] = str(root)
        return workspace

    @staticmethod
    def _gate_workspace(workspace: Mapping[str, Any] | None) -> tuple[Path | None, str | None]:
        if not workspace or workspace.get("mode") == "read_shared":
            return None, None
        return Path(str(workspace["worktree_path"])), str(workspace["base_commit"])

    @staticmethod
    def _publish_and_queue_workspace_artifact(
        service: Service,
        lineage: CapabilityLineage,
        workspace: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Publish the producer HEAD and enqueue it using existing workspace authority."""

        if workspace.get("mode") != "isolated_merge":
            return {}
        with service.db.read() as conn:
            later = conn.execute(
                "SELECT 1 FROM workflow_lane_tasks current JOIN workflow_lane_tasks pending "
                "ON pending.lane_id=current.lane_id AND pending.position>current.position "
                "WHERE current.lane_id=? AND current.task_id=? "
                "AND pending.state NOT IN ('completed','cancelled','skipped') LIMIT 1",
                (lineage.lane_id, lineage.task_id),
            ).fetchone()
        if later:
            return {}
        root = Path(str(workspace["worktree_path"]))
        if workspace.get("state") == "queued":
            with service.db.read() as conn:
                existing = conn.execute(
                    "SELECT a.id AS artifact_id,a.artifact_ref,a.content_hash,a.base_commit,q.id AS queue_id,"
                    "q.position,q.run_id,q.integration_task_id,q.integrator_lane_id "
                    "FROM workflow_patch_artifacts a JOIN workflow_integration_queue q "
                    "ON q.patch_artifact_id=a.id WHERE a.workspace_id=? AND a.task_id=? "
                    "AND a.state='queued' AND q.state='queued' ORDER BY q.position LIMIT 1",
                    (workspace["id"], lineage.task_id),
                ).fetchone()
            if existing is None:
                raise TodoError(
                    "queued_workspace_artifact_missing",
                    "Queued producer workspace has no recoverable integration artifact",
                )
            head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if head.returncode != 0:
                raise TodoError(
                    "queued_workspace_head_mismatch",
                    "Queued artifact no longer matches the managed producer HEAD",
                )
            current_head = head.stdout.strip()
            if current_head != str(existing["artifact_ref"]):
                status = subprocess.run(
                    ["git", "-C", str(root), "status", "--porcelain=v1", "-z"],
                    capture_output=True,
                    check=False,
                )
                descendant = subprocess.run(
                    ["git", "-C", str(root), "merge-base", "--is-ancestor", str(existing["artifact_ref"]), current_head],
                    capture_output=True,
                    check=False,
                )
                diff = subprocess.run(
                    ["git", "-C", str(root), "diff", "--binary", str(existing["base_commit"]), current_head],
                    capture_output=True,
                    check=False,
                )
                if (status.returncode != 0 or status.stdout or descendant.returncode != 0 or
                        diff.returncode != 0 or not diff.stdout):
                    raise TodoError(
                        "queued_workspace_head_mismatch",
                        "Queued artifact no longer matches the managed producer HEAD",
                    )
                replacement_id = str(uuid.uuid4())
                replacement_hash = hashlib.sha256(diff.stdout).hexdigest()
                now = utc_now()

                def replace(conn: Any, revision: int) -> dict[str, Any]:
                    current = conn.execute(
                        "SELECT a.state AS artifact_state,q.state AS queue_state,w.state AS workspace_state "
                        "FROM workflow_patch_artifacts a JOIN workflow_integration_queue q ON q.patch_artifact_id=a.id "
                        "JOIN workflow_workspaces w ON w.id=a.workspace_id WHERE a.id=? AND q.id=?",
                        (existing["artifact_id"], existing["queue_id"]),
                    ).fetchone()
                    if (current is None or current["artifact_state"] != "queued" or
                            current["queue_state"] != "queued" or current["workspace_state"] != "queued"):
                        raise TodoError("queued_workspace_changed", "Queued producer state changed during refresh")
                    conn.execute(
                        "INSERT INTO workflow_patch_artifacts("
                        "id,workspace_id,task_id,kind,artifact_ref,content_hash,base_commit,created_at,state) "
                        "VALUES(?,?,?,?,?,?,?,?, 'queued')",
                        (replacement_id, workspace["id"], lineage.task_id, "commit", current_head,
                         replacement_hash, existing["base_commit"], now),
                    )
                    conn.execute(
                        "UPDATE workflow_patch_artifacts SET state='superseded' WHERE id=?",
                        (existing["artifact_id"],),
                    )
                    conn.execute(
                        "UPDATE workflow_integration_queue SET patch_artifact_id=?,updated_at=? WHERE id=?",
                        (replacement_id, now, existing["queue_id"]),
                    )
                    conn.execute(
                        "UPDATE workflow_workspaces SET artifact_ref=?,diff_hash=?,updated_at=? WHERE id=?",
                        (current_head, replacement_hash, now, workspace["id"]),
                    )
                    return {"artifact_id": replacement_id, "diff_hash": replacement_hash}

                refreshed, _, _ = service.mutate(
                    actor=lineage.session_id,
                    entity_type="workflow_patch_artifact",
                    entity_id=replacement_id,
                    event_type="workflow.patch_artifact.refreshed",
                    payload={"supersedes": str(existing["artifact_id"]), "queue_id": str(existing["queue_id"])},
                    operation=replace,
                )
                existing = {
                    **dict(existing),
                    "artifact_id": refreshed["artifact_id"],
                    "artifact_ref": current_head,
                    "content_hash": refreshed["diff_hash"],
                }
            return {
                "artifact": {
                    "artifact_id": str(existing["artifact_id"]),
                    "workspace_id": str(workspace["id"]),
                    "kind": "commit",
                    "artifact_ref": str(existing["artifact_ref"]),
                    "diff_hash": str(existing["content_hash"]),
                },
                "integration_request": {
                    "queue_id": str(existing["queue_id"]),
                    "position": int(existing["position"]),
                    "run_id": str(existing["run_id"]),
                    "artifact_id": str(existing["artifact_id"]),
                },
                "integration_task_id": str(existing["integration_task_id"]),
                "integrator_lane_id": str(existing["integrator_lane_id"]),
            }
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z"],
            capture_output=True,
            check=False,
        )
        if status.returncode != 0:
            raise TodoError("workspace_status_failed", "Cannot verify the producer workspace state")
        if status.stdout:
            raise TodoError(
                "workspace_uncommitted_changes",
                "Completion requires all producer workspace changes in an immutable commit",
                ExitCode.BLOCKED,
            )
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if head.returncode != 0 or not head.stdout.strip():
            raise TodoError("workspace_head_missing", "Producer workspace HEAD is unavailable")
        integration_task_id = str(workspace.get("integration_task_id") or "")
        if not integration_task_id:
            raise TodoError("integration_task_required", "Isolated producer workspace has no integration task")
        with service.db.read() as conn:
            integrator = conn.execute(
                "SELECT l.id FROM workflow_lanes l JOIN workflow_lane_tasks lt ON lt.lane_id=l.id "
                "WHERE l.run_id=? AND l.role IN ('integrator','validator') "
                "AND lt.task_id=? ORDER BY l.id LIMIT 1",
                (lineage.run_id, integration_task_id),
            ).fetchone()
        if not integrator:
            raise TodoError("integrator_lane_missing", "No integrator lane owns the declared integration task")
        workspaces = WorkspaceService(
            service.db,
            managed_root=service.paths.state_dir / "workflow-workspaces",
            repository_identity_resolver=lambda candidate: repository_identity(
                candidate, str(service.project["project_uuid"])
            ),
        )
        artifact = workspaces.publish_artifact(
            workspace_id=str(workspace["id"]),
            task_id=lineage.task_id,
            kind="commit",
            artifact_ref=head.stdout.strip(),
            actor_session_id=lineage.session_id,
        )
        queued = workspaces.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]),
            integrator_lane_id=str(integrator["id"]),
            integration_task_id=integration_task_id,
            actor_session_id=lineage.session_id,
        )
        return {
            "artifact": artifact,
            "integration_request": queued,
            "integration_task_id": integration_task_id,
            "integrator_lane_id": str(integrator["id"]),
        }

    @staticmethod
    def _context_version(conn: Any, run_id: str, lane_id: str, task_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(version),1) FROM workflow_context_fragments WHERE run_id=? "
            "AND (lane_id IS NULL OR lane_id=?) AND (task_id IS NULL OR task_id=?) AND invalidated_at IS NULL",
            (run_id, lane_id, task_id),
        ).fetchone()
        return max(1, int(row[0]))

    @staticmethod
    def _session(conn: Any, repo_root: Path) -> Any:
        external = os.environ.get("CODEX_THREAD_ID") or os.environ.get("OPENAI_CODEX_THREAD_ID")
        if external:
            row = conn.execute(
                "SELECT * FROM sessions WHERE external_id=? AND repo_root=? AND state='active' ORDER BY last_seen_at DESC LIMIT 1",
                (external, str(repo_root)),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM sessions WHERE hostname=? AND pid=? AND repo_root=? AND state='active' ORDER BY last_seen_at DESC LIMIT 1",
                (socket.gethostname(), os.getpid(), str(repo_root)),
            ).fetchone()
        return row

    def next_task(
        self, *, repo_root: str, task_id: str | None, run_id: str | None = None
    ) -> Mapping[str, Any]:
        service = self._service(repo_root)
        capabilities = WorkflowCapabilityStore(service.db)
        project_uuid = str(service.project["project_uuid"])
        repo_identity = repository_identity(service.paths.repo_root, project_uuid)

        def task_run_ids(conn: Any, requested_task_id: str, session_id: str | None = None) -> set[str]:
            queued = conn.execute(
                "SELECT DISTINCT l.run_id FROM workflow_lane_tasks lt "
                "JOIN workflow_lanes l ON l.id=lt.lane_id "
                "JOIN workflow_runs r ON r.id=l.run_id "
                "WHERE lt.task_id=? AND lt.state='queued' AND r.status='active'",
                (requested_task_id,),
            )
            active = ()
            if session_id is not None:
                active = conn.execute(
                    "SELECT DISTINCT l.run_id FROM workflow_dispatches d "
                    "JOIN workflow_lanes l ON l.id=d.lane_id "
                    "JOIN workflow_lane_tasks lt ON lt.lane_id=d.lane_id AND lt.state='active' "
                    "JOIN claims c ON c.id=d.claim_id AND c.state='active' "
                    "JOIN workflow_runs r ON r.id=l.run_id "
                    "WHERE d.session_id=? AND d.state='active' AND lt.task_id=? AND r.status='active'",
                    (session_id, requested_task_id),
                )
            return {str(row["run_id"]) for row in (*queued, *active)}

        def selection_required(requested_task_id: str, candidates: set[str]) -> TodoError:
            return TodoError(
                "run_selection_required", "Task is eligible in multiple active runs; select run_id",
                details={
                    "task_id": requested_task_id,
                    "choices": [{"run_id": value} for value in sorted(candidates)],
                },
            )

        # Resolve task-only run ambiguity before entering the mutation
        # transaction.  A choice must not manufacture a session, event, or
        # revision merely to tell the caller which explicit run to select.
        with service.db.read() as conn:
            if run_id is not None:
                active_run = conn.execute(
                    "SELECT 1 FROM workflow_runs WHERE id=? AND status='active'", (run_id,)
                ).fetchone()
                if active_run is None:
                    return {
                        "status": "idle", "run_id": run_id,
                        "warnings": ["run_focus_unavailable"],
                        "recommended_next_call": None,
                    }
            elif task_id is not None:
                session = self._session(conn, service.paths.repo_root)
                candidates = task_run_ids(conn, task_id, str(session["id"]) if session else None)
                if len(candidates) > 1:
                    return {
                        "status": "run_selection_required", "task_id": task_id,
                        "choices": [{"run_id": value} for value in sorted(candidates)],
                        "recommended_next_call": None,
                    }

        def operation(conn: Any, revision: int) -> dict[str, Any]:
            # Repeat read preflight under the mutation transaction.  This
            # closes the interval in which another active run could acquire
            # the same task and otherwise make ORDER BY pick arbitrarily.
            if run_id is not None and conn.execute(
                "SELECT 1 FROM workflow_runs WHERE id=? AND status='active'", (run_id,)
            ).fetchone() is None:
                raise TodoError("run_focus_unavailable", "Requested workflow run is not active")
            if run_id is None and task_id is not None:
                session = self._session(conn, service.paths.repo_root)
                candidates = task_run_ids(conn, task_id, str(session["id"]) if session else None)
                if len(candidates) > 1:
                    raise selection_required(task_id, candidates)
            sweep_expired(conn, service.paths.repo_root)
            session = self._session(conn, service.paths.repo_root)
            if session:
                resumed = conn.execute(
                    "SELECT d.*,l.run_id,l.role,lt.task_id FROM workflow_dispatches d "
                    "JOIN workflow_lanes l ON l.id=d.lane_id "
                    "JOIN workflow_lane_tasks lt ON lt.lane_id=d.lane_id AND lt.state='active' "
                    "JOIN claims c ON c.id=d.claim_id AND c.state='active' "
                    "WHERE d.session_id=? AND d.state='active' "
                    "AND (? IS NULL OR l.run_id=?) ORDER BY d.created_at DESC",
                    (session["id"], run_id, run_id),
                ).fetchall()
                if task_id is None and len(resumed) > 1:
                    raise TodoError("ambiguous_lane_resume", "Multiple active lanes share this session; pass the assigned task_id")
                resumed = next((row for row in resumed if task_id is None or row["task_id"] == task_id), None)
                if resumed and (task_id is None or resumed["task_id"] == task_id):
                    now = utc_now()
                    workspace_id = resumed["workspace_id"]
                    if workspace_id is None and resumed["role"] in {"integrator", "validator"}:
                        recoverable = conn.execute(
                            "SELECT id FROM workflow_workspaces WHERE run_id=? AND lane_id=? "
                            "AND state IN ('active','artifact_ready','queued','apply_failed','conflict','awaiting_gates',"
                            "'gate_failed','applied_pending_wave','finalization_failed','integrated') "
                            "ORDER BY id LIMIT 1",
                            (resumed["run_id"], resumed["lane_id"]),
                        ).fetchone()
                        if recoverable is not None:
                            workspace_id = str(recoverable["id"])
                            conn.execute(
                                "UPDATE workflow_dispatches SET workspace_id=? WHERE id=?",
                                (workspace_id, resumed["id"]),
                            )
                    conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (now, session["id"]))
                    renew_claim_for_session(conn, str(resumed["claim_id"]), str(session["id"]), service.claim_lease_seconds)
                    conn.execute("UPDATE workflow_dispatches SET heartbeat_at=?,revision=? WHERE id=?", (now, revision, resumed["id"]))
                    lineage = CapabilityLineage(
                        "first_class", project_uuid, repo_identity, str(session["id"]),
                        str(resumed["claim_id"]), str(resumed["run_id"]), str(resumed["lane_id"]),
                        str(resumed["role"]), str(resumed["task_id"]),
                        default_first_class_operations(str(resumed["role"])), 1,
                    )
                    handle, authorized = capabilities.stage_first_class(conn, lineage=lineage, revision=revision)
                    return {
                        "status": "resumed", "workflow_handle": handle,
                        "run_id": resumed["run_id"], "lane_id": resumed["lane_id"],
                        "role": resumed["role"], "task_id": resumed["task_id"],
                        "context_version": int(resumed["context_version"]),
                        "capability_id": authorized.id,
                    }
            else:
                session_view, _raw_session_token = create_session(
                    conn, service.paths.repo_root, {"command": "workflow.next_task", "owner_system": CANONICAL_WORKFLOW_OWNER}
                )
                session = conn.execute("SELECT * FROM sessions WHERE id=?", (session_view["agent_id"],)).fetchone()

            runs = conn.execute(
                "SELECT id FROM workflow_runs WHERE status='active' AND (? IS NULL OR id=?) ORDER BY created_at,id",
                (run_id, run_id),
            ).fetchall()
            if not runs:
                raise TodoError("workflow_run_missing", "No active workflow run is available")
            selected = None
            selected_run = None
            if task_id:
                row = conn.execute(
                    "SELECT l.run_id,l.id AS lane_id,l.role,lt.task_id FROM workflow_lane_tasks lt "
                    "JOIN workflow_lanes l ON l.id=lt.lane_id JOIN workflow_runs r ON r.id=l.run_id "
                    "WHERE lt.task_id=? AND lt.state='queued' AND r.status='active' "
                    "AND (? IS NULL OR l.run_id=?) ORDER BY l.run_id,l.id LIMIT 1",
                    (task_id, run_id, run_id),
                ).fetchone()
                if row:
                    selected = dict(row)
                    selected_run = str(row["run_id"])
            else:
                for run in runs:
                    candidate = deterministic_lane_assignment(conn, str(run["id"]))
                    if candidate:
                        selected, selected_run = candidate, str(run["id"])
                        break
            if not selected or not selected_run:
                raise TodoError("no_actionable_work", "No safe first-class lane is claimable", ExitCode.NO_ACTIONABLE_WORK)
            claim, _raw_claim_token = claim_best(
                conn, service.paths.repo_root, str(session["id"]), revision,
                service.claim_lease_seconds, requested_task_id=str(selected["task_id"]),
                reconcile_expired=False, owner_system=CANONICAL_WORKFLOW_OWNER,
                owner_instance_id="fi_" + uuid.uuid4().hex,
            )
            context_version = self._context_version(conn, selected_run, str(selected["lane_id"]), str(selected["task_id"]))
            workspace = conn.execute(
                "SELECT id FROM workflow_workspaces WHERE run_id=? AND lane_id=? "
                "AND (state IN ('active','artifact_ready','queued') OR "
                "(? IN ('integrator','validator') AND state IN "
                "('apply_failed','conflict','awaiting_gates','gate_failed','applied_pending_wave',"
                "'finalization_failed','integrated'))) "
                "ORDER BY id LIMIT 1",
                (selected_run, str(selected["lane_id"]), str(selected["role"])),
            ).fetchone()
            dispatch = dispatch_claim_in_transaction(
                conn, revision, run_id=selected_run, lane_id=str(selected["lane_id"]),
                session_id=str(session["id"]), claim_id=str(claim["claim_id"]),
                context_version=context_version, workspace_id=str(workspace["id"]) if workspace else None,
                hostname=socket.gethostname(), pid=os.getpid(),
            )
            lineage = CapabilityLineage(
                "first_class", project_uuid, repo_identity, str(session["id"]), str(claim["claim_id"]),
                selected_run, str(selected["lane_id"]), str(selected["role"]), str(selected["task_id"]),
                default_first_class_operations(str(selected["role"])), 1,
            )
            handle, authorized = capabilities.stage_first_class(conn, lineage=lineage, revision=revision)
            return {**dispatch, "workflow_handle": handle, "capability_id": authorized.id}

        try:
            result, revision, _ = service.mutate(
                actor=lambda value: value.get("session_id"), entity_type="workflow_dispatch",
                entity_id=lambda value: value.get("lane_id"), event_type="workflow.next_task",
                payload=lambda value: {key: value.get(key) for key in ("run_id", "lane_id", "task_id", "status")},
                operation=operation,
            )
        except TodoError as exc:
            if exc.code == "run_selection_required":
                details = dict(exc.details) if isinstance(exc.details, Mapping) else {}
                return {
                    "status": "run_selection_required", "task_id": details.get("task_id", task_id),
                    "choices": details.get("choices", []), "recommended_next_call": None,
                }
            if exc.code == "run_focus_unavailable":
                return {
                    "status": "idle", "run_id": run_id,
                    "warnings": [exc.code], "recommended_next_call": None,
                }
            if exc.code in {"no_actionable_work", "workflow_run_missing"}:
                return {"status": "idle", "warnings": [exc.code], "recommended_next_call": "next_task"}
            if exc.code == "workflow_workspace_required":
                details = dict(exc.details) if isinstance(exc.details, Mapping) else {}
                return {
                    "status": "root_preparation_required",
                    "warnings": [exc.code],
                    "root_action": {
                        "operation": "prepare_run_workspaces",
                        "authority": "root_only",
                        "run_id": details.get("run_id"),
                        "lane_id": details.get("lane_id"),
                        "task_id": details.get("task_id"),
                        "workspace_mode": details.get("workspace_mode"),
                    },
                    "recommended_next_call": "root_prepare_workspaces",
                }
            raise
        handle = str(result["workflow_handle"])
        self.locator.register(handle, service.paths.repo_root)
        store = ContextFragmentStore(service.db)

        def context_receipt(reason: str) -> dict[str, Any]:
            fragments = store.active_for(
                run_id=str(result["run_id"]), lane_id=str(result["lane_id"]), task_id=str(result["task_id"]),
            )
            # The charter, lane, and task briefs carry mandatory constraints.
            by_kind = {fragment.kind: fragment for fragment in fragments}
            essential = [
                by_kind[kind].reference() for kind in ("run_charter", "lane_brief", "task_brief")
                if kind in by_kind
            ]
            target = essential[0]["fragment_id"] if essential else None
            receipt: dict[str, Any] = {
                "run_id": result["run_id"], "lane_id": result["lane_id"], "task_id": result["task_id"],
                "reason": reason, "essential_fragments": essential,
            }
            if target:
                receipt["next_call"] = {
                    "kind": "context_fragment", "target": target, "budget_bytes": 4096,
                }
            return receipt
        try:
            # Reserve room for the claim, capability, and action policy in the
            # enclosing public response; the entry envelope is what is bounded.
            context = store.compose_first_class(
                run_id=str(result["run_id"]), lane_id=str(result["lane_id"]), task_id=str(result["task_id"]),
                budget_bytes=6 * 1024,
            )
        except TodoError as exc:
            if exc.code != "context_capsule_too_large":
                raise
            return {
                **result, "project_revision": revision, "status": "needs_context",
                "context_receipt": context_receipt(exc.code),
                "allowed_actions": ["inspect_task"], "recommended_next_call": "inspect_task",
            }
        return {
            **result,
            "project_revision": revision,
            "context": context,
            "recommended_next_call": None,
        }

    def inspect_task(self, capability: AuthorizedCapability, *, kind: str, target: str | None, budget_bytes: int) -> Mapping[str, Any]:
        service = self._resolve_service(capability)
        lineage = capability.lineage
        with service.db.read() as conn:
            if kind == "task":
                payload: dict[str, Any] = {
                    "task": explain_task(conn, target or lineage.task_id),
                    "scopes": scopes_for(conn, lineage.task_id),
                }
            elif kind == "evidence":
                # Evidence has no task_id. Retain task-owned gate precedence;
                # checkpoint ownership supplies records without a task-owned gate.
                payload = {"evidence": [dict(row) for row in conn.execute(
                    "SELECT e.id,COALESCE(g.task_id,c.task_id) AS task_id,e.gate_id,e.kind,e.path,e.content_hash,e.created_at,e.revision "
                    "FROM evidence e LEFT JOIN gates g ON g.id=e.gate_id "
                    "LEFT JOIN checkpoints c ON c.id=COALESCE(e.checkpoint_id,g.checkpoint_id) "
                    "WHERE COALESCE(g.task_id,c.task_id)=? ORDER BY e.revision DESC,e.id LIMIT 50",
                    (target or lineage.task_id,),
                )]}
            elif kind == "run":
                payload = RunService(service.db).inspect(target or str(lineage.run_id))
            elif kind == "lane":
                lane_id = target or str(lineage.lane_id)
                lane = conn.execute("SELECT * FROM workflow_lanes WHERE id=? AND run_id=?", (lane_id, lineage.run_id)).fetchone()
                payload = {"lane": dict(lane) if lane else None, "queue": [dict(row) for row in conn.execute(
                    "SELECT * FROM workflow_lane_tasks WHERE lane_id=? ORDER BY position", (lane_id,),
                )]}
            elif kind == "decision":
                payload = {"decisions": [dict(row) for row in conn.execute("SELECT * FROM decisions ORDER BY id LIMIT 100")]}
            elif kind == "messages":
                payload = MessageService(service.db).inspect(run_id=str(lineage.run_id), lane_id=str(lineage.lane_id), limit=50)
            elif kind == "rendezvous":
                if target:
                    payload = RendezvousService(service.db).inspect(target)
                else:
                    payload = {"rendezvous": [dict(row) for row in conn.execute(
                        "SELECT id,run_id,barrier_id,mode,quorum,join_task_id,state,revision FROM workflow_rendezvous WHERE run_id=? ORDER BY id",
                        (lineage.run_id,),
                    )]}
            elif kind == "workspace":
                payload = {"workspaces": [dict(row) for row in conn.execute(
                    "SELECT * FROM workflow_workspaces WHERE run_id=? AND (? IS NULL OR id=?) ORDER BY id",
                    (lineage.run_id, target, target),
                )]}
            elif kind == "integration":
                payload = {"integration_queue": [dict(row) for row in conn.execute(
                    "SELECT * FROM workflow_integration_queue WHERE run_id=? ORDER BY integration_task_id,position",
                    (lineage.run_id,),
                )]}
            elif kind == "context_fragment":
                if not target:
                    raise TodoError("context_fragment_target_required", "Context fragment inspection requires a fragment id")
                store = ContextFragmentStore(service.db)
                page_cursor = store._parse_page_cursor(target)
                fragment = store.get(page_cursor[0] if page_cursor else target)
                if fragment.kind == "context_note":
                    visible = {item.id for item in store._relevant_notes(
                        run_id=str(lineage.run_id), lane_id=str(lineage.lane_id), task_id=lineage.task_id,
                    )}
                    if not fragment.active or fragment.id not in visible:
                        raise TodoError("context_fragment_forbidden", "Context note is outside current virtual scope")
                elif (
                    fragment.owner.run_id != lineage.run_id
                    or (fragment.owner.lane_id is not None and fragment.owner.lane_id != lineage.lane_id)
                    or (fragment.owner.task_id is not None and fragment.owner.task_id != lineage.task_id)
                ):
                    raise TodoError("context_fragment_forbidden", "Context fragment is outside current workflow lineage")
                if page_cursor:
                    # Protocol identity/policy is added after this kernel call.
                    # Reserve its fixed bounded envelope before selecting text.
                    payload = store.expand_page(target, budget_bytes=max(512, budget_bytes - 1536))
                else:
                    # Plain fragment ids keep the established expanded-content
                    # response when it fits.  Paging starts only when needed.
                    try:
                        payload = store.expand(target, budget_bytes=budget_bytes)
                    except TodoError as exc:
                        if exc.code != "context_expansion_too_large":
                            raise
                        payload = store.expand_page(target, budget_bytes=max(512, budget_bytes - 1536))
            elif kind == "source":
                payload = {}
            else:
                raise TodoError("invalid_inspection_kind", "Inspection kind is unsupported")
        if kind == "source":
            if self.ctxpp_adapter is None:
                payload = {
                    "status": "fallback_authorized",
                    "fallback_authorization": {
                        "specialized_skill": "cpp-context-compiler", "permitted_operation": "bounded source inspection",
                        "reason": "ctxpp adapter is unavailable", "scope": {"task_id": lineage.task_id, "target": target},
                        "access": "read_only",
                    },
                }
            else:
                payload = dict(self.ctxpp_adapter.inspect(repo=service.paths.repo_root, target=target, budget_bytes=budget_bytes))
        require_bounded_payload(payload, limit=budget_bytes, code="workflow_inspection_too_large")
        return payload

    def _publish_project_context(
        self, capability: AuthorizedCapability, *, workflow_handle: str,
        kind: str, payload: Mapping[str, Any], task_id: str | None = None,
        source_verifier=None, expected_repository_root=None,
    ) -> Mapping[str, Any]:
        from .capabilities import capability_hash
        from ..project_amendments import (
            anchors, encode, _publish_authenticated_claim, _verify_publication_sources,
        )
        service = self._resolve_service(capability)
        if expected_repository_root is not None and Path(expected_repository_root).resolve() != service.paths.repo_root.resolve():
            raise TodoError("repository_identity_mismatch", "Publication repository differs from host binding")
        # Freeze request data before crossing callback/transaction boundaries.
        request = json.loads(encode({"kind": kind, "payload": dict(payload),
                                    "task_id": capability.lineage.task_id if task_id is None else task_id}))
        if kind not in {"skill_use", "finding", "candidate_relation"}:
            raise TodoError("publication_scope_denied", "Unsupported scoped publication kind")
        _verify_publication_sources(service, anchors(request['payload']), source_verifier)

        def operation(conn, revision):
            row = conn.execute("SELECT * FROM workflow_capabilities WHERE token_hash=?", (capability_hash(workflow_handle),)).fetchone()
            if row is None or row['id'] != capability.id or row['state'] != 'active' or row['expires_at'] <= utc_now():
                raise TodoError("invalid_workflow_capability", "Publication capability is inactive")
            store = WorkflowCapabilityStore(service.db)
            lineage = store._lineage(row)
            if lineage != capability.lineage or lineage.capability_class != 'first_class' or 'coordinate:publish_project_context' not in lineage.allowed_operations:
                raise TodoError("capability_operation_forbidden", "Publication capability lineage changed")
            store._validate_lineage_rows(conn, lineage)
            from .roles import require_lane_action
            require_lane_action(conn, str(lineage.lane_id), "publish_project_context",
                                allowed_run_ids=[str(lineage.run_id)])
            session = conn.execute("SELECT repo_root FROM sessions WHERE id=?", (lineage.session_id,)).fetchone()
            if Path(session['repo_root']).resolve() != service.paths.repo_root.resolve() or repository_identity(service.paths.repo_root, str(service.project['project_uuid'])) != lineage.repository_identity:
                raise TodoError("repository_identity_mismatch", "Publication dispatch repository changed")
            claim = conn.execute("SELECT * FROM claims WHERE id=?", (lineage.claim_id,)).fetchone()
            from ..claims import compatible_workflow_owner
            if not compatible_workflow_owner(claim["owner_system"]) or not claim["owner_instance_id"]:
                raise TodoError("publication_scope_denied", "Publication claim is not owned by the canonical workflow")
            return _publish_authenticated_claim(
                service, conn, revision, request, claim,
                source_verifier=source_verifier, strict=True,
            )

        return service._project_semantic_mutation(
            operation=operation, event_type="workflow.project_context_published",
        )

    def coordinate_task(self, capability: AuthorizedCapability, *, action: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        service = self._resolve_service(capability)
        lineage = capability.lineage
        role_action = {"publish_interface": "publish_interface", "request_integration": "request_integration"}.get(action, action)
        require_role_action(str(lineage.role), role_action)
        if action == "sync":
            synced = MessageService(service.db).sync(
                capability_class="first_class", run_id=str(lineage.run_id), lane_id=str(lineage.lane_id),
                actor_session_id=lineage.session_id,
            )
            known = payload.get("known_fragments")
            if known is not None:
                if not isinstance(known, Mapping):
                    raise TodoError("invalid_context_manifest", "known_fragments must map fragment ids to known versions")
                changes = ContextFragmentStore(service.db).delta_for(
                    run_id=str(lineage.run_id), lane_id=str(lineage.lane_id), task_id=str(lineage.task_id),
                    known_manifest=known,
                )
                synced["context_delta"] = {
                    "requested_cursor": payload.get("cursor"),
                    "cursor": synced.get("cursor"),
                    "changed_fragments": changes,
                }
            return synced
        if action == "bind_required_gates":
            result, revision = bind_required_gates(
                service.db, service.paths.repo_root, str(lineage.claim_id), list(payload["gates"]),
                actor_session_id=str(lineage.session_id),
            )
            service.refresh({lineage.task_id})
            return {**result, "project_revision": revision}
        if action == "publish_context":
            note_content: dict[str, Any] = {
                "content": dict(payload["content"]),
                "anchors": list(payload["anchors"]),
            }
            for key in ("classification", "source_identity"):
                if key in payload:
                    note_content[key] = payload[key]
            fragment, revision = ContextFragmentStore(service.db).publish(
                actor_session_id=lineage.session_id,
                owner=FragmentOwner(str(lineage.run_id), str(lineage.lane_id), str(lineage.task_id)),
                kind="context_note",
                series_key=str(payload["series_key"]),
                content=note_content,
                invalidate_fragment_ids=list(payload.get("invalidate_fragment_ids", [])),
                scope_validator=lambda conn: validate_context_note_publication_scope(
                    conn, role=str(lineage.role), run_id=str(lineage.run_id), lane_id=str(lineage.lane_id),
                    task_id=lineage.task_id, content=note_content,
                ),
                invalidation_validator=lambda conn, target: validate_context_note_invalidation_scope(
                    conn, role=str(lineage.role), run_id=str(lineage.run_id), lane_id=str(lineage.lane_id),
                    task_id=str(lineage.task_id), target=target,
                ),
            )
            return {"context_note": fragment.reference(), "content": fragment.content, "project_revision": revision}
        if action == "message":
            return MessageService(service.db).publish(
                capability_class="first_class", run_id=str(lineage.run_id), author_lane_id=str(lineage.lane_id),
                task_id=lineage.task_id, kind=str(payload["kind"]), payload=dict(payload["payload"]),
                recipients=list(payload["recipients"]), references=list(payload.get("references", [])),
                blocking=bool(payload.get("blocking", False)), actor_session_id=lineage.session_id,
            )
        if action == "answer":
            return MessageService(service.db).answer(
                capability_class="first_class", run_id=str(lineage.run_id), author_lane_id=str(lineage.lane_id),
                question_id=str(payload["question_id"]), payload=dict(payload["payload"]),
                references=list(payload.get("references", [])), actor_session_id=lineage.session_id,
            )
        if action == "arrive":
            return RendezvousService(service.db).arrive(
                capability_class="first_class", run_id=str(lineage.run_id), lane_id=str(lineage.lane_id),
                task_id=lineage.task_id, rendezvous_id=str(payload["rendezvous_id"]), summary=str(payload["summary"]),
                context_version=int(payload.get("context_version", 1)),
                base_source_identity=str(payload.get("base_source_identity") or ""),
                final_source_identity=str(payload.get("final_source_identity") or ""),
                artifact=dict(payload.get("artifact") or {}), interfaces=dict(payload.get("interfaces") or {}),
                evidence=list(payload.get("evidence") or []), warnings=list(payload.get("warnings") or []),
                actor_session_id=lineage.session_id,
            )
        if action == "fork":
            lane_id = "lane-" + uuid.uuid4().hex[:12]
            tasks = [str(item) for item in payload["tasks"]]

            def operation(conn: Any, revision: int) -> dict[str, Any]:
                created = create_lane_in_transaction(
                    conn, revision, run_id=str(lineage.run_id), lane_id=lane_id,
                    parent_lane_id=str(lineage.lane_id), role=str(payload.get("role", "specialist")),
                    workspace_mode=str(payload.get("workspace_mode", "exclusive")),
                )
                queued = enqueue_tasks_in_transaction(conn, revision, lane_id=lane_id, task_ids=tasks)
                return {**created, **queued}

            result, revision, _ = service.mutate(
                actor=lineage.session_id, entity_type="workflow_lane", entity_id=lane_id,
                event_type="workflow.lane.forked", payload={"parent_lane_id": lineage.lane_id, "tasks": tasks}, operation=operation,
            )
            return {**result, "project_revision": revision}
        if action == "publish_interface":
            workspace = self._workspace_for_dispatch(service, lineage)
            interface_root, _ = self._gate_workspace(workspace)
            if interface_root is None:
                interface_root = service.paths.repo_root

            def operation(conn: Any, revision: int) -> dict[str, Any]:
                owner = conn.execute("SELECT owner_task_id,state FROM interfaces WHERE id=?", (payload["interface_id"],)).fetchone()
                if not owner or owner["owner_task_id"] != lineage.task_id:
                    raise TodoError("interface_owner_mismatch", "Only the owning task may publish an interface")
                fn = revise_interface if owner["state"] == "frozen" else freeze_interface
                result = fn(conn, interface_root, str(payload["interface_id"]), str(payload["version"]), revision)
                if result["content_hash"] != payload["content_hash"]:
                    raise TodoError("interface_hash_mismatch", "Published interface hash differs from authoritative source")
                return result
            result, revision, _ = service.mutate(
                actor=lineage.session_id, entity_type="interface", entity_id=str(payload["interface_id"]),
                event_type="workflow.interface.published", payload={"version": payload["version"]}, operation=operation,
            )
            return {**result, "project_revision": revision}
        if action == "run_gates":
            workspace = self._workspace_for_dispatch(service, lineage)
            execution_root, workspace_base = self._gate_workspace(workspace)
            requested_effect = payload.get("effect")
            if requested_effect not in {None, "validate", "integrate_and_validate"}:
                raise TodoError(
                    "invalid_coordination_payload",
                    "run_gates effect must be validate or integrate_and_validate",
                )
            required = bool(payload.get("required", True))
            integrate = required and (
                requested_effect == "integrate_and_validate"
                or (requested_effect is None and lineage.role in {"integrator", "validator"})
            )
            actual_effect = "integrate_and_validate" if integrate else "validate"
            workspaces = WorkspaceService(
                service.db,
                managed_root=service.paths.state_dir / "workflow-workspaces",
                repository_identity_resolver=lambda root: repository_identity(
                    root, str(service.project["project_uuid"])
                ),
            )

            # A declared batch wave replaces the legacy serial integration
            # loop.  Its apply, gate binding, and finalization are privileged
            # root-owned lifecycle operations; letting this compatibility path
            # take even its first queue entry would split one sealed wave.
            if integrate:
                with service.db.read() as conn:
                    wave_rows = conn.execute(
                        "SELECT id,merge_result_json FROM workflow_integration_queue "
                        "WHERE run_id=? AND integrator_lane_id=? AND integration_task_id=? "
                        "AND state NOT IN ('integrated','rejected') ORDER BY position",
                        (lineage.run_id, lineage.lane_id, lineage.task_id),
                    ).fetchall()
                declared_waves = sorted({
                    str(metadata.get("wave_id"))
                    for row in wave_rows
                    for metadata in [json.loads(row["merge_result_json"] or "{}")]
                    if metadata.get("wave_id")
                })
                if declared_waves:
                    raise TodoError(
                        "integration_wave_root_lifecycle_required",
                        "A declared integration wave requires root-owned apply, gate, and finalization lifecycle",
                    )

            # Integration is an integrator-only specialization of the existing
            # run_gates action.  The model never receives a WorkspaceService
            # capability: the kernel selects only queue entries owned by this
            # exact run, lane, and task, applies them serially, and finalizes
            # each artifact before considering the next one.
            integration_results: list[dict[str, Any]] = []
            finalized_gate_results: dict[str, dict[str, Any]] = {}
            finalization_proofs: list[dict[str, Any]] = []
            if integrate:
                while True:
                    with service.db.read() as conn:
                        queued = conn.execute(
                            "SELECT id,state FROM workflow_integration_queue "
                            "WHERE run_id=? AND integrator_lane_id=? AND integration_task_id=? "
                            "AND state IN ('queued','apply_failed','awaiting_gates','conflict','gate_failed','finalization_failed') "
                            "ORDER BY position LIMIT 1",
                            (lineage.run_id, lineage.lane_id, lineage.task_id),
                        ).fetchone()
                    if queued is None:
                        break
                    queue_id = str(queued["id"])
                    queue_state = str(queued["state"])
                    applied: dict[str, Any] | None = None
                    if queue_state == "apply_failed":
                        workspaces.retry_apply_failed(
                            queue_id=queue_id,
                            actor_session_id=lineage.session_id,
                        )
                        queue_state = "queued"
                    if queue_state == "conflict":
                        workspaces.retry_conflict(
                            queue_id=queue_id,
                            actor_session_id=lineage.session_id,
                        )
                        queue_state = "queued"
                    if queue_state == "gate_failed":
                        workspaces.retry_failed_gates(
                            queue_id=queue_id,
                            actor_session_id=lineage.session_id,
                            allow_source_resolution=True,
                        )
                        queue_state = "awaiting_gates"
                    if queue_state == "finalization_failed":
                        workspaces.retry_finalization_failed(
                            queue_id=queue_id,
                            actor_session_id=lineage.session_id,
                            allow_source_resolution=True,
                        )
                        queue_state = "awaiting_gates"
                    if queue_state == "queued":
                        applied = dict(
                            workspaces.apply_next(
                                queue_id=queue_id,
                                actor_session_id=lineage.session_id,
                            )
                        )
                        if applied.get("state") != "awaiting_gates":
                            integration_results.append({"apply": applied})
                            break

                    with service.db.read() as conn:
                        gates = required_gates(conn, lineage.task_id)
                    gate_results = []
                    for gate in gates:
                        result, revision = run_gate(
                            service.db, service.paths, service.project, str(gate["id"]), None,
                            authorized_claim_id=lineage.claim_id,
                            execution_root=execution_root,
                            workspace_base_commit=workspace_base,
                        )
                        gate_results.append({**result, "project_revision": revision})
                    finalized = dict(
                        workspaces.record_post_merge_gates(
                            queue_id=queue_id,
                            gate_results=[
                                {"gate_id": str(item["gate_id"]), "evidence_id": str(item["evidence_id"])}
                                for item in gate_results
                            ],
                            actor_session_id=lineage.session_id,
                        )
                    )
                    integration_results.append({
                        "queue_id": queue_id,
                        "apply_state": (applied or {}).get("state"),
                        "gates": [
                            {key: item.get(key) for key in ("gate_id", "status", "evidence_id")}
                            for item in gate_results
                        ],
                        "state": finalized.get("state"),
                        "integrated_artifact": finalized.get("integrated_artifact"),
                    })
                    if finalized.get("state") != "integrated":
                        service.refresh({lineage.task_id})
                        return {
                            "status": "blocked",
                            "gates": [
                                {key: item.get(key) for key in ("gate_id", "status", "evidence_id")}
                                for item in gate_results
                            ],
                            "integration": integration_results,
                            "validation": {"effect": actual_effect, "validated_in_integration_gate_ids": []},
                        }
                    artifact = finalized.get("integrated_artifact") or {}
                    source_identity = artifact.get("content_hash")
                    if source_identity:
                        finalization_proofs.append({
                            "source_identity": str(source_identity),
                            "gate_fingerprints": {
                                str(item["gate_id"]): str(item["input_fingerprint"])
                                for item in finalized.get("gates", [])
                            },
                        })
                        finalized_gate_results.update({str(item["gate_id"]): item for item in gate_results})

            with service.db.read() as conn:
                gates = required_gates(conn, lineage.task_id) if required else [dict(row) for row in conn.execute("SELECT * FROM gates WHERE task_id=?", (lineage.task_id,))]
            results = []
            validated_in_integration_gate_ids: list[str] = []
            for gate in gates:
                gate_id = str(gate["id"])
                reusable = False
                if execution_root is not None and workspace_base is not None and gate_id in finalized_gate_results:
                    source = subprocess.run(
                        ["git", "-C", str(execution_root), *integration_diff_args(workspace_base)],
                        capture_output=True, check=False,
                    )
                    if source.returncode == 0:
                        current_source_identity = hashlib.sha256(source.stdout).hexdigest()
                        with service.db.read() as conn:
                            current_fingerprint, _ = gate_input_fingerprint(
                                conn, execution_root, json.loads(str(gate["config_json"])), gate_type=str(gate["type"]),
                            )
                        reusable = any(
                            proof["source_identity"] == current_source_identity
                            and proof["gate_fingerprints"].get(gate_id) == current_fingerprint
                            for proof in finalization_proofs
                        )
                if reusable:
                    results.append(finalized_gate_results[gate_id])
                    validated_in_integration_gate_ids.append(gate_id)
                    continue
                result, revision = run_gate(
                    service.db, service.paths, service.project, gate_id, None,
                    authorized_claim_id=lineage.claim_id,
                    execution_root=execution_root,
                    workspace_base_commit=workspace_base,
                )
                results.append({**result, "project_revision": revision})
            service.refresh({lineage.task_id})
            return {
                "status": "passed" if all(item.get("status") == "passed" for item in results) else "blocked",
                "gates": [
                    {key: item.get(key) for key in ("gate_id", "status", "evidence_id")}
                    for item in results
                ],
                "integration": integration_results,
                "validation": {
                    "effect": actual_effect,
                    "validated_in_integration_gate_ids": validated_in_integration_gate_ids,
                },
            }
        if action in {"accept_child", "reject_child"}:
            child_id = str(payload["child_execution_id"])
            target = "accepted" if action == "accept_child" else "rejected"
            def operation(conn: Any, revision: int) -> dict[str, Any]:
                result = disposition_child_execution_for_claim(
                    conn, str(lineage.claim_id), child_id, action="accept" if action == "accept_child" else "reject"
                )
                conn.execute(
                    "UPDATE workflow_child_result_candidates SET state=?,decided_at=?,decision_revision=? "
                    "WHERE child_execution_id=? AND parent_claim_id=? AND state='collected'",
                    (target, utc_now(), revision, child_id, lineage.claim_id),
                )
                return result
            result, revision, _ = service.mutate(
                actor=lineage.session_id, entity_type="child_execution", entity_id=child_id,
                event_type=f"workflow.child.{target}", payload={"child_execution_id": child_id}, operation=operation,
            )
            return {**result, "project_revision": revision, "parent_task_completed": False}
        if action == "request_integration":
            artifact_ids = [str(value) for value in payload["artifacts"]]
            if not artifact_ids or len(artifact_ids) != len(set(artifact_ids)):
                raise TodoError("invalid_integration_request", "Integration requires unique immutable artifact ids")
            with service.db.read() as conn:
                placeholders = ",".join("?" for _ in artifact_ids)
                artifacts = conn.execute(
                    f"SELECT a.id,a.task_id,w.run_id,w.integration_task_id FROM workflow_patch_artifacts a "
                    f"JOIN workflow_workspaces w ON w.id=a.workspace_id WHERE a.id IN ({placeholders}) ORDER BY a.id",
                    artifact_ids,
                ).fetchall()
                if len(artifacts) != len(artifact_ids) or any(str(item["run_id"]) != str(lineage.run_id) for item in artifacts):
                    raise TodoError("integration_artifact_scope_mismatch", "Artifacts must exist in the authorized run")
                if lineage.role != "coordinator" and any(str(item["task_id"]) != lineage.task_id for item in artifacts):
                    raise TodoError("integration_artifact_owner_mismatch", "A lane may request integration only for its task artifacts")
                configured = str(payload.get("integration_task_id") or artifacts[0]["integration_task_id"] or "")
                if not configured or any(str(item["integration_task_id"] or "") != configured for item in artifacts):
                    raise TodoError("integration_task_mismatch", "Artifacts must share one declared integration task")
                integrator = conn.execute(
                    "SELECT l.id FROM workflow_lanes l JOIN workflow_lane_tasks lt ON lt.lane_id=l.id "
                    "WHERE l.run_id=? AND l.role IN ('integrator','validator') "
                    "AND lt.task_id=? ORDER BY l.id LIMIT 1",
                    (lineage.run_id, configured),
                ).fetchone()
                if not integrator:
                    raise TodoError("integrator_lane_missing", "No integrator lane owns the declared integration task")
            workspaces = WorkspaceService(
                service.db,
                managed_root=service.paths.state_dir / "workflow-workspaces",
                repository_identity_resolver=lambda root: repository_identity(root, str(service.project["project_uuid"])),
            )
            queued = [
                workspaces.enqueue_artifact(
                    artifact_id=artifact_id, integrator_lane_id=str(integrator["id"]),
                    integration_task_id=configured, actor_session_id=lineage.session_id,
                )
                for artifact_id in artifact_ids
            ]
            return {
                "status": "claimed", "integration_task_id": configured,
                "integrator_lane_id": str(integrator["id"]), "queued": queued,
            }
        raise TodoError("invalid_coordination_action", "Coordination action is unsupported")

    def _child_scope(
        self,
        service: Service,
        claim_id: str,
        *,
        access: str,
        source_targets: list[str] | None,
    ) -> tuple[list[str], list[str]]:
        with service.db.read() as conn:
            claim = conn.execute("SELECT task_id FROM claims WHERE id=? AND state='active'", (claim_id,)).fetchone()
            if not claim:
                raise TodoError("invalid_claim_authority", "Parent claim is inactive")
            parents = scopes_for(conn, claim["task_id"], "exclusive" if access == "write" else None)
        if source_targets is None:
            if access == "read":
                return parents, parents
            raise TodoError("child_scope_not_strict", "Writable delegation requires exact narrower source targets")
        if not source_targets:
            raise TodoError("child_scope_not_strict", "Source targets must be non-empty when supplied")
        child_paths = sorted({canonical_relative(service.paths.repo_root, target) for target in source_targets})
        if any(not (service.paths.repo_root / target).is_file() for target in child_paths):
            raise TodoError("child_source_target_invalid", "Source targets must name existing repository files")
        if any(not any(target == parent or target.startswith(parent.rstrip("/") + "/") for parent in parents) for target in child_paths):
            raise TodoError("child_scope_violation", "Source targets must remain inside the parent authorized scope")
        return parents, child_paths

    @staticmethod
    def _child_packet_context(
        service: Service, lineage: CapabilityLineage, child_paths: list[str], *, access: str,
    ) -> tuple[list[str], list[Mapping[str, Any]]]:
        fragments = ContextFragmentStore(service.db).active_for(
            run_id=str(lineage.run_id), lane_id=str(lineage.lane_id), task_id=str(lineage.task_id),
        )
        constraints = ["remain subordinate to the parent claim"]
        for fragment in fragments:
            fields = (
                ("boundaries", "run boundaries"), ("invariants", "run invariants"),
                ("acceptance_conditions", "run acceptance conditions"),
            ) if fragment.kind == "run_charter" else (
                ("completion_contract", "task completion contract"),
                ("forbidden_mutations", "task forbidden mutations"),
                ("references", "task references"),
            ) if fragment.kind == "task_brief" else ()
            for field, label in fields:
                value = fragment.content.get(field)
                if value:
                    constraints.append(f"{label}: {json.dumps(value, sort_keys=True)}")
        references: list[Mapping[str, Any]] = []
        for fragment in fragments:
            if fragment.kind != "source_packet_ref":
                continue
            for reference in fragment.content.get("references", []):
                paths = reference.get("paths", []) if isinstance(reference, Mapping) else []
                if paths and all(any(str(path) == scope or str(path).startswith(scope.rstrip("/") + "/") for scope in child_paths) for path in paths):
                    references.append(reference)
        return constraints, references

    def delegate_task(
        self,
        capability: AuthorizedCapability,
        *,
        objective: str,
        mode: str,
        source_targets: list[str] | None = None,
    ) -> Mapping[str, Any]:
        service = self._resolve_service(capability)
        lineage = capability.lineage
        require_role_action(str(lineage.role), "delegate_child")
        if self.local_worker_adapter is None:
            return {
                "status": "local_unavailable",
                "fallback_authorization": {
                    "specialized_skill": "local-coding-worker", "permitted_operation": "continue directly in Codex",
                    "reason": "local worker is unavailable", "scope": {"task_id": lineage.task_id}, "access": "read_only",
                },
            }
        access = "read" if mode == "readonly" else "write"
        try:
            parent_paths, child_paths = self._child_scope(
                service, str(lineage.claim_id), access=access, source_targets=source_targets,
            )
        except TodoError as exc:
            return {
                "status": "not_eligible", "fallback_authorization": {
                    "specialized_skill": "local-coding-worker", "permitted_operation": "continue directly in Codex",
                    "reason": exc.code, "scope": {"task_id": lineage.task_id}, "access": "read_only",
                },
            }
        parent_constraints, source_packet_refs = self._child_packet_context(
            service, lineage, child_paths, access=access,
        )
        packet = compose_child_packet(
            delegated_objective=objective, parent_constraints=parent_constraints,
            parent_authorized_paths=parent_paths, child_authorized_paths=child_paths, source_packet_refs=source_packet_refs,
            required_output_schema={"kind": sorted(CHILD_RESULT_KINDS), "summary": "bounded"},
            candidate_gates=[], acceptance_gates=[],
            access=access,
        )
        capabilities = WorkflowCapabilityStore(service.db)
        parent = capability

        def operation(conn: Any, revision: int) -> dict[str, Any]:
            child = authorize_child_execution_for_claim(
                conn, service.paths.repo_root, str(lineage.claim_id), objective=objective,
                scopes=child_paths, gates=[], access=access,
            )
            child_lineage = CapabilityLineage(
                "child", lineage.project_uuid, lineage.repository_identity, lineage.session_id,
                lineage.claim_id, None, None, None, lineage.task_id, child_operations(), 1,
                parent_capability_id=parent.id, child_execution_id=str(child["child_execution_id"]),
            )
            handle, authorized = capabilities.stage_child(conn, lineage=child_lineage, parent=parent, revision=revision)
            return {
                "status": "delegated", "delegation_handle": handle,
                "child_execution_id": child["child_execution_id"], "capability_id": authorized.id,
                "packet_reference": content_hash(packet), "packet_size_bytes": len(json.dumps(packet).encode()),
            }
        result, revision, _ = service.mutate(
            actor=lineage.session_id, entity_type="child_execution", entity_id=lambda value: value["child_execution_id"],
            event_type="workflow.child.delegated", payload={"task_id": lineage.task_id}, operation=operation,
        )
        child_handle = str(result["delegation_handle"])
        self.locator.register(child_handle, service.paths.repo_root)
        adapter = self.local_worker_adapter.delegate(
            repo=service.paths.repo_root, parent_claim_ref=str(lineage.claim_id), objective_ref=content_hash(objective),
            packet_ref=str(result["packet_reference"]), mode=mode,
        )
        if adapter.get("status") in {"local_unavailable", "not_eligible"}:
            self.coordinate_task(capability, action="reject_child", payload={"child_execution_id": result["child_execution_id"], "reason": adapter.get("status")})
            WorkflowCapabilityStore(service.db).revoke(child_handle, actor_session_id=lineage.session_id)
            self.locator.forget(child_handle)
            return {
                "status": adapter["status"], "fallback_authorization": {
                    "specialized_skill": "local-coding-worker", "permitted_operation": "continue directly in Codex",
                    "reason": str(adapter["status"]), "scope": {"task_id": lineage.task_id, "paths": child_paths}, "access": "read_only",
                },
            }
        return {**result, "project_revision": revision, "worker_status": adapter.get("status", "running")}

    def collect_delegation(self, capability: AuthorizedCapability) -> Mapping[str, Any]:
        service = self._resolve_service(capability)
        child_id = str(capability.lineage.child_execution_id)
        adapter_result = self.local_worker_adapter.collect(repo=service.paths.repo_root, execution_id=child_id) if self.local_worker_adapter else {"status": "running"}
        with service.db.read() as conn:
            state = child_execution_status_for_claim(conn, str(capability.lineage.claim_id), child_id)
        if adapter_result.get("status") not in {"candidate_available", "succeeded", "ready_for_acceptance"}:
            return {"status": "running", "child": state}
        kind = str(adapter_result.get("kind", "source_finding"))
        if kind not in CHILD_RESULT_KINDS:
            raise TodoError("invalid_child_result_kind", "Local child returned an unsupported candidate kind")
        candidate_id = str(uuid.uuid5(uuid.UUID(child_id), content_hash(adapter_result)))

        def operation(conn: Any, revision: int) -> dict[str, Any]:
            candidate_payload = dict(adapter_result.get("result") or {})
            conn.execute(
                "UPDATE child_executions SET state='ready_for_acceptance',result_json=?,completed_at=? WHERE id=?",
                (json.dumps(candidate_payload, sort_keys=True), utc_now(), child_id),
            )
            conn.execute(
                "UPDATE child_attempts SET state='ready_for_acceptance',result_json=?,completed_at=? "
                "WHERE child_execution_id=? AND state='active'",
                (json.dumps(candidate_payload, sort_keys=True), utc_now(), child_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO workflow_child_result_candidates(id,child_execution_id,parent_claim_id,kind,payload_json,artifact_refs_json,state,created_at) "
                "VALUES(?,?,?,?,?,?,'collected',?)",
                (candidate_id, child_id, capability.lineage.claim_id, kind,
                 json.dumps(candidate_payload, sort_keys=True),
                 json.dumps(list(adapter_result.get("artifacts") or []), sort_keys=True), utc_now()),
            )
            return {"status": "candidate_available", "candidate_id": candidate_id, "kind": kind}
        result, revision, _ = service.mutate(
            actor=capability.lineage.session_id, entity_type="workflow_child_candidate", entity_id=candidate_id,
            event_type="workflow.child.collected", payload={"child_execution_id": child_id}, operation=operation,
        )
        return {**result, "project_revision": revision, "parent_task_completed": False}

    def finish_task(self, capability: AuthorizedCapability, *, action: str, disposition: str | None, note: str | None, reason: str | None) -> Mapping[str, Any]:
        service = self._resolve_service(capability)
        lineage = capability.lineage
        require_role_action(str(lineage.role), "finish_task")
        if action == "complete":
            # A terminal producer may own a checkpoint interface. Validate its
            # publication before creating the immutable integration queue item;
            # otherwise a legitimate interface commit can make that item stale.
            with service.db.read() as conn:
                unpublished = conn.execute(
                    "SELECT i.id FROM interfaces i JOIN checkpoint_interfaces ci ON ci.interface_id=i.id "
                    "JOIN checkpoints c ON c.id=ci.checkpoint_id "
                    "WHERE i.owner_task_id=? AND c.task_id=? AND i.state!='frozen' ORDER BY i.id LIMIT 1",
                    (lineage.task_id, lineage.task_id),
                ).fetchone()
            if unpublished is not None:
                raise TodoError(
                    "interface_artifact_missing",
                    f"Required owned interface is not frozen: {unpublished['id']}",
                )
            # Required completion gates are lifecycle authority, not an explicit
            # validator-role operation. They always run for the parent claim.
            workspace = self._workspace_for_dispatch(service, lineage)
            execution_root, workspace_base = self._gate_workspace(workspace)
            with service.db.read() as conn:
                completion_gates = required_gates(conn, lineage.task_id)
            for gate in completion_gates:
                run_gate(
                    service.db, service.paths, service.project, str(gate["id"]), None,
                    authorized_claim_id=lineage.claim_id,
                    execution_root=execution_root,
                    workspace_base_commit=workspace_base,
                )
            integration = self._publish_and_queue_workspace_artifact(service, lineage, workspace or {})
            capabilities = WorkflowCapabilityStore(service.db)
            rendezvous = RendezvousService(service.db)
            with service.db.read() as conn:
                dispatch = conn.execute(
                    "SELECT * FROM workflow_dispatches WHERE claim_id=? AND lane_id=? AND state='active'",
                    (lineage.claim_id, lineage.lane_id),
                ).fetchone()
            if not dispatch:
                raise TodoError("workflow_dispatch_inactive", "Completion requires the active first-class dispatch")

            def terminal(conn: Any, revision: int, claim: Any, task: Any, handoff: dict[str, Any]) -> dict[str, Any]:
                arrivals = []
                later = conn.execute(
                    "SELECT 1 FROM workflow_lane_tasks current JOIN workflow_lane_tasks pending "
                    "ON pending.lane_id=current.lane_id AND pending.position>current.position "
                    "WHERE current.lane_id=? AND current.task_id=? AND pending.state NOT IN ('completed','cancelled','skipped') LIMIT 1",
                    (lineage.lane_id, lineage.task_id),
                ).fetchone()
                if not later:
                    for row in conn.execute(
                        "SELECT r.id FROM workflow_rendezvous r JOIN workflow_rendezvous_participants p ON p.rendezvous_id=r.id "
                        "WHERE r.run_id=? AND p.lane_id=? AND r.state='open' ORDER BY r.id",
                        (lineage.run_id, lineage.lane_id),
                    ):
                        arrivals.append(rendezvous._arrive_in_transaction(
                            conn, revision, capability_class="first_class", run_id=str(lineage.run_id),
                            lane_id=str(lineage.lane_id), rendezvous_id=str(row["id"]), task_id=lineage.task_id,
                            summary=note or "task completed", base_source_identity=str(claim["baseline_head"] or "unknown"),
                            final_source_identity=str(integration.get("artifact", {}).get("artifact_ref") or handoff.get("git_head") or "unknown"),
                            artifact={"kind": "commit", "ref": str(integration.get("artifact", {}).get("artifact_ref") or handoff.get("git_head") or "unknown")},
                            interfaces={}, evidence=[{"type": "handoff", "id": handoff.get("task_id")}], warnings=[],
                            context_version=int(dispatch["context_version"]),
                        ))
                advanced = advance_lane_in_transaction(
                    conn, revision, lane_id=str(lineage.lane_id), task_id=lineage.task_id, dispatch_id=str(dispatch["id"])
                )
                capabilities.stage_revoke(conn, capability_id=capability.id, family=True)
                return {"lane": advanced, "rendezvous_arrivals": arrivals, "terminal": True}

            completed = service.complete_claim_id(
                str(lineage.claim_id), disposition or "implemented", note or "", terminal_hook=terminal
            )
            return {**completed, **integration, "status": "idle", "terminal": True}

        status = {"handoff": "in_progress", "block": "blocked", "release": "in_progress"}[action]
        def operation(conn: Any, revision: int) -> dict[str, Any]:
            claim = conn.execute("SELECT * FROM claims WHERE id=? AND state='active'", (lineage.claim_id,)).fetchone()
            if not claim:
                raise TodoError("invalid_claim_authority", "Workflow claim is inactive")
            handoff_id = None
            if action in {"handoff", "block"}:
                handoff_id = str(uuid.uuid4())
                body = service._handoff_payload(conn, claim, note or "")
                conn.execute(
                    "INSERT INTO handoffs(id,task_id,claim_id,kind,note,payload_json,created_at,revision) VALUES(?,?,?,?,?,?,?,?)",
                    (handoff_id, lineage.task_id, lineage.claim_id, action, note or "", json.dumps(body, sort_keys=True), utc_now(), revision),
                )
            dispatch = conn.execute(
                "SELECT id,lane_id FROM workflow_dispatches WHERE claim_id=? AND state='active'",
                (lineage.claim_id,),
            ).fetchone()
            if not dispatch:
                raise TodoError("workflow_dispatch_inactive", "Nonterminal disposition requires the active first-class dispatch")
            release_claim_id(conn, str(lineage.claim_id), next_status=status, reason=reason)
            now = utc_now()
            conn.execute(
                "UPDATE workflow_dispatches SET state='released',released_at=?,revision=? WHERE id=?",
                (now, revision, dispatch["id"]),
            )
            conn.execute(
                "UPDATE workflow_lane_tasks SET state='queued',activated_at=NULL,revision=? "
                "WHERE lane_id=? AND task_id=? AND state='active'",
                (revision, dispatch["lane_id"], lineage.task_id),
            )
            conn.execute(
                "UPDATE workflow_lanes SET state=?,updated_at=?,revision=? WHERE id=?",
                ("attention_required" if action == "block" else "ready", now, revision, dispatch["lane_id"]),
            )
            WorkflowCapabilityStore(service.db).stage_revoke(conn, capability_id=capability.id, family=True)
            return {"status": "finished", "task_id": lineage.task_id, "handoff_id": handoff_id, "terminal": True}
        result, revision, projection = service.mutate(
            actor=lineage.session_id, entity_type="task", entity_id=lineage.task_id,
            event_type=f"workflow.task.{action}", payload={"reason": reason}, operation=operation,
        )
        return {**result, "project_revision": revision, "projection": projection}
