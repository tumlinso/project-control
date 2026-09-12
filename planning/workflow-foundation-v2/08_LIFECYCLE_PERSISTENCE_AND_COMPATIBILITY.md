# 8. Lifecycle correctness, recovery and persistence compatibility

## Preserve first, reconcile explicitly later

The initial core relocation opens existing `.todo-orchestrator` state without moving it or rewriting UUIDs/history. Read-only opens must not migrate. New migrations run only through authorized writer paths after backups and compatibility checks. Older processes must not write a new schema merely because they can still open the SQLite file.

After parity, centralize run/lane invariant maintenance in the core. Invoke it after completion, plan apply, recovery and relevant integration transitions. Define successful terminal states, rootless runs, canceled/skipped work, required rendezvous/integration obligations, and timestamp semantics. Do not conflate a run's operational completion, workspace artifact acceptance and cleanup eligibility.

Existing `completed_at` should be set once on an actual terminal transition. Idempotent reconciliation should not churn event/revision state or rewrite historical timestamps on every read. An active-looking legacy record requires an explicit diagnostic/repair transaction, not an observer's automatic database write.

## Readiness must agree with actual claim admission

A hint of safe parallelism cannot claim work by itself. Reuse one resource/scope/lock feasibility implementation where possible. Test resource amounts, capacity, overlapping selector pools, multiple locks, isolated worktree bases, expired claims, stale heartbeats and unsupported states. Conservative rejection must be explained, and declared ready should not systematically fail for a different hidden admission algorithm.

## External operations and crash recovery

Represent Git workspace intent, attempt identity, expected base and observed result durably. Test crashes before intent commit, after commit/before process start, after worktree creation/before confirmation, during merge/conflict preservation and during receipt publication. Retrying an external operation must be idempotent by identity, not a blind duplicate command.

Never turn failure into cleanup of unknown files. Preserve successful first-authority import if the second fails. Never assume switching the old executable back is a valid DB rollback after a new migration. Qualify old-reader/old-writer compatibility explicitly; otherwise rollback requires restored backups or a tested migrate-forward repair.

## Deployment is a separate milestone

Retain an immutable old release while candidate code is developed. Before PC I50, qualify the exact candidate pair, quiesce/fence old writers, capture tested backups and reconnection steps. After swap, fresh HTTP and stdio connections must identify the same accepted artifact and preserved UUIDs. A metadata receipt claiming promotion without observed fresh transports is insufficient.
