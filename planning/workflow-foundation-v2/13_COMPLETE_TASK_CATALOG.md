# 13. Complete task catalog

This reading copy is generated from the authored catalog; current execution state lives only in the native authority.

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


# PC-WF2-0000 — Workflow Foundation v2 — project-control aggregate

**Status: proposed, not implemented.** Native role: `coordinator`. Profile: `{"context_depth": "focused", "difficulty": "routine", "risk": "high", "work_type": "review"}`.

## Purpose and mechanism

Close the aggregate only after all local child tasks, including the ordinary coordinator, have completed successfully and final evidence is coherent. The existing native aggregate readiness rule provides the child-completion check; no prerequisite points from a child back to this epic.

## Inputs and ordering

No task prerequisite. Revalidate source and authority before claiming.
This is a terminal obligation, not a claim-time wait: PC-WF2-COORD, PC-WF2-A01, PC-WF2-A02, PC-WF2-A03, PC-WF2-C01, PC-WF2-C02, PC-WF2-C03, PC-WF2-K01, PC-WF2-K02, PC-WF2-K03, PC-WF2-K04, PC-WF2-K05, PC-WF2-V01, PC-WF2-V02, PC-WF2-V03, PC-WF2-V04, PC-WF2-B01, PC-WF2-B02, PC-WF2-B03, PC-WF2-B04, PC-WF2-S01, PC-WF2-S02, PC-WF2-S03, PC-WF2-S04, PC-WF2-S05, PC-WF2-S06, PC-WF2-S07, PC-WF2-L01, PC-WF2-L02, PC-WF2-L03, PC-WF2-L04, PC-WF2-L05, PC-WF2-R01, PC-WF2-R02, PC-WF2-R03, PC-WF2-R04, PC-WF2-R05, PC-WF2-X01, PC-WF2-X02, PC-WF2-X03, PC-WF2-X04, PC-WF2-Q01, PC-WF2-Q02, PC-WF2-Q03, PC-WF2-Q04, PC-WF2-T01, PC-WF2-T02, PC-WF2-T03, PC-WF2-T04, PC-WF2-T05, PC-WF2-T06, PC-WF2-G01, PC-WF2-G02, PC-WF2-G03, PC-WF2-E01, PC-WF2-E02, PC-WF2-E03, PC-WF2-D01, PC-WF2-D02, PC-WF2-D03, PC-WF2-D04, PC-WF2-I00, PC-WF2-I10, PC-WF2-I20, PC-WF2-I30, PC-WF2-I40, PC-WF2-I50, PC-WF2-I90.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: none; coordination only.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-0000-A01:** Every local child is successfully terminal.
2. **PC-WF2-0000-A02:** The final local milestone remains qualified and integrated.
3. **PC-WF2-0000-A03:** The paired program release identity and limitations are explicitly reported.

## Evidence and completion

Gate `PC-WF2-0000-G` invokes the delivered runner. Evidence kind is `closure`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-A01 — Freeze the current product, authority, and compatibility baseline

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "cross_project", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Observe both source trees, all relevant installed release identities, APIs, DB versions, active claims and historical WF/PCU obligations. Record which old findings are confirmed, superseded or unproven; do not treat this package's timestamps as current authority.

## Inputs and ordering

No task prerequisite. Revalidate source and authority before claiming.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/baseline`, `tests/wf2_acceptance/a`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-A01-A01:** Both UUIDs and source heads recorded with observation provenance.
2. **PC-WF2-A01-A02:** Affected paths, live claims and unknown work dispositions are enumerated.
3. **PC-WF2-A01-A03:** No source or database is rewritten.

## Evidence and completion

Gate `PC-WF2-A01-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-A02 — Establish isolated test and rollback environments

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Inventory existing test commands and counts. Create disposable fixture roots outside real authorities; retain the deployed launcher and release as the control runtime. Discover exact administrative/dispatch APIs and provision the exclusive integrator destination before producers finish.

## Inputs and ordering

Prerequisites: `PC-WF2-A01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/baseline`, `tests/wf2_acceptance/a`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-A02-A01:** Old runtime can still serve real authorities while candidates use disposable state.
2. **PC-WF2-A02-A02:** Rollback includes launcher, environment, DB backup policy and reconnect procedure.
3. **PC-WF2-A02-A03:** Test registration precedes any milestone that requires the tests.

## Evidence and completion

Gate `PC-WF2-A02-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


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


# PC-WF2-C01 — Specify the embedded-core authority boundary

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "deep", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Resolve the destination as workflow_core to avoid colliding with the existing project_control/workflow.py read module. Inventory package-owned vs external state, preserving the current internal Todo layout for the parity move.

## Inputs and ordering

Prerequisites: `PC-WF2-A01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/contracts`, `schemas/workflow_foundation_v2`, `tests/wf2_acceptance/c`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-C01-A01:** One workflow transaction authority is named.
2. **PC-WF2-C01-A02:** Git, host resources and compiler artifacts remain independent authorities.
3. **PC-WF2-C01-A03:** No baseline storage relocation or lifecycle changes are hidden in the move.

## Evidence and completion

Gate `PC-WF2-C01-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-C02 — Freeze schema, snapshot, and evidence contracts

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Define optional work_profile, explicit unknown semantics, independent schema versions, public/observed execution-attempt metadata, typed WorkflowSnapshot, source evidence and opaque observation references. Distinguish difficulty, cost policy, scheduling, authorization, and factual observation.

## Inputs and ordering

Prerequisites: `PC-WF2-C01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/contracts`, `schemas/workflow_foundation_v2`, `tests/wf2_acceptance/c`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-C02-A01:** Unknown task keys are rejected only under the new explicit strict format.
2. **PC-WF2-C02-A02:** Legacy v2/v3 plans retain a documented compatible reader.
3. **PC-WF2-C02-A03:** Observation refs are not authorization capabilities and do not hide race/coverage warnings.

## Evidence and completion

Gate `PC-WF2-C02-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


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


# PC-WF2-K02 — Preserve database transactions and durable projections

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Adapt only relocation-dependent imports. Preserve migrations, BEGIN/commit semantics, revision sequencing, events, snapshots and recovery representation; audit absolute imports and command entry points.

## Inputs and ordering

Prerequisites: `PC-WF2-K01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core`, `tests/workflow_core_parity`, `tests/wf2_acceptance/k`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-K02-A01:** DB and export fixtures round-trip with UUID/event identity preserved.
2. **PC-WF2-K02-A02:** Read-only opens do not migrate or repair.
3. **PC-WF2-K02-A03:** The compatibility matrix documents preexisting limitations.

## Evidence and completion

Gate `PC-WF2-K02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/k/test_pc_wf2_k02.py` and the exact cases:

    WF2Acceptance.test_db_and_export_fixtures_round_trip_with_uuid_event_identity_preserved
    WF2Acceptance.test_read_only_opens_do_not_migrate_or_repair
    WF2Acceptance.test_the_compatibility_matrix_documents_preexisting_limitations

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-K03 — Preserve workflow protocol and capability behavior

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Port sessions, claims, runs, lanes, dispatches, context fragments, role checks, completion and child acceptance. Keep permission resolution and transaction logic in the core.

## Inputs and ordering

Prerequisites: `PC-WF2-K02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core`, `tests/workflow_core_parity`, `tests/wf2_acceptance/k`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-K03-A01:** Protocol payloads and error codes match the old runtime on deterministic fixtures.
2. **PC-WF2-K03-A02:** Duplicate claim and wrong-scope capability requests still fail.
3. **PC-WF2-K03-A03:** No adapter writes core tables.

## Evidence and completion

Gate `PC-WF2-K03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/k/test_pc_wf2_k03.py` and the exact cases:

    WF2Acceptance.test_protocol_payloads_and_error_codes_match_the_old_runtime_on_deterministic_fixtures
    WF2Acceptance.test_duplicate_claim_and_wrong_scope_capability_requests_still_fail
    WF2Acceptance.test_no_adapter_writes_core_tables

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-K04 — Preserve sidecars and external runtime contracts

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "cross_project", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Carry supported runtime/source/artifact/host facades and required sidecars without treating their observations as workflow truth. Maintain private/public boundary and old caller behavior.

## Inputs and ordering

Prerequisites: `PC-WF2-K03`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core`, `tests/workflow_core_parity`, `tests/wf2_acceptance/k`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-K04-A01:** Host resource leases are not duplicated by the move.
2. **PC-WF2-K04-A02:** Source-identity algorithm tags and wire output are unchanged at parity.
3. **PC-WF2-K04-A03:** External job and child failure paths retain evidence and cleanup ownership.

