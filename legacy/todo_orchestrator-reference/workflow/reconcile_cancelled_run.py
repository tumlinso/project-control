"""Owner reconciliation for already-cancelled, fully quiescent workflow runs.

This is a deliberately narrow source-inclusion repair. It never changes task,
gate, artifact, or run history. Every selected managed worktree must be clean
and its recorded HEAD must already be an ancestor of the accepted main commit.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..config import utc_now
from ..models import ExitCode, TodoError
from ..retirement import TERMINAL, TERMINAL_LANE_TASK_STATES
from .foundation import WorkflowDatabase


_WORKSPACE_TERMINAL = frozenset({"integrated", "rejected"})
_LANE_TERMINAL = frozenset({"closed", "cancelled"})
_ARTIFACT_TERMINAL = frozenset({"integrated", "rejected", "superseded"})
_QUEUE_TERMINAL = frozenset({"integrated", "rejected"})
_HASH = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


def _error(code: str, message: str, details: object = None) -> TodoError:
    return TodoError(code, message, ExitCode.BLOCKED, details)


def _normalise_request(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {"run_id", "expected_revision", "accepted_main_commit", "workspaces", "reason"}
    if set(value) != required:
        raise TodoError(
            "cancelled_run_reconciliation_request_invalid",
            "request fields must be exactly run_id, expected_revision, accepted_main_commit, workspaces, and reason",
            details={"missing": sorted(required - set(value)), "unknown": sorted(set(value) - required)},
        )
    run_id = value["run_id"]
    accepted = value["accepted_main_commit"]
    reason = value["reason"]
    revision = value["expected_revision"]
    workspaces = value["workspaces"]
    if not isinstance(run_id, str) or not run_id.strip():
        raise TodoError("cancelled_run_reconciliation_request_invalid", "run_id must be non-empty text")
    if type(revision) is not int or revision < 0:
        raise TodoError("cancelled_run_reconciliation_request_invalid", "expected_revision must be a non-negative integer")
    if not isinstance(accepted, str) or not _HASH.fullmatch(accepted):
        raise TodoError("cancelled_run_reconciliation_request_invalid", "accepted_main_commit must be a full Git commit id")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 2000:
        raise TodoError("cancelled_run_reconciliation_request_invalid", "reason must be non-empty text of at most 2000 characters")
    if not isinstance(workspaces, dict) or not workspaces:
        raise TodoError("cancelled_run_reconciliation_request_invalid", "workspaces must be a non-empty exact workspace_id to HEAD map")
    normalized_workspaces: dict[str, str] = {}
    for workspace_id, head in workspaces.items():
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise TodoError("cancelled_run_reconciliation_request_invalid", "workspace ids must be non-empty text")
        if not isinstance(head, str) or not _HASH.fullmatch(head):
            raise TodoError("cancelled_run_reconciliation_request_invalid", f"workspace {workspace_id} requires a full Git HEAD id")
        normalized_workspaces[workspace_id] = head.lower()
    return {
        "run_id": run_id.strip(),
        "expected_revision": revision,
        "accepted_main_commit": accepted.lower(),
        "workspaces": normalized_workspaces,
        "reason": reason.strip(),
    }


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise _error("cancelled_run_reconciliation_git_unavailable", "Git validation could not run") from exc


def _git_value(path: Path, *args: str, code: str) -> str:
    result = _git(path, *args)
    if result.returncode != 0:
        raise _error(code, "Git could not verify the requested repository state", {"returncode": result.returncode})
    return result.stdout.decode("utf-8", errors="replace").strip()


def _common_dir(path: Path) -> Path:
    raw = Path(_git_value(path, "rev-parse", "--git-common-dir", code="cancelled_run_reconciliation_repository_invalid"))
    return (raw if raw.is_absolute() else path / raw).resolve()


def _registered_worktrees(repository_root: Path) -> dict[Path, dict[str, str]]:
    output = _git_value(repository_root, "worktree", "list", "--porcelain", code="cancelled_run_reconciliation_worktree_list_failed")
    records: dict[Path, dict[str, str]] = {}
    current: dict[str, str] = {}
    for line in [*output.splitlines(), ""]:
        if not line:
            if current.get("worktree"):
                records[Path(current["worktree"]).resolve()] = current
            current = {}
            continue
        key, separator, item = line.partition(" ")
        if separator:
            current[key] = item
    return records


def _assert_repo_and_git_state(
    *,
    repository_root: Path,
    managed_root: Path,
    repository_identity: str,
    project_uuid: str,
    accepted_main_commit: str,
    workspaces: list[dict[str, Any]],
) -> None:
    root = repository_root.resolve()
    managed = managed_root.resolve()
    top = Path(_git_value(root, "rev-parse", "--show-toplevel", code="cancelled_run_reconciliation_repository_invalid")).resolve()
    if top != root:
        raise _error("cancelled_run_reconciliation_repository_invalid", "repository root is not the selected canonical worktree")
    canonical_identity = hashlib.sha256(f"{project_uuid}\0{_common_dir(root)}".encode()).hexdigest()
    if repository_identity != canonical_identity:
        raise _error("cancelled_run_reconciliation_repository_identity_mismatch", "canonical repository identity does not match Todo authority")
    branch = _git_value(root, "symbolic-ref", "-q", "HEAD", code="cancelled_run_reconciliation_main_branch_invalid")
    main = _git_value(root, "rev-parse", "--verify", "refs/heads/main^{commit}", code="cancelled_run_reconciliation_main_missing").lower()
    head = _git_value(root, "rev-parse", "--verify", "HEAD^{commit}", code="cancelled_run_reconciliation_repository_invalid").lower()
    if branch != "refs/heads/main" or main != accepted_main_commit or head != accepted_main_commit:
        raise _error(
            "cancelled_run_reconciliation_main_changed",
            "canonical HEAD and refs/heads/main must equal the accepted commit",
            {"branch": branch, "head": head, "main": main, "accepted_main_commit": accepted_main_commit},
        )

    registered = _registered_worktrees(root)
    for workspace in workspaces:
        workspace_id = str(workspace["id"])
        row_identity = str(workspace["repository_identity"])
        if row_identity != repository_identity:
            raise _error(
                "cancelled_run_reconciliation_repository_identity_mismatch",
                f"workspace repository identity differs from the canonical repository: {workspace_id}",
            )
        if not workspace["worktree_path"] or not workspace["branch"]:
            raise _error("cancelled_run_reconciliation_worktree_unavailable", f"workspace lacks a managed path or branch: {workspace_id}")
        recorded_path = Path(str(workspace["worktree_path"]))
        target = recorded_path.resolve()
        try:
            target.relative_to(managed)
        except ValueError as exc:
            raise _error("cancelled_run_reconciliation_worktree_unmanaged", f"workspace is outside the managed root: {workspace_id}") from exc
        if target == managed or recorded_path.is_symlink() or not target.is_dir():
            raise _error("cancelled_run_reconciliation_worktree_unavailable", f"managed workspace is unavailable: {workspace_id}")
        record = registered.get(target)
        expected_branch = f"refs/heads/{workspace['branch']}"
        if record is None or record.get("branch") != expected_branch:
            raise _error(
                "cancelled_run_reconciliation_branch_mismatch",
                f"managed worktree branch does not match workspace authority: {workspace_id}",
                {"expected": expected_branch, "actual": record.get("branch") if record else None},
            )
        branch_commit = _git_value(
            root, "rev-parse", "--verify", f"{expected_branch}^{{commit}}",
            code="cancelled_run_reconciliation_branch_mismatch",
        ).lower()
        if branch_commit != str(workspace["expected_head"]).lower():
            raise _error(
                "cancelled_run_reconciliation_branch_mismatch",
                f"stored workspace branch no longer points at its explicit HEAD: {workspace_id}",
                {"expected": str(workspace["expected_head"]).lower(), "branch_commit": branch_commit},
            )
        if _common_dir(target) != _common_dir(root):
            raise _error("cancelled_run_reconciliation_repository_identity_mismatch", f"worktree belongs to a different Git repository: {workspace_id}")
        actual_head = _git_value(target, "rev-parse", "--verify", "HEAD^{commit}", code="cancelled_run_reconciliation_head_mismatch").lower()
        expected_head = str(workspace["expected_head"]).lower()
        if actual_head != expected_head or record.get("HEAD", "").lower() != expected_head:
            raise _error(
                "cancelled_run_reconciliation_head_mismatch",
                f"managed worktree HEAD differs from the explicit request: {workspace_id}",
                {"expected": expected_head, "actual": actual_head},
            )
        cleanliness = _git(target, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if cleanliness.returncode != 0:
            raise _error("cancelled_run_reconciliation_cleanliness_unknown", f"Git cleanliness could not be verified: {workspace_id}")
        if cleanliness.stdout:
            raise _error(
                "cancelled_run_reconciliation_worktree_dirty",
                f"selected worktree contains tracked or untracked files: {workspace_id}",
                {"status_bytes": len(cleanliness.stdout), "status_sha256": hashlib.sha256(cleanliness.stdout).hexdigest()},
            )
        ancestry = _git(root, "merge-base", "--is-ancestor", expected_head, accepted_main_commit)
        if ancestry.returncode != 0:
            code = "cancelled_run_reconciliation_head_not_merged" if ancestry.returncode == 1 else "cancelled_run_reconciliation_git_failed"
            raise _error(code, f"workspace HEAD is not proven as an ancestor of accepted main: {workspace_id}")


def _validate(
    conn: Any,
    *,
    request: dict[str, Any],
    repository_root: Path,
    managed_root: Path,
    repository_identity: str,
    project_uuid: str,
) -> dict[str, Any]:
    current_revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
    if current_revision != request["expected_revision"]:
        raise _error(
            "cancelled_run_reconciliation_stale_revision",
            "Todo authority revision changed since the exact workspace heads were reviewed",
            {"expected": request["expected_revision"], "actual": current_revision},
        )
    run = conn.execute("SELECT status FROM workflow_runs WHERE id=?", (request["run_id"],)).fetchone()
    if run is None or str(run["status"]) != "cancelled":
        raise _error("cancelled_run_reconciliation_run_not_cancelled", "only an already-cancelled workflow run can be reconciled")

    memberships = [dict(row) for row in conn.execute(
        "SELECT lt.lane_id,lt.task_id,lt.state AS lane_task_state,t.status AS task_status "
        "FROM workflow_lane_tasks lt JOIN workflow_lanes l ON l.id=lt.lane_id "
        "JOIN tasks t ON t.id=lt.task_id WHERE l.run_id=? ORDER BY lt.lane_id,lt.position,lt.task_id",
        (request["run_id"],),
    )]
    unfinished = [row for row in memberships if row["lane_task_state"] not in TERMINAL_LANE_TASK_STATES or row["task_status"] not in TERMINAL]
    if unfinished:
        raise _error("cancelled_run_reconciliation_membership_active", "all run memberships and their tasks must already be terminal", unfinished)
    lanes = [dict(row) for row in conn.execute(
        "SELECT id,state FROM workflow_lanes WHERE run_id=? ORDER BY id", (request["run_id"],)
    )]
    if not lanes:
        raise _error("cancelled_run_reconciliation_lanes_missing", "cancelled run has no workflow lanes")
    lane_state = {str(row["id"]): str(row["state"]) for row in lanes}

    owners = {
        "dispatches": [dict(row) for row in conn.execute(
            "SELECT d.id,d.lane_id,d.state FROM workflow_dispatches d JOIN workflow_lanes l ON l.id=d.lane_id "
            "WHERE l.run_id=? AND d.state IN ('active','revoking') ORDER BY d.id", (request["run_id"],)
        )],
        "claims": [dict(row) for row in conn.execute(
            "SELECT DISTINCT c.id,c.task_id,c.state FROM claims c JOIN workflow_lane_tasks lt ON lt.task_id=c.task_id "
            "JOIN workflow_lanes l ON l.id=lt.lane_id WHERE l.run_id=? AND c.state IN ('active','orphaned') ORDER BY c.id",
            (request["run_id"],),
        )],
        "resources": [dict(row) for row in conn.execute(
            "SELECT DISTINCT r.id,c.task_id FROM resource_leases r JOIN claims c ON c.id=r.claim_id "
            "JOIN workflow_lane_tasks lt ON lt.task_id=c.task_id JOIN workflow_lanes l ON l.id=lt.lane_id "
            "WHERE l.run_id=? AND r.state='active' ORDER BY r.id", (request["run_id"],)
        )],
        "lock_leases": [dict(row) for row in conn.execute(
            "SELECT DISTINCT ll.id,c.task_id,ll.lock_name FROM lock_leases ll JOIN claims c ON c.id=ll.claim_id "
            "JOIN workflow_lane_tasks lt ON lt.task_id=c.task_id JOIN workflow_lanes l ON l.id=lt.lane_id "
            "WHERE l.run_id=? AND ll.state='active' ORDER BY ll.id", (request["run_id"],)
        )],
        "child_scope_leases": [dict(row) for row in conn.execute(
            "SELECT DISTINCT sl.child_execution_id,ce.task_id,sl.path FROM child_scope_leases sl "
            "JOIN child_executions ce ON ce.id=sl.child_execution_id "
            "JOIN workflow_lane_tasks lt ON lt.task_id=ce.task_id JOIN workflow_lanes l ON l.id=lt.lane_id "
            "WHERE l.run_id=? AND sl.state='active' ORDER BY sl.child_execution_id,sl.path", (request["run_id"],)
        )],
        "children": [dict(row) for row in conn.execute(
            "SELECT DISTINCT ce.id,ce.task_id,ce.state FROM child_executions ce JOIN workflow_lane_tasks lt ON lt.task_id=ce.task_id "
            "JOIN workflow_lanes l ON l.id=lt.lane_id WHERE l.run_id=? AND ce.state NOT IN ('failed','cancelled','rejected') ORDER BY ce.id",
            (request["run_id"],),
        )],
        "gates": [dict(row) for row in conn.execute(
            "SELECT DISTINCT g.id,g.task_id,g.status FROM gates g JOIN workflow_lane_tasks lt ON lt.task_id=g.task_id "
            "JOIN workflow_lanes l ON l.id=lt.lane_id WHERE l.run_id=? AND g.status='running' ORDER BY g.id",
            (request["run_id"],),
        )],
        "artifacts": [dict(row) for row in conn.execute(
            "SELECT a.id,a.workspace_id,a.state FROM workflow_patch_artifacts a JOIN workflow_workspaces w ON w.id=a.workspace_id "
            "WHERE w.run_id=? AND a.state NOT IN ('integrated','rejected','superseded') ORDER BY a.id",
            (request["run_id"],),
        )],
        "queues": [dict(row) for row in conn.execute(
            "SELECT q.id,q.state FROM workflow_integration_queue q WHERE q.run_id=? AND q.state NOT IN ('integrated','rejected') ORDER BY q.id",
            (request["run_id"],),
        )],
    }
    blocked_owners = {name: rows for name, rows in owners.items() if rows}
    if blocked_owners:
        raise _error("cancelled_run_reconciliation_work_active", "live ownership or unresolved artifacts/queues block reconciliation", blocked_owners)

    rows = [dict(row) for row in conn.execute(
        "SELECT w.*,l.state AS lane_state FROM workflow_workspaces w JOIN workflow_lanes l ON l.id=w.lane_id "
        "WHERE w.run_id=? AND w.state NOT IN ('integrated','rejected') ORDER BY w.id",
        (request["run_id"],),
    )]
    expected_ids = set(request["workspaces"])
    actual_ids = {str(row["id"]) for row in rows}
    if expected_ids != actual_ids:
        raise _error(
            "cancelled_run_reconciliation_workspace_set_changed",
            "request must name exactly every current nonterminal workspace of the cancelled run",
            {"expected_ids": sorted(expected_ids), "actual_ids": sorted(actual_ids)},
        )
    for row in rows:
        row["expected_head"] = request["workspaces"][str(row["id"])]
        row["lane_state"] = lane_state[str(row["lane_id"])]
        if str(row["state"]) not in {"active", "quarantined"}:
            raise _error(
                "cancelled_run_reconciliation_workspace_state_unsupported",
                f"only active or quarantined workspaces can be reconciled: {row['id']} ({row['state']})",
            )
        if row["artifact_kind"] or row["artifact_ref"]:
            raise _error(
                "cancelled_run_reconciliation_workspace_artifact_present",
                f"workspace has an unaccounted artifact reference: {row['id']}",
            )
        try:
            old_receipt = json.loads(str(row["merge_result_json"] or "{}"))
        except (TypeError, json.JSONDecodeError) as exc:
            raise _error("cancelled_run_reconciliation_workspace_receipt_invalid", f"workspace receipt is not valid JSON: {row['id']}") from exc
        if not isinstance(old_receipt, dict):
            raise _error("cancelled_run_reconciliation_workspace_receipt_invalid", f"workspace receipt is not a JSON object: {row['id']}")

    _assert_repo_and_git_state(
        repository_root=repository_root,
        managed_root=managed_root,
        repository_identity=repository_identity,
        project_uuid=project_uuid,
        accepted_main_commit=request["accepted_main_commit"],
        workspaces=rows,
    )
    lane_changes = [
        {"lane_id": str(row["id"]), "previous_state": str(row["state"]), "new_state": "closed"}
        for row in lanes if str(row["state"]) not in _LANE_TERMINAL
    ]
    return {
        "run_id": request["run_id"],
        "expected_revision": request["expected_revision"],
        "accepted_main_commit": request["accepted_main_commit"],
        "workspaces": [
            {"workspace_id": str(row["id"]), "lane_id": str(row["lane_id"]), "branch": str(row["branch"]),
             "head": str(row["expected_head"]), "previous_state": str(row["state"]), "new_state": "integrated"}
            for row in rows
        ],
        "lane_state_changes": lane_changes,
        "reason": request["reason"],
    }


def reconcile_cancelled_run_workspaces(
    db: WorkflowDatabase,
    *,
    repository_root: Path,
    managed_root: Path,
    repository_identity: str,
    project_uuid: str,
    request: Mapping[str, Any],
    apply: bool = False,
) -> dict[str, Any]:
    """Preview or atomically apply an exact cancelled-run reconciliation."""
    normalized = _normalise_request(request)
    request_hash = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if not apply:
        with db.read() as conn:
            plan = _validate(
                conn, request=normalized, repository_root=repository_root, managed_root=managed_root,
                repository_identity=repository_identity, project_uuid=project_uuid,
            )
        return {"status": "ready", **plan, "request_sha256": request_hash}

    def operation(conn: Any, revision: int) -> dict[str, Any]:
        plan = _validate(
            conn, request=normalized, repository_root=repository_root, managed_root=managed_root,
            repository_identity=repository_identity, project_uuid=project_uuid,
        )
        now = utc_now()
        for item in plan["workspaces"]:
            row = conn.execute("SELECT merge_result_json FROM workflow_workspaces WHERE id=?", (item["workspace_id"],)).fetchone()
            previous = json.loads(str(row["merge_result_json"] or "{}"))
            receipt = {
                "schema": "cancelled-run-workspace-reconciliation-v1",
                "run_id": plan["run_id"],
                "workspace_id": item["workspace_id"],
                "lane_id": item["lane_id"],
                "branch": item["branch"],
                "source_head": item["head"],
                "accepted_main_commit": plan["accepted_main_commit"],
                "proof": {"kind": "source_included", "ancestor_of": "refs/heads/main"},
                "previous_state": item["previous_state"],
                "reason": plan["reason"],
                "request_sha256": request_hash,
                "revision": revision,
            }
            previous["cancelled_run_reconciliation"] = receipt
            conn.execute(
                "UPDATE workflow_workspaces SET state='integrated',merge_result_json=?,updated_at=? WHERE id=?",
                (json.dumps(previous, sort_keys=True, separators=(",", ":"), ensure_ascii=False), now, item["workspace_id"]),
            )
        for change in plan["lane_state_changes"]:
            if change["new_state"] == "closed":
                conn.execute(
                    "UPDATE workflow_lanes SET state='closed',updated_at=?,revision=? WHERE id=? AND state=?",
                    (now, revision, change["lane_id"], change["previous_state"]),
                )
        result = {
            "status": "reconciled",
            **plan,
            "request_sha256": request_hash,
            "pre_revision": normalized["expected_revision"],
            "revision": revision,
        }
        result["event_payload"] = {
            "run_id": plan["run_id"],
            "accepted_main_commit": plan["accepted_main_commit"],
            "request_sha256": request_hash,
            "reason": plan["reason"],
            "workspaces": plan["workspaces"],
            "lane_state_changes": plan["lane_state_changes"],
        }
        return result

    result, revision = db.mutate(
        actor_session_id=None,
        entity_type="workflow_run",
        entity_id=normalized["run_id"],
        event_type="workflow.cancelled_run_reconciliation.applied",
        payload=lambda value: value["event_payload"],
        operation=operation,
    )
    result.pop("event_payload", None)
    result["revision"] = revision
    return result
