# PC-WF2-COORD — Coordinate the accepted program; close only after final local integration

**Status: proposed, not implemented.** Native role: `coordinator`. Profile: `{"context_depth": "cross_project", "difficulty": "hard", "risk": "critical", "work_type": "architecture"}`.

## Purpose and mechanism

Hold a claimable ordinary coordinator seat and direct first-class lanes under the single strategic controller. Revalidate source, claims, receipts and resource limits. Continue coordinating while leaves run; the completion gate requires final local integration, not a claim-time dependency.

## Inputs and ordering

No task prerequisite. Revalidate source and authority before claiming.
This is a terminal obligation, not a claim-time wait: PC-WF2-I90.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: none; coordination only.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-COORD-A01:** The final local integration task and its checkpoint are successfully complete.
2. **PC-WF2-COORD-A02:** Every required producer/consumer receipt is current and unresolved blockers are recorded.
3. **PC-WF2-COORD-A03:** Owned work and explicit limitations are handed off before the aggregate epic closes.

## Evidence and completion

Gate `PC-WF2-COORD-G` invokes the delivered runner. Evidence kind is `closure`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
