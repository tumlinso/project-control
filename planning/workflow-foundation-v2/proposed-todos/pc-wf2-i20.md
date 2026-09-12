# PC-WF2-I20 — Integrate direct bindings and publish a candidate runtime

**Status: proposed, not implemented.** Native role: `integrator`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "integration"}`.

## Purpose and mechanism

Integrate B04, package a separately named candidate with supported facade and old-entry forwarding contract. Publish WF2-RUNTIME for Skills; no live launcher swap.

## Inputs and ordering

Prerequisites: `PC-WF2-I10`, `PC-WF2-B04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `pyproject.toml`, `uv.lock`, `packaging`, `scripts`, `src/project_control/app.py`, `src/project_control/cli.py`, `docs/workflow_foundation_v2/integration`, `README.md`, `tests/wf2_acceptance/i`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-I20-A01:** Candidate starts without sibling discovery.
2. **PC-WF2-I20-A02:** Legacy old runtime remains available.
3. **PC-WF2-I20-A03:** Supported facade receipt is published with source/build identity.

## Evidence and completion

Gate `PC-WF2-I20-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/i/test_pc_wf2_i20.py` and the exact cases:

    WF2Acceptance.test_candidate_starts_without_sibling_discovery
    WF2Acceptance.test_legacy_old_runtime_remains_available
    WF2Acceptance.test_supported_facade_receipt_is_published_with_source_build_identity

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
