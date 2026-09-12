# PC-WF2-S03 — Add compatibility-safe legacy plan normalization

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Keep readable v2/v3 inputs with explicit warning/report policy for tolerated legacy extras. New strict writer rejects extras. Legacy reapply without profile must preserve existing profiles; explicit clear uses documented new-format semantics.

## Inputs and ordering

Prerequisites: `PC-WF2-S02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/models.py`, `src/project_control/workflow_core/migrations.py`, `src/project_control/workflow_core/task_profiles.py`, `src/project_control/workflow_core/profile_store.py`, `src/project_control/preledger.py`, `schemas/work-profile-v1.schema.json`, `tests/wf2_acceptance/s`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-S03-A01:** Old plans cannot erase profiles accidentally.
2. **PC-WF2-S03-A02:** Unsupported old writes to upgraded state are fenced.
3. **PC-WF2-S03-A03:** Unknown vNext fields are never accepted then silently dropped.

## Evidence and completion

Gate `PC-WF2-S03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/s/test_pc_wf2_s03.py` and the exact cases:

    WF2Acceptance.test_old_plans_cannot_erase_profiles_accidentally
    WF2Acceptance.test_unsupported_old_writes_to_upgraded_state_are_fenced
    WF2Acceptance.test_unknown_vnext_fields_are_never_accepted_then_silently_dropped

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
