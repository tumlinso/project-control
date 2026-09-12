# PC-WF2-C03 — Publish shared engine and cutover interfaces

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "cross_project", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Publish developmental contracts for read-only ctxpp query, algorithm-tagged source identity, supported host/worker facades and donor-to-recipient mapping. Agree handoff, migration fencing and rollback compatibility with the Skills stream.

## Inputs and ordering

Prerequisites: `PC-WF2-C02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/contracts`, `schemas/workflow_foundation_v2`, `tests/wf2_acceptance/c`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-C03-A01:** Contract files and owner task are explicit.
2. **PC-WF2-C03-A02:** The source-under-test/receipt ordering has no self-reference.
3. **PC-WF2-C03-A03:** The joint release closure has no mutual final-acceptance dependency.

## Evidence and completion

Gate `PC-WF2-C03-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
