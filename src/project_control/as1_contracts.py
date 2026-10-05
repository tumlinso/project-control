"""Versioned AS1 producer/consumer contracts; no authority or startup effects.

These types freeze wire values, not persistence, dispatch, or model lifecycle.
File locators describe actual bytes; semantic entities use EntityReference.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from types import MappingProxyType
from typing import Annotated, Any, Callable, Literal, Mapping, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from .models import ProposalEnvelope, ToolEnvelope

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Nonempty = Annotated[str, Field(min_length=1)]
Detail = Literal["compact", "standard", "extended"]
SHARED_INFORMATION_TOOLS = ("overview", "delta", "frontier", "search", "evidence", "impact", "history", "machine")
WORKFLOW_TOOLS = ("next_task", "inspect_task", "coordinate_task", "finish_task")
TEMPORARILY_INACTIVE = ("delegate_task", "collect_delegation")
RESPONSE_BUDGETS_BYTES = {"compact": 2048, "standard": 8192, "extended": 65536}


def relative_path(value: str) -> str:
    if (not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value)
            or "\\" in value or "\0" in value or ".." in value.split("/")):
        raise ValueError("source path must be relative without traversal")
    return value


def canonical_digest(value: Any) -> str:
    """SHA256 of canonical UTF-8 JSON; producers and consumers share one rule."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceLocator(Contract):
    project: Nonempty
    repository: Nonempty
    path: Nonempty
    content_sha256: Sha256
    revision: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    worktree: str | None = None

    _path = field_validator("path")(relative_path)

    @model_validator(mode="after")
    def ordered_range(self):
        if self.line_start is not None and self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end precedes line_start")
        return self


class ExactEntityQuery(Contract):
    kind: Nonempty
    target: Nonempty


SearchQuery = str | ExactEntityQuery
_SEARCH = TypeAdapter(SearchQuery)
R = TypeVar("R")


def route_search(query: str | Mapping[str, Any] | ExactEntityQuery, *, exact_lookup: Callable[[str, str], R], discovery: Callable[[str], R]) -> R:
    """Exact typed lookup never falls back to lexical/discovery retrieval."""
    parsed = _SEARCH.validate_python(query)
    if isinstance(parsed, ExactEntityQuery):
        return exact_lookup(parsed.kind, parsed.target)
    return discovery(parsed)


class InformationPacket(Contract):
    format: Literal["pc-packet/1"] = "pc-packet/1"
    packet_id: str = Field(min_length=8)
    alias: str = Field(pattern=r"^[a-z]+(?:-[a-z]+){1,3}$")
    created_at: str
    tool: str
    payload: dict[str, Any]
    payload_sha256: Sha256
    sources: list[SourceLocator]
    parents: list[str]
    access_scope: dict[str, Any]
    expires_at: str | None = None
    freshness: dict[str, Any] | None = None
    omissions: list[Any] | None = None
    pinned_by: list[str] | None = None

    @field_validator("created_at", "expires_at")
    @classmethod
    def timestamp(cls, value):
        if value is not None:
            if not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})",
                value,
            ):
                raise ValueError("timestamp must be RFC3339")
            # fromisoformat alone accepts arbitrary separators and offset seconds.
            # Normalize only for validation, retaining the supplied wire value.
            normalized = value[:10] + "T" + value[11:]
            if normalized.endswith(("Z", "z")):
                normalized = normalized[:-1] + "+00:00"
            if normalized[-6] in "+-" and (
                int(normalized[-5:-3]) > 23 or int(normalized[-2:]) > 59
            ):
                raise ValueError("timestamp timezone offset is invalid")
            datetime.fromisoformat(normalized)
        return value

    @model_validator(mode="after")
    def payload_identity(self):
        if canonical_digest(self.payload) != self.payload_sha256:
            raise ValueError("packet payload hash mismatch")
        if len(self.parents) != len(set(self.parents)):
            raise ValueError("packet parents must be unique")
        return self


class Finding(Contract):
    text: str
    evidence_packets: list[str] = Field(min_length=1)


