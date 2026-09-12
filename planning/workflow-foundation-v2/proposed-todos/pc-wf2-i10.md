# PC-WF2-I10 — Accept the behavior-preserving embedded-core milestone

**Status: proposed, not implemented.** Native role: `integrator`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "integration"}`.

## Purpose and mechanism

Integrate K and independently reviewed differential results. Publish WF2-PARITY and freeze an exact old/new candidate pair; semantic feature changes may start only after this milestone.

## Inputs and ordering

Prerequisites: `PC-WF2-I00`, `PC-WF2-V02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `pyproject.toml`, `uv.lock`, `packaging`, `scripts`, `src/project_control/app.py`, `src/project_control/cli.py`, `docs/workflow_foundation_v2/integration`, `README.md`, `tests/wf2_acceptance/i`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-I10-A01:** V02 passes against the donor baseline.
2. **PC-WF2-I10-A02:** No storage move, strict-schema change or lifecycle fix is included in parity.
3. **PC-WF2-I10-A03:** Independent reviewer records approved normalization differences.

## Evidence and completion

Gate `PC-WF2-I10-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/i/test_pc_wf2_i10.py` and the exact cases:

    WF2Acceptance.test_v02_passes_against_the_donor_baseline
    WF2Acceptance.test_no_storage_move_strict_schema_change_or_lifecycle_fix_is_included_in_parity
    WF2Acceptance.test_independent_reviewer_records_approved_normalization_differences

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
