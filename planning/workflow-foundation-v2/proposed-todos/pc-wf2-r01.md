# PC-WF2-R01 — Produce one workflow snapshot under one read transaction

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Build immutable typed records from one DB read transaction, with project UUID, revision, observation time and consistency. Preserve status/export/semantic/workflow as projections rather than competing authorities.

## Inputs and ordering

Prerequisites: `PC-WF2-S07`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/semantic`, `src/project_control/workflow_core/read_snapshot.py`, `src/project_control/snapshot.py`, `src/project_control/models.py`, `src/project_control/reconcile.py`, `src/project_control/workflow.py`, `tests/wf2_acceptance/r`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-R01-A01:** A writer racing the read yields one coherent revision or explicit failure.
2. **PC-WF2-R01-A02:** No read-time lifecycle mutation.
3. **PC-WF2-R01-A03:** Transient lease/clock validity has explicit observation time.

## Evidence and completion

Gate `PC-WF2-R01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/r/test_pc_wf2_r01.py` and the exact cases:

    WF2Acceptance.test_a_writer_racing_the_read_yields_one_coherent_revision_or_explicit_failure
    WF2Acceptance.test_no_read_time_lifecycle_mutation
    WF2Acceptance.test_transient_lease_clock_validity_has_explicit_observation_time

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
