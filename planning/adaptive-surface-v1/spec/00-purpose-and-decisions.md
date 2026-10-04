# Purpose, authoritative decisions, and consistency review

## 1. What success means

Project Control should make long-horizon engineering easier: reliable orientation, discoverable exact source, useful dependency and evidence traces, continuity across agents and model eviction, and low-friction scoped execution and replanning. Agents should decide scientific/engineering goals and tradeoffs; deterministic machinery should own routine bookkeeping, provenance, conflict checks, and transactions.

Optimize completed, verified engineering outcomes—not tool-count reduction alone, not raw token minimization at the expense of evidence, and not busy GPUs. Measure the whole journey including schemas/prompts, retries, context rereads, queue/poll overhead, human interruptions, and maintenance detours. Do not manufacture workflows merely to exercise Project Control.

This is a frontend rebuild with backend reuse, not a thin rename and not a rewrite of Todo/CUDA/ctxpp. Share canonical services across profiles. Do not make a separate investigator retrieval engine, a second lifecycle interpreter, a second GPU lock service, or a second writable Todo projection.

## 2. User decisions to preserve

The machine-readable requirement ledger is `contracts/requirements.json`. In particular:

* `overview`, `delta`, and `frontier` survive with distinct jobs. Overview is first-look project understanding and defining paths; frontier is active work and coordination; delta is material change.
* `read` accepts **multiple relative paths** and is remote-observer-only. Coders, mutators and local scouts read files natively.
* `search` combines semantic/project-graph discovery and source/filesystem discovery, with the latter preserved as fallback. It is an option, not the only way into a project.
* `search` includes exact typed semantic lookup through the existing canonical backend, taking the cheapest deterministic path for exact IDs/entities without unnecessary fuzzy or lexical retrieval. There is no public `find` or extra local-only `inspect_entity` vocabulary. Native filesystem discovery still uses Unix `find`, `rg`, and Git; it is not exact semantic lookup.
* `evidence`, deterministic `impact`, and deterministic `history` stay first-class. Coordination moves into frontier. Machine facts become a shared `machine` tool.
* `investigate` is a short-lived, command-first read-only scout service with durable queue/history. It can be invoked without a project, within registered host access policy.
* `command` is the final name for `exec_readonly`. The sandbox enforces effects; avoid unnecessary command allowlists and content mutilation.
* Information packets have retained memorable word aliases, exact source/evidence identity, and reusable hints. Expiry must never turn an old alias into another packet.
* Busy service means persist the accepted question, return an ID, tell the caller **not to wait**, and let it continue reasoning. GPU residency is not job persistence.
* `skill(query, skill?, hints?)` is observer-only. A local read-only adapter homed in the registered skills directory follows native skill routing. Small synthesis is allowed; Project Control supplies the actual skill text by direct reads.
* Used, relevant skills become project semantic context. Exploratory reads alone do not count as applied skills.
* Temporarily hide `delegate_task` and `collect_delegation`; retain their implementations, data, and tests. No time-based automatic re-enablement.
* `terminal_capture`, `performance_probe`, and `agent_status` disappear from the ordinary observer surface. Performance findings become ordinary source-backed semantic/evidence context, not a universal CUDA status tool.
* Mutator can independently understand and plan. It receives applicable observer knowledge and investigation capabilities plus full typed plan/control-plane mutation, registration and maintenance. Native filesystem/skills access replaces redundant observer adapters.
* Genuinely destructive admin operations require fresh explicit user permission; routine safe transactions do not.
* Backend provider registration is mostly automatic. Mutator publishes ambiguous/durable declarations through `amend_project`; agents never edit private graph indexes or the Todo database directly.

## 3. Resolve conversational drift before coding

The user's statements override earlier assistant restatements. These are explicit implementation resolutions, not claims that every earlier wording agreed.

