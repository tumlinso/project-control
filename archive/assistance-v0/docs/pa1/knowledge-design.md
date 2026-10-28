# Controller decisions for persistent assistance knowledge

Use the existing private packet SQLite database for derived notes, supporting
packet pins, user goal cards and dismissals. Commit a note and its pins in one
transaction; a second database must not create an apparent durable assertion
whose evidence was already collected. Note owners are separate from the recent
terminal inquiry retention window. Deletion releases only that note's pins.

A note is advisory derived context, never Todo authority or independent proof.
Keep original source locators, packet identities, per-project scope, material
source/configuration determinants, coverage and visible uncertainty. Repetition
of a derived note is not corroboration. Source deletion, inaccessible evidence
and partial coverage remain explicit. Compare material determinants rather than
invalidating every note when repository HEAD changes. Bound stored content and
retrieval; never retain hidden reasoning or authority tokens.

User goal cards have explicit user provenance and priority over inferred
suggestions. They are not power grants: the existing expiring focus window and
quiet/release veto remain separate trusted controller state. Source text and
model-produced prose cannot grant scratch access, background inference or
canonical publication.

Expose relevant prepared context through existing discovery/evidence paths
without changing public MCP schemas or exact inquiry/cache identities. Recheck
each registered project's current trusted scope separately. Missing or forbidden
segments are omissions, not an inferred global atomic snapshot. Public cache
lookup precedes any preparation; retain the existing historical-answer warning
when supporting sources changed.

These are controller integration choices for the next claimed outcome, not
implementation or executed qualification.

## Bounded interfaces for the next outcome

Keep note records as validated JSON dictionaries so persistence and retrieval
can be developed independently without a new framework. The packet-store API
will be `put_note(record, *, access_scope)`, `get_note(note_id, *, access_scope)`,
`list_notes(*, access_scope, project, limit=128)` and
`delete_note(note_id, *, access_scope)`. The store validates provenance, scope,
content limits and original evidence references. A separate trusted
`put_user_goal(record, *, access_scope)` path sets user provenance; ordinary
derived-note writes cannot impersonate a user goal. Preserve stable note IDs on
updates and use `note:<id>` as the independent pin owner. Store at most 128
records per project and 8 KiB per record; capacity exhaustion is explicit.

The provider consumes those dictionaries through
`retrieve(query, *, project, access_scope, budget_bytes=4096, limit=5)` and a
trusted dependency resolver `resolve_dependency(project, key)`. Resolver results
are current material digests or explicit unavailable reasons. Retrieval returns
`notes`, `goals`, `omissions`, `coverage` and `authoritative=False`, with original
provenance, evidence and freshness on every record. It never grants publication
or treats its output as a new independent source witness.

Wire the optional provider into existing information discovery and evidence
responses using trusted host scope. Instantiate it for both public information
and inquiry-worker information services. Keep exact public cache handling and
typed exact search routing unchanged. The required unittest integration module
will exercise these actual store/provider/services with disposable projects.
