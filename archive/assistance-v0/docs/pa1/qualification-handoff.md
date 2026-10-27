# Historical PA1 qualification handoff — R10-era evidence

> **Historical record, not current operating instructions or Todo status.**
> This file describes the October 6 R10 candidate, its retired entry points,
> tool counts, artifact locations, and task handoff. Do not run its command
> recipes or infer current task state from it. The repository was subsequently
> moved to bundled Todo and checkout-first source development. Use the current
> [source qualification guide](source-qualification.md),
> [current A01–A40 mapping](current-acceptance-mapping.md), and the live Todo
> task/frontier for current interfaces, authorization, and status. Current
> service checks are documented in [deployment](../deployment.md).

The receipts and dispositions below are preserved as evidence of that
candidate only. They have not been rewritten to imitate current runtime state.

**Decision:** the `5a302b1` delivery candidate is built, selected, rollback-tested, and reselected. The native `PC-PA1-LAB` implementation task has a terminal handoff. Overall `PC-PA1-QUALIFY` and the mandatory 40-case product matrix remain partial and unaccepted; `final_acceptance` remains false and automatic assistance stays off. The exact evidence digest is [`live-validation.json`](live-validation.json), with per-case dispositions in [`acceptance-results.json`](acceptance-results.json).

## Candidate, cutover, and native task

The immutable candidate is bound to frozen Project Control production source commit `5a302b153aa6e941f78b65bb3b821ba40fc21957` and Skills commit `5c69981f8d1755c3408e4f45adbf340c02c4dfe8`. The builder preserved and recorded a docs-only Project Control worktree change; the production source identity remains the recorded commit. Its release manifest SHA-256 is `068e395439b4b6122add2f17c2ff0588552a57f0ea7b700ea2aaf43b3d927b7e`. Isolated runtime identity/import checks, launcher help, and a 41-package dependency check passed. Focused tests after the source correction passed: 16 attention tests plus 6 subtests, and 10 economics tests. The build itself did not rerun a broad suite or call service endpoints.

The selector was changed atomically to the candidate, rolled back to the prior candidate, then set to the delivery candidate again. The rollback and final selection changed only the current symlink; no workspace, configuration, Todo, or state data were restored or deleted, and prior candidate releases remain intact. Fresh HTTP/MCP checks later verified delivery binding. The initial transient connection-refused check is retained alongside the successful retries.

All five native LAB delivery gates passed. The native handoff receipt records handoff `4c152a62-2913-4873-915c-8e2d30ac4cf9`, terminal/idle status, released claim, and Project Control projection revision 1111. That revision identifies the native handoff projection; the runtime artifact remains bound to production source commit `5a302b1`, not the later documentation/projection revision. The LAB implementation task is finished and handed off; it does not finish `PC-PA1-QUALIFY` or accept the 40 product cases.

## Product-case evidence

Baseline-r6 attempted four public inquiries in 260.0469 seconds. The direct cited-source assessment qualified baseline E01 only; E02 and E04 remain partial/unqualified, and E03 exhausted its deadline. The separate held-out run used all four inquiries in 246.7493 seconds: E01 qualified on the changed-limits fixture, E02 and E03 were partial/unqualified, and E04 completed but was unqualified. Its cap is consumed; preserve original reports and do not tune against those labels.

A live A31 answer contained no executable Python test. The bridge rejected it before CPU execution, used no fallback, created no scratch tree, and did not mutate canonical files. Missing source citations also prevent acceptance. The preceding CPU odd-tail counterexample is overlap evidence only. A31 remains failed.

A33 remains bounded handoff evidence: the initial quiescence-timeout attempt failed closed without GPU execution; the retry verified inference-resource release and ran the controller-leased non-model correctness probe on four devices, each checking 4096 items and the expected sum. This is not A16 cooperative-delay evidence, a benchmark, or full A33 parent-resumption/current-power qualification. A16 remains unqualified.