## Evidence and completion

Gate `PC-WF2-K04-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/k/test_pc_wf2_k04.py` and the exact cases:

    WF2Acceptance.test_host_resource_leases_are_not_duplicated_by_the_move
    WF2Acceptance.test_source_identity_algorithm_tags_and_wire_output_are_unchanged_at_parity
    WF2Acceptance.test_external_job_and_child_failure_paths_retain_evidence_and_cleanup_ownership

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-K05 — Qualify the complete relocated core

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Execute the donor baseline inventory and relocation-specific tests in fresh isolated processes for old and new packages. Freeze a parity candidate; retain an explicit allowlist only for import-path and package identity differences.

## Inputs and ordering

Prerequisites: `PC-WF2-K04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core`, `tests/workflow_core_parity`, `tests/wf2_acceptance/k`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-K05-A01:** No unexplained behavioral differences or silently reduced test inventory.
2. **PC-WF2-K05-A02:** State fixtures and test logs identify both executable releases.
3. **PC-WF2-K05-A03:** The parity receipt is ready for independent review.

## Evidence and completion

Gate `PC-WF2-K05-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/k/test_pc_wf2_k05.py` and the exact cases:

    WF2Acceptance.test_no_unexplained_behavioral_differences_or_silently_reduced_test_inventory
    WF2Acceptance.test_state_fixtures_and_test_logs_identify_both_executable_releases
    WF2Acceptance.test_the_parity_receipt_is_ready_for_independent_review

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-V01 — Build the differential test harness and baseline fixtures

**Status: proposed, not implemented.** Native role: `specialist`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Drive old and relocated runtimes in separate interpreters with fresh isolated state per case. Inject deterministic clock and IDs where supported; normalize only documented nondeterminism.

## Inputs and ordering

Prerequisites: `PC-WF2-A02`, `PC-WF2-C01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `tests/wf2_validation`, `docs/workflow_foundation_v2/validation`, `tests/wf2_acceptance/v`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-V01-A01:** Harness catches a deliberately changed status and missing event.
2. **PC-WF2-V01-A02:** Fixtures never point to a registered live DB.
3. **PC-WF2-V01-A03:** Full original tests and new scenario inventory are reported separately.

## Evidence and completion

Gate `PC-WF2-V01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/v/test_pc_wf2_v01.py` and the exact cases:

    WF2Acceptance.test_harness_catches_a_deliberately_changed_status_and_missing_event
    WF2Acceptance.test_fixtures_never_point_to_a_registered_live_db
    WF2Acceptance.test_full_original_tests_and_new_scenario_inventory_are_reported_separately

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-V02 — Differentially qualify workflow and persistence parity

**Status: proposed, not implemented.** Native role: `specialist`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Compare plan import/no-op/diff, snapshots, claims, context, capabilities, gates, completion and recovery across the frozen old and relocated candidates. Include concurrent claims and failure-before/after-commit scenarios.

## Inputs and ordering

Prerequisites: `PC-WF2-V01`, `PC-WF2-K05`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `tests/wf2_validation`, `docs/workflow_foundation_v2/validation`, `tests/wf2_acceptance/v`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-V02-A01:** Every required scenario has a nonempty execution result.
2. **PC-WF2-V02-A02:** No missing field is normalized away as irrelevant.
3. **PC-WF2-V02-A03:** Old/new state and protocol differences are zero outside the relocation allowlist.

## Evidence and completion

Gate `PC-WF2-V02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/v/test_pc_wf2_v02.py` and the exact cases:

    WF2Acceptance.test_every_required_scenario_has_a_nonempty_execution_result
    WF2Acceptance.test_no_missing_field_is_normalized_away_as_irrelevant
    WF2Acceptance.test_old_new_state_and_protocol_differences_are_zero_outside_the_relocation_allowlist

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-V03 — Attack the unified authority and compatibility boundary

**Status: proposed, not implemented.** Native role: `specialist`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "critical", "work_type": "review"}`.

## Purpose and mechanism

After feature integration, test unauthorized workflow calls, stale proposals, malformed profiles, resource vs authorization confusion, forged source receipts, read-only repairs and old-writer attempts on upgraded state.

## Inputs and ordering

Prerequisites: `PC-WF2-I30`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `tests/wf2_validation`, `docs/workflow_foundation_v2/validation`, `tests/wf2_acceptance/v`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-V03-A01:** Observer remains project-read-only even with hostile arguments.
2. **PC-WF2-V03-A02:** Rejected proposals cause no semantic mutation.
3. **PC-WF2-V03-A03:** Legacy writer policy is enforced rather than merely documented.

## Evidence and completion

Gate `PC-WF2-V03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/v/test_pc_wf2_v03.py` and the exact cases:

    WF2Acceptance.test_observer_remains_project_read_only_even_with_hostile_arguments
    WF2Acceptance.test_rejected_proposals_cause_no_semantic_mutation
    WF2Acceptance.test_legacy_writer_policy_is_enforced_rather_than_merely_documented

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-V04 — Qualify the candidate end to end and rehearse rollback

**Status: proposed, not implemented.** Native role: `specialist`. Profile: `{"context_depth": "cross_project", "difficulty": "hard", "risk": "critical", "work_type": "testing"}`.

## Purpose and mechanism

Run independent restored copies of representative Cellerator, GlassHelix, Skills and Project Control state without modifying their real DBs. Exercise bootstrap, parallel lanes, interruption, integration, compact reads and prior-release rollback compatibility.

## Inputs and ordering

Prerequisites: `PC-WF2-V03`, `PC-WF2-X03`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `tests/wf2_validation`, `docs/workflow_foundation_v2/validation`, `tests/wf2_acceptance/v`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-V04-A01:** No target fixture loses UUID, task history, event sequence or pending work.
2. **PC-WF2-V04-A02:** Rollback after schema change is either proven compatible or explicitly restore/migrate-forward only.
3. **PC-WF2-V04-A03:** Both MCP transports see the same runtime/authority after reconnect.

## Evidence and completion

Gate `PC-WF2-V04-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/v/test_pc_wf2_v04.py` and the exact cases:

    WF2Acceptance.test_no_target_fixture_loses_uuid_task_history_event_sequence_or_pending_work
    WF2Acceptance.test_rollback_after_schema_change_is_either_proven_compatible_or_explicitly_restore_migrate_forward_o
    WF2Acceptance.test_both_mcp_transports_see_the_same_runtime_authority_after_reconnect

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


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


# PC-WF2-B02 — Unify native validation, preview, and apply entry points

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Make observer preview, local CLI and mutator share native validation/diff semantics, canonical digest algorithms and complete collision checking. Preserve reviewed-precondition application rather than constructing a fresh unreviewed proposal at apply time.

## Inputs and ordering

Prerequisites: `PC-WF2-B01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_binding.py`, `src/project_control/todo_authority.py`, `src/project_control/adapters/todo.py`, `src/project_control/workflow_tools.py`, `src/project_control/mutation.py`, `src/project_control/runtime_identity.py`, `tests/wf2_acceptance/b`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-B02-A01:** Exact same plan produces compatible validator/diff results through every front door.
2. **PC-WF2-B02-A02:** All entity namespaces are collision-checked, not only task IDs.
3. **PC-WF2-B02-A03:** Unknown or stale approved inputs fail closed without a retry.

## Evidence and completion

Gate `PC-WF2-B02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/b/test_pc_wf2_b02.py` and the exact cases:

    WF2Acceptance.test_exact_same_plan_produces_compatible_validator_diff_results_through_every_front_door
    WF2Acceptance.test_all_entity_namespaces_are_collision_checked_not_only_task_ids
    WF2Acceptance.test_unknown_or_stale_approved_inputs_fail_closed_without_a_retry

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


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


# PC-WF2-S01 — Implement strict work-profile and versioned plan validation

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Introduce difficulty/risk/work_type/context_depth with optional whole-object absence. Require all four when present, forbid model/reasoning/profile routing keys, and use explicit namespaced extensions. Preserve legacy execution readiness strings.

## Inputs and ordering

Prerequisites: `PC-WF2-B04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/models.py`, `src/project_control/workflow_core/migrations.py`, `src/project_control/workflow_core/task_profiles.py`, `src/project_control/workflow_core/profile_store.py`, `src/project_control/preledger.py`, `schemas/work-profile-v1.schema.json`, `tests/wf2_acceptance/s`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-S01-A01:** Invalid enum, wrong type, boolean-as-integer and unknown nested keys fail.
2. **PC-WF2-S01-A02:** Missing profile is unspecified, never silently routine.
3. **PC-WF2-S01-A03:** Plan wire version, DB migration version and profile schema version are distinct.

