# PC-WF2-G02 — Use dependency-sensitive mutation freshness

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "critical", "work_type": "implementation"}`.

## Purpose and mechanism

Track exactly the workflow records and external observations relied upon by a proposal. Keep a conservative full-precondition fallback until complete read-set coverage is proven.

## Inputs and ordering

Prerequisites: `PC-WF2-G01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/retrieval.py`, `src/project_control/graph.py`, `src/project_control/proposals.py`, `src/project_control/services/architecture.py`, `src/project_control/services/history.py`, `src/project_control/services/program.py`, `src/project_control/query`, `tests/wf2_acceptance/g`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-G02-A01:** Unrelated source change does not invalidate a pure ledger proposal unnecessarily.
2. **PC-WF2-G02-A02:** Relevant task/interface/source change still rejects stale apply.
3. **PC-WF2-G02-A03:** Missing read-set evidence cannot weaken the fail-closed default.

## Evidence and completion

Gate `PC-WF2-G02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/g/test_pc_wf2_g02.py` and the exact cases:

    WF2Acceptance.test_unrelated_source_change_does_not_invalidate_a_pure_ledger_proposal_unnecessarily
    WF2Acceptance.test_relevant_task_interface_source_change_still_rejects_stale_apply
    WF2Acceptance.test_missing_read_set_evidence_cannot_weaken_the_fail_closed_default

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
