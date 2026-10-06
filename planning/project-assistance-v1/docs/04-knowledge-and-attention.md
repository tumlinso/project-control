# 4. Prepared knowledge, goals, and bounded attention

## Use the existing graph; do not ask a model to recreate it

Source membership, hashes, imports, exported relationships, task scopes, and known interface dependencies should be obtained deterministically through current providers. A model should add an explanation, a useful synthesis, an uncertainty, or a discriminating experiment—not retype an AST into a prose index [S06, S07].

Start from focused source changes, current user goals, and actual incoming questions. Reuse material dependency manifests to identify what became stale. Stat and watcher events are invalidation hints; source hashes and provider determinants are evidence. Treat a whole-repository HEAD change as insufficient to invalidate every note. Conversely, unchanged file bytes do not establish unchanged behavior when relevant configuration, generated inputs, compiler, or dependencies changed.

A small debounced polling/reconciliation loop over opted-in workspaces is sufficient initially. Reuse any working watcher support discovered during implementation, but do not require a new watcher framework. A missed event or service restart triggers bounded reconciliation. No-change reconciliation should require no model call.

## The derived notebook

Add an assistant-owned, access-filtered layer, not a second canonical project graph. A useful record contains:

- a kind: source fact, explanation, hypothesis, open question, experiment result, negative result, or suggestion;
- an exact subject/goal anchor, the claim, and a short reason it matters;
- supporting packet/artifact references and material dependency identities;
- scope and applicability: snapshot, workload, environment, configuration, and any known exclusions;
- freshness state, creation/recheck time, and links to superseded or contradicting observations;
- next verification or proposed action when one is genuinely useful;
- provenance that distinguishes user instruction, code observation, test result, and model inference.

Keep the schema small. Do not require every note to contain every field. A one-line source fact and a multi-run benchmark result have different needs. Treat confidence as an epistemic label and explanation, not an uncalibrated model-generated probability.

Separate three storage lifetimes. Recent inquiry answers retain their existing exact-cache behavior. Active continuations pin the evidence needed to resume. Selected useful notebook records retain their supporting evidence independently, with explicit capacity/retention controls. A useful note must not silently lose its only source because fifty unrelated questions were answered. If a retained body expires, expose that loss and re-fetch before calling it current.

Do not populate the public recent-answer log with thousands of automatic micro-questions. Background material belongs in the derived notebook and can be retrieved as evidence for a new inquiry. Exact public cache identity and negative-cache behavior remain intact [S05].

## Reading and promotion quality

The fast path is a current exact cached answer, then a bounded retrieval of relevant prepared material, then targeted fresh reads, then deeper synthesis or a permitted experiment. Retrieval candidates are not automatically accepted answers. Validate access and required material freshness before using them. Reusing an old conclusion as attributed history is different from asserting that it was freshly verified.

Skill entry and resource proof rules remain enforced. A summary of a skill is not a substitute for the required authoritative entry/resource consultation. Preserve the applicable source bodies or valid re-fetch references through compaction; avoid the current failure mode in which indiscriminate oldest-first dropping can discard foundational evidence. Missing proof is explicit, not reconstructed from a guessed hash [S13].

Canonical Todo findings, context notes, decisions, orientations, and relations remain behind their current publication capabilities [S19, S20]. The assistant can collect derived evidence without a project claim. It cannot impersonate an implementer lane to publish that evidence canonically. Suggestions become candidate records; the user or a duly authorized task owner promotes them through the existing front door. No autonomous backlog inflation.

Repeated model agreement does not make a fact independent evidence. Preserve conflicting observations rather than compressing disagreement away. A self-generated note cannot become a new source proving the same note. Prefer the original test/source witness when ranking evidence.

## Attention without a learned scheduler

An opted-in focus record should say: goal, projects/paths, start/end or duration, permitted work classes, resource ceiling, exclusions, and notification preference. User focus outranks inferred interest. Existing active work, recent material changes, requested questions, a failing test, or a stale high-use note can nominate candidates. Merely having a registered repository does not make it an automatic exploration target.

