# Durable observer job broker

`JobService` owns service-private SQLite admission, leases, attempt generations,
visible observations and an immutable packet outbox. It does not mutate Todo,
choose a semantic skill, or schedule a GPU. One central Project Control
inference supervisor owns the persistent warm pool shared by local MCP, remote
HTTP and all profile clients. It cooperates with the installed runtime and host
resource interlock for inference and eviction. ProductionBackend pools belong
to that supervisor, rather than individual profile processes. Profile EOF leaves
the central pool warm; the global two-executing/four-waiting limit applies across
all clients.

Host startup constructs `SQLitePacketStore`, verifies the installed
`observer_runtime.py` digest against the producer receipt, then constructs
`TrustedObserverFactory(skills_root, expected_sha256, backend=..., roots=...,
tools=..., skills=...)`. `tools(name, arguments, scope)` is the trusted shared
information callback. Only overview/delta/frontier/search/evidence/impact/history/
machine and the internal log are allowed. Command uses the actual installed
`ReadOnlyCommandRunner`; skill names map to registered `{name, root}` objects.
The worker reads installed SKILL.md and selected resources agentically.

Construct `JobService(directory, packets=store, worker_factory=factory,
backend=provider)` using a client of the central inference supervisor and call
`start()` in the broker host lifespan. Profile hosts do not construct their own
ProductionBackend pools. Admission
fails honestly when the dispatcher is stopped, a hard cap is reached, or SQLite
fails. An accepted response follows a FULL synchronous SQLite commit. Configured project/source access is validated and supplied evidence is pinned before the admission reply.
Unregistered skill names are rejected before admission. Legacy private submit
request IDs are operationally scoped to the principal/profile/project dictionary;
a changed request under the same ID is refused. This compatibility bookkeeping
does not partition shared oracle knowledge. Similar questions are separate inquiries. Two executing and four waiting inquiries
are permitted. A hard-cap response is busy and means not accepted.
`shutdown(timeout=95)` stops new claims and waits for bounded inflight work;
its boolean reports whether the dispatcher actually stopped. Client disconnect
has no effect on this independent dispatcher. `health()` exposes thread state
and the last exception type without sensitive exception contents.

The public observer uses `inquire(question, access_scope, mode, skill, hints,
request_id, execution_question, foreground_timeout=30)` through the inquiry
adapter. One global cache/log retains the last 50 answered inquiries across
trusted callers/profiles. Original literal question, mode, selected skill and
project/authority context identify the inquiry; principal/profile do not.
Project knowledge, evidence, supplied hints, answers and log entries are shared
oracle context reusable across roles. Configured project/source access, profile
tool permissions and credential exclusions remain enforced. Caller/profile
identity is provenance rather than an answer or evidence privacy boundary.
Public callers receive answers or `thinking`, `busy` and
`unavailable`, never job IDs, leases, attempts or queue positions. The observer
contract and stale recomputation are described in [as1-inquiry-cache.md](as1-inquiry-cache.md).

`submit`, `lookup`, `poll` and `cancel` remain service-private compatibility seams.
Terminal inquiry lookup supplies the private job and observations to the answer
adapter. `freshness_provider(jobdict)` validates material dependencies and returns
`fresh` and `changed_sources`; missing verification is stale. Internal `log`
retrieves at most five lexical matches from the last 50 answered inquiries.
Neither exact current-answer retrieval nor log starts inference or acquires GPUs.

Each claim increments the attempt in a short transaction. An expired lease may
be reclaimed by another service process. Every observation/checkpoint/final
write verifies both the generation and a live running lease. Cancellation
increments the generation immediately, preserving previous evidence, and late
worker outputs cannot change the cancelled record. Worker sessions are closed
in `finally`, including failures and cancellation. Bounded inflight inference
may finish before cancellation is observed; it cannot commit afterwards.
Foreground preemption, unavailable sessions and eviction preserve observations
and requeue after configurable retry time. Ordinary turn-budget exhaustion
(including legacy `yielding/step_budget`) terminates as `partial` with a result
packet and unresolved question. Polling and same-ID resubmission return that
retained partial result without automatically claiming another attempt.
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
reservations survive body expiry. Storage may retain historical broker records beyond 50 (the configured
storage cap bounds admissions and observations), while lexical log retrieval
considers only the last 50 answered inquiries. Packet retention keeps the last
50 terminal jobs and active jobs. Administrative pruning/backup policy is left to the host.

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
The separate dispatcher fixture selects candidate source only in its child
environment, removes inherited deployment pins there, and verifies the Jobs
and packet module paths and content hashes before admission. The native gate
parent retains its deployed release binding.
