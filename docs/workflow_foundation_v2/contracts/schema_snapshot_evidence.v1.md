# Schema, snapshot, and evidence contracts

The candidate introduces strict semantic records only through the explicit
`workflow-task-record-v1` next-writer format. Its top-level members reject
unknown keys and wrong types; `work_profile` is optional as a whole. Legacy
plan wire formats v2 and v3 remain readable under a compatible reader; their
unknown-key behavior is preserved and reported as a compatibility warning, not
retroactively tightened. Plan wire version, database migration version, and
work-profile schema version are independent version contracts.

If supplied, `work_profile` validates against
`work-profile-v1.schema.json`; missing means *unspecified*, never routine.
Difficulty, risk, work type, and context depth are routing inputs only: none
changes task priority, scheduling admission, or authorization. Provider/model
choice is excluded. Execution facts instead use the public,
observed-only `execution-attempt-v1` record.

The Core emits one immutable `workflow-snapshot-v1` from a coherent workflow
revision and its `tasks` items reference `workflow-task-record-v1`. Observation
time and heartbeat freshness remain distinct from that revision. The optional
`observation-reference-v1` binds workspace, permission
domain, revision, source identity, fields, expiry, and all race/coverage
warnings. It is an opaque read reference, never a capability or authorization
token: expiry, loss, restart, or a changed client requires reobservation and
cannot silently bind current state. Warning records are strict `{code,message}`
records and remain serialized rather than being hidden to meet a response
budget.

The normative schemas are versioned independently in
`schemas/workflow_foundation_v2/`. They define an offline contract only; they
do not claim installed runtime support or promote sidecar profiles into live
state.
