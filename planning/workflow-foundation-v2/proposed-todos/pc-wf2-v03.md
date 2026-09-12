# PC-WF2-V03 — Attack the unified authority and compatibility boundary

**Status: proposed, not implemented.** Native role: `specialist`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "critical", "work_type": "review"}`.

## Purpose and mechanism

After feature integration, test unauthorized workflow calls, stale proposals, malformed profiles, resource vs authorization confusion, forged source receipts, read-only repairs and old-writer attempts on upgraded state.

## Inputs and ordering

Prerequisites: `PC-WF2-I30`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `tests/wf2_validation`, `docs/workflow_foundation_v2/validation`, `tests/wf2_acceptance/v`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-V03-A01:** Observer remains project-read-only even with hostile arguments.
2. **PC-WF2-V03-A02:** Rejected proposals cause no semantic mutation.
3. **PC-WF2-V03-A03:** Legacy writer policy is enforced rather than merely documented.

## Evidence and completion

Gate `PC-WF2-V03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/v/test_pc_wf2_v03.py` and the exact cases:

    WF2Acceptance.test_observer_remains_project_read_only_even_with_hostile_arguments
    WF2Acceptance.test_rejected_proposals_cause_no_semantic_mutation
    WF2Acceptance.test_legacy_writer_policy_is_enforced_rather_than_merely_documented

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
