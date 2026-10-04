# PCE2 design: semantic guidance, not procedural control

## Purpose and governing distinction

Project Control makes engineering work intelligible and executable across Todo, Git, workspace and resource authorities. It should remove repeated administrative reasoning while retaining the agent's ability to reason about the actual project.

The observer/planner supplies **what is being built, why, the clear mechanical direction, important constraints, dependencies, decision domains and evidence of success**. It is not a dispatcher prescribing every read, test or recovery command. The executing agent may choose and revise local tactics within that direction. A coordinating/root agent can retain consequential judgment or delegate it within an approved decision domain. Model expense or the word “root” does not itself establish authority.

The division is: **planner provides direction; agents exercise scoped judgment; infrastructure carries out repeatable mechanics and reports real decisions.** Local scouts remain evidence gatherers rather than hidden planners.

## 1. The common path should be short

Keep the existing six ordinary tool names and improve their behavior. Add one specialized capability-checked maintenance entry point. Do not create a new universal workflow DSL, generic SQL tool, free-text repair interpreter or second scheduler.

| Entry point | What an agent should normally need to supply | What infrastructure should supply |
|---|---|---|
| `next_task` | Repository once; optional exact run/task focus | Safe ordinary preparation, claim/resume, execution location, sufficient intent/acceptance context and actual blockers |
| `inspect_task` | The missing subject or reference | Current bounded explanation or precise source context; no ritual follow-up call |
| `coordinate_task` | The semantic action and genuinely new data | Typed payload defaults, current identities, correct validation/integration/delivery route and receipts |
| `delegate_task` | Objective; optional intended source scope, references and required executor class | Inherited constraints, suitable configured executor/host assignment, narrowed authority, result/acceptance contract |
| `collect_delegation` | Delegate reference | Literal progress/candidate/failure state, one idempotent result and sensible waiting/other-work advice |
| `finish_task` | Outcome, rationale and new evidence where needed | Missing/fresh-required validation, final publication, lane advancement and a truthful completion receipt |
| `maintain_execution` (new) | Target or returned maintenance ticket; intent and consequential choices | Diagnosis, exact affected-set derivation, safe approved transitions, resource/workspace stages and resumable outcome |

Names and illustrative request shapes here guide the interface. Keep old calls compatible; choose the smallest additive schema changes and tested compatibility adapters. Do not add a new tool for each low-level administrative primitive. Conversely, “exactly six tools forever” is not an invariant worth contorting useful semantics around.

### Illustrative agent experience

These examples describe proposed behavior, not installed commands or measured results.

**Ordinary work.** An agent requests its assigned task. The reply gives the numerical or architectural outcome, why the chosen mechanism matters, the actual workspace, relevant input deliveries and acceptance. It says “ready to work,” not “inspect next” when the information is already present. The agent inspects only what is missing, implements, and finishes; fresh required evidence is ensured once.

**Delegated maintenance.** A coordinator assigns “make execution E resumable, preserve all work, do not change its requirements” to a configured cheaper tool-capable agent. The host attaches the corresponding scoped mandate. The delegate invokes maintenance, receives a usable continuation or a concrete blocked condition, and returns the receipt. The coordinator does not assemble claim IDs, resource-owner rows or confirmation strings.

**A real decision.** The same delegate discovers retained edits not covered by its mandate. The reply identifies the exact retained work and asks whether to adopt that version; it does not merely say “permission denied.” An authorized decision binds that adoption and lets the delegate continue. A changed version, live writer or foreign workload cannot be approved away by repeating the request.

### A useful execution packet

Return a concise work-oriented packet: goal and mechanical direction; exact execution focus/workspace; required constraints and acceptance; actual input delivery/qualification; what changed; genuine blockers; and an optional executable next call with required arguments already filled. If work can proceed, say so and do not recommend another call merely because a protocol stage exists.

Action advice must come from the same canonical predicates that validate it. A suggestion is not authorization, and apply still checks fresh authority. Do not invent a global materialized project-state machine; factor a small shared assessment and action-policy definition from existing code. Generate role permissions, capability operations and schema documentation consistently.

Use one stable execution reference/context cursor where already available, not model-carried row digests, PID lists or a full observation manifest. Exact historical inspection remains available. Routine reads default to current scoped context; explicit snapshot reads remain pinned and report staleness rather than silently substituting newer evidence.

### Context and output size

Keep the planner's direction and safety-bearing constraints available. Typical complete responses should fit the existing approximately 8 KiB workflow budget, with optional detail behind precise references. Budget the full envelope before returning it; a successful claim or mutation must never be hidden by a later rendering failure. If essential constraints cannot fit, return an explicit context-needed continuation before permitting work—not a silently incomplete brief.

Use the existing fragment/delta machinery; avoid repeated full packets. Do not create a durable task for every read or a second progress ledger. Rich reads are available directly when useful, not only after spending calls to prove that smaller reads are insufficient.

## 2. Privileged does not mean root-exclusive

