"""Transactional application service used by every v2 CLI command."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .audit import audit_state, reconcile_state
from .barriers import barrier_report
from .checkpoints import checkpoint_status, reach_checkpoint, revoke_checkpoint
from .completion import (
    SUCCESSFUL_DISPOSITIONS,
    recover_terminal_checkpoints,
    reach_eligible_owned_checkpoints,
    snapshot_completion_gates,
    terminal_finalization_report,
)
from .claims import (
    approve_force_release,
    approve_live_override,
    claim_best,
    force_release_live_claim,
    inspect_force_release,
    inspect_live_override,
    inspect_recovery,
    override_live_claim,
    pulse_claim,
    recover_adopt,
    recover_release,
    release_claim,
    release_claim_id,
    sweep_expired,
)
from .config import create_project_identity, project_paths, read_project, utc_now
from .context import build_context, expand_context
from .db import Database
from .events import changes_since
from .evidence import gate_is_satisfied, required_gates
from .gates import explain_gate, list_gates, run_gate
from .front_door import require_mutation_route
from .git_state import dirty_paths, git_head, path_contains
from .graph import reevaluate_barriers
from .interfaces import freeze as freeze_interface
from .interfaces import revise as revise_interface
from .interfaces import status as interface_status
from .legacy import apply_markdown_import, inspect_markdown
from .models import ExitCode, TodoError
from .ownership import acquire_named_locks, guard_paths, release_lock, scopes_for
from .plan import apply_plan, apply_selective_replan, load_plan, plan_diff, scaffold, validate_plan
from .projections import build_snapshot, refresh_projections, restore_snapshot, write_snapshot
from .readiness import explain_task, ready_tasks
from .reporting import git_diffstat, no_work_frontier, project_status
from .resources import acquire_resource, discover_nvidia, list_resources, release_resource, upsert_inventory
from .retirement import prior_retirement_receipt, retirement_request_hash, retire_run_batch_in_transaction
from .sessions import authenticate_claim, authenticate_session, create_session


class Service:
    def __init__(
        self,
        repo_root: str | Path = ".",
        *,
        bootstrap: bool = False,
        name: str | None = None,
        mutation_mode: str = "automated",
        read_only: bool | None = None,
        project_source_verifier=None,
    ):
        if project_source_verifier is not None and not callable(project_source_verifier):
            raise TypeError("project_source_verifier must be callable or None")
        self.project_source_verifier = project_source_verifier
        if bootstrap:
            self.paths, self.project = create_project_identity(repo_root, name)
        else:
            self.paths = project_paths(repo_root)
            self.project = read_project(self.paths.repo_root)
        configuration = self.project.get("configuration", {})
        self.mutation_mode = mutation_mode
        self.read_only = (
            os.environ.get("TODO_ORCHESTRATOR_READ_ONLY") == "1"
            if read_only is None
            else read_only
        )
        self.db = Database(
            self.paths.db_file,
            busy_timeout_ms=int(configuration.get("busy_timeout_ms", 5000)),
            read_only=self.read_only,
        )
        database_existed = self.paths.db_file.exists()
        if self.read_only and not database_existed:
            raise TodoError("todo_state_unavailable", "Todo state is unavailable", ExitCode.CONSISTENCY_ERROR)
        if self.read_only:
            self.db.require_current_schema(self.project, repo_root=self.paths.repo_root)
        else:
            self.db.initialize(self.project)
        if not database_existed and self.paths.snapshot_file.exists():
            restore_snapshot(self.db, self.paths, self.project)

    @classmethod
    def bootstrap(cls, repo_root: str | Path = ".", name: str | None = None) -> tuple["Service", dict[str, object]]:
        service = cls(repo_root, bootstrap=True, name=name)
        with service.db.read() as conn:
            has_event = conn.execute("SELECT 1 FROM events LIMIT 1").fetchone()
        if not has_event:
            result, revision = service.db.mutate(
                actor_session_id=None,
                entity_type="project",
                entity_id=str(service.project["project_uuid"]),
                event_type="project.bootstrapped",
                payload={"project_name": service.project["project_name"]},
                operation=lambda conn, revision: {"project_uuid": service.project["project_uuid"], "state_db": str(service.paths.db_file), "revision": revision},
            )
        else:
            result, revision = {"project_uuid": service.project["project_uuid"], "state_db": str(service.paths.db_file)}, service.db.revision()
        projection = service.refresh()
        return service, {**result, "project_revision": revision, "project_file": str(service.paths.project_file), "snapshot_file": str(service.paths.snapshot_file), "projection": projection}

    def refresh(self, task_ids: set[str] | None = None) -> dict[str, object]:
        return refresh_projections(self.db, self.paths, self.project, task_ids)

    def project_context(self) -> dict[str, object]:
        """Read canonical project declarations, skill use and orientation."""
        from .project_amendments import read_context
        with self.db.read() as conn:
            return read_context(conn, self.project, repo_root=self.paths.repo_root, project_source_verifier=self.project_source_verifier)

    def _project_semantic_mutation(self, *, operation, event_type, principal=None):
        # Internal host route, not the legacy self_debug escape hatch. No-op
        # results bypass projection churn while committing durable replay data.
        require_mutation_route(self.project, operation=event_type,
                               mutation_mode=self.mutation_mode, canonical_workflow=True)
        result, revision = self.db.mutate(
            actor_session_id=None, entity_type="project",
            entity_id=str(self.project["project_uuid"]), event_type=event_type,
            payload=lambda value: {"principal": principal, "operation_id": value.get("operation_id"),
                                   "changes": value.get("changes", []),
                                   "invalidations": value.get("invalidations", [])},
            operation=operation,
        )
        projection = None
        if result.get("status") in {"applied", "published"}:
            try:
                projection = self.refresh(set())
            except Exception as exc:
                # A committed semantic result remains true even when a derived
                # projection fails; callers can repair through canonical refresh.
                projection = {"status": "failed", "error": str(exc), "repair": "Service.refresh"}
        return {**result, "project_revision": revision, "projection": projection}

    def amend_project(self, request, *, principal=None, role="observer", context=None):
        """Host-only role/context arguments must be derived by trusted adapter.

        A caller-authored wire role is never authentication. Root/project access
        and provider trust are explicit host configuration, never declarations.
        """
        from .project_amendments import validate_request, amend_in_transaction
        if role != "mutator":
            raise TodoError("project_amendment_forbidden", "Project declarations require trusted mutator policy")
        parsed = validate_request(request, self.project, context)
        return self._project_semantic_mutation(
            operation=lambda conn, revision: amend_in_transaction(self, conn, revision, parsed),
            event_type="project.amended", principal=principal,
        )

    def publish_project_context(self, request, *, claim_token):
        """Scoped coder publication authenticates canonical task claim/scopes."""
        from .project_amendments import publish_in_transaction
        return self._project_semantic_mutation(
            operation=lambda conn, revision: publish_in_transaction(self, conn, revision, request, claim_token),
            event_type="workflow.project_context_published",
        )

    @property
    def claim_lease_seconds(self) -> int:
        return int(self.project.get("configuration", {}).get("claim_lease_seconds", 7200))

    @property
    def resource_lease_seconds(self) -> int:
        return int(self.project.get("configuration", {}).get("resource_lease_seconds", 300))

    @property
    def context_budget(self) -> int:
        return int(self.project.get("configuration", {}).get("context_budget_bytes", 12000))

    def mutate(
        self,
        *,
        actor,
        entity_type: str,
        entity_id,
        event_type: str,
        payload,
        operation,
        full_projection: bool = False,
        canonical_workflow: bool = False,
    ):
        require_mutation_route(
            self.project,
            operation=event_type,
            mutation_mode=self.mutation_mode,
            canonical_workflow=canonical_workflow or event_type.startswith("workflow."),
        )
        result, revision = self.db.mutate(
            actor_session_id=actor,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            payload=payload,
            operation=operation,
        )
        if isinstance(result, dict) and result.get("status") == "noop":
            return result, revision, None
        changed_tasks: set[str] = set()
        if isinstance(result, dict):
            task_id = result.get("task_id")
            if not task_id and isinstance(result.get("claim"), dict):
                task_id = result["claim"].get("task_id")
            if task_id:
                changed_tasks.add(str(task_id))
            for key in ("affected_active_tasks", "affected_active_consumers"):
                changed_tasks.update(str(value) for value in result.get(key, []))
            for change in result.get("barrier_changes", []):
                changed_tasks.update(str(value) for value in change.get("affected_active_tasks", []))
        projection = self.refresh(None if full_projection else changed_tasks)
        # Private, best-effort wake.  It is a no-op unless an external CUDA
        # controller has explicitly armed a sidecar watch for this project.
        # Import lazily so ordinary todo commands retain their startup and
        # output behavior and background failures cannot affect the commit.
        try:
            from .background.wake import wake_after_commit

            wake_after_commit(self.paths.repo_root, revision)
        except Exception:
            pass
        return result, revision, projection

    def continue_work(
        self,
        *,
        session_token: str | None = None,
        task_id: str | None = None,
        owner_system: str | None = None,
        owner_instance_id: str | None = None,
    ) -> dict[str, object]:
        if (owner_system is None) != (owner_instance_id is None):
            raise TodoError(
                "claim_owner_incomplete",
                "Claim owner system and instance must be supplied together",
                ExitCode.BLOCKED,
            )
        if owner_system is not None and (
            owner_system != "coding-workflow"
            or not isinstance(owner_instance_id, str)
            or not owner_instance_id.startswith("fi_")
        ):
            raise TodoError("claim_owner_invalid", "Claim owner metadata is invalid", ExitCode.BLOCKED)
        credentials: dict[str, str] = {}

        def operation(conn, revision):
            swept = sweep_expired(conn, self.paths.repo_root)
            if session_token:
                row = authenticate_session(conn, session_token)
                session = {"agent_id": row["id"], "label": row["label"], "hostname": row["hostname"]}
                credentials["session_token"] = session_token
            else:
                session, raw = create_session(conn, self.paths.repo_root, {
                    "command": "continue", "owner_system": owner_system,
                    "owner_instance_id": owner_instance_id,
                })
                credentials["session_token"] = raw
            try:
                claim, raw_claim = claim_best(
                    conn,
                    self.paths.repo_root,
                    str(session["agent_id"]),
                    revision,
                    self.claim_lease_seconds,
                    requested_task_id=task_id,
                    reconcile_expired=False,
                    owner_system=owner_system,
                    owner_instance_id=owner_instance_id,
                )
            except TodoError as exc:
                if exc.code != "no_actionable_work":
                    raise
                return {"no_actionable_work": True, "session": session, "swept_claims": swept}
            credentials["claim_token"] = raw_claim
            capsule = build_context(
                conn,
                project_revision=revision,
                session=session,
                session_token=credentials["session_token"],
                claim=claim,
                claim_token=raw_claim,
                budget=self.context_budget,
            )
            capsule["reconciled_claims"] = swept
            return capsule

        result, revision, projection = self.mutate(
            actor=lambda value: value.get("session", {}).get("agent_id"),
            entity_type="claim",
            entity_id=lambda value: value.get("claim", {}).get("task_id") or task_id,
            event_type="continue.completed",
            payload={"requested_task_id": task_id},
            operation=operation,
        )
        if result.get("no_actionable_work"):
            with self.db.read() as conn:
                frontier = no_work_frontier(conn)
            frontier["session"] = result["session"]
            frontier["session_token"] = credentials["session_token"]
            frontier["swept_claims"] = result["swept_claims"]
            frontier["project_revision"] = revision
            frontier["projection"] = projection
            raise TodoError("no_actionable_work", "No safe task is currently claimable", ExitCode.NO_ACTIONABLE_WORK, frontier)
        result["project_revision"] = revision
        result["projection"] = projection
        return result

    def ready(self) -> dict[str, object]:
        with self.db.read() as conn:
            revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
            return {"project_revision": revision, "tasks": ready_tasks(conn)}

    def retire_run_batch(self, request: dict[str, object]) -> dict[str, object]:
        """Root-owned, exact replacement of a quiescent workflow run.

        This intentionally has no claim/session argument: the Project Control
        root/admin facade is the authority boundary.  The kernel still rejects
        stale, foreign, live, or partially specified requests transactionally.
        """
        request_hash = retirement_request_hash(request)
        with self.db.read() as conn:
            prior = prior_retirement_receipt(conn, request_hash)
        if prior is not None:
            return {**prior, "project_revision": self.db.revision(), "projection": None}
        result, revision, projection = self.mutate(
            actor=None,
            entity_type="workflow_run",
            entity_id=lambda value: value.get("source_run_id"),
            event_type="workflow.run.retired",
            payload=lambda value: {"request_hash": value.get("request_hash"), "receipt": value},
            operation=lambda conn, rev: retire_run_batch_in_transaction(
                conn, rev, project=self.project, request=request
            ),
            full_projection=True,
            canonical_workflow=True,
        )
        # An idempotent retry makes no logical change.  Avoid presenting the
        # wrapper transaction's revision as a second retirement receipt.
        return {**result, "project_revision": revision, "projection": projection}

    def explain(self, task_id: str) -> dict[str, object]:
        with self.db.read() as conn:
            return explain_task(conn, task_id)

    def status(self) -> dict[str, object]:
        with self.db.read() as conn:
            return project_status(conn)

    def plan_validate(self, path: str) -> dict[str, object]:
        return validate_plan(load_plan(path), self.paths.repo_root)

    def plan_diff(self, path: str) -> dict[str, object]:
        data = load_plan(path)
        validate_plan(data, self.paths.repo_root)
        with self.db.read() as conn:
            return plan_diff(conn, data, self.paths.repo_root)

    def plan_apply(self, path: str) -> dict[str, object]:
        data = load_plan(path)
        result, revision, projection = self.mutate(
            actor=None,
            entity_type="project",
            entity_id=str(self.project["project_uuid"]),
            event_type="plan.applied",
            payload={"file": str(Path(path).resolve())},
            operation=lambda conn, revision: apply_plan(conn, data, self.paths.repo_root, revision),
            full_projection=True,
        )
        return {**result, "project_revision": revision, "projection": projection}

    def selective_replan(self, data: dict[str, object], expected_revision: int) -> dict[str, object]:
        def operation(conn, revision):
            if revision - 1 != expected_revision:
                raise TodoError("proposal_stale", "Todo revision changed before selective replan")
            return apply_selective_replan(conn, data, self.paths.repo_root, revision)
        result, revision, projection = self.mutate(actor=None, entity_type="project", entity_id=str(self.project["project_uuid"]), event_type="plan.selective_replanned", payload={"format": data.get("format")}, operation=operation, full_projection=True)
        return {**result, "project_revision": revision, "projection": projection}

    def pulse(self, claim_token: str) -> dict[str, object]:
        result, revision, _ = self.mutate(
            actor=lambda value: value.get("session_id"),
            entity_type="claim",
            entity_id=lambda value: value.get("task_id"),
            event_type="claim.pulsed",
            payload={},
            operation=lambda conn, revision: pulse_claim(conn, claim_token, self.claim_lease_seconds),
        )
        return {**result, "project_revision": revision}

    def release(self, claim_token: str, status: str = "in_progress", reason: str | None = None) -> dict[str, object]:
        result, revision, projection = self.mutate(
            actor=lambda value: value.get("session_id"),
            entity_type="claim",
            entity_id=lambda value: value.get("task_id"),
            event_type="claim.released",
            payload={"status": status, "reason": reason},
            operation=lambda conn, revision: release_claim(conn, claim_token, next_status=status, reason=reason),
        )
        return {**result, "project_revision": revision, "projection": projection}

    def _claim_source_root(self, conn, claim) -> Path:
        row = conn.execute(
            "SELECT w.mode,w.worktree_path FROM workflow_dispatches d "
            "JOIN workflow_workspaces w ON w.id=d.workspace_id WHERE d.claim_id=? "
            "ORDER BY d.created_at DESC LIMIT 1", (claim["id"],),
        ).fetchone()
        if row is None or row["mode"] == "read_shared":
            return self.paths.repo_root
        root = Path(str(row["worktree_path"])).resolve()
        from .workflow.service import repository_identity
        project_uuid = str(self.project["project_uuid"])
        if not root.is_dir() or repository_identity(root, project_uuid) != repository_identity(self.paths.repo_root, project_uuid):
            raise TodoError("completion_workspace_missing", "Completion source must be the verified dispatch workspace")
        return root

    def _complete_claim_in_transaction(self, conn, revision, claim, disposition: str, note: str, terminal_hook=None):
        if claim is None:
            raise TodoError("invalid_claim_authority", "Workflow capability claim is inactive", ExitCode.INVALID_TOKEN)
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (claim["task_id"],)).fetchone()
        policy = json.loads(task["result_policy_json"] or "{}")
        allowed = policy.get("allowed_dispositions", ["implemented", "validated", "evaluated_not_promoted", "no_change_required", "superseded", "failed"])
        if disposition not in allowed:
            raise TodoError("invalid_disposition", f"Disposition {disposition} is not allowed for {task['id']}")
        unsatisfied = [gate for gate in required_gates(conn, task["id"]) if not gate_is_satisfied(gate)]
        if unsatisfied:
            raise TodoError("required_gates_unsatisfied", "Required gates are missing, failed, or invalidated", ExitCode.GATE_FAILURE, unsatisfied)
        now = utc_now()
        source_root = self._claim_source_root(conn, claim)
        missing = [str(row["path"]) for row in conn.execute(
            "SELECT path FROM task_artifacts WHERE task_id=?", (task["id"],)
        ) if not (source_root / str(row["path"])).exists()]
        if disposition in SUCCESSFUL_DISPOSITIONS and missing:
            raise TodoError("completion_artifacts_missing", "Declared task artifacts are missing from the producer workspace", details={"paths": missing})
        if disposition in SUCCESSFUL_DISPOSITIONS and policy.get("require_tracked_artifacts"):
            untracked = [str(row["path"]) for row in conn.execute(
                "SELECT path FROM task_artifacts WHERE task_id=?", (task["id"],)
            ) if subprocess.run(["git", "ls-files", "--error-unmatch", "--", str(row["path"])],
                cwd=source_root, capture_output=True).returncode != 0]
            if untracked:
                raise TodoError("completion_artifacts_untracked", "Declared delivery artifacts must be tracked by Git", details={"paths": untracked})
        completion_head = git_head(source_root)
        conn.execute(
            "UPDATE tasks SET status='done',result=?,attention_reason=NULL,updated_at=?,revision=?,"
            "completion_revision=?,completion_git_head=?,completion_commit=? WHERE id=?",
            (disposition, now, revision, revision, completion_head, completion_head, task["id"]),
        )
        completion_gates = snapshot_completion_gates(conn, task, revision, completion_head)
        checkpoint_finalization = (
            reach_eligible_owned_checkpoints(conn, source_root, str(task["id"]), revision)
            if disposition in SUCCESSFUL_DISPOSITIONS else {"reached": [], "skipped": []}
        )
        handoff_id = str(uuid.uuid4())
        payload = self._handoff_payload(conn, claim, note)
        payload.update({
            "completion_revision": revision,
            "completion_commit": completion_head,
            "completion_gates": [
                {
                    "id": gate["gate_id"], "status": gate["status"], "valid": bool(gate["valid"]),
                    "input_fingerprint": gate["input_fingerprint"], "evidence_id": gate["evidence_id"],
                    "evidence_revision": gate["evidence_revision"], "validation_git_head": gate["validation_git_head"],
                }
                for gate in completion_gates
            ],
            "finalized_checkpoints": [item["checkpoint_id"] for item in checkpoint_finalization["reached"]],
        })
        conn.execute(
            "INSERT INTO handoffs(id,task_id,claim_id,kind,note,payload_json,created_at,revision) VALUES(?,?,?,?,?,?,?,?)",
            (handoff_id, task["id"], claim["id"], "complete", note, json.dumps(payload, sort_keys=True), now, revision),
        )
        terminal = terminal_hook(conn, revision, claim, task, payload) if terminal_hook else {}
        release_claim_id(conn, str(claim["id"]), next_status="done")
        barriers = reevaluate_barriers(conn, revision)
        checkpoint_barriers = [change for item in checkpoint_finalization["reached"] for change in item.get("barrier_changes", [])]
        return {
            "task_id": task["id"], "session_id": claim["session_id"], "status": "done",
            "disposition": disposition, "handoff_id": handoff_id, "handoff": payload,
            "reached_checkpoints": [item["checkpoint_id"] for item in checkpoint_finalization["reached"]],
            "skipped_checkpoints": checkpoint_finalization["skipped"],
            "barrier_changes": checkpoint_barriers + barriers, **dict(terminal or {}),
        }

    def complete(self, claim_token: str, disposition: str, note: str = "") -> dict[str, object]:
        result, revision, projection = self.mutate(
            actor=lambda value: value.get("session_id"),
            entity_type="task",
            entity_id=lambda value: value.get("task_id"),
            event_type="task.completed",
            payload=lambda value: {
                "disposition": disposition,
                "completion_revision": value.get("handoff", {}).get("completion_revision"),
                "reached_checkpoints": value.get("reached_checkpoints", []),
            },
            operation=lambda conn, revision: self._complete_claim_in_transaction(
                conn, revision, authenticate_claim(conn, claim_token), disposition, note,
            ),
        )
        return {**result, "project_revision": revision, "projection": projection}

    def complete_claim_id(self, claim_id: str, disposition: str, note: str = "", *, terminal_hook=None) -> dict[str, object]:
        result, revision, projection = self.mutate(
            actor=lambda value: value.get("session_id"), entity_type="task",
            entity_id=lambda value: value.get("task_id"), event_type="task.completed",
            payload=lambda value: {
                "disposition": disposition,
                "completion_revision": value.get("handoff", {}).get("completion_revision"),
                "reached_checkpoints": value.get("reached_checkpoints", []),
            },
            operation=lambda conn, revision: self._complete_claim_in_transaction(
                conn,
                revision,
                conn.execute("SELECT * FROM claims WHERE id=? AND state='active'", (claim_id,)).fetchone(),
                disposition,
                note,
                terminal_hook,
            ),
            canonical_workflow=True,
        )
        return {**result, "project_revision": revision, "projection": projection}

    def _handoff_payload(self, conn: sqlite3.Connection, claim: sqlite3.Row, note: str) -> dict[str, object]:
        source_root = self._claim_source_root(conn, claim)
        scopes = scopes_for(conn, claim["task_id"], "exclusive")
        dirty = [path for path in dirty_paths(source_root) if any(path_contains(root, path) for root in scopes)]
        checkpoints = [dict(row) for row in conn.execute("SELECT id,state,reached_at FROM checkpoints WHERE task_id=?", (claim["task_id"],))]
        gates = [dict(row) for row in conn.execute(
            "SELECT id,status,valid,input_fingerprint,last_run_at,revision FROM gates WHERE task_id=?",
            (claim["task_id"],),
        )]
        interfaces = [dict(row) for row in conn.execute("SELECT id,state,version,content_hash FROM interfaces WHERE owner_task_id=?", (claim["task_id"],))]
        resources = [dict(row) for row in conn.execute("SELECT instance_id,state,acquired_at,released_at FROM resource_leases WHERE claim_id=?", (claim["id"],))]
        return {"task_id": claim["task_id"], "git_head": git_head(source_root), "producer_commit": git_head(source_root), "producer_root": str(source_root), "authority_commit": git_head(self.paths.repo_root), "integration_commit": None, "git_diffstat": git_diffstat(source_root), "changed_owned_paths": dirty, "checkpoints": checkpoints, "gates": gates, "interfaces": interfaces, "resources": resources, "note": note}

    def handoff(self, claim_token: str, *, note: str = "", status: str = "in_progress", reason: str | None = None) -> dict[str, object]:
        def operation(conn, revision):
            claim = authenticate_claim(conn, claim_token)
            payload = self._handoff_payload(conn, claim, note)
            handoff_id = str(uuid.uuid4())
            conn.execute("INSERT INTO handoffs(id,task_id,claim_id,kind,note,payload_json,created_at,revision) VALUES(?,?,?,?,?,?,?,?)", (handoff_id, claim["task_id"], claim["id"], "handoff", note, json.dumps(payload, sort_keys=True), utc_now(), revision))
            release_claim(conn, claim_token, next_status=status, reason=reason)
            return {"handoff_id": handoff_id, "task_id": claim["task_id"], "session_id": claim["session_id"], "status": status, "handoff": payload}
        result, revision, projection = self.mutate(actor=lambda value: value.get("session_id"), entity_type="handoff", entity_id=lambda value: value.get("task_id"), event_type="task.handed_off", payload={"status": status}, operation=operation)
        return {**result, "project_revision": revision, "projection": projection}

    def context(self, claim_token: str, section: str | None = None) -> object:
        self.pulse(claim_token)
        with self.db.read() as conn:
            claim = authenticate_claim(conn, claim_token, allow_orphaned=True)
            if section:
                return expand_context(conn, claim["task_id"], section)
            session = conn.execute("SELECT id AS agent_id,label,hostname FROM sessions WHERE id=?", (claim["session_id"],)).fetchone()
            revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
            return build_context(conn, project_revision=revision, session=dict(session), session_token="<redacted-use-existing-session-token>", claim=dict(claim), claim_token=claim_token, budget=self.context_budget)

    def changes(self, since: int, claim_token: str | None = None) -> dict[str, object]:
        if claim_token:
            self.pulse(claim_token)
        with self.db.read() as conn:
            task_id = authenticate_claim(conn, claim_token, allow_orphaned=True)["task_id"] if claim_token else None
            revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
            return {"since": since, "events": changes_since(conn, since, task_id), "project_revision": revision}

    def checkpoint(self, action: str, checkpoint_id: str, claim_token: str | None = None) -> dict[str, object]:
        if action == "status":
            with self.db.read() as conn:
                return checkpoint_status(conn, checkpoint_id)
        def operation(conn, revision):
            pulse_claim(conn, claim_token, self.claim_lease_seconds)
            claim = authenticate_claim(conn, claim_token)
            checkpoint = conn.execute("SELECT task_id FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
            if checkpoint and claim["task_id"] != checkpoint["task_id"]:
                raise TodoError("claim_task_mismatch", "Checkpoint is owned by another task", ExitCode.INVALID_TOKEN)
            result = reach_checkpoint(conn, self.paths.repo_root, checkpoint_id, revision) if action == "reach" else revoke_checkpoint(conn, checkpoint_id, revision)
            result["session_id"] = claim["session_id"]
            return result
        result, revision, projection = self.mutate(actor=lambda value: value.get("session_id"), entity_type="checkpoint", entity_id=checkpoint_id, event_type=f"checkpoint.{action}", payload={}, operation=operation)
        return {**result, "project_revision": revision, "projection": projection}

    def terminal_checkpoint_finalize(
        self, task_id: str, checkpoint_id: str | None = None,
    ) -> dict[str, object]:
        with self.db.read() as conn:
            report = terminal_finalization_report(conn, task_id, checkpoint_id)
        if not report["eligible"]:
            if report["rejected"]:
                raise TodoError(
                    "terminal_checkpoint_prerequisites_unsatisfied",
                    "Checkpoint prerequisites were not satisfied at owner completion",
                    ExitCode.GATE_FAILURE,
                    {"checkpoints": report["rejected"], "completion_revision": report["completion_revision"]},
                )
            return {
                **report,
                "status": "already_finalized" if report["already_reached"] else "nothing_eligible",
                "reached": [],
                "project_revision": self.db.revision(),
                "idempotent_noop": True,
            }
        result, revision, projection = self.mutate(
            actor=None,
            entity_type="task",
            entity_id=task_id,
            event_type="task.terminal_checkpoints_finalized",
            payload=lambda value: {
                "task_id": task_id,
                "checkpoint_id": checkpoint_id,
                "completion_revision": value.get("completion_revision"),
                "reached_checkpoints": [item["checkpoint_id"] for item in value.get("reached", [])],
                "authority": "recorded_successful_terminal_completion",
            },
            operation=lambda conn, revision: recover_terminal_checkpoints(
                conn, self.paths.repo_root, task_id, checkpoint_id, revision,
            ),
        )
        return {
            **result, "status": "finalized", "idempotent_noop": False,
            "project_revision": revision, "projection": projection,
        }

    def barrier(self, barrier_id: str) -> dict[str, object]:
        with self.db.read() as conn:
            return barrier_report(conn, barrier_id)

    def interface(self, action: str, interface_id: str, version: str | None = None, claim_token: str | None = None) -> dict[str, object]:
        if action == "status":
            with self.db.read() as conn:
                return interface_status(conn, interface_id)
        callback = freeze_interface if action == "freeze" else revise_interface
        def operation(conn, revision):
            pulse_claim(conn, claim_token, self.claim_lease_seconds)
            claim = authenticate_claim(conn, claim_token)
            interface = conn.execute("SELECT owner_task_id FROM interfaces WHERE id=?", (interface_id,)).fetchone()
            if not interface or interface["owner_task_id"] != claim["task_id"]:
                raise TodoError("claim_task_mismatch", "The active claim does not own this interface", ExitCode.INVALID_TOKEN)
            result = callback(conn, self.paths.repo_root, interface_id, version, revision)
            result["barrier_changes"] = reevaluate_barriers(conn, revision)
            result["session_id"] = claim["session_id"]
            return result
        result, revision, projection = self.mutate(actor=lambda value: value.get("session_id"), entity_type="interface", entity_id=interface_id, event_type=f"interface.{action}", payload={"version": version}, operation=operation)
        return {**result, "project_revision": revision, "projection": projection}

    def gate_run(self, gate_id: str, claim_token: str | None) -> dict[str, object]:
        require_mutation_route(
            self.project,
            operation="gate.run",
            mutation_mode=self.mutation_mode,
        )
        result, revision = run_gate(self.db, self.paths, self.project, gate_id, claim_token)
        result["project_revision"] = revision
        result["projection"] = self.refresh({str(result["task_id"])})
        return result

    def gate_list(self, task_id: str | None = None) -> list[dict[str, object]]:
        with self.db.read() as conn:
            return list_gates(conn, task_id)

    def gate_explain(self, gate_id: str) -> dict[str, object]:
        with self.db.read() as conn:
            return explain_gate(conn, gate_id)

    def resource_discover(self) -> dict[str, object]:
        inventory = discover_nvidia()
        def operation(conn, revision):
            conn.execute("UPDATE resource_instances SET enabled=0 WHERE class_id='gpu' AND hostname=?", (socket.gethostname(),))
            return {"count": upsert_inventory(conn, inventory), "resources": inventory}
        result, revision, _ = self.mutate(actor=None, entity_type="resource", entity_id="gpu", event_type="resource.discovered", payload={"count": len(inventory)}, operation=operation)
        return {**result, "project_revision": revision}

    def resource_list(self) -> list[dict[str, object]]:
        with self.db.read() as conn:
            return list_resources(conn)

    def resource_acquire(self, selector: str, claim_token: str) -> dict[str, object]:
        tokens: dict[str, str] = {}
        def operation(conn, revision):
            pulse_claim(conn, claim_token, self.claim_lease_seconds)
            claim = authenticate_claim(conn, claim_token)
            lease, token = acquire_resource(conn, selector=selector, session_id=claim["session_id"], claim_id=claim["id"], request_id=None, lease_seconds=self.resource_lease_seconds)
            tokens["token"] = token
            lease["session_id"] = claim["session_id"]
            return lease
        result, revision, _ = self.mutate(actor=lambda value: value.get("session_id"), entity_type="resource", entity_id=selector, event_type="resource.acquired", payload={}, operation=operation)
        return {**result, "lease_token": tokens["token"], "project_revision": revision}

    def resource_release(self, lease_token: str) -> dict[str, object]:
        result, revision, _ = self.mutate(actor=lambda value: value.get("session_id"), entity_type="resource", entity_id=lambda value: value.get("instance_id"), event_type="resource.released", payload={}, operation=lambda conn, revision: release_resource(conn, lease_token))
        return {**result, "project_revision": revision}

    def lock_acquire(self, name: str, claim_token: str) -> dict[str, object]:
        return self.lock_acquire_many([name], claim_token)["leases"][0]

    def lock_acquire_many(self, names: list[str], claim_token: str, command: list[str] | None = None) -> dict[str, object]:
        def operation(conn, revision):
            pulse_claim(conn, claim_token, self.claim_lease_seconds)
            claim = authenticate_claim(conn, claim_token)
            return {"leases": acquire_named_locks(conn, names, claim_id=claim["id"], session_id=claim["session_id"], lease_seconds=self.resource_lease_seconds, command=command), "session_id": claim["session_id"]}
        result, revision, _ = self.mutate(actor=lambda value: value.get("session_id"), entity_type="lock", entity_id=",".join(sorted(names)), event_type="lock.acquired", payload={"names": sorted(names)}, operation=operation)
        return {"leases": result["leases"], "project_revision": revision}

    def lock_release(self, token: str) -> dict[str, object]:
        result, revision, _ = self.mutate(actor=lambda value: value.get("session_id"), entity_type="lock", entity_id=lambda value: value.get("name"), event_type="lock.released", payload={}, operation=lambda conn, revision: release_lock(conn, token))
        return {**result, "project_revision": revision}

    def guard(self, claim_token: str, paths: list[str]) -> dict[str, object]:
        self.pulse(claim_token)
        with self.db.read() as conn:
            claim = authenticate_claim(conn, claim_token)
            return guard_paths(conn, self.paths.repo_root, claim["id"], paths)

    def decision(self, action: str, decision_id: str, value: object | None = None) -> dict[str, object]:
        if action == "status":
            with self.db.read() as conn:
                row = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
                if not row:
                    raise TodoError("decision_not_found", f"Unknown decision {decision_id}")
                return {**dict(row), "value": json.loads(row["value_json"]) if row["value_json"] is not None else None, "allowed": json.loads(row["allowed_json"])}
        def operation(conn, revision):
            row = conn.execute("SELECT allowed_json FROM decisions WHERE id=?", (decision_id,)).fetchone()
            if not row:
                raise TodoError("decision_not_found", f"Unknown decision {decision_id}")
            allowed = json.loads(row["allowed_json"] or "[]")
            if allowed and value not in allowed:
                raise TodoError("decision_value_invalid", f"Value is not allowed for {decision_id}", details={"allowed": allowed})
            conn.execute("UPDATE decisions SET value_json=?,updated_at=?,revision=? WHERE id=?", (json.dumps(value, sort_keys=True), utc_now(), revision, decision_id))
            return {"decision_id": decision_id, "value": value}
        result, revision, projection = self.mutate(actor=None, entity_type="decision", entity_id=decision_id, event_type="decision.set", payload={"value": value}, operation=operation)
        return {**result, "project_revision": revision, "projection": projection}

    def audit(self) -> dict[str, object]:
        with self.db.read() as conn:
            return audit_state(conn, self.paths.repo_root, self.paths.snapshot_file)

    def reconcile(self) -> dict[str, object]:
        result, revision, projection = self.mutate(actor=None, entity_type="project", entity_id=str(self.project["project_uuid"]), event_type="project.reconciled", payload={}, operation=lambda conn, revision: reconcile_state(conn, self.paths.repo_root, self.paths.snapshot_file, revision), full_projection=True)
        return {**result, "project_revision": revision, "projection": projection}

    def recover(self, action: str, task_id: str, *, session_token: str | None = None, acknowledge_dirty: bool = False) -> dict[str, object]:
        if action == "inspect":
            with self.db.read() as conn:
                return inspect_recovery(conn, self.paths.repo_root, task_id)
        credentials: dict[str, str] = {}
        def operation(conn, revision):
            if action == "release":
                return recover_release(conn, self.paths.repo_root, task_id, acknowledge_dirty)
            if session_token:
                session = authenticate_session(conn, session_token)
                credentials["session_token"] = session_token
            else:
                session_data, raw = create_session(conn, self.paths.repo_root, {"command": "recover adopt"})
                session = {"id": session_data["agent_id"]}
                credentials["session_token"] = raw
            claim, token = recover_adopt(conn, self.paths.repo_root, task_id, session["id"], revision, self.claim_lease_seconds)
            credentials["claim_token"] = token
            return claim
        result, revision, projection = self.mutate(actor=None, entity_type="recovery", entity_id=task_id, event_type=f"recovery.{action}", payload={}, operation=operation)
        return {**result, **credentials, "project_revision": revision, "projection": projection}

    def live_recovery_inspect(self, task_id: str) -> dict[str, object]:
        revision = self.db.revision()
        with self.db.read() as conn:
            return inspect_live_override(
                conn,
                self.paths.repo_root,
                str(self.project["project_uuid"]),
                task_id,
                revision,
            )

    def live_recovery_approve(self, task_id: str, reason: str, ttl_seconds: int) -> dict[str, object]:
        credential: dict[str, str] = {}

        def operation(conn, revision):
            report, token = approve_live_override(
                conn,
                self.paths.repo_root,
                str(self.project["project_uuid"]),
                task_id,
                revision,
                reason,
                ttl_seconds,
            )
            credential["approval_token"] = token
            return report

        result, revision, projection = self.mutate(
            actor=None,
            entity_type="recovery_approval",
            entity_id=task_id,
            event_type="recovery.live_approved",
            payload={"task_id": task_id, "reason": reason[:1000], "requester_uid": os.getuid()},
            operation=operation,
        )
        return {**result, **credential, "project_revision": revision, "projection": projection}

    def live_recovery_override(
        self,
        task_id: str,
        approval_token: str,
        new_instance_id: str,
    ) -> dict[str, object]:
        def operation(conn, revision):
            claim, issued = override_live_claim(
                conn,
                self.paths.repo_root,
                str(self.project["project_uuid"]),
                task_id,
                revision,
                approval_token,
                new_instance_id,
                self.claim_lease_seconds,
            )
            return build_context(
                conn,
                project_revision=revision,
                session=issued["session"],
                session_token=issued["session_token"],
                claim=claim,
                claim_token=issued["claim_token"],
                budget=self.context_budget,
            )

        result, revision, projection = self.mutate(
            actor=lambda value: value.get("session", {}).get("agent_id"),
            entity_type="recovery",
            entity_id=task_id,
            event_type="recovery.live_overridden",
            payload={
                "task_id": task_id,
                "new_instance_id": new_instance_id,
                "disposition": "live_claim_replaced",
            },
            operation=operation,
        )
        result["project_revision"] = revision
        result["projection"] = projection
        return result

    def force_release_inspect(self, task_id: str, acknowledge_dirty: bool = False) -> dict[str, object]:
        revision = self.db.revision()
        with self.db.read() as conn:
            return inspect_force_release(
                conn,
                self.paths.repo_root,
                str(self.project["project_uuid"]),
                task_id,
                revision,
                acknowledge_dirty=acknowledge_dirty,
            )

    def force_release_approve(
        self,
        task_id: str,
        reason: str,
        ttl_seconds: int,
        acknowledge_dirty: bool = False,
    ) -> dict[str, object]:
        credential: dict[str, str] = {}

        def operation(conn, revision):
            report, token = approve_force_release(
                conn,
                self.paths.repo_root,
                str(self.project["project_uuid"]),
                task_id,
                revision,
                reason,
                ttl_seconds,
                acknowledge_dirty,
            )
            credential["approval_token"] = token
            return report

        result, revision, projection = self.mutate(
            actor=None,
            entity_type="recovery_approval",
            entity_id=task_id,
            event_type="recovery.force_release_approved",
            payload={
                "task_id": task_id,
                "reason": reason[:1000],
                "requester_uid": os.getuid(),
                "acknowledge_dirty": bool(acknowledge_dirty),
            },
            operation=operation,
        )
        return {**result, **credential, "project_revision": revision, "projection": projection}

    def force_release(self, task_id: str, approval_token: str) -> dict[str, object]:
        result, revision, projection = self.mutate(
            actor=None,
            entity_type="recovery",
            entity_id=task_id,
            event_type="claim.force_released",
            payload={"task_id": task_id, "disposition": "owner_force_released"},
            operation=lambda conn, revision: force_release_live_claim(
                conn,
                self.paths.repo_root,
                str(self.project["project_uuid"]),
                task_id,
                revision,
                approval_token,
            ),
        )
        return {**result, "project_revision": revision, "projection": projection}

    def migrate_markdown(self, apply: bool) -> dict[str, object]:
        report = inspect_markdown(self.paths.repo_root)
        summary = {"tasks_found": len(report["records"]), "warnings": report["warnings"]}
        if not apply:
            return {**summary, "dry_run": True}
        result, revision, projection = self.mutate(actor=None, entity_type="migration", entity_id="markdown", event_type="migration.markdown_applied", payload=summary, operation=lambda conn, revision: apply_markdown_import(conn, self.paths.repo_root, report, revision), full_projection=True)
        return {**summary, **result, "project_revision": revision, "projection": projection}

    def doctor(self) -> dict[str, object]:
        integrity = self.db.integrity()
        audit = self.audit()
        return {"database": integrity, "audit": audit, "state_db": str(self.paths.db_file), "snapshot": str(self.paths.snapshot_file), "clean": integrity["integrity"] == "ok" and not integrity["foreign_key_errors"] and audit["clean"]}

    def export(self) -> dict[str, object]:
        if self.read_only:
            with self.db.read() as conn:
                snapshot = build_snapshot(conn, self.project)
            return {"state": snapshot, "project_revision": int(snapshot["project_revision"])}
        revision = write_snapshot(self.db, self.paths, self.project)
        return {"snapshot": str(self.paths.snapshot_file), "project_revision": revision}

    def cleanup(self) -> dict[str, object]:
        with self.db.read() as conn:
            unfinished = [row[0] for row in conn.execute("SELECT id FROM tasks WHERE status NOT IN ('done','superseded','cancelled')")]
        if unfinished:
            raise TodoError("cleanup_blocked", "Explicit cleanup is blocked by unfinished tasks", ExitCode.BLOCKED, {"unfinished": unfinished})
        return {"safe": True, "message": "Cleanup remains explicit; v2 durable state was not deleted."}
