# PC-WF2-R05 — Qualify snapshot compatibility and cost

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Compare typed/coherent outputs with frozen legacy fixtures, using explicit contract versions. Measure SQL calls and response work without conflating lower server time with token savings.

## Inputs and ordering

Prerequisites: `PC-WF2-R04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/semantic`, `src/project_control/workflow_core/read_snapshot.py`, `src/project_control/snapshot.py`, `src/project_control/models.py`, `src/project_control/reconcile.py`, `src/project_control/workflow.py`, `tests/wf2_acceptance/r`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-R05-A01:** Full meaningful output parity is demonstrated.
2. **PC-WF2-R05-A02:** Legacy observer remains usable.
3. **PC-WF2-R05-A03:** Performance report retains cold/warm costs and qualifications.

## Evidence and completion

Gate `PC-WF2-R05-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/r/test_pc_wf2_r05.py` and the exact cases:

    WF2Acceptance.test_full_meaningful_output_parity_is_demonstrated
    WF2Acceptance.test_legacy_observer_remains_usable
    WF2Acceptance.test_performance_report_retains_cold_warm_costs_and_qualifications

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
