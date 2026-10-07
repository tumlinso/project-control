"""Managed first-class lane workspaces and integration queues.

Git worktrees provide mutation isolation; todo SQLite remains the semantic
authority.  This module deliberately has no cleanup operation: dirty or
conflicted work is preserved, and cleanup only becomes *eligible* after an
explicit, validated state transition.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

from ..config import utc_now
from ..git_state import integration_diff_args, material_dirty_paths
from ..models import TodoError
from .foundation import WORKSPACE_MODES, WorkflowDatabase


Runner = Callable[..., subprocess.CompletedProcess[bytes]]
_BRANCH = re.compile(r"^(?![-.])(?!.*(?:\.\.|@\{|//|[\\ ~^:?*\[]))(?!.*[/.]$).+$")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_immutable(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise TodoError("artifact_hash_collision", "Managed artifact hash collision")
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o444)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class WorkspaceService:
    """Transactional workspace state plus conservative Git operations."""

    def __init__(
        self,
        db: WorkflowDatabase,
        *,
        managed_root: Path,
        runner: Runner | None = None,
        repository_identity_resolver: Callable[[Path], str] | None = None,
    ):
        self.db = db
        self.managed_root = managed_root.resolve()
        self.runner = runner or subprocess.run
        self.repository_identity_resolver = repository_identity_resolver

    def _git(self, repo: Path, args: Sequence[str], *, text: bool = False) -> subprocess.CompletedProcess[Any]:
        return self.runner(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            check=False,
            text=text,
        )

    def _git_ok(self, repo: Path, args: Sequence[str], *, code: str) -> bytes:
        result = self._git(repo, args)
        if result.returncode != 0:
            raise TodoError(code, "Git operation failed", details={"returncode": result.returncode})
        return result.stdout

    def _git_input(self, repo: Path, args: Sequence[str], content: bytes) -> subprocess.CompletedProcess[bytes]:
        return self.runner(
            ["git", "-C", str(repo), *args],
            input=content,
            capture_output=True,
            check=False,
        )

    def _git_input_ok(self, repo: Path, args: Sequence[str], content: bytes, *, code: str) -> bytes:
        result = self.runner(
            ["git", "-C", str(repo), *args],
            input=content,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise TodoError(code, "Git operation failed", details={"returncode": result.returncode})
        return result.stdout

    def _managed_path(self, value: Path) -> Path:
        target = value.resolve()
        try:
            target.relative_to(self.managed_root)
        except ValueError as exc:
            raise TodoError("workspace_path_unmanaged", "Workspace path must be below the managed state root") from exc
        if target == self.managed_root:
            raise TodoError("workspace_path_unmanaged", "Managed state root cannot itself be a workspace")
        return target

    def _commit(self, repository_root: Path, ref: str) -> str:
        raw = self._git_ok(repository_root, ["rev-parse", "--verify", f"{ref}^{{commit}}"], code="workspace_base_missing")
        return raw.decode("utf-8", errors="replace").strip()

    def _source_diff(self, repository_root: Path, base_commit: str, *, code: str) -> bytes:
        """Return material integration changes without mutable authority projections."""
        return self._git_ok(
            repository_root,
            integration_diff_args(base_commit),
            code=code,
        )

    def _source_identity(self, repository_root: Path, base_commit: str) -> str:
        """Hash material tracked destination state relative to its frozen base."""
        return _sha256(
            self._source_diff(
                repository_root,
                base_commit,
                code="integration_source_identity_failed",
            )
        )

    def _freeze_integration_commit(
        self,
        repository_root: Path,
        *,
        base_commit: str,
        queue_id: str,
        source_identity: str,
    ) -> str:
        """Create an immutable commit/ref for the gated index without moving HEAD."""
        tree = self._git_ok(repository_root, ["write-tree"], code="integration_tree_freeze_failed").decode().strip()
        frozen_diff = self._git_ok(
            repository_root,
            integration_diff_args(base_commit, tree),
            code="integration_tree_verify_failed",
        )
        if _sha256(frozen_diff) != source_identity:
            raise TodoError("integration_tree_mismatch", "Frozen integration tree differs from the gated source")
        commit = self._git_input_ok(
            repository_root,
            ["commit-tree", tree, "-p", base_commit],
            f"coding-workflow integration {queue_id}\n".encode("utf-8"),
            code="integration_commit_freeze_failed",
        ).decode().strip()
        self._git_ok(
            repository_root,
            ["update-ref", f"refs/coding-workflow/integrations/{queue_id}", commit],
            code="integration_ref_freeze_failed",
        )
        return commit

    def _advance_integration_head(self, repository_root: Path, frozen_commit: str) -> None:
        """Advance only the managed destination ref; its index/tree already match."""
        previous = self._commit(repository_root, "HEAD")
        self._git_ok(
            repository_root,
            ["update-ref", "HEAD", frozen_commit, previous],
            code="integration_head_advance_failed",
        )
        if self._commit(repository_root, "HEAD") != frozen_commit:
            raise TodoError("integration_head_advance_failed", "Managed destination did not advance to the frozen integration commit")
        if material_dirty_paths(repository_root):
            raise TodoError("integration_head_state_mismatch", "Managed destination does not exactly match the frozen integration commit")

    def _lane(self, conn: Any, run_id: str, lane_id: str) -> Any:
        row = conn.execute(
            "SELECT id,run_id,role,workspace_mode,state FROM workflow_lanes WHERE id=? AND run_id=?",
            (lane_id, run_id),
        ).fetchone()
        if row is None:
            raise TodoError("workspace_lane_missing", "Workspace requires a first-class lane in the active run")
        return row

    def create_workspace(
        self,
        *,
        repository_root: Path,
        repository_identity: str,
        run_id: str,
        lane_id: str,
        mode: str,
        base_commit: str,
        worktree_path: Path | None,
        branch: str | None,
        integration_task_id: str | None,
        actor_session_id: str | None = None,
        worker_class: str = "first_class",
    ) -> dict[str, object]:
        """Materialize and record a managed first-class workspace.

        If the semantic record cannot be written after Git creates the worktree,
        the worktree is intentionally left in place for owner inspection.
        """
        if worker_class != "first_class":
            raise TodoError("local_child_workspace_forbidden", "Local-worker children cannot own first-class lane workspaces")
        if mode not in WORKSPACE_MODES:
            raise TodoError("invalid_workspace_mode", f"Unsupported workspace mode: {mode}")
        repository_root = repository_root.resolve()
        if not repository_identity:
            raise TodoError("repository_identity_required", "Repository identity is required")
        if mode != "read_shared" and self.repository_identity_resolver is None:
            raise TodoError("repository_identity_resolver_required", "Writable workspaces require an authoritative repository identity resolver")
        if self.repository_identity_resolver is not None:
            authoritative_identity = self.repository_identity_resolver(repository_root)
            if repository_identity != authoritative_identity:
                raise TodoError("repository_identity_mismatch", "Workspace repository identity is not authoritative")
        canonical_base = self._commit(repository_root, base_commit)
        target: Path | None = None
        if mode != "read_shared":
            if worktree_path is None:
                raise TodoError("workspace_path_required", f"{mode} requires a managed worktree path")
            target = self._managed_path(worktree_path)
            if target.exists():
                raise TodoError("workspace_path_exists", "Managed worktree path already exists")
            if branch is not None and not _BRANCH.match(branch):
                raise TodoError("invalid_workspace_branch", "Workspace branch name is unsafe or invalid")
        elif worktree_path is not None or branch is not None:
            raise TodoError("read_shared_materialization_forbidden", "read_shared does not create a writable worktree")
        if mode == "isolated_merge" and not integration_task_id:
            raise TodoError("integration_task_required", "isolated_merge requires an explicit integration task")

        workspace_id = str(uuid.uuid4())
        now = utc_now()

        def reserve(conn: Any, revision: int) -> dict[str, object]:
            lane = self._lane(conn, run_id, lane_id)
            if lane["workspace_mode"] != mode:
                raise TodoError("lane_workspace_mode_mismatch", "Workspace mode differs from the lane contract")
            if mode == "isolated_merge":
                stale = conn.execute(
                    """SELECT id,base_commit FROM workflow_workspaces
                       WHERE run_id=? AND integration_task_id=? AND mode='isolated_merge' AND base_commit<>?""",
                    (run_id, integration_task_id, canonical_base),
                ).fetchone()
                if stale is not None:
                    raise TodoError(
                        "workspace_base_mismatch",
                        "All isolated participants for an integration task must start from the exact same base commit",
                        details={"existing_workspace_id": stale["id"]},
                    )
            conn.execute(
                """INSERT INTO workflow_workspaces(
                     id,repository_identity,run_id,lane_id,mode,base_commit,worktree_path,branch,state,
                     integration_task_id,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    workspace_id, repository_identity, run_id, lane_id, mode, canonical_base,
                    str(target) if target else None, branch, "provisioning", integration_task_id, now, now,
                ),
            )
            return {"workspace_id": workspace_id, "run_id": run_id, "lane_id": lane_id, "mode": mode, "base_commit": canonical_base}

        def activate(conn: Any, rev: int) -> dict[str, object]:
            changed = conn.execute(
                "UPDATE workflow_workspaces SET state='active',updated_at=? WHERE id=? AND state='provisioning'",
                (utc_now(), workspace_id),
            )
            if changed.rowcount != 1:
                raise TodoError("workspace_provisioning_state_changed", "Workspace reservation changed during materialization")
            return {"workspace_id": workspace_id, "run_id": run_id, "lane_id": lane_id, "mode": mode, "base_commit": canonical_base}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_workspace",
            entity_id=workspace_id,
            event_type="workflow_workspace_reserved",
            payload={"run_id": run_id, "lane_id": lane_id, "mode": mode, "base_commit": canonical_base},
            operation=reserve,
        )
        try:
            if target is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                args = ["worktree", "add"]
                if branch:
                    args.extend(["-b", branch])
                else:
                    args.append("--detach")
                args.extend([str(target), canonical_base])
                self._git_ok(repository_root, args, code="workspace_materialization_failed")
        except Exception:
            self.db.mutate(
                actor_session_id=actor_session_id,
                entity_type="workflow_workspace",
                entity_id=workspace_id,
                event_type="workflow_workspace_provisioning_failed",
                payload={"preserved": True},
                operation=lambda conn, rev: conn.execute(
                    "UPDATE workflow_workspaces SET state='provisioning_failed',updated_at=? WHERE id=?",
                    (utc_now(), workspace_id),
                ),
            )
            raise

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_workspace",
            entity_id=workspace_id,
            event_type="workflow_workspace_created",
            payload={"run_id": run_id, "lane_id": lane_id, "mode": mode, "base_commit": canonical_base},
            operation=activate,
        )
        result["revision"] = revision
        result["worktree_path"] = str(target) if target else None
        return result

    def resume_quarantined_workspace(
        self,
        *,
        repository_root: Path,
        repository_identity: str,
        run_id: str,
        lane_id: str,
        workspace_id: str,
        expected_base_commit: str,
        expected_head: str,
        actor_session_id: str | None = None,
    ) -> dict[str, object]:
        """Reactivate one proven-clean quarantined managed workspace.

        This deliberately does not reconcile sibling workspaces, alter Git, or
        accept retained source.  Callers must bind every identity and the
        observed HEAD in their authority before asking for this narrow resume.
        """
        repository_root = repository_root.resolve()
        if not repository_identity:
            raise TodoError("repository_identity_required", "Workspace repository identity is required")
        if self.repository_identity_resolver is None:
            raise TodoError("repository_identity_resolver_required", "Workspace resume requires an authoritative repository identity resolver")
        authoritative_identity = self.repository_identity_resolver(repository_root)
        if repository_identity != authoritative_identity:
            raise TodoError("repository_identity_mismatch", "Workspace repository identity is not authoritative")

        with self.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_workspaces WHERE id=? AND run_id=? AND lane_id=?",
                (workspace_id, run_id, lane_id),
            ).fetchone()
            if row is None:
                raise TodoError("workspace_resume_target_missing", "Exact quarantined workspace is unavailable")
            workspace = dict(row)
            if (workspace["state"] != "quarantined" or workspace["mode"] == "read_shared" or
                    not workspace["worktree_path"]):
                raise TodoError("workspace_resume_not_quarantined", "Only a writable quarantined managed workspace can resume")
            if (workspace["repository_identity"] != repository_identity or
                    workspace["base_commit"] != expected_base_commit):
                raise TodoError("workspace_resume_identity_changed", "Workspace identity or base differs from the approved resume")
            active_owner = conn.execute(
                "SELECT 1 FROM workflow_dispatches WHERE workspace_id=? AND state='active' "
                "UNION SELECT 1 FROM claims c JOIN workflow_lane_tasks lt ON lt.task_id=c.task_id "
                "WHERE lt.lane_id=? AND c.state IN ('active','orphaned') "
                "UNION SELECT 1 FROM resource_leases r JOIN claims c ON c.id=r.claim_id "
                "JOIN workflow_lane_tasks lt ON lt.task_id=c.task_id "
                "WHERE lt.lane_id=? AND r.state='active' "
                "UNION SELECT 1 FROM child_executions ce JOIN workflow_lane_tasks lt ON lt.task_id=ce.task_id "
                "WHERE lt.lane_id=? AND ce.state NOT IN ('succeeded','failed','cancelled','rejected') "
                "UNION SELECT 1 FROM gates g JOIN workflow_lane_tasks lt ON lt.task_id=g.task_id "
                "WHERE lt.lane_id=? AND g.status='running' "
                "UNION SELECT 1 FROM workflow_patch_artifacts WHERE workspace_id=? "
                "UNION SELECT 1 FROM workflow_integration_queue q JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id "
                "WHERE a.workspace_id=?",
                (workspace_id, lane_id, lane_id, lane_id, lane_id, workspace_id, workspace_id),
            ).fetchone()
            if active_owner is not None:
                raise TodoError("workspace_resume_owner_or_artifact_active", "Workspace has active ownership or integration state")

        target = self._managed_path(Path(str(workspace["worktree_path"])))
        if not target.is_dir():
            raise TodoError("workspace_missing", "Workspace path is unavailable; no resume state was changed")
        status = self._git(target, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
        if status.returncode != 0:
            raise TodoError("workspace_cleanliness_unknown", "Git cleanliness could not be verified")
        if material_dirty_paths(target):
            raise TodoError("adoption_required", "Retained workspace content requires exact adoption before resume")
        if self._commit(target, "HEAD") != expected_head:
            raise TodoError("workspace_resume_head_changed", "Workspace HEAD differs from the approved resume")

        def resume(conn: Any, revision: int) -> dict[str, object]:
            current = conn.execute(
                "SELECT * FROM workflow_workspaces WHERE id=? AND run_id=? AND lane_id=?",
                (workspace_id, run_id, lane_id),
            ).fetchone()
            if current is None or current["state"] != "quarantined":
                raise TodoError("workspace_resume_state_changed", "Workspace state changed during resume")
            if (current["repository_identity"] != repository_identity or
                    current["base_commit"] != expected_base_commit):
                raise TodoError("workspace_resume_identity_changed", "Workspace identity changed during resume")
            if self._git(target, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]).returncode != 0:
                raise TodoError("workspace_cleanliness_unknown", "Git cleanliness could not be verified during resume")
            if material_dirty_paths(target):
                raise TodoError("adoption_required", "Retained workspace content changed before resume")
            if self._commit(target, "HEAD") != expected_head:
                raise TodoError("workspace_resume_head_changed", "Workspace HEAD changed during resume")
            if conn.execute(
                "SELECT 1 FROM workflow_dispatches WHERE workspace_id=? AND state='active' "
                "UNION SELECT 1 FROM claims c JOIN workflow_lane_tasks lt ON lt.task_id=c.task_id "
                "WHERE lt.lane_id=? AND c.state IN ('active','orphaned') "
                "UNION SELECT 1 FROM workflow_patch_artifacts WHERE workspace_id=?",
                (workspace_id, lane_id, workspace_id),
            ).fetchone() is not None:
                raise TodoError("workspace_resume_state_changed", "Workspace acquired an owner or artifact during resume")
            conn.execute(
                "UPDATE workflow_workspaces SET state='active',cleanup_eligible=0,updated_at=? WHERE id=?",
                (utc_now(), workspace_id),
            )
            return {"workspace_id": workspace_id, "run_id": run_id, "lane_id": lane_id,
                    "state": "active", "worktree_path": str(target), "base_commit": expected_base_commit,
                    "head": expected_head}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_workspace",
            entity_id=workspace_id,
            event_type="workflow_workspace_quarantine_resumed",
            payload={"run_id": run_id, "lane_id": lane_id, "base_commit": expected_base_commit,
                     "head": expected_head},
            operation=resume,
        )
        result["revision"] = revision
        return result

    def reconcile_workspace_base(
        self,
        *,
        repository_root: Path,
        run_id: str,
        lane_id: str,
        base_commit: str,
        reason: str,
        actor_session_id: str | None = None,
    ) -> dict[str, object]:
        """Advance a clean workspace's recorded base after it incorporated that base.

        This is an owner recovery operation for workspaces prepared before an
        earlier integration wave reached the canonical branch. It never changes
        the worktree or branch; the desired base must already be in the lane's
        history. An unqueued pending artifact may be explicitly superseded. A
        still-queued commit artifact may be retargeted only when its material
        diff is byte-identical across the old and new bases. Any artifact that
        integration has started consuming fails closed.
        """
        if not reason.strip():
            raise TodoError("workspace_base_reason_required", "Workspace base reconciliation requires a reason")
        repository_root = repository_root.resolve()
        canonical_base = self._commit(repository_root, base_commit)

        with self.db.read() as conn:
            selected = conn.execute(
                "SELECT * FROM workflow_workspaces WHERE run_id=? AND lane_id=? AND state IN "
                "('active','artifact_ready','queued','conflict','awaiting_gates','gate_failed','quarantined')",
                (run_id, lane_id),
            ).fetchall()
            if len(selected) != 1:
                raise TodoError("workspace_reconcile_target_ambiguous", "Expected exactly one active lane workspace")
            selected_workspace = dict(selected[0])
            all_participants = [dict(row) for row in conn.execute(
                "SELECT * FROM workflow_workspaces WHERE run_id=? AND integration_task_id=? "
                "AND mode='isolated_merge' AND state IN "
                "('active','artifact_ready','queued','conflict','awaiting_gates','gate_failed','quarantined') "
                "ORDER BY lane_id",
                (run_id, selected_workspace["integration_task_id"]),
            ).fetchall()]
            # Contract-split lanes recover independently; isolated participants
            # retain their existing group reconciliation and integrator ordering.
            if selected_workspace["mode"] == "contract_split":
                all_participants = [selected_workspace]
            participants = [
                workspace for workspace in all_participants
                if workspace["base_commit"] != canonical_base or
                workspace["state"] in {"quarantined", "artifact_ready"} or
                workspace["id"] == selected_workspace["id"]
            ]
            workspace_ids = [str(workspace["id"]) for workspace in participants]
            placeholders = ",".join("?" for _ in workspace_ids)
            published = (conn.execute(
                f"SELECT id,workspace_id,state,kind,artifact_ref,content_hash,base_commit "
                f"FROM workflow_patch_artifacts WHERE workspace_id IN ({placeholders}) "
                "AND state IN ('pending','queued','applying','awaiting_gates','gate_failed','finalization_failed')",
                workspace_ids,
            ).fetchall() if workspace_ids else [])
            if any(row["state"] not in {"pending", "queued"} for row in published):
                raise TodoError("workspace_artifact_already_consumed", "Consumed workspace artifacts cannot be rebased")
            queue_rows = (conn.execute(
                "SELECT patch_artifact_id,state FROM workflow_integration_queue WHERE patch_artifact_id IN "
                f"(SELECT id FROM workflow_patch_artifacts WHERE workspace_id IN ({placeholders}))",
                workspace_ids,
            ).fetchall() if workspace_ids else [])
            queued_by_artifact = {str(row["patch_artifact_id"]): str(row["state"]) for row in queue_rows}
            if any(state != "queued" for state in queued_by_artifact.values()):
                raise TodoError("workspace_artifact_already_consumed", "Consumed workspace artifacts cannot be rebased")
            if any(row["state"] == "queued" and queued_by_artifact.get(str(row["id"])) != "queued"
                   for row in published):
                raise TodoError("workspace_reconcile_state_changed", "Queued artifact has no matching queue entry")
            pending_artifact_ids = [str(row["id"]) for row in published if row["state"] == "pending"]
            queued_artifacts = {str(row["workspace_id"]): dict(row) for row in published if row["state"] == "queued"}
            integrator_destination_row = conn.execute(
                "SELECT w.*,current.position AS current_position,current.state AS current_task_state,"
                "required.position AS required_position,required.state AS required_task_state "
                "FROM workflow_lane_tasks required JOIN workflow_lanes l ON l.id=required.lane_id "
                "JOIN workflow_workspaces w ON w.run_id=l.run_id AND w.lane_id=l.id "
                "LEFT JOIN workflow_lane_tasks current ON current.lane_id=l.id "
                "AND current.task_id=w.integration_task_id "
                "WHERE required.task_id=? AND required.lane_id=l.id AND l.run_id=? "
                "AND l.role IN ('integrator','validator')",
                (selected_workspace["integration_task_id"], run_id),
            ).fetchone()
            integrator_destination = (
                dict(integrator_destination_row)
                if integrator_destination_row and selected_workspace["mode"] != "contract_split"
                else None
            )
            if (integrator_destination is not None and
                    integrator_destination["integration_task_id"] != selected_workspace["integration_task_id"]):
                required_position = integrator_destination["required_position"]
                current_position = integrator_destination["current_position"]
                if required_position is None or current_position is None or required_position == current_position:
                    raise TodoError("integration_task_order_invalid", "Integrator destination task ordering is invalid")
                if required_position < current_position:
                    consumed_future = conn.execute(
                        "SELECT 1 FROM workflow_integration_queue WHERE run_id=? AND integration_task_id=? "
                        "AND state<>'queued' LIMIT 1",
                        (run_id, integrator_destination["integration_task_id"]),
                    ).fetchone()
                    if consumed_future is not None:
                        raise TodoError("integration_task_already_consumed", "Cannot restore ordering after future integration work was consumed")
                    integrator_destination["transition"] = "restore_prior"
                else:
                    unfinished_prior = conn.execute(
                        "SELECT 1 FROM workflow_lane_tasks WHERE lane_id=? AND position<? "
                        "AND state<>'completed' LIMIT 1",
                        (integrator_destination["lane_id"], required_position),
                    ).fetchone()
                    if unfinished_prior is not None:
                        raise TodoError("integration_task_order_invalid", "Prior integrator-lane tasks must complete before advancing the destination")
                    if integrator_destination["current_task_state"] != "completed":
                        raise TodoError("integration_task_order_invalid", "Current integrator task is not complete")
                    integrator_destination["transition"] = "advance_next"

        reconciled_workspaces: list[dict[str, object]] = []
        retargeted_artifacts: list[dict[str, object]] = []
        for workspace in participants:
            target = self._managed_path(Path(str(workspace["worktree_path"])))
            if material_dirty_paths(target):
                raise TodoError(
                    "workspace_reconcile_dirty",
                    "Every active integration participant must be clean before base reconciliation",
                    details={"lane_id": workspace["lane_id"]},
                )
            head = self._commit(target, "HEAD")
            old_base = str(workspace["base_commit"])
            old_to_new = self._git(target, ["merge-base", "--is-ancestor", old_base, canonical_base])
            new_to_head = self._git(target, ["merge-base", "--is-ancestor", canonical_base, head])
            if old_to_new.returncode != 0 or new_to_head.returncode != 0:
                raise TodoError(
                    "workspace_base_not_in_history",
                    "Reconciled base must advance every recorded base and already be an ancestor of every workspace HEAD",
                    details={"lane_id": workspace["lane_id"]},
                )
            queued_artifact = queued_artifacts.get(str(workspace["id"]))
            if queued_artifact is not None:
                if queued_artifact["kind"] != "commit" or queued_artifact["base_commit"] != old_base:
                    raise TodoError(
                        "workspace_artifact_not_retargetable",
                        "Only a queued commit artifact on the recorded workspace base can be retargeted",
                    )
                old_material = self._git_ok(
                    target,
                    integration_diff_args(old_base, str(queued_artifact["artifact_ref"])),
                    code="artifact_material_diff_failed",
                )
                new_material = self._git_ok(
                    target, integration_diff_args(canonical_base, head),
                    code="artifact_material_diff_failed",
                )
                if old_material != new_material:
                    raise TodoError(
                        "workspace_artifact_material_changed",
                        "Queued artifact material changed while incorporating the new base",
                        details={"lane_id": workspace["lane_id"]},
                    )
                new_content = self._git_ok(
                    target, ["diff", "--binary", canonical_base, head],
                    code="artifact_diff_failed",
                )
                retargeted_artifacts.append({
                    "artifact_id": queued_artifact["id"],
                    "workspace_id": workspace["id"],
                    "artifact_ref": head,
                    "content_hash": _sha256(new_content),
                    "base_commit": canonical_base,
                })
            reconciled_workspaces.append({
                "workspace_id": workspace["id"],
                "lane_id": workspace["lane_id"],
                "old_base_commit": old_base,
                "head": head,
                "state": workspace["state"],
            })
        if (integrator_destination is not None and
                integrator_destination["integration_task_id"] != selected_workspace["integration_task_id"]):
            destination_path = self._managed_path(Path(str(integrator_destination["worktree_path"])))
            if material_dirty_paths(destination_path):
                raise TodoError("integration_workspace_dirty", "Integrator destination must be clean before task-order repair")
            if self._commit(destination_path, "HEAD") != canonical_base:
                raise TodoError("integration_stale_base", "Integrator destination HEAD must match the restored integration base")

        now = utc_now()

        def reconcile(conn: Any, revision: int) -> dict[str, object]:
            for workspace in reconciled_workspaces:
                current = conn.execute(
                    "SELECT base_commit,state FROM workflow_workspaces WHERE id=?", (workspace["workspace_id"],)
                ).fetchone()
                if (current is None or current["base_commit"] != workspace["old_base_commit"] or
                        current["state"] != workspace["state"]):
                    raise TodoError("workspace_reconcile_state_changed", "Workspace state changed during reconciliation")
            if pending_artifact_ids:
                placeholders = ",".join("?" for _ in pending_artifact_ids)
                rows = conn.execute(
                    f"SELECT id,state FROM workflow_patch_artifacts WHERE id IN ({placeholders})",
                    pending_artifact_ids,
                ).fetchall()
                if len(rows) != len(pending_artifact_ids) or any(row["state"] != "pending" for row in rows):
                    raise TodoError("workspace_reconcile_state_changed", "Workspace artifact state changed during reconciliation")
                conn.execute(
                    f"UPDATE workflow_patch_artifacts SET state='superseded' WHERE id IN ({placeholders})",
                    pending_artifact_ids,
                )
            for artifact in retargeted_artifacts:
                changed = conn.execute(
                    "UPDATE workflow_patch_artifacts SET artifact_ref=?,content_hash=?,base_commit=? "
                    "WHERE id=? AND state='queued'",
                    (artifact["artifact_ref"], artifact["content_hash"], artifact["base_commit"],
                     artifact["artifact_id"]),
                )
                if changed.rowcount != 1:
                    raise TodoError("workspace_reconcile_state_changed", "Queued artifact changed during reconciliation")
            retargeted_by_workspace = {
                str(artifact["workspace_id"]): artifact for artifact in retargeted_artifacts
            }
            for workspace in reconciled_workspaces:
                artifact = retargeted_by_workspace.get(str(workspace["workspace_id"]))
                if artifact is None:
                    conn.execute(
                        "UPDATE workflow_workspaces SET base_commit=?,state='active',artifact_kind=NULL,"
                        "artifact_ref=NULL,diff_hash=NULL,updated_at=? WHERE id=?",
                        (canonical_base, now, workspace["workspace_id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE workflow_workspaces SET base_commit=?,state='queued',artifact_kind='commit',"
                        "artifact_ref=?,diff_hash=?,updated_at=? WHERE id=?",
                        (canonical_base, artifact["artifact_ref"], artifact["content_hash"], now,
                         workspace["workspace_id"]),
                    )
            repaired_destination = None
            if (integrator_destination is not None and
                    integrator_destination["integration_task_id"] != selected_workspace["integration_task_id"]):
                if integrator_destination["transition"] == "advance_next":
                    changed = conn.execute(
                        "UPDATE workflow_workspaces SET integration_task_id=?,base_commit=?,state='active',"
                        "artifact_kind=NULL,artifact_ref=NULL,diff_hash=NULL,updated_at=? "
                        "WHERE id=? AND integration_task_id=?",
                        (selected_workspace["integration_task_id"], canonical_base, now,
                         integrator_destination["id"], integrator_destination["integration_task_id"]),
                    )
                else:
                    changed = conn.execute(
                        "UPDATE workflow_workspaces SET integration_task_id=?,state='active',updated_at=? "
                        "WHERE id=? AND integration_task_id=? AND state='active'",
                        (selected_workspace["integration_task_id"], now, integrator_destination["id"],
                         integrator_destination["integration_task_id"]),
                    )
                if changed.rowcount != 1:
                    raise TodoError("workspace_reconcile_state_changed", "Integrator destination changed during task-order repair")
                repaired_destination = {
                    "workspace_id": integrator_destination["id"],
                    "lane_id": integrator_destination["lane_id"],
                    "from_task_id": integrator_destination["integration_task_id"],
                    "integration_task_id": selected_workspace["integration_task_id"],
                    "transition": integrator_destination["transition"],
                }
            return {
                "workspace_id": selected_workspace["id"],
                "run_id": run_id,
                "lane_id": lane_id,
                "integration_task_id": selected_workspace["integration_task_id"],
                "base_commit": canonical_base,
                "reason": reason,
                "reconciled_workspaces": reconciled_workspaces,
                "repaired_integrator_destination": repaired_destination,
                "superseded_artifact_ids": pending_artifact_ids,
                "retargeted_artifacts": retargeted_artifacts,
            }

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_workspace_group",
            entity_id=str(selected_workspace["integration_task_id"]),
            event_type="workflow_workspace_bases_reconciled",
            payload={
                "run_id": run_id,
                "lane_id": lane_id,
                "integration_task_id": selected_workspace["integration_task_id"],
                "base_commit": canonical_base,
                "reason": reason,
                "workspace_ids": workspace_ids,
                "superseded_artifact_ids": pending_artifact_ids,
                "retargeted_artifact_ids": [item["artifact_id"] for item in retargeted_artifacts],
            },
            operation=reconcile,
        )
        result["revision"] = revision
        return result

    def advance_producer_wave(
        self,
        *,
        repository_root: Path,
        workspace_id: str,
        base_commit: str,
        integration_task_id: str,
        reason: str,
        actor_session_id: str | None = None,
    ) -> dict[str, object]:
        """Resume an integrated producer for its next serial integration wave.

        Owner maintenance only. The caller first incorporates the accepted
        integration into the existing worktree without discarding history.
        This operation changes no Git files and retains every artifact/queue
        receipt; it only advances the clean workspace's current wave contract.
        """
        if not reason.strip():
            raise TodoError("workspace_wave_reason_required", "Wave advancement requires a reason")
        repository_root = repository_root.resolve()
        base = self._commit(repository_root, base_commit)

        def operation(conn: Any, revision: int) -> dict[str, object]:
            row = conn.execute("SELECT * FROM workflow_workspaces WHERE id=?", (workspace_id,)).fetchone()
            if row is None or row["mode"] != "isolated_merge" or row["state"] != "integrated":
                raise TodoError("workspace_wave_not_integrated", "Only an integrated isolated producer can advance")
            if self.repository_identity_resolver is None or self.repository_identity_resolver(repository_root) != row["repository_identity"]:
                raise TodoError("repository_identity_mismatch", "Wave repository identity is not authoritative")
            lane = self._lane(conn, row["run_id"], row["lane_id"])
            run = conn.execute("SELECT status FROM workflow_runs WHERE id=?", (row["run_id"],)).fetchone()
            if run is None or run["status"] != "active":
                raise TodoError("workspace_wave_run_inactive", "Producer run must be active")
            if lane["state"] in {"active", "closed", "cancelled"} or conn.execute(
                "SELECT 1 FROM workflow_dispatches WHERE lane_id=? AND state IN ('active','revoking')",
                (row["lane_id"],),
            ).fetchone() or conn.execute(
                "SELECT 1 FROM workflow_lane_tasks WHERE lane_id=? AND state='active'", (row["lane_id"],),
            ).fetchone() or conn.execute(
                "SELECT 1 FROM claims c JOIN workflow_lane_tasks t ON t.task_id=c.task_id "
                "WHERE t.lane_id=? AND c.state='active'", (row["lane_id"],),
            ).fetchone():
                raise TodoError("workspace_wave_lane_busy", "Producer must be idle with no live dispatch")
            if not conn.execute(
                "SELECT 1 FROM workflow_lane_tasks WHERE lane_id=? AND state='queued'", (row["lane_id"],),
            ).fetchone():
                raise TodoError("workspace_wave_no_remaining_task", "Producer has no remaining serial task")
            pending = conn.execute(
                "SELECT 1 FROM workflow_patch_artifacts WHERE workspace_id=? AND state NOT IN ('integrated','rejected')",
                (workspace_id,),
            ).fetchone()
            queues = conn.execute(
                "SELECT q.* FROM workflow_integration_queue q JOIN workflow_patch_artifacts a "
                "ON a.id=q.patch_artifact_id WHERE a.workspace_id=? ORDER BY q.created_at,q.id",
                (workspace_id,),
            ).fetchall()
            if pending or not queues or any(q["state"] not in {"integrated", "rejected"} for q in queues):
                raise TodoError("workspace_wave_pending_artifacts", "All prior artifact queues must be terminal")
            accepted = [q for q in queues if q["state"] == "integrated" and q["integration_task_id"] == row["integration_task_id"]]
            if not accepted:
                raise TodoError("workspace_wave_receipt_missing", "Current wave has no accepted integration receipt")
            prior = accepted[-1]
            previous_task = conn.execute("SELECT status FROM tasks WHERE id=?", (row["integration_task_id"],)).fetchone()
            current = conn.execute(
                "SELECT position,state FROM workflow_lane_tasks WHERE lane_id=? AND task_id=?",
                (prior["integrator_lane_id"], row["integration_task_id"]),
            ).fetchone()
            following = conn.execute(
                "SELECT task_id,position FROM workflow_lane_tasks WHERE lane_id=? AND state='queued' ORDER BY position LIMIT 1",
                (prior["integrator_lane_id"],),
            ).fetchone()
            if (not previous_task or previous_task["status"] != "done" or not current or current["state"] != "completed"
                    or not following or following["task_id"] != integration_task_id or following["position"] <= current["position"]):
                raise TodoError("workspace_wave_order_invalid", "Prior integration must complete before the next queued integration wave")
            wave_queues = conn.execute(
                "SELECT * FROM workflow_integration_queue WHERE run_id=? AND integration_task_id=? ORDER BY position",
                (row["run_id"], row["integration_task_id"]),
            ).fetchall()
            if any(q["state"] not in {"integrated", "rejected"} for q in wave_queues):
                raise TodoError("workspace_wave_pending_artifacts", "The entire prior integration wave must be terminal")
            # Each queue acceptance freezes the cumulative material tree against
            # the wave base. These commits are siblings, not an ancestry chain.
            # The final accepted queue therefore supersedes intermediate commits.
            final_queue = next(q for q in reversed(wave_queues) if q["state"] == "integrated")
            receipt = json.loads(final_queue["merge_result_json"]).get("integrated_artifact") or {}
            commit = receipt.get("ref") if receipt.get("kind") == "commit" else None
            if not commit or self._git(repository_root, ["merge-base", "--is-ancestor", commit, base]).returncode:
                raise TodoError("workspace_wave_base_unqualified", "New base must contain the final accepted integration-wave commit")
            target = self._managed_path(Path(row["worktree_path"]))
            if self.repository_identity_resolver(target) != row["repository_identity"]:
                raise TodoError("repository_identity_mismatch", "Producer worktree identity changed")
            head = self._commit(target, "HEAD")
            if material_dirty_paths(target) or self._git(target, ["merge-base", "--is-ancestor", base, head]).returncode:
                raise TodoError("workspace_wave_source_unready", "Clean producer must already incorporate the new base")
            if self._git_ok(target, integration_diff_args(base, head), code="workspace_wave_diff_failed"):
                raise TodoError("workspace_wave_source_unready", "Producer must have no unpublished difference from the new base")
            if conn.execute(
                "SELECT 1 FROM workflow_workspaces WHERE run_id=? AND integration_task_id=? "
                "AND mode='isolated_merge' AND id<>? AND base_commit<>?",
                (row["run_id"], integration_task_id, workspace_id, base),
            ).fetchone():
                raise TodoError("workspace_base_mismatch", "Next-wave isolated participants must share the same base")
            conn.execute(
                "UPDATE workflow_workspaces SET state='active',base_commit=?,integration_task_id=?,"
                "artifact_kind=NULL,artifact_ref=NULL,diff_hash=NULL,merge_result_json='{}',cleanup_eligible=0,updated_at=? WHERE id=?",
                (base, integration_task_id, utc_now(), workspace_id),
            )
            return {"workspace_id": workspace_id, "previous_base": row["base_commit"],
                    "previous_integration_task_id": row["integration_task_id"], "base_commit": base,
                    "integration_task_id": integration_task_id, "preserved_head": head, "state": "active"}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id, entity_type="workflow_workspace", entity_id=workspace_id,
            event_type="workflow_producer_wave_advanced",
            payload={"base_commit": base, "integration_task_id": integration_task_id, "reason": reason}, operation=operation,
        )
        result["revision"] = revision
        return result

    def publish_artifact(
        self,
        *,
        workspace_id: str,
        task_id: str,
        kind: str,
        artifact_ref: str,
        actor_session_id: str | None = None,
    ) -> dict[str, object]:
        if kind not in {"commit", "patch"}:
            raise TodoError("invalid_patch_artifact_kind", "Artifact kind must be commit or patch")
        with self.db.read() as conn:
            workspace = conn.execute("SELECT * FROM workflow_workspaces WHERE id=?", (workspace_id,)).fetchone()
            if workspace is None:
                raise TodoError("workspace_missing", "Workspace does not exist")
            if workspace["mode"] != "isolated_merge":
                raise TodoError("workspace_artifact_forbidden", "Only isolated_merge lanes publish integration artifacts")
            if not conn.execute(
                "SELECT 1 FROM workflow_lane_tasks WHERE lane_id=? AND task_id=?",
                (workspace["lane_id"], task_id),
            ).fetchone():
                raise TodoError("workspace_artifact_task_mismatch", "Artifact task is not assigned to the producer lane")
            root = Path(workspace["worktree_path"])
            base = workspace["base_commit"]
        if kind == "commit":
            resolved = self._commit(root, artifact_ref)
            if resolved != self._commit(root, "HEAD"):
                raise TodoError("artifact_workspace_head_mismatch", "Commit artifact must be the registered workspace HEAD")
            ancestry = self._git(root, ["merge-base", "--is-ancestor", base, resolved])
            if ancestry.returncode != 0:
                raise TodoError("artifact_base_mismatch", "Commit artifact is not based on the recorded workspace base")
            artifact_ref = resolved
            content = self._git_ok(root, ["diff", "--binary", base, resolved], code="artifact_diff_failed")
        else:
            patch = Path(artifact_ref).resolve()
            if not patch.is_file():
                raise TodoError("patch_artifact_missing", "Patch artifact file does not exist")
            content = patch.read_bytes()
            if not content:
                raise TodoError("patch_artifact_empty", "Patch artifact must not be empty")
            digest = _sha256(content)
            artifact_dir = self.managed_root / "artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            immutable = artifact_dir / f"{digest}.patch"
            _write_immutable(immutable, content)
            artifact_ref = str(immutable)
        digest = _sha256(content)
        with self.db.read() as conn:
            existing = conn.execute(
                "SELECT id,workspace_id,kind,artifact_ref,content_hash FROM workflow_patch_artifacts "
                "WHERE workspace_id=? AND task_id=? AND state='pending' AND kind=? "
                "AND artifact_ref=? AND content_hash=? AND base_commit=? ORDER BY created_at LIMIT 1",
                (workspace_id, task_id, kind, artifact_ref, digest, base),
            ).fetchone()
        if existing is not None:
            return {
                "artifact_id": str(existing["id"]),
                "workspace_id": str(existing["workspace_id"]),
                "kind": str(existing["kind"]),
                "artifact_ref": str(existing["artifact_ref"]),
                "diff_hash": str(existing["content_hash"]),
                "revision": self.db.revision(),
            }
        artifact_id = str(uuid.uuid4())
        now = utc_now()

        def operation(conn: Any, revision: int) -> dict[str, object]:
            current = conn.execute("SELECT state,base_commit,lane_id FROM workflow_workspaces WHERE id=?", (workspace_id,)).fetchone()
            if current is None or current["state"] not in {"active", "artifact_ready"}:
                raise TodoError("workspace_artifact_state", "Workspace is not eligible to publish an artifact")
            if not conn.execute(
                "SELECT 1 FROM workflow_lane_tasks WHERE lane_id=? AND task_id=?",
                (current["lane_id"], task_id),
            ).fetchone():
                raise TodoError("workspace_artifact_task_mismatch", "Artifact task ownership changed")
            conn.execute(
                """INSERT INTO workflow_patch_artifacts(
                     id,workspace_id,task_id,kind,artifact_ref,content_hash,base_commit,created_at,state)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (artifact_id, workspace_id, task_id, kind, artifact_ref, digest, current["base_commit"], now, "pending"),
            )
            conn.execute(
                """UPDATE workflow_workspaces SET state='artifact_ready',artifact_kind=?,artifact_ref=?,
                   diff_hash=?,updated_at=? WHERE id=?""",
                (kind, artifact_ref, digest, now, workspace_id),
            )
            return {"artifact_id": artifact_id, "workspace_id": workspace_id, "kind": kind, "artifact_ref": artifact_ref, "diff_hash": digest}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_patch_artifact",
            entity_id=artifact_id,
            event_type="workflow_patch_artifact_published",
            payload={"workspace_id": workspace_id, "task_id": task_id, "kind": kind, "content_hash": digest},
            operation=operation,
        )
        result["revision"] = revision
        return result

    def enqueue_artifact(
        self,
        *,
        artifact_id: str,
        integrator_lane_id: str,
        integration_task_id: str,
        actor_session_id: str | None = None,
    ) -> dict[str, object]:
        queue_id = str(uuid.uuid4())
        now = utc_now()

        def operation(conn: Any, revision: int) -> dict[str, object]:
            artifact = conn.execute(
                """SELECT a.*,w.run_id,w.integration_task_id AS declared_task,w.repository_identity
                   FROM workflow_patch_artifacts a JOIN workflow_workspaces w ON w.id=a.workspace_id
                   WHERE a.id=?""",
                (artifact_id,),
            ).fetchone()
            if artifact is None or artifact["state"] != "pending":
                raise TodoError("artifact_not_queueable", "Artifact is missing or is not pending")
            if artifact["declared_task"] != integration_task_id:
                raise TodoError("integration_task_mismatch", "Artifact was not declared for this integration task")
            lane = self._lane(conn, artifact["run_id"], integrator_lane_id)
            if lane["role"] not in {"integrator", "validator"}:
                raise TodoError("integrator_role_required", "Integration queue ownership requires an integrator lane")
            destination = conn.execute(
                """SELECT * FROM workflow_workspaces WHERE run_id=? AND lane_id=?""",
                (artifact["run_id"], integrator_lane_id),
            ).fetchone()
            if destination is None or destination["mode"] != "exclusive":
                raise TodoError("exclusive_integration_workspace_required", "Integrator must exclusively own the destination workspace")
            if destination["repository_identity"] != artifact["repository_identity"]:
                raise TodoError("integration_repository_mismatch", "Producer and destination repositories differ")
            next_owned = conn.execute(
                "SELECT task_id FROM workflow_lane_tasks WHERE lane_id=? AND state='queued' "
                "ORDER BY position LIMIT 1",
                (integrator_lane_id,),
            ).fetchone()
            if next_owned is None:
                raise TodoError("integration_task_owner_mismatch", "Integrator lane has no queued integration task")
            is_next_owned_task = next_owned["task_id"] == integration_task_id
            if (destination["state"] == "integrated" and
                    destination["integration_task_id"] != integration_task_id and
                    is_next_owned_task):
                unfinished = conn.execute(
                    "SELECT 1 FROM workflow_integration_queue WHERE run_id=? AND integrator_lane_id=? "
                    "AND integration_task_id=? AND state NOT IN ('integrated','rejected') LIMIT 1",
                    (artifact["run_id"], integrator_lane_id, integration_task_id),
                ).fetchone()
                destination_path = self._managed_path(Path(str(destination["worktree_path"])))
                status = self._git(destination_path, ["status", "--porcelain=v1", "-z"])
                head = self._git(destination_path, ["rev-parse", "--verify", "HEAD^{commit}"], text=True)
                if (unfinished is not None or status.returncode != 0 or
                        status.stdout or head.returncode != 0 or
                        head.stdout.strip() != artifact["base_commit"]):
                    raise TodoError(
                        "integration_stale_base",
                        "Integrated destination cannot advance safely to the producer base",
                    )
                conn.execute(
                    "UPDATE workflow_workspaces SET base_commit=?,integration_task_id=?,state='active',"
                    "artifact_kind=NULL,artifact_ref=NULL,diff_hash=NULL,merge_result_json='{}',updated_at=? "
                    "WHERE id=?",
                    (artifact["base_commit"], integration_task_id, now, destination["id"]),
                )
                destination = conn.execute(
                    "SELECT * FROM workflow_workspaces WHERE id=?", (destination["id"],)
                ).fetchone()
            if destination["base_commit"] != artifact["base_commit"]:
                raise TodoError("integration_stale_base", "Producer and destination must have the exact same recorded base")
            owns_task = conn.execute(
                "SELECT 1 FROM workflow_lane_tasks WHERE lane_id=? AND task_id=?",
                (integrator_lane_id, integration_task_id),
            ).fetchone()
            if owns_task is None or (
                    destination["integration_task_id"] != integration_task_id and is_next_owned_task):
                raise TodoError("integration_task_owner_mismatch", "Integration task is not declared for the integrator workspace and lane")
            position = conn.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM workflow_integration_queue WHERE run_id=? AND integration_task_id=?",
                (artifact["run_id"], integration_task_id),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO workflow_integration_queue(
                     id,run_id,integration_task_id,integrator_lane_id,patch_artifact_id,position,state,
                     conflict_json,merge_result_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,'{}','{}',?,?)""",
                (queue_id, artifact["run_id"], integration_task_id, integrator_lane_id, artifact_id, position, "queued", now, now),
            )
            conn.execute("UPDATE workflow_patch_artifacts SET state='queued' WHERE id=?", (artifact_id,))
            conn.execute("UPDATE workflow_workspaces SET state='queued',updated_at=? WHERE id=?", (now, artifact["workspace_id"]))
            return {"queue_id": queue_id, "position": position, "run_id": artifact["run_id"], "artifact_id": artifact_id}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_integration_queue",
            entity_id=queue_id,
            event_type="workflow_artifact_queued",
            payload={"artifact_id": artifact_id, "integrator_lane_id": integrator_lane_id, "integration_task_id": integration_task_id},
            operation=operation,
        )
        result["revision"] = revision
        return result

    def publish_completed_wave(
        self,
        *,
        workspace_id: str,
        task_id: str,
        artifact_ref: str,
        integrator_lane_id: str,
        integration_task_id: str,
        actor_session_id: str | None = None,
    ) -> dict[str, object]:
        """Atomically publish and queue one completed serial producer wave."""
        with self.db.read() as conn:
            workspace = conn.execute(
                "SELECT * FROM workflow_workspaces WHERE id=?", (workspace_id,),
            ).fetchone()
            if workspace is None or workspace["mode"] != "isolated_merge":
                raise TodoError("workspace_artifact_forbidden", "An isolated producer workspace is required")
            root = self._managed_path(Path(str(workspace["worktree_path"])))
            base = str(workspace["base_commit"])
        status = self._git(root, ["status", "--porcelain=v1", "-z"])
        if status.returncode != 0 or status.stdout:
            raise TodoError("workspace_uncommitted_changes", "Producer wave publication requires a clean workspace")
        resolved = self._commit(root, artifact_ref)
        if resolved != self._commit(root, "HEAD"):
            raise TodoError("artifact_workspace_head_mismatch", "Commit artifact must be the workspace HEAD")
        if self._git(root, ["merge-base", "--is-ancestor", base, resolved]).returncode:
            raise TodoError("artifact_base_mismatch", "Commit artifact is not based on the workspace base")
        content = self._git_ok(root, ["diff", "--binary", base, resolved], code="artifact_diff_failed")
        digest = _sha256(content)
        artifact_id, queue_id, now = str(uuid.uuid4()), str(uuid.uuid4()), utc_now()

        def operation(conn: Any, revision: int) -> dict[str, object]:
            current = conn.execute(
                "SELECT * FROM workflow_workspaces WHERE id=?", (workspace_id,),
            ).fetchone()
            if current is None or current["state"] != "active" or current["base_commit"] != base:
                raise TodoError("workspace_artifact_state", "Producer wave changed before publication")
            terminal = conn.execute(
                "SELECT 1 FROM workflow_lane_tasks lt JOIN tasks t ON t.id=lt.task_id "
                "WHERE lt.lane_id=? AND lt.task_id=? AND lt.state='completed' AND t.status='done'",
                (current["lane_id"], task_id),
            ).fetchone()
            if terminal is None:
                raise TodoError("workspace_wave_task_not_terminal", "Producer wave task is not terminal")
            if current["integration_task_id"] != integration_task_id:
                raise TodoError("integration_task_mismatch", "Producer wave integration target changed")
            if conn.execute(
                "SELECT 1 FROM workflow_patch_artifacts WHERE workspace_id=? AND task_id=? "
                "AND state NOT IN ('integrated','rejected','superseded')",
                (workspace_id, task_id),
            ).fetchone():
                raise TodoError("workspace_wave_artifact_exists", "Producer wave already has a live artifact")
            lane = self._lane(conn, current["run_id"], integrator_lane_id)
            if lane["role"] not in {"integrator", "validator"}:
                raise TodoError("integrator_role_required", "Integration queue requires an integrator lane")
            destination = conn.execute(
                "SELECT * FROM workflow_workspaces WHERE run_id=? AND lane_id=?",
                (current["run_id"], integrator_lane_id),
            ).fetchone()
            if destination is None or destination["mode"] != "exclusive":
                raise TodoError("exclusive_integration_workspace_required", "Integrator destination is unavailable")
            if destination["repository_identity"] != current["repository_identity"]:
                raise TodoError("integration_repository_mismatch", "Producer and destination repositories differ")
            next_owned = conn.execute(
                "SELECT task_id FROM workflow_lane_tasks WHERE lane_id=? AND state='queued' "
                "ORDER BY position LIMIT 1", (integrator_lane_id,),
            ).fetchone()
            if next_owned is None or next_owned["task_id"] != integration_task_id:
                raise TodoError("integration_task_owner_mismatch", "Integration task is not the next owned task")
            if destination["state"] == "integrated" and destination["integration_task_id"] != integration_task_id:
                unfinished = conn.execute(
                    "SELECT 1 FROM workflow_integration_queue WHERE run_id=? AND integrator_lane_id=? "
                    "AND integration_task_id=? AND state NOT IN ('integrated','rejected') LIMIT 1",
                    (current["run_id"], integrator_lane_id, integration_task_id),
                ).fetchone()
                target = self._managed_path(Path(str(destination["worktree_path"])))
                target_status = self._git(target, ["status", "--porcelain=v1", "-z"])
                target_head = self._git(target, ["rev-parse", "--verify", "HEAD^{commit}"], text=True)
                if (unfinished is not None or target_status.returncode != 0 or target_status.stdout or
                        target_head.returncode != 0 or target_head.stdout.strip() != base):
                    raise TodoError("integration_stale_base", "Integrator destination cannot advance safely")
                conn.execute(
                    "UPDATE workflow_workspaces SET base_commit=?,integration_task_id=?,state='active',"
                    "artifact_kind=NULL,artifact_ref=NULL,diff_hash=NULL,merge_result_json='{}',updated_at=? WHERE id=?",
                    (base, integration_task_id, now, destination["id"]),
                )
                destination = conn.execute(
                    "SELECT * FROM workflow_workspaces WHERE id=?", (destination["id"],),
                ).fetchone()
            if destination["base_commit"] != base or destination["integration_task_id"] != integration_task_id:
                raise TodoError("integration_stale_base", "Producer and destination wave contracts differ")
            position = conn.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM workflow_integration_queue "
                "WHERE run_id=? AND integration_task_id=?",
                (current["run_id"], integration_task_id),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO workflow_patch_artifacts(id,workspace_id,task_id,kind,artifact_ref,content_hash,"
                "base_commit,created_at,state) VALUES(?,?,?,?,?,?,?,?, 'queued')",
                (artifact_id, workspace_id, task_id, "commit", resolved, digest, base, now),
            )
            conn.execute(
                "INSERT INTO workflow_integration_queue(id,run_id,integration_task_id,integrator_lane_id,"
                "patch_artifact_id,position,state,conflict_json,merge_result_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?, 'queued','{}','{}',?,?)",
                (queue_id, current["run_id"], integration_task_id, integrator_lane_id,
                 artifact_id, position, now, now),
            )
            conn.execute(
                "UPDATE workflow_workspaces SET state='queued',artifact_kind='commit',artifact_ref=?,"
                "diff_hash=?,updated_at=? WHERE id=?", (resolved, digest, now, workspace_id),
            )
            return {"artifact_id": artifact_id, "queue_id": queue_id, "position": position,
                    "run_id": current["run_id"], "artifact_ref": resolved, "diff_hash": digest}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_integration_queue",
            entity_id=queue_id,
            event_type="workflow_producer_wave_published",
            payload={"workspace_id": workspace_id, "task_id": task_id,
                     "integration_task_id": integration_task_id},
            operation=operation,
        )
        result["revision"] = revision
        return result

    def apply_next(self, *, queue_id: str, actor_session_id: str | None = None) -> dict[str, object]:
        def reserve(conn: Any, revision: int) -> dict[str, object]:
            row = conn.execute(
                """SELECT q.*,a.kind,a.artifact_ref,a.content_hash,a.base_commit,a.workspace_id,
                          d.worktree_path AS destination_path,d.state AS destination_state,
                          d.integration_task_id AS destination_task,d.base_commit AS destination_base
                   FROM workflow_integration_queue q
                   JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id
                   JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id
                   WHERE q.id=?""",
                (queue_id,),
            ).fetchone()
            if row is None or row["state"] != "queued":
                raise TodoError("integration_not_queueable", "Queue entry is missing or is not queued")
            earlier = conn.execute(
                """SELECT id FROM workflow_integration_queue WHERE run_id=? AND integration_task_id=?
                   AND position<? AND state NOT IN ('integrated','rejected') ORDER BY position LIMIT 1""",
                (row["run_id"], row["integration_task_id"], row["position"]),
            ).fetchone()
            if earlier is not None:
                raise TodoError("integration_queue_order", "An earlier integration entry must finish first")
            next_owned = conn.execute(
                "SELECT task_id FROM workflow_lane_tasks WHERE lane_id=? "
                "AND state IN ('active','queued') ORDER BY position LIMIT 1",
                (row["integrator_lane_id"],),
            ).fetchone()
            if next_owned is None or next_owned["task_id"] != row["integration_task_id"]:
                raise TodoError("integration_task_out_of_order", "A prior integrator-lane task must finish first")
            if row["destination_task"] != row["integration_task_id"]:
                if (row["destination_state"] != "integrated" or
                        row["destination_base"] != row["base_commit"]):
                    raise TodoError("integration_task_owner_mismatch", "Integrator destination is not ready for this task")
                conn.execute(
                    "UPDATE workflow_workspaces SET integration_task_id=?,state='active',updated_at=? "
                    "WHERE run_id=? AND lane_id=?",
                    (row["integration_task_id"], utc_now(), row["run_id"], row["integrator_lane_id"]),
                )
            conn.execute(
                "UPDATE workflow_integration_queue SET state='applying',updated_at=? WHERE id=? AND state='queued'",
                (utc_now(), queue_id),
            )
            conn.execute(
                "UPDATE workflow_workspaces SET state='applying',updated_at=? WHERE run_id=? AND lane_id=?",
                (utc_now(), row["run_id"], row["integrator_lane_id"]),
            )
            return dict(row)

        row, _ = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_integration_queue",
            entity_id=queue_id,
            event_type="workflow_integration_reserved",
            payload={"state": "applying"},
            operation=reserve,
        )
        destination = Path(row["destination_path"])
        try:
            if not destination.exists():
                raise TodoError("integration_workspace_missing", "Destination integration workspace is unavailable")
            if material_dirty_paths(destination):
                raise TodoError("integration_workspace_dirty", "Destination has dirty changes; all files are preserved")
            pre_apply_head = self._commit(destination, "HEAD")
            if row["kind"] == "commit":
                current = self._commit(destination, row["artifact_ref"])
                diff = self._git_ok(destination, ["diff", "--binary", row["base_commit"], current], code="artifact_diff_failed")
                if _sha256(diff) != row["content_hash"]:
                    raise TodoError("artifact_content_changed", "Commit artifact no longer matches its immutable hash")
                material = self._git_ok(
                    destination,
                    integration_diff_args(str(row["base_commit"]), current),
                    code="artifact_material_diff_failed",
                )
                applied = self._git_input(
                    destination,
                    ["apply", "--index", "--3way", "--allow-empty", "-"],
                    material,
                )
            else:
                patch = Path(row["artifact_ref"])
                content = patch.read_bytes() if patch.is_file() else b""
                if _sha256(content) != row["content_hash"]:
                    raise TodoError("artifact_content_changed", "Patch artifact no longer matches its immutable hash")
                command = ["apply", "--index", "--3way", str(patch)]
                applied = self._git(destination, command)
        except Exception as exc:
            code = exc.code if isinstance(exc, TodoError) else "integration_apply_exception"
            def fail_operation(conn: Any, revision: int) -> None:
                conn.execute(
                    "UPDATE workflow_integration_queue SET state='apply_failed',conflict_json=?,updated_at=? WHERE id=? AND state='applying'",
                    (_json({"code": code, "preserved": True}), utc_now(), queue_id),
                )
                conn.execute(
                    "UPDATE workflow_workspaces SET state='apply_failed',updated_at=? WHERE run_id=? AND lane_id=? AND state='applying'",
                    (utc_now(), row["run_id"], row["integrator_lane_id"]),
                )
            self.db.mutate(
                actor_session_id=actor_session_id,
                entity_type="workflow_integration_queue",
                entity_id=queue_id,
                event_type="workflow_integration_apply_failed",
                payload={"code": code, "preserved": True},
                operation=fail_operation,
            )
            raise
        conflicts: list[str] = []
        if applied.returncode != 0:
            unresolved = self._git(destination, ["diff", "--name-only", "--diff-filter=U"])
            conflicts = sorted(filter(None, unresolved.stdout.decode("utf-8", errors="replace").splitlines()))
        now = utc_now()
        state = "awaiting_gates" if applied.returncode == 0 else "conflict"
        source_identity = self._source_identity(destination, row["base_commit"]) if applied.returncode == 0 else None
        conflict = {
            "paths": conflicts,
            "integration_task_id": row["integration_task_id"],
            "preserved": applied.returncode != 0,
        } if applied.returncode != 0 else {}

        def operation(conn: Any, revision: int) -> dict[str, object]:
            current = conn.execute("SELECT state FROM workflow_integration_queue WHERE id=?", (queue_id,)).fetchone()
            if current is None or current["state"] != "applying":
                raise TodoError("integration_state_changed", "Integration queue entry changed while Git was applying")
            merge_result = {
                "returncode": applied.returncode,
                "state": state,
                "apply_revision": revision,
                "pre_apply_head": pre_apply_head,
                "source_identity": source_identity,
                "destination_worktree": str(destination.resolve()),
            }
            conn.execute(
                "UPDATE workflow_integration_queue SET state=?,conflict_json=?,merge_result_json=?,updated_at=? WHERE id=?",
                (state, _json(conflict), _json(merge_result), now, queue_id),
            )
            conn.execute(
                "UPDATE workflow_workspaces SET state=?,merge_result_json=?,updated_at=? WHERE run_id=? AND lane_id=?",
                (state, _json(merge_result), now, row["run_id"], row["integrator_lane_id"]),
            )
            return {"queue_id": queue_id, "state": state, "conflict": conflict, "integration_task_id": row["integration_task_id"]}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_integration_queue",
            entity_id=queue_id,
            event_type="workflow_integration_applied" if applied.returncode == 0 else "workflow_integration_conflict",
            payload={"state": state, "conflict_paths": conflicts},
            operation=operation,
        )
        result["revision"] = revision
        return result

    def retry_conflict(self, *, queue_id: str, actor_session_id: str | None = None) -> dict[str, object]:
        """Restore a preserved cherry-pick conflict to its recorded base for one explicit retry."""

        with self.db.read() as conn:
            row = conn.execute(
                "SELECT q.state,q.position,q.run_id,q.integrator_lane_id,q.merge_result_json,a.base_commit,d.worktree_path "
                "FROM workflow_integration_queue q "
                "JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id "
                "JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id "
                "WHERE q.id=?",
                (queue_id,),
            ).fetchone()
        if row is None or row["state"] != "conflict":
            raise TodoError("integration_conflict_required", "Only a preserved integration conflict can be retried")
        merge_result = json.loads(row["merge_result_json"] or "{}")
        pre_apply_head = str(merge_result.get("pre_apply_head") or (row["base_commit"] if int(row["position"]) == 0 else ""))
        if not pre_apply_head:
            raise TodoError("integration_conflict_provenance_missing", "Conflict retry requires the recorded pre-apply commit")
        destination = Path(row["worktree_path"])
        aborted = self._git(destination, ["cherry-pick", "--abort"])
        if aborted.returncode != 0:
            # Older --no-commit integrations may leave an unmerged index
            # without a sequencer record.  The destination was proven clean
            # immediately before apply, so restoring the recorded pre-apply
            # tree is the only bounded fallback; untracked residue still makes
            # the subsequent cleanliness check fail closed.
            unmerged = self._git_ok(
                destination,
                ["diff", "--name-only", "--diff-filter=U", "-z"],
                code="integration_conflict_paths_failed",
            ).decode("utf-8", errors="strict").split("\0")
            for path in filter(None, unmerged):
                existed = self._git(destination, ["cat-file", "-e", f"{pre_apply_head}:{path}"])
                command = (
                    ["restore", "--source", pre_apply_head, "--staged", "--worktree", "--", path]
                    if existed.returncode == 0
                    else ["rm", "-f", "--", path]
                )
                if self._git(destination, command).returncode != 0:
                    raise TodoError("integration_conflict_abort_failed", "Preserved conflict could not be restored safely")
            restored = self._git(
                destination,
                ["restore", "--source", pre_apply_head, "--staged", "--worktree", "--", "."],
            )
            if restored.returncode != 0:
                raise TodoError("integration_conflict_abort_failed", "Preserved conflict could not be restored safely")
        if material_dirty_paths(destination):
            raise TodoError("integration_conflict_restore_dirty", "Conflict restoration did not produce a clean destination")
        if self._commit(destination, "HEAD") != pre_apply_head:
            raise TodoError("integration_conflict_restore_mismatch", "Conflict restoration did not return to the recorded pre-apply commit")

        def operation(conn: Any, revision: int) -> dict[str, object]:
            current = conn.execute("SELECT state FROM workflow_integration_queue WHERE id=?", (queue_id,)).fetchone()
            if current is None or current["state"] != "conflict":
                raise TodoError("integration_state_changed", "Integration conflict changed during restoration")
            conn.execute(
                "UPDATE workflow_integration_queue SET state='queued',conflict_json='{}',merge_result_json='{}',updated_at=? WHERE id=?",
                (utc_now(), queue_id),
            )
            conn.execute(
                "UPDATE workflow_workspaces SET state='active',merge_result_json='{}',updated_at=? WHERE run_id=? AND lane_id=?",
                (utc_now(), row["run_id"], row["integrator_lane_id"]),
            )
            return {"queue_id": queue_id, "state": "queued", "restored_head": pre_apply_head}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_integration_queue",
            entity_id=queue_id,
            event_type="workflow_integration_conflict_retried",
            payload={"restored_head": pre_apply_head},
            operation=operation,
        )
        result["revision"] = revision
        return result

    def retry_apply_failed(self, *, queue_id: str, actor_session_id: str | None = None) -> dict[str, object]:
        """Requeue a clean destination after a pre-apply dirty-worktree refusal."""

        with self.db.read() as conn:
            row = conn.execute(
                "SELECT q.state,q.run_id,q.integrator_lane_id,q.conflict_json,d.state AS destination_state,"
                "d.worktree_path FROM workflow_integration_queue q "
                "JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id "
                "WHERE q.id=?",
                (queue_id,),
            ).fetchone()
        if row is None or row["state"] != "apply_failed":
            raise TodoError("integration_apply_failure_required", "Only a preserved apply failure can be retried")
        failure = json.loads(row["conflict_json"] or "{}")
        if failure.get("code") != "integration_workspace_dirty":
            raise TodoError(
                "integration_apply_failure_not_retryable",
                "Only a pre-apply dirty-worktree refusal is retryable without owner intervention",
            )
        destination = Path(row["worktree_path"])
        if not destination.is_dir():
            raise TodoError("integration_workspace_missing", "Destination integration workspace is unavailable")
        if material_dirty_paths(destination):
            raise TodoError("integration_workspace_dirty", "Destination remains dirty; all files are preserved")

        def operation(conn: Any, revision: int) -> dict[str, object]:
            current = conn.execute(
                "SELECT q.state,d.state AS destination_state FROM workflow_integration_queue q "
                "JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id "
                "WHERE q.id=?",
                (queue_id,),
            ).fetchone()
            if (
                current is None
                or current["state"] != "apply_failed"
                or current["destination_state"] != "apply_failed"
            ):
                raise TodoError("integration_state_changed", "Apply-failed integration changed during retry")
            now = utc_now()
            conn.execute(
                "UPDATE workflow_integration_queue SET state='queued',conflict_json='{}',updated_at=? WHERE id=?",
                (now, queue_id),
            )
            conn.execute(
                "UPDATE workflow_workspaces SET state='active',merge_result_json='{}',updated_at=? "
                "WHERE run_id=? AND lane_id=?",
                (now, row["run_id"], row["integrator_lane_id"]),
            )
            return {"queue_id": queue_id, "state": "queued", "destination_state": "active"}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_integration_queue",
            entity_id=queue_id,
            event_type="workflow_integration_apply_retried",
            payload={"failure_code": "integration_workspace_dirty"},
            operation=operation,
        )
        result["revision"] = revision
        return result

    def retry_failed_gates(
        self,
        *,
        queue_id: str,
        actor_session_id: str | None = None,
        allow_source_resolution: bool = False,
    ) -> dict[str, object]:
        """Reopen preserved applied source after a corrected gate contract."""

        with self.db.read() as conn:
            row = conn.execute(
                "SELECT q.state,q.run_id,q.integrator_lane_id,q.merge_result_json,a.base_commit,d.worktree_path "
                "FROM workflow_integration_queue q "
                "JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id "
                "JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id "
                "WHERE q.id=?",
                (queue_id,),
            ).fetchone()
        if row is None or row["state"] != "gate_failed":
            raise TodoError("integration_gate_failure_required", "Only a preserved gate failure can be retried")
        merge_result = json.loads(row["merge_result_json"] or "{}")
        previous_source_identity = str(merge_result.get("source_identity") or "")
        destination = Path(row["worktree_path"])
        current_source_identity = self._source_identity(destination, str(row["base_commit"]))
        if not previous_source_identity:
            raise TodoError("integration_apply_provenance_missing", "Gate retry requires preserved source provenance")
        if current_source_identity != previous_source_identity and not allow_source_resolution:
            raise TodoError("integration_source_changed", "Preserved gate-failed source changed before retry")
        source_identity = current_source_identity

        def operation(conn: Any, revision: int) -> dict[str, object]:
            current = conn.execute("SELECT state FROM workflow_integration_queue WHERE id=?", (queue_id,)).fetchone()
            if current is None or current["state"] != "gate_failed":
                raise TodoError("integration_state_changed", "Gate-failed integration changed during retry")
            conn.execute(
                "UPDATE workflow_integration_queue SET state='awaiting_gates',merge_result_json=?,updated_at=? WHERE id=?",
                (_json({
                    "state": "awaiting_gates",
                    "apply_revision": revision,
                    "source_identity": source_identity,
                    "destination_worktree": str(destination.resolve()),
                    "gate_retry": True,
                    "resolved_source": source_identity != previous_source_identity,
                    "previous_source_identity": previous_source_identity,
                }), utc_now(), queue_id),
            )
            conn.execute(
                "UPDATE workflow_workspaces SET state='awaiting_gates',updated_at=? WHERE run_id=? AND lane_id=?",
                (utc_now(), row["run_id"], row["integrator_lane_id"]),
            )
            return {"queue_id": queue_id, "state": "awaiting_gates", "source_identity": source_identity}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_integration_queue",
            entity_id=queue_id,
            event_type="workflow_integration_gates_retried",
            payload={
                "source_identity": source_identity,
                "previous_source_identity": previous_source_identity,
                "resolved_source": source_identity != previous_source_identity,
            },
            operation=operation,
        )
        result["revision"] = revision
        return result

    def record_post_merge_gates(
        self,
        *,
        queue_id: str,
        gate_results: Sequence[dict[str, object]],
        actor_session_id: str | None = None,
    ) -> dict[str, object]:
        if not gate_results:
            raise TodoError("post_merge_gates_required", "At least one post-merge gate result is required")
        authoritative: list[dict[str, object]] = []
        with self.db.read() as conn:
            queue = conn.execute(
                "SELECT q.integration_task_id,q.updated_at,q.state,q.merge_result_json,d.worktree_path,d.base_commit FROM workflow_integration_queue q "
                "JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id WHERE q.id=?",
                (queue_id,),
            ).fetchone()
            if queue is None:
                raise TodoError("integration_queue_missing", "Integration queue entry does not exist")
            if queue["state"] != "awaiting_gates":
                raise TodoError("integration_not_awaiting_gates", "Integration is not awaiting post-merge gates")
            applied = json.loads(queue["merge_result_json"] or "{}")
            apply_revision = int(applied.get("apply_revision", 0))
            source_identity = str(applied.get("source_identity", ""))
            destination_path = str(Path(queue["worktree_path"]).resolve())
            if not apply_revision or not source_identity or applied.get("destination_worktree") != destination_path:
                raise TodoError("integration_apply_provenance_missing", "Integration apply provenance is incomplete")
            if self._source_identity(Path(destination_path), queue["base_commit"]) != source_identity:
                raise TodoError("integration_source_changed", "Destination source changed after integration apply")
            required_gate_ids = {
                row[0] for row in conn.execute(
                    "SELECT id FROM gates WHERE task_id=? AND required=1", (queue["integration_task_id"],)
                )
            }
            supplied_gate_ids = {str(item.get("gate_id", "")) for item in gate_results}
            if not required_gate_ids or supplied_gate_ids != required_gate_ids:
                raise TodoError("integration_gate_coverage_incomplete", "All and only required integration-task gates must be supplied")
            for supplied in gate_results:
                gate_id = str(supplied.get("gate_id", ""))
                evidence_id = str(supplied.get("evidence_id", ""))
                row = conn.execute(
                    "SELECT g.id,g.task_id,g.status,g.valid,g.input_fingerprint,e.id AS evidence_id,e.status AS evidence_status,e.revision,e.created_at,e.metadata_json "
                    "FROM gates g JOIN evidence e ON e.gate_id=g.id WHERE g.id=? AND e.id=?",
                    (gate_id, evidence_id),
                ).fetchone()
                if not row or row["task_id"] != queue["integration_task_id"]:
                    raise TodoError("integration_gate_provenance_invalid", "Post-merge gate evidence is not authoritative for the integration task")
                metadata = json.loads(row["metadata_json"] or "{}")
                if metadata.get("input_fingerprint") != row["input_fingerprint"]:
                    raise TodoError("integration_gate_provenance_stale", "Gate evidence does not match the current gate input fingerprint")
                if (
                    int(metadata.get("started_revision", -1)) < apply_revision
                    or metadata.get("workspace_path") != destination_path
                    or metadata.get("source_identity") != source_identity
                ):
                    raise TodoError(
                        "integration_gate_workspace_mismatch",
                        "Post-merge gate did not start against the applied destination source",
                    )
                authoritative.append({
                    "gate_id": gate_id,
                    "evidence_id": evidence_id,
                    "status": row["status"],
                    "valid": bool(row["valid"]),
                    "evidence_status": row["evidence_status"],
                    "evidence_revision": int(row["revision"]),
                    "input_fingerprint": row["input_fingerprint"],
                    "started_revision": int(metadata["started_revision"]),
                })
        passed = all(item["status"] == "passed" and item["valid"] and item["evidence_status"] == "passed" for item in authoritative)
        now = utc_now()
        target_state = "integrated" if passed else "gate_failed"
        reserved = False

        def reserve_finalization(conn: Any, revision: int) -> dict[str, object]:
            row = conn.execute(
                "SELECT q.state,q.run_id,q.integrator_lane_id,q.integration_task_id,q.merge_result_json "
                "FROM workflow_integration_queue q WHERE q.id=?",
                (queue_id,),
            ).fetchone()
            if row is None or row["state"] != "awaiting_gates":
                raise TodoError("integration_not_awaiting_gates", "Integration is not awaiting post-merge gates")
            current_apply = json.loads(row["merge_result_json"] or "{}")
            if int(current_apply.get("apply_revision", 0)) != apply_revision or current_apply.get("source_identity") != source_identity:
                raise TodoError("integration_apply_provenance_changed", "Integration apply provenance changed before finalization")
            required_gate_ids = {
                item[0] for item in conn.execute(
                    "SELECT id FROM gates WHERE task_id=? AND required=1", (row["integration_task_id"],)
                )
            }
            if required_gate_ids != {str(item["gate_id"]) for item in authoritative}:
                raise TodoError("integration_gate_coverage_changed", "Required integration gates changed during finalization")
            conn.execute(
                "UPDATE workflow_integration_queue SET state='finalizing',updated_at=? WHERE id=? AND state='awaiting_gates'",
                (utc_now(), queue_id),
            )
            conn.execute(
                "UPDATE workflow_workspaces SET state='finalizing',updated_at=? WHERE run_id=? AND lane_id=?",
                (utc_now(), row["run_id"], row["integrator_lane_id"]),
            )
            return {"queue_id": queue_id, "state": "finalizing"}

        self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_integration_queue",
            entity_id=queue_id,
            event_type="workflow_integration_finalization_reserved",
            payload={"source_identity": source_identity, "gate_count": len(authoritative)},
            operation=reserve_finalization,
        )
        reserved = True
        integrated_artifact: dict[str, object] | None = None
        destination = Path(queue["worktree_path"])
        try:
            if self._source_identity(destination, queue["base_commit"]) != source_identity:
                raise TodoError("integration_source_changed", "Destination source changed during finalization")
            if passed:
                content = self._source_diff(
                    destination,
                    str(queue["base_commit"]),
                    code="integration_final_diff_failed",
                )
                digest = _sha256(content)
                if digest != source_identity:
                    raise TodoError("integration_source_changed", "Final artifact does not match the gated destination source")
                artifact_dir = self.managed_root / "artifacts"
                artifact_dir.mkdir(parents=True, exist_ok=True)
                artifact_path = artifact_dir / f"integration-{digest}.patch"
                _write_immutable(artifact_path, content)
                frozen_commit = self._freeze_integration_commit(
                    destination,
                    base_commit=queue["base_commit"],
                    queue_id=queue_id,
                    source_identity=source_identity,
                )
                self._advance_integration_head(destination, frozen_commit)
                integrated_artifact = {
                    "kind": "commit",
                    "ref": frozen_commit,
                    "patch_ref": str(artifact_path),
                    "content_hash": digest,
                }

            def operation(conn: Any, revision: int) -> dict[str, object]:
                row = conn.execute(
                    """SELECT q.*,a.workspace_id,d.id AS destination_workspace_id FROM workflow_integration_queue q
                       JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id
                       JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id
                       WHERE q.id=?""",
                    (queue_id,),
                ).fetchone()
                if row is None or row["state"] != "finalizing":
                    raise TodoError("integration_not_finalizing", "Integration finalization reservation was lost")
                for gate in authoritative:
                    current = conn.execute(
                        "SELECT g.status,g.valid,g.input_fingerprint,e.status,e.metadata_json,e.revision "
                        "FROM gates g JOIN evidence e ON e.gate_id=g.id WHERE g.id=? AND e.id=?",
                        (gate["gate_id"], gate["evidence_id"]),
                    ).fetchone()
                    metadata = json.loads(current["metadata_json"] or "{}") if current else {}
                    if (
                        not current
                        or current["status"] != gate["status"]
                        or bool(current["valid"]) != gate["valid"]
                        or current["status"] != current[3]
                        or int(current["revision"]) != gate["evidence_revision"]
                        or metadata.get("input_fingerprint") != current["input_fingerprint"]
                        or int(metadata.get("started_revision", -1)) < apply_revision
                        or metadata.get("workspace_path") != destination_path
                        or metadata.get("source_identity") != source_identity
                    ):
                        raise TodoError("integration_gate_provenance_stale", "Integration gate provenance changed before finalization")
                if self._source_identity(destination, queue["base_commit"]) != source_identity:
                    raise TodoError("integration_source_changed", "Destination source changed before authoritative finalization")
                if integrated_artifact:
                    frozen = self._git_ok(
                        destination,
                        integration_diff_args(
                            str(queue["base_commit"]),
                            str(integrated_artifact["ref"]),
                        ),
                        code="integration_frozen_commit_missing",
                    )
                    if _sha256(frozen) != source_identity:
                        raise TodoError("integration_frozen_commit_changed", "Frozen integration commit no longer matches gated source")
                # Retain immutable apply provenance even when a failed gate
                # leaves the source preserved for explicit owner adoption into
                # a later declared batch wave.
                merge_result = {
                    "state": target_state,
                    "gates": authoritative,
                    "integrated_artifact": integrated_artifact,
                    "source_identity": source_identity,
                    "apply_revision": apply_revision,
                    "pre_apply_head": applied.get("pre_apply_head"),
                    "destination_worktree": destination_path,
                }
                conn.execute(
                    "UPDATE workflow_integration_queue SET state=?,merge_result_json=?,updated_at=? WHERE id=?",
                    (target_state, _json(merge_result), now, queue_id),
                )
                conn.execute("UPDATE workflow_patch_artifacts SET state=? WHERE id=?", (target_state, row["patch_artifact_id"]))
                conn.execute(
                    "UPDATE workflow_workspaces SET state=?,merge_result_json=?,updated_at=? WHERE id=?",
                    (target_state, _json(merge_result), now, row["workspace_id"]),
                )
                conn.execute(
                    "UPDATE workflow_workspaces SET state=?,merge_result_json=?,updated_at=? WHERE run_id=? AND lane_id=?",
                    (target_state, _json(merge_result), now, row["run_id"], row["integrator_lane_id"]),
                )
                if integrated_artifact:
                    final_artifact_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO workflow_patch_artifacts(id,workspace_id,task_id,kind,artifact_ref,content_hash,base_commit,created_at,state) "
                        "VALUES(?,?,?,?,?,?,?,?, 'integrated')",
                        (final_artifact_id, row["destination_workspace_id"], row["integration_task_id"], integrated_artifact["kind"], integrated_artifact["ref"], integrated_artifact["content_hash"], queue["base_commit"], now),
                    )
                    conn.execute(
                        "UPDATE workflow_workspaces SET artifact_kind=?,artifact_ref=?,diff_hash=? WHERE run_id=? AND lane_id=?",
                        (integrated_artifact["kind"], integrated_artifact["ref"], integrated_artifact["content_hash"], row["run_id"], row["integrator_lane_id"]),
                    )
                return {"queue_id": queue_id, "state": target_state, "gates": authoritative, "integrated_artifact": integrated_artifact}

            result, revision = self.db.mutate(
                actor_session_id=actor_session_id,
                entity_type="workflow_integration_queue",
                entity_id=queue_id,
                event_type="workflow_post_merge_gates_recorded",
                payload={"state": target_state, "gate_count": len(gate_results), "source_identity": source_identity},
                operation=operation,
            )
        except Exception:
            if reserved:
                self.db.mutate(
                    actor_session_id=actor_session_id,
                    entity_type="workflow_integration_queue",
                    entity_id=queue_id,
                    event_type="workflow_integration_finalization_failed",
                    payload={"preserved": True},
                    operation=lambda conn, revision: (
                        conn.execute("UPDATE workflow_integration_queue SET state='finalization_failed',updated_at=? WHERE id=? AND state='finalizing'", (utc_now(), queue_id)),
                        conn.execute("UPDATE workflow_workspaces SET state='finalization_failed',updated_at=? WHERE run_id=(SELECT run_id FROM workflow_integration_queue WHERE id=?) AND lane_id=(SELECT integrator_lane_id FROM workflow_integration_queue WHERE id=?) AND state='finalizing'", (utc_now(), queue_id, queue_id)),
                    ),
                )
            raise
        result["revision"] = revision
        return result

    def retry_finalization_failed(
        self,
        *,
        queue_id: str,
        actor_session_id: str | None = None,
        allow_source_resolution: bool = False,
    ) -> dict[str, object]:
        """Return a preserved finalization failure to authoritative gate validation."""

        with self.db.read() as conn:
            row = conn.execute(
                "SELECT q.state,q.run_id,q.integrator_lane_id,q.merge_result_json,d.worktree_path,d.base_commit "
                "FROM workflow_integration_queue q "
                "JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id "
                "WHERE q.id=?",
                (queue_id,),
            ).fetchone()
        if row is None or row["state"] != "finalization_failed":
            raise TodoError(
                "integration_finalization_failure_required",
                "Only a preserved finalization failure can be retried",
            )
        destination = Path(str(row["worktree_path"]))
        if not destination.is_dir():
            raise TodoError("integration_workspace_missing", "Destination integration workspace is unavailable")
        previous = json.loads(row["merge_result_json"] or "{}")
        previous_source_identity = str(previous.get("source_identity") or "")
        if not previous_source_identity:
            raise TodoError("integration_apply_provenance_missing", "Finalization retry requires source provenance")
        source_identity = self._source_identity(destination, str(row["base_commit"]))
        if source_identity != previous_source_identity and not allow_source_resolution:
            raise TodoError("integration_source_changed", "Preserved finalization source changed before retry")

        def operation(conn: Any, revision: int) -> dict[str, object]:
            current = conn.execute(
                "SELECT state FROM workflow_integration_queue WHERE id=?",
                (queue_id,),
            ).fetchone()
            if current is None or current["state"] != "finalization_failed":
                raise TodoError("integration_state_changed", "Finalization failure changed during retry")
            merge_result = {
                "state": "awaiting_gates",
                "apply_revision": revision,
                "source_identity": source_identity,
                "destination_worktree": str(destination.resolve()),
                "finalization_retry": True,
                "resolved_source": source_identity != previous_source_identity,
                "previous_source_identity": previous_source_identity,
            }
            now = utc_now()
            conn.execute(
                "UPDATE workflow_integration_queue SET state='awaiting_gates',merge_result_json=?,updated_at=? WHERE id=?",
                (_json(merge_result), now, queue_id),
            )
            conn.execute(
                "UPDATE workflow_workspaces SET state='awaiting_gates',updated_at=? WHERE run_id=? AND lane_id=?",
                (now, row["run_id"], row["integrator_lane_id"]),
            )
            return {
                "queue_id": queue_id,
                "state": "awaiting_gates",
                "source_identity": source_identity,
                "resolved_source": source_identity != previous_source_identity,
            }

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_integration_queue",
            entity_id=queue_id,
            event_type="workflow_integration_finalization_retried",
            payload={
                "source_identity": source_identity,
                "previous_source_identity": previous_source_identity,
                "resolved_source": source_identity != previous_source_identity,
            },
            operation=operation,
        )
        result["revision"] = revision
        return result

    def reject_artifact(self, *, artifact_id: str, actor_session_id: str | None = None,
                        resume_producer: bool = False, reason: str = "") -> dict[str, object]:
        """Reject an unconsumed artifact, optionally withdrawing an early handoff.

        The owner-maintenance resume option preserves committed work and lets
        the idle producer finish more serial tasks before publishing again.
        It cannot withdraw an integration that has begun consuming the source.
        """
        now = utc_now()

        def operation(conn: Any, revision: int) -> dict[str, object]:
            row = conn.execute("SELECT * FROM workflow_patch_artifacts WHERE id=?", (artifact_id,)).fetchone()
            if row is None or row["state"] not in {"pending", "queued"}:
                raise TodoError("artifact_not_rejectable", "Artifact is missing or already terminal")
            if resume_producer:
                workspace = conn.execute("SELECT * FROM workflow_workspaces WHERE id=?", (row["workspace_id"],)).fetchone()
                if not reason.strip() or workspace is None or workspace["mode"] != "isolated_merge" or workspace["state"] not in {"artifact_ready", "queued"}:
                    raise TodoError("artifact_withdrawal_invalid", "Withdrawal needs a reason and an unconsumed isolated workspace")
                lane = self._lane(conn, workspace["run_id"], workspace["lane_id"])
                run = conn.execute("SELECT status FROM workflow_runs WHERE id=?", (workspace["run_id"],)).fetchone()
                if run["status"] != "active" or lane["state"] != "ready" or conn.execute(
                    "SELECT 1 FROM workflow_dispatches WHERE lane_id=? AND state IN ('active','revoking')", (lane["id"],),
                ).fetchone() or conn.execute(
                    "SELECT 1 FROM claims c JOIN workflow_lane_tasks t ON c.task_id=t.task_id WHERE t.lane_id=? AND c.state='active'", (lane["id"],),
                ).fetchone() or conn.execute(
                    "SELECT 1 FROM workflow_lane_tasks WHERE lane_id=? AND state='active'", (lane["id"],),
                ).fetchone():
                    raise TodoError("artifact_withdrawal_busy", "Withdrawal requires an idle producer in an active run")
                if not conn.execute("SELECT 1 FROM workflow_lane_tasks WHERE lane_id=? AND state='queued'", (lane["id"],)).fetchone():
                    raise TodoError("artifact_withdrawal_no_remaining_task", "Producer has no remaining serial task")
                if conn.execute(
                    "SELECT 1 FROM workflow_patch_artifacts WHERE workspace_id=? AND id<>? AND state NOT IN ('integrated','rejected')",
                    (workspace["id"], artifact_id),
                ).fetchone() or conn.execute(
                    "SELECT 1 FROM workflow_integration_queue WHERE patch_artifact_id=? AND state<>'queued'", (artifact_id,),
                ).fetchone():
                    raise TodoError("artifact_withdrawal_consumed", "Artifact must be the sole unconsumed handoff")
                target = self._managed_path(Path(workspace["worktree_path"]))
                if self.repository_identity_resolver is None or self.repository_identity_resolver(target) != workspace["repository_identity"]:
                    raise TodoError("repository_identity_mismatch", "Withdrawal worktree identity is not authoritative")
                if row["kind"] != "commit" or material_dirty_paths(target) or self._commit(target, "HEAD") != row["artifact_ref"]:
                    raise TodoError("artifact_withdrawal_source_changed", "Withdrawal must preserve the exact clean published commit")
            conn.execute("UPDATE workflow_patch_artifacts SET state='rejected' WHERE id=?", (artifact_id,))
            target_state = "active" if resume_producer else "rejected"
            conn.execute("UPDATE workflow_workspaces SET state=?,updated_at=? WHERE id=?", (target_state, now, row["workspace_id"]))
            if resume_producer:
                conn.execute("UPDATE workflow_workspaces SET artifact_kind=NULL,artifact_ref=NULL,diff_hash=NULL,merge_result_json='{}' WHERE id=?", (row["workspace_id"],))
            conn.execute(
                "UPDATE workflow_integration_queue SET state='rejected',updated_at=? WHERE patch_artifact_id=? AND state='queued'",
                (now, artifact_id),
            )
            return {"artifact_id": artifact_id, "workspace_id": row["workspace_id"], "state": "rejected", "workspace_state": target_state}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_patch_artifact",
            entity_id=artifact_id,
            event_type="workflow_patch_artifact_withdrawn" if resume_producer else "workflow_patch_artifact_rejected",
            payload={"state": "rejected", "resume_producer": resume_producer, "reason": reason},
            operation=operation,
        )
        result["revision"] = revision
        return result

    def record_contract_split_integration(
        self, *, repository_root: Path, workspace_id: str, integration_task_id: str,
        accepted_commit: str, reason: str, apply: bool = False,
        actor_session_id: str | None = None,
    ) -> dict[str, object]:
        """Record completed contract-split work already merged into canonical main.

        This owner maintenance operation never merges Git, completes tasks,
        supplies gate evidence, or makes cleanup eligible. The run must already
        be completed and the accepted source must pass its recorded final gates.
        """
        from ..evidence import gate_input_fingerprint
        from ..git_state import is_generated_projection

        root = Path(repository_root).resolve()
        if self.repository_identity_resolver is None:
            raise TodoError("repository_identity_resolver_required", "Canonical repository identity is required")
        if not reason.strip():
            raise TodoError("integration_reason_required", "Record the owner integration reason")
        accepted = self._commit(root, accepted_commit)

        def validate(conn: Any) -> dict[str, object]:
            row = conn.execute(
                "SELECT w.*,l.state AS lane_state,r.status AS run_status "
                "FROM workflow_workspaces w JOIN workflow_lanes l ON l.id=w.lane_id "
                "JOIN workflow_runs r ON r.id=w.run_id WHERE w.id=?", (workspace_id,),
            ).fetchone()
            if row is None or row["mode"] != "contract_split" or row["state"] not in {"active", "integrated"}:
                raise TodoError("contract_workspace_required", "Only active or integrated contract_split workspaces qualify")
            if row["run_status"] != "completed" or row["lane_state"] != "closed":
                raise TodoError("contract_run_not_terminal", "Run must be completed and producer lane closed")
            if conn.execute(
                "SELECT 1 FROM workflow_lanes WHERE run_id=? AND state!='closed'", (row["run_id"],),
            ).fetchone():
                raise TodoError("contract_run_not_terminal", "All run lanes must be closed")
            if conn.execute(
                "SELECT 1 FROM workflow_lane_tasks lt JOIN workflow_lanes l ON l.id=lt.lane_id "
                "JOIN tasks t ON t.id=lt.task_id WHERE l.run_id=? AND (lt.state!='completed' OR t.status!='done')",
                (row["run_id"],),
            ).fetchone():
                raise TodoError("contract_tasks_not_complete", "All run tasks must be completed")
            if conn.execute(
                "SELECT 1 FROM workflow_dispatches d JOIN workflow_lanes l ON l.id=d.lane_id WHERE l.run_id=? AND d.state='active'", (row["run_id"],),
            ).fetchone() or conn.execute(
                "SELECT 1 FROM claims c JOIN workflow_lane_tasks lt ON lt.task_id=c.task_id "
                "JOIN workflow_lanes l ON l.id=lt.lane_id WHERE l.run_id=? AND c.state='active'", (row["run_id"],),
            ).fetchone():
                raise TodoError("contract_owner_active", "All run claims and dispatches must be inactive")
            integration = conn.execute(
                "SELECT t.status FROM tasks t JOIN workflow_lane_tasks lt ON lt.task_id=t.id "
                "JOIN workflow_lanes l ON l.id=lt.lane_id WHERE t.id=? AND l.run_id=? "
                "AND l.role='integrator' AND lt.state='completed'", (integration_task_id, row["run_id"]),
            ).fetchone()
            if integration is None or integration["status"] != "done":
                raise TodoError("contract_integration_not_complete", "A completed integrator task in this run is required")
            worktree = Path(str(row["worktree_path"])).resolve()
            if (not worktree.is_dir() or self._managed_path(worktree) != worktree
                    or self.repository_identity_resolver(root) != row["repository_identity"]
                    or self.repository_identity_resolver(worktree) != row["repository_identity"]):
                raise TodoError("contract_repository_mismatch", "Managed worktree and canonical repository must match")
            if material_dirty_paths(root) or material_dirty_paths(worktree):
                raise TodoError("workspace_dirty_preserved", "Dirty work is preserved")
            if self._commit(root, "HEAD") != accepted or self._commit(root, "refs/heads/main") != accepted:
                raise TodoError("contract_main_mismatch", "Accepted commit must equal canonical HEAD and main")
            producer = self._commit(worktree, "HEAD")
            if self._git(root, ["merge-base", "--is-ancestor", producer, accepted]).returncode != 0:
                raise TodoError("contract_not_merged", "Producer HEAD must be an ancestor of accepted main")
            gates = conn.execute("SELECT * FROM gates WHERE task_id=? AND required=1", (integration_task_id,)).fetchall()
            if not gates:
                raise TodoError("contract_gates_required", "Final integration requires recorded executable gates")
            evidence = []
            for gate in gates:
                fingerprint, _ = gate_input_fingerprint(conn, root, json.loads(gate["config_json"]), gate_type=str(gate["type"]))
                found = conn.execute(
                    "SELECT id,metadata_json FROM evidence WHERE gate_id=? AND status='passed' ORDER BY revision DESC LIMIT 1",
                    (gate["id"],),
                ).fetchone()
                if (gate["type"] not in {"command", "test"} or gate["status"] != "passed" or not gate["valid"]
                        or gate["input_fingerprint"] != fingerprint or found is None
                        or json.loads(found["metadata_json"]).get("input_fingerprint") != fingerprint):
                    raise TodoError("contract_gate_stale", "Required executable gate evidence must match accepted main")
                metadata = json.loads(found["metadata_json"])
                if metadata.get("candidate_evidence") or any(
                    not is_generated_projection(str(path))
                    for path in metadata.get("inputs", {}).get("dirty_paths", [])
                ):
                    raise TodoError("contract_gate_stale", "Final evidence must validate committed material source")
                recorded_head = metadata.get("inputs", {}).get("recorded_git_head")
                if not recorded_head or self._source_diff(root, self._commit(root, recorded_head), code="contract_gate_source_unavailable"):
                    raise TodoError("contract_gate_stale", "Accepted material source differs from recorded gate source")
                evidence.append({"gate_id": gate["id"], "evidence_id": found["id"], "input_fingerprint": fingerprint})
            return {"workspace_id": workspace_id, "run_id": row["run_id"], "lane_id": row["lane_id"],
                    "producer_commit": producer, "accepted_commit": accepted, "integration_task_id": integration_task_id,
                    "evidence": evidence, "reason": reason, "previous_state": row["state"],
                    "cleanup_eligible": bool(row["cleanup_eligible"]), "deleted": False}

        with self.db.read() as conn:
            preview = validate(conn)
        if not apply or preview["previous_state"] == "integrated":
            return {**preview, "status": "noop" if preview["previous_state"] == "integrated" else "ready"}

        def operation(conn: Any, revision: int) -> dict[str, object]:
            observed = validate(conn)
            if observed != preview:
                raise TodoError("contract_integration_changed", "Integration observation changed; review a new preview")
            conn.execute(
                "UPDATE workflow_workspaces SET state='integrated',merge_result_json=?,updated_at=? WHERE id=?",
                (_json(observed), utc_now(), workspace_id),
            )
            return {**observed, "status": "integrated"}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id, entity_type="workflow_workspace", entity_id=workspace_id,
            event_type="workflow_contract_split_integration_recorded", payload=preview, operation=operation,
        )
        return {**result, "revision": revision}

    # A wave is deliberately stored in the immutable queue receipts rather than a
    # new table.  That keeps existing databases compatible while making the
    # member list part of the audited integration contract.
    def declare_integration_wave(
        self, *, queue_ids: Sequence[str], actor_session_id: str | None = None
    ) -> dict[str, object]:
        """Freeze the complete ordered member set for one integration task.

        No artifact may be added after this point: application and finalization
        re-check that the declared members are the entire live nonterminal set.
        A first member already in ``gate_failed`` is permitted only so the owner
        can explicitly adopt it with :meth:`adopt_gate_failed_wave_member`.
        """
        supplied = [str(item) for item in queue_ids]
        if not supplied or len(set(supplied)) != len(supplied):
            raise TodoError("integration_wave_members_invalid", "A wave requires unique queue members")
        wave_id = str(uuid.uuid4())

        def operation(conn: Any, revision: int) -> dict[str, object]:
            placeholders = ",".join("?" for _ in supplied)
            rows = conn.execute(
                f"SELECT q.*,a.base_commit,d.worktree_path,d.state AS destination_state "
                f"FROM workflow_integration_queue q JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id "
                f"JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id "
                f"WHERE q.id IN ({placeholders}) ORDER BY q.position", supplied,
            ).fetchall()
            if len(rows) != len(supplied):
                raise TodoError("integration_wave_member_missing", "A declared queue member does not exist")
            first = rows[0]
            scope = (first["run_id"], first["integration_task_id"], first["integrator_lane_id"], first["base_commit"])
            if any((row["run_id"], row["integration_task_id"], row["integrator_lane_id"], row["base_commit"]) != scope for row in rows):
                raise TodoError("integration_wave_scope_mismatch", "Wave members must share one run, task, lane, and base")
            member_ids = [str(row["id"]) for row in rows]
            if set(member_ids) != set(supplied):
                raise TodoError("integration_wave_members_invalid", "Wave members changed while being ordered")
            live = conn.execute(
                "SELECT id,state FROM workflow_integration_queue WHERE run_id=? AND integration_task_id=? "
                "AND state NOT IN ('integrated','rejected') ORDER BY position",
                scope[:2],
            ).fetchall()
            if [str(row["id"]) for row in live] != member_ids:
                raise TodoError("integration_wave_members_incomplete", "A wave must declare every live member in queue order")
            allowed = {"queued", "gate_failed"}
            if any(row["state"] not in allowed for row in rows):
                raise TodoError("integration_wave_member_state", "Only queued members or the first preserved gate failure can be declared")
            failed = [row for row in rows if row["state"] == "gate_failed"]
            if failed and (len(failed) != 1 or failed[0]["id"] != rows[0]["id"]):
                raise TodoError("integration_wave_adoption_required", "Only the first preserved gate failure may join a wave")
            if any(json.loads(row["merge_result_json"] or "{}").get("wave_id") for row in rows):
                raise TodoError("integration_wave_already_declared", "A queue member already belongs to an immutable wave")
            payload = {"wave_id": wave_id, "wave_members": member_ids, "wave_base_commit": scope[3],
                       "wave_declared_revision": revision}
            for row in rows:
                merged = json.loads(row["merge_result_json"] or "{}")
                merged.update(payload)
                conn.execute("UPDATE workflow_integration_queue SET merge_result_json=?,updated_at=? WHERE id=?",
                             (_json(merged), utc_now(), row["id"]))
            return {"wave_id": wave_id, "queue_ids": member_ids, "run_id": scope[0],
                    "integration_task_id": scope[1], "integrator_lane_id": scope[2], "base_commit": scope[3],
                    "adoption_required": bool(failed)}

        result, revision = self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave",
                                          entity_id=wave_id, event_type="workflow_integration_wave_declared",
                                          payload={"queue_ids": supplied}, operation=operation)
        result["revision"] = revision
        return result

    def declare_and_adopt_integration_wave(
        self,
        *,
        queue_ids: Sequence[str],
        legacy_provenance_reason: str | None = None,
        inherited_base: dict[str, str] | None = None,
        actor_session_id: str | None = None,
    ) -> dict[str, object]:
        """Atomically declare a wave and adopt its optional first gate failure.

        This is the root-facing API.  It avoids a stranded immutable wave by
        doing all Git-read provenance checks before one database mutation that
        writes both declaration and adoption receipts.
        """
        supplied = [str(item) for item in queue_ids]
        if not supplied or len(set(supplied)) != len(supplied):
            raise TodoError("integration_wave_members_invalid", "A wave requires unique queue members")
        inherited_receipt: dict[str, str] | None = None
        if inherited_base is not None:
            required_receipt_fields = {"task_id", "completion_commit", "content_hash", "workspace_id"}
            if set(inherited_base) != required_receipt_fields or any(
                not isinstance(inherited_base[field], str) or not inherited_base[field]
                for field in required_receipt_fields
            ):
                raise TodoError("integration_inherited_base_invalid", "Inherited integration base receipt is incomplete")
            inherited_receipt = {field: inherited_base[field] for field in sorted(required_receipt_fields)}
        with self.db.read() as conn:
            placeholders = ",".join("?" for _ in supplied)
            rows = conn.execute(
                f"SELECT q.*,a.kind,a.artifact_ref,a.content_hash,a.base_commit,d.worktree_path,d.base_commit AS destination_base "
                f"FROM workflow_integration_queue q JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id "
                f"JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id "
                f"WHERE q.id IN ({placeholders}) ORDER BY q.position", supplied,
            ).fetchall()
        if len(rows) != len(supplied):
            raise TodoError("integration_wave_member_missing", "A declared queue member does not exist")
        if len({str(row["base_commit"]) for row in rows}) != 1:
            raise TodoError("integration_wave_scope_mismatch", "Wave members must share the exact artifact base")
        if inherited_receipt is not None and {
            str(row["base_commit"]) for row in rows
        } != {inherited_receipt["completion_commit"]}:
            raise TodoError("integration_inherited_base_mismatch", "Wave artifacts do not inherit the accepted completion base")
        if any(json.loads(row["merge_result_json"] or "{}").get("wave_id") for row in rows):
            raise TodoError("integration_wave_already_declared", "A queue member already belongs to an immutable wave")
        failed = [row for row in rows if row["state"] == "gate_failed"]
        legacy = False
        if failed:
            if len(failed) != 1 or failed[0]["id"] != rows[0]["id"]:
                raise TodoError("integration_wave_adoption_required", "Only the first preserved gate failure may join a wave")
            row = failed[0]
            applied = json.loads(row["merge_result_json"] or "{}")
            destination = Path(str(row["worktree_path"]))
            destination_path = str(destination.resolve())
            source_identity, pre_apply = str(applied.get("source_identity") or ""), str(applied.get("pre_apply_head") or "")
            legacy = not pre_apply or applied.get("destination_worktree") != destination_path
            if legacy and not (legacy_provenance_reason and legacy_provenance_reason.strip()):
                raise TodoError("integration_apply_provenance_missing", "Legacy gate failure requires an explicit root-owned adoption reason")
            if (not source_identity or row["destination_base"] != row["base_commit"] or
                    (not legacy and pre_apply != row["base_commit"]) or row["kind"] != "commit"):
                raise TodoError("integration_apply_provenance_missing", "Gate failure lacks auditable first-apply provenance")
            if self._source_identity(destination, str(row["base_commit"])) != source_identity:
                raise TodoError("integration_source_changed", "Preserved gate-failed source changed before adoption")
            artifact = self._commit(destination, str(row["artifact_ref"]))
            diff = self._git_ok(destination, ["diff", "--binary", str(row["base_commit"]), artifact], code="artifact_diff_failed")
            if _sha256(diff) != row["content_hash"]:
                raise TodoError("artifact_content_changed", "Adopted artifact no longer matches its immutable hash")
        wave_id = str(uuid.uuid4())
        def operation(conn: Any, revision: int) -> dict[str, object]:
            placeholders = ",".join("?" for _ in supplied)
            current = conn.execute(f"SELECT * FROM workflow_integration_queue WHERE id IN ({placeholders}) ORDER BY position", supplied).fetchall()
            if (len(current) != len(supplied) or [str(row["id"]) for row in current] != [str(row["id"]) for row in rows] or
                    any(json.loads(row["merge_result_json"] or "{}").get("wave_id") for row in current)):
                raise TodoError("integration_wave_changed", "Wave members changed during adoption")
            first = current[0]
            scope = (first["run_id"], first["integration_task_id"], first["integrator_lane_id"])
            if any((row["run_id"], row["integration_task_id"], row["integrator_lane_id"]) != scope for row in current):
                raise TodoError("integration_wave_scope_mismatch", "Wave members must share one run, task, and lane")
            members = [str(row["id"]) for row in current]
            live = conn.execute("SELECT id FROM workflow_integration_queue WHERE run_id=? AND integration_task_id=? AND state NOT IN ('integrated','rejected') ORDER BY position", scope[:2]).fetchall()
            if [str(row["id"]) for row in live] != members or any(row["state"] not in {"queued", "gate_failed"} for row in current):
                raise TodoError("integration_wave_members_incomplete", "Wave membership or state changed before declaration")
            payload = {"wave_id": wave_id, "wave_members": members, "wave_base_commit": rows[0]["base_commit"], "wave_declared_revision": revision}
            if inherited_receipt is not None:
                payload["inherited_base"] = inherited_receipt
            for queue in current:
                merged = json.loads(queue["merge_result_json"] or "{}"); merged.update(payload)
                if queue["id"] == rows[0]["id"] and failed:
                    source = str(json.loads(queue["merge_result_json"] or "{}").get("source_identity"))
                    merged.update({"wave_adopted_revision": revision, "wave_cumulative_source_identity": source})
                    if legacy:
                        merged.update({"legacy_provenance_adopted_revision": revision, "legacy_provenance_adoption_reason": legacy_provenance_reason.strip()})
                    conn.execute("UPDATE workflow_integration_queue SET state='applied_pending_wave',merge_result_json=?,updated_at=? WHERE id=?", (_json(merged), utc_now(), queue["id"]))
                else:
                    conn.execute("UPDATE workflow_integration_queue SET merge_result_json=?,updated_at=? WHERE id=?", (_json(merged), utc_now(), queue["id"]))
            if failed:
                conn.execute("UPDATE workflow_workspaces SET state='applied_pending_wave',updated_at=? WHERE run_id=? AND lane_id=?", (utc_now(), scope[0], scope[2]))
            return {"wave_id": wave_id, "queue_ids": members, "run_id": scope[0], "integration_task_id": scope[1], "integrator_lane_id": scope[2], "base_commit": rows[0]["base_commit"], "inherited_base": inherited_receipt, "adopted": bool(failed), "legacy_provenance_adopted": legacy}
        result, revision = self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave", entity_id=wave_id,
                                          event_type="workflow_integration_wave_declared_and_adopted", payload={"queue_ids": supplied, "legacy_provenance_adoption_reason": legacy_provenance_reason if legacy else None}, operation=operation)
        result["revision"] = revision
        return result

    def adopt_gate_failed_wave_member(
        self,
        *,
        queue_id: str,
        legacy_provenance_reason: str | None = None,
        actor_session_id: str | None = None,
    ) -> dict[str, object]:
        """Audit-adopt a preserved first apply into its declared wave; never mutates Git."""
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT q.*,a.kind,a.artifact_ref,a.content_hash,a.base_commit,d.worktree_path,d.base_commit AS destination_base "
                "FROM workflow_integration_queue q JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id "
                "JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id WHERE q.id=?", (queue_id,)
            ).fetchone()
        if row is None or row["state"] != "gate_failed":
            raise TodoError("integration_gate_failure_required", "Only a preserved gate failure can be adopted")
        applied = json.loads(row["merge_result_json"] or "{}")
        wave_id, members = str(applied.get("wave_id") or ""), applied.get("wave_members")
        if not wave_id or not isinstance(members, list) or not members or members[0] != queue_id:
            raise TodoError("integration_wave_adoption_invalid", "Gate failure is not the first member of a declared wave")
        destination = Path(str(row["worktree_path"]))
        destination_path = str(destination.resolve())
        source_identity = str(applied.get("source_identity") or "")
        pre_apply_head = str(applied.get("pre_apply_head") or "")
        legacy = not pre_apply_head or applied.get("destination_worktree") != destination_path
        if legacy and not (legacy_provenance_reason and legacy_provenance_reason.strip()):
            raise TodoError(
                "integration_apply_provenance_missing",
                "Legacy gate failure requires an explicit root-owned adoption reason",
            )
        if (not source_identity or row["destination_base"] != row["base_commit"] or
                (not legacy and pre_apply_head != row["base_commit"])):
            raise TodoError("integration_apply_provenance_missing", "Gate failure lacks auditable first-apply provenance")
        if self._source_identity(destination, str(row["base_commit"])) != source_identity:
            raise TodoError("integration_source_changed", "Preserved gate-failed source changed before adoption")
        if row["kind"] != "commit":
            raise TodoError("integration_wave_adoption_artifact_invalid", "Only immutable commit artifacts can be adopted")
        artifact = self._commit(destination, str(row["artifact_ref"]))
        artifact_diff = self._git_ok(destination, ["diff", "--binary", str(row["base_commit"]), artifact], code="artifact_diff_failed")
        if _sha256(artifact_diff) != row["content_hash"]:
            raise TodoError("artifact_content_changed", "Adopted artifact no longer matches its immutable hash")

        def operation(conn: Any, revision: int) -> dict[str, object]:
            current = conn.execute("SELECT state,merge_result_json FROM workflow_integration_queue WHERE id=?", (queue_id,)).fetchone()
            if current is None or current["state"] != "gate_failed":
                raise TodoError("integration_state_changed", "Gate failure changed during adoption")
            merged = json.loads(current["merge_result_json"] or "{}")
            if merged.get("wave_id") != wave_id or merged.get("wave_members") != members:
                raise TodoError("integration_wave_changed", "Declared wave changed before adoption")
            merged.update({"wave_adopted_revision": revision, "wave_cumulative_source_identity": source_identity})
            if legacy:
                # This deliberately records an owner attestation rather than
                # backfilling absent historical facts.  The checks above bind
                # current source, destination/base and immutable artifact.
                merged.update({
                    "legacy_provenance_adopted_revision": revision,
                    "legacy_provenance_adoption_reason": legacy_provenance_reason.strip(),
                })
            conn.execute("UPDATE workflow_integration_queue SET state='applied_pending_wave',merge_result_json=?,updated_at=? WHERE id=?",
                         (_json(merged), utc_now(), queue_id))
            conn.execute("UPDATE workflow_workspaces SET state='applied_pending_wave',updated_at=? WHERE run_id=? AND lane_id=?",
                         (utc_now(), row["run_id"], row["integrator_lane_id"]))
            return {"queue_id": queue_id, "wave_id": wave_id, "state": "applied_pending_wave", "source_identity": source_identity,
                    "legacy_provenance_adopted": legacy}
        result, revision = self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave",
                                          entity_id=wave_id, event_type="workflow_integration_wave_member_adopted",
                                          payload={"queue_id": queue_id, "source_identity": source_identity,
                                                   "legacy_provenance_adopted": legacy,
                                                   "legacy_provenance_adoption_reason": legacy_provenance_reason if legacy else None}, operation=operation)
        result["revision"] = revision
        return result

    def _declared_wave(self, conn: Any, integration_task_id: str, wave_id: str) -> list[Any]:
        rows = conn.execute(
            "SELECT q.*,a.workspace_id,a.kind,a.artifact_ref,a.content_hash,a.base_commit,d.id AS destination_workspace_id,d.worktree_path,d.base_commit AS destination_base "
            "FROM workflow_integration_queue q JOIN workflow_patch_artifacts a ON a.id=q.patch_artifact_id "
            "JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id "
            "WHERE q.integration_task_id=? ORDER BY q.position", (integration_task_id,)
        ).fetchall()
        wave = [row for row in rows if json.loads(row["merge_result_json"] or "{}").get("wave_id") == wave_id]
        if not wave:
            raise TodoError("integration_wave_missing", "Declared integration wave does not exist")
        metadata = json.loads(wave[0]["merge_result_json"] or "{}")
        members = metadata.get("wave_members")
        if (not isinstance(members, list) or [str(row["id"]) for row in wave] != members or
                any(json.loads(row["merge_result_json"] or "{}").get("wave_members") != members for row in wave)):
            raise TodoError("integration_wave_changed", "Declared wave membership is inconsistent")
        scope = (wave[0]["run_id"], wave[0]["integrator_lane_id"], wave[0]["base_commit"])
        if any((row["run_id"], row["integrator_lane_id"], row["base_commit"]) != scope for row in wave):
            raise TodoError("integration_wave_scope_mismatch", "Declared wave scope changed")
        inherited = metadata.get("inherited_base")
        if inherited is not None:
            if (
                not isinstance(inherited, dict)
                or str(inherited.get("completion_commit") or "") != str(scope[2])
                or any(json.loads(row["merge_result_json"] or "{}").get("inherited_base") != inherited for row in wave)
            ):
                raise TodoError("integration_inherited_base_changed", "Inherited integration base receipt is inconsistent")
        live = [row for row in rows if row["state"] not in {"integrated", "rejected"}]
        if [str(row["id"]) for row in live] != members:
            raise TodoError("integration_wave_members_changed", "A late or missing integration member invalidated the wave")
        return wave

    def apply_declared_integration_wave(self, *, integration_task_id: str, wave_id: str,
                                        actor_session_id: str | None = None) -> dict[str, object]:
        """Apply every remaining declared member in order, retaining one cumulative source."""
        with self.db.read() as conn:
            rows = self._declared_wave(conn, integration_task_id, wave_id)
            destination = Path(str(rows[0]["worktree_path"]))
            base = str(rows[0]["base_commit"])
            applied_rows = [row for row in rows if row["state"] == "applied_pending_wave"]
            pending = [row for row in rows if row["state"] == "queued"]
            if any(row["state"] not in {"queued", "applied_pending_wave"} for row in rows):
                raise TodoError("integration_wave_member_state", "Wave contains a member that cannot be applied")
            if applied_rows:
                current_identity = self._source_identity(destination, base)
                expected = json.loads(applied_rows[-1]["merge_result_json"] or "{}").get("wave_cumulative_source_identity")
                if current_identity != expected:
                    raise TodoError("integration_source_changed", "Preserved wave source changed before continued apply")
            elif material_dirty_paths(destination):
                raise TodoError("integration_workspace_dirty", "Destination has dirty changes; all files are preserved")
        for row in pending:
            def reserve(conn: Any, revision: int, queue_id=str(row["id"])) -> None:
                fresh = self._declared_wave(conn, integration_task_id, wave_id)
                current = next((item for item in fresh if item["id"] == queue_id), None)
                if current is None or current["state"] != "queued":
                    raise TodoError("integration_state_changed", "Wave member changed during application")
                conn.execute("UPDATE workflow_integration_queue SET state='applying',updated_at=? WHERE id=?", (utc_now(), queue_id))
                conn.execute("UPDATE workflow_workspaces SET state='applying',updated_at=? WHERE run_id=? AND lane_id=?",
                             (utc_now(), current["run_id"], current["integrator_lane_id"]))
            self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave", entity_id=wave_id,
                           event_type="workflow_integration_wave_member_reserved", payload={"queue_id": row["id"]}, operation=reserve)
            try:
                artifact = self._commit(destination, str(row["artifact_ref"]))
                diff = self._git_ok(destination, ["diff", "--binary", base, artifact], code="artifact_diff_failed")
                if _sha256(diff) != row["content_hash"]:
                    raise TodoError("artifact_content_changed", "Commit artifact no longer matches its immutable hash")
                material = self._git_ok(destination, integration_diff_args(base, artifact), code="artifact_material_diff_failed")
                result = self._git_input(destination, ["apply", "--index", "--3way", "--allow-empty", "-"], material)
                if result.returncode != 0:
                    raise TodoError("integration_wave_apply_conflict", "Wave apply conflicted; destination is preserved")
                identity = self._source_identity(destination, base)
            except Exception as exc:
                failure_code = exc.code if isinstance(exc, TodoError) else "integration_wave_apply_exception"
                self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave", entity_id=wave_id,
                               event_type="workflow_integration_wave_apply_failed", payload={"queue_id": row["id"], "code": failure_code, "preserved": True},
                               operation=lambda conn, revision, q=str(row["id"]): (
                                   conn.execute("UPDATE workflow_integration_queue SET state='apply_failed',conflict_json=?,updated_at=? WHERE id=? AND state='applying'", (_json({"code": failure_code, "preserved": True}), utc_now(), q)),
                                   conn.execute("UPDATE workflow_workspaces SET state='apply_failed',updated_at=? WHERE run_id=? AND lane_id=?", (utc_now(), row["run_id"], row["integrator_lane_id"]))))
                raise
            def applied(conn: Any, revision: int, queue_id=str(row["id"]), source_identity=identity) -> None:
                current = conn.execute("SELECT state,merge_result_json FROM workflow_integration_queue WHERE id=?", (queue_id,)).fetchone()
                if current is None or current["state"] != "applying":
                    raise TodoError("integration_state_changed", "Wave member changed after Git apply")
                merged = json.loads(current["merge_result_json"] or "{}")
                merged.update({"apply_revision": revision, "pre_apply_head": base, "destination_worktree": str(destination.resolve()),
                               "wave_cumulative_source_identity": source_identity})
                conn.execute("UPDATE workflow_integration_queue SET state='applied_pending_wave',merge_result_json=?,updated_at=? WHERE id=?",
                             (_json(merged), utc_now(), queue_id))
                conn.execute("UPDATE workflow_workspaces SET state='applied_pending_wave',merge_result_json=?,updated_at=? WHERE run_id=? AND lane_id=?",
                             (_json(merged), utc_now(), row["run_id"], row["integrator_lane_id"]))
            self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave", entity_id=wave_id,
                           event_type="workflow_integration_wave_member_applied", payload={"queue_id": row["id"], "source_identity": identity}, operation=applied)
        identity = self._source_identity(destination, base)
        return {"wave_id": wave_id, "integration_task_id": integration_task_id, "queue_ids": [str(row["id"]) for row in rows],
                "state": "applied_pending_wave", "source_identity": identity}

    def retry_declared_integration_wave_apply(
        self, *, integration_task_id: str, wave_id: str, actor_session_id: str | None = None
    ) -> dict[str, object]:
        """Requeue one clean failed wave apply without restoring or changing Git.

        A conflict or any other mutation residue remains preserved and must be
        handled by explicit recovery; this retry only covers a refusal whose
        destination still equals the last accepted cumulative source.
        """
        with self.db.read() as conn:
            rows = self._declared_wave(conn, integration_task_id, wave_id)
            failed = [row for row in rows if row["state"] == "apply_failed"]
            if len(failed) != 1 or any(row["state"] not in {"applied_pending_wave", "apply_failed", "queued"} for row in rows):
                raise TodoError("integration_wave_apply_failure_required", "Wave must contain exactly one retryable apply failure")
            failed_row = failed[0]
            if any(row["state"] == "queued" and row["position"] < failed_row["position"] for row in rows):
                raise TodoError("integration_wave_order", "An earlier wave member has not been applied")
            destination, base = Path(str(failed_row["worktree_path"])), str(failed_row["base_commit"])
            expected = self._source_identity(destination, base)
            applied = [row for row in rows if row["state"] == "applied_pending_wave"]
            expected_prior = json.loads(applied[-1]["merge_result_json"] or "{}").get("wave_cumulative_source_identity") if applied else expected
            if material_dirty_paths(destination) or self._source_identity(destination, base) != expected_prior:
                raise TodoError("integration_wave_apply_retry_unsafe", "Preserved destination is not a clean prior wave source")
        def operation(conn: Any, revision: int) -> dict[str, object]:
            fresh = self._declared_wave(conn, integration_task_id, wave_id)
            current = next((row for row in fresh if row["id"] == failed_row["id"]), None)
            if current is None or current["state"] != "apply_failed":
                raise TodoError("integration_state_changed", "Wave apply failure changed during retry")
            conn.execute("UPDATE workflow_integration_queue SET state='queued',conflict_json='{}',updated_at=? WHERE id=?", (utc_now(), failed_row["id"]))
            conn.execute("UPDATE workflow_workspaces SET state=?,updated_at=? WHERE run_id=? AND lane_id=?",
                         ("applied_pending_wave" if applied else "active", utc_now(), failed_row["run_id"], failed_row["integrator_lane_id"]))
            return {"wave_id": wave_id, "queue_id": failed_row["id"], "state": "queued", "source_identity": expected_prior}
        result, revision = self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave", entity_id=wave_id,
                                          event_type="workflow_integration_wave_apply_retried", payload={"queue_id": failed_row["id"]}, operation=operation)
        result["revision"] = revision
        return result

    def record_integration_wave_gates(self, *, integration_task_id: str, wave_id: str,
                                       gate_results: Sequence[dict[str, object]], actor_session_id: str | None = None) -> dict[str, object]:
        """Bind one gate run to the cumulative wave source and finalize all members atomically."""
        if not gate_results:
            raise TodoError("post_merge_gates_required", "At least one post-merge gate result is required")
        with self.db.read() as conn:
            rows = self._declared_wave(conn, integration_task_id, wave_id)
            if any(row["state"] != "applied_pending_wave" for row in rows):
                raise TodoError("integration_wave_not_applied", "Every declared wave member must be applied before gates")
            destination, base = Path(str(rows[0]["worktree_path"])), str(rows[0]["base_commit"])
            identity = self._source_identity(destination, base)
            last_apply_revision = max(
                int(json.loads(row["merge_result_json"] or "{}").get("apply_revision") or
                    json.loads(row["merge_result_json"] or "{}").get("wave_adopted_revision") or 0)
                for row in rows
            )
            required = {row[0] for row in conn.execute("SELECT id FROM gates WHERE task_id=? AND required=1", (integration_task_id,))}
            supplied = {str(item.get("gate_id", "")) for item in gate_results}
            if not required or supplied != required:
                raise TodoError("integration_gate_coverage_incomplete", "All and only required integration-task gates must be supplied")
            authoritative: list[dict[str, object]] = []
            for supplied_gate in gate_results:
                gate_id, evidence_id = str(supplied_gate.get("gate_id", "")), str(supplied_gate.get("evidence_id", ""))
                record = conn.execute("SELECT g.status,g.valid,g.input_fingerprint,e.status,e.revision,e.metadata_json FROM gates g JOIN evidence e ON e.gate_id=g.id WHERE g.id=? AND e.id=?", (gate_id, evidence_id)).fetchone()
                metadata = json.loads(record["metadata_json"] or "{}") if record else {}
                if (not record or record["status"] != "passed" or not record["valid"] or record[3] != "passed" or
                        metadata.get("input_fingerprint") != record["input_fingerprint"] or metadata.get("workspace_path") != str(destination.resolve()) or
                        metadata.get("source_identity") != identity or int(metadata.get("started_revision", -1)) < last_apply_revision):
                    raise TodoError("integration_gate_workspace_mismatch", "Gate evidence does not bind the cumulative wave source")
                authoritative.append({"gate_id": gate_id, "evidence_id": evidence_id, "status": record["status"], "valid": bool(record["valid"]), "evidence_revision": int(record["revision"]), "input_fingerprint": record["input_fingerprint"]})
        def reserve(conn: Any, revision: int) -> None:
            fresh = self._declared_wave(conn, integration_task_id, wave_id)
            if any(row["state"] != "applied_pending_wave" for row in fresh):
                raise TodoError("integration_state_changed", "Wave changed before finalization")
            placeholders = ",".join("?" for _ in fresh)
            conn.execute("UPDATE workflow_integration_queue SET state='finalizing',updated_at=? WHERE id IN (%s)" % placeholders, [utc_now(), *[row["id"] for row in fresh]])
            conn.execute("UPDATE workflow_workspaces SET state='finalizing',updated_at=? WHERE run_id=? AND lane_id=?", (utc_now(), rows[0]["run_id"], rows[0]["integrator_lane_id"]))
        self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave", entity_id=wave_id,
                       event_type="workflow_integration_wave_finalization_reserved", payload={"source_identity": identity, "gate_count": len(authoritative), "last_apply_revision": last_apply_revision}, operation=reserve)
        try:
            content = self._source_diff(destination, base, code="integration_final_diff_failed")
            if _sha256(content) != identity:
                raise TodoError("integration_source_changed", "Final artifact does not match gated wave source")
            artifact_path = self.managed_root / "artifacts" / f"integration-{identity}.patch"
            _write_immutable(artifact_path, content)
            frozen = self._freeze_integration_commit(destination, base_commit=base, queue_id=wave_id, source_identity=identity)
            self._advance_integration_head(destination, frozen)
        except Exception:
            self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave", entity_id=wave_id,
                           event_type="workflow_integration_wave_finalization_failed", payload={"preserved": True},
                           operation=lambda conn, revision: (
                               conn.execute("UPDATE workflow_integration_queue SET state='finalization_failed',updated_at=? WHERE integration_task_id=? AND state='finalizing'", (utc_now(), integration_task_id)),
                               conn.execute("UPDATE workflow_workspaces SET state='finalization_failed',updated_at=? WHERE run_id=? AND lane_id=?", (utc_now(), rows[0]["run_id"], rows[0]["integrator_lane_id"]))))
            raise
        def finalize(conn: Any, revision: int) -> dict[str, object]:
            fresh = self._declared_wave(conn, integration_task_id, wave_id)
            if any(row["state"] != "finalizing" for row in fresh):
                raise TodoError("integration_not_finalizing", "Wave finalization reservation was lost")
            for gate in authoritative:
                current = conn.execute(
                    "SELECT g.status,g.valid,g.input_fingerprint,e.status,e.metadata_json,e.revision "
                    "FROM gates g JOIN evidence e ON e.gate_id=g.id WHERE g.id=? AND e.id=?",
                    (gate["gate_id"], gate["evidence_id"]),
                ).fetchone()
                metadata = json.loads(current["metadata_json"] or "{}") if current else {}
                if (
                    not current
                    or current["status"] != gate["status"]
                    or bool(current["valid"]) != gate["valid"]
                    or current["status"] != current[3]
                    or int(current["revision"]) != gate["evidence_revision"]
                    or metadata.get("input_fingerprint") != current["input_fingerprint"]
                    or int(metadata.get("started_revision", -1)) < last_apply_revision
                    or metadata.get("workspace_path") != str(destination.resolve())
                    or metadata.get("source_identity") != identity
                ):
                    raise TodoError(
                        "integration_gate_provenance_stale",
                        "Integration-wave gate provenance changed before finalization",
                    )
            if self._source_identity(destination, base) != identity:
                raise TodoError("integration_source_changed", "Destination source changed before authoritative wave finalization")
            frozen_diff = self._git_ok(
                destination,
                integration_diff_args(base, frozen),
                code="integration_frozen_commit_missing",
            )
            if _sha256(frozen_diff) != identity:
                raise TodoError("integration_frozen_commit_changed", "Frozen integration-wave commit no longer matches gated source")
            result = {"state": "integrated", "wave_id": wave_id, "wave_members": [str(row["id"]) for row in fresh],
                      "gates": authoritative, "source_identity": identity,
                      "integrated_artifact": {"kind": "commit", "ref": frozen, "patch_ref": str(artifact_path), "content_hash": identity}}
            for row in fresh:
                conn.execute("UPDATE workflow_integration_queue SET state='integrated',merge_result_json=?,updated_at=? WHERE id=?", (_json(result), utc_now(), row["id"]))
                conn.execute("UPDATE workflow_patch_artifacts SET state='integrated' WHERE id=?", (row["patch_artifact_id"],))
                conn.execute("UPDATE workflow_workspaces SET state='integrated',merge_result_json=?,updated_at=? WHERE id=?", (_json(result), utc_now(), row["workspace_id"]))
            conn.execute("UPDATE workflow_workspaces SET state='integrated',artifact_kind='commit',artifact_ref=?,diff_hash=?,merge_result_json=?,updated_at=? WHERE run_id=? AND lane_id=?", (frozen, identity, _json(result), utc_now(), fresh[0]["run_id"], fresh[0]["integrator_lane_id"]))
            return {"wave_id": wave_id, "queue_ids": result["wave_members"], "state": "integrated", "gates": authoritative, "integrated_artifact": result["integrated_artifact"]}
        try:
            result, revision = self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave", entity_id=wave_id,
                                              event_type="workflow_integration_wave_gates_recorded", payload={"source_identity": identity, "gate_count": len(authoritative)}, operation=finalize)
        except Exception:
            # Git may already hold the frozen commit.  Never strand a semantic
            # ``finalizing`` reservation: retain that Git state and make the
            # database retry path explicit and auditable.
            self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave", entity_id=wave_id,
                           event_type="workflow_integration_wave_finalization_failed", payload={"preserved": True, "after_frozen_head": True},
                           operation=lambda conn, revision: (
                               conn.execute("UPDATE workflow_integration_queue SET state='finalization_failed',updated_at=? WHERE integration_task_id=? AND state='finalizing'", (utc_now(), integration_task_id)),
                               conn.execute("UPDATE workflow_workspaces SET state='finalization_failed',updated_at=? WHERE run_id=? AND lane_id=? AND state='finalizing'", (utc_now(), rows[0]["run_id"], rows[0]["integrator_lane_id"]))))
            raise
        result["revision"] = revision
        return result

    def retry_declared_integration_wave_finalization(
        self, *, integration_task_id: str, wave_id: str, actor_session_id: str | None = None
    ) -> dict[str, object]:
        """Return a preserved failed wave finalization to its gated apply state.

        This operation is database-only. It verifies the destination still
        represents the exact cumulative source; a subsequent fresh gate call
        performs any Git freezing work.
        """
        with self.db.read() as conn:
            rows = self._declared_wave(conn, integration_task_id, wave_id)
            if any(row["state"] != "finalization_failed" for row in rows):
                raise TodoError("integration_wave_finalization_failure_required", "All wave members must be in preserved finalization failure")
            destination, base = Path(str(rows[0]["worktree_path"])), str(rows[0]["base_commit"])
            source_identity = self._source_identity(destination, base)
            expected = json.loads(rows[-1]["merge_result_json"] or "{}").get("wave_cumulative_source_identity")
            if source_identity != expected:
                raise TodoError("integration_source_changed", "Finalization-failed wave source changed before retry")
        def operation(conn: Any, revision: int) -> dict[str, object]:
            fresh = self._declared_wave(conn, integration_task_id, wave_id)
            if any(row["state"] != "finalization_failed" for row in fresh):
                raise TodoError("integration_state_changed", "Wave finalization failure changed during retry")
            conn.execute("UPDATE workflow_integration_queue SET state='applied_pending_wave',updated_at=? WHERE integration_task_id=? AND state='finalization_failed'", (utc_now(), integration_task_id))
            conn.execute("UPDATE workflow_workspaces SET state='applied_pending_wave',updated_at=? WHERE run_id=? AND lane_id=?", (utc_now(), rows[0]["run_id"], rows[0]["integrator_lane_id"]))
            return {"wave_id": wave_id, "state": "applied_pending_wave", "source_identity": source_identity}
        result, revision = self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave", entity_id=wave_id,
                                          event_type="workflow_integration_wave_finalization_retried", payload={"source_identity": source_identity}, operation=operation)
        result["revision"] = revision
        return result

    def recover_interrupted_integration_wave_finalization(
        self,
        *,
        integration_task_id: str,
        wave_id: str,
        reason: str,
        actor_session_id: str | None = None,
    ) -> dict[str, object]:
        """Owner recovery for a process death after wave finalization reservation.

        It deliberately performs no Git operation.  The destination may still
        be staged at the pre-freeze source or already have its frozen HEAD; in
        either case the material source must exactly match the recorded wave.
        """
        if not reason.strip():
            raise TodoError("integration_wave_recovery_reason_required", "Interrupted finalization recovery requires a reason")
        with self.db.read() as conn:
            rows = self._declared_wave(conn, integration_task_id, wave_id)
            if any(row["state"] != "finalizing" for row in rows):
                raise TodoError("integration_wave_not_finalizing", "All wave members must be finalizing for interruption recovery")
            destination, base = Path(str(rows[0]["worktree_path"])), str(rows[0]["base_commit"])
            recorded = json.loads(rows[-1]["merge_result_json"] or "{}").get("wave_cumulative_source_identity")
            if not recorded or self._source_identity(destination, base) != recorded:
                raise TodoError("integration_source_changed", "Interrupted finalization source differs from its recorded cumulative wave")
        def operation(conn: Any, revision: int) -> dict[str, object]:
            fresh = self._declared_wave(conn, integration_task_id, wave_id)
            if any(row["state"] != "finalizing" for row in fresh):
                raise TodoError("integration_state_changed", "Wave finalization changed during interruption recovery")
            for row in fresh:
                merged = json.loads(row["merge_result_json"] or "{}")
                merged.update({"interrupted_finalization_recovered_revision": revision,
                               "interrupted_finalization_recovery_reason": reason.strip()})
                conn.execute("UPDATE workflow_integration_queue SET state='finalization_failed',merge_result_json=?,updated_at=? WHERE id=?",
                             (_json(merged), utc_now(), row["id"]))
            conn.execute("UPDATE workflow_workspaces SET state='finalization_failed',updated_at=? WHERE run_id=? AND lane_id=? AND state='finalizing'",
                         (utc_now(), fresh[0]["run_id"], fresh[0]["integrator_lane_id"]))
            return {"wave_id": wave_id, "state": "finalization_failed", "queue_ids": [str(row["id"]) for row in fresh],
                    "source_identity": recorded, "reason": reason.strip()}
        result, revision = self.db.mutate(actor_session_id=actor_session_id, entity_type="workflow_integration_wave", entity_id=wave_id,
                                          event_type="workflow_integration_wave_interruption_recovered", payload={"reason": reason.strip(), "source_identity": recorded}, operation=operation)
        result["revision"] = revision
        return result

    def mark_cleanup_eligible(self, *, workspace_id: str, actor_session_id: str | None = None) -> dict[str, object]:
        with self.db.read() as conn:
            row = conn.execute("SELECT state,worktree_path FROM workflow_workspaces WHERE id=?", (workspace_id,)).fetchone()
            if row is None:
                raise TodoError("workspace_missing", "Workspace does not exist")
            if row["state"] not in {"integrated", "rejected"}:
                raise TodoError("workspace_cleanup_not_terminal", "Only integrated or explicitly rejected work can become cleanup eligible")
            path = Path(row["worktree_path"]) if row["worktree_path"] else None
        if path is not None:
            if not path.exists():
                raise TodoError("workspace_missing", "Workspace path is unavailable; no cleanup state was changed")
            if material_dirty_paths(path):
                raise TodoError("workspace_dirty_preserved", "Dirty or conflicted workspace is preserved and cannot become cleanup eligible")
        now = utc_now()

        def operation(conn: Any, revision: int) -> dict[str, object]:
            current = conn.execute("SELECT state,cleanup_eligible FROM workflow_workspaces WHERE id=?", (workspace_id,)).fetchone()
            if current is None or current["state"] not in {"integrated", "rejected"}:
                raise TodoError("workspace_cleanup_state_changed", "Workspace state changed during cleanup assessment")
            if conn.execute(
                "SELECT 1 FROM workflow_dispatches WHERE workspace_id=? AND state='active'", (workspace_id,)
            ).fetchone():
                raise TodoError("workspace_cleanup_owner_active", "Active workspace owner must stop before cleanup eligibility")
            if path is not None and material_dirty_paths(path):
                raise TodoError("workspace_dirty_preserved", "Workspace became dirty during cleanup assessment")
            conn.execute("UPDATE workflow_workspaces SET cleanup_eligible=1,updated_at=? WHERE id=?", (now, workspace_id))
            return {"workspace_id": workspace_id, "cleanup_eligible": True, "deleted": False}

        result, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_workspace",
            entity_id=workspace_id,
            event_type="workflow_workspace_cleanup_eligible",
            payload={"cleanup_eligible": True, "deleted": False},
            operation=operation,
        )
        result["revision"] = revision
        return result
