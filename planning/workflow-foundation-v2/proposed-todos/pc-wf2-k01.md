# PC-WF2-K01 — Import the identified Todo package and tests without redesign

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "deep", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Consume the verified donor receipt through X01, copy the inventoried Todo package under project_control/workflow_core retaining relative layout, attribution and source mapping. Exclude live state, generated projections, caches and credentials.

## Inputs and ordering

Prerequisites: `PC-WF2-C03`, `PC-WF2-X01`, `PC-WF2-I00`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core`, `tests/workflow_core_parity`, `tests/wf2_acceptance/k`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-K01-A01:** Every moved module maps to donor commit/path/hash.
2. **PC-WF2-K01-A02:** No algorithm, status, DB schema or persistence location changes.
3. **PC-WF2-K01-A03:** New package imports do not bind the deployed authority.

## Evidence and completion

Gate `PC-WF2-K01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/k/test_pc_wf2_k01.py` and the exact cases:

    WF2Acceptance.test_every_moved_module_maps_to_donor_commit_path_hash
    WF2Acceptance.test_no_algorithm_status_db_schema_or_persistence_location_changes
    WF2Acceptance.test_new_package_imports_do_not_bind_the_deployed_authority

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