Use simple ranking tiers initially: explicit request; directly useful to active work; refresh a used fact; cheap high-information check; speculative opportunity. Within a tier, prefer a small scope, fresh input, clear decision value, and low expected cost. Do not invent a precise numerical value-of-information model until there is enough observed utility to justify one.

Every candidate needs a material-input fingerprint and a bounded work identity. Coalesce an edit burst into one candidate. Do not re-enqueue unchanged input after a model declines to find anything useful. Cool down failures, cap retries, and exclude service outputs, generated notes, and routine bookkeeping from automatic self-triggering. When input changes too quickly, defer or analyze a named stable snapshot rather than chasing every keystroke.

Limit automatic execution to one slot by default, a bounded number of candidates per focus window, and a cumulative resource budget. Foreground work can use both slots. Waiting work consumes no inference lease. High-value automatic work can be adopted by a foreground inquiry without rerunning the same preparation, subject to exact source/access compatibility.

## Power and quiet controls

Keep automatic-work permission and GPU residency permission as two separate settings.

**Demand-only:** no automatic investigations; explicit supported questions may use inference. **Automatic window:** bounded opted-in work until its expiry. **Quiet until:** prevent new automatic work and automatic descendants until a timestamp or manual resume. **Release GPUs:** drain/evict the owned inference runtime and prevent accidental reload until an explicit release of that veto or its declared expiry.

Quiet mode alone does not necessarily unload warm weights. Explain that distinction in the CLI. Offer an operator choice to combine quiet mode with immediate GPU release or a shorter idle-residency policy. Do not count idle utilization as measured power savings; report resident time and energy only when genuinely instrumented.

Persist control state so a restart does not silently re-enable automatic work. Store time boundaries unambiguously, display the user's timezone, and handle clock changes conservatively. Expiration of quiet mode does not resurrect an expired focus window. Status/health queries must not wake a model. Budget exhaustion and source-change events do not override quiet or GPU-release vetoes.

Recheck permission before effect dispatch, not only when work was first enqueued. Already running scratch commands should reach a bounded safe cancellation point or be stopped as owned process groups; do not abandon resource tracking. State explicitly whether a user request is blocked by GPU-release mode rather than repeatedly trying to reload. Reject a demand that cannot be admitted under the current operator residency veto before creating a negative-cache inquiry. For a previously admitted inquiry, keep its original expiry and existing terminal/cache semantics; do not silently renew it when the operator resumes hardware.

## A thin chat/CLI

Use the same controller behind ordinary commands and a basic conversational shell. Begin with a line-oriented UI, existing logging/progress, and a compact status view. A custom graphical dashboard, permanent chat model, or terminal multiplexer is not required.

Illustrative interactions, not frozen command names:

```
Focus on Cellerator's current packing changes for this work session.
What did you find that changes a decision?
What is the smallest test of that hypothesis?
Run that in scratch space, without changing the repository.
Quiet automatic work until tonight.
Free the GPUs and do not reload them until I resume.
```

The chat model translates requests into typed proposals. Trusted controller code validates effects and required confirmation. A pasted repository instruction cannot turn a question into a grant. The CLI itself remains responsive without occupying a model slot while the user is typing or a test is running.

## Useful creative work to prioritize

**Decision-linked preparation:** attach the two or three relevant facts and the cheapest discriminating check to an active decision. This is more useful than a general subsystem summary.

**Counterexample mining:** when an agent asserts equivalence or generality, generate a narrow test case that would falsify it. Preserve scope and the reference implementation. A reviewer prompt is not independent verification.

**Negative-result reuse:** retain why a mechanism lost on a specific workload or why an experiment was inconclusive. Revisit it only when its determinants change.

**Cross-workspace transfer:** propose that a mechanism in one project addresses a demonstrated bottleneck in another, with the source relationship and the missing experiment made explicit. Avoid speculative associations presented as dependencies.

**Goal-aware handoff:** provide a compact explanation of what changed, why it matters to the current long-term goal, what remains uncertain, and the next valuable action. Prefer this at handoff or a decision boundary, not as a constant interruption.

These are objectives for the same few behaviors, not five more permanent profiles.
