# 3. Architecture: small controller, explicit continuity

## The boundary to keep

```
Existing MCP tools                         Thin operator CLI/chat
          \                                      /
           Existing Project Control information/control services
                              |
             assistance controller + service-private state
               /              |                 \
       continuations      derived evidence     focus policy
               |                                |
       existing inquiry broker / runnable work selection
               |                         |
       local inference owner       isolated tool/experiment runner
         [slot 0] [slot 1]               CPU / leased GPU
               \                         /
                 existing host resource interlock

Todo remains the project workflow authority. Domain Skills remain inputs.
```

Names and file boundaries are illustrative. Keep strong internal seams without inventing new public APIs for each subsystem. Existing AS1 compatibility adapters may delegate to the evolved runtime while retaining their public response shape.

## Three things that must not be conflated

An **inquiry** is an admitted externally visible question with exact cache identity and a bounded public lifetime. A **continuation** is saved execution state that can be runnable or waiting. A **model lease** is temporary permission to execute a turn on one physical slot. A long-lived CLI conversation can create several bounded inquiries; it is not one endlessly renewed 300-second inquiry.

A continuation holds the current goal, visible dialogue/tool events, operational resume summary, source/evidence references, pending dependencies, attempt generation, budget accounting, trusted capability reference, prompt/policy identity, and cancellation/expiry state. The resume summary says what has been established and what remains to do; it is not a hidden chain-of-thought archive. Credentials and raw capability tokens stay behind the controller boundary.

Use the existing service-private database for additive continuation, wait, event, attention, and note metadata where this fits. Do not copy Todo scheduling or its project revision counter. Keep large immutable source/test artifacts in the existing packet/artifact mechanisms, referenced by digest. Persistent notes need retention ownership separate from the last-50 public inquiry window.

## Turn/slice execution

Refactor the worker entry point into a resumable slice or equivalent small controller step. A slice returns an accepted tool action, final/partial answer, explicit wait, voluntary yield, or typed failure. Reuse current schema checking, tool policy, source proof, checkpoint and generation callbacks. Do not rewrite the entire worker merely to introduce another loop name.

After a short read, a lease may be retained briefly when that preserves a valuable prefix and does not block demand. A child-agent dependency, long command, experiment, user wait, or resource wait must release the model lease. The controller—not a blocked model invocation—waits for completion. A private, typed dependency proposal can request bounded child work; the controller validates it and supplies an intersected capability envelope. This deliberately extends the old internal no-recursion policy only at that controlled boundary. It does not enable recursive public MCP calls or the legacy writable child API. Treat model-assisted delegation as worthwhile only when it reduces context, enables useful independent work, or needs different authority. Obvious source reads should normally stay in the same gather slice and may be batched within existing limits.

The existing six-round public budget is the initial baseline, not a mandate for every future internal job. Track model rounds, successful tools, invalid proposals, prefill, thinking, visible output, command time, and waiting separately. Resumption must not reset consumed work or silently renew the public deadline. Deadline and cleanup processing must run even when the execution threads are blocked.

## Dependency-driven wake-up

The semantic graph helps identify useful knowledge. Execution readiness should use a small explicit dependency graph, not arbitrary semantic relationships. A wait record names an exact child/result, experiment, source-version condition, or approved user decision and a completion predicate. It includes a version, parent generation, deadline, and how failure/partial/cancellation is handled. No model-generated SQL, predicates-as-code, or English statements treated as executable truth. An evidence dependency names the required source/version or reports the conditions under which a fresh result can substitute; a similarly worded note never satisfies it merely by similarity.

A safe transition is:

1. Validate the proposed dependency against trusted scope and the parent's remaining budget; reject cycles and over-depth expansion.
2. Commit the wait/child intent and parent checkpoint together, or use the existing outbox pattern with an idempotent identity.
3. Release the parent's model lease and prove release; until then its execution occupancy remains accounted for.
4. Make eligible child work runnable. If it completed before wait registration finished, reconciliation still observes its terminal version.
5. On a valid result, atomically satisfy the wait for that generation and enqueue the parent once. Reconstruct context from the checkpoint plus the result.

A child failure should normally wake the parent with an explicit failure/partial packet, not leave it waiting forever. A cancelled or expired parent rejects late results as current output, although correctly recorded historical evidence may remain. Crash recovery reconciles waits against durable result records; it does not require a perfect in-memory callback delivery history.

Two parents occupying both slots must be able to suspend and let their two children run. Test this explicitly. Internal descendants consume bounded credits from their admitted roots rather than competing for the last public waiting slot in a way that deadlocks the roots. Preserve the public two-executing/four-waiting admission contract at its external boundary; define and test separate bounded internal-frame accounting. Neither an unbounded descendant tree nor new public queue identifiers are acceptable.

