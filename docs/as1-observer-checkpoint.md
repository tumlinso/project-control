# Durable public observer checkpoints

Accepted commands and shared tools commit their immutable packet bodies before
the native observer adds `public_tool_call`. `JobService.checkpoint` now saves
that exact public conversation in a separate private SQLite `job_checkpoints`
record. It does not modify the packet body, its hash, the broker's raw observation
rows, or the immutable outbox. The metadata is not source evidence by itself.

The qualified native factory binds checkpoint and fencing callbacks to its
admitted job, attempt and scope. A checkpoint transaction checks the running
lease/generation and scope, then verifies every frame references an observation
issued for that job, in order without duplicates. Its payload must match the
canonical broker observation after the existing payload privacy filter. The
native oversized-frame omission marker is permitted only when the full frame
actually exceeds its native budget. Forged source fields and foreign packet IDs
are rejected before the saved frames or lease can change.

The public call is the exact parsed JSON supplied by the qualified worker,
validated as a supported `{tool, arguments}` object. It is not reconstructed from
command output, inferred for legacy packets, or masked into a different executed
call. This caller-scoped private ledger retains intentionally supplied public
tool arguments; payload privacy filtering continues separately. Hidden reasoning
and unrelated private payload fields cannot be added through a checkpoint: the
payload must still match its already filtered broker observation. Actual accepted
call provenance relies on the qualified private worker callback, not merely on a
valid packet ID or JSON shape.

Limits match the worker conversation: at most 24 frames, 32768 bytes per frame,
and 60000 bytes per checkpoint. The broker's cumulative storage cap also counts
checkpoint bytes. Validation, storage checks, saving frames and renewing the
lease happen in one short transaction, without model or packet-store operations.
A failed checkpoint leaves the previous frames and lease intact. An expired or
superseded attempt cannot commit.

Scoped lookup/poll overlays saved frames by packet ID on canonical observations.
Dispatch resumes from this same view and still supplies only the latest 24
observations. A packet committed after the last checkpoint remains present as a
raw observation; it does not acquire a fabricated call. Databases predating this
additive table and jobs without checkpoints likewise retain legacy raw behavior.
The native worker replays annotated frames as assistant calls followed by public
user results, while legacy/internal observations remain labeled observations.
Its existing request and turn budgets remain in force.

CPU tests exercise the installed qualified observer port, real Bubblewrap and
SQLite: a command checkpoints, foreground preemption yields, both service and
packet store reopen, and the native worker answers from the same replayed packet
without rereading a source that has been removed. Other tests verify immutable
packet bytes/hashes, stale leases/attempts, same-database cross-caller isolation,
legacy/crash-gap fallback, exact public call JSON and rollback on rejected
payloads or budgets. They perform no GPU operations or real-model qualification.