## Evidence and completion

Gate `PC-WF2-S01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/s/test_pc_wf2_s01.py` and the exact cases:

    WF2Acceptance.test_invalid_enum_wrong_type_boolean_as_integer_and_unknown_nested_keys_fail
    WF2Acceptance.test_missing_profile_is_unspecified_never_silently_routine
    WF2Acceptance.test_plan_wire_version_db_migration_version_and_profile_schema_version_are_distinct

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-S02 — Persist profiles with transactional semantic revisions

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Add a migration and stable serialization for task profiles/provenance. Ensure profile changes participate in semantic/task revisions, diff/export/import and context invalidation; no-op assignments do not churn revisions unnecessarily.

## Inputs and ordering

Prerequisites: `PC-WF2-S01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/models.py`, `src/project_control/workflow_core/migrations.py`, `src/project_control/workflow_core/task_profiles.py`, `src/project_control/workflow_core/profile_store.py`, `src/project_control/preledger.py`, `schemas/work-profile-v1.schema.json`, `tests/wf2_acceptance/s`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-S02-A01:** Round-trip through DB and export preserves profile exactly.
2. **PC-WF2-S02-A02:** Active affected task changes require explicit requalification or handoff.
3. **PC-WF2-S02-A03:** A profile change never grants authority or changes priority ordering.

## Evidence and completion

Gate `PC-WF2-S02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/s/test_pc_wf2_s02.py` and the exact cases:

    WF2Acceptance.test_round_trip_through_db_and_export_preserves_profile_exactly
    WF2Acceptance.test_active_affected_task_changes_require_explicit_requalification_or_handoff
    WF2Acceptance.test_a_profile_change_never_grants_authority_or_changes_priority_ordering

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-S03 — Add compatibility-safe legacy plan normalization

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Keep readable v2/v3 inputs with explicit warning/report policy for tolerated legacy extras. New strict writer rejects extras. Legacy reapply without profile must preserve existing profiles; explicit clear uses documented new-format semantics.

## Inputs and ordering

Prerequisites: `PC-WF2-S02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/models.py`, `src/project_control/workflow_core/migrations.py`, `src/project_control/workflow_core/task_profiles.py`, `src/project_control/workflow_core/profile_store.py`, `src/project_control/preledger.py`, `schemas/work-profile-v1.schema.json`, `tests/wf2_acceptance/s`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-S03-A01:** Old plans cannot erase profiles accidentally.
2. **PC-WF2-S03-A02:** Unsupported old writes to upgraded state are fenced.
3. **PC-WF2-S03-A03:** Unknown vNext fields are never accepted then silently dropped.

## Evidence and completion

Gate `PC-WF2-S03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/s/test_pc_wf2_s03.py` and the exact cases:

    WF2Acceptance.test_old_plans_cannot_erase_profiles_accidentally
    WF2Acceptance.test_unsupported_old_writes_to_upgraded_state_are_fenced
    WF2Acceptance.test_unknown_vnext_fields_are_never_accepted_then_silently_dropped

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-S04 — Expose task profiles through native dispatch and inspection

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Update authoritative task briefs, next_task/inspect_task and serial lane-head projections. Refresh stale context when capability requirements change; record requested/resolved/observed executor facts separately from task semantics.

## Inputs and ordering

Prerequisites: `PC-WF2-S03`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/models.py`, `src/project_control/workflow_core/migrations.py`, `src/project_control/workflow_core/task_profiles.py`, `src/project_control/workflow_core/profile_store.py`, `src/project_control/preledger.py`, `schemas/work-profile-v1.schema.json`, `tests/wf2_acceptance/s`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-S04-A01:** Every native current-task response carries its actual profile or explicit absence.
2. **PC-WF2-S04-A02:** No expensive model choice is stored as task difficulty.
3. **PC-WF2-S04-A03:** Reported executor configuration is labelled unverified until externally observed.

## Evidence and completion

Gate `PC-WF2-S04-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/s/test_pc_wf2_s04.py` and the exact cases:

    WF2Acceptance.test_every_native_current_task_response_carries_its_actual_profile_or_explicit_absence
    WF2Acceptance.test_no_expensive_model_choice_is_stored_as_task_difficulty
    WF2Acceptance.test_reported_executor_configuration_is_labelled_unverified_until_externally_observed

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-S05 — Promote the reviewed bootstrap profile catalog

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "integration"}`.

## Purpose and mechanism

Implement a supported administrative import/diff path and test it on disposable copies of these exact two bootstrap plans. Promote machine/work_profiles.json only after strict native support is qualified.

## Inputs and ordering

Prerequisites: `PC-WF2-S04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/models.py`, `src/project_control/workflow_core/migrations.py`, `src/project_control/workflow_core/task_profiles.py`, `src/project_control/workflow_core/profile_store.py`, `src/project_control/preledger.py`, `schemas/work-profile-v1.schema.json`, `tests/wf2_acceptance/s`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-S05-A01:** Every profile matches the sealed sidecar catalog by task ID.
2. **PC-WF2-S05-A02:** Unrelated tasks and lifecycle records remain unchanged.
3. **PC-WF2-S05-A03:** The currently held coordinator claim is deliberately refreshed or handed off.

## Evidence and completion

Gate `PC-WF2-S05-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/s/test_pc_wf2_s05.py` and the exact cases:

    WF2Acceptance.test_every_profile_matches_the_sealed_sidecar_catalog_by_task_id
    WF2Acceptance.test_unrelated_tasks_and_lifecycle_records_remain_unchanged
    WF2Acceptance.test_the_currently_held_coordinator_claim_is_deliberately_refreshed_or_handed_off

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-S06 — Lower structured pre-ledger metadata without notes encoding

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Teach the versioned frontend to preserve work_profile and structured provenance through the new writer. Keep the legacy v2 compiler mode explicit; reject attempts to lose v3 lane semantics.

## Inputs and ordering

Prerequisites: `PC-WF2-S05`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/models.py`, `src/project_control/workflow_core/migrations.py`, `src/project_control/workflow_core/task_profiles.py`, `src/project_control/workflow_core/profile_store.py`, `src/project_control/preledger.py`, `schemas/work-profile-v1.schema.json`, `tests/wf2_acceptance/s`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-S06-A01:** Task mechanisms and extension data round-trip through declared channels.
2. **PC-WF2-S06-A02:** A v3 run is never silently emitted as schema 2.
3. **PC-WF2-S06-A03:** Generated views are never accepted as parallel live task authority.

## Evidence and completion

Gate `PC-WF2-S06-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/s/test_pc_wf2_s06.py` and the exact cases:

    WF2Acceptance.test_task_mechanisms_and_extension_data_round_trip_through_declared_channels
    WF2Acceptance.test_a_v3_run_is_never_silently_emitted_as_schema_2
    WF2Acceptance.test_generated_views_are_never_accepted_as_parallel_live_task_authority

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-S07 — Qualify schema migration and profile routing invariants

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Exercise real migrations, downgrade policy, JSON schema conformance, no-op diffs, historical tasks and active claims. Publish the feature receipt and strict profile examples.

## Inputs and ordering

Prerequisites: `PC-WF2-S06`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/models.py`, `src/project_control/workflow_core/migrations.py`, `src/project_control/workflow_core/task_profiles.py`, `src/project_control/workflow_core/profile_store.py`, `src/project_control/preledger.py`, `schemas/work-profile-v1.schema.json`, `tests/wf2_acceptance/s`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-S07-A01:** Exact reviewed catalog survives promotion and export.
2. **PC-WF2-S07-A02:** Model-agnostic schema remains independent of Codex versions.
3. **PC-WF2-S07-A03:** Authorization and scheduling regression suites pass.

## Evidence and completion

Gate `PC-WF2-S07-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/s/test_pc_wf2_s07.py` and the exact cases:

    WF2Acceptance.test_exact_reviewed_catalog_survives_promotion_and_export
    WF2Acceptance.test_model_agnostic_schema_remains_independent_of_codex_versions
    WF2Acceptance.test_authorization_and_scheduling_regression_suites_pass

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-L01 — Centralize transactional run/lane reconciliation

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Define an idempotent reconcile function, invoked after completion, plan apply and recovery. Terminalization considers successful root completion, queues and unresolved integration/recovery obligations; define rootless run behavior explicitly.

## Inputs and ordering