A40 now has a bounded non-inference lifecycle receipt: all 13 declared checks passed, with no NVIDIA compute processes or residency markers, inference units and known supervisor stopped, candidate-bound HTTP observer allowed, isolated job stores terminal with dispatchers stopped, and the exact foreground GPU owner terminally released with no resources. This qualifies the lifecycle report's bounded scope only, not the product as a whole.

## Economics and final resource state

The economics sequence stopped after three attempts over 85.4405 seconds. R1 failed preflight. R2's report showed zero public inquiries, but its correction proves one owned model process started. R3 passed focus preflight and stopped at a production `AttentionController.record_result`/`JobService.preparation_lookup` call-signature error after two attempts and 44.5906 seconds. Its `inference_performed=false` field does not prove no inference: the correction proves one owned model server, while terminal answer completion is explicitly unproved. The automatic preparation job and known nonterminal jobs were cancelled through supported service paths with history retained. The source fix and strict targeted tests are included in the selected delivery build. This finite qualification run stopped; any continuation starts from saved evidence under an updated finite plan. Automatic work remains disabled.

The final operator status is model-free and demand-only. Its projected power field says `physical_state=pending` with zero verified sessions, which is not physical release evidence. Owned release receipts and the independent final census provide that evidence: the census reports zero NVIDIA compute processes, no residency markers, stopped inference units, terminal isolated jobs, absent known supervisor, and the released GPU foreground owner. Do not use the status projection as a substitute for physical-resource proof.

## Root-owned integration boundary

Root retained the final integration because the production call-site correction crossed `AttentionController` and `JobService`; the corrected source, immutable candidate identity, native handoff, selector cutover/rollback, fresh HTTP/MCP checks, and physical quiescence needed to reconcile to one delivery lineage. The focused implementation and tests remained bounded. Candidate promotion, rollback, runtime entrypoint verification, final resource census, and aggregate acceptance are integration/lifecycle decisions and remain with root.

## Operator interaction and reconnect

The selected runtime is `/home/tumlinson/.local/share/project-control/releases/unified-20261007-pa1-delivery`, release manifest `068e395439b4b6122add2f17c2ff0588552a57f0ea7b700ea2aaf43b3d927b7e`. Demand-only operation is active: `project-control assistance status` returned `status=ok`, `demand_only=true`, notifications off, no focus, and `automatic_enabled=false`.

After changing the selector, an already-open MCP client connection may still refer to the previous runtime. Disconnect/reconnect the client transport to create a fresh MCP connection, then verify initialize/tools/list reports 12 tools and schema SHA-256 `9223c29700eac956491b838388ba3a1da20caa68951bfc0a0d361c8d2b13595e`. The verification invoked no MCP tool method and made no inference request. Fresh HTTP checks returned 200 for `/healthz` and `/version`; `/readyz` returned 503 as expected while inference is disabled. A first probe's connection-refused responses are retained; later ready and final checks verified startup and candidate binding.

Use the ordinary controls below. They preserve explicit, demand-only behavior; `ask`, `run`, and non-command chat lines are inference entry points. Keep automatic preparation off until the full A38 case is accepted.

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

For chat, `/status`, `/quiet 45m`, `/release`, `/resume quiet`, `/resume release`, `/resume all`, `/accept NOTE_ID`, and `/exit` are controls; `/ask TEXT` or another non-command line asks a question. Waiting for input does not hold an inference session. Handoffs remain advisory. Accepting a suggestion records intent; it does not publish a finding or create a Todo task. See [`usage.md`](usage.md) for command semantics.

## Remaining acceptance work

A31 remains a failed model-generated test case, A37 remains partial despite two case-level source assessments, and A38 stopped without a terminal answer. Any continuation starts from saved evidence under an updated finite plan; do not infer missing model answers or reset used budgets. Keep automatic assistance off.

A16 is unqualified. A33 supports bounded handoff only. A40 has a bounded receipt-qualified lifecycle report, not full product acceptance. The native LAB implementation task and delivery cutover are complete, but `PC-PA1-QUALIFY` and the 40-case matrix remain partial. Root retains the aggregate decision.
