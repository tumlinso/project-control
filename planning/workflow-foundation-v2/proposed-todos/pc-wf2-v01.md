# PC-WF2-V01 — Build the differential test harness and baseline fixtures

**Status: proposed, not implemented.** Native role: `specialist`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Drive old and relocated runtimes in separate interpreters with fresh isolated state per case. Inject deterministic clock and IDs where supported; normalize only documented nondeterminism.

## Inputs and ordering

Prerequisites: `PC-WF2-A02`, `PC-WF2-C01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `tests/wf2_validation`, `docs/workflow_foundation_v2/validation`, `tests/wf2_acceptance/v`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-V01-A01:** Harness catches a deliberately changed status and missing event.
2. **PC-WF2-V01-A02:** Fixtures never point to a registered live DB.
3. **PC-WF2-V01-A03:** Full original tests and new scenario inventory are reported separately.

## Evidence and completion

Gate `PC-WF2-V01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/v/test_pc_wf2_v01.py` and the exact cases:

    WF2Acceptance.test_harness_catches_a_deliberately_changed_status_and_missing_event
    WF2Acceptance.test_fixtures_never_point_to_a_registered_live_db
    WF2Acceptance.test_full_original_tests_and_new_scenario_inventory_are_reported_separately

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
