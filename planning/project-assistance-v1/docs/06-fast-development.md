# 6. Fast development and empirical tuning

## The loop to eliminate

Do not use `edit a few prompt words → rebuild the paired release → replace the live service → ask a new production question` as a development loop. It mixes logic debugging, model-quality evaluation, serving configuration and deployment. It also contaminates comparisons with cold starts, cache identity changes, unrelated source changes and different questions.

Production remains frozen and verified. Development uses isolated source-bound workers, private broker/cache state, fixed fixtures, and deliberately leased model capacity. A development process must assert which code it imported; it must not accidentally test the installed candidate while claiming to test a worktree. Source mode is a separate trusted configuration, not a bypass that relaxes production receipts [S23]. Register synthetic repositories only in isolated development configuration, not in the user's global workspace registry.

## Five test layers

| Layer | Run for | Reuse / execute | Restart or deploy? |
|---|---|---|---|
| A. CPU correctness | Every relevant code edit | Real SQLite/outbox, fake clocks, scripted backend, actual packet validation, selected sandbox tests | Neither |
| B. Visible-trace replay | Prompt/context/protocol changes | Frozen source/tool observations, accepted calls, expected semantic checks, no hidden reasoning | Neither |
| C. Warm-model quality | A declared promising candidate | Source worker + isolated broker against an owned warm endpoint; fixed questions and snapshots | No weight restart for compatible request changes |
| D. Serving calibration | Context capacity, KV type, batch/split, or binary changes | Existing controller-leased harness, grouped settings, source/model/binary hashes | Restart only owned evaluation server when needed |
| E. Release qualification | A selected complete implementation | One frozen candidate, targeted public journeys, lifecycle/authority/eviction checks | Deliberate candidate build and promotion |

Layer A should use the existing constructor seams: injected transport, process factory, clocks, source readers, supervisor fixtures and real local stores [S14, S15]. The tests must execute the real production orchestration code with deterministic models, not a separate toy scheduler with the same names.

Layer B replays visible inputs/results and checks what information the next turn receives, which IDs are legal evidence, how compaction behaves, and whether recovery repeats an effect. It cannot prove the model will choose a good action. Retain the distinction explicitly.

Layer C tests behavior with the actual model. Obtain inference through the legitimate development/supervisor lease path; do not create private uncoordinated pools. If the existing broker cannot safely host candidate code, run the candidate worker and private broker in a separate process while leasing compatible inference, or use the existing foreground-controller-leased calibration path. Do not change production prompts, cache entries or hashes for a trial.

Layer D should extend `scripts/qualify_observer_model.py`, not duplicate it [S22]. The current script already separates thinking sweeps from cold context trials and can keep a server alive across reasoning variants. Use that facility narrowly. Do not rerun every split/context/KV candidate for a prompt change.

## Separate the tunable axes

**Per-request / logical:** system and task instructions, selected evidence, logical context target, compaction choice, reasoning phase allowance, answer/tool-envelope limits, supported sampling fields, and optional policy preset. These should not inherently require a new weight process.

**Per-server / physical:** model artifact, tokenizer/template capability, actual context capacity, KV format, parallel slots, batch/ubatch, split mode, GPU layout and binary. Change these only when the experiment requires it. Record the effective settings, not only requested flags.

**Controller correctness:** deadlines, wait transitions, effect fencing, packet hashes, legal citations, sandbox grants and public cache identity are not prompt parameters. Test them deterministically. No amount of Qwen prompt tuning repairs an unserviced deadline or a lost wake-up.

The current adapter's global generation setter is suitable for exclusive calibration. Production per-frame settings must be request-local and validated, so concurrent frames cannot overwrite one another's budgets [S15]. Keep physical context fixed at the current qualified value initially while comparing smaller logical context selection.

## First bounded comparison

Use a baseline plus at most two deliberately different candidates in an initial comparison, not a Cartesian grid.

- Baseline: current source-bound behavior and budgets.
- Candidate A: evidence-first navigation, compact tool requests, less deliberation on obvious reads, unchanged public output semantics.
- Candidate B: A plus phase-aware reasoning/context selection, retaining a larger allowance for genuine synthesis.