Prerequisites: `PC-WF2-S07`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/workflow/lanes.py`, `src/project_control/workflow_core/workflow/runs.py`, `src/project_control/workflow_core/workflow/recovery.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/readiness.py`, `tests/wf2_acceptance/l`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-L01-A01:** Plan-applied terminal tasks cannot leave misleading ready lanes.
2. **PC-WF2-L01-A02:** completed_at is populated once and preserved on no-op.
3. **PC-WF2-L01-A03:** A run with unresolved required obligations is not auto-completed.

## Evidence and completion

Gate `PC-WF2-L01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/l/test_pc_wf2_l01.py` and the exact cases:

    WF2Acceptance.test_plan_applied_terminal_tasks_cannot_leave_misleading_ready_lanes
    WF2Acceptance.test_completed_at_is_populated_once_and_preserved_on_no_op
    WF2Acceptance.test_a_run_with_unresolved_required_obligations_is_not_auto_completed

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-L02 — Separate workspace usage, integration, and cleanup status

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Preserve producer artifacts and integration receipts while representing workspace operational lifecycle separately from source-merge status. Cleanup eligibility requires inactive ownership, merged evidence and owned paths.

## Inputs and ordering

Prerequisites: `PC-WF2-L01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/workflow/lanes.py`, `src/project_control/workflow_core/workflow/runs.py`, `src/project_control/workflow_core/workflow/recovery.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/readiness.py`, `tests/wf2_acceptance/l`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-L02-A01:** Closed lane does not imply deleted worktree.
2. **PC-WF2-L02-A02:** Unmerged/dirty/unknown work survives reconciliation.
3. **PC-WF2-L02-A03:** Old active workspaces receive an explicit reviewable disposition.

## Evidence and completion

Gate `PC-WF2-L02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/l/test_pc_wf2_l02.py` and the exact cases:

    WF2Acceptance.test_closed_lane_does_not_imply_deleted_worktree
    WF2Acceptance.test_unmerged_dirty_unknown_work_survives_reconciliation
    WF2Acceptance.test_old_active_workspaces_receive_an_explicit_reviewable_disposition

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-L03 — Unify resource and ownership feasibility semantics

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Compare readiness and actual acquisition for capacity>1 locks, multi-unit resources and overlapping selectors. Reuse canonical feasibility logic or mark conservative predictions clearly.

## Inputs and ordering

Prerequisites: `PC-WF2-L02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/workflow/lanes.py`, `src/project_control/workflow_core/workflow/runs.py`, `src/project_control/workflow_core/workflow/recovery.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/readiness.py`, `tests/wf2_acceptance/l`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-L03-A01:** Ready-but-unclaimable examples are fixed or explicitly diagnosed.
2. **PC-WF2-L03-A02:** Scope and resource checks remain under transaction at acquisition.
3. **PC-WF2-L03-A03:** No lexical or heuristic read model grants a lease.

## Evidence and completion

Gate `PC-WF2-L03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/l/test_pc_wf2_l03.py` and the exact cases:

    WF2Acceptance.test_ready_but_unclaimable_examples_are_fixed_or_explicitly_diagnosed
    WF2Acceptance.test_scope_and_resource_checks_remain_under_transaction_at_acquisition
    WF2Acceptance.test_no_lexical_or_heuristic_read_model_grants_a_lease

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-L04 — Harden idempotent completion and interruption recovery

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Test plan reapply, lost completion responses, claim expiry, queue advancement, interface publication and partial integration with controlled crashes. Preserve task, dispatch, attempt and artifact identities separately.

## Inputs and ordering

Prerequisites: `PC-WF2-L03`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/workflow/lanes.py`, `src/project_control/workflow_core/workflow/runs.py`, `src/project_control/workflow_core/workflow/recovery.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/readiness.py`, `tests/wf2_acceptance/l`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-L04-A01:** Retries never produce duplicate accepted completion or lost provenance.
2. **PC-WF2-L04-A02:** Recovery refuses live foreign mutable work.
3. **PC-WF2-L04-A03:** Coordinator-epic circular acquisition cannot recur.

## Evidence and completion

Gate `PC-WF2-L04-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/l/test_pc_wf2_l04.py` and the exact cases:

    WF2Acceptance.test_retries_never_produce_duplicate_accepted_completion_or_lost_provenance
    WF2Acceptance.test_recovery_refuses_live_foreign_mutable_work
    WF2Acceptance.test_coordinator_epic_circular_acquisition_cannot_recur

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-L05 — Publish lifecycle repair with historical-state diagnostics

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Provide a previewable owner operation for historical inconsistencies; keep observation reads nonmutating. Record exact repairs and before/after revisions, with old-state fixtures rather than live blanket cleanup.

## Inputs and ordering

Prerequisites: `PC-WF2-L04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/workflow/lanes.py`, `src/project_control/workflow_core/workflow/runs.py`, `src/project_control/workflow_core/workflow/recovery.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/readiness.py`, `tests/wf2_acceptance/l`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-L05-A01:** Read views explain stale historical statuses without modifying them.
2. **PC-WF2-L05-A02:** Repair is explicit, bounded and audit logged.
3. **PC-WF2-L05-A03:** No automatic deletion is coupled to run completion.

## Evidence and completion

Gate `PC-WF2-L05-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/l/test_pc_wf2_l05.py` and the exact cases:

    WF2Acceptance.test_read_views_explain_stale_historical_statuses_without_modifying_them
    WF2Acceptance.test_repair_is_explicit_bounded_and_audit_logged
    WF2Acceptance.test_no_automatic_deletion_is_coupled_to_run_completion

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-R01 — Produce one workflow snapshot under one read transaction

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Build immutable typed records from one DB read transaction, with project UUID, revision, observation time and consistency. Preserve status/export/semantic/workflow as projections rather than competing authorities.

## Inputs and ordering

Prerequisites: `PC-WF2-S07`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/semantic`, `src/project_control/workflow_core/read_snapshot.py`, `src/project_control/snapshot.py`, `src/project_control/models.py`, `src/project_control/reconcile.py`, `src/project_control/workflow.py`, `tests/wf2_acceptance/r`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-R01-A01:** A writer racing the read yields one coherent revision or explicit failure.
2. **PC-WF2-R01-A02:** No read-time lifecycle mutation.
3. **PC-WF2-R01-A03:** Transient lease/clock validity has explicit observation time.

## Evidence and completion

Gate `PC-WF2-R01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/r/test_pc_wf2_r01.py` and the exact cases:

    WF2Acceptance.test_a_writer_racing_the_read_yields_one_coherent_revision_or_explicit_failure
    WF2Acceptance.test_no_read_time_lifecycle_mutation
    WF2Acceptance.test_transient_lease_clock_validity_has_explicit_observation_time

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-R02 — Replace parallel Todo observations with shared projections

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "deep", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Migrate SnapshotBuilder and reconciliation to the core snapshot. Keep Git/source/ctxpp/host observations separately revisioned with skew rather than claiming a global atomic snapshot.

## Inputs and ordering

Prerequisites: `PC-WF2-R01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/semantic`, `src/project_control/workflow_core/read_snapshot.py`, `src/project_control/snapshot.py`, `src/project_control/models.py`, `src/project_control/reconcile.py`, `src/project_control/workflow.py`, `tests/wf2_acceptance/r`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-R02-A01:** Four separate Todo read universes are removed from the new path.
2. **PC-WF2-R02-A02:** Existing observer compatibility projection remains available.
3. **PC-WF2-R02-A03:** External authority race is not hidden by workflow consistency.

## Evidence and completion

Gate `PC-WF2-R02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/r/test_pc_wf2_r02.py` and the exact cases:

    WF2Acceptance.test_four_separate_todo_read_universes_are_removed_from_the_new_path
    WF2Acceptance.test_existing_observer_compatibility_projection_remains_available
    WF2Acceptance.test_external_authority_race_is_not_hidden_by_workflow_consistency

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-R03 — Type workflow records at internal boundaries

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Introduce Task/Run/Lane/Dispatch/Attempt/Workspace records or equivalent stable types without monolithic god objects. Distinguish raw persisted state, effective state and heuristic advisory data.

## Inputs and ordering

Prerequisites: `PC-WF2-R02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/semantic`, `src/project_control/workflow_core/read_snapshot.py`, `src/project_control/snapshot.py`, `src/project_control/models.py`, `src/project_control/reconcile.py`, `src/project_control/workflow.py`, `tests/wf2_acceptance/r`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-R03-A01:** Missing required fields fail at a precise boundary.
2. **PC-WF2-R03-A02:** Child executions cannot masquerade as first-class lanes.
3. **PC-WF2-R03-A03:** Dictionary serialization remains at transport edges.

## Evidence and completion

Gate `PC-WF2-R03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/r/test_pc_wf2_r03.py` and the exact cases:

    WF2Acceptance.test_missing_required_fields_fail_at_a_precise_boundary
    WF2Acceptance.test_child_executions_cannot_masquerade_as_first_class_lanes
    WF2Acceptance.test_dictionary_serialization_remains_at_transport_edges

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


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


# PC-WF2-R05 — Qualify snapshot compatibility and cost

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Compare typed/coherent outputs with frozen legacy fixtures, using explicit contract versions. Measure SQL calls and response work without conflating lower server time with token savings.

## Inputs and ordering

Prerequisites: `PC-WF2-R04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/semantic`, `src/project_control/workflow_core/read_snapshot.py`, `src/project_control/snapshot.py`, `src/project_control/models.py`, `src/project_control/reconcile.py`, `src/project_control/workflow.py`, `tests/wf2_acceptance/r`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-R05-A01:** Full meaningful output parity is demonstrated.
2. **PC-WF2-R05-A02:** Legacy observer remains usable.
3. **PC-WF2-R05-A03:** Performance report retains cold/warm costs and qualifications.

## Evidence and completion

Gate `PC-WF2-R05-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/r/test_pc_wf2_r05.py` and the exact cases:

    WF2Acceptance.test_full_meaningful_output_parity_is_demonstrated
    WF2Acceptance.test_legacy_observer_remains_usable
    WF2Acceptance.test_performance_report_retains_cold_warm_costs_and_qualifications

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-X01 — Verify and import the Skills donor baseline

**Status: proposed, not implemented.** Native role: `specialist`. Profile: `{"context_depth": "cross_project", "difficulty": "complex", "risk": "high", "work_type": "inspection"}`.

## Purpose and mechanism

Read WF2-DONOR receipt and fresh producer authority. Verify donor source commit, complete module/test inventory, public entry points and package digests before K01 begins.

## Inputs and ordering

Prerequisites: `PC-WF2-A02`.
External receipt edge: `WF2-DONOR`; verify fresh producer authority, not an asserted done flag.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/imports`, `tests/wf2_acceptance/x`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-X01-A01:** Producer SK-WF2-I10 is complete in the correct authority.
2. **PC-WF2-X01-A02:** Relevant source hashes and inventory agree.
3. **PC-WF2-X01-A03:** No foreign native dependency is invented.

