from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ToolStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    INVALID_REQUEST = "invalid_request"
    INTERNAL_ERROR = "internal_error"


class RepositoryIdentity(BaseModel):
    commit: str
    dirty: bool
    working_tree_fingerprint: str | None = None
    git_common_id: str | None = None
    worktrees: dict[str, "WorktreeIdentity"] = Field(default_factory=dict)


class WorktreeIdentity(BaseModel):
    id: str
    repository: str
    branch: str | None = None
    head: str
    detached: bool = False
    dirty: bool
    working_tree_fingerprint: str
    dirty_paths: list[str] = Field(default_factory=list)
    observed_at: str


class AuthorityComponent(BaseModel):
    status: Literal["available", "unavailable", "partial", "raced"]
    operation: str
    revision: int | None = None
    read_authority_fingerprint: str | None = None
    project_uuid: str | None = None
    observed_at: str
    source_identity: str
    error_code: str | None = None
    revision_skew: int | None = None


class WorktreePrecondition(BaseModel):
    head: str
    working_tree_fingerprint: str


class VersionedPrecondition(BaseModel):
    version: int | str | None = None
    content_hash: str | None = None
    state: str | None = None


class ObservationPreconditions(BaseModel):
    workspace_id: str
    project_uuid: str | None = None
    todo_revision: int | None = None
    todo_semantic_authority_fingerprint: str | None = None
    workflow_revision: int | None = None
    workflow_authority_fingerprint: str | None = None
    repository_commits: dict[str, str] = Field(default_factory=dict)
    worktrees: dict[str, WorktreePrecondition] = Field(default_factory=dict)
    run_id: str | None = None
    task_ids: list[str] = Field(default_factory=list)
    lane_ids: list[str] = Field(default_factory=list)
    context_fragments: dict[str, VersionedPrecondition] = Field(default_factory=dict)
    interfaces: dict[str, VersionedPrecondition] = Field(default_factory=dict)
    observed_at: str
    provider_skew: dict[str, int | None] = Field(default_factory=dict)