class DurableJob(Contract):
    format: Literal["pc-job/1"] = "pc-job/1"
    job_id: str = Field(min_length=8)
    mode: Literal["investigate", "skill"]
    question: Nonempty
    hints: list[str]
    status: Literal["queued", "running", "yielding", "queued_after_eviction", "completed", "partial", "failed", "cancelled"]
    attempt: int = Field(ge=0)
    created_at: str
    findings: list[Finding]
    evidence_packets: list[str]
    unresolved_questions: list[str]
    project: str | None = None
    skill: str | None = None
    request_id: str | None = None
    result_packet: str | None = None
    scope: dict[str, Any] | None = None

    deadline_epoch: float | None = None
    execution_question: str | None = None
    refresh_context: dict[str, Any] | None = None
    answer: str | None = None
    failure_reason: str | None = None
    terminal_reason: str | None = None

    _timestamp = field_validator("created_at")(InformationPacket.timestamp.__func__)

    @field_validator("hints")
    @classmethod
    def unique_hints(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("hints must be unique")
        return value


class SkillResourceSelection(Contract):
    skill: str
    resource: Nonempty
    content_sha256: Sha256
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    reason: str
    prerequisites: list[str] | None = None

    _path = field_validator("resource")(relative_path)

    @model_validator(mode="after")
    def ordered_range(self):
        if self.line_end < self.line_start:
            raise ValueError("line_end precedes line_start")
        return self


class SkillSelection(Contract):
    format: Literal["pc-skill-selection/1"] = "pc-skill-selection/1"
    selections: list[SkillResourceSelection] = Field(min_length=1)
    synthesis: str
    unresolved: list[str] | None = None


class ProjectAmendment(Contract):
    format: Literal["pc-project-amendment/1"] = "pc-project-amendment/1"
    project: str
    action: Literal["register_identity", "register_relation", "register_generation", "configure_provider", "remove_registration", "record_skill_use", "update_orientation"]
    intent: Nonempty
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=8)
    payload: dict[str, Any]
    mode: Literal["preview", "apply"] | None = None


class EntityReference(Contract):
    project_uuid: str
    repository: str
    kind: str
    id: str
    path: str | None = None

    @field_validator("path")
    @classmethod
    def optional_path(cls, value):
        return relative_path(value) if value is not None else value


class ProviderInput(Contract):
    kind: Literal["file", "configuration", "provider", "exports", "manifest", "directory_membership", "registry", "environment", "semantic_revision"]
    key: str
    digest: Sha256


class ImpactEdge(Contract):
    format: Literal["pc-impact-edge/1"] = "pc-impact-edge/1"
    source: EntityReference
    target: EntityReference
    relation: str
    origin: Literal["observed", "project_declared", "candidate"]
    provider: str
    generation: str
    input_manifest: list[ProviderInput]
    resolution: Literal["resolved", "unresolved", "ambiguous", "stale"]
    witness: dict[str, Any] | None = None
    change_conditions: list[str] | None = None


class ProducerReceipt(Contract):
    format: Literal["pc-as1-producer-receipt/1"] = "pc-as1-producer-receipt/1"
    producer_project_uuid: Nonempty
    producer_task_id: Nonempty
    gate_ids: list[Nonempty] = Field(min_length=1)
    source_identity: dict[str, Any]
    contract_hashes: dict[str, Sha256]
    artifact_locators: list[SourceLocator] = Field(min_length=1)
    checked_at: str
    status: Literal["passed", "partial", "unavailable", "failed"]

    _timestamp = field_validator("checked_at")(InformationPacket.timestamp.__func__)

    @model_validator(mode="after")
    def identified(self):
        if not self.source_identity or not self.contract_hashes:
            raise ValueError("receipt needs source identity and contract hashes")
        return self

    def require_current(self, *, producer_project_uuid: str, producer_task_id: str,
                        contract_hashes: Mapping[str, str], verify_authority: Callable[["ProducerReceipt"], bool]) -> None:
        """Receiver must re-read canonical authority; this receipt grants no capability."""
        if (self.status != "passed" or self.producer_project_uuid != producer_project_uuid
                or self.producer_task_id != producer_task_id or self.contract_hashes != dict(contract_hashes)):
            raise ValueError("producer receipt mismatch or unsuccessful")
        if not verify_authority(self):
            raise ValueError("producer gate/source relevance not current")