Authorize an **operation on a target under an approved mandate**, not a model rank. There are two ordinary ways an agent receives permission:

* Its existing execution assignment includes routine preparation and in-scope housekeeping.
* A trusted issuer/host gives it an explicit maintenance mandate for a bounded job, such as making one interrupted execution resumable while preserving work.

The first should not require an extra grant call for every workspace or stale context refresh. The second is issued once with the delegation and covers permitted stages until completion, expiry or revocation. It need not expire merely because the target claim is recovered or the ledger revision changes.

A small mandate record is sufficient: issuer and recipient principal; project/run/task/resource scope; allowed intent family and effect constraints; approved source adoption or successor when relevant; expiry, revocation and optional bounded redelegation. Use existing audited authorization/storage mechanisms where suitable. Do not put a new generic authorization language, signing service or role hierarchy into the funded scope.

Trusted startup/profile controls still select surfaces. Local execution profiles may expose `maintain_execution`, but each call independently validates a principal's grant; visibility is not permission. The observer does not register project mutation. Agents cannot select a stronger profile, self-declare a role, replay another actor's reviewed handle, or obtain broader rights by changing a prompt. Tool-boundary isolation is not OS isolation for arbitrary same-user shell access; document that honestly.

### Delegating environment work

One assignment should carry the objective, preservation constraints, known observations, granted maintenance intents and expected result. A suitable cheaper tool-capable agent can execute the job and return one verified receipt. The originator need not approve each deterministic repair.

Keep **tool-capable maintenance operators distinct from local code children**. A local child's bounded code/result channel does not magically give it MCP tools or a first-class lane. Bind an already-supported tool-capable host launcher when available. If the host has no callable launcher, return a complete `launch_required` assignment/grant for the host's normal agent launch, not a fictional `running` status. Do not invent a new model-serving backend to implement delegation.

The grant for repairing an execution is independent of the broken target claim. It ends through its own revocation/expiry/job-completion conditions and cannot be renewed by the delegate itself without authorized issuer action. A run generation change or out-of-scope target still invalidates it. Routine internal stages do not force reauthorization.

## 3. Put safety checks inside operations, not in the agent's checklist

Already-approved routine preparation, same-owner resume, fresh read refresh and provably safe scoped cleanup should normally execute in one call. A fresh internal preview/check is still performed; the agent does not have to manually execute the checklist.

Use an explicit preview/decision only when new material judgment is needed: selecting a successor, accepting exact dirty retained source, changing accepted requirements, choosing a supported handover for live/uncertain work, or widening the affected scope. If an exact authorized mandate already supplies the decision and impact constraints, do not ask for it again. Never treat a free-text reason as authorization.

The result is one of: work ready; operation completed; waiting for an identified external condition; needs a specific decision; or unavailable with a supported repair/launch route. Include what has already committed and an operation reference when relevant. An unchanged blocked condition should not generate another mandatory next_task loop.

### Freshness and atomicity

Reuse canonical Todo transactions. Validate the facts that make the transition safe—including membership and absence of conflicting owners—inside the mutation. Whole-run retirement must account for all unfinished source members; a partial task selection cannot cancel the entire run. Preserve shared/foreign members, external consumers, successful history, evidence and actual source.

Do not make unrelated heartbeats or the operation's own safe reconciliation steps force repeated model approvals. For an already-authorized effect, re-read and recompute under the existing lock/transaction and continue only if the same effect remains within the mandate. A reviewed impact or exact source-adoption change requires a new decision. Start with existing conservative compare-and-set checks where adequate; factor a local semantic-effect comparison only for demonstrated needless conflicts. No general version-vector framework and no silent semantic rebasing.

Have stable request/operation identities and canonical receipts. Normal callers need not construct them. A lost reply, expired old workflow handle or repeated request must recover the known effect through the principal/target operation record rather than executing another mutation. Keep no-op amendments truly unchanged.

A Todo ledger transaction can be atomic. Git worktrees, host reservations, sidecar markers and another project database cannot be declared atomic with it. Use a small idempotent stage record for those existing operations; report partial completion and resume only unfinished stages. A post-commit projection refresh failure means `committed_projection_pending`, not “the mutation failed; apply it again.” Preview/read must not initialize or restore a missing authority.

### Preserved source and real liveness

Dirty source is not necessarily a reason to block metadata-only retirement, but a new writer needs a verified adoption/handover. Preserve tracked and untracked content without auto-commit, reset or deletion. Bind adoption to exact content and ownership; the delegate may act only within the adoption authorized to it. Retained work is not automatically qualified work.

Use live/stopped/unknown classifications. Stale heartbeat alone cannot free a potentially live writer or GPU workload. A decision to hand over work does not itself prove a writer stopped; the existing shutdown/fencing boundary must establish that fact before reassignment. Use existing owner/resource APIs, including host sweeps and narrowly scoped service-owner repair, and add only failing edge-case coverage. Failed Git status is unknown, not clean. Generated projections do not become material source dirt merely by refreshing, but arbitrary edits cannot be ignored under a broad “generated” label.

