# PC-WF2-I30 — Integrate schema, lifecycle, snapshots and efficient reads

**Status: proposed, not implemented.** Native role: `integrator`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "integration"}`.

## Purpose and mechanism

Integrate S/L/R/Q/T/G/E/D work, resolve central-file edits and run new-format and legacy protocol compatibility tests. Keep feature commits separable from the preserved-core commit.

## Inputs and ordering

Prerequisites: `PC-WF2-I20`, `PC-WF2-S07`, `PC-WF2-L05`, `PC-WF2-R05`, `PC-WF2-T06`, `PC-WF2-G03`, `PC-WF2-E03`, `PC-WF2-D04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `pyproject.toml`, `uv.lock`, `packaging`, `scripts`, `src/project_control/app.py`, `src/project_control/cli.py`, `docs/workflow_foundation_v2/integration`, `README.md`, `tests/wf2_acceptance/i`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-I30-A01:** Every feature acceptance task passes with nonempty tests.
2. **PC-WF2-I30-A02:** Strict schema, compact responses and side-effect recovery cooperate.
3. **PC-WF2-I30-A03:** Legacy observer contract is explicitly negotiated, not silently replaced.

## Evidence and completion

Gate `PC-WF2-I30-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/i/test_pc_wf2_i30.py` and the exact cases:

    WF2Acceptance.test_every_feature_acceptance_task_passes_with_nonempty_tests
    WF2Acceptance.test_strict_schema_compact_responses_and_side_effect_recovery_cooperate
    WF2Acceptance.test_legacy_observer_contract_is_explicitly_negotiated_not_silently_replaced

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
