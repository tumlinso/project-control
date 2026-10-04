# Information packets, short-lived scouts, and persistent service state

## Packet authority and identity

Separate three objects: an invocation audit event, an immutable information packet, and a durable question/job. Existing call-audit metadata is not a packet store. Reuse its call correlation and logging hooks, but persist the actual authorized tool result rather than just an excerpt or tool name.

A packet has an immutable opaque ID, a memorable random word alias, origin tool/profile/principal scope, normalized request, creation time, exact delivered payload, content hash, source/evidence manifest, parent packet IDs, and declared completeness/omissions. Do not retain credentials, bearer capabilities, hidden reasoning or private conversation context that was not intentionally supplied. Store output after policy enforcement; masking must be recorded. Store source observations, not claims of authority invented by a local model.

Word aliases should be short two-word combinations sampled from a curated human/model-readable list. Reserve them transactionally and collision-check. They are labels, not capabilities or globally secret identifiers. A durable namespace and a never-rebind alias table survive daemon restart, backup/restore and packet-body expiry. An expired alias yields `expired`; it must not point to a new packet. Use a longer word combination if the small vocabulary becomes exhausted, not silent recycling.

Initial tunable policy: retain packet bodies for seven days and at least the last 50 terminal question records; active jobs, linked skill-selection jobs, explicit user retention, and required child evidence pin their inputs. The exact retention window is a configuration decision; it is not an instruction to delete active evidence at seven days. Preserve the alias reservation/tombstone after body deletion. Recent logs must not contain broken evidence links just because the backing packet TTL was shorter. GC works from references and retention policy and never edits project/Todo authority.

Common packet references should be returned by **all information operations**, including internal command observations and mutator read/preview results, not only observer reads. Avoid duplicate physical payloads with content-addressed storage. Different invocations may share an immutable body but retain distinct call/provenance identity. Small empty/error responses are also auditable; do not let oversized unbounded exceptions become stored packet payloads.

## Hints and context assembly

A question may reference one or more retained aliases. The server resolves access, sources and freshness, then assembles a bounded context for that question. It is not a concatenation of every hinted response. Preserve decisive assertions/records and exact needed source spans, deduplicate shared content, include omissions and typed follow-ups, and leave room for exploration and the answer. Profile budget is derived from the real model context limit/tokenizer when known; byte estimates must be labeled estimates.

Treat old summaries as attributed leads. A changed or unavailable source does not invalidate all reusable material, but it cannot support an unqualified current-state conclusion. Relevant source dependencies, registry versions, semantic revisions, provider configuration, skill content hashes, and volatile machine timestamps govern reuse. A broad repository fingerprint is a conservative fallback, not an excuse to repeat all reads after an unrelated edit.

Negative findings need special care: “no consumers” depends on the searched universe/index generation, not just on a few previously read files. New files/importers, export-map changes, glob additions and environment changes can invalidate absence claims. Record the discovery scope/version as a dependency.

## One durable job engine, two modes

`investigate` and `skill` use one service-private durable job engine with separate job modes/prompts. Reuse the existing local-worker supervisor and model service. Do not implement a second GPU scheduler, a separate skill daemon, or a tool-call-only pseudo-queue.

A job record contains stable job ID, caller idempotency key, question, scope, hint refs, request hash, mode, created/updated times, status, attempt/fencing generation, source manifest, compact observed findings, unresolved questions, new evidence refs, and final result packet. The optional project is scope, not a prerequisite for every host-level investigation. Trusted roots and access policy govern a project-less command job.

Suggested durable states: `queued`, `running`, `yielding`, `queued_after_eviction`, `completed`, `partial`, `failed`, `cancelled`. GPU/service waiting is a state/reason, not loss of the question. A worker process/daemon dispatches committed jobs independently of the initiating MCP connection. The request handler must not be the only thing capable of draining the queue. Expose dispatcher health and honest failure if durable processing is unavailable.

Persist admission before acknowledging acceptance. Claim a queued job with a short transaction/lease and fenced attempt number; never hold a database transaction while waiting for inference or doing shell reads. Commit observations/compact working findings after useful read/model steps. After a worker crash, reclaim an expired attempt; a late result from its old generation cannot overwrite the new attempt. Exactly-once admission via an idempotency key is feasible; arbitrary command execution is at-least-once after crashes, so the sandbox and read-only semantics must make retries safe. Do not falsely promise universal exactly-once computation.

