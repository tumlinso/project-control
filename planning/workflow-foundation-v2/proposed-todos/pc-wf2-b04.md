# PC-WF2-B04 — Publish the qualified embedded runtime contract

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "cross_project", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Freeze supported facade/version negotiation and no-provider startup tests. Publish a receipt for Skills consumer rewiring; do not remove old live runtime yet.

## Inputs and ordering

Prerequisites: `PC-WF2-B03`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_binding.py`, `src/project_control/todo_authority.py`, `src/project_control/adapters/todo.py`, `src/project_control/workflow_tools.py`, `src/project_control/mutation.py`, `src/project_control/runtime_identity.py`, `tests/wf2_acceptance/b`, `docs/workflow_foundation_v2/contracts/runtime.md`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-B04-A01:** Skills can import/use the new facade in an isolated candidate.
2. **PC-WF2-B04-A02:** New release identity binds the product and adapter contracts.
3. **PC-WF2-B04-A03:** Unqualified candidate never overwrites the common live launcher.

## Evidence and completion

Gate `PC-WF2-B04-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
