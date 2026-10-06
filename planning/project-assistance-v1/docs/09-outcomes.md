# 9. Durable outcome briefs

Each task below owns a deliverable, not a sequence of micro-actions. Use the actual current source and the cited reuse seams. All work remains bounded by the intent and authority documents. The native plans are inert until an authorized root validates and applies them.

## Ordering and cross-authority work

Start PC-PA1-FAST, obtain the Skills supplier handoff, then absorb the runtime. Complete Skills forwarding/retirement after receiver parity, before final qualification. Continue with frames, knowledge, focus/CLI and laboratory work. Cross-authority handoffs are explicitly listed in `machine/cross-authority.json`; neither native task scheduler can infer a foreign receipt. The root checks it before starting the consumer and uses existing coordination/gates.

The default is one implementer lane per authority, with its aggregate epic last. Do not add prerequisite edges from children to the aggregate. Preserve unrelated active runs and explicitly select the PA1 run. The root may safely reorder independent local work or use bounded subagents, but does not create a durable task for every experiment or prompt revision.

## PC-PA1-FAST — Establish fast source-bound development and useful-answer evaluation

**Purpose.** Deliver an isolated source-mode worker/broker test seam, visible-trace replay and a small warm-model comparison path, with baseline identities and consequential adoption decisions recorded. Prevent production rebuilds from becoming the tuning loop.

**Difficulty / dependency.** medium; local prerequisites: none.

**Deliverable.** Source-import and isolated-state assertions; Deterministic orchestration fixtures and visible trace replay; Fixed quality cases plus held-out variants; Bounded experiment ledger and decision capture.

**Acceptance.** Request-only candidate changes can be exercised without rebuilding/deploying production; failures are classified by layer and counted tests bind real source.

**Source seams.** S02, S13, S15, S22, S23, O02.

**Test gate.** `tests.assistance.test_fast_loop` is a proposed actual-source suite to create or map deliberately to equivalent existing tests. It must contain nonzero executed tests and cover the task's scenarios in the acceptance matrix. Do not satisfy it with package checks, empty discovery, or fixture-only model claims.

## PC-PA1-RUNTIME — Absorb the owned local-worker runtime without changing its contracts

**Purpose.** Move PC-owned observer/inference machinery and relevant tests into Project Control using a verified supplier inventory, update consumers and release bindings, and prove baseline parity before behavioral expansion. Keep one canonical implementation and preserve independent Todo/CUDA/ctxpp authority.

**Difficulty / dependency.** medium-high; local prerequisites: PC-PA1-FAST.

**Deliverable.** Canonical runtime package and compatibility seam; Updated source/release/entry-point bindings; Consumer and policy migration manifest; Parity and denied-authority tests.

**Acceptance.** PC no longer needs mutable Skills runtime source for its owned inference machinery; old paths have one implementation, compatibility and pinned qualification.

**Source seams.** S01, S08, S09, S14, S15, S23.

**Test gate.** `tests.assistance.test_runtime_binding` is a proposed actual-source suite to create or map deliberately to equivalent existing tests. It must contain nonzero executed tests and cover the task's scenarios in the acceptance matrix. Do not satisfy it with package checks, empty discovery, or fixture-only model claims.

## PC-PA1-FRAMES — Make continuations suspend, wake and release resources correctly

**Purpose.** Extend the existing private broker with bounded continuations, typed waits and wake-up reconciliation. Release leases during dependency or long-effect waits; retain generation/effect fencing, truthful deadlines, two-slot capacity and verified GPU eviction.

**Difficulty / dependency.** high; local prerequisites: PC-PA1-RUNTIME.

**Deliverable.** Slice/continuation protocol and compatible state persistence; Bounded wait graph and admission accounting; Independent deadline/cleanup supervision; Trusted request-local policy selection; Power-control state core and resource veto.

**Acceptance.** Two parents can suspend for children without deadlock or occupied model leases; restart, expiry, late results and cleanup races remain correct.

**Source seams.** S04, S05, S13, S14, S15, S17, S18.

**Test gate.** `tests.assistance.test_frames` is a proposed actual-source suite to create or map deliberately to equivalent existing tests. It must contain nonzero executed tests and cover the task's scenarios in the acceptance matrix. Do not satisfy it with package checks, empty discovery, or fixture-only model claims.

## PC-PA1-KNOWLEDGE — Serve goal-linked prepared context with valid evidence and freshness

**Purpose.** Add bounded assistant-owned notes, goals and experiment references using existing packets and trace determinants. Integrate access-filtered retrieval into current information paths without altering exact cache behavior or claiming canonical Todo publication.

**Difficulty / dependency.** medium-high; local prerequisites: PC-PA1-FRAMES.

**Deliverable.** Derived notebook provider and source-based invalidation; User-owned focus/goal cards with provenance; Evidence retention pins independent of recent answers; Bounded relevant context in existing discovery/evidence paths.

**Acceptance.** Useful context survives appropriate retention, stale material is explicit, unrelated changes avoid blanket invalidation, and inferred notes never become authority silently.

**Source seams.** S05, S06, S07, S19, S20, S25.

**Test gate.** `tests.assistance.test_knowledge` is a proposed actual-source suite to create or map deliberately to equivalent existing tests. It must contain nonzero executed tests and cover the task's scenarios in the acceptance matrix. Do not satisfy it with package checks, empty discovery, or fixture-only model claims.

## PC-PA1-ASSIST — Deliver focused automatic assistance and a thin conversational control plane

**Purpose.** Implement opted-in change-driven assistance, coalescing and no-change backoff over the existing graph, with foreground priority, power/quiet controls and a simple nonresident chat/CLI. Produce decision-relevant context and ideas rather than continuous summaries.

