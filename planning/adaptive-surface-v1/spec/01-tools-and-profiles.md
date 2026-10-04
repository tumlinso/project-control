# Target tools and profile contract

`contracts/surface.json` is the machine-checkable name/mode matrix. Common service implementations must drive registration, schemas, dispatch authorization, documentation and tests. A tool being hidden is not an authorization barrier: blocked names/modes are rejected before reaching a backend. Trust comes from startup-bound profiles/principals, not model-supplied role strings.

## Common information contract

Ordinary reads return compact `status`, `packet` reference, `data`, `sources`, and `coverage`. A source locator has registered `project`, `repository`, optional `worktree`, **relative** `path`, optional exact `range`, and actual content/revision identity. Non-file entities have typed stable IDs and authority revision; do not invent a filesystem path for a database row. Their exact follow-up is `search` with an exact typed identifier/entity.

Coverage states what was considered, returned, omitted, stale, unresolved or unavailable and carries a targeted continuation when needed. Do not repeat the complete source/Todo/worktree envelope per result. Sources and identifiers can be deduplicated in a shared table. A persisted packet retains the exact authorized response and the fuller provenance manifest; small caller responses do not imply small durable evidence.

Default detail is compact. Proposed initial **soft whole-response** budgets are 2 KiB compact, 8 KiB standard, and 64 KiB extended (observer only), with targeted continuations and a bounded transport ceiling. These are tuning defaults, not substitutes for acceptance or hard universal message sizes. Mandatory status, source locators, freshness, omissions and continuation must survive. Never split a JSON object, silently truncate an exact code excerpt, or claim a complete result after cutting it. If a single requested unit exceeds the budget, report its size and return an explicit range/continuation. Per-profile schemas should not advertise forbidden `extended` values.

## Tool contracts

### `overview(project?, detail=compact)`

First-look orientation: purpose, defining architecture/entry points/relative paths, constraints/invariants/non-goals, compact current transition/work state and relevant applied skills. It is not an active-claims dashboard. Use a versioned orientation record with source anchors; invalidate affected fields when their supporting records/paths change. Missing or stale authored orientation is labeled, not replaced by fabricated purpose from directory names. A cheap deterministic fallback can expose known roots and source entry points.

With no project, return a compact catalog of registered projects and how to address them. This closes the otherwise missing discovery step for an observer or project-less scout. Extended gives the observer richer architecture, selected records, impact-provider coverage and relevant skills/routes. All profiles can call overview; **none receives it automatically**.

### `delta(project, since, detail=compact)`

Material changes relative to a returned cursor or retained packet reference: source, relevant Todo/coordination state, semantic notes/declarations/skill-use, and provider coverage. Avoid heartbeat/audit churn. A missing/expired comparison baseline returns a typed partial/current-baseline result, not a fabricated empty delta. Reuse authority deltas and dependency-sensitive freshness rather than rescanning everything.

### `frontier(project, scope?, detail=compact)`

Active/ready/blocked work, dependencies, current queues/lanes/runs, unresolved questions, rendezvous, integration/workspaces/patches, recovery attention and safe parallelism. Compact shows decisive work state; targeted `scope` or standard/extended retrieves deeper coordination. Preserve authoritative scope and lane/task identity. Removing `agent_status` does not remove claims necessary to understand conflicts. Do not resurrect historical blockers merely because an old row still exists.

### `read(project, repository?, paths:[relative paths or per-path range objects], revision?, detail=compact)`

Remote observer only. Batch one or many exact files, with per-file status, returned range and identity. Reject absolute, traversal, drive/UNC, NUL and symlink-containing paths. Trusted registered roots may be canonicalized at registration, but requested components do not follow symlinks. No filesystem escape through historical Git symlink blobs either. A directory is a typed `not_a_file` result; discovery is search/overview. Support UTF-8 text initially with honest unsupported-binary status. Never silently drop a failed element in a batch.

Known revision reads must use the requested immutable revision, not current indexes. Working-tree reads record actual content hashes and detect races; revalidate before emitting a claim of exactness. Preserve byte/text content; where policy requires masking, mark redaction explicitly and do not call that fragment verbatim authority. Foreign-project results are read with that registered project/repository plus its relative path, not a global absolute path.

### `search(project?, query, scope?, detail=compact)`

`query` accepts the existing discovery query or an exact typed entity reference `{kind, target}`. For an exact typed ID/entity, route directly to the existing canonical exact lookup backend using the cheapest deterministic path; do not invoke fuzzy or lexical retrieval unnecessarily, expand into filesystem discovery, or fall back to discovery after a missing exact record. Preserve the canonical machinery internally rather than exposing a separate public `find` tool.

Exact lookup covers an already identified task, interface, decision, gate, checkpoint, source symbol, context record, run/lane/workspace, registration, packet, or investigation. Return the canonical typed record and navigable locations/relationships; ambiguous IDs get candidates without guessing. Packet/investigation access remains scoped to permitted caller/project domains; aliases are not permission tokens. For native/local agents, filesystem discovery remains ordinary Unix `find`, `rg`, and Git through native capabilities or investigator `command`; exact semantic lookup does not become filesystem discovery.

One deterministic discovery entry point combines project-graph/entities, source/symbol indexes, lexical search and live filesystem/source fallback. Typed results distinguish exact reference, structural relation, declared relation, lexical hit and summarized guidance. Include why matched, origin, relative locator or entity ID, and freshness. Allow file/path discovery and scoped queries; do not force a natural-language search to retrieve a known file.

With a project, include relevant applied-skill guidance and applicable notes; with no project, cheap catalog/global metadata discovery is permitted, not an automatic scan of every host directory. Cross-project traces identify another project that the caller can query explicitly. Search does not claim to perform impact closure, establish causality, or validate completion. Fix lexical behavior where tokenizing `def X` returns every `def`: exact symbols/phrases and meaningful term weighting need tests. Fallback must disclose provider availability and scope.

