# PA1 qualification handoff — working status

**Decision:** live qualification is in progress and remains unaccepted. The prior candidate at commit `6ac6bec6e6311e715899ecdc3c291a3e210bdfed` built and passed isolated runtime/dependency smoke checks, but it is not the latest source. A new candidate for `57105ac814d8de8f28123d12874e1a9fa3b78e3d` is being built as `pa1-final`; its final receipt is pending, and no cutover or activation has occurred. All five latest native `PC-PA1-LAB` stage gates passed, but root retains final task disposition and A31 is a genuine failed package-case attempt. The advisory 40-case matrix is not blanket accepted. The full evidence index is [`live-validation.json`](live-validation.json); the case matrix and root decision state are in [`acceptance-results.json`](acceptance-results.json).

## Candidate and native gates

The previous offline candidate receipt binds release manifest `f0dbf88dc39f9042127de278809efb89fcfdc4276e8a6d6e4be013a95ca8171b` and receiver manifest `c29de1b8f58f4804f159730ad46ab7141ee178bc51573feef31ab9da8aed1398`. Runtime binding/import smoke, launcher help, and dependency checks passed on that older source. The new `57105ac` candidate is not yet receipted; root retains cutover and activation.

The latest native LAB gates passed: contained runner (9), capture (25), CLI (8), source integration (16), and LAB service tests (16). Their exact evidence IDs and artifact hashes are indexed in `live-validation.json`. Native gate passage is distinct from package-case acceptance.

## What the live receipts establish

Baseline-r6 attempted four public inquiries in 260.0469 seconds. E01 and E04 returned completed jobs, E02 was partial, and E03 exhausted its deadline. Same-model/binary/settings operation across two distinct slots was observed. The original score report had citation failures. A source-cited `direct_cat` resolver and separate post-hoc rescore made zero model calls and left the original untouched: E01 qualifies its source/citation assessment, E02 and E04 remain partial/unqualified, and E03 was not rescored after deadline exhaustion. That is not an overall A37 pass or a model-trace validity claim. The four-inquiry held-out run completed in 246.7493 seconds: E01 qualified on the changed-limits fixture; E02 and E03 were partial/unqualified; E04 completed but was unqualified. The held-out cap is consumed. Original reports remain preserved.

The extension report includes a live A31 attempt, but the model answer contained no Python test code. The bridge rejected it before CPU execution; no fallback was invented, no scratch tree was created, and canonical files were not changed. Source assessment also found required A31 fixture citations missing. A37 extension citations remain insufficient. A31 remains a genuine failed case; the earlier CPU fixture counterexample does not substitute for model-generated test evidence.

A first GPU preflight failed closed because the controller could not establish quiescence; it performed no GPU execution. The later retry verified release of both inference targets, ran the controller-leased non-model correctness smoke across four visible devices (4096 items per device, expected sum on each), and recorded the foreground owner released with an empty resource list. This supports a bounded hardware-handoff result. It is not an A16 cooperative-delay measurement, a performance benchmark, or proof of every A33 parent-resume and current-power-permission behavior. The GPU effect explicitly limits its claim to a correctness fixture.

Economics r1 failed before inference with a `NameError`. In r2, the report recorded `focus_window_invalid` at 40.8499 seconds and zero public inquiries, but an admission correction confirms one durable job and model process actually started; do not interpret the report count as no inference. A supported one-slot release verified on its first attempt. Root planned r3 with the remaining five inquiries/79 seconds under the 6-inquiry/120-second cap; its actual result is pending. The separate held-out run already consumed four inquiries/246.7493 seconds. Keep automatic work off unless the economics case passes its wait, overhead, and evidence-quality thresholds.

The broad 320-test source-mode suite and 54 subtests remain bound to the earlier `ceabc21507a89163b0c55253180bf5fcc175e857` snapshot, with mocked/scripted inference. On source commit `57105ac814d8de8f28123d12874e1a9fa3b78e3d`, root reports a targeted 15-test/4-subtest run for the focus-grant clock change and 10 focused economics tests. These targeted checks do not constitute a fresh broad source suite or product-case acceptance.

A30 retains the real bubblewrap/cgroup-v2 containment probe: source/host-secret write attempts, `/root/.ssh` and `/dev/nvidia0` visibility, host loopback, artifact bounds, and detached-descendant cleanup were tested. This supports only that disposable runner and those specific paths. Owned-resource recovery remains supported by the prior release-binding and reboot test receipts, but physical recovery and final lifecycle reporting still require their own current evidence.

## Blocking work

- Root records final `PC-PA1-LAB` disposition against the latest five passed gates and the failed A31 attempt. Stage-gate success does not erase the mandatory product-case failure.
- A31 remains a genuine failed live attempt: no Python test executed. Root records its failed disposition; any further model attempt needs updated authority and a valid input. Do not infer a pass or fabricate fallback code.
- Root reviews the completed held-out scores: the four-inquiry/246.7493-second cap is consumed. Do not repeat the held-out set or tune against its labels without new authority. Keep the original live reports and post-hoc assessments separate.
- Root reports the actual r3 economics outcome and reconcile total inference with the r2 admission correction. The planned allowance is five inquiries/79 seconds within six/120 overall. If added foreground wait exceeds 2 seconds, elapsed overhead exceeds 10%, or evidence quality degrades, keep automatic work off.
- Review the partial A33 handoff. A16 cooperative eviction delay, A40 final non-inference lifecycle report, and any cutover/rollback decision remain separate and incomplete.
- Re-fetch the active recovery accounting before any new admission. Prior failures remain counted; do not reset or relabel them.


## Operator-facing behavior

On the selected runtime, ordinary interaction stays explicit and demand-only by default. Model-free controls report or record operator intent; they do not start inference. Explicit `ask`, `run`, and ordinary chat questions are inference entry points. Automatic preparation needs a selected source path, explicit opt-in, and a finite focus window; it remains off unless qualification proves the practical envelope.

```sh
project-control assistance status
project-control assistance goal <project> "Understand the parser's quoted-token behavior"
project-control assistance focus <project> "Review parser edge cases" \
  --repository <alias> --path src/parser.py
project-control assistance quiet --for 45m
project-control assistance release --for 1h
project-control assistance resume --quiet
project-control assistance resume --release
project-control assistance ask --project <project> "What changed in the parser?"
project-control assistance run --project <project> "Check the quoted-token path"
project-control assistance chat --project <project>
project-control assistance handoff <project>
project-control assistance accept <project> <note-id>
project-control assistance dismiss <project> <focus-id> <input-fingerprint>
```

For chat, `/status`, `/quiet 45m`, `/release`, `/resume quiet`, `/resume release`, `/resume all`, `/accept NOTE_ID`, and `/exit` are controls; `/ask TEXT` or any other non-command line asks a question. Waiting for input does not hold an inference session. Handoffs are advisory. Accepting a suggestion records user intent; it does not publish a finding or create a Todo task. See [`usage.md`](usage.md) for full command semantics.

## Limits

The current receipts do not establish accepted overall model quality (despite the completed but mixed held-out run), completed economics, A16 foreground eviction behavior, A40 lifecycle readiness, or a production cutover. A30 containment and CPU fixtures do not provide broader production readiness. Keep qualification pending and automatic work off until root reviews the remaining cases and current resource evidence.
