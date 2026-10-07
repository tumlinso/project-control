# Project Control workflow v3 operations

## Boundary and protocol

Project Control's Codex profile exposes workflow protocol v2;
todo-orchestrator is its in-process kernel and SQLite remains the only semantic
authority. Project Control's observer profile reads normalized semantics and
never mutates them. The cheap-first workflow tools are `next_task`,
`inspect_task`, `coordinate_task`, `delegate_task`, `collect_delegation`, and
`finish_task`.
Responses include stable status, run/lane/role/task identity where applicable,
context cursor or versions, allowed actions, recommended next call, and bounded
blockers. Opaque capabilities are hash-stored and revalidated on every use.
Use Project Control's rich read tools only when this bounded context is
insufficient or source, architecture, history, impact, performance, or
cross-project context is genuinely needed.

`next_task` atomically resolves the project, registers/resumes a session,
selects a safe serial lane, derives its role, claims the lane head, acquires
claim-time locks/resources, creates a dispatch, and issues a capability. Legacy
v2 plans receive a deterministic compatibility run and lane.

`coordinate_task` supports sync, fork, typed message/answer, arrival, interface
publication, gate execution, integration requests, and parent child
acceptance/rejection. `finish_task(complete)` validates gates and atomically
freezes provenance, publishes eligible checkpoints/interfaces, records handoff,
advances the lane, submits configured arrivals, and releases all authority.

## Messages, rendezvous, and context

Run messages are bounded typed records, not chat authority. Linked answers
resolve blocking questions transactionally. Decisions become durable state and
interface changes use existing revision/invalidation authority. Rendezvous
support all, quorum, and designated producers; only first-class lanes arrive.

Context fragments are run charter, lane brief, task brief, decision ledger,
delta inbox, and source references. Normal task/coordination results target
8 KiB. Targeted invalidation returns `context_stale` references.

## Child and workspace boundary

A local child belongs to exactly one parent claim and receives a <=4 KiB packet
without broad run context. Candidate result kinds are patch, test, performance,
source, review, or diagnostic findings. Collection is nonblocking. The parent
is the communication airlock and alone can accept/reject, integrate, publish,
arrive, run acceptance gates, and finish. Dead child state returns control to
the parent rather than deadlocking the run.

Overlapping first-class edits require declared managed isolation, one base,
immutable artifacts, integrator destination ownership, and post-merge gates.
Dirty/conflicted work is never automatically deleted. Child sandboxes, if any,
remain subordinate and produce candidates only.

## Recovery and compatibility

`project-control admin recover` requires a TTY, locks the project, prints a
bounded plan, refuses live mutable work, requires exact confirmation, preserves
files/workspaces/patches, reconciles safe state, and audits the result.
`--inspect-only` is read-only.

Plan schema v2 remains accepted; schema v3 adds run/lane/rendezvous/workspace
declarations. New and newly canonicalized projects set `workflow_front_door`
to `project-control`:
read-only todo remains available, ordinary noninteractive direct mutation
returns `workflow_front_door_required`, and owner TTY maintenance plus explicit
self-debug/test service modes remain available.

The old `coding-workflow` name remains accepted only as a temporary historical
compatibility alias. It is neither a second implementation nor a second
ordinary or live MCP registration.
