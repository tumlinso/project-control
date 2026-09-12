# PC-WF2-B01 — Replace provider discovery with the embedded-core facade

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "deep", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Following parity approval, route workflow operations to the single internal core. Retain read/mutation adapters as semantic boundaries while removing sibling-package identity discovery from the new candidate path.

## Inputs and ordering

Prerequisites: `PC-WF2-I10`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_binding.py`, `src/project_control/todo_authority.py`, `src/project_control/adapters/todo.py`, `src/project_control/workflow_tools.py`, `src/project_control/mutation.py`, `src/project_control/runtime_identity.py`, `tests/wf2_acceptance/b`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-B01-A01:** No external todo package resolution is needed in the new product path.
2. **PC-WF2-B01-A02:** All mutations still enter the same core transaction API.
3. **PC-WF2-B01-A03:** Installed old runtime remains available until cutover.

## Evidence and completion

Gate `PC-WF2-B01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/b/test_pc_wf2_b01.py` and the exact cases:

    WF2Acceptance.test_no_external_todo_package_resolution_is_needed_in_the_new_product_path
    WF2Acceptance.test_all_mutations_still_enter_the_same_core_transaction_api
    WF2Acceptance.test_installed_old_runtime_remains_available_until_cutover

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