class PacketStore(Protocol):
    """Service-private durable store. Expiry never permits alias reassignment.

    Resolve must check trusted scope before exposing payload, and return an
    explicit tombstone for expired bodies. Hints never authorize mutation.
    """
    def put(self, packet: InformationPacket) -> InformationPacket: ...
    def resolve(self, reference: str, *, access_scope: Mapping[str, Any]) -> InformationPacket | None: ...


BACKEND_REUSE = {
    "source_identity": "Git and actual working-tree bytes",
    "workflow": "todo_orchestrator.workflow.protocol.WorkflowProtocol",
    "semantic_authority": "Todo canonical semantic read/export and transactional declarations",
    "search": "services.inspect.inspect_subject canonical typed lookup plus retrieval/source_index discovery",
    "information": "app.Runtime, services information synthesis, ToolEnvelope and ProjectReconciler",
    "skills": "canonical installed Skills tree; broker exact reads",
    "model_lifecycle": "existing local-worker supervisor; observer modes only",
    "gpu_ownership": "existing CUDA host-global interlock",
    "service_state": "one shared service-private durable packet/job store (AS1 implementation pending)",
}

# Frozen from the reviewed pc-adaptive-surface/1 contract; no planning-time reads.
SURFACE = {'format': 'pc-adaptive-surface/1',
 'profiles': {'observer': {'tools': ['overview',
                                     'delta',
                                     'frontier',
                                     'search',
                                     'evidence',
                                     'impact',
                                     'history',
                                     'machine',
                                     'read',
                                     'investigate',
                                     'skill'],
                           'details': ['compact', 'standard', 'extended'],
                           'native_filesystem': False,
                           'automatic_overview': False},
              'investigator': {'tools': ['overview',
                                         'delta',
                                         'frontier',
                                         'search',
                                         'evidence',
                                         'impact',
                                         'history',
                                         'machine',
                                         'command',
                                         'log'],
                               'details': ['compact', 'standard'],
                               'native_filesystem': True,
                               'automatic_overview': False,
                               'internal': True},
              'coder': {'tools': ['overview',
                                  'delta',
                                  'frontier',
                                  'search',
                                  'evidence',
                                  'impact',
                                  'history',
                                  'machine',
                                  'next_task',
                                  'inspect_task',
                                  'coordinate_task',
                                  'finish_task'],
                        'details': ['compact', 'standard'],
                        'native_filesystem': True,
                        'automatic_overview': False,
                        'compatibility_profile_name': 'codex'},
              'mutator': {'tools': ['overview',
                                    'delta',
                                    'frontier',
                                    'search',
                                    'evidence',
                                    'impact',
                                    'history',
                                    'machine',
                                    'investigate',
                                    'next_task',
                                    'inspect_task',
                                    'coordinate_task',
                                    'finish_task',
                                    'plan',
                                    'amend_project',
                                    'maintain_execution'],
                          'details': ['compact', 'standard'],
                          'native_filesystem': True,
                          'automatic_overview': False},
              'skill_assembler': {'tools': ['overview',
                                            'delta',
                                            'frontier',
                                            'search',
                                            'evidence',
                                            'impact',
                                            'history',
                                            'machine',
                                            'command',
                                            'log'],
                                  'details': ['compact', 'standard'],
                                  'native_filesystem': True,
                                  'automatic_overview': False,
                                  'internal': True,
                                  'mode_of': 'investigator',
                                  'home': 'registered_skills_root'}},
 'shared_information_tools': ['overview',
                              'delta',
                              'frontier',
                              'search',
                              'evidence',
                              'impact',
                              'history',
                              'machine'],
 'workflow_tools': ['next_task', 'inspect_task', 'coordinate_task', 'finish_task'],
 'removed_default_names': ['project_overview',
                           'project_delta',
                           'project_frontier',
                           'architecture_context',
                           'source_context',
                           'inspect',
                           'coordination_view',
                           'program_context',
                           'history_trace',
                           'impact_preview',
                           'skill_list',
                           'skill_context',
                           'skill_read',
                           'plan_preview',
                           'apply_plan',
                           'terminal_capture',
                           'performance_probe',
                           'agent_status',
                           'performance_status',
                           'local_investigate',
                           'find'],
 'temporarily_inactive': {'delegate_task': {'preserve_implementation': True,
                                            'dispatch': 'temporarily_inactive',
                                            'reenable': 'explicit operator decision; no timed '
                                                        'reactivation'},
                          'collect_delegation': {'preserve_implementation': True,
                                                 'dispatch': 'temporarily_inactive',
                                                 'reenable': 'explicit operator decision; no timed '
                                                             'reactivation'}},
 'common_contract': {'default_detail': 'compact',
                     'proposed_response_budgets_bytes': {'compact': 2048,
                                                         'standard': 8192,
                                                         'extended': 65536},
                     'extended_profiles': ['observer'],
                     'source_paths': 'relative_only',
                     'source_locators': ['project', 'repository', 'path', 'identity'],
                     'all_information_results_packetized': True,
                     'mutation_authority_from_hints': False},
 'mutator_actions': {'plan': ['validate', 'diff', 'apply', 'amend', 'supersede', 'retire'],
                     'amend_project': ['register_identity',
                                       'register_relation',
                                       'register_generation',
                                       'configure_provider',
                                       'remove_registration',
                                       'record_skill_use',
                                       'update_orientation'],
                     'maintain_execution': ['diagnose', 'prepare', 'execute']},
 'search_contract': {'query_forms': ['discovery_query', 'exact_typed_entity'],
                     'exact_typed_entity': {'kind': 'semantic entity type',
                                            'target': 'exact canonical identifier'},
                     'exact_routing': 'existing canonical lookup directly; cheapest deterministic '
                                      'path; no unnecessary fuzzy or lexical retrieval; no '
                                      'filesystem discovery',
                     'discovery_behavior': 'unchanged',
                     'native_filesystem_discovery': 'native find/rg/Git'}}

