# PC-WF2-V02 — Differentially qualify workflow and persistence parity

**Status: proposed, not implemented.** Native role: `specialist`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Compare plan import/no-op/diff, snapshots, claims, context, capabilities, gates, completion and recovery across the frozen old and relocated candidates. Include concurrent claims and failure-before/after-commit scenarios.

## Inputs and ordering

Prerequisites: `PC-WF2-V01`, `PC-WF2-K05`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `tests/wf2_validation`, `docs/workflow_foundation_v2/validation`, `tests/wf2_acceptance/v`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-V02-A01:** Every required scenario has a nonempty execution result.
2. **PC-WF2-V02-A02:** No missing field is normalized away as irrelevant.
3. **PC-WF2-V02-A03:** Old/new state and protocol differences are zero outside the relocation allowlist.

## Evidence and completion

Gate `PC-WF2-V02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/v/test_pc_wf2_v02.py` and the exact cases:

    WF2Acceptance.test_every_required_scenario_has_a_nonempty_execution_result
    WF2Acceptance.test_no_missing_field_is_normalized_away_as_irrelevant
    WF2Acceptance.test_old_new_state_and_protocol_differences_are_zero_outside_the_relocation_allowlist

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