| Tension | Resolution |
|---|---|
| Early investigator brief injection versus later “overview ... callable by all ... instead of injected” | **No mandatory overview injection in any role.** Every role may call overview. A scout starts with question, scope and supplied hints; it calls overview only when useful. The versioned orientation record still exists. |
| Mutator gets “all observer tools” versus later observer-only read/skill and extended | Mutator gets all relevant knowledge capabilities including investigate, but **not** redundant `read`/`skill` adapters and **not** `extended`. Native reads/skills provide those information sources. |
| Same semantic toolkit versus a short-lived local scout | Same tool meanings/services; compact defaults, targeted expansions, no extended mode, no recursive investigate/skill adapter calls. |
| “Queue full” yet still accepts the question | Three outstanding is a **soft saturation threshold**, not a hard capacity limit. Accepted work is durable. A separate true storage/admission limit must honestly report `accepted:false`. |
| Persistent aliases versus expiry | Bodies can expire; alias ownership never changes. Retain a small permanent reserved-alias/tombstone ledger in a durable service namespace, or equivalently collision-check all previously issued aliases. |
| Read-only observers/scouts versus persistent logs and queued work | Read-only applies to project/Todo/source authority. Service-owned cache, packet and job state can be written; this does not grant project mutation. Describe that effect accurately. |
| Cross-project impact versus relative-only read | Cross-project locators carry project and repository identities **plus a relative path**. Read each repository explicitly. Never encode scope changes as `../` or symlink traversal. |
| Deterministic dependency evidence versus “proven impact” | A recorded dependency proves that a relationship was observed/declared, not that an arbitrary change will break a consumer. Report dependent candidates and change assumptions, not certainty of breakage. |
| Agent knowledge versus skill authority | Search summaries and local synthesis remain attributed secondary guidance. `skill` must include direct exact excerpts with hashes/ranges and never substitute a paraphrase. |
| Persistent questions versus long-lived model memory | Persist question, evidence, compact findings, unresolved issues and attempts. Do not depend on KV cache or store hidden reasoning. |
| Removing delegation surface versus mutator investigate | Coder local delegation remains hidden. Mutator may request a **read-only scout** for planning; this does not reintroduce local coding delegation. |
| Large scope versus sensible work granularity | Use the outcome-sized tasks supplied here; split only after a concrete concurrency, ownership or blocking need appears. Do not turn every schema, test, or parameter into a Todo. |

## 4. Authority ownership

| State | Canonical owner | Project Control's role |
|---|---|---|
| Source and committed history | Git plus actual working-tree bytes | Read, identify, trace; never invent clean/unchanged state |
| Tasks, claims, gates, run/lane coordination, durable project declarations | Existing Todo kernel / semantic read port | Typed facade; extend kernel schema only where needed |
| Registered roots and principal/profile trust | Trusted local startup/configuration | Validate access; semantic registration never expands host access implicitly |
| Skill bodies, routing maps, resource graph | Canonical installed Skills tree | Read and map; preserve content, identity and declared routing |
| GPU ownership and profiler interference | Existing CUDA host-global interlock | Request/release/evict through it, never keep competing ownership truth |
| Model process/service leases | Existing local-worker supervisor | Reuse compatible slots, handle eviction, no new model daemon architecture by accident |
| Packets, aliases, question queue, attempt history | Service-private durable Project Control store | One shared implementation for investigate and skill jobs |
| Source/semantic search and impact indexes | Derived provider caches | Incremental refresh; disposable, never declaration authority |

Project registration/declarations should use the existing Todo transactional authority when they are project semantic facts. Trusted root/provider-execution policy remains host configuration. A registry lookup/cache does not make those stores one transaction.

## 5. Economics by role

**Observer:** broad architectural reasoning and planning. It can opt into extended dossiers, direct multi-file reads, and skill/scout adapters. Compact is still the default; it must not consume its context needlessly.

**Investigator:** brief, bounded read-only exploration. It gets a real command sandbox plus the same semantic tools. A sufficient question is sufficient context; do not inject README/frontier/skills automatically. Hints and `log` avoid rediscovery. No recursive delegation or planning authority.

**Coder:** native computer and skills plus scoped execution. `next_task` and existing task context remain the normal front door for substantial claimed work; overview/search/evidence/impact/history are available when useful, not enforced as a ritual. Existing host subagents remain usable outside these temporarily disabled local-delegation tools.

**Mutator:** independent planning and control-plane work. It uses compact semantic tools, native files/skills, optional investigate, and transactional plan/registration/maintenance tools. Being privileged does not make giant read responses economical, nor does it remove freshness/scope invariants.

**Skill assembler mode:** an internal mode of the same local read-only worker, not another public product. Its home is the registered skills root. It selects sources and briefly explains their relationship; the broker, not the model, emits authoritative excerpts.

## 6. Boundaries

Do not resume NF1A or mutate Cellerator, GlassHelix, Baseplane, or other user project sources to qualify this infrastructure. They may be observed read-only; fixtures cover mutation cases. Preserve dirty files, historical successful evidence, atlas source ZIP and technical card bodies. Do not inherit an old epic's numeric token cap as a new user instruction. Record actual cost, use economical capable subagents for bounded work, and keep no-op/proof decisions evidence-backed.
