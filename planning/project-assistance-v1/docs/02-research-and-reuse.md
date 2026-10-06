# 2. Research findings and reuse map

## Observation boundary

The inspection used non-agentic Project Control reads, discovery, orientation, frontier, history, traces, and machine facts on 6 October 2026. No inquiry worker was launched, no benchmark was run on the user host, and no state was deliberately repaired. Working-tree sources were inspected, not a newly qualified installed candidate.

Project Control reported HEAD `1b1026845d3fff5a27ad539a386b24649ba48f68`, a dirty worktree, and semantic revision 923. Skills discovery reported HEAD `95818340006dd50ef233d7c67ddca2da8eb08bc4`, dirty, and revision 1017. Individual content hashes in the source ledger are the more precise evidence. Re-fetch at adoption: the agent surface and Skills are actively changing.

The frontier included existing PCE2 work and a stale-heartbeat attention item [O02]. Those are observations, not authorization to resolve or replace them. An orientation read lacked authored orientation; one commit-based delta request returned no retained baseline; a trace and history expansion were partial [O01, O06]. These gaps must not be reported as a complete repository audit.

## What is already there

| Existing mechanism | What v1 should reuse | What is actually missing |
|---|---|---|
| `JobService`, SQLite, observations, checkpoints, attempt fences, packet outbox [S04, S17] | Accepted-work durability, restart recovery, exact retry identity, source observations, generation guards | Explicit waiting dependencies, resumable execution slices, differentiated admission classes, independent deadline supervision |
| Central `SupervisorServer` / `ProductionBackend` [S09, S14] | One owner, two slots, warmed same-model services, verified process ownership, leases, native host interlock | Safe per-frame policy selection and lease release around long waits; not a new serving system |
| `ObserverWorkerPort` [S13] | Tool validation, source navigation, schema validation, explicit message reconstruction, checkpoint callbacks | A return-to-controller continuation outcome instead of only a synchronous bounded loop |
| `InformationService`, exact packets and source locators [S06, S18] | Small existing MCP surface, access checks, exact reads, source hashes, citations, continuations | A bounded prepared-context/derived-note provider |
| `TraceService`, AST/native exports and material determinants [S07] | Incremental source understanding, witnessed relations, current-versus-stale provenance | A small focused change-trigger service; the tracer is not already a persistent watcher |
| Todo context fragments and canonical publication [S19, S20] | User/owner-authored goals and commitments, authorized promotion, normal task protocol | Claimless assistant notes must have a separate non-authoritative home |
| Preserved local-worker coding/snapshot mechanisms [S10–S12] | Dirty-overlay capture, isolated work, baseline comparison, scoped patch evidence, external verification | A permitted scratch task path that never automatically invokes canonical patch acceptance |
| Adapter injection points and model calibration harness [S15, S22] | Scripted transports, fake processes/clocks, actual protocol testing, controller-leased real trials | A workload-quality suite, replayable agent cases, and fast candidate comparison rather than capacity maximization |
| Current deployment manifest and source binding [S01, S23] | Frozen production identity, one common launcher, rollback and deliberate promotion | A cleanly separated development harness and a controlled runtime-ownership transition |

This is substantial reusable infrastructure. The meaningful new work is orchestration of resumable assistance, promotion-quality context, bounded automation, and a usable operator interface—not a new LLM stack.

## Corrections to the earlier conversational diagnosis

The public tests established two symptoms: a CUDA partial and a non-CUDA request remaining `thinking` beyond its expected budget. They did not establish how many useful rounds remained, whether Qwen was still generating, or which lifecycle component was stuck. Earlier explanations assigning both to excessive deliberation were too confident.

The inspected worker reserves the final round for an answer. A six-round inquiry therefore dispatches at most five model-requested tools. Invalid JSON or rejected argument attempts also use the finite loop. The source currently contains conservative continuation instructions, keyword-based reasoning selection, context omission rules, and constrained final JSON. A short partial can arise from several causes; instrument the turn trace before changing policy [S13].

The current broker holds a session across `worker.run()` and closes it in `finally`. Cleanup failure deliberately retains execution-slot accounting. An overlong public pending state must therefore be diagnosed separately from model generation: admission, startup, locks, transport, tools, deadline sweeps, and verified release all matter [S17].