## Saturation, polling, and deduplication

Three outstanding questions is the initial **soft** busy threshold. A newly accepted question still gets durably queued. The response includes `accepted:true`, job ID, current state, poll instruction and concise text:

> Queue busy. Your question is queued. Do not wait; continue reasoning or other useful work and ask again later using this ID.

A hard backlog/storage cap is separate, configurable and reported as `accepted:false, reason=admission_limit`; do not claim work was queued when storage failed. Optional retry-after metadata is advice, never a requirement to sleep or busy-poll. Pending/finished job lookup must not load a model or reacquire GPUs. A disconnected client must not cancel accepted work.

Caller-supplied request ID gives exact retries their existing job. Server exact deduplication uses normalized question, scope, mode and hint/source identity; changed hints or a materially changed question are not silently discarded. Similarity search only proposes prior jobs; it does not merge different questions automatically. On repeated questions the worker checks `log` for exact and semantically relevant previous answers, revalidates dependencies, and performs only missing work.

Log retains the most recent approximately 50 terminal question/answer records, plus active/pinned records regardless of that number. Search by text, source path, skill, entity, job and result packet. Return compact findings and evidence refs; fetch selected evidence when needed. A cached answer's old timestamp is not current verification. Volatile machine facts may require live re-query even when source hashes are unchanged.

## The scout's interaction model

Use the shared semantic services with an internal read-only profile, not bespoke `orient/search_source/read_source/inspect_workflow` aliases. Expose `command`, `log`, and the compact shared tools. No Project Control read adapter, recursive investigate, recursive skill adapter, coder claims, registrations or mutation tools. The skill mode has the same agent substrate with a different home/objective and source-selection output.

The initial context is the question, explicit scope, capabilities/policy and supplied hints—not an automatically injected overview, AGENTS, README, frontier, or all skill instructions. Native/local agents can read relevant instructions normally; overview remains callable. The scout should normally inspect files/Git/build metadata with `command` and ask semantic tools for authoritative Todo/graph/evidence state.

The command adapter accepts an argv vector (not an automatically interpolated shell string), chosen working directory within allowed mounts, and bounded limits. Shells/scripts may be allowed inside the same OS sandbox; the safety property is enforced effects, not a brittle list of command spellings. Reuse Bubblewrap, process-group cleanup, no-network namespace, read-only host/project mounts, dropped privileges, cleared environment and actual credential masking. Disposable scratch is permitted. Avoid blanket path/content suppression that makes legitimate source unusable. Report denied resources clearly.

No arbitrary direct device exposure through command; machine uses fixed diagnostics for device queries. No paid-model fallback, network use or model download merely because local resources are busy. Choose installed, verified local model/runtime policy; capacity and parallelism stay backend concerns.

Each command returns exit status, stdout/stderr, truncation, timing, working scope, and a packet ID. Commands can observe files outside the nominated repository if allowed by trusted registered sandbox policy; label them as external observations rather than pretending a commit pins them. For source proofs the broker should record exact referenced files/ranges where feasible. If it cannot reliably derive the read set of arbitrary code, mark provenance coarse/volatile; never infer exact dependencies from command text alone.

## Evidence and result quality

Final investigation packets contain a concise answer, evidence-backed facts, explicitly labeled inferences, unresolved questions, source paths/entities and packet references. A valid evidence ID only proves the evidence was issued; the implementation must not claim that ID validation proves entailment. Test claim-to-source alignment, especially search-hit-only ownership claims, stale read sets and unrelated citations. Keep useful existing structured-output repair but avoid turning formatting trouble into a complete loss of gathered evidence.

Persistence retains compact observed findings, not hidden reasoning or a full chain-of-thought transcript. A restarted/replaced local model resumes from question, selected evidence and unresolved issues, not from opaque KV state. Independent queued questions must not inherit another caller's context. The shared model server may be warm; semantic conversation state is job-scoped.

## Practical storage

A service-private SQLite database plus content-addressed blobs is sufficient initially. Keep transactions short, enable crash-safe handling and test actual deployment filesystem semantics; do not store the WAL database on an unsupported network filesystem. Audit metadata, packets and jobs may share storage infrastructure but have separate retention and authority roles. Rust-style dependency-sensitive cache validation is a useful design pattern, not a requirement to recreate a compiler query system. Primary references are listed in `sources/ledger.md`.
