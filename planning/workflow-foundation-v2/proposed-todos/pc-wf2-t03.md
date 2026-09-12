# PC-WF2-T03 — Provide compact defaults and explicit expansion routes

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Use small per-operation defaults, independent count and byte limits, selected-worktree identity and deduplicated references. Offer full diagnostic provenance only on explicit expansion with a bounded continuation.

## Inputs and ordering

Prerequisites: `PC-WF2-T02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/normalize.py`, `src/project_control/response_budget.py`, `src/project_control/observation_refs.py`, `src/project_control/services/source_context.py`, `src/project_control/services/overview.py`, `src/project_control/services/frontier.py`, `src/project_control/services/planning.py`, `tests/wf2_acceptance/t`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-T03-A01:** Current-task/summary replies target 8 KiB and simple unchanged deltas 2 KiB.
2. **PC-WF2-T03-A02:** Source reads default to a tested compact budget with recoverable omissions.
3. **PC-WF2-T03-A03:** Original full observer response remains available through version negotiation.

## Evidence and completion

Gate `PC-WF2-T03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/t/test_pc_wf2_t03.py` and the exact cases:

    WF2Acceptance.test_current_task_summary_replies_target_8_kib_and_simple_unchanged_deltas_2_kib
    WF2Acceptance.test_source_reads_default_to_a_tested_compact_budget_with_recoverable_omissions
    WF2Acceptance.test_original_full_observer_response_remains_available_through_version_negotiation

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
