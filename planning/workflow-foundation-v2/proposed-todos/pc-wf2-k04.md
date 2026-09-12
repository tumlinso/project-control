# PC-WF2-K04 — Preserve sidecars and external runtime contracts

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "cross_project", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Carry supported runtime/source/artifact/host facades and required sidecars without treating their observations as workflow truth. Maintain private/public boundary and old caller behavior.

## Inputs and ordering

Prerequisites: `PC-WF2-K03`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core`, `tests/workflow_core_parity`, `tests/wf2_acceptance/k`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-K04-A01:** Host resource leases are not duplicated by the move.
2. **PC-WF2-K04-A02:** Source-identity algorithm tags and wire output are unchanged at parity.
3. **PC-WF2-K04-A03:** External job and child failure paths retain evidence and cleanup ownership.

## Evidence and completion

Gate `PC-WF2-K04-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/k/test_pc_wf2_k04.py` and the exact cases:

    WF2Acceptance.test_host_resource_leases_are_not_duplicated_by_the_move
    WF2Acceptance.test_source_identity_algorithm_tags_and_wire_output_are_unchanged_at_parity
    WF2Acceptance.test_external_job_and_child_failure_paths_retain_evidence_and_cleanup_ownership

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
