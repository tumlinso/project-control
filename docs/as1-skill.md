# AS1 observer skill adapter

`SkillService` brokers source access and persistence. The local read-only worker
chooses resources by reading the installed `SKILL.md`, authored maps, references,
and prerequisites. Project Control does not match questions to architectures,
skill names, routes, or lexical hits. Indexes and graphs may accelerate worker
navigation; they are not a second routing policy.

The one public adapter is `skill(query?, skill?, hints?, request_id?, job_id?,
detail?)`. The host supplies trusted principal/profile scope. Calls are allowed
only for the observer profile. Native coder, mutator, and scout continue reading
installed skills directly. This module adds no public list/read/context tools.

## Host integration

The host owns startup and shutdown of the already-qualified `JobService`:

```python
from project_control.as1_jobs import JobService, TrustedObserverFactory
from project_control.as1_skill import SkillObserverFactory, SkillService

trusted = TrustedObserverFactory(
    skills_root, validated_runtime_sha256,
    backend=local_backend, roots=registered_readonly_roots,
    tools=shared_information_callback, skills=registered_skills,
)
factory = SkillObserverFactory(trusted, skills_root=skills_root)
jobs = JobService(state_directory, packets=packet_store,
                  worker_factory=factory, backend=local_backend)
adapter = SkillService(jobs, skills_root=skills_root)
# Host lifespan: jobs.start(); ...; jobs.shutdown()
reply = adapter.submit(access_scope=trusted_scope, query=query, skill=skill,
                       hints=hints, request_id=request_id, job_id=job_id,
                       detail=detail)
```

`registered_skills` maps exact names to `{"name": name, "root": absolute_root}`.
Roots and the runtime hash come from startup registration and validated producer
receipts; tool callers cannot choose them. `SkillObserverFactory.skills` preserves
that mapping for the shared job service. It adapts the installed port's narrow
source validator to registered cross-skill selections, requiring each selected
skill's original entry read/hash before delegating its original path/hash/range
validator. The original loop, sandbox, model backend, checkpoints, and fences are
reused. It never changes model-selected names, paths, or hashes. A mixed selection
can retain valid authority while the broker explicitly omits failed entries.

With no query and no skill, `submit` returns a deterministic catalog and packet
alias without starting dispatch or inference. It reads the installed
`integrations/native-skill-catalog.json` and only its explicit entry files,
including descriptions, stable installed IDs, source hashes, rejected or stale
registrations, missing external dependencies, and unsupported coverage. Complete
means all installed catalog rows are reported; it does not imply universal skill
availability. This is the producer's standalone Skills catalog, not external
Codex plugin discovery. No filesystem discovery or semantic entity search is
added here. Shared `search` retains its existing discovery and typed lookup.

A query without a skill dispatches the registered `local-coding-worker` entry as
a startup-selected discovery guide, then exposes the exact installed catalog
location and registered identities to the worker. That entry is bootstrap
navigation. The worker reads each subsequently selected skill's own entry and
follows its authored routes. An explicit name scopes normal navigation to that
skill while permitting explicitly identified registered cross-skill dependencies.
`discovery_skill` is a host option; an unavailable registration is reported, not
guessed. A name without a query requests relevant entry guidance.

`submit` delegates to the shared queue with `mode="skill"`. Request IDs, hint
pinning, scoped polling, cancellation, retry after eviction, durable observations,
outbox replay, and attempt fencing come from `JobService`. `poll` can replay a
terminal outbox and assemble a retained selection after restart without loading
a model. It never owns a second queue, model residency, or GPU reservation.

## Authority and freshness

`SkillSelection` is the existing versioned producer contract. The worker's
synthesis is not the source. `assemble` requires a terminal, scope-checked skill
job, its exact durable result manifest, and the matching attempt. Caller-created
observations or manifests cannot substitute for that result.

Every excerpt requires an observed, broker-persisted command packet with successful,
untruncated `direct_cat` proof, the registered original entry's current hash, and
the selected resource's full-file SHA256. Semantic tool packets cannot supply
reader proof. Entry-before-resource order is also authorization: each selected
skill's first successful original entry read must precede its first selected
resource read. Order is derived from scope-checked durable command observations,
not model annotations or a collapsed path map; a later validator reread cannot
erase an earlier reversed-order read. Selecting the entry itself is permitted.
The factory enforces this before the installed resource validator, and terminal
assembly independently rejects reversed-order selections while retaining valid
ones. The broker rereads original files with the existing Skills
descriptor-pinned, no-symlink reader. It checks the full hash before applying
strict line bounds. Exact content preserves CRLF, whitespace, indentation, code,
and noncontiguous excerpt separation. Out-of-range, changed, escaped, unregistered,
unread, unsafe, or redacted resources have explicit omissions. No redacted text
is labeled verbatim. Other valid selections remain available.

Sources carry canonical skill/resource identity, full content hash, and original
line ranges. Separate labeled local-agent synthesis links to the returned excerpt
IDs; these links identify supporting context, not mechanically proven entailment.
Its budget is at most 12% of the requested response budget, 15% of the returned
excerpt bytes, and 768 bytes. Excerpts consume up to 75% of the detail budget;
envelope/metadata packing remains the host surface's responsibility. Necessary
context exceeding this limit is omitted with a poll/extended continuation instead
of fabricated or silently truncated original text.

Prerequisites may identify selected resources or explicitly retained hint aliases.
Retained hint dependencies must have current source hashes; stale/unverified hints
remain historical leads and do not satisfy prerequisites. Missing, external, or
unregistered dependencies remain explicit unresolved entries. Assembly merges
and deduplicates unresolved manifest entries with the durable job's top-level
unresolved questions; a valid excerpt cannot hide a partial worker result's
missing dependencies.

Freshness has `max_age_seconds: 0`: hashes were checked during this assembly only.
An alias stores the historical packet; it is not a promise that files remain
unchanged. Polling the same job rereads and revalidates source authority, returning
a new packet alias. A historical selection whose source changes returns partial
authority and requires worker refresh/reselection for updated ranges. The broker
checks attempt identity again before publishing assembly.

## Executed proof and limits

`tests/as1/test_pc_as1_skill.py` covers SKL-01 through SKL-04 with real SQLite,
the installed receipt-verified `ObserverWorkerPort`, actual Bubblewrap read-only
commands, and a scripted CPU model backend. It reads the installed CUDA/Volta
router and nested V100 atlas without changing the corpus. Fixtures force missing
maps, cross-skill dependencies, stale files, symlink replacement, mutation during
read, wrong roots/hashes/ranges, redaction, corrupt producer output, retained hints,
restart polling, between-turn preemption/resumption, and cancelled attempt fencing.

These are broker behavioral tests. They do not qualify genuine local-model route
reasoning, GPU admission, broad performance, or production readiness. The actual
agentic skill loop remains the later SQA acceptance campaign. Native gate execution,
workflow lifecycle, integration commits, and final acceptance belong to the root
controller; this adapter does not claim those results.
