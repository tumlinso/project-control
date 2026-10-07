# PA1 acceptance mapping for the current architecture

This map adapts the original A01–A40 intent in
[`planning/project-assistance-v1/machine/acceptance.json`](../../planning/project-assistance-v1/machine/acceptance.json)
to the bundled-Todo, source-checkout architecture. It is not a fresh execution
receipt. The “recorded result” values are copied from
[`acceptance-results.json`](acceptance-results.json) and describe its older
candidate only; **none of those labels establishes current acceptance**.
Current live status comes from Todo and new evidence attached to the active
task. See the [source qualification guide](source-qualification.md) for the
current small-test and live-evidence boundary.

Historical supplier/adapter paths, exact profile counts and schema hashes,
installed-candidate assumptions, and old qualification commands are not
current interface contracts. This adaptation preserves the user-facing
behavioral requirement unless a row explicitly identifies a retired mechanism.
No case is marked passed here.

| Case | Retained meaning and current route | Recorded result (historical only) |
| --- | --- | --- |
| A01 — Candidate source, not ambient deployment | Use `scripts/pc-dev` to bind imports and runtime provenance to the checkout; source identity follows current PC/Todo code. A frozen release remains separately strict. No installed sibling supplier is required. | `partial` |
| A02 — Replay is real orchestration | Keep replay/evaluation on actual source orchestration and SQLite boundaries with controlled model responses; distinguish fixture replay from the minimal live user journeys. | `partial` |
| A03 — Request-only variants keep weights warm | Preserve warm residency while eligible; idle time alone does not trigger eviction. Full live duration/resource qualification is deferred. | `not_qualified` |
| A04 — Layer-specific failure diagnosis and stop rule | Record actionable proposal, validation, execution, model, and cleanup failures at their owning layer; keep retries finite and explain stops. | `partial` |
| A05 — Supplier dependency and consumer inventory | The external Todo supplier and adapter are retired as executable dependencies. Todo is bundled in PC; the absorbed local observer runtime remains in PC, while delegated coding/research uses configured Codex subagents. Preserve old supplier provenance, not a second implementation. | `partial` |
| A06 — One implementation, baseline protocol parity | Verify imports and behavior against the single bundled implementation and supported compatibility consumers. Do not require parity against the removed supplier as an operating mode. | `partial` |
| A07 — Release identity and incompatible source binding | Source execution derives identity from current checkout code and rejects mismatched process bindings; frozen packages keep strict manifest/package checks. Optional domain-content roots do not select executable code. | `covered_cpu_source_scenario` |
| A08 — Preserved consumers and inactive delegation | Resolve consumers through the PC runtime; use the current profile's live schema. Codex delegation is an external configured-agent workflow. Do not restore retired adapter or dormant public coding-delegation routes. | `partial` |
| A09 — Two parents, two children, two slots | Preserve the bounded concurrency, parent suspension, child completion, and correct parent resumption contract in source tests; the old named test is not itself the interface. | `covered_cpu_source_scenario` |
| A10 — Lost wake-up and duplicate delivery | Preserve durable reconciliation across restart and exactly-once parent wake/result handling. | `covered_cpu_source_scenario` |
| A11 — Cycle and descendant bounds | Preserve refusal of cycles, over-depth work, and exhausted child/global credits without losing accepted foreground work. | `covered_cpu_source_scenario` |
| A12 — Public expiry independent of blocked execution | Public deadlines remain bounded while blocked workers and cleanup stay accounted for; fake-clock/source tests are distinct from live model timing. | `covered_cpu_source_scenario` |
| A13 — Restart and late generation fence | Preserve restart reconciliation and prevent late work from mutating a superseded or cancelled generation. | `partial` |
| A14 — Busy admission, internal children and starvation | Preserve bounded internal progress and foreground admission under saturated public capacity. | `partial` |
| A15 — Frame-local budgets and isolation | Keep policy, budget, and source scope attached to each frame/request; prevent cross-frame or concurrent-request leakage. | `partial` |
| A16 — Cooperative and full eviction are distinct | Preserve cooperative-yield versus verified owner release as separate states and report measured delay. Real active-turn/GPU eviction timing is deferred. | `not_qualified` |
| A17 — Material freshness versus unrelated changes | Invalidate evidence when its actual source/config determinants change; do not stale it merely because unrelated checkout content changes. | `covered_cpu_source_scenario` |
| A18 — Retention outlives recent-answer window | Retained knowledge must keep its supporting evidence or report it unavailable; high-volume retention qualification is deferred. | `covered_cpu` |
| A19 — Exact public cache and historical warning | Preserve exact-request cache identity, refresh behavior, and explicit freshness limits on historical evidence. | `covered_cpu` |
| A20 — Foundational evidence survives compaction | Preserve source references or re-fetch decisive evidence after context pressure; the specific compaction/re-fetch case remains deferred. | `unrun` |
| A21 — No self-corroboration or canonical impersonation | Model output remains advisory; only explicit authorized workflows can create canonical Todo/source changes. LAB candidates remain unpromoted. | `partial` |
| A22 — Trusted cross-project access | Preserve registered-project boundaries and provenance; inaccessible sources remain explicit and partial evidence is not upgraded. | `covered_cpu_source_scenario` |
| A23 — Change shadowing gives useful prepared context | Retain the usefulness goal, but keep automatic preparation off. Current phase does not qualify automatic shadowing or its value. | `not_qualified` |
| A24 — Stable code and self-trigger suppression | Preserve no-change suppression, bounded coalescing, private request roots, and recheck-before-retain behavior. | `partial` |
| A25 — Persistent quiet and focus expiry | Preserve durable quiet/focus expiry and ensure expired or quieted work does not restart after process restart. | `covered_cpu_source_scenario` |
| A26 — GPU-release veto prevents rewarming | Preserve release-veto behavior and prevent rewarm while veto or foreground ownership applies. Full hardware lifecycle evidence remains deferred. | `covered_cpu_source_scenario` |
| A27 — Nonresident conversational UI and grants | Source/model text remains data. Only typed, principal-bound authorization can permit bounded work; UI waiting does not itself hold a model session. | `partial` |
| A28 — Goal relevance and dismissal | Preserve user-goal precedence, provenance, and explicit dismissal semantics for derived suggestions. | `covered_cpu_source_scenario` |
| A29 — Dirty snapshot and canonical sentinel integrity | Preserve dirty tracked/untracked source capture, isolation, and sentinels for checkout, Todo state, index, and canonical files. | `partial` |
| A30 — Sandbox descendants and secrets | Preserve containment of descendants, filesystem writes, secrets, devices, and network access; environment-specific sandbox proof must be reported separately. | `covered_sandbox` |
| A31 — Generate a discriminating unit test | Preserve a real generated scratch test that exposes the odd-tail pairing defect, with source/test evidence and verified cleanup. The old live attempt failed before execution; the current bounded CPU journey must not use a canned proposal or claim success without its own receipt. | `failed_live_preflight_no_execution` |
| A32 — Negative or inconclusive results are useful | Preserve truthful negative/inconclusive outcomes, bounded measurements, and no unsupported speedup claim. | `covered_cpu` |
| A33 — GPU experiment does not wait behind itself | Preserve explicit CUDA admission, owner-proven inference release, cleanup, permitted rewarm, and resumed interpretation. Real CUDA lifecycle qualification is deferred. | `partial_live_hardware_handoff` |
| A34 — Interrupted effect reconciliation | Persist intent before execution; uncertain effects remain unknown until exact reconciliation and are never replayed blindly. | `covered_cpu_source_scenario` |
| A35 — Patch and canonical acceptance remain separate | Scratch candidates remain separate from canonical source; changed source or tampered snapshots invalidate verification. | `covered_cpu_source_scenario` |
| A36 — Exact public interface and denied authority | Discover actual profile tools/schemas from current `tools/list`; verify forbidden operations against that surface. Historical 11/12/16 tool counts and schema hashes are not contracts. Mutator rescue is through bundled Todo and does not require the Todo skill. | `covered_cpu` |
| A37 — Finite grounded model-quality comparison | Preserve grounded usefulness and finite-budget evaluation. The one authorized answer in this implementation pass is a smoke journey only; broad/held-out model-quality qualification remains deferred. | `partial_live_model_not_qualified` |
| A38 — Assistance economics and foreground interference | Preserve honest accounting of startup, inference, waiting, and foreground effects. Broad latency/economics profiling is explicitly deferred. | `incomplete_unqualified` |
| A39 — Versioned state and reversible candidate | Preserve durable state compatibility, source/release identity, and rollback provenance. Source edits run directly from checkout; frozen release qualification is a separate later step. | `partial` |
| A40 — Final resource, permission and provenance report | Report the exact scope and provenance of each run, authorization, cleanup, and resource receipt. The historical bounded census is not a current comprehensive lifecycle or product-acceptance receipt. | `bounded_noninference_lifecycle_receipt_qualified` |

## Current disposition

All forty behavioral intents remain visible in this map; implementation
mechanics that depended on external supplier code, installed-sibling
bootstrapping, fixed tool inventories, or manual source receiver-manifest
refresh have been replaced by the current bundled/source contract described
above. The original machine acceptance package and receipts remain unchanged.

The current authorized implementation boundary is repair plus focused source
regressions and the two minimal journeys specified by the active task. Model
quality campaigns, broad profiling, real CUDA LAB, comprehensive recovery
qualification, and the full A01–A40 matrix remain deferred. A case becomes
accepted only through new, case-relevant evidence bound to the current source
or release identity and an explicit current acceptance decision.
