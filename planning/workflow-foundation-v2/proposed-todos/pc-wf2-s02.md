# PC-WF2-S02 — Persist profiles with transactional semantic revisions

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Add a migration and stable serialization for task profiles/provenance. Ensure profile changes participate in semantic/task revisions, diff/export/import and context invalidation; no-op assignments do not churn revisions unnecessarily.

## Inputs and ordering

Prerequisites: `PC-WF2-S01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/models.py`, `src/project_control/workflow_core/migrations.py`, `src/project_control/workflow_core/task_profiles.py`, `src/project_control/workflow_core/profile_store.py`, `src/project_control/preledger.py`, `schemas/work-profile-v1.schema.json`, `tests/wf2_acceptance/s`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-S02-A01:** Round-trip through DB and export preserves profile exactly.
2. **PC-WF2-S02-A02:** Active affected task changes require explicit requalification or handoff.
3. **PC-WF2-S02-A03:** A profile change never grants authority or changes priority ordering.

## Evidence and completion

Gate `PC-WF2-S02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/s/test_pc_wf2_s02.py` and the exact cases:

    WF2Acceptance.test_round_trip_through_db_and_export_preserves_profile_exactly
    WF2Acceptance.test_active_affected_task_changes_require_explicit_requalification_or_handoff
    WF2Acceptance.test_a_profile_change_never_grants_authority_or_changes_priority_ordering

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