**Difficulty / dependency.** medium-high; local prerequisites: PC-PA1-FRAMES, PC-PA1-KNOWLEDGE.

**Deliverable.** Time-bounded focus and deterministic attention selection; Change coalescing, no self-trigger and bounded queues; Quiet/demand-only/GPU-release CLI and chat proposals; Decision-linked findings and dismissible goal-aware handoff.

**Acceptance.** An opted-in moving-source demonstration creates useful context; quiet survives restart and no-change input makes no model calls; UI waits never reserve a model.

**Source seams.** S02, S03, S07, S14, S23, O01, O02.

**Test gate.** `tests.assistance.test_assistance` is a proposed actual-source suite to create or map deliberately to equivalent existing tests. It must contain nonzero executed tests and cover the task's scenarios in the acceptance matrix. Do not satisfy it with package checks, empty discovery, or fixture-only model claims.

## PC-PA1-LAB — Turn permitted questions into isolated tests and mechanism experiments

**Purpose.** Reuse snapshot, patch and verification machinery for a service-owned scratch laboratory. Support bounded CPU tests and explicitly authorized GPU experiments that release inference resources first. Return empirical evidence and unpromoted candidates only.

**Difficulty / dependency.** high; local prerequisites: PC-PA1-FRAMES, PC-PA1-KNOWLEDGE.

**Deliverable.** Consistent dirty-source materialization with containment; Typed experiment request/receipt and bounded effect runner; CPU/GPU execution policy and owned-process cleanup; Counterexample/negative-result/patch evidence without canonical application.

**Acceptance.** A new scratch test detects a fixture defect, resource and repository sentinels remain intact, and a GPU experiment never waits behind its own retained inference lease.

**Source seams.** S08, S10, S11, S12, S14, S15, S19, O03, O04.

**Test gate.** `tests.assistance.test_lab` is a proposed actual-source suite to create or map deliberately to equivalent existing tests. It must contain nonzero executed tests and cover the task's scenarios in the acceptance matrix. Do not satisfy it with package checks, empty discovery, or fixture-only model claims.

## PC-PA1-QUALIFY — Qualify useful workflows and promote one reversible candidate

**Purpose.** Measure prepared-context usefulness, foreground overhead, bounded real-model behavior and permitted GPU lifecycle on the integrated source. Preserve current MCP contracts, test migration/rollback and promote only a selected candidate with explicit activation settings.

**Difficulty / dependency.** high; local prerequisites: PC-PA1-ASSIST, PC-PA1-LAB.

**Deliverable.** Executed acceptance matrix with source/measurement identity; Finite model-quality comparison and end-to-end usefulness report; MCP compatibility and old/new runtime isolation results; Candidate promotion/rollback and explicit feature activation record.

**Acceptance.** The complete product is useful on representative cases and safe under lifecycle pressure; no fixture-only result is labeled live qualification and unsupported optional features stay off.

**Source seams.** S01, S03, S04, S05, S15, S22, S23, O03, O04.

**Test gate.** `tests.assistance.test_release` is a proposed actual-source suite to create or map deliberately to equivalent existing tests. It must contain nonzero executed tests and cover the task's scenarios in the acceptance matrix. Do not satisfy it with package checks, empty discovery, or fixture-only model claims.

## SK-PA1-HANDOFF — Prepare a bounded local-worker supplier handoff

**Purpose.** Inventory runtime files, policies, consumers and source identities required for Project Control ownership; preserve active Skills edits and domain guidance. Produce an auditable handoff without enabling dormant coding or deleting the supplier prematurely.

**Difficulty / dependency.** medium; local prerequisites: none.

**Deliverable.** Hashed runtime/policy/test inventory; Consumer inventory including CUDA qualification and native maintenance; Agreed temporary compatibility and catalog transition contract.

**Acceptance.** The receiver can identify and move a bounded dependency closure without losing authority rules, policies or consumers.

**Source seams.** S08, S09, S10, S11, S12, S13, S14, S15, S16.

**Test gate.** `tests.assistance.test_lcw_handoff` is a proposed actual-source suite to create or map deliberately to equivalent existing tests. It must contain nonzero executed tests and cover the task's scenarios in the acceptance matrix. Do not satisfy it with package checks, empty discovery, or fixture-only model claims.

## SK-PA1-RETIRE — Complete compatibility forwarding and retire duplicate runtime ownership

**Purpose.** After receiver parity, repoint authorized consumers and catalog/bootstrap guidance, keep one canonical runtime, and retain historical/dormant coding material with truthful status. Preserve Todo/CUDA/ctxpp behavior and verified release identities.

**Difficulty / dependency.** medium; local prerequisites: SK-PA1-HANDOFF.

**Deliverable.** Verified forwarders or explicitly retired imports; Updated catalog and installed guidance; No duplicate runtime implementation or authority cycle; Supplier transition tests and handoff receipt.

**Acceptance.** The receiver-owned runtime and Skills consumers operate without duplicate pools or stale source bindings; no legacy mutation tool is silently reactivated.

**Source seams.** S08, S09, S14, S15, S19, S23.

**Test gate.** `tests.assistance.test_lcw_transition` is a proposed actual-source suite to create or map deliberately to equivalent existing tests. It must contain nonzero executed tests and cover the task's scenarios in the acceptance matrix. Do not satisfy it with package checks, empty discovery, or fixture-only model claims.

## Scope handling

Scopes are deliberately coarse enough for coherent serial outcomes. They are not permission for unrelated refactors. When current work conflicts, coordinate through the existing protocol instead of resetting or overwriting it. Routine implementation details are delegated; product/authority changes follow the decision register. `no_change_required` is valid only when equivalent current implementation and applicable acceptance evidence are demonstrated. Final aggregate completion follows verified outcomes and required handoffs, not merely an empty queue.
