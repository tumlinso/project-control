"""Frozen workflow-kernel constants, value types, and validation helpers.

This module contains no process, repository, network, model, or GPU startup work.
It deliberately owns no database: callers pass the existing todo Database or
transaction connection to higher-level workflow services.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable, ContextManager, Protocol, TypeVar

from ..migrations import DATABASE_MIGRATION_VERSION, PROJECT_SCHEMA_VERSION
from ..models import TodoError


PROTOCOL_VERSION = 2
LEGACY_PLAN_SCHEMA_VERSION = 2
WORKFLOW_PLAN_SCHEMA_VERSION = 3
WORKFLOW_SNAPSHOT_SECTION_VERSION = 1

NEXT_TASK_BUDGET_BYTES = 8 * 1024
COORDINATE_TASK_BUDGET_BYTES = 8 * 1024
DELEGATION_RESULT_BUDGET_BYTES = 4 * 1024
FINISH_TASK_BUDGET_BYTES = 8 * 1024
MESSAGE_PAYLOAD_BUDGET_BYTES = 4 * 1024
CHILD_PACKET_BUDGET_BYTES = 4 * 1024

ROLES = frozenset({"coordinator", "implementer", "validator", "integrator", "specialist"})
CAPABILITY_CLASSES = frozenset({"first_class", "child"})
MESSAGE_KINDS = frozenset({
    "status", "question", "answer", "decision", "interface_change",
    "conflict", "artifact", "handoff", "rendezvous_arrival",
})
CHILD_RESULT_KINDS = frozenset({
    "candidate_patch", "test_result", "performance_measurement", "source_finding",
    "review_finding", "diagnostic_finding",
})
COORDINATION_ACTIONS = frozenset({
    "sync", "fork", "message", "answer", "arrive", "publish_interface",
    "publish_context", "run_gates", "request_integration", "accept_child", "reject_child",
    "bind_required_gates",
})
RUN_LEVEL_ACTIONS = frozenset({
    "fork", "message", "answer", "arrive", "publish_interface", "publish_context", "request_integration",
})
WORKSPACE_MODES = frozenset({"exclusive", "read_shared", "isolated_merge", "contract_split"})


T = TypeVar("T")


class WorkflowDatabase(Protocol):
    """The existing todo Database seam consumed by workflow modules."""

    def read(self) -> ContextManager[Any]: ...

    def mutate(
        self,
        *,
        actor_session_id: str | None | Callable[[T], str | None],
        entity_type: str,
        entity_id: str | None | Callable[[T], str | None],
        event_type: str,
        payload: dict[str, Any] | Callable[[T], dict[str, Any]] | None,
        operation: Callable[[Any, int], T],
    ) -> tuple[T, int]: ...


@dataclass(frozen=True)
class WorkflowIdentity:
    project_uuid: str
    repository_identity: str
    run_id: str
    lane_id: str
    role: str
    task_id: str

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise TodoError("invalid_workflow_role", f"Unknown workflow role: {self.role}")
        for name, value in self.__dict__.items():
            if not isinstance(value, str) or not value:
                raise TodoError("invalid_workflow_identity", f"{name} must be non-empty")


@dataclass(frozen=True)
class CapabilityLineage:
    capability_class: str
    project_uuid: str
    repository_identity: str
    session_id: str | None
    claim_id: str | None
    run_id: str | None
    lane_id: str | None
    role: str | None
    task_id: str
    allowed_operations: frozenset[str]
    incarnation: int
    parent_capability_id: str | None = None
    child_execution_id: str | None = None

    def validate(self, parent: "CapabilityLineage | None" = None) -> None:
        if self.capability_class not in CAPABILITY_CLASSES:
            raise TodoError("invalid_capability_class", "Capability class is not supported")
        if not self.project_uuid or not self.repository_identity or not self.task_id or self.incarnation < 1:
            raise TodoError("invalid_capability_lineage", "Capability lineage is incomplete")
        if self.capability_class == "first_class":
            required = (self.session_id, self.claim_id, self.run_id, self.lane_id, self.role)
            if not all(required) or self.parent_capability_id or self.child_execution_id:
                raise TodoError("invalid_first_class_capability", "First-class lineage is incomplete or mixed")
            if self.role not in ROLES:
                raise TodoError("invalid_workflow_role", "First-class capability role is invalid")
            return
        if not parent or parent.capability_class != "first_class":
            raise TodoError("invalid_child_capability", "Child capability requires one first-class parent")
        if not self.parent_capability_id or not self.child_execution_id:
            raise TodoError("invalid_child_capability", "Child lineage is incomplete")
        if self.run_id is not None or self.lane_id is not None or self.role is not None:
            raise TodoError("child_run_authority_forbidden", "Child capability cannot carry run or lane authority")
        if self.project_uuid != parent.project_uuid or self.repository_identity != parent.repository_identity:
            raise TodoError("child_lineage_mismatch", "Child project lineage differs from its parent")
        if self.claim_id != parent.claim_id or self.task_id != parent.task_id:
            raise TodoError("child_parent_claim_mismatch", "Child must remain under exactly one parent claim/task")
        if not self.allowed_operations <= parent.allowed_operations:
            raise TodoError("child_operation_scope_expansion", "Child operations exceed parent authority")
        if self.allowed_operations & RUN_LEVEL_ACTIONS:
            raise TodoError("child_run_authority_forbidden", "Child capability cannot call run-level actions")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require_bounded_payload(value: object, *, limit: int, code: str = "workflow_payload_too_large") -> int:
    size = len(canonical_json(value).encode("utf-8"))
    if size > limit:
        raise TodoError(code, f"Payload is {size} bytes; limit is {limit}", details={"size": size, "limit": limit})
    return size


def _normalized_scope(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise TodoError("invalid_child_scope", f"Invalid repository-relative scope: {path}")
    return candidate


def child_scope_is_subset(parent_paths: list[str], child_paths: list[str]) -> bool:
    parents = [_normalized_scope(path) for path in parent_paths]
    children = [_normalized_scope(path) for path in child_paths]
    return all(any(child == parent or parent in child.parents for parent in parents) for child in children)


def require_child_scope_subset(parent_paths: list[str], child_paths: list[str]) -> None:
    if not child_scope_is_subset(parent_paths, child_paths):
        raise TodoError("child_scope_expansion", "Child scope must be a strict subset of parent-authorized paths")


def schema_contract() -> dict[str, object]:
    return {
        "database_migration_version": DATABASE_MIGRATION_VERSION,
        "project_schema_version": PROJECT_SCHEMA_VERSION,
        "legacy_plan_schema_version": LEGACY_PLAN_SCHEMA_VERSION,
        "workflow_plan_schema_version": WORKFLOW_PLAN_SCHEMA_VERSION,
        "workflow_snapshot_section_version": WORKFLOW_SNAPSHOT_SECTION_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "roles": sorted(ROLES),
        "message_kinds": sorted(MESSAGE_KINDS),
        "child_result_kinds": sorted(CHILD_RESULT_KINDS),
        "coordination_actions": sorted(COORDINATION_ACTIONS),
        "workspace_modes": sorted(WORKSPACE_MODES),
    }
