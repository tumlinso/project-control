# PA1 qualification handoff — working status

**Decision:** qualification is not accepted yet. The package's 40-case matrix is an advisory product-evidence record; it does not complete the native `PC-PA1-LAB` task or the native `PC-PA1-QUALIFY` task. Root owns the active LAB claim and must record its terminal Project Control task transition separately. The native LAB gate result is distinct from every product case in the 40-case matrix. Root has reported all five required native `PC-PA1-LAB` stage gates passed for `runPC-PA1-RUN-1`; the gate receipts are indexed in [`acceptance-results.json`](acceptance-results.json). The native `finish_task` terminal result and root acceptance are still pending. No completed `PC-PA1-QUALIFY` outcome is recorded.

The current case-by-case draft is [`acceptance-results.json`](acceptance-results.json). It records 16 CPU scenarios with direct source-level coverage, 14 CPU scenarios with remaining gaps, A20 as unrun, A30 as covered only within the tested sandbox scope, and all eight live-model/live-GPU cases as unqualified. Root retains the acceptance decision. The CPU supplement itself sets `final_acceptance=false` and `root_review_required=true`.

## What the receipts establish

The native LAB evidence index records five passed stage gates: contained runner (`8dfb7622-0b6d-44d6-92ec-4ddb6fef0c52`), capture (`c75f051b-c598-42b6-a2d9-10f2f86618ee`), CLI (`a28f6f04-d10f-48a9-9991-9b478330fc4a`), source integration (`8b2c95a5-8f6c-4886-8849-287b89704f0a`), and LAB tests (`0374f673-93f5-4b2c-b4e7-bffc94327e07`). This completes the required stage gates for the no-code-integration LAB task, pending its native terminal `finish_task` result and root acceptance. It does not convert the advisory 40-case product matrix into qualified evidence.

The source-mode test run recorded in [`current-source-tests.json`](current-source-tests.json) passed 320 tests and 54 subtests with no failures. It used repository source, private fixture databases, and mocked or scripted inference providers. Its checkout identity is `ceabc21507a89163b0c55253180bf5fcc175e857` with a dirty worktree and receiver manifest SHA-256 `9e60122d80dd57c045226df9180ba676099b879137d3359975c8a1302c4b78fb`. Focused follow-ups passed the one descendant cleanup test and the nine-test runner suite after the no-swap-cap adjustment. These source identities predate later root edits; the broad suite was not rerun after those edits.

The exact-case supplement in [`acceptance-cpu-supplement.json`](acceptance-cpu-supplement.json) records bounded additions for A13, A14, A18, A19, A21, A27, A29, A32, and A36. It closes A18/A19/A32/A36 at CPU/source scope; A13/A14/A21/A27/A29 remain partial; A20 remains unrun. The negative A32 comparison is one contained sample, not a benchmark or speedup result. A31's real odd-tail counterexample is a CPU fixture probe and does not demonstrate model-generated test mining.

A30 has real bubblewrap/cgroup-v2 runner evidence: the probe checks writes to the mounted source and a temporary host-secret path, visibility of `/root/.ssh` and `/dev/nvidia0`, host loopback, bounded artifacts, and detached-descendant cleanup. This supports that disposable runner's sandbox behavior. It does not establish an installed production sandbox or broader network behavior beyond the tested paths.

Two separate recovery validations are recorded: [`owned-release-binding-validation.json`](owned-release-binding-validation.json) reports 23 tests and 20 subtests plus one exact released-slot proof; its second slot used ordinary owned-daemon shutdown after a stale capability, so no second public acknowledgement was fabricated. [`reboot-recovery-validation.json`](reboot-recovery-validation.json) reports 22 passing tests but says physical recovery remains pending a verified candidate retry. These are recovery evidence, not A16/A33 acceptance or a final lifecycle report.

The active [`live-recovery-plan.json`](live-recovery-plan.json) must be re-fetched before any new admission. Its latest recorded accounting retains six failed initial inquiries (four residency-recovery and two public-source-scope), allows two additional recovery inquiries, caps the initial total including failures at 18, caps successful comparison/extension/economics at 12, holds out four inquiries within 300 seconds, and limits concurrency to two. It also records an economics allowance of six inquiries/120 seconds and a maximum active execution allocation of 799.6 seconds. These counters include failed attempts; do not reset or relabel them. The original qualification limits also keep lifecycle work within 300 seconds without inference and require all owned inference resources to be physically released by the 900-second residency deadline.

The failed attempts are retained evidence. `baseline-r4` attempted four public inquiries but started no model processes because owned-residency recovery stopped on `orphan_marker_identity_mismatch`. `baseline-r5` started model processes for two public inquiries, but the responses were `freshness_unverifiable`; they do not count as completed package cases. No current final host/process census or candidate-cutover receipt is in this handoff's reviewed set, so reconcile current owned resources and candidate identity before further use.

## Blocking work

- Record the native `PC-PA1-LAB` terminal `finish_task` result and root acceptance against `runPC-PA1-RUN-1`. Its five required stage gates are reported passed and indexed above; the CPU supplement and 40-case matrix neither replace nor extend those task gates.
- Keep the `PC-PA1-QUALIFY` outcome pending. Re-fetch the task and prerequisite contracts through Project Control when root moves to that task, then bind all evidence to the selected candidate's current source, receiver manifest, configuration, fixture, binary, model/weights, and server identities.
- Run A20's bounded compaction/re-fetch scenario. Do not treat the existing four-slice worker test as context compaction coverage.
- Run the remaining live-model cases A03, A23, A31, A37, and A38 under the amended retained counters. The earlier public responses with unverifiable freshness did not qualify. A38 must meet the predeclared foreground wait, elapsed-overhead, evidence-quality, and sample requirements or automatic work stays off.
- Run A16 and A33 only with current foreground interlock, permission, exact ownership, process identity, quiescence, and physical release receipts. The two-slot recovery notes do not establish cooperative delay or the GPU experiment handoff.
- Produce A40's single authorized, non-inference lifecycle report with nonzero test counts, current identities, actual resource proof, omissions, and activation choice. The reboot tests alone are not that report.
- Refresh the dirty/untracked source manifest and candidate identity after current root edits; the 320-pass suite and CPU supplement are bound to earlier snapshots. Keep the old release and rollback evidence intact, and do not activate automatic assistance without passing the economics case.

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

No reviewed receipt establishes useful live model answers, held-out performance, assistance economics, actual foreground interference, or a production cutover. A30's tested containment and CPU fixtures do not provide biological validation or production readiness. Until remaining mandatory cases, resource reconciliation, native task terminal records, and root review are complete, keep qualification status pending and automatic work off.
