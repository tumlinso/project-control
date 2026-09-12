# Schema, snapshot, and evidence contracts

The candidate introduces strict semantic records only through an explicit next
writer format. Such records reject unknown task keys and wrong types. Legacy
plan wire formats v2 and v3 remain readable under a compatible reader; their
unknown-key behavior is preserved and reported as a compatibility warning, not
retroactively tightened. Plan wire version, database migration version, and
work-profile schema version are independent version contracts.

`work_profile` is optional. If supplied, it validates against
`work-profile-v1.schema.json`; missing means *unspecified*, never routine.
Difficulty, risk, work type, and context depth are routing inputs only: none
changes task priority, scheduling admission, or authorization. Provider/model
choice is excluded. Execution facts instead use the public,
observed-only `execution-attempt-v1` record.

The Core emits one immutable `workflow-snapshot-v1` from a coherent workflow
revision. Observation time and heartbeat freshness remain distinct from that
revision. The optional `observation-reference-v1` binds workspace, permission
domain, revision, source identity, fields, expiry, and all race/coverage
warnings. It is an opaque read reference, never a capability or authorization
token: expiry, loss, restart, or a changed client requires reobservation and
cannot silently bind current state. Warnings remain serialized rather than
being hidden to meet a response budget.

The normative schemas are versioned independently in
`schemas/workflow_foundation_v2/`. They define an offline contract only; they
do not claim installed runtime support or promote sidecar profiles into live
state.