## 4. Make ordinary collaboration semantically useful

### Scope and context

Add optional exact source targets/read references to delegation. Default from a declared task/sub-outcome, never the first file in a directory. The objective directs work; authoritative scope bounds it. Carry actual relevant planner constraints and acceptance, not a generic one-line reminder with empty source references.

No authority amplification is the rule; a strict smaller pathname set is not the rule. Equal bounded read scope is permitted. For writes, use existing isolated candidate work or an enforceable exclusive handoff with no concurrent parent/child writers. If that mechanism is unavailable, return the specific supported alternative instead of weakening ownership. A maintenance mandate names administrative targets and resource owners; it need not pretend to own an arbitrary source file.

Check adapter availability before authorizing a child when possible, and make failure after authorization recoverable. Record a real launch acknowledgement. Collection that records a candidate is correctly annotated and idempotent; it does not imply parent acceptance. Return a waiting hint or independent ready work rather than asking the originator to spin.

### Delivery, integration and qualification

Reuse immutable artifact/workspace receipts for source delivery before final task completion. The producer states what it is delivering; the kernel records base/content/identity/qualification and the consumer's pinned import. This is not permission to satisfy a dependency that requires completed qualification. Later source creates a new delivery.

Separate the user-facing effects of validation and integration. Add explicit semantic action(s) or a versioned compatibility adapter around the existing executor; keep one implementation of the actual operations. Existing old combined calls can remain supported, but new normal validation must not unexpectedly merge source. Serial delivery/import does not require unrelated producers; a declared sealed batch cannot silently become serial.

### Validation cost

`finish_task` should ensure the required evidence, not blindly reschedule gates already valid for exactly the same inputs. Reuse the canonical fingerprint/freshness checks and include source, command, dependencies, environment/toolchain and policy inputs that actually determine validity. When that complete reuse contract is not established, rerun. Time-sensitive measurements, actual resource admission and GPU quiescence require fresh observation even with unchanged source. Do not use an empty passed flag or parent acceptance shortcut.

## 5. One environment with truthful capabilities

Keep existing frozen releases and strict identity checks. Use the same verified runtime for ordinary calls, preview, maintenance and reads. Extend the current doctor/launcher rather than making agents assemble paths and environment variables repeatedly. Show which authority failed and the supported remediation; do not infer corruption from an unavailable database.

Bind existing source/local-worker adapters where configured, and expose runtime capability facts independent of whether a model happens to be loaded. A known direct read should work without first trying an unavailable adapter. This does not authorize a new external model provider, hot package rebinding or automatic model installation.

## 6. A small implementation, with a real cost boundary

Six durable outcomes cover this revision. The two native lane sequences are bootstrap defaults, not a permanent prohibition on useful concurrency. They deliberately start as implementer lanes, whose inspected baseline supports bounded code delegation; no writable coordinator repair is required to begin. Use existing host delegation where available until any broken adapter path is fixed.

Reuse PCE1 work if it exists. The highest-leverage first slice is an ordinary entry packet plus role/schema consistency, followed by an authorized maintenance delegate that repairs a disposable stale execution and returns a usable receipt. Prove those paths before adding polishing or generality. This is prioritization, not another mandatory microtask chain.

The [usage policy](machine/usage.json) keeps the same 3-million all-agent ceiling, with a 1.6–2.4-million uncalibrated working range. It removes quotas on delegate count, parallel heads and validation passes. Delegate or parallelize only when actual isolation and expected total cost justify it; do not confuse the cheapest per-token model with the cheapest completed outcome. Account using existing telemetry, report gaps, and stop before the remaining allowance cannot fund required validation and a safe handoff. Do not build a budget platform.

Funded work is the consolidation above, including retained PCE1 correctness edges. A new scheduler, generic autonomous environment-repair agent, global permission framework, database merger, model backend or full index rewrite is not required. Additional ideas belong in a brief deferred note unless a reproduced in-scope failure makes a small fix necessary.

## 7. Acceptance is about what agents can accomplish

[ACCEPTANCE.md](ACCEPTANCE.md) measures real public-boundary journeys: enter and start work; delegate a meaningful task; delegate maintenance and resume; amend one binding; deliver a dependency; validate and complete. Use real Todo/services/transports with disposable repositories, controlled process/clock probes and at least one separate-process grant/launcher check. Mocks alone must not make a broken public route look complete.

Measure actual caller-visible calls, fields, bytes, originator interventions and gate executions. The ordinary prepared entry target is one call; approved routine maintenance one call; clean reconciliation plus actual claim at most two; a new consequential preview/decision normally two. These targets apply only to defined no-conflict fixtures, not to cases requiring a legitimate decision. They are requirements to test, not measured speedup claims.

All retained safety scenarios stay covered; only the root-exclusive and universal-ceremony policies change. Finish with a qualified paired candidate, evidence/usage report and a paused read-only NF1A handoff. Deployment activation and live donor repair require their own existing authorization.
