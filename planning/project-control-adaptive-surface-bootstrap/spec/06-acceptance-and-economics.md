# Acceptance, role efficiency, and release evidence

## Completion means user journeys, not names

The machine cases in `contracts/acceptance-cases.json` assign required behaviors to outcome-sized tasks. The implementing agent creates executable conformance tests under `tests/as1/` in the appropriate repository, marks them with their case IDs, and binds them through the supplied acceptance runner. A missing test, unimplemented adapter, skipped required case or prose claim does not satisfy a gate.

Package self-tests validate this bootstrap's integrity, role matrix, dependency topology and helper behavior. They do **not** establish that the new Project Control surface exists. Product gate receipts must come from the target candidate/runtime and include its identities. The delivered `validation/preparation.json` records that distinction.

## Required journeys

1. **First look to exact source:** observer discovers a project, obtains purpose/defining relative paths, reads several files, follows a typed search result, and sees exact source/evidence/freshness without opening a terminal or guessing host paths.
2. **Existing execution:** coder uses current task context, optionally overview, command/native files/skills, targeted evidence and frontier, and completes through the canonical kernel. No automatic overview or giant skill payload, no hidden local delegation, no need to rebuild plans to perform normal work.
3. **Busy scout:** observer submits with hint aliases, receives an accepted ID when saturated, continues useful work, reconnects/polls after model eviction and gets an answer reusing verified evidence. Restart the dispatcher and model independently during the test.
4. **Large skill authority:** observer asks about CUDA/Volta routing, local worker follows native maps, returns modest synthesis, and the broker emits exact direct excerpts. Mutate a fixture resource between selection and read to prove stale selection cannot masquerade as authority.
5. **Cross-project trace:** a Python consumer, C++/generated contract and Markdown declaration in separate fixture projects form a dependency chain. Trace direction and project labels are correct, cycles terminate, stale/new consumers are accounted for, and snippets match the traced generation.
6. **Independent planning:** mutator understands a project using its own tools/native source, creates/validates/applies a plan, amends a relation and observes impact invalidation. Coder/observer cannot call those mutations. No arbitrary config or SQL edits.
7. **Exceptional maintenance:** source-preserving recovery/supersession reuses canonical safeguards and idempotent receipts; destructive examples demand explicit human permission and still respect live-work protections.
8. **Paired live cutover:** exact role discovery and dispatch permissions, installed skills/native instructions, real local inference, host-global idle-model eviction, persistent queue/cache, and rollback compatibility all work on the paired candidate. No NF1A or unrelated project work is resumed.

## Context and coordination economics

Capture a baseline before changing the surface. Evaluate old and new implementations on the same questions/scope/source snapshots, not mismatched cases. Measure:

* correct task/answer outcome and evidence/path usefulness;
* total server/client visible prompt/tool bytes, optional real tokenizer counts, schema/description exposure and total model tokens when metered;
* number of navigation calls, retries, repeated full reads and authority revalidation calls;
* latency to a useful result, queue waiting versus active time, unnecessary model starts, reload/eviction overhead;
* mandatory boilerplate, omitted critical facts, false completeness and unnecessary human prompts.

Do not optimize one response in isolation: smaller output causing five more calls can be worse. No fabricated monetary equivalence between local models and a particular hosted model. Use measured deployment costs where available; otherwise report model tokens/time/energy as separate metrics. No model brand is hard-coded into the architecture.

Start with the budgets in `spec/01` and leave them configurable. Require compact coder/scout journeys to avoid extended outputs and redundant automatic orientation. For observer extended mode, score completeness and useful navigability rather than penalizing all extra text. Target no correctness/coverage regression and less avoidable context/coordination overhead than the current surface. If a journey needs more bytes for necessary evidence, document the justified tradeoff instead of weakening accuracy.

## Security and epistemic tests

Test both discovery and hidden invocation for every role; schemas/descriptions must match actual permissions. Extended cannot be accessed through alternate detail names or legacy aliases. Temporary delegation disabling applies at dispatch, not just listing. Skill/scout modes cannot recursively delegate, publish declarations, or acquire task authority.

Relative-path validation must cover nested symlinks, Git symlink blobs, TOCTOU replacement, traversal/absolute/UNC/drive forms, partial multi-file failures and edited source ranges. Sandbox must remain no-network/read-only with own-process cleanup and bounded scratch; do not use prompt claims as OS isolation.

Test registered-but-unrun versus executed gates, stale versus frozen terminal evidence, lexical candidates versus resolved references, declared relations versus observed semantics, provider absence versus “no impacts,” chronology versus causality, and exact skill text versus paraphrase. Citation/packet-ID existence is not semantic entailment.

## Test construction and gate runner

`contracts/acceptance-cases.json` maps cases to `tests/as1/test_<outcome>.py`. Add the supplied `as1_case` marker to each actual behavioral test (multiple tests may cover a case). `scripts/acceptance_gate.py --outcome ID` runs the configured pytest target with the packaged collection/report plugin and fails if expected cases did not execute and pass. It does not accept a manually typed success receipt.

Do not satisfy product cases with tests that only check schema strings, documentation or mocked declarations where a runtime effect is required. Deterministic fixtures are correct for crash/fencing/authority tests; real installed runtime and GPU interlock cases additionally need executed end-to-end checks. Required live tests must not be skipped and called complete. A hardware block is a visible remaining gate, not a silent scope cut.

## Reviewer focus

Independent review should examine the risky boundaries: durable admission/dispatcher, stale attempt fencing, packet access/alias reuse, graph invalidation of newly added consumers, snippet authority, mutator permission policy and preserved-work handling, and the installed role surfaces. Use economical bounded reviewers; no fixed number of review loops. Fix demonstrated issues and rerun their affected gates. Full qualification remains required before cutover.
