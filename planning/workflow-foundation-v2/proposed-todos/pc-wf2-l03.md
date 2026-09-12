# PC-WF2-L03 — Unify resource and ownership feasibility semantics

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Compare readiness and actual acquisition for capacity>1 locks, multi-unit resources and overlapping selectors. Reuse canonical feasibility logic or mark conservative predictions clearly.

## Inputs and ordering

Prerequisites: `PC-WF2-L02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/workflow/lanes.py`, `src/project_control/workflow_core/workflow/runs.py`, `src/project_control/workflow_core/workflow/recovery.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/readiness.py`, `tests/wf2_acceptance/l`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-L03-A01:** Ready-but-unclaimable examples are fixed or explicitly diagnosed.
2. **PC-WF2-L03-A02:** Scope and resource checks remain under transaction at acquisition.
3. **PC-WF2-L03-A03:** No lexical or heuristic read model grants a lease.

## Evidence and completion

Gate `PC-WF2-L03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/l/test_pc_wf2_l03.py` and the exact cases:

    WF2Acceptance.test_ready_but_unclaimable_examples_are_fixed_or_explicitly_diagnosed
    WF2Acceptance.test_scope_and_resource_checks_remain_under_transaction_at_acquisition
    WF2Acceptance.test_no_lexical_or_heuristic_read_model_grants_a_lease

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
