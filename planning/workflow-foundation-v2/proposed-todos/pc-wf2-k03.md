# PC-WF2-K03 — Preserve workflow protocol and capability behavior

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Port sessions, claims, runs, lanes, dispatches, context fragments, role checks, completion and child acceptance. Keep permission resolution and transaction logic in the core.

## Inputs and ordering

Prerequisites: `PC-WF2-K02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core`, `tests/workflow_core_parity`, `tests/wf2_acceptance/k`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-K03-A01:** Protocol payloads and error codes match the old runtime on deterministic fixtures.
2. **PC-WF2-K03-A02:** Duplicate claim and wrong-scope capability requests still fail.
3. **PC-WF2-K03-A03:** No adapter writes core tables.

## Evidence and completion

Gate `PC-WF2-K03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/k/test_pc_wf2_k03.py` and the exact cases:

    WF2Acceptance.test_protocol_payloads_and_error_codes_match_the_old_runtime_on_deterministic_fixtures
    WF2Acceptance.test_duplicate_claim_and_wrong_scope_capability_requests_still_fail
    WF2Acceptance.test_no_adapter_writes_core_tables

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
