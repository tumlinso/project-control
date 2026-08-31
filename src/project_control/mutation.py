"""Validated native Todo plan mutation through the verified Todo runtime.

Project Control owns proposal freshness and the bounded receipt.  Todo
Orchestrator continues to own plan validation, diffing, transactions, events,
and generated projections.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .config import ProjectControlConfig, configured_skills_root
from .models import ObservationPreconditions, ProposalEnvelope, ProjectSnapshot
from .proposals import observation_preconditions, stale_preconditions
from .registry import WorkspaceRegistry
from .snapshot import SnapshotBuilder
from .workflow_binding import initialize_workflow_binding, todo_read_port_factory


class MutationRejected(RuntimeError):
    """A bounded, fail-closed rejection safe for CLI and MCP adapters."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def canonical_plan_bytes(plan: Mapping[str, Any]) -> bytes:
    return (json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def plan_digest(plan: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_plan_bytes(plan)).hexdigest()


def _runtime_environment(config: ProjectControlConfig) -> dict[str, str]:
    environment = dict(os.environ)
    root = configured_skills_root(config, environment)
    if root is None:
        raise MutationRejected("skills_root_unavailable", "Todo runtime root is not configured")
    environment["PROJECT_CONTROL_SKILLS_ROOT"] = str(root)
    environment.pop("CODING_WORKFLOW_SKILLS_ROOT", None)
    return environment


def _snapshot_builder(
    config: ProjectControlConfig,
    environment: Mapping[str, str],
    builder: SnapshotBuilder | None,
) -> SnapshotBuilder:
    if builder is not None:
        return builder
    return SnapshotBuilder(config, todo_read_port_factory=todo_read_port_factory(environment))


def _authority_root(config: ProjectControlConfig, project: str) -> Path:
    registry = WorkspaceRegistry(config)
    workspace = registry.workspace(project)
    if workspace.authority_repository is None:
        raise MutationRejected("todo_authority_unavailable", "Workspace has no Todo authority repository")
    return registry.repository(project, workspace.authority_repository).root


def _todo_service(
    config: ProjectControlConfig,
    project: str,
    environment: Mapping[str, str],
    *,
    read_only: bool,
):
    binding = initialize_workflow_binding(environment)
    binding.validate()
    from todo_orchestrator.service import Service

    service = Service(_authority_root(config, project), read_only=read_only)
    binding.validate()
    return binding, service


def _temporary_plan(plan: Mapping[str, Any]):
    descriptor, name = tempfile.mkstemp(prefix="project-control-plan-", suffix=".json")
    path = Path(name)
    os.fchmod(descriptor, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_plan_bytes(plan))
            stream.flush()
            os.fsync(stream.fileno())
        yield path
    finally:
        path.unlink(missing_ok=True)


# Keep this local context manager dependency-free and easy to patch in tests.
from contextlib import contextmanager

_temporary_plan = contextmanager(_temporary_plan)


def _todo_error(error: Exception) -> MutationRejected:
    code = getattr(error, "code", None)
    details = getattr(error, "details", None)
    return MutationRejected(
        str(code) if isinstance(code, str) and code else "todo_plan_operation_failed",
        str(error),
        details=details if isinstance(details, Mapping) else None,
    )


def _validate_and_diff(service: object, path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        validation = service.plan_validate(str(path))
        diff = service.plan_diff(str(path))
    except Exception as error:
        raise _todo_error(error) from error
    return dict(validation), dict(diff)


def validate_native_plan(
    config: ProjectControlConfig,
    project: str,
    native_plan: Mapping[str, Any],
    *,
    snapshot_builder: SnapshotBuilder | None = None,
) -> dict[str, Any]:
    """Validate and diff one native Todo plan without mutation."""

    plan = dict(native_plan)
    environment = _runtime_environment(config)
    builder = _snapshot_builder(config, environment, snapshot_builder)
    before = builder.build(project)
    binding, service = _todo_service(config, project, environment, read_only=True)
    with _temporary_plan(plan) as path:
        validation, diff = _validate_and_diff(service, path)
    binding.validate()
    after = builder.build(project)
    if before.project_uuid != after.project_uuid or before.todo_revision != after.todo_revision:
        raise MutationRejected("plan_validation_mutated_authority", "Todo authority changed during validation")
    return {
        "status": "validated",
        "valid": bool(validation.get("valid")),
        "plan_digest": plan_digest(plan),
        "project_uuid": before.project_uuid,
        "revision": before.todo_revision,
        "would_add": list(diff.get("add", [])),
        "would_modify": list(diff.get("update", [])),
        "warnings": list(validation.get("warnings", [])),
        "validation": validation,
        "current_observation_preconditions": observation_preconditions(after).model_dump(mode="json"),
    }


def _parse_proposal(value: ProposalEnvelope | Mapping[str, Any]) -> ProposalEnvelope:
    if isinstance(value, ProposalEnvelope):
        # Revalidate even already-constructed models so callers cannot rely on
        # unchecked model construction helpers.
        value = value.model_dump(mode="json")
    try:
        return ProposalEnvelope.model_validate(value)
    except (ValidationError, ValueError, TypeError) as error:
        raise MutationRejected("proposal_invalid", "ProposalEnvelope is invalid", details={"error": str(error)}) from error


def _fresh(proposal: ProposalEnvelope, current: ObservationPreconditions) -> None:
    comparison = stale_preconditions(proposal.observation_preconditions, current)
    if comparison["stale"]:
        raise MutationRejected(
            "proposal_stale",
            "Proposal observation preconditions are stale",
            details={"mismatches": comparison["mismatches"]},
        )


def _apply_once(
    service: object,
    path: Path,
    *,
    plan_hash: str,
    expected_revision: int,
) -> tuple[dict[str, Any], int, dict[str, Any]]:
    """Enter Todo's transaction once through its canonical front door."""

    from todo_orchestrator.models import TodoError
    from todo_orchestrator.plan import apply_plan, load_plan

    data = load_plan(path)

    def operation(conn, revision):
        if revision - 1 != expected_revision:
            raise TodoError(
                "proposal_stale",
                "Todo revision changed after proposal revalidation",
                details={"expected_revision": expected_revision, "current_revision": revision - 1},
            )
        return apply_plan(conn, data, service.paths.repo_root, revision)

    try:
        result, revision, projection = service.mutate(
            actor=None,
            entity_type="project",
            entity_id=str(service.project["project_uuid"]),
            event_type="plan.applied",
            payload={"plan_digest": plan_hash, "source": "project-control"},
            operation=operation,
            full_projection=True,
            canonical_workflow=True,
        )
    except Exception as error:
        raise _todo_error(error) from error
    return dict(result), int(revision), dict(projection)


def _task_ids(snapshot: ProjectSnapshot) -> set[str]:
    return {
        str(row["id"])
        for row in snapshot.todo_tables.get("tasks", [])
        if isinstance(row, dict) and row.get("id") is not None
    }


def apply_proposal(
    config: ProjectControlConfig,
    project: str,
    proposal: ProposalEnvelope | Mapping[str, Any],
    *,
    snapshot_builder: SnapshotBuilder | None = None,
) -> dict[str, Any]:
    """Apply one fresh inert proposal containing a native Todo plan."""

    envelope = _parse_proposal(proposal)
    plan = envelope.proposed_change
    if not isinstance(plan.get("schema_version"), int) or not isinstance(plan.get("tasks"), list):
        raise MutationRejected(
            "unsupported_proposed_change",
            "Proposal proposed_change must be a native Todo plan",
        )

    environment = _runtime_environment(config)
    builder = _snapshot_builder(config, environment, snapshot_builder)
    before = builder.build(project)
    _fresh(envelope, observation_preconditions(before))
    binding, read_service = _todo_service(config, project, environment, read_only=True)
    digest = plan_digest(plan)

    with _temporary_plan(plan) as path:
        validation, diff = _validate_and_diff(read_service, path)
        would_add = list(diff.get("add", []))
        would_modify = list(diff.get("update", []))
        if not would_add and not would_modify:
            binding.validate()
            current = builder.build(project)
            _fresh(envelope, observation_preconditions(current))
            return {
                "status": "noop",
                "proposal_digest": envelope.deterministic_digest,
                "plan_digest": digest,
                "project_uuid": current.project_uuid,
                "before_revision": current.todo_revision,
                "after_revision": current.todo_revision,
                "would_add": [],
                "would_modify": [],
                "applied_add": [],
                "applied_modify": [],
                "warnings": list(validation.get("warnings", [])),
                "current_observation_preconditions": observation_preconditions(current).model_dump(mode="json"),
            }

        # Re-observe immediately before Todo's transaction.  The transaction
        # itself also checks the revision under BEGIN IMMEDIATE.
        current = builder.build(project)
        _fresh(envelope, observation_preconditions(current))
        if current.project_uuid is None or current.todo_revision is None:
            raise MutationRejected("todo_authority_unavailable", "Todo authority identity is incomplete")
        binding.validate()
        _write_binding, write_service = _todo_service(
            config, project, environment, read_only=False,
        )
        applied, applied_revision, _projection = _apply_once(
            write_service,
            path,
            plan_hash=digest,
            expected_revision=current.todo_revision,
        )

    binding.validate()
    after = builder.build(project)
    if after.project_uuid != current.project_uuid:
        raise MutationRejected("todo_authority_changed", "Todo project UUID changed during plan application")
    if after.todo_revision != applied_revision or applied_revision <= current.todo_revision:
        raise MutationRejected(
            "todo_revision_incoherent",
            "Todo revision after application is incoherent",
            details={"before": current.todo_revision, "reported": applied_revision, "observed": after.todo_revision},
        )
    missing = sorted((set(would_add) | set(would_modify)) - _task_ids(after))
    if missing:
        raise MutationRejected(
            "todo_plan_postcondition_failed",
            "Applied Todo plan tasks are absent from resulting state",
            details={"missing_task_ids": missing},
        )
    workflow_result = applied.get("workflow", {})
    if not isinstance(workflow_result, Mapping):
        workflow_result = {}
    return {
        "status": "applied",
        "proposal_digest": envelope.deterministic_digest,
        "plan_digest": digest,
        "project_uuid": after.project_uuid,
        "before_revision": current.todo_revision,
        "after_revision": after.todo_revision,
        "would_add": would_add,
        "would_modify": would_modify,
        "applied_add": would_add,
        "applied_modify": would_modify,
        "warnings": list(validation.get("warnings", [])),
        "todo_result": {
            "tasks_upserted": applied.get("tasks_upserted"),
            "plan_schema_version": workflow_result.get("plan_schema_version"),
            "compatibility": workflow_result.get("compatibility"),
            "runs": workflow_result.get("runs", []),
        },
        "current_observation_preconditions": observation_preconditions(after).model_dump(mode="json"),
    }
