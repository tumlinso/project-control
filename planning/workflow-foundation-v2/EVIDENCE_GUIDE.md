# Producing evidence during later implementation

This file is a recipe, not a pass receipt. Keep actual records outside both repositories. Set `WF2_BINDINGS` to a reviewed JSON file based on `machine/execution_bindings.example.json`; use actual interpreter paths, canonical authority roots and one external evidence root.

For the current task, read its entry in `machine/acceptance_matrix.json`. Implement its exact proposed test file/case names with real assertions. Do not create empty tests merely to satisfy names. Run existing regressions required by the mechanism too, and preserve those logs with source/environment identity. Commit material source before qualification.

An independent reviewer then prepares `reviews/<TASK-ID>.json` in the external evidence root using `schemas/task-review-v1.schema.json`. The record binds the current source commit, distinct author/reviewer identities, every exact assertion ID, an explanation and hashed evidence references. A reference uses exactly `domain`, `path`, `sha256`; `domain` is `source` or `evidence`, and the path is relative to the corresponding root. Unknown symlinks/traversals are rejected.

The delivered task gate runs the exact tests again in the dispatch worktree and preserves machine reports under external `runs/`. Zero tests, skips, expected failures, wrong source commits, absent artifacts and missing reviews cannot qualify an implementation. Governance tasks have no fake test count: their independent review/evidence is labeled as governance.

For a cross-repository edge, publish `wf2-capability-receipt-v1` with the matching program/edge/producer UUID/task/checkpoint, exact consumed source commit, nonempty hashed source dependencies/evidence and independent producer review. Qualified runtime edges also require actual test execution reports in the external evidence domain. Set the receipt's `qualification` exactly to the edge's `receipt_kind`; a governance statement cannot become a runtime pass. Complete the producer and checkpoint through the native workflow, then run the local consumer import task. Its gate reobserves producer authority and verifies the exact source/evidence. An assertion that a task is done is not used as authority.

The conservative delivered receipt checker requires the producer's configured source checkout at the exact consumed commit. It does not freeze the entire other repository indefinitely: use the qualified checkout/artifact at the import boundary, or obtain a new reviewed receipt. A descendant shortcut requires the separately specified dependency-aware renewal protocol, not a force flag.

The COORD and epic gates are authoritative terminal checks, not execution tests. Their obligations are checked at completion, so the coordinator can remain active while it directs the other lanes. Final I90 provides the substantive final evidence and limitations.

Checksums and structured assertions establish identity and coverage, not the truth of a fabricated experiment. Independent review must inspect actual commands, test assertions, outputs, exact source and runtime behavior. A test of this package's own guard is not a production WF2 acceptance test.
