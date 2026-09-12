# 3. Absorb implementation, preserve the authority boundary

## Behavior-preserving first milestone

Skills A/V/I10 produces the donor module/test inventory and qualified source receipt. PC imports it through X01 and establishes explicit module-to-module mapping, provenance and retained old source. K01-K05 move the implementation and fix relocation-dependent imports only. V01/V02 independently compare deterministic cases in separate interpreter processes. PC I10 accepts the parity pair and releases post-parity work.

Initially preserve DB migrations, storage locations, transaction boundaries, revision/event sequencing, graph and readiness semantics, capability classes, context fragments, workspaces, integration, child acceptance and recovery. A bug known before the merge is still part of the baseline behavior until a separate feature commit fixes it. Do not label schema/lifecycle changes as relocation exceptions.

The differential harness must catch intentionally altered statuses, lost events and omitted fields. Normalize only identified nondeterminism (for example deterministic clock/UUID fixture injection); never normalize away a semantic difference. Compare original inventory counts as well as newly authored tests. Fixtures must be outside actual registered authorities and contain no live auth tokens.

## New internal boundary

MCP/CLI -> WorkflowProtocol -> internal Workflow Core -> authoritative storage remains the write path. Observatory, query graph, source services and presentation receive typed snapshots/facades. Claims, scheduler, capability checks and completion remain implemented once.

Remove provider discovery and duplicate product binding from the new candidate only after parity. Keep supported entry facades for existing consumers and retain installed artifact identity/security checks appropriate to the unified product. Do not replace justified release integrity with unverified imports merely because two packages became one.

Git/worktree changes are external side effects, not SQLite-atomic operations. E-stream work introduces recorded intents, idempotent operation identity, observed result receipts and explicit failure/recovery outcomes. An interrupted Git operation must neither duplicate a worktree nor cause an unknown worktree to be deleted.

## Packaging and compatibility

PC I20 publishes a candidate runtime API. Skills X02 gates forwarding shims and CUDA/local-worker rewiring. A legacy `todo` executable or `todo_orchestrator` package must forward to the same implementation, not execute an old second kernel. Inspect import-order class identity and mixed-version failures. Keep the old frozen product out of the new import path but available for rollback and differential testing.

No copied live `.todo-orchestrator` state, caches, sessions, credentials or compiled artifacts belong in this delivery. The donor code is copied during implementation from the newly reverified source, not distributed here as an unreviewed duplicate.
