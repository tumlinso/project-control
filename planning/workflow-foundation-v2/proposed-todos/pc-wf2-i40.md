# PC-WF2-I40 — Accept the candidate and deployment rehearsal

**Status: proposed, not implemented.** Native role: `integrator`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "critical", "work_type": "integration"}`.

## Purpose and mechanism

Integrate all consumer-ready receipts and independent V04 results. Build one digest-bound candidate manifest, rehearse stdio/HTTP replacement and preserve old writers/DB fencing rules.

## Inputs and ordering

Prerequisites: `PC-WF2-I30`, `PC-WF2-V04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `pyproject.toml`, `uv.lock`, `packaging`, `scripts`, `src/project_control/app.py`, `src/project_control/cli.py`, `docs/workflow_foundation_v2/integration`, `README.md`, `tests/wf2_acceptance/i`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-I40-A01:** V04 and peer readiness pass.
2. **PC-WF2-I40-A02:** Rollback safety is qualified at the actual DB format.
3. **PC-WF2-I40-A03:** Candidate is deployable without editing the service currently executing the epic.

## Evidence and completion

Gate `PC-WF2-I40-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/i/test_pc_wf2_i40.py` and the exact cases:

    WF2Acceptance.test_v04_and_peer_readiness_pass
    WF2Acceptance.test_rollback_safety_is_qualified_at_the_actual_db_format
    WF2Acceptance.test_candidate_is_deployable_without_editing_the_service_currently_executing_the_epic

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
