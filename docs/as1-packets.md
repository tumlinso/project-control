# Service-private AS1 packet persistence

`project_control.as1_packets.SQLitePacketStore` implements the `PacketStore`
contract. It grants no workflow authority and has no public dispatch or startup
side effects. The service owner provides a private directory on a local,
WAL-capable filesystem, its namespace and trusted scope. SQLite uses WAL and
`FULL` synchronous transactions. Content-addressed JSON bodies, immutable
provenance and never-rebound aliases commit together; no separately renamed
blob can be lost between filesystem and database commits. Connections close
at operation completion. The database has mode 0600; new directories use 0700.
The operator must ensure preexisting parent directories remain private.

## Producer and consumer API

```python
store = SQLitePacketStore(private_directory, namespace="service-instance")
packet = store.create(
    tool="search", payload=authorized_result,
    access_scope={"principal": "caller", "profile": "observer", "project": "pc"},
    sources=source_locators, parents=[],
    normalized_request={"query": "importers"}, audit_id="call-correlation-id",
    freshness={"dependencies": {"discovery:pc/importers": generation}, "negative": True},
)
result = store.lookup(packet.alias, access_scope=trusted_scope)
# result.status: ok, expired, forbidden, not_found
hints = store.assemble_hints(
    [packet.alias], access_scope=trusted_scope,
    current_dependencies=verify_one_dependency, budget_bytes=8192,
)
```

`create` returns the exact policy-masked result that consumers should deliver,
not the original sensitive input. Recognized credentials, bearer strings,
workflow handles and private reasoning fields are masked and reported in
`omissions`; ordinary source/query/command text and domain `token` fields are
preserved. It is not a detector for arbitrary unlabeled secrets. Producers must
apply their authorization/output policy before submission. Sensitive invocation
metadata is rejected rather than silently altered. Audit correlation and
normalized request are separate immutable invocation metadata accessible via
`invocation(ref, access_scope=...)`. Oversized payloads are rejected explicitly
(default cap 4 MiB). Errors and empty dictionaries are supported.

`put(InformationPacket)` validates its digest and JSON representation, rejects
unmasked private fields, and stores a deep copy. Duplicate identical packet IDs
are idempotent; differing content/provenance and expired IDs are rejected. Unique
alias collisions raise `sqlite3.IntegrityError` to direct `put` producers;
`create` retries reservations and extends random two-word aliases to three or
four words on collisions. It never recycles a reservation. `resolve` preserves
the contract's packet-or-None API; use `lookup` for explicit failure status.
Scope matching requires every stored scope restriction to match trusted caller
scope. Empty producer scopes are rejected. Scope is checked before expiry or
payload access, so forbidden callers cannot inspect bodies or expiry details.

## Freshness and bounded hints

Dependency identities are producer-defined keys mapped to observed values.
Recommended prefixes include `file:repository/path`, `semantic_revision:project/entity`,
`registry:project`, `provider:name/config`, `skill:name`, `environment:host`,
`discovery:scope`, `directory_membership:scope` and `exports:scope`.
Canonical semantic evidence must preserve project UUID, entity kind/ID and
actual authority revision in its dependency identity and delivered entity refs;
it must not invent file paths for database records. File locators add exact
file-hash dependencies automatically. The verifier callback receives only these
keys; unrelated repository edits do not trigger rescans. Missing verifications
are reported as unverified and yield attributed stale leads. Conflicting hashes
for one file identity, including conflicts between producer dependencies and exact
source locators, are reported as stale and cannot be overridden by a current hash.

Negative findings require a discovery, directory-membership or export dependency.
Volatile observations require live verification after `max_age_seconds` (default
zero); set `volatile: true` in freshness. No dependency manifest means freshness
is unverified. Hints retain exact payloads and source spans, deduplicate identical
bodies while retaining contributing references and source locators, and report
changed dependencies, producer omissions, expiry, scope failures and whole-packet
budget omissions. The budget uses actual UTF-8 JSON byte estimates, not model
tokens. It bounds selected evidence, while the omission ledger itself is separate.
Hints explicitly report `authority: false`; validating a packet ID proves neither
claim entailment nor permission to mutate.

## Retention, recovery and limitations

Bodies expire after seven days by default (`ttl_seconds=None` disables TTL).
`pin(owner, refs)` / `unpin(owner)` are trusted engine retention operations, not
caller capabilities. `retain_job(owner, refs, terminal=False)` pins active inputs;
terminal jobs retain the latest 50 owners by default. Explicit user retention and
linked skill jobs use distinct owner IDs. Nonexpired children and pinned packets
transitively retain their parents. Missing, logically expired or collected inputs
cannot be resurrected by pinning or child creation. `gc()` removes unreferenced
expired bodies while preserving alias/ID tombstones and invocation provenance.
It never writes project or Todo authority. Shared bodies survive until all live
packet references expire. Job pinning, retention state changes and terminal eviction commit atomically.
Lookup reads metadata, liveness and payload through one consistent SQLite snapshot.

`backup(destination)` uses SQLite's online backup API, including committed WAL
state. Restore that file as `packets.sqlite3` in a private directory; opening it
with a different namespace fails. Backup/restore never changes alias identity.
Hash validation detects body corruption during lookup. Acceptance exercises
restart in a separate process, collision/concurrency, backup/restore, expiry,
retention, corruption and dependency verification. This is local filesystem
qualification; unsupported network filesystems and power-loss hardware behavior
are not claimed. SQLite storage lives independently of existing audit metadata,
and public information-operation packet wiring belongs to the surface task.