class ProposalEnvelope(BaseModel):
    proposal_version: Literal[1] = 1
    intent: str = Field(min_length=1, max_length=4000)
    proposed_change: dict[str, Any]
    observation_preconditions: ObservationPreconditions
    deterministic_digest: str
    created_at: str
    authority_to_apply: Literal[False] = False

    @staticmethod
    def digest(intent: str, proposed_change: dict[str, Any], preconditions: ObservationPreconditions) -> str:
        value = {
            "proposal_version": 1,
            "intent": intent,
            "proposed_change": proposed_change,
            "observation_preconditions": preconditions.model_dump(mode="json"),
            "authority_to_apply": False,
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @classmethod
    def create(
        cls,
        *,
        intent: str,
        proposed_change: dict[str, Any],
        observation_preconditions: ObservationPreconditions,
        created_at: str | None = None,
    ) -> "ProposalEnvelope":
        return cls(
            intent=intent,
            proposed_change=proposed_change,
            observation_preconditions=observation_preconditions,
            deterministic_digest=cls.digest(intent, proposed_change, observation_preconditions),
            created_at=created_at or utc_now(),
        )

    @model_validator(mode="after")
    def digest_matches_content(self) -> "ProposalEnvelope":
        expected = self.digest(self.intent, self.proposed_change, self.observation_preconditions)
        if self.deterministic_digest != expected:
            raise ValueError("proposal digest does not match inert proposal content")
        return self


class ProjectIdentity(BaseModel):
    id: str
    observed_at: str
    todo_revision: int | None = None
    repositories: dict[str, RepositoryIdentity]


class Cursor(BaseModel):
    todo_revision: int | None = None
    commits: dict[str, str] = Field(default_factory=dict)
    fingerprints: dict[str, str] = Field(default_factory=dict)
    working_tree_fingerprints: dict[str, str] = Field(default_factory=dict)
    todo_semantic_fingerprint: str | None = None
    workflow_semantic_fingerprint: str | None = None
    worktrees: dict[str, WorktreePrecondition] = Field(default_factory=dict)
    context_fragments: dict[str, VersionedPrecondition] = Field(default_factory=dict)
    active_run_id: str | None = None
    observed_at: str


class ToolEnvelope(BaseModel):
    schema_version: Literal[1] = 1
    tool: str
    status: ToolStatus
    project: ProjectIdentity
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    cursor: Cursor


class ProjectOverviewInput(BaseModel):
    project: str
    detail: Literal["compact", "standard", "expanded"] = "standard"
    max_items: int = Field(default=20, ge=1, le=100)


class DeltaSince(BaseModel):
    todo_revision: int | None = Field(default=None, ge=0)
    commits: dict[str, str] = Field(default_factory=dict)
    fingerprints: dict[str, str] = Field(default_factory=dict)
    working_tree_fingerprints: dict[str, str] = Field(default_factory=dict)
    todo_semantic_fingerprint: str | None = None
    workflow_semantic_fingerprint: str | None = None
    worktrees: dict[str, WorktreePrecondition] = Field(default_factory=dict)
    context_fragments: dict[str, VersionedPrecondition] = Field(default_factory=dict)
    active_run_id: str | None = None
    observed_at: str | None = None
    task: str | None = Field(default=None, max_length=256)
    checkpoint: str | None = Field(default=None, max_length=256)
    interface: str | None = Field(default=None, max_length=256)
    commit: str | None = Field(default=None, max_length=128)
    time: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def one_semantic_anchor(self) -> "DeltaSince":
        anchors = [self.todo_revision is not None, self.task is not None, self.checkpoint is not None, self.interface is not None, self.time is not None]
        if sum(anchors) > 1:
            raise ValueError("select at most one todo revision, task, checkpoint, interface, or time anchor")
        return self


class ProjectDeltaInput(BaseModel):
    project: str
    since: DeltaSince
    detail: Literal["architectural", "standard", "implementation"] = "standard"
    max_items: int = Field(default=40, ge=1, le=200)


class ProjectFrontierInput(BaseModel):
    project: str
    max_ready: int = Field(default=20, ge=1, le=100)
    include_blocked: bool = True
    include_parallel_groups: bool = True


class InspectInput(BaseModel):
    project: str
    kind: Literal[
        "task", "interface", "checkpoint", "decision", "dependency", "symbol", "path", "subsystem",
        "run", "lane", "dispatch", "message", "rendezvous", "context_fragment", "workspace",
        "worktree", "patch", "integration", "gate", "invariant", "artifact", "commit", "test",
    ]
    target: str = Field(min_length=1, max_length=512)
    repository: str | None = None
    intent: Literal["architecture", "implementation", "debug", "review", "performance"] = "architecture"
    budget_tokens: int = Field(default=4000, ge=256, le=32768)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    worktree_id: str | None = Field(default=None, max_length=128)
    source_selector: str = Field(default="working_tree", max_length=128)
    continuation_cursor: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def valid_line_range(self) -> "InspectInput":
        if self.line_start and self.line_end and self.line_end < self.line_start:
            raise ValueError("line_end must not precede line_start")
        return self


class EvidenceInput(BaseModel):
    project: str
    subject: str = Field(min_length=1, max_length=512)
    kinds: list[Literal[
        "source", "tests", "gates", "worker", "cuda", "git", "architecture", "decision",
        "message", "context", "workspace", "integration",
    ]] = Field(default_factory=list)
    detail: Literal["summary", "provenance", "bounded_excerpt"] = "summary"
    max_items: int = Field(default=30, ge=1, le=100)


class PlanPreviewInput(BaseModel):
    project: str
    mode: Literal["context", "validate", "handoff"]
    objective: str | None = Field(default=None, max_length=4000)
    proposal: dict[str, Any] | None = None
    detail: Literal["compact", "standard"] = "standard"

    @model_validator(mode="after")
    def proposal_required(self) -> "PlanPreviewInput":
        if self.mode in {"validate", "handoff"} and self.proposal is None:
            raise ValueError("proposal is required for validate and handoff")
        if self.proposal is not None and len(json.dumps(self.proposal).encode()) > 256 * 1024:
            raise ValueError("proposal exceeds 256 KiB")
        return self


class AgentStatusInput(BaseModel):
    project: str
    include_children: bool = True
    include_local_services: bool = True


class PerformanceStatusInput(BaseModel):
    project: str
    campaign: str | None = Field(default=None, max_length=256)
    detail: Literal["compact", "standard", "expanded"] = "standard"
    include_host_capacity: bool = True


class ArchitectureContextInput(BaseModel):
    project: str
    question: str = Field(min_length=1, max_length=12000)
    repository: str | None = None
    worktree_id: str | None = Field(default=None, max_length=128)
    detail: Literal["compact", "standard", "expanded"] = "standard"
    scope: Literal["current", "current_and_reference", "all"] = "current_and_reference"
    inclusion_categories: list[str] = Field(default_factory=list, max_length=32)
    max_items: int = Field(default=60, ge=1, le=500)
    continuation_cursor: str | None = Field(default=None, max_length=4096)


class CoordinationViewInput(BaseModel):
    project: str
    run_id: str | None = Field(default=None, max_length=256)
    lane_id: str | None = Field(default=None, max_length=256)
    task_id: str | None = Field(default=None, max_length=256)
    since_revision: int | None = Field(default=None, ge=0)
    detail: Literal["compact", "standard", "expanded"] = "standard"
    include_resolved_messages: bool = False
    include_historical_arrivals: bool = False
    max_items: int = Field(default=100, ge=1, le=1000)
    continuation_cursor: str | None = Field(default=None, max_length=4096)


class SourceTarget(BaseModel):
    kind: Literal["path", "symbol", "subsystem", "text"]
    value: str = Field(min_length=1, max_length=1024)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def path_range_only(self) -> "SourceTarget":
        if (self.line_start or self.line_end) and self.kind != "path":
            raise ValueError("line ranges are valid only for path targets")
        if self.line_start and self.line_end and self.line_end < self.line_start:
            raise ValueError("line_end must not precede line_start")
        return self


class SourceContextInput(BaseModel):
    project: str
    repository: str
    worktree_id: str | None = Field(default=None, max_length=128)
    targets: list[SourceTarget] = Field(min_length=1, max_length=32)
    source_selector: str = Field(default="working_tree", min_length=1, max_length=128)
    intent: Literal["architecture", "implementation", "debug", "review", "performance"] = "implementation"
    requested_relations: list[Literal[
        "definitions", "references", "callers", "callees", "tests", "build_config_references",
        "documentation", "recent_changes", "task_ownership", "interfaces", "performance_evidence",
    ]] = Field(default_factory=list, max_length=16)
    detail: Literal["compact", "standard", "expanded"] = "standard"
    budget_bytes: int = Field(default=48 * 1024, ge=1024, le=128 * 1024)
    continuation_cursor: str | None = Field(default=None, max_length=4096)


class HistoryTraceInput(BaseModel):
    project: str
    subject: str = Field(min_length=1, max_length=1024)
    from_revision: int | None = Field(default=None, ge=0)
    from_time: str | None = Field(default=None, max_length=64)
    from_task: str | None = Field(default=None, max_length=256)
    from_checkpoint: str | None = Field(default=None, max_length=256)
    from_interface: str | None = Field(default=None, max_length=256)
    from_commit: str | None = Field(default=None, max_length=128)
    to_revision: int | None = Field(default=None, ge=0)
    to_commit: str | None = Field(default=None, max_length=128)
    detail: Literal["compact", "standard", "expanded"] = "standard"
    max_events: int = Field(default=100, ge=1, le=1000)
    continuation_cursor: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def one_from_anchor(self) -> "HistoryTraceInput":
        anchors = [
            self.from_revision is not None, self.from_time is not None, self.from_task is not None,
            self.from_checkpoint is not None, self.from_interface is not None, self.from_commit is not None,
        ]
        if sum(anchors) > 1:
            raise ValueError("select at most one from anchor")
        return self


class ImpactPreviewInput(BaseModel):
    project: str
    hypothesis: str = Field(min_length=1, max_length=12000)
    proposed_change: dict[str, Any] | None = None
    target_entities: list[str] = Field(default_factory=list, max_length=64)
    detail: Literal["compact", "standard", "expanded"] = "standard"
    max_items: int = Field(default=100, ge=1, le=1000)
    include_proposal_envelope: bool = True


class ProgramContextInput(BaseModel):
    program_id: str | None = Field(default=None, max_length=128)
    workspaces: list[str] = Field(default_factory=list, max_length=16)
    question: str = Field(min_length=1, max_length=12000)
    detail: Literal["compact", "standard", "expanded"] = "standard"
    max_items: int = Field(default=100, ge=1, le=1000)
    continuation_cursor: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def one_program_selector(self) -> "ProgramContextInput":
        if bool(self.program_id) == bool(self.workspaces):
            raise ValueError("select exactly one configured program or explicit workspace list")
        return self


class ProjectSnapshot(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    workspace_id: str
    display_name: str | None = None
    observed_at: str
    todo_revision: int | None = None
    project_uuid: str | None = None
    repositories: dict[str, RepositoryIdentity]
    repository_fingerprints: dict[str, str] = Field(default_factory=dict)
    todo_status: dict[str, Any] = Field(default_factory=dict)
    todo_tables: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    todo_semantic: dict[str, Any] = Field(default_factory=dict)
    todo_workflow: dict[str, Any] = Field(default_factory=dict)
    component_authority: dict[str, AuthorityComponent] = Field(default_factory=dict)
    local_worker: dict[str, Any] = Field(default_factory=dict)
    cuda: dict[str, Any] = Field(default_factory=dict)
    host: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    provider_warnings: dict[str, list[str]] = Field(default_factory=dict)

    def observation_preconditions(self) -> ObservationPreconditions:
        semantic = self.component_authority.get("todo_semantic_state")
        workflow = self.component_authority.get("todo_workflow")
        worktrees = {
            worktree_id: WorktreePrecondition(
                head=item.head,
                working_tree_fingerprint=item.working_tree_fingerprint,
            )
            for repository in self.repositories.values()
            for worktree_id, item in repository.worktrees.items()
        }
        return ObservationPreconditions(
            workspace_id=self.workspace_id,
            project_uuid=self.project_uuid,
            todo_revision=self.todo_revision,
            todo_semantic_authority_fingerprint=(semantic.read_authority_fingerprint if semantic else None),
            workflow_revision=(workflow.revision if workflow else self.todo_workflow.get("revision")),
            workflow_authority_fingerprint=(workflow.read_authority_fingerprint if workflow else self.todo_workflow.get("read_authority_fingerprint")),
            repository_commits={name: identity.commit for name, identity in self.repositories.items()},
            worktrees=worktrees,
            run_id=self.todo_workflow.get("active_run_id"),
            observed_at=self.observed_at,
            provider_skew={name: component.revision_skew for name, component in self.component_authority.items()},
        )

    def cursor(self) -> Cursor:
        preconditions = self.observation_preconditions()
        return Cursor(
            todo_revision=self.todo_revision,
            commits={name: identity.commit for name, identity in self.repositories.items()},
            fingerprints=self.repository_fingerprints,
            working_tree_fingerprints=self.repository_fingerprints,
            todo_semantic_fingerprint=preconditions.todo_semantic_authority_fingerprint,
            workflow_semantic_fingerprint=preconditions.workflow_authority_fingerprint,
            worktrees=preconditions.worktrees,
            active_run_id=preconditions.run_id,
            observed_at=self.observed_at,
        )

    def identity(self) -> ProjectIdentity:
        return ProjectIdentity(
            id=self.workspace_id,
            observed_at=self.observed_at,
            todo_revision=self.todo_revision,
            repositories=self.repositories,
        )

    def warnings_for(self, *providers: str) -> list[str]:
        values: list[str] = []
        for provider in providers:
            values.extend(self.provider_warnings.get(provider, []))
        return list(dict.fromkeys(values))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def envelope(tool: str, snapshot: ProjectSnapshot, data: dict[str, Any], *, warnings: list[str] | None = None) -> ToolEnvelope:
    all_warnings = list(dict.fromkeys([*snapshot.warnings, *(warnings or [])]))
    status = ToolStatus.PARTIAL if all_warnings else ToolStatus.OK
    return ToolEnvelope(
        tool=tool,
        status=status,
        project=snapshot.identity(),
        data=data,
        warnings=all_warnings,
        cursor=snapshot.cursor(),
    )