The proposed initial live-comparison ceiling is three configurations, twelve new inference inquiries, and one 900-second wall-budget batch, with no more than the two legitimately leased execution slots. This is a stop budget, not a promised duration or a requirement to consume it. Declare an explicit changed budget before extending a batch. Cheap identical pending-result lookups do not count as new inference, but also do not renew its deadline. The machine policy records these defaults.

These are hypotheses, not frozen prompts. Begin with four representative cases: direct lookup, multi-hop skill navigation, conflicting evidence, and synthesis across source/results. Add a held-out variation and a continuation/context-pressure case for the finalist. The complete case catalog is in `machine/eval-cases.json`; not every case must be run with every candidate. Use the stable synthetic navigation chain for repeatability and the currently installed native skill for a final journey. Do not recreate removed CUDA atlas material or treat an old missing-resource query as a required successful answer.

A reasoning-off/short-budget candidate must not win by returning an empty or unhelpful answer quickly. A larger-budget candidate must not win merely because it uses more context or thinking. Useful source-backed partials count; invented proof, misleading freshness, missing essential task coverage, or unauthorized actions fail.

The included fixture deliberately has a correct direct-limit lookup, a skill navigation chain, outdated guidance, synthetic break-even data, a latent odd-tail bug with passing baseline tests, and an instruction-like source comment. Keep ground-truth expectations in the assessor, not in the model's prompt. Create a deterministic renamed/number-varied held-out copy so optimization does not memorize the fixture.

## Measurements that diagnose rather than obscure

For each case retain: source/model/binary and policy hashes; cold/warm conditions; actual prompt tokens and evaluated/cached tokens; prefill time; requested/effective/used thinking tokens; visible tokens; tool latency and bytes; first useful source-read time; model rounds and successful reads; protocol repairs; frame wait time; lease occupancy; total wall time; completion/partial/failure classification; any `analysis_unavailable` reason and whether it blocked otherwise usable evidence; valid-source and useful-answer assessment; and process restart count.

Do not infer reasoning duration from the public `thinking` status. Do not infer PID stability or ownership from GPU utilization. Do not conflate context tokens with UTF-8 bytes, the physical window with actual prompt length, or source excerpts with generated-answer allowance.

For prepared context, compare equivalent tasks with and without assistance using fixed source snapshots. Include background preparation cost, staleness refresh cost and external-agent context consumed. Savings that disappear once preparation is counted are not free wins. Report small samples as small samples; a deterministic fixture demonstrates function, not a statistically established production improvement.

## A repair decision tree

If a request stays pending too long, inspect admission/startup/transport/deadline/cleanup timing first. If generation ends with invalid JSON, inspect schema/format limits and response metadata. If source proof fails, inspect exact reader output and visibility. If the answer is grounded but incomplete, inspect remaining rounds, read strategy and relevance. Only then change prompts or budgets.

A prompt edit that cannot state a failure hypothesis and expected distinguishing observation is not a useful experiment. Avoid adding repeated caution paragraphs until the policy becomes harder to use. Prefer a smaller, clearer prompt, good current tool schemas, bounded batches, and reliable feedback.

## Stop rules and developer efficiency

Each tuning branch declares its candidate count, cases, and compute/wall budget before starting. Stop after two consecutive non-improving iterations or budget exhaustion. Retain the best supported variant and record remaining uncertainty. More trials require a materially new hypothesis or explicit approval, not impatience with the result.

Do focused tests during development. At an outcome boundary run its relevant suites, then the integrated suite for the selected candidate. The canonical `finish_task` path already runs required gates; do not run a full release cycle before every finish and then rerun the same cycle again without an invalidating change. Reuse evidence only under its actual freshness rules. Current command gates that require fresh execution must still run—do not bypass them to save time [S23].

Use coarse durable tasks and a single default lane. The root may assign bounded independent work to inexpensive Codex subagents; it should not create a new durable lane for each read, fixture, prompt edit or review comment. Report progress at meaningful outcomes. The test harness is early implementation work, not an additional research project that must be perfected before useful product work begins.
