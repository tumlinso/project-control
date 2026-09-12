# PC-WF2-R02 — Replace parallel Todo observations with shared projections

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "deep", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Migrate SnapshotBuilder and reconciliation to the core snapshot. Keep Git/source/ctxpp/host observations separately revisioned with skew rather than claiming a global atomic snapshot.

## Inputs and ordering

Prerequisites: `PC-WF2-R01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/semantic`, `src/project_control/workflow_core/read_snapshot.py`, `src/project_control/snapshot.py`, `src/project_control/models.py`, `src/project_control/reconcile.py`, `src/project_control/workflow.py`, `tests/wf2_acceptance/r`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-R02-A01:** Four separate Todo read universes are removed from the new path.
2. **PC-WF2-R02-A02:** Existing observer compatibility projection remains available.
3. **PC-WF2-R02-A03:** External authority race is not hidden by workflow consistency.

## Evidence and completion

Gate `PC-WF2-R02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/r/test_pc_wf2_r02.py` and the exact cases:

    WF2Acceptance.test_four_separate_todo_read_universes_are_removed_from_the_new_path
    WF2Acceptance.test_existing_observer_compatibility_projection_remains_available
    WF2Acceptance.test_external_authority_race_is_not_hidden_by_workflow_consistency

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
