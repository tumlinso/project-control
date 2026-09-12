# PC-WF2-E02 — Record external worktree intent and reconciliation

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Treat Git worktree/branch actions as durable intent, observed result and reconcile states. Use idempotency keys and process identity; never hold a DB write transaction across long Git operations.

## Inputs and ordering

Prerequisites: `PC-WF2-E01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/admin.py`, `src/project_control/workflow_core/external_operations.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `tests/wf2_acceptance/e`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-E02-A01:** Crash before and after Git action can be distinguished/reconciled.
2. **PC-WF2-E02-A02:** Retry does not create duplicate or overwrite foreign worktrees.
3. **PC-WF2-E02-A03:** Filesystem/Git observations remain separate evidence.

## Evidence and completion

Gate `PC-WF2-E02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/e/test_pc_wf2_e02.py` and the exact cases:

    WF2Acceptance.test_crash_before_and_after_git_action_can_be_distinguished_reconciled
    WF2Acceptance.test_retry_does_not_create_duplicate_or_overwrite_foreign_worktrees
    WF2Acceptance.test_filesystem_git_observations_remain_separate_evidence

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
