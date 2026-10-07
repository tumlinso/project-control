# PA1 qualification handoff — working status

**Decision:** live qualification is in progress and remains unaccepted. The source candidate at commit `6ac6bec6e6311e715899ecdc3c291a3e210bdfed` built and passed isolated runtime/dependency smoke checks; it was not promoted or activated. All five latest native `PC-PA1-LAB` stage gates passed, but root retains final task disposition and A31 is a genuine failed package-case attempt. The advisory 40-case matrix is not blanket accepted. The full evidence index is [`live-validation.json`](live-validation.json); the case matrix and root decision state are in [`acceptance-results.json`](acceptance-results.json).

## Candidate and native gates

The offline candidate build receipt binds the clean Project Control source and Skills commits, release manifest `f0dbf88dc39f9042127de278809efb89fcfdc4276e8a6d6e4be013a95ca8171b`, and receiver manifest `c29de1b8f58f4804f159730ad46ab7141ee178bc51573feef31ab9da8aed1398`. Runtime binding/import smoke, launcher help, and dependency checks passed. The source suite was not rerun for this candidate, and no selector, service, or activation change was made.

The latest native LAB gates passed: contained runner (9), capture (25), CLI (8), source integration (16), and LAB service tests (16). Their exact evidence IDs and artifact hashes are indexed in `live-validation.json`. Native gate passage is distinct from package-case acceptance.

## What the live receipts establish

Baseline-r6 attempted four public inquiries in 260.0469 seconds. E01 and E04 returned completed jobs, E02 was partial, and E03 exhausted its deadline. Same-model/binary/settings operation across two distinct slots was observed. The original score report had citation failures. A separate post-hoc rescore made zero model calls and left the original untouched: E01 provisionally passed its citation check, E02 and E04 remained partial, and E03 was not rescored. A two-session owned-release receipt verified on its first attempt. These observations are partial A37 evidence, not accepted quality or held-out results.

The extension report includes a live A31 attempt, but the bridge rejected it before test execution: the advisory supplied no Python test code block, fallback was prohibited, required cited fixture files were missing, and no scratch tree or canonical mutation occurred. No model-authored counterexample test was executed. A37 extension evidence is also partial. A31 therefore remains a failed blocker; the earlier CPU fixture counterexample does not substitute for it.

A first GPU preflight failed closed because the controller could not establish quiescence; it performed no GPU execution. The later retry verified release of both inference targets, ran the controller-leased non-model correctness smoke across four visible devices (4096 items per device, expected sum on each), and recorded the foreground owner released with an empty resource list. This supports a bounded hardware-handoff result. It is not an A16 cooperative-delay measurement, a performance benchmark, or proof of every A33 parent-resume and current-power-permission behavior. The GPU effect explicitly limits its claim to a correctness fixture.

The initial economics harness preflight failed before any inquiry with a `NameError`. Root reports a bounded retry is currently in progress under the 6-inquiry/120-second cap; no completed economics receipt is indexed yet. The separate held-out allowance remains pending (at most four inquiries/300 seconds). Keep automatic work disabled unless the final paired measurements satisfy the predeclared wait, overhead, and evidence-quality limits.

The 320-test source-mode suite and 54 subtests remain bound to the earlier `ceabc21507a89163b0c55253180bf5fcc175e857` dirty snapshot, with mocked or scripted inference. Focused cleanup and runner follow-ups passed on their recorded snapshots. They do not replace the later candidate-bound source suite or live case receipts.

A30 retains the real bubblewrap/cgroup-v2 containment probe: source/host-secret write attempts, `/root/.ssh` and `/dev/nvidia0` visibility, host loopback, artifact bounds, and detached-descendant cleanup were tested. This supports only that disposable runner and those specific paths. Owned-resource recovery remains supported by the prior release-binding and reboot test receipts, but physical recovery and final lifecycle reporting still require their own current evidence.

## Blocking work

- Root records final `PC-PA1-LAB` disposition against the latest five passed gates and the failed A31 attempt. Stage-gate success does not erase the mandatory product-case failure.
- Resolve A31 with a valid exact test-generation input, executed scratch test, valid source citations, and preserved model/trace/result receipts, or record an explicit failed disposition. Do not reuse the rejected answer as a pass.
- Complete source-citation assessment for A37 while preserving original reports, then perform held-out work without tuning against held-out labels. Keep every inquiry and time counter.
- Finish the bounded A38 economics comparison with current source/model/config/resource identities. If added foreground wait exceeds 2 seconds, elapsed overhead exceeds 10%, or evidence quality degrades, keep automatic work off.
- Review the partial A33 handoff. A16 cooperative eviction delay, A40 final non-inference lifecycle report, and any cutover/rollback decision remain separate and incomplete.
- Re-fetch the active recovery accounting before any new admission. Prior failures remain counted; do not reset or relabel them.


## Operator-facing behavior

After a separately authorized candidate installation and activation, the ordinary interaction stays explicit and demand-only by default. Model-free controls report or record operator intent; they do not start inference. Explicit `ask`, `run`, and ordinary chat questions are inference entry points. Automatic preparation needs a selected source path, explicit opt-in, and a finite focus window; it remains off unless qualification proves the practical envelope.

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

The current receipts do not establish accepted overall model quality, held-out performance, completed economics, A16 foreground eviction behavior, A40 lifecycle readiness, or a production cutover. A30 containment and CPU fixtures do not provide broader production readiness. Keep qualification pending and automatic work off until root reviews the remaining cases and current resource evidence.
