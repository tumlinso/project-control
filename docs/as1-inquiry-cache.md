# Observer inquiry cache

The observer asks a question and receives a supported answer or a compact state.
`investigate(question, project?, hints?, request_id?, detail?)` and
`skill(query?, skill?, project?, hints?, request_id?, detail?)` do not accept a
public job ID. All eleven observer tools remain read-only for source and Todo.
Private packet, cache and scheduler bookkeeping can write service-owned state.

The public states are `completed`, `partial`, `thinking`, `busy` and `unavailable`.
Only `thinking` says to repeat the identical question later and avoid variants;
continue useful work meanwhile. `busy` means the question was not accepted.
Use search, read or evidence to contextualize or refine a later question.
Answers retain supporting findings, evidence packet references and source identity.
Nested results and continuations do not expose attempts, leases or queue metadata.

Cache identity is the literal original question, trusted project and authority
context, mode and selected skill. The cache is global across trusted callers and
profiles; caller principal and profile do not partition it. Different projects
remain distinct inquiry contexts, and catalog context includes its trusted project
allowlist. Whitespace is significant. Skill navigation instructions are
saved separately as the execution question. Details, request IDs and advisory
hints do not duplicate an inquiry. Similarity classification does not merge
questions. A pending exact repeat returns its existing state without restarting
work, changing scheduler order, extending expiry or consuming another slot.
A current answer returns immediately, including after dispatcher restart.

Freshness follows the material dependency manifest of retained hints, evidence
and result packets: exact source identities and hashes, declared semantic
revisions, and successful direct-cat reads. Skill entry and selected resource
proofs must be present and entry reads must precede resource reads. Descriptor
pinned no-symlink readers validate current bytes. An unrelated repository HEAD
change is insufficient to invalidate an answer. A terminal result's bookkeeping
`volatile/max_age_seconds: 0` does not alone invalidate verified source evidence.
Missing, expired, unverifiable, changed or conflicting required material cannot
be called fresh. Coarse arbitrary command provenance and volatile machine facts
retain their conservative freshness behavior.

An exact repeat of a stale supported answer schedules a new generation preseeded with the previous
answer, visible evidence and changed-source information. The agent checks what
changed, preserves still-valid work and obtains missing evidence. This is visible
context reuse, without cached hidden reasoning or opaque model state. Old
answers remain attributed historical evidence while recomputation proceeds.

An inquiry that terminates without a usable answer is a negative cached result:
public calls return `unavailable`. An exact repeat preserves that result without
automatically starting another generation or a refresh loop. Supported stale
completed/partial answers can refresh as described above; empty terminal failures
do not. Failure causes and backend diagnostics remain in internal records.

The internal scheduler permits two executing inquiries and four waiting, with a
30 second foreground wait and 300 second inquiry lifetime. Private job identities,
fences, SQLite/outbox durability and restart recovery remain implementation
mechanics. The cache retains the 50 most recently answered inquiries globally.
Internal log retrieval filters that global window by trusted source access and
returns at most five lexical matches; it does not create a private window per
caller or profile. Legacy answered jobs join the same global retrieval window.

Project Control is a shared oracle: caller principal and profile record provenance,
not private knowledge compartments. Ordinary read packets, hints, answers, prior
job observations and log entries can be reused across roles when the trusted host
allows their projects and sources. Other explicit authority requirements still
apply. Source allowlists, credential masking, denied paths, workflow capabilities
and mutation permissions retain their existing controls.

Storage exhaustion and oversized inquiries report `unavailable`; `busy` describes
global queue capacity. Dispatchers skip incompatible project authority before
claiming work. Catalog applicability records the trusted project allowlist; a host
executing a catalog inquiry stays within that recorded authority.
Source writes, similarity routing and a public queue workflow are outside this contract.

Focused CPU tests use actual MCP schemas, real local SQLite and scripted ports;
they do not establish live model quality, GPU performance or biological validity.
Live qualification and release acceptance belong to the root controller.