ROLE_POLICIES = MappingProxyType({
    role: MappingProxyType({"tools": tuple(value["tools"]), "details": tuple(value["details"]),
                            "automatic_overview": False})
    for role, value in SURFACE["profiles"].items()
})
BACKEND_REUSE = MappingProxyType(BACKEND_REUSE)



def require_tool(profile: str, tool: str, detail: Detail = "compact") -> None:
    profile = "coder" if profile == "codex" else profile
    policy = ROLE_POLICIES.get(profile)
    if policy is None or tool not in policy["tools"] or detail not in policy["details"]:
        raise ValueError("tool/detail unavailable for trusted profile")


class PairedReleaseComponent(Contract):
    project_uuid: Nonempty
    repository: Nonempty
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    runtime_identity: dict[str, Any]
    receipt: ProducerReceipt


class PairedReleaseManifest(Contract):
    """Explicit standalone pairing; neither a gitlink nor a joint transaction."""
    format: Literal["pc-as1-paired-release/1"] = "pc-as1-paired-release/1"
    project_control: PairedReleaseComponent
    skills: PairedReleaseComponent
    checked_at: str

    _timestamp = field_validator("checked_at")(InformationPacket.timestamp.__func__)

    @model_validator(mode="after")
    def separate_authorities(self):
        if self.project_control.project_uuid == self.skills.project_uuid:
            raise ValueError("paired releases require separate authority identities")
        for component in (self.project_control, self.skills):
            if component.receipt.producer_project_uuid != component.project_uuid:
                raise ValueError("component receipt belongs to another authority")
            if not component.runtime_identity:
                raise ValueError("component requires verified runtime identity")
        return self


class AdoptionDecision(Contract):
    project: Nonempty
    old_task: Nonempty
    new_outcomes: list[Nonempty]
    disposition: Literal["preserve", "adopt_verified_backend", "needs_current_authority"]
    evidence: list[SourceLocator]
    authority_to_mutate: Literal[False] = False


def reconcile_existing_work(*, mappings: list[Mapping[str, Any]],
                            verified_sources: Mapping[str, list[SourceLocator]]) -> list[AdoptionDecision]:
    """Map only reviewed work; unknown entries stay preserved. Never changes Todo.

    Source adoption acknowledges reusable implementation, not task/gate success.
    Unfinished scope needs a fresh supported authority proposal at its owner.
    """
    return [AdoptionDecision(project=row["project"], old_task=row["old_task"],
                            new_outcomes=list(row["new_outcomes"]),
                            disposition="adopt_verified_backend" if verified_sources.get(row["old_task"]) else "needs_current_authority",
                            evidence=list(verified_sources.get(row["old_task"], []))) for row in mappings]
