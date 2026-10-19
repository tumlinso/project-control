"""Typed MCP transport for the standalone Todo public application API.

The adapter accepts native project/work-item/run identifiers only. Trusted
host identity, profile, provider composition and bearer tokens are resolved by
``NativeWorkflowHost`` and never appear in a tool schema.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .profiles import TODO_ADMIN_TOOL_NAMES, TODO_READ_TOOL_NAMES, TODO_WORKFLOW_TOOL_NAMES

WORKFLOW_TOOL_NAMES = TODO_WORKFLOW_TOOL_NAMES
WORKFLOW_INSTRUCTIONS = (
    "Use the native Todo tools for work planning and execution. Preview a native plan, "
    "then commit that exact preview with an idempotency key. Open a run with an explicit "
    "schedule; create or select a workspace before dispatch. Dispatch returns a public "
    "attempt reference and lease, while the narrowed attempt credential remains private "
    "to Project Control. Report and verify a candidate before authorized outcome review. "
    "Acceptance or rejection finalizes the attempt; integrate only an accepted attempt. "
    "Use finish only to safely release work with no accepted outcome. Read authority/run/context "
    "before acting when needed. Each tool "
    "accepts an optional registered repository alias; use it when a workspace has multiple "
    "Todo authorities, otherwise the workspace authority repository is selected."
)

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
)
_MUTATING = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False,
)


@dataclass(frozen=True, slots=True)
class MaintenanceHostContext:
    """Trusted startup identity for a native mutator profile."""

    recipient_principal: str


def trusted_maintenance_context(principal: str) -> MaintenanceHostContext:
    if not isinstance(principal, str) or not principal or len(principal) > 160:
        raise ValueError("maintenance_host_principal_invalid")
    return MaintenanceHostContext(principal)

# Each entry uses Python annotations to produce a precise FastMCP JSON schema.
# ``None`` defaults make an argument optional; all other values are required.
_COMMON_REV = {"expected_revision": (int | None, None)}
_RUN_CONTEXT = {"run_id": (str | None, None), "assignment_id": (str | None, None),
                "attempt_id": (str | None, None)}
_TOOL_FIELDS: dict[str, dict[str, tuple[Any, Any]]] = {
    "todo_preview_plan": {"project": (str, inspect.Parameter.empty), "package_json": (str, inspect.Parameter.empty)},
    "todo_commit_plan": {"project": (str, inspect.Parameter.empty), "package_json": (str, inspect.Parameter.empty),
                         "preview": (str | dict[str, object], inspect.Parameter.empty),
                         "idempotency_key": (str, inspect.Parameter.empty)},
    "todo_read_authority": {"project": (str, inspect.Parameter.empty), **_COMMON_REV,
                            "limit": (int, 50), "page_token": (str | None, None)},
    "todo_open_run": {"project": (str, inspect.Parameter.empty), "plan_revision_id": (str, inspect.Parameter.empty),
                       "schedule": (dict[str, object], inspect.Parameter.empty), "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_read_run": {"project": (str, inspect.Parameter.empty), "run_id": (str, inspect.Parameter.empty)},
    "todo_create_workspace": {"project": (str, inspect.Parameter.empty), "work_item_id": (str, inspect.Parameter.empty),
                              "plan_revision_id": (str | None, None), "run_id": (str | None, None),
                              "assignment_id": (str | None, None), "workspace_id": (str | None, None),
                              "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_dispatch_attempt": {"project": (str, inspect.Parameter.empty), "workspace_id": (str, inspect.Parameter.empty),
                              "work_item_id": (str | None, None), "plan_revision_id": (str | None, None),
                              "run_id": (str | None, None), "assignment_id": (str | None, None),
                              "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_integrate_attempt": {"project": (str, inspect.Parameter.empty), "attempt_id": (str, inspect.Parameter.empty),
                               "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_verify_attempt": {"project": (str, inspect.Parameter.empty), "attempt_id": (str, inspect.Parameter.empty),
                            "requirement_id": (str, inspect.Parameter.empty), "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_heartbeat_attempt": {"project": (str, inspect.Parameter.empty), "attempt_id": (str, inspect.Parameter.empty),
                               "lease_id": (str, inspect.Parameter.empty), "plan_revision_id": (str, inspect.Parameter.empty),
                               "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_resume_attempt": {"project": (str, inspect.Parameter.empty), "attempt_id": (str, inspect.Parameter.empty),
                            "lease_id": (str, inspect.Parameter.empty), "plan_revision_id": (str, inspect.Parameter.empty),
                            "idempotency_key": (str, inspect.Parameter.empty), "work_item_id": (str | None, None),
                            "run_id": (str | None, None), "assignment_id": (str | None, None), **_COMMON_REV},
    "todo_release_attempt": {"project": (str, inspect.Parameter.empty), "attempt_id": (str, inspect.Parameter.empty),
                             "lease_id": (str, inspect.Parameter.empty), "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_reserve_resource": {"project": (str, inspect.Parameter.empty), "attempt_id": (str, inspect.Parameter.empty),
                              "resource_class": (str, inspect.Parameter.empty), "units": (int, inspect.Parameter.empty),
                              "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_inspect_resource": {"project": (str, inspect.Parameter.empty), "operation_id": (str, inspect.Parameter.empty)},
    "todo_reconcile_resource": {"project": (str, inspect.Parameter.empty), "operation_id": (str, inspect.Parameter.empty),
                                "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_retry_resource": {"project": (str, inspect.Parameter.empty), "operation_id": (str, inspect.Parameter.empty),
                            "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_release_resource": {"project": (str, inspect.Parameter.empty), "operation_id": (str, inspect.Parameter.empty),
                              "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_report_outcome": {"project": (str, inspect.Parameter.empty), "attempt_id": (str, inspect.Parameter.empty),
                            "disposition": (str, inspect.Parameter.empty), "note": (str | None, None),
                            "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_read_candidate": {"project": (str, inspect.Parameter.empty), "candidate_id": (str, inspect.Parameter.empty)},
    "todo_accept_outcome": {"project": (str, inspect.Parameter.empty), "attempt_id": (str, inspect.Parameter.empty),
                            "candidate_id": (str, inspect.Parameter.empty), "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_reject_outcome": {"project": (str, inspect.Parameter.empty), "attempt_id": (str, inspect.Parameter.empty),
                            "candidate_id": (str, inspect.Parameter.empty), "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_finish_attempt": {"project": (str, inspect.Parameter.empty), "attempt_id": (str, inspect.Parameter.empty),
                            "lease_id": (str, inspect.Parameter.empty), "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_inspect_attempt": {"project": (str, inspect.Parameter.empty), "attempt_id": (str, inspect.Parameter.empty)},
    "todo_inspect_effect": {"project": (str, inspect.Parameter.empty), "operation_id": (str, inspect.Parameter.empty)},
    "todo_apply_recovery": {"project": (str, inspect.Parameter.empty), "intent": (str, inspect.Parameter.empty),
                            "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_close_run": {"project": (str, inspect.Parameter.empty), "run_id": (str, inspect.Parameter.empty),
                       "idempotency_key": (str, inspect.Parameter.empty), "schedule_digest": (str | None, None), **_COMMON_REV},
    "todo_retire_run": {"project": (str, inspect.Parameter.empty), "run_id": (str, inspect.Parameter.empty),
                        "idempotency_key": (str, inspect.Parameter.empty), "schedule_digest": (str | None, None),
                        "successor_run_id": (str | None, None), **_COMMON_REV},
    "todo_resolve_decision": {"project": (str, inspect.Parameter.empty), "plan_revision_id": (str, inspect.Parameter.empty),
                              "declaration_id": (str, inspect.Parameter.empty), "option_id": (str, inspect.Parameter.empty),
                              "rationale": (str, inspect.Parameter.empty), **_RUN_CONTEXT,
                              "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_publish_interface": {"project": (str, inspect.Parameter.empty), "plan_revision_id": (str, inspect.Parameter.empty),
                               "declaration_id": (str, inspect.Parameter.empty), "version": (str, inspect.Parameter.empty),
                               "rationale": (str, inspect.Parameter.empty), **_RUN_CONTEXT,
                               "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_accept_checkpoint": {"project": (str, inspect.Parameter.empty), "plan_revision_id": (str, inspect.Parameter.empty),
                               "declaration_id": (str, inspect.Parameter.empty), "evidence_ids": (list[str], inspect.Parameter.empty),
                               "rationale": (str, inspect.Parameter.empty), **_RUN_CONTEXT,
                               "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_release_barrier": {"project": (str, inspect.Parameter.empty), "plan_revision_id": (str, inspect.Parameter.empty),
                             "declaration_id": (str, inspect.Parameter.empty), "rationale": (str, inspect.Parameter.empty),
                             **_RUN_CONTEXT, "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_arrive_rendezvous": {"project": (str, inspect.Parameter.empty), "plan_revision_id": (str, inspect.Parameter.empty),
                               "declaration_id": (str, inspect.Parameter.empty), "participant": (dict[str, object], inspect.Parameter.empty),
                               **_RUN_CONTEXT, "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_open_blocker": {"project": (str, inspect.Parameter.empty), "plan_revision_id": (str, inspect.Parameter.empty),
                          "declaration_id": (str, inspect.Parameter.empty), "reason": (str, inspect.Parameter.empty),
                          **_RUN_CONTEXT, "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_clear_blocker": {"project": (str, inspect.Parameter.empty), "plan_revision_id": (str, inspect.Parameter.empty),
                           "declaration_id": (str, inspect.Parameter.empty), "reason": (str, inspect.Parameter.empty),
                           **_RUN_CONTEXT, "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_build_context": {"project": (str, inspect.Parameter.empty), "work_item_id": (str, inspect.Parameter.empty),
                           **_RUN_CONTEXT, "max_bytes": (int, 16384)},
    "todo_expand_context": {"project": (str, inspect.Parameter.empty), "work_item_id": (str, inspect.Parameter.empty),
                            "fragment_id": (str, inspect.Parameter.empty), "expected_digest": (str, inspect.Parameter.empty),
                            **_RUN_CONTEXT, "max_bytes": (int, 16384), "offset": (int | None, None),
                            "since_revision": (int | None, None)},
    "todo_publish_finding": {"project": (str, inspect.Parameter.empty), "work_item_id": (str, inspect.Parameter.empty),
                             "summary": (str, inspect.Parameter.empty), "source_manifest": (dict[str, object], inspect.Parameter.empty),
                             "source_fragment_refs": (list[dict[str, object]], inspect.Parameter.empty),
                             "finding_id": (str | None, None), "expected_finding_version": (int | None, None),
                             **_RUN_CONTEXT, "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_invalidate_finding": {"project": (str, inspect.Parameter.empty), "work_item_id": (str, inspect.Parameter.empty),
                                "finding_id": (str, inspect.Parameter.empty), "finding_version": (int, inspect.Parameter.empty),
                                "reason": (str, inspect.Parameter.empty), "source_manifest": (dict[str, object], inspect.Parameter.empty),
                                **_RUN_CONTEXT, "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_send_message": {"project": (str, inspect.Parameter.empty), "scope": (dict[str, object], inspect.Parameter.empty),
                          "recipients": (list[dict[str, object]], inspect.Parameter.empty), "subject": (str, inspect.Parameter.empty),
                          "message_type": (str, "status"), "body": (str | None, None),
                          "content_ref": (dict[str, object] | None, None), **_RUN_CONTEXT,
                          "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_ask_question": {"project": (str, inspect.Parameter.empty), "scope": (dict[str, object], inspect.Parameter.empty),
                          "recipients": (list[dict[str, object]], inspect.Parameter.empty), "subject": (str, inspect.Parameter.empty),
                          "body": (str | None, None), "content_ref": (dict[str, object] | None, None), **_RUN_CONTEXT,
                          "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_answer_question": {"project": (str, inspect.Parameter.empty), "question_id": (str, inspect.Parameter.empty),
                             "body": (str | None, None), "content_ref": (dict[str, object] | None, None), **_RUN_CONTEXT,
                             "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_sync_inbox": {"project": (str, inspect.Parameter.empty), "scope": (dict[str, object], inspect.Parameter.empty),
                        **_RUN_CONTEXT, "limit": (int, 50), "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_mark_messages_read": {"project": (str, inspect.Parameter.empty), "scope": (dict[str, object], inspect.Parameter.empty),
                                "through_sequence": (int, inspect.Parameter.empty), **_RUN_CONTEXT,
                                "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_inbox_status": {"project": (str, inspect.Parameter.empty), "scope": (dict[str, object], inspect.Parameter.empty), **_RUN_CONTEXT},
    "todo_read_child_execution": {"project": (str, inspect.Parameter.empty), "parent_attempt_id": (str, inspect.Parameter.empty),
                                  "execution_id": (str, inspect.Parameter.empty)},
    "todo_start_child": {"project": (str, inspect.Parameter.empty), "parent_attempt_id": (str, inspect.Parameter.empty),
                         "execution_id": (str, inspect.Parameter.empty), "logical_id": (str, inspect.Parameter.empty),
                         "scope": (dict[str, object], inspect.Parameter.empty), "actions": (list[str], inspect.Parameter.empty),
                         "idempotency_key": (str, inspect.Parameter.empty), "retry_of": (str | None, None), **_COMMON_REV},
    "todo_submit_child_candidate": {"project": (str, inspect.Parameter.empty), "parent_attempt_id": (str, inspect.Parameter.empty),
                                     "execution_id": (str, inspect.Parameter.empty), "candidate_id": (str, inspect.Parameter.empty),
                                     "summary": (str, inspect.Parameter.empty), "artifacts": (list[dict[str, object]], []),
                                     "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_review_child_candidate": {"project": (str, inspect.Parameter.empty), "parent_attempt_id": (str, inspect.Parameter.empty),
                                    "execution_id": (str, inspect.Parameter.empty), "candidate_id": (str, inspect.Parameter.empty),
                                    "decision": (str, inspect.Parameter.empty), "review_id": (str, inspect.Parameter.empty),
                                    "reason": (str | None, None), "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_cancel_child": {"project": (str, inspect.Parameter.empty), "parent_attempt_id": (str, inspect.Parameter.empty),
                          "execution_id": (str, inspect.Parameter.empty), "cancellation_id": (str, inspect.Parameter.empty),
                          "reason": (str | None, None), "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_reconcile_child": {"project": (str, inspect.Parameter.empty), "parent_attempt_id": (str, inspect.Parameter.empty),
                             "execution_id": (str, inspect.Parameter.empty), "idempotency_key": (str, inspect.Parameter.empty), **_COMMON_REV},
    "todo_search_legacy_history": {"project": (str, inspect.Parameter.empty), "query": (str, ""),
                                   "import_id": (str | None, None), "limit": (int, 100),
                                   "after": (dict[str, object] | None, None)},
    "todo_read_legacy_current_tasks": {"project": (str, inspect.Parameter.empty),
                                        "import_id": (str | None, None), "limit": (int, 100),
                                        "after": (dict[str, object] | None, None)},
    "todo_read_legacy_project_context": {"project": (str, inspect.Parameter.empty),
                                          "import_id": (str | None, None), "limit": (int, 100),
                                          "after": (dict[str, object] | None, None)},
    "todo_preview_legacy_import": {"project": (str, inspect.Parameter.empty), "snapshot": (dict[str, object], inspect.Parameter.empty),
                                   "accepted_plan_revision_id": (str, inspect.Parameter.empty)},
    "todo_import_legacy_snapshot": {"project": (str, inspect.Parameter.empty), "snapshot": (dict[str, object], inspect.Parameter.empty),
                                    "preview": (dict[str, object], inspect.Parameter.empty),
                                    "idempotency_key": (str, inspect.Parameter.empty)},
}

_DESCRIPTIONS = {
    "todo_preview_plan": "Validate a native Todo plan and return its versioned preview without committing it.",
    "todo_commit_plan": "Commit the exact reviewed native plan preview using an idempotency key.",
    "todo_read_authority": "Read the bounded versioned native Todo authority model.",
    "todo_open_run": "Open a scheduled run against an accepted native plan revision.",
    "todo_read_run": "Read the native schedule and state for one run.",
    "todo_create_workspace": "Create a native managed workspace for a work item through the configured Git provider.",
    "todo_dispatch_attempt": "Dispatch a work item into a managed workspace; the attempt bearer stays in private host storage.",
    "todo_integrate_attempt": "Integrate one accepted attempt through the configured Git provider.",
    "todo_verify_attempt": "Run a configured verifier for one attempt and requirement.",
    "todo_heartbeat_attempt": "Renew an attempt lease using its private attempt session.",
    "todo_resume_attempt": "Resume an exact attempt after reviewing its native state.",
    "todo_release_attempt": "Release an attempt lease after reviewing its state.",
    "todo_reserve_resource": "Request a logical resource permit for an attempt.",
    "todo_inspect_resource": "Read one logical resource permit operation.",
    "todo_reconcile_resource": "Reconcile an uncertain logical resource permit operation.",
    "todo_retry_resource": "Retry a logical resource operation only when its provider proves it was not applied.",
    "todo_release_resource": "Release one logical resource permit operation.",
    "todo_report_outcome": "Report an attempt candidate; reporting does not accept the outcome.",
    "todo_read_candidate": "Read one candidate report before acceptance review.",
    "todo_accept_outcome": "Accept a reviewed candidate for an attempt.",
    "todo_reject_outcome": "Reject a reviewed candidate for an attempt.",
    "todo_finish_attempt": "Safely release work with its exact lease when it has no accepted outcome; acceptance or rejection finalizes the attempt.",
    "todo_inspect_attempt": "Inspect attempt recovery state without modifying it.",
    "todo_inspect_effect": "Inspect one uncertain effect before choosing a recovery action.",
    "todo_apply_recovery": "Apply the inspected recovery intent using its complete canonical Todo API document: JSON with format `todo-orchestrator.api-document`, version 1, kind `recovery_intent`, and the inspected intent under `payload`. Do not pass only the payload mapping.",
    "todo_close_run": "Close a run whose exact schedule digest and current revision were reviewed.",
    "todo_retire_run": "Retire a run while preserving an optional successor relationship.",
    "todo_resolve_decision": "Resolve one declared decision with rationale.",
    "todo_publish_interface": "Publish a declared interface version with rationale.",
    "todo_accept_checkpoint": "Accept a declared checkpoint with evidence references and rationale.",
    "todo_release_barrier": "Release one declared barrier with rationale.",
    "todo_arrive_rendezvous": "Record one participant's arrival at a declared rendezvous.",
    "todo_open_blocker": "Open a declared blocker with a reason.",
    "todo_clear_blocker": "Clear a declared blocker with a reason.",
    "todo_build_context": "Build a bounded context capsule for one work item.",
    "todo_expand_context": "Expand one exact context fragment using its expected digest.",
    "todo_publish_finding": "Publish a context finding with an exact source manifest and fragment references.",
    "todo_invalidate_finding": "Invalidate a context finding against a new source manifest.",
    "todo_send_message": "Send a typed native workflow message to exact same-project recipients.",
    "todo_ask_question": "Ask exact same-project workflow recipients a typed question.",
    "todo_answer_question": "Answer one native workflow question.",
    "todo_sync_inbox": "Synchronize one scoped inbox and return its bounded native page.",
    "todo_mark_messages_read": "Mark messages through one exact inbox sequence as read.",
    "todo_inbox_status": "Read status for one scoped native workflow inbox.",
    "todo_read_child_execution": "Read one child execution through its parent attempt session.",
    "todo_start_child": "Delegate a bounded child execution to a trusted configured host actor; child credentials never enter MCP.",
    "todo_submit_child_candidate": "Submit a child execution candidate with immutable artifact references.",
    "todo_review_child_candidate": "Accept or reject a child candidate under the review capability.",
    "todo_cancel_child": "Request cancellation of one child execution.",
    "todo_reconcile_child": "Reconcile a child execution after its stop is confirmed.",
    "todo_search_legacy_history": "Search immutable linked legacy history in the native authority.",
    "todo_read_legacy_current_tasks": "Read paged current legacy task status mappings and completion assertions.",
    "todo_read_legacy_project_context": "Read paged sanitized legacy project declarations and their import provenance.",
    "todo_preview_legacy_import": "Preview a complete legacy snapshot import without mutating the native authority.",
    "todo_import_legacy_snapshot": "Commit a previously reviewed legacy snapshot import.",
}


def register_workflow_tools(server: FastMCP, workflow_host) -> tuple[str, ...]:
    """Register explicitly named native Todo operations under profile allowlists."""
    if workflow_host is None:
        raise ValueError("native_workflow_host_required")
    registered: list[str] = []
    admin_names = set(TODO_ADMIN_TOOL_NAMES)
    for name, fields in _TOOL_FIELDS.items():
        if name in admin_names and server.profile.value != "mutator":
            continue
        # A trusted workspace may contain more than one registered repository
        # with its own native Todo authority. The selector is an alias only;
        # callers never supply a filesystem path or authority identifier.
        fields = {**fields, "repository": (str | None, None)}
        parameters = []
        annotations = {}
        # inspect.Signature (and FastMCP's schema builder) require all required
        # parameters before optional parameters. Tool callers use named JSON
        # fields, so keep the contract and only reorder the Python wrapper.
        ordered_fields = sorted(fields.items(), key=lambda item: item[1][1] is not inspect.Parameter.empty)
        for field, (annotation, default) in ordered_fields:
            parameters.append(inspect.Parameter(field, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                                default=default, annotation=annotation))
            annotations[field] = annotation

        def invoke(_name=name, **arguments):
            return workflow_host.invoke(_name, **arguments)

        invoke.__name__ = name
        invoke.__qualname__ = name
        invoke.__annotations__ = {**annotations, "return": dict[str, object]}
        invoke.__signature__ = inspect.Signature(parameters, return_annotation=dict[str, object])
        readonly = name in TODO_READ_TOOL_NAMES or name in {"todo_preview_plan", "todo_preview_legacy_import"}
        server.add_tool(invoke, name=name,
                        description=_DESCRIPTIONS[name] +
                        " Optional repository selects a registered repository alias within the workspace; omit it to use the workspace authority repository.",
                        annotations=_READ_ONLY if readonly else _MUTATING,
                        structured_output=True)
        registered.append(name)
    return tuple(registered)