### `evidence(project, subject, kinds?, detail=compact)`

Aggregate registered gates and evidence with decisive validity, applicability and provenance. Distinguish declaration, unrun/pending, running, failed, current pass, stale historical pass, terminal frozen success, absent evidence and source mention. A test-source match or active claim is not proof of correctness. See the evidence contract in `spec/03`.

### `impact(project, targets, change?, direction=dependents, view=paths, detail=compact)`

Deterministic typed dependency tracing. Targets may be exact returned locators/entities. `change` supplies a declared change class/assumptions, not an LLM-generated proof. Traverse known compatible edges automatically across authorized registered projects. Return trace chains and coverage; view selects paths versus exact supported snippets. Standard mode can return bounded snippets; only observer can request extended output. See provider/invalidation contract in `spec/03`.

### `history(project, subject, from?, to?, detail=compact)`

Material temporal trace over existing semantic events and real Git ancestry. Recorded cross-project references can lead into another registered authority, with clocks/revisions labeled independently. Include evidence retention limits, unavailable sources and explicit causes; do not synthesize intent from adjacency. Cursors support precise continuation.

### `machine(query_or_view?, detail=compact)`

Shared bounded host/system facts: GPU/interconnect/process summaries, capacity, toolchain versions, selected process/service/storage/network state and relevant diagnostics. Reuse fixed machine-inspection providers. No benchmark launching, arbitrary privileged command execution, GPU lease mutation or unrelated secret content. Include observation time because these facts are volatile. Compact defaults for every role. Optional views must not silently become the removed CUDA-specific performance dossier.

### `investigate(question(s)?, project?, hints?, request_id?, job_id?, detail=compact)`

Observer and mutator may submit or poll read-only scout questions. Batch entries have independent question text and hint aliases. `job_id` polls an existing durable job without resubmission; explicit caller request IDs support retry-safe admission. Job scope is optional but resolved against trusted registered access. Busy is not failure: persist first, then return accepted/pending plus “Do not wait; continue reasoning or other useful work and ask again later using this ID.” Polling does not need a GPU.

No recursive `investigate` within investigator/skill modes. Do not expose model name, tensor/layer parallelism, warm-slot selection or GPU numbers as ordinary caller requirements. Those belong to registered runtime policy.

### `skill(query?, skill?, hints?, request_id?, job_id?, detail=compact)`

Observer-only local adapter. Omitted skill searches the installed catalog; an empty query can return catalog names/descriptions cheaply without loading a model. Specific queries use native routing. Return small separated synthesis plus authoritative direct resource text and exact locators. Same packet/job/retry mechanism as investigate, not another queue. See `spec/04`.

## Shared internal/native tools

`command(argv, cwd?, limits?)` is internal scout/skill-mode command execution in the existing OS sandbox, not an observer shell. Host code clamps resource limits. Use standard Unix/Git/parser tools by default. It may use disposable scratch; it cannot mutate host/project/Todo state or delegate.

`log(query?, job_id?, path_or_entity?, limit?)` gives internal scouts/skill assemblers compact recent investigation/skill-job findings with source refs. Reuse existing records only after freshness checks. Observer/mutator can retrieve a known job through investigate/search or discover logged work through search; do not add redundant public log UI unless necessary for a documented requirement.

## Workflow and mutator additions

Coder and mutator retain `next_task`, `inspect_task`, `coordinate_task`, `finish_task`. `inspect_task` is retained as the existing handle-scoped context mechanism, not a competing general source reader. New context operations must reuse shared semantic services beneath its authority checks. Surface contracts for its existing supported actions remain compatible.

Mutator adds `plan`, `amend_project`, and `maintain_execution`. `plan` consolidates validate/diff/apply/amend/supersede/retire using existing kernels; `amend_project` manages durable semantic declarations; maintenance handles exceptional state repair through existing preconditions and receipts. Exact actions are in `spec/05`. Keep model permissions distinct from CLI owner compatibility.

## Profiles

| Profile | Shared information tools | Adapters/extras | Mutation |
|---|---|---|---|
| Observer | overview, delta, frontier, search, evidence, impact, history, machine | read, investigate, skill; extended available | No project or Todo mutation |
| Investigator, internal | Same eight, compact/standard | command, log; native files/skills; no automatic brief | None |
| Coder (`codex` compatibility identity) | Same eight, compact/standard | Native files, shell and skills; four workflow tools | Scoped workflow only |
| Mutator | Same eight, compact/standard | investigate; native files/skills; four workflow tools | plan, amend_project, maintain_execution |
| Skill assembler, internal mode | Same semantic implementation with permitted scope, compact/standard | command, log; home=registered skills root | None; only proposes source selections |

This deliberately does not expose Project Control read/skill to local profiles or recursive investigator adapters. Native capabilities are not reimplemented as MCP tools. Temporarily disabled coder delegation tools are absent from discovery and rejected at dispatch with `temporarily_inactive`; feature metadata gives reason and explicit reactivation policy, not a scheduled date.

## Removal/compatibility

Default discovery drops old `project_*`, `architecture_context`, `source_context`, generic `inspect`, `coordination_view`, `program_context`, `history_trace`, `impact_preview`, `skill_list/read/context`, `plan_preview`, `apply_plan`, `terminal_capture`, `performance_probe`, `agent_status`, `performance_status`. Reuse their backend components where appropriate. Existing CLI/admin/kernel functions may remain for migration and compatibility, clearly not ordinary model routing. If legacy MCP compatibility is necessary, isolate it to an explicit trusted compatibility configuration; do not advertise aliases alongside the new API or permit forbidden calls through aliases.