## Evidence and completion

Gate `PC-WF2-X01-G` invokes the delivered runner. Evidence kind is `cross_receipt`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-X02 — Verify the standalone ctxpp query capability

**Status: proposed, not implemented.** Native role: `specialist`. Profile: `{"context_depth": "cross_project", "difficulty": "complex", "risk": "high", "work_type": "inspection"}`.

## Purpose and mechanism

Read WF2-CTXPP receipt and fresh producer state. Confirm read-only query operation, algorithm-tagged identity and no workflow import dependency using the real independent consumer.

## Inputs and ordering

Prerequisites: `PC-WF2-B04`.
External receipt edge: `WF2-CTXPP`; verify fresh producer authority, not an asserted done flag.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/imports`, `tests/wf2_acceptance/x`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-X02-A01:** Producer SK-WF2-I20 is authoritatively complete.
2. **PC-WF2-X02-A02:** Contract and source dependency hashes are current.
3. **PC-WF2-X02-A03:** Read-only semantics and no silent scan/refresh are proven.

## Evidence and completion

Gate `PC-WF2-X02-G` invokes the delivered runner. Evidence kind is `cross_receipt`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-X03 — Verify the Skills consumer-ready candidate

**Status: proposed, not implemented.** Native role: `specialist`. Profile: `{"context_depth": "cross_project", "difficulty": "complex", "risk": "high", "work_type": "inspection"}`.

## Purpose and mechanism

Read WF2-CONSUMERS receipt for rewired CUDA/local-worker/ctxpp consumers and forwarding shims. This is pre-cutover readiness, not a claim that the old runtime is already removed.

## Inputs and ordering

Prerequisites: `PC-WF2-I30`.
External receipt edge: `WF2-CONSUMERS`; verify fresh producer authority, not an asserted done flag.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/imports`, `tests/wf2_acceptance/x`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-X03-A01:** Producer SK-WF2-I60 is complete.
2. **PC-WF2-X03-A02:** Qualified candidate versions are compatible.
3. **PC-WF2-X03-A03:** No final-acceptance circular dependency.

## Evidence and completion

Gate `PC-WF2-X03-G` invokes the delivered runner. Evidence kind is `cross_receipt`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-X04 — Verify Skills final closure after cutover

**Status: proposed, not implemented.** Native role: `specialist`. Profile: `{"context_depth": "cross_project", "difficulty": "complex", "risk": "high", "work_type": "inspection"}`.

## Purpose and mechanism

Read WF2-SK-FINAL after the accepted runtime has been promoted and Skills has completed its final owned work. Check final source pair and retained recovery references.

## Inputs and ordering

Prerequisites: `PC-WF2-I50`.
External receipt edge: `WF2-SK-FINAL`; verify fresh producer authority, not an asserted done flag.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/imports`, `tests/wf2_acceptance/x`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-X04-A01:** Producer SK-WF2-I90 is complete.
2. **PC-WF2-X04-A02:** No obsolete duplicate kernel remains on the new runtime path.
3. **PC-WF2-X04-A03:** Legacy compatibility removal matches the agreed support window.

## Evidence and completion

Gate `PC-WF2-X04-G` invokes the delivered runner. Evidence kind is `cross_receipt`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-Q01 — Use ctxpp through a supported read-only query contract

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Consume the Skills capability at X02. Prefer indexed exact-ID/name/range/edge queries over linear JSONL scanning, with declared format support and honest fallback.

## Inputs and ordering

Prerequisites: `PC-WF2-X02`, `PC-WF2-R05`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/adapters/ctxpp.py`, `src/project_control/source_index.py`, `src/project_control/services/source_context.py`, `tests/wf2_acceptance/q`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-Q01-A01:** Cold/hot readers never refresh .ctxpp in observer mode.
2. **PC-WF2-Q01-A02:** Current/stale/partial/unsupported are distinct.
3. **PC-WF2-Q01-A03:** Ctxpp remains independently installable and optional.

## Evidence and completion

Gate `PC-WF2-Q01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/q/test_pc_wf2_q01.py` and the exact cases:

    WF2Acceptance.test_cold_hot_readers_never_refresh_ctxpp_in_observer_mode
    WF2Acceptance.test_current_stale_partial_unsupported_are_distinct
    WF2Acceptance.test_ctxpp_remains_independently_installable_and_optional

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-Q02 — Repair source pagination and search selectivity

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Replace OR-only noisy matching defaults with explicit exact/phrase/all/any modes as appropriate. Continue from the last delivered item, not the last scanned batch; preserve per-target ranges and stable query identity across pagination.

## Inputs and ordering

Prerequisites: `PC-WF2-Q01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/adapters/ctxpp.py`, `src/project_control/source_index.py`, `src/project_control/services/source_context.py`, `tests/wf2_acceptance/q`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-Q02-A01:** A truncated 50-item retrieval neither skips undelivered items nor loops.
2. **PC-WF2-Q02-A02:** Path reads have explicit continuation and no silent mid-entity truncation.
3. **PC-WF2-Q02-A03:** Queries starting with dashes do not become Git options.

## Evidence and completion

Gate `PC-WF2-Q02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/q/test_pc_wf2_q02.py` and the exact cases:

    WF2Acceptance.test_a_truncated_50_item_retrieval_neither_skips_undelivered_items_nor_loops
    WF2Acceptance.test_path_reads_have_explicit_continuation_and_no_silent_mid_entity_truncation
    WF2Acceptance.test_queries_starting_with_dashes_do_not_become_git_options

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-Q03 — Bind source evidence to actual selected bytes and worktrees

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Disambiguate metadata identity from content SHA-256; hash emitted canonical ranges and relevant dependencies. Handle configured live links safely, including target-byte changes under unchanged Git status.

## Inputs and ordering

Prerequisites: `PC-WF2-Q02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/adapters/ctxpp.py`, `src/project_control/source_index.py`, `src/project_control/services/source_context.py`, `tests/wf2_acceptance/q`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-Q03-A01:** Unchanged symlink blob cannot mask changed live target.
2. **PC-WF2-Q03-A02:** Immutable-commit queries never silently use a working-tree semantic index.
3. **PC-WF2-Q03-A03:** Raced or partially verified source is not reported as current proof.

## Evidence and completion

