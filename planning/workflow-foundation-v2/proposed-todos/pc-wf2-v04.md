# PC-WF2-V04 — Qualify the candidate end to end and rehearse rollback

**Status: proposed, not implemented.** Native role: `specialist`. Profile: `{"context_depth": "cross_project", "difficulty": "hard", "risk": "critical", "work_type": "testing"}`.

## Purpose and mechanism

Run independent restored copies of representative Cellerator, GlassHelix, Skills and Project Control state without modifying their real DBs. Exercise bootstrap, parallel lanes, interruption, integration, compact reads and prior-release rollback compatibility.

## Inputs and ordering

Prerequisites: `PC-WF2-V03`, `PC-WF2-X03`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `tests/wf2_validation`, `docs/workflow_foundation_v2/validation`, `tests/wf2_acceptance/v`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-V04-A01:** No target fixture loses UUID, task history, event sequence or pending work.
2. **PC-WF2-V04-A02:** Rollback after schema change is either proven compatible or explicitly restore/migrate-forward only.
3. **PC-WF2-V04-A03:** Both MCP transports see the same runtime/authority after reconnect.

## Evidence and completion

Gate `PC-WF2-V04-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/v/test_pc_wf2_v04.py` and the exact cases:

    WF2Acceptance.test_no_target_fixture_loses_uuid_task_history_event_sequence_or_pending_work
    WF2Acceptance.test_rollback_after_schema_change_is_either_proven_compatible_or_explicitly_restore_migrate_forward_o
    WF2Acceptance.test_both_mcp_transports_see_the_same_runtime_authority_after_reconnect

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