## Scheduling and responsiveness

Use foreground demand before opportunistic work. Start with at most one automatic model execution at a time; do not permanently reserve a physical slot for a role. Demand can use both slots. Keep a small global cap on live continuations and internal descendants, with a modest nesting-depth limit. These are private safeguards, configurable by the operator, not new task-model concepts.

A foreground question can consume already prepared evidence or subscribe to an exact compatible ongoing preparation. This is internal work sharing, not semantic merging of different public questions. Literal public retries still do not extend deadlines, restart work, or change queue order. A background task promoted for a real foreground dependency inherits the foreground's constraints and is not duplicated.

Bounded cooperative preemption is the default. Check at turn/tool boundaries and before expensive effects. Lower unnecessary background reasoning and avoid giant automatic prefills. Measure the remaining active-turn delay; do not promise zero-latency swaps. If a required foreground GPU lease needs full eviction, use the existing verified owned-process path. Do not release resource accounting merely because a cancellation flag was set. A backend-specific soft-cancel optimization is optional and requires exact-build testing.

There should be one owner of scheduling decisions for this assistance state, not another per-frontend pool. Do not hold a database transaction or workflow lock while doing model inference, waiting for a process, or acquiring hardware. Preserve the central owner/peer identity checks, crash fencing, and packet outbox semantics already implemented [S04, S14, S17].

## Deadlines and effects

The public inquiry's wall deadline continues through waiting and eviction. Internal assistance episodes have their own bounded work-window and cumulative active-compute budget. A conversation can survive between episodes without reserving a slot. An independent watchdog marks expired public work partial/unavailable from retained evidence and prevents new dispatch; physical cleanup can remain separately pending until verified. This prevents a stuck cleanup path from being mistaken for active thinking while still respecting ownership.

Persist intent for an external effect before executing it. Give each effect a stable identity including snapshot and generation. On recovery, reconcile an existing process/receipt before re-execution. Exactly-once arbitrary process execution is not promised: ambiguous interrupted tests are recorded as interrupted/unknown, not as a pass. Re-running a permitted scratch test uses a fresh isolated attempt and a bounded retry decision. Never replay canonical patch application through generic continuation recovery.

## Context and policy swapping

Represent each behavior policy as a small immutable, versioned configuration: system prompt, allowed behavior, logical context budget, phase-specific reasoning/visible limits, and tool-result retention. The trusted controller selects it; an observer request cannot inject a stronger capability or arbitrary runtime setting.

Keep physical serving parameters stable initially: same weights, two narrow instances, current supported maximum context. Choose smaller logical prompts when useful. Gather mode emphasizes decisive reads and compact tool envelopes; synthesis may receive a larger reasoning allowance once evidence exists. The values are experimentally selected operating points, not assumptions that more or less thinking is always superior.

Add numerical per-turn policy overrides only through validated internal contracts. An adapter-global setter is suitable for an exclusively owned evaluation fixture, not concurrent per-frame production tuning. Record the actual effective budget after deadline/context clamps, not just the requested ceiling.

Reconstruction from explicit context is v1 continuity. Preserve a stable prompt prefix where safe, but never sacrifice source freshness or isolation to improve a cache hit. Do not require KV save/restore. Only investigate it if measured re-prefill dominates suspension cost and the installed model/backend supports compatible snapshots. A missing or mismatched cache must fall back to reconstruction, not incorrect continuation.

## Runtime ownership

Move the PC-owned observer loop, inference client/supervisor/adapter code, applicable policies, and their tests into Project Control in an auditable transition. Keep initial behavior unchanged while imports, package data, release receipts, service entry points, and consumers are repointed. Then evolve behavior. Exact target module names are implementation choices.

Leave the canonical Todo kernel and its host resource authority independent. Retain CUDA/ctxpp as domain/tooling inputs. Inventory native coding harness and CUDA qualification consumers before retiring old paths. A temporary forwarding module can preserve import compatibility with one canonical implementation; do not keep two mutable copies or a permanent runtime-to-Skills-to-runtime cycle.

The old `local-coding-worker` skill need not remain a generic discovery bootstrap. Replace that incidental dependency with a minimal installed catalog/navigation entry or host guidance while preserving each domain skill's native routing and source proof. Do not introduce a model-independent hard-coded semantic skill router.

Runtime source changes and old/new brokers must be coordinated at promotion. Reuse existing candidate identity isolation and quiescent migration patterns; version private records when incompatible. Never point an old parser at new continuation states or overwrite newer Todo data during rollback.