The preserved coding delegate is not simply the observer loop under a different prompt. Its legacy contract includes bounded parent-authorized execution and distinct harness/acceptance semantics [S08, S10–S12]. Reuse components, not an imagined already-unified agent architecture.

## Model budgets and suspension: concrete findings

The inspected production profile selects narrow Qwen3.6-35B-A3B Q4_K_M, 65,536 context capacity, two workers, 900-second warm idle residency, 16,384 nominal reasoning-token ceiling, and `preserve_reasoning=false`. The configured throughput values are calibration seeds, not measurements from this inspection. Wide is not selected [S16].

The adapter implements a private thinking phase followed by a bounded visible answer; effective thinking is reduced by context, predicted prefill, time, and an answer reserve. It exposes `set_observer_generation()` on an owned server. Thus a request-policy experiment need not restart weights. The supervisor's current turn schema, however, does not yet expose arbitrary numerical per-frame overrides. Add a trusted immutable policy path; do not mutate shared global generation policy concurrently [S14, S15].

Physical context capacity is supplied at server startup. Logical context selection can change per frame inside that capacity. Changing one is not changing the other. A 64K allocation is not permission to fill every prompt with 64K tokens.

Continuation already reconstructs explicit messages from observations. That is the correct starting point. Native KV persistence is an optional future optimization, not the record of the agent's identity. A dropped hidden reasoning stream need not be reconstructed to continue a question.

Eviction is not currently cost-free mid-turn. The supervisor defers normal slot eviction while an active turn exists, and adapter cancellation can terminate the owned server. Preserve those distinctions when setting responsiveness targets: use short, useful background slices and verified release, not a claim of instantaneous arbitrary preemption [S14, S15].

## Development-loop finding

`scripts/qualify_observer_model.py` already runs dry by default, requires a CUDA controller lease for real GPU trials, records source/model hashes and timings, and reuses a server for thinking-budget trials. Its final best-trial ordering favors reasoning tokens and context occupancy among passing fixtures. That is a reasonable capacity exploration artifact, not a product-usefulness objective [S22].

Extend this machinery. Do not repeat the broad calibration sweep every time a prompt changes. The source history records both prompt-related changes and genuine protocol, source-proof, transport, cache, and lifecycle repairs [O05]. It would be misleading to call all of that work cosmetic; the avoidable cost is mixing qualification/deployment into every local diagnostic iteration.

## External research: ideas to borrow, not dependencies to install

**W01 — llama.cpp server documentation.** Upstream documents request-level sampling, explicit token prompts, prompt-cache reuse, and slot save/restore/erase. Its context capacity is a server parameter. This supports investigating cheap request variation and optional cache reuse. It does not establish support or compatibility in the user's installed binary; qualify that exact build. Cache reuse is not guaranteed to make generation bit-identical.

**W02 — LangGraph persistence.** The useful analogy is checkpointed execution and separate longer-lived shared memory. PA1 should keep the same conceptual distinction between a continuation and project knowledge, but implement the minimal extension over its existing broker rather than importing a second durable workflow engine.

**W03 — Anthropic context engineering.** Selective retrieval, compact durable notes, and keeping tool/context representations economical are useful design patterns. They motivate preparing high-value evidence and re-fetchable references instead of continually enlarging transcripts. These are general guidance, not measured Qwen improvements.

**W04 — Anthropic effective agents.** Prefer simple composable components and introduce agentic decisions where the problem genuinely requires them. Here that argues for deterministic change detection, admission and permission checks, with model judgment reserved for interpretation and useful questions.

**W05 — Ray nested tasks.** Ray documents releasing CPU resources while a task waits, but retaining GPU resources. This is a useful warning: adopting a generic task framework would not automatically provide the intended GPU-free waiting. PA1 must explicitly release its own inference lease.

**W06 — MCP Tasks.** The versioned November 2025 specification describes an experimental long-running-task facility. There is no demonstrated need to replace the existing literal-retry observer contract with it. Internal continuations do not require public protocol churn.

**W07 — Bubblewrap security guidance.** Bubblewrap is a mechanism whose safety depends on the enclosing configuration, not a complete policy by itself. Worktree separation and command allowlisting alone are not sufficient containment for arbitrary test execution.

The machine-readable bibliography records exact primary-source locations and retrieval date. None of these sources is used to claim implementation or live performance qualification.
