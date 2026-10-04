# Durable observer job broker

`JobService` owns service-private SQLite admission, leases, attempt generations,
visible observations and an immutable packet outbox. It does not mutate Todo,
choose a semantic skill, or schedule a GPU. The installed Skills supervisor and
its host resource interlock remain responsible for inference and eviction.

Host startup constructs `SQLitePacketStore`, verifies the installed
`observer_runtime.py` digest against the producer receipt, then constructs
`TrustedObserverFactory(skills_root, expected_sha256, backend=..., roots=...,
tools=..., skills=...)`. `tools(name, arguments, scope)` is the trusted shared
information callback. Only overview/delta/frontier/search/evidence/impact/history/
machine and the internal log are allowed. Command uses the actual installed
`ReadOnlyCommandRunner`; skill names map to registered `{name, root}` objects.
The worker reads installed SKILL.md and selected resources agentically.

Construct `JobService(directory, packets=store, worker_factory=factory,
backend=provider)` and call `start()` in the server process lifespan. Admission
fails honestly when the dispatcher is stopped, a hard cap is reached, or SQLite
fails. An accepted response follows a FULL synchronous SQLite commit. Hint scope is validated and inputs are pinned before the admission reply.
Unregistered skill names are rejected before admission. Request
IDs are scoped to the exact principal/profile/project dictionary; a changed
request under the same ID is refused. Similar questions are separate jobs.
Three already outstanding questions trigger accepted-ID do-not-wait guidance.
`shutdown(timeout=95)` stops new claims and waits for bounded inflight work;
its boolean reports whether the dispatcher actually stopped. Client disconnect
has no effect on this independent dispatcher. `health()` exposes thread state
and the last exception type without sensitive exception contents.

The frontend can use `submit(question=..., access_scope=..., request_id=...,
mode='investigate'|'skill', hints=[...], skill=registered_name)`,
`poll(job_id, access_scope=...)`, `lookup` with the same signature, and
`cancel(job_id, access_scope=...)`. Lookup returns `status: ok`, a typed
`pc-job/1` wire record, and retained visible observations; outside scope it
returns `forbidden`. Bind this exact lookup callback for search kinds `job` and
`investigation`; it needs no model, source snapshot, or fuzzy retrieval.
`log(access_scope=..., query='', limit=50, current_dependencies=...)` searches
job/question/answer/packet/source-path/entity text and assembles evidence with
explicit stale/unverified results. Previous answers are attributed evidence,
never current verification. Poll/log do not initialize or acquire GPUs.

Each claim increments the attempt in a short transaction. An expired lease may
be reclaimed by another service process. Every observation/checkpoint/final
write verifies both the generation and a live running lease. Cancellation
increments the generation immediately, preserving previous evidence, and late
worker outputs cannot change the cancelled record. Worker sessions are closed
in `finally`, including failures and cancellation. Bounded inflight inference
may finish before cancellation is observed; it cannot commit afterwards.
Yield/eviction preserve observations and requeue after configurable retry time.
No transaction waits for inference, commands or packet materialization.

The observation and its stable packet identity are committed together to the
broker outbox before materialization. `reconcile()` replays idempotent
`PacketStore.put` and reconstructs active/recent-terminal retention from broker
records; it runs independently on startup and between claims. A private file
lock serializes reconciliation snapshots across service processes. Broker update
times select the configured terminal retention window, independent of packet
clock ties; expired historical evidence becomes a log omission. SQLite WAL is
initialized once, and short same-process connection lifetimes are serialized
to avoid concurrent native SQLite open/close races. A crash between
packet write and outbox acknowledgment replays the same immutable identity.
This is eventual reconciliation between two SQLite stores, not a distributed
transaction. Active jobs and recent terminal answers pin evidence; alias
reservations survive body expiry. Broker history is currently retained beyond
50 records for text search (the configured storage cap bounds admissions and
new observations); packet retention keeps the last 50 terminal jobs and active
jobs. Administrative pruning/backup policy is left to the host.

Only intentionally supplied questions and authorized visible observations are
stored; hidden reasoning, credentials and private capability fields are masked.
Resumption uses the last 24 compact observations, never opaque KV/transcripts.
Commands run with no network, read-only trusted roots, private scratch,
credential exclusions, bounded bytes/time, and owned-process-group cleanup.
Arbitrary command provenance remains coarse/volatile; the existing direct-cat
path supplies exact byte hashes. Information callbacks should supply their own
source-backed typed packet data; broker packets conservatively declare volatile
provenance rather than fabricating exact dependencies.

The acceptance tests use real local SQLite, concurrent admission, a separate
process dispatcher, lease restart/reclaim, packet outbox crash replay,
cancellation/session cleanup, the actual installed observer port, and actual
Bubblewrap commands. They do not launch inference or GPUs and establish no
scientific validation or production deployment claim. Host-provided roots,
receipt digests, callbacks, and registered skill maps are trusted configuration,
not model arguments. Run the native source acceptance helper with the absolute
Project Control `.venv` interpreter and the project's `src` Python path.
