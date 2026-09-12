# PC-WF2-R04 — Make read caching coherent and bounded

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "performance"}`.

## Purpose and mechanism

Remove the write-only cache or implement observed read-through with correct invalidation. Separate durable revision from clock/lease freshness; include source identity, worktree and query policy in cache keys.

## Inputs and ordering

Prerequisites: `PC-WF2-R03`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/semantic`, `src/project_control/workflow_core/read_snapshot.py`, `src/project_control/snapshot.py`, `src/project_control/models.py`, `src/project_control/reconcile.py`, `src/project_control/workflow.py`, `tests/wf2_acceptance/r`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-R04-A01:** Changed source or authorization policy cannot reuse an incompatible cache.
2. **PC-WF2-R04-A02:** Restart/eviction behavior is explicit.
3. **PC-WF2-R04-A03:** Cached heartbeat validity is not frozen indefinitely.

## Evidence and completion

Gate `PC-WF2-R04-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/r/test_pc_wf2_r04.py` and the exact cases:

    WF2Acceptance.test_changed_source_or_authorization_policy_cannot_reuse_an_incompatible_cache
    WF2Acceptance.test_restart_eviction_behavior_is_explicit
    WF2Acceptance.test_cached_heartbeat_validity_is_not_frozen_indefinitely

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
