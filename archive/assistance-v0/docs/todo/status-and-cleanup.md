# Computed Status, Leases, and Cleanup

Lifecycle values include `planned`, `in_progress`, `blocked`, `review`, `done`, `superseded`, `cancelled`, legacy `stale`, and recovery-only `attention_required`.

Execution state is computed: `ready`, `claimed`, `closed`, `inactive`, dependency/barrier/scope/resource blocked, `orphaned`, or `attention_required`. Markdown execution labels are compatibility projections, not input.

Claims default to a two-hour renewable lease. Expired clean scopes can return to ready; dirty scopes are quarantined. Legacy freshness review remains separate: planned/in-progress/stale use 3 days and blocked uses 7 days.

Recovery paths are distinct. A holder with the current token uses the ordinary
claim lifecycle. Expired/orphaned claims use `recover release|adopt`. A still-live
unchanged `coding-workflow` claim may use the manually approved live override.
Any owner-controlled still-live claim whose token was lost may instead use the
interactive owner force-release flow when it has no unsafe attached child, gate,
background/CUDA, or running external work. Changed scope requires explicit
`--acknowledge-dirty`; that permission preserves all files and is bound to the
current material scope fingerprint. The transaction marks the claim `force_released`,
releases safe claim-owned locks and resources, returns the task to `planned`,
and records an owner audit entry.

A successful terminal owner with a legacy pending checkpoint uses `recover
terminal-checkpoints`. This path has no live-claim or raw-token requirement: it
validates the task's recorded completion revision and frozen historical gates,
reaches only owned, non-revoked checkpoints whose completion-time prerequisites
were satisfied, and is a revision-preserving no-op once finalized.

Cleanup is explicit-only. `todo cleanup` reports safety and never deletes v2 durable state. Legacy `cleanup_todos.py` retains its existing explicit cleanup behavior only in repositories not bootstrapped to v2.
