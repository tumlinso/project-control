from __future__ import annotations

import json
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
    kind: Literal["task", "interface", "checkpoint", "decision", "dependency", "symbol", "path", "subsystem"]
    target: str = Field(min_length=1, max_length=512)
    repository: str | None = None
    intent: Literal["architecture", "implementation", "debug", "review", "performance"] = "architecture"
    budget_tokens: int = Field(default=4000, ge=256, le=7000)


class EvidenceInput(BaseModel):
    project: str
    subject: str = Field(min_length=1, max_length=512)
    kinds: list[Literal["source", "tests", "gates", "worker", "cuda", "git"]] = Field(default_factory=list)
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
    local_worker: dict[str, Any] = Field(default_factory=dict)
    cuda: dict[str, Any] = Field(default_factory=dict)
    host: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    provider_warnings: dict[str, list[str]] = Field(default_factory=dict)

    def cursor(self) -> Cursor:
        return Cursor(
            todo_revision=self.todo_revision,
            commits={name: identity.commit for name, identity in self.repositories.items()},
            fingerprints=self.repository_fingerprints,
            working_tree_fingerprints=self.repository_fingerprints,
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