Gate `PC-WF2-Q03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/q/test_pc_wf2_q03.py` and the exact cases:

    WF2Acceptance.test_unchanged_symlink_blob_cannot_mask_changed_live_target
    WF2Acceptance.test_immutable_commit_queries_never_silently_use_a_working_tree_semantic_index
    WF2Acceptance.test_raced_or_partially_verified_source_is_not_reported_as_current_proof

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-Q04 — Qualify source routing precision and boundedness

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Benchmark representative exact-symbol, source-range and archaeology queries on current PC/Skills plus an independent C++ fixture. Verify result provenance, completeness and no unrequested duplicate relation lists.

## Inputs and ordering

Prerequisites: `PC-WF2-Q03`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/adapters/ctxpp.py`, `src/project_control/source_index.py`, `src/project_control/services/source_context.py`, `tests/wf2_acceptance/q`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-Q04-A01:** Useful hits are not buried under generated history.
2. **PC-WF2-Q04-A02:** Read costs and reply bytes are measured separately.
3. **PC-WF2-Q04-A03:** Missing semantics is visible rather than replaced with false caller/callee evidence.

## Evidence and completion

Gate `PC-WF2-Q04-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/q/test_pc_wf2_q04.py` and the exact cases:

    WF2Acceptance.test_useful_hits_are_not_buried_under_generated_history
    WF2Acceptance.test_read_costs_and_reply_bytes_are_measured_separately
    WF2Acceptance.test_missing_semantics_is_visible_rather_than_replaced_with_false_caller_callee_evidence

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-T01 — Implement compact observation references and retrieval

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Store immutable scoped read manifests behind opaque refs. Scope refs to workspace/principal/policy, bind content and runtime contract; explicit expiry/eviction/restart behavior must return stale or missing, never silently retarget.

## Inputs and ordering

Prerequisites: `PC-WF2-Q04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/normalize.py`, `src/project_control/response_budget.py`, `src/project_control/observation_refs.py`, `src/project_control/services/source_context.py`, `src/project_control/services/overview.py`, `src/project_control/services/frontier.py`, `src/project_control/services/planning.py`, `tests/wf2_acceptance/t`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-T01-A01:** Ordinary reads carry selected scope plus compact identity.
2. **PC-WF2-T01-A02:** Mutation resolves full reviewed preconditions server-side.
3. **PC-WF2-T01-A03:** A ref cannot grant read or write authority.

## Evidence and completion

Gate `PC-WF2-T01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/t/test_pc_wf2_t01.py` and the exact cases:

    WF2Acceptance.test_ordinary_reads_carry_selected_scope_plus_compact_identity
    WF2Acceptance.test_mutation_resolves_full_reviewed_preconditions_server_side
    WF2Acceptance.test_a_ref_cannot_grant_read_or_write_authority

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-T02 — Budget the whole serialized tool envelope semantically

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Include project identity, cursor, provenance and results in the budget. Preserve required status, freshness, coverage, warnings, identities and continuation; choose optional records before rendering.

## Inputs and ordering

Prerequisites: `PC-WF2-T01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/normalize.py`, `src/project_control/response_budget.py`, `src/project_control/observation_refs.py`, `src/project_control/services/source_context.py`, `src/project_control/services/overview.py`, `src/project_control/services/frontier.py`, `src/project_control/services/planning.py`, `tests/wf2_acceptance/t`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-T02-A01:** Two dozen irrelevant worktrees do not appear in each narrow read.
2. **PC-WF2-T02-A02:** Envelope bytes obey the negotiated cap or return an explicit required-minimum response.
3. **PC-WF2-T02-A03:** No arbitrary dictionary-key removal or misleading excerpt labels.

## Evidence and completion

Gate `PC-WF2-T02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/t/test_pc_wf2_t02.py` and the exact cases:

    WF2Acceptance.test_two_dozen_irrelevant_worktrees_do_not_appear_in_each_narrow_read
    WF2Acceptance.test_envelope_bytes_obey_the_negotiated_cap_or_return_an_explicit_required_minimum_response
    WF2Acceptance.test_no_arbitrary_dictionary_key_removal_or_misleading_excerpt_labels

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-T03 — Provide compact defaults and explicit expansion routes

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Use small per-operation defaults, independent count and byte limits, selected-worktree identity and deduplicated references. Offer full diagnostic provenance only on explicit expansion with a bounded continuation.

## Inputs and ordering

Prerequisites: `PC-WF2-T02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/normalize.py`, `src/project_control/response_budget.py`, `src/project_control/observation_refs.py`, `src/project_control/services/source_context.py`, `src/project_control/services/overview.py`, `src/project_control/services/frontier.py`, `src/project_control/services/planning.py`, `tests/wf2_acceptance/t`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-T03-A01:** Current-task/summary replies target 8 KiB and simple unchanged deltas 2 KiB.
2. **PC-WF2-T03-A02:** Source reads default to a tested compact budget with recoverable omissions.
3. **PC-WF2-T03-A03:** Original full observer response remains available through version negotiation.

## Evidence and completion

Gate `PC-WF2-T03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/t/test_pc_wf2_t03.py` and the exact cases:

    WF2Acceptance.test_current_task_summary_replies_target_8_kib_and_simple_unchanged_deltas_2_kib
    WF2Acceptance.test_source_reads_default_to_a_tested_compact_budget_with_recoverable_omissions
    WF2Acceptance.test_original_full_observer_response_remains_available_through_version_negotiation

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-T04 — Add reusable context and delta-first handoffs

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Reuse source/decision/interface references by content/version across research and implementation tasks. Avoid resending full run charters to each helper; deliver relevant deltas and a reconstruction route.

## Inputs and ordering

Prerequisites: `PC-WF2-T03`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/normalize.py`, `src/project_control/response_budget.py`, `src/project_control/observation_refs.py`, `src/project_control/services/source_context.py`, `src/project_control/services/overview.py`, `src/project_control/services/frontier.py`, `src/project_control/services/planning.py`, `tests/wf2_acceptance/t`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-T04-A01:** A child receives bounded task scope rather than the controller transcript.
2. **PC-WF2-T04-A02:** No stale context cursor conceals changed contracts.
3. **PC-WF2-T04-A03:** Full reconstruction remains possible after cache eviction.

## Evidence and completion

Gate `PC-WF2-T04-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/t/test_pc_wf2_t04.py` and the exact cases:

    WF2Acceptance.test_a_child_receives_bounded_task_scope_rather_than_the_controller_transcript
    WF2Acceptance.test_no_stale_context_cursor_conceals_changed_contracts
    WF2Acceptance.test_full_reconstruction_remains_possible_after_cache_eviction

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-T05 — Measure end-to-end token economy and answer coverage

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "performance"}`.

## Purpose and mechanism

Create a fixed workload corpus and retain old/full, new/compact and delegated-digest transcripts with tokenizer ID or clearly labelled estimate. Include child input/output, duplicated startup context, retries and root rereads.

## Inputs and ordering

Prerequisites: `PC-WF2-T04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/normalize.py`, `src/project_control/response_budget.py`, `src/project_control/observation_refs.py`, `src/project_control/services/source_context.py`, `src/project_control/services/overview.py`, `src/project_control/services/frontier.py`, `src/project_control/services/planning.py`, `tests/wf2_acceptance/t`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-T05-A01:** Compact narrow-read bytes improve by target 60 percent on the recorded corpus without dropping required facts.
2. **PC-WF2-T05-A02:** Root intake improves by target 70 percent for deep-research cases, or non-promotion is documented.
3. **PC-WF2-T05-A03:** Total model usage is reported where observable; no guaranteed subscription savings are fabricated.

## Evidence and completion

Gate `PC-WF2-T05-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/t/test_pc_wf2_t05.py` and the exact cases:

    WF2Acceptance.test_compact_narrow_read_bytes_improve_by_target_60_percent_on_the_recorded_corpus_without_dropping_r
    WF2Acceptance.test_root_intake_improves_by_target_70_percent_for_deep_research_cases_or_non_promotion_is_documented
    WF2Acceptance.test_total_model_usage_is_reported_where_observable_no_guaranteed_subscription_savings_are_fabricated

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-T06 — Qualify context economy without weakening safety

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Adversarially test tiny budgets, many worktrees, multibyte text, stale refs, multiple principals, omitted required records and restarted servers. Block promotion when compression erases a material warning.

## Inputs and ordering

Prerequisites: `PC-WF2-T05`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/normalize.py`, `src/project_control/response_budget.py`, `src/project_control/observation_refs.py`, `src/project_control/services/source_context.py`, `src/project_control/services/overview.py`, `src/project_control/services/frontier.py`, `src/project_control/services/planning.py`, `tests/wf2_acceptance/t`, `docs/workflow_foundation_v2/contracts/compact-reads.md`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-T06-A01:** All mandatory facts and negative findings survive.
2. **PC-WF2-T06-A02:** Expired refs do not become fresh approvals.
3. **PC-WF2-T06-A03:** Efficiency targets and semantic acceptance are independent gates.

