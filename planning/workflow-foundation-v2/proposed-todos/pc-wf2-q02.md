# PC-WF2-Q02 — Repair source pagination and search selectivity

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Replace OR-only noisy matching defaults with explicit exact/phrase/all/any modes as appropriate. Continue from the last delivered item, not the last scanned batch; preserve per-target ranges and stable query identity across pagination.

## Inputs and ordering

Prerequisites: `PC-WF2-Q01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/adapters/ctxpp.py`, `src/project_control/source_index.py`, `src/project_control/services/source_context.py`, `tests/wf2_acceptance/q`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-Q02-A01:** A truncated 50-item retrieval neither skips undelivered items nor loops.
2. **PC-WF2-Q02-A02:** Path reads have explicit continuation and no silent mid-entity truncation.
3. **PC-WF2-Q02-A03:** Queries starting with dashes do not become Git options.

## Evidence and completion

Gate `PC-WF2-Q02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/q/test_pc_wf2_q02.py` and the exact cases:

    WF2Acceptance.test_a_truncated_50_item_retrieval_neither_skips_undelivered_items_nor_loops
    WF2Acceptance.test_path_reads_have_explicit_continuation_and_no_silent_mid_entity_truncation
    WF2Acceptance.test_queries_starting_with_dashes_do_not_become_git_options

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
