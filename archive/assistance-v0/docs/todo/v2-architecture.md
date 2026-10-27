# v2 Architecture

## State Layers

1. SQLite is the live operational authority. It lives outside ordinary Git tracking: first under `TODO_ORCHESTRATOR_STATE_DIR`, otherwise under the Git common directory, otherwise under the XDG user-state directory keyed by project UUID.
2. `.todo-orchestrator/project.json` is a small tracked identity/configuration file with stable UUID and schema version.
3. `.todo-orchestrator/state.snapshot.json` is deterministic durable recovery state. It excludes session/claim/resource tokens and active volatile leases.
4. `todos.md`, `todo-status.md`, and `todos/*.md` are atomic human projections and legacy inputs, never synchronization primitives.

If the database is absent, bootstrap restores durable entities from the snapshot and emits `snapshot.restored` at a new revision.

## Transaction Contract

Every semantic mutation uses a bounded-retry `BEGIN IMMEDIATE` transaction, validates current state, updates entities, increments the monotonic project revision, appends one event, and commits atomically. Foreign keys and a busy timeout are enabled. Partial unique indexes enforce one active claim per task and one active capacity-one named lock.

Successful task completion freezes required-gate provenance, terminalizes the
task, reaches every currently eligible task-owned checkpoint, records the
handoff, and only then releases the claim in that same transaction. Required
gate freshness remains live for active work; successful terminal tasks retain
the validation identity recorded at completion even after repository HEAD
moves. Legacy terminal/pending states are repaired by the idempotent terminal
checkpoint finalizer, whose authority is the recorded successful completion,
not a fabricated claim.

Snapshot and Markdown projection happen after commit from consistent reads. A filesystem lock serializes complete projection refreshes; files use temporary write, fsync, and atomic replacement. Projection failure cannot roll back semantic state and is visible to `todo doctor`.

## Readiness and Pickup

Lifecycle state is stored; pickup state is computed. Dependencies, decisions, interface compatibility, barrier state, active claims, path/parallel-policy conflicts, claim locks, and claim-time resources contribute to readiness.

Candidate score is deterministic: priority, downstream unlock value, age, then stable task ID. Pickup and claim-time allocations occur in one write transaction. Session and claim tokens are generated randomly; only SHA-256 hashes are stored.

## Same-Worktree Safety

Claims provide logical ownership, not file isolation. Exclusive roots, read roots, frozen contract reads, named critical sections, and conservative parallel policies determine compatibility. `todo audit` compares claim baselines, current Git state, declared scopes, siblings, interface hashes, gate inputs, artifacts, snapshot, and projections without pretending dirty-file attribution is perfect.

## Recovery

Expired clean claims may return to ready. Changed owned scopes are quarantined. A matching local process prevents time-only resource reclamation. Recovery is explicit through inspect, adopt, or acknowledged release.

The event log is append-only and revisioned. `todo changes` filters deltas to the current task, ancestors, prerequisites, checkpoints, barriers, consumed interfaces, and related entities.
