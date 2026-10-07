# PA1 implementation rescue: current results

**Transfer status:** the user stopped this pass before final delivery. Read the
[complete transfer record](rescue-transfer-20261007.md) for the final CPU LAB
failure, native claim handoff, uncommitted changes, and residual power accounting.
The pending entries below were intermediate observations.

This is a bounded source-repair record for the current checkout, not PA1
acceptance. The source tree began at `67222b7`; the repairs recorded here were
uncommitted when this note was drafted. Record the integrated commit when the
root finishes delivery. Current Todo status and task authority remain with the
live task/frontier. Interpret old A01–A40 receipts through the
[current acceptance mapping](current-acceptance-mapping.md).

## Source repairs and focused evidence

- **Source and frozen identity:** source-mode runtime inventory is derived
  dynamically from the current bundled Project Control/Todo code and receiver
  files; it does not depend on a manually refreshed receiver manifest. Frozen
  releases retain strict immutable inventory and digest checks.
- **Owner-release recovery:** release recovery now selects only receiptless
  `active` or `release_pending` sessions tied to the exact release request.
  Reclaim still depends on the supervisor's proof that the borrower process
  generation is gone and a valid owner receipt; a snapshot state alone is not
  release proof.
- **Reconciliation reentrancy:** a process-local reentrant lock and per-thread
  nesting depth prevent a same-thread nested reconcile from opening a second
  conflicting `flock`; the outer file lock still serializes across processes.
  This is defensive and covered by focused tests. Live diagnostics later found
  older Codex stdio frontends holding the reconcile lock; the exact contribution
  of same-thread reentrancy to the earlier stalls is not established.
- **Packet retention:** the liveness snapshot preserves packet authentication,
  aliases, expiry, parent lineage, and fully validated packet bodies, while
  applying the additive retention pin. A bounded focused run passed 18 tests;
  independent review found no blocker.
- **Plan preview:** Todo's `todo_plan_validation_failed` is converted into an
  invalid proposal preview. Other Todo read failures still propagate as
  operational errors rather than being mislabeled as invalid proposals.
- **Planner proposal contract:** the prompt now requires the complete,
  nonempty artifact contents and runnable `argv` that strict validation
  already required. The test fixture uses the real nested source paths and
  workspace import shape. Regression tests and review cover the code contract;
  they do not prove live model compliance.
- **Public compute default:** the public model/provider default now selects
  `narrow`; the public `wide` route is retired. Retirement of the supervisor's
  executable `wide` path still awaits owner cleanup and is not complete.
- **Release receipt transaction ordering:** a focused repair moves the close
  receipt transition into the owner-release transaction boundary. The existing
  closed-cap record can still block stop; the worker is implementing
  whole-epoch proof-based recovery while retaining the original receipt and
  audit. Do not infer recovery completion from the transaction-order fix.
- **Test fixtures and diagnostics:** the owned-release test imports its helper
  package-qualified. The read-only audit bounds each MCP call to 15 seconds and
  checks authoritative sentinels in a `finally` path, including when a call
  times out.

The integrating root reports **56 focused source tests passed in 20.64 seconds**
across source release identity, reconcile reentrancy, owned release, plan
preview, packet retention, and concurrency; this includes the earlier 38-test
selection. The packet-retention repair also has a separate **18-test focused
pass**. The default smoke passed **23 tests in 7.58 seconds**. An explicit
`tests --collect-only` pass collected **2,135 tests in 1.96 seconds**. These are
distinct signals: pytest's default `--collect-only` still collects only the
23-test smoke selection. Collection is discovery evidence, not assertion
coverage. A later focused group reports **11 planner tests**, plus **67 related
tests passed in 1.75 seconds**, one existing skip because the delegated
systemd user unit is unavailable, and three subtests. The older 2,128
collection count is historical.

## Live observations and pending work

| Journey or control | Current record |
| --- | --- |
| Initial grounded inquiry | Interrupted with return code `-15` after 345.51 seconds. The first model turn completed, but the caller did not obtain its terminal response while stalled in reconciliation. |
| Grounded inquiry exact retry, first attempt | Interrupted after 129.29 seconds. Timing showed about 45 seconds in the model RPC and about 90 seconds with workers waiting on the reconcile `flock`. A host census found older Codex stdio frontends holding that lock. The root restarted the three identified older stdio frontends and HTTP service while preserving owner state. This narrows the contention evidence; it is not a broad latency or root-cause qualification. Preserve the failed-attempt artifacts. |
| Owner-proven assistance stop and native cleanup | Stop succeeded after the source-client repair. Subsequent Todo cleanup reported no active tasks or slots, `released_verified=5`, and `superseded=2`. That is task-store cleanup evidence, not current supervisor/model release evidence. |
| Grounded inquiry exact retry, later attempt | Returned exit code 0 in 99.819 seconds with the correct setup, smoke-test, targeted-test, restart, and no-wheel guidance. The answer named README/development references and surfaced evidence-packet findings, but remained `partial`: it carried a historical-command freshness warning and `sources=[]`. This is not a fully grounded/cited acceptance result. Keep the exact retry/citation requirement open and retain its receipt. |
| CPU LAB odd-tail, first current attempt | Failed before any experiment after 28.329 seconds because the public result lacked required artifacts. The runtime fingerprint stayed stable. Cancellation completed with `cleanup_pending=false`, but the helper reported `cleanup_verified=false`; full cleanup is **not verified**. Preserve the receipt and do not turn this pre-effect failure into a pass. |
| CPU LAB prompt and fixture repair | The original planner prompt did not request artifacts required by the strict validator. The prompt now requests nonempty full contents and runnable `argv`; the fixture was corrected to use real nested source paths and workspace imports. Regression/review evidence is not evidence of model compliance. |
| CPU LAB exact retry | **Pending under the active task.** Require the generated scratch test, interpretation, effect status, unchanged canonical sentinels, and independently verified cleanup. Keep all failed-attempt files. |
| Current inference owner | **Pending release verification.** The release veto remains true and automatic preparation remains off; the model is still owned and idle pending verification. An earlier successful stop is a point-in-time result and does not establish current release. |
| Operator controls and final cleanup | **Pending readback.** Initial state was automatic preparation off with the release veto active. Restore and verify both values after the authorized live journeys. |

The source reentrancy guard is a defensive repair supported by focused tests;
the observed old lock holders explain concrete contention but do not prove the
guard was the live stall's original cause. A completed model turn is not a
completed user inquiry. The later answer was useful but lacked source citations
and retains a freshness warning. The root is investigating these answer and
freshness findings; this note records no further source changes for them. Keep
full grounded acceptance and CPU LAB open until their required receipts and
cleanup evidence are recorded. The current partial answer has mixed provenance:
direct `cat` evidence supports the development-guide commands, while two other
cited commands lack proof and one excerpt was truncated. Do not weaken freshness
checks or claim broad grounded acceptance.

Two unrelated stale-dispatch recovery attempts were denied by automatic
approval outside this PA1 work. Their records were not changed; preserve them
for root-owned recovery handling.

## Acceptance boundary

This rescue does not complete PA1. Comprehensive A01–A40 acceptance, broad
model-quality evaluation or profiling, real CUDA LAB yield/run/release/rewarm,
full cancellation/crash recovery, and frozen-release qualification remain
deferred. Do not mark the epic accepted or enable automatic assistance based on
these repairs or focused tests. Keep the [source qualification guide](source-qualification.md)
and [current acceptance map](current-acceptance-mapping.md) as the operating
references for subsequent work.
