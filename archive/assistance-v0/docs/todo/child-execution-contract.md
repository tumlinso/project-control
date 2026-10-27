# Child execution contract

Child executions are bounded helpers subordinate to one todo task. They are not tasks, claims, architects, or completion authorities. A child token remains invalid for `complete`, `handoff`, `release`, scope expansion, recursive delegation, commits, and pushes.

The parent authorizes an objective, exclusive subscopes, and zero or more gate IDs with `todo child create`. A child may heartbeat, report one terminal result, and pass its restricted token to the existing `todo gate run GATE --claim-token TOKEN` command. Gate execution rejects any gate not explicitly authorized for that child or owned by another task.

Child gate success creates candidate evidence and artifact files but does not set the parent gate valid, open barriers, or complete the task. After the child reports `succeeded`, the next parent capsule contains a `TODO-CHILD-RESULT/1` record and guarded acceptance commands. A parent claim accepts candidate evidence by running the same gate command with its claim token. Acceptance succeeds without re-execution only when the candidate's complete input fingerprint still matches current canonical source. Otherwise the ordinary gate runs against current source and the stale candidate becomes `superseded`.

Successful child reports map to `completed` when changed paths are present and `no_change` otherwise. `needs_codex` is a successful hand-back requiring frontier judgment, not a child failure. Exhausted failures map to `failed`. Capsule results include bounded summaries, changed paths, authorized gates, gate evidence, artifact references, omitted counts, acceptance state, and structured `command-spec-v1` acceptance commands. When no terminal child result exists, normal capsules are unchanged.

Only accepted current-source gates satisfy parent completion. Child evidence alone never does. SQLite remains work authority; canonical repository files remain source authority; evidence directories and capsule records are read-only evidence.

## Version 2 lifecycle

Writable results move from `running` to `ready_for_acceptance`; they become
`accepted` only after current-source acceptance evidence is recorded. A parent
may instead mark them `rejected` or `stale`. Active child write-scope leases
continue to block parent edits until acceptance, rejection, staleness,
supersession, or cancellation. Read-only and no-change v1 results may retain
the `succeeded` projection.

Durable result references identify source identity, context packets,
binary-safe patches, candidate and acceptance verification, telemetry,
reviewer evidence, and compact logs. Candidate gates and current-source
acceptance gates are stored separately.

A later active claim for the same task may adopt a durable
`ready_for_acceptance` result only after the originating claim is inactive.
Child credentials never gain parent completion, graph, interface, or scope
authority.