## Evidence and completion

Gate `PC-WF2-T06-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/t/test_pc_wf2_t06.py` and the exact cases:

    WF2Acceptance.test_all_mandatory_facts_and_negative_findings_survive
    WF2Acceptance.test_expired_refs_do_not_become_fresh_approvals
    WF2Acceptance.test_efficiency_targets_and_semantic_acceptance_are_independent_gates

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-G01 — Build a shared typed retrieval facade

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "deep", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Unify relevance and graph traversal for architecture/history/program queries over the coherent snapshot and external evidence. Verify current filenames; proposed modules are negotiable through scope transfer.

## Inputs and ordering

Prerequisites: `PC-WF2-R05`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/retrieval.py`, `src/project_control/graph.py`, `src/project_control/proposals.py`, `src/project_control/services/architecture.py`, `src/project_control/services/history.py`, `src/project_control/services/program.py`, `src/project_control/query`, `tests/wf2_acceptance/g`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-G01-A01:** Same subject receives consistent identity and authority labels.
2. **PC-WF2-G01-A02:** Current vs historical relevance is not guessed repeatedly in each service.
3. **PC-WF2-G01-A03:** Public tools remain small projections rather than independent query engines.

## Evidence and completion

Gate `PC-WF2-G01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/g/test_pc_wf2_g01.py` and the exact cases:

    WF2Acceptance.test_same_subject_receives_consistent_identity_and_authority_labels
    WF2Acceptance.test_current_vs_historical_relevance_is_not_guessed_repeatedly_in_each_service
    WF2Acceptance.test_public_tools_remain_small_projections_rather_than_independent_query_engines

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-G02 — Use dependency-sensitive mutation freshness

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "critical", "work_type": "implementation"}`.

## Purpose and mechanism

Track exactly the workflow records and external observations relied upon by a proposal. Keep a conservative full-precondition fallback until complete read-set coverage is proven.

## Inputs and ordering

Prerequisites: `PC-WF2-G01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/retrieval.py`, `src/project_control/graph.py`, `src/project_control/proposals.py`, `src/project_control/services/architecture.py`, `src/project_control/services/history.py`, `src/project_control/services/program.py`, `src/project_control/query`, `tests/wf2_acceptance/g`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-G02-A01:** Unrelated source change does not invalidate a pure ledger proposal unnecessarily.
2. **PC-WF2-G02-A02:** Relevant task/interface/source change still rejects stale apply.
3. **PC-WF2-G02-A03:** Missing read-set evidence cannot weaken the fail-closed default.

## Evidence and completion

Gate `PC-WF2-G02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/g/test_pc_wf2_g02.py` and the exact cases:

    WF2Acceptance.test_unrelated_source_change_does_not_invalidate_a_pure_ledger_proposal_unnecessarily
    WF2Acceptance.test_relevant_task_interface_source_change_still_rejects_stale_apply
    WF2Acceptance.test_missing_read_set_evidence_cannot_weaken_the_fail_closed_default

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-G03 — Qualify common retrieval and scoped-precondition races

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Exercise shared corpus ranking, cross-workspace skew, concurrent task updates and source changes immediately before transaction. Declare external-state TOCTOU limits rather than claiming Git/SQLite atomicity.

## Inputs and ordering

Prerequisites: `PC-WF2-G02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/retrieval.py`, `src/project_control/graph.py`, `src/project_control/proposals.py`, `src/project_control/services/architecture.py`, `src/project_control/services/history.py`, `src/project_control/services/program.py`, `src/project_control/query`, `tests/wf2_acceptance/g`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-G03-A01:** Known relevant changes are never accepted on stale evidence.
2. **PC-WF2-G03-A02:** Ranking improvements retain adverse and negative findings.
3. **PC-WF2-G03-A03:** All public views cite the same authoritative entity identity.

## Evidence and completion

Gate `PC-WF2-G03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/g/test_pc_wf2_g03.py` and the exact cases:

    WF2Acceptance.test_known_relevant_changes_are_never_accepted_on_stale_evidence
    WF2Acceptance.test_ranking_improvements_retain_adverse_and_negative_findings
    WF2Acceptance.test_all_public_views_cite_the_same_authoritative_entity_identity

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-E01 — Move admin persistence queries behind typed core methods

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "deep", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Replace SQL joins outside the core with bounded workspace/integration queries, retaining the same authority checks and operation ownership.

## Inputs and ordering

Prerequisites: `PC-WF2-L05`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/admin.py`, `src/project_control/workflow_core/external_operations.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `tests/wf2_acceptance/e`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-E01-A01:** Only core code knows workflow table structure.
2. **PC-WF2-E01-A02:** Admin operations do not widen a normal claim.
3. **PC-WF2-E01-A03:** Observer inspection remains read-only.

## Evidence and completion

Gate `PC-WF2-E01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/e/test_pc_wf2_e01.py` and the exact cases:

    WF2Acceptance.test_only_core_code_knows_workflow_table_structure
    WF2Acceptance.test_admin_operations_do_not_widen_a_normal_claim
    WF2Acceptance.test_observer_inspection_remains_read_only

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-E02 — Record external worktree intent and reconciliation

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Treat Git worktree/branch actions as durable intent, observed result and reconcile states. Use idempotency keys and process identity; never hold a DB write transaction across long Git operations.

## Inputs and ordering

Prerequisites: `PC-WF2-E01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/admin.py`, `src/project_control/workflow_core/external_operations.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `tests/wf2_acceptance/e`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-E02-A01:** Crash before and after Git action can be distinguished/reconciled.
2. **PC-WF2-E02-A02:** Retry does not create duplicate or overwrite foreign worktrees.
3. **PC-WF2-E02-A03:** Filesystem/Git observations remain separate evidence.

## Evidence and completion

Gate `PC-WF2-E02-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/e/test_pc_wf2_e02.py` and the exact cases:

    WF2Acceptance.test_crash_before_and_after_git_action_can_be_distinguished_reconciled
    WF2Acceptance.test_retry_does_not_create_duplicate_or_overwrite_foreign_worktrees
    WF2Acceptance.test_filesystem_git_observations_remain_separate_evidence

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-E03 — Test partial side effects and uncertain outcomes

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "testing"}`.

## Purpose and mechanism

Inject failures around intent commit, subprocess success, receipt write, integration finalization and restart. Preserve orphan artifacts and emit bounded recovery actions.

## Inputs and ordering

Prerequisites: `PC-WF2-E02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/admin.py`, `src/project_control/workflow_core/external_operations.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `tests/wf2_acceptance/e`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-E03-A01:** Unknown outcome is not reported as rolled back.
2. **PC-WF2-E03-A02:** Recovery never deletes unpreserved source.
3. **PC-WF2-E03-A03:** Duplicate resume respects ownership and completed integration evidence.

## Evidence and completion

Gate `PC-WF2-E03-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/e/test_pc_wf2_e03.py` and the exact cases:

    WF2Acceptance.test_unknown_outcome_is_not_reported_as_rolled_back
    WF2Acceptance.test_recovery_never_deletes_unpreserved_source
    WF2Acceptance.test_duplicate_resume_respects_ownership_and_completed_integration_evidence

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-D01 — Specify research delegation and compact evidence digests

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Write role-neutral routing guidance with mandatory conclusion/evidence/uncertainty/coverage fields. Default to one researcher for an independent deep question and avoid redoing exploration in the root.

## Inputs and ordering

Prerequisites: `PC-WF2-C03`, `PC-WF2-A03`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/agents`, `docs/CODEX_SETUP.md`, `docs/MIGRATION.md`, `tests/wf2_acceptance/d`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-D01-A01:** Difficulty and context depth are used independently.
2. **PC-WF2-D01-A02:** Digest includes contradictory evidence and exact re-fetch references.
3. **PC-WF2-D01-A03:** No helper is represented as a workflow lane merely because it was spawned.

## Evidence and completion

