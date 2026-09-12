# PC-WF2-E01 — Move admin persistence queries behind typed core methods

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "deep", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Replace SQL joins outside the core with bounded workspace/integration queries, retaining the same authority checks and operation ownership.

## Inputs and ordering

Prerequisites: `PC-WF2-L05`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/admin.py`, `src/project_control/workflow_core/external_operations.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `tests/wf2_acceptance/e`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-E01-A01:** Only core code knows workflow table structure.
2. **PC-WF2-E01-A02:** Admin operations do not widen a normal claim.
3. **PC-WF2-E01-A03:** Observer inspection remains read-only.

## Evidence and completion

Gate `PC-WF2-E01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/e/test_pc_wf2_e01.py` and the exact cases:

    WF2Acceptance.test_only_core_code_knows_workflow_table_structure
    WF2Acceptance.test_admin_operations_do_not_widen_a_normal_claim
    WF2Acceptance.test_observer_inspection_remains_read_only

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
