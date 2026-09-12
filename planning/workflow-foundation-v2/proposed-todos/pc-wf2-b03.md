# PC-WF2-B03 — Define forward compatibility entry points without duplicate kernels

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "cross_project", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Provide a bounded supported facade for old Python/CLI consumers. New and legacy imports must resolve to the same classes and transaction implementation in a candidate; instrument split-runtime detection.

## Inputs and ordering

Prerequisites: `PC-WF2-B02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_binding.py`, `src/project_control/todo_authority.py`, `src/project_control/adapters/todo.py`, `src/project_control/workflow_tools.py`, `src/project_control/mutation.py`, `src/project_control/runtime_identity.py`, `tests/wf2_acceptance/b`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-B03-A01:** No duplicate scheduler/capability/claim implementations.
2. **PC-WF2-B03-A02:** Mixed old/new packages fail clearly when unsafe.
3. **PC-WF2-B03-A03:** Supported legacy symbols and retirement criteria are enumerated.

## Evidence and completion

Gate `PC-WF2-B03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/b/test_pc_wf2_b03.py` and the exact cases:

    WF2Acceptance.test_no_duplicate_scheduler_capability_claim_implementations
    WF2Acceptance.test_mixed_old_new_packages_fail_clearly_when_unsafe
    WF2Acceptance.test_supported_legacy_symbols_and_retirement_criteria_are_enumerated

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
