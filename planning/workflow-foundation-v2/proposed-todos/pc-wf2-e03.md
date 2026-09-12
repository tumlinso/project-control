# PC-WF2-E03 — Test partial side effects and uncertain outcomes

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Inject failures around intent commit, subprocess success, receipt write, integration finalization and restart. Preserve orphan artifacts and emit bounded recovery actions.

## Inputs and ordering

Prerequisites: `PC-WF2-E02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/admin.py`, `src/project_control/workflow_core/external_operations.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `tests/wf2_acceptance/e`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-E03-A01:** Unknown outcome is not reported as rolled back.
2. **PC-WF2-E03-A02:** Recovery never deletes unpreserved source.
3. **PC-WF2-E03-A03:** Duplicate resume respects ownership and completed integration evidence.

## Evidence and completion

Gate `PC-WF2-E03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/e/test_pc_wf2_e03.py` and the exact cases:

    WF2Acceptance.test_unknown_outcome_is_not_reported_as_rolled_back
    WF2Acceptance.test_recovery_never_deletes_unpreserved_source
    WF2Acceptance.test_duplicate_resume_respects_ownership_and_completed_integration_evidence

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
