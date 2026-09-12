# PC-WF2-D04 — Publish tested operator and controller instructions

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Update short entry instructions, granular role handoffs, recovery playbooks and cost measurements. Preserve user-wide configuration by merging only scoped keys after review; never replace the redacted config rendering.

## Inputs and ordering

Prerequisites: `PC-WF2-D03`, `PC-WF2-T06`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/agents`, `docs/CODEX_SETUP.md`, `docs/MIGRATION.md`, `tests/wf2_acceptance/d`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-D04-A01:** Operator can distinguish install, import, launch and deploy.
2. **PC-WF2-D04-A02:** Measured total usage/coverage tradeoffs are reported.
3. **PC-WF2-D04-A03:** One concise launch handoff fits within 4000 characters.

## Evidence and completion

Gate `PC-WF2-D04-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
