# PC-WF2-G01 — Build a shared typed retrieval facade

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "deep", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Unify relevance and graph traversal for architecture/history/program queries over the coherent snapshot and external evidence. Verify current filenames; proposed modules are negotiable through scope transfer.

## Inputs and ordering

Prerequisites: `PC-WF2-R05`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/retrieval.py`, `src/project_control/graph.py`, `src/project_control/proposals.py`, `src/project_control/services/architecture.py`, `src/project_control/services/history.py`, `src/project_control/services/program.py`, `src/project_control/query`, `tests/wf2_acceptance/g`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-G01-A01:** Same subject receives consistent identity and authority labels.
2. **PC-WF2-G01-A02:** Current vs historical relevance is not guessed repeatedly in each service.
3. **PC-WF2-G01-A03:** Public tools remain small projections rather than independent query engines.

## Evidence and completion

Gate `PC-WF2-G01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/g/test_pc_wf2_g01.py` and the exact cases:

    WF2Acceptance.test_same_subject_receives_consistent_identity_and_authority_labels
    WF2Acceptance.test_current_vs_historical_relevance_is_not_guessed_repeatedly_in_each_service
    WF2Acceptance.test_public_tools_remain_small_projections_rather_than_independent_query_engines

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