Gate `PC-WF2-D01-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-D02 — Qualify effective Codex tools and permissions

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "cross_project", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Verify the installed Codex version against current official configuration docs. Use observer-only MCP for researchers, exclude terminal_capture and inherited workflow mutation tools, and inspect parent live override effects.

## Inputs and ordering

Prerequisites: `PC-WF2-D01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/agents`, `docs/CODEX_SETUP.md`, `docs/MIGRATION.md`, `tests/wf2_acceptance/d`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-D02-A01:** Requested and actual agent model/effort are distinct observed facts.
2. **PC-WF2-D02-A02:** Read-only instruction is never treated as security enforcement.
3. **PC-WF2-D02-A03:** Unavailable cheap model or observer path fails explicitly without expensive silent fallback.

## Evidence and completion

Gate `PC-WF2-D02-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-D03 — Integrate capability-aware routing with dispatch metadata

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Consume authoritative profiles and apply replaceable external routing policy. Record requested agent class, resolved model/effort, observed configuration and policy digest on attempts/evidence, not in the task requirement.

## Inputs and ordering

Prerequisites: `PC-WF2-D02`, `PC-WF2-S07`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `docs/workflow_foundation_v2/agents`, `docs/CODEX_SETUP.md`, `docs/MIGRATION.md`, `tests/wf2_acceptance/d`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-D03-A01:** Changing model policy requires no rewrite of task semantics.
2. **PC-WF2-D03-A02:** Critical risk invokes independent review regardless of tiny code size.
3. **PC-WF2-D03-A03:** Helpers cannot inherit parent write capabilities accidentally.

## Evidence and completion

Gate `PC-WF2-D03-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


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


# PC-WF2-I00 — Integrate baseline test hooks and freeze developmental interfaces

**Status: proposed, not implemented.** Native role: `integrator`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Integrate A/C/V01 materials and source/test hooks before core qualification. Provision phase-specific producer bases and an exclusive integration destination using observed supported APIs, not guessed commands.

## Inputs and ordering

Prerequisites: `PC-WF2-A03`, `PC-WF2-C03`, `PC-WF2-V01`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `pyproject.toml`, `uv.lock`, `packaging`, `scripts`, `src/project_control/app.py`, `src/project_control/cli.py`, `docs/workflow_foundation_v2/integration`, `README.md`, `tests/wf2_acceptance/i`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-I00-A01:** Required tests are discoverable before producer completion.
2. **PC-WF2-I00-A02:** All interfaces are owned and published by a permitted role.
3. **PC-WF2-I00-A03:** Old production release is unchanged.

## Evidence and completion

Gate `PC-WF2-I00-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-I10 — Accept the behavior-preserving embedded-core milestone

**Status: proposed, not implemented.** Native role: `integrator`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "integration"}`.

## Purpose and mechanism

Integrate K and independently reviewed differential results. Publish WF2-PARITY and freeze an exact old/new candidate pair; semantic feature changes may start only after this milestone.

## Inputs and ordering

Prerequisites: `PC-WF2-I00`, `PC-WF2-V02`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `pyproject.toml`, `uv.lock`, `packaging`, `scripts`, `src/project_control/app.py`, `src/project_control/cli.py`, `docs/workflow_foundation_v2/integration`, `README.md`, `tests/wf2_acceptance/i`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-I10-A01:** V02 passes against the donor baseline.
2. **PC-WF2-I10-A02:** No storage move, strict-schema change or lifecycle fix is included in parity.
3. **PC-WF2-I10-A03:** Independent reviewer records approved normalization differences.

## Evidence and completion

Gate `PC-WF2-I10-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/i/test_pc_wf2_i10.py` and the exact cases:

    WF2Acceptance.test_v02_passes_against_the_donor_baseline
    WF2Acceptance.test_no_storage_move_strict_schema_change_or_lifecycle_fix_is_included_in_parity
    WF2Acceptance.test_independent_reviewer_records_approved_normalization_differences

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


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


# PC-WF2-I30 — Integrate schema, lifecycle, snapshots and efficient reads

**Status: proposed, not implemented.** Native role: `integrator`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "integration"}`.

## Purpose and mechanism

Integrate S/L/R/Q/T/G/E/D work, resolve central-file edits and run new-format and legacy protocol compatibility tests. Keep feature commits separable from the preserved-core commit.

## Inputs and ordering

Prerequisites: `PC-WF2-I20`, `PC-WF2-S07`, `PC-WF2-L05`, `PC-WF2-R05`, `PC-WF2-T06`, `PC-WF2-G03`, `PC-WF2-E03`, `PC-WF2-D04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `pyproject.toml`, `uv.lock`, `packaging`, `scripts`, `src/project_control/app.py`, `src/project_control/cli.py`, `docs/workflow_foundation_v2/integration`, `README.md`, `tests/wf2_acceptance/i`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-I30-A01:** Every feature acceptance task passes with nonempty tests.
2. **PC-WF2-I30-A02:** Strict schema, compact responses and side-effect recovery cooperate.
3. **PC-WF2-I30-A03:** Legacy observer contract is explicitly negotiated, not silently replaced.

## Evidence and completion

Gate `PC-WF2-I30-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/i/test_pc_wf2_i30.py` and the exact cases:

    WF2Acceptance.test_every_feature_acceptance_task_passes_with_nonempty_tests
    WF2Acceptance.test_strict_schema_compact_responses_and_side_effect_recovery_cooperate
    WF2Acceptance.test_legacy_observer_contract_is_explicitly_negotiated_not_silently_replaced

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-I40 — Accept the candidate and deployment rehearsal

**Status: proposed, not implemented.** Native role: `integrator`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "critical", "work_type": "integration"}`.

## Purpose and mechanism

Integrate all consumer-ready receipts and independent V04 results. Build one digest-bound candidate manifest, rehearse stdio/HTTP replacement and preserve old writers/DB fencing rules.

## Inputs and ordering

Prerequisites: `PC-WF2-I30`, `PC-WF2-V04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `pyproject.toml`, `uv.lock`, `packaging`, `scripts`, `src/project_control/app.py`, `src/project_control/cli.py`, `docs/workflow_foundation_v2/integration`, `README.md`, `tests/wf2_acceptance/i`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-I40-A01:** V04 and peer readiness pass.
2. **PC-WF2-I40-A02:** Rollback safety is qualified at the actual DB format.
3. **PC-WF2-I40-A03:** Candidate is deployable without editing the service currently executing the epic.

## Evidence and completion

Gate `PC-WF2-I40-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/i/test_pc_wf2_i40.py` and the exact cases:

    WF2Acceptance.test_v04_and_peer_readiness_pass
    WF2Acceptance.test_rollback_safety_is_qualified_at_the_actual_db_format
    WF2Acceptance.test_candidate_is_deployable_without_editing_the_service_currently_executing_the_epic

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-I50 — Promote the qualified release through an explicit cutover

**Status: proposed, not implemented.** Native role: `integrator`. Profile: `{"context_depth": "cross_project", "difficulty": "complex", "risk": "critical", "work_type": "implementation"}`.

## Purpose and mechanism

Under the later controller launch mandate, use the discovered owner deployment path. Quiesce all old writers for affected authorities, back up with tested restore, swap launcher atomically where supported, restart HTTP and reconnect stdio. Record any operator-only action as a blocker, not a fabricated success.

## Inputs and ordering

Prerequisites: `PC-WF2-I40`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `pyproject.toml`, `uv.lock`, `packaging`, `scripts`, `src/project_control/app.py`, `src/project_control/cli.py`, `docs/workflow_foundation_v2/integration`, `README.md`, `tests/wf2_acceptance/i`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-I50-A01:** Fresh transports report the same accepted artifact and authority UUIDs.
2. **PC-WF2-I50-A02:** No mixed old/new writers can corrupt upgraded state.
3. **PC-WF2-I50-A03:** WF2-DEPLOYED receipt distinguishes rehearsal from actual deployment.

## Evidence and completion

Gate `PC-WF2-I50-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.


# PC-WF2-I90 — Close the paired foundation and publish the qualified source pair

**Status: proposed, not implemented.** Native role: `integrator`. Profile: `{"context_depth": "cross_project", "difficulty": "complex", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

After X04, rerun smoke/compatibility at final source heads, verify no open required integration/recovery obligations and publish final capabilities and limitations. Close COORD only after this task; close epic last.

## Inputs and ordering

Prerequisites: `PC-WF2-I50`, `PC-WF2-X04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `pyproject.toml`, `uv.lock`, `packaging`, `scripts`, `src/project_control/app.py`, `src/project_control/cli.py`, `docs/workflow_foundation_v2/integration`, `README.md`, `tests/wf2_acceptance/i`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-I90-A01:** All owned implementation has landed on the intended main branches.
2. **PC-WF2-I90-A02:** Final PC/Skills commits and Codex configuration hashes are recorded.
3. **PC-WF2-I90-A03:** Only owned merged worktrees become cleanup eligible; unrelated programs remain untouched.

## Evidence and completion

Gate `PC-WF2-I90-G` invokes the delivered runner. Evidence kind is `governance`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
