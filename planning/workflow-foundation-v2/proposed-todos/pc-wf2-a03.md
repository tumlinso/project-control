# PC-WF2-A03 — Verify low-cost research routing before fan-out

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "cross_project", "difficulty": "complex", "risk": "high", "work_type": "inspection"}`.

## Purpose and mechanism

Use the Codex companion to stage distinct researcher, scout, implementer and reviewer profiles. Preserve the observed root model unless the operator changes it. Resolve the codex-workspace symlinks and record effective child model, effort, tool inventory and permission behavior with one bounded read-only smoke question.

## Inputs and ordering

Prerequisites: `PC-WF2-A02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/baseline`, `tests/wf2_acceptance/a`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-A03-A01:** Actual child launch metadata confirms model and effort or routing remains unverified.
2. **PC-WF2-A03-A02:** Researcher queries Project Control directly and returns a bounded evidence digest.
3. **PC-WF2-A03-A03:** No claim or source write is performed by the read-only smoke test.

## Evidence and completion

Gate `PC-WF2-A03-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
