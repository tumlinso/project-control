# 10. Validation, negative controls, and honest claims

## Three distinct levels

Offline package tests validate syntax, hashes, task/profile projections, ownership and graphs. Live construction probes validate representative native schema shapes without applying anything. Full installed native validation/diff of the **exact delivered plan** is mandatory in the manual preview, followed by one explicit apply and structural verification. None of these levels means the future implementation tests have passed.

`evidence/native_plan_validation.json` records that distinction explicitly. A failed early probe is retained and explained; accepted corrected/minimal probes are not promoted into acceptance of the final 99-record program. The wrapper never hides a failed native diff behind its own validator.

## Task acceptance

`machine/acceptance_matrix.json` maps every task to three concrete assertion IDs and an evidence kind. Proposed executable test files and exact test cases are named but **not delivered as pass stubs**. Missing files, unresolved test names, an empty inventory, skips, expected-failure substitution and failing cases are non-passes. The strict runner emits actual logs/results and source identities.

Governance tasks require an external reviewed record with hashed source/evidence references for every assertion. The verifier checks record shape, identity, coverage and bytes. It cannot prove the reviewer actually understood the evidence. Independent review and V-stream substantive validation remain essential, and a JSON `accepted` field is not scientific or security proof.

Before qualification, commit source-under-test. Review/evidence files stay in an external evidence directory. Generated Todo projections may be dirty, but material source may not. Replaying a gate requires a review at the current qualified source. Cross receipts publish after producer qualification and are consumed only after fresh native task/checkpoint completion.

## Required regression families

Parity includes validation/diff/apply/no-op; task hierarchy and combined scheduling; claim contention and leases; capability/role denials; context version changes; gate completion; integration artifact/base matching; recovery; exports and read-only compatibility.

Post-parity tests include malformed nested schema/types, unknown fields, profile persistence/promotion, legacy omission preserving profiles, old-writer fencing, finalization timestamps, resource amount/capacity feasibility, cursor exhaustiveness, source configuration identity, opaque-ref expiry/restart/access isolation, whole-envelope budgets, no-op/delta behavior, mutation TOCTOU rejection, external-operation crash recovery, and observer immutability.

Ctxpp standalone qualification runs with neither Todo nor Project Control installed. Real C++ fixtures exercise the indexed/packet contract. Simulated GPU lease tests must be reported as orchestration tests, not actual GPU execution. Hardware-unavailable blocks only genuine hardware requirements; no blanket skip passes.

## Release and rollback

Use restored/sanitized copies of representative live state for migration rehearsals. Never point experimental candidate tests at actual user project databases. Require a nonempty original test inventory and separately enumerated new acceptance tests. Compare artifact/source/version identities in fresh processes; distinguish source checkout and installed artifact.

The release receipt proves actual deployment only after writer quiescence/fencing, tested backup/restore and fresh HTTP/stdio observation. After a schema upgrade, old-code rollback may require state restore; retain exact compatibility constraints. Cutover failure preserves candidates, logs and user source for recovery.
