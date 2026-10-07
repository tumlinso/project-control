# PA1 rescue transfer — 2026-10-07

The user requested an immediate handoff to continue faster in a new chat. Stop
implementation and qualification here. These are current checkout observations,
not full PA1 acceptance. Preserve the dirty tree and all workflow/runtime state.

## Source and development

- Repository: `/home/tumlinson/project-control`.
- HEAD: `67222b73165693ce99ec69df33d2a221b6d73c04` (previous rescue commit).
- **This pass is uncommitted.** No commit, reset, clean, or push was performed.
- Use `scripts/pc-dev setup`, `scripts/pc-dev run ...`, and
  `scripts/pc-dev test <selectors>`. Default tests are a 23-test CPU smoke.
- Source edits require process restart, not a wheel build or receiver-manifest
  refresh. Batch source changes and restart paired services before live cases:
  `systemctl --user restart project-control-inference.service project-control.service`.
- Do not reintroduce external Todo, coding-workflow-mcp, local-coding-worker
  coding delegation, external application catalogs, or the retired wide profile.
- Use named configured Codex subagents. Root owns recovery and native lifecycle.
- Source/deployment guidance: `AGENTS.md`, `README.md`, `docs/development.md`,
  `docs/deployment.md`, `docs/pa1/source-qualification.md`.

## Scope and stopping boundary

The approved pass permitted focused source regressions, one grounded source
answer, one CPU LAB odd-tail counterexample journey (maximum two experiments,
600 seconds), and exact retests after concrete repairs. Broad profiling/tuning,
model-quality campaigns, GPU/CUDA experiments, full-suite execution, complete
A01–A40 qualification, and frozen-release qualification remain deferred. The
whole PA1 epic is **not accepted**.

## Implemented changes in the dirty tree

1. `services/planning.py`: invalid/empty Todo proposals produce ordinary invalid
   previews; unrelated backend errors still propagate.
2. `observer_analysis.py`: a source-only supervisor client validates release
   identity using the dynamic bundled receiver inventory. It retains canonical
   import paths, source hash, Todo identity/fingerprint, and daemon identity.
   Frozen clients retain strict manifest checks.
3. `as1_jobs.py`: defensive same-thread reconciliation nesting support, retaining
   cross-process flock and thread serialization. **Do not describe this as the
   demonstrated root cause of the live stall.**
4. `as1_packets.py` and `as1_jobs.py`: reconciliation batches retention in one
   transaction with one liveness/parent-closure calculation instead of rescanning
   all packets for every reference. Scope checks, aliases, expiration, parent
   retention, full body/model validation, additive pins, and old-owner unpinning
   remain enforced.
5. `assistance/resources.py` and `observer_analysis.py`: exact-intent receiptless
   `release_pending` sessions can enter native orphan receipt reclamation.
6. `assistance/resources.py`: stale-row metadata is projected after the external
   release callback, preserving its committed-state transaction guard.
7. `assistance/resources.py` and `as1_jobs.py`: dedicated audited recovery for
   stale receipts matching the current idle owner. It requires a permanent veto,
   current intent, zero broker work/slots, zero model leases/admissions, matching
   epoch/target/PID generation, and a sealed whole-epoch physical-stop proof.
   Original close receipts are retained. A new stale reconciliation audit table
   is additive. Mixed normal and audited stale proofs have exact-set checks.
8. `assistance/lab_cli.py`: planner instructions now require nonempty complete
   runnable artifacts for `done=false`, exact source citations, and correct
   nested `/workspace` paths. Strict proposal validation was not weakened.
9. `models.py`, `observer_analysis.py`, embedded `local_runtime/local_worker/
   supervisor.py`: public/default/runtime compute-profile requests are narrow
   only. Configured candidate topology still supports two/four GPU layouts;
   stored historical records were not rewritten.
10. Fixture repairs: package-qualified local runtime helper imports; source
    inventory identity for migrated source fixtures; readonly audit per-tool
    timeout and sentinel assertion in `finally`; updated profile fixtures.
11. `scripts/qualify_assistance_scoped_lab.py`: runtime-neutral
    `qualification_cli_*` errors preserve diagnostic receipts.
12. PA1 docs/current acceptance mapping and implementer-start instructions now
    distinguish current source seams from archived R10 evidence. Inert checker
    allows links to repository docs, uses bundled-validator wording, and has
    refreshed manifest rows. Historical acceptance JSON/results are preserved.

## Focused evidence

Counts overlap; do not sum them into a qualification claim.

- Default source smoke: **23 passed, 7.58 seconds**.
- Repair selection (packet/retention/concurrency/source identity/locks/owned
  release/preview): **56 passed, 20.64 seconds**, before later recovery additions.
- Latest stale recovery and demand broker selection:
  **44 passed, 5 subtests** (`tests/assistance/test_owned_release.py` and
  `tests/assistance/test_demand_broker.py`). This includes a negative active-slot
  test preventing owner stop/audit settlement while broker work remains.
- Investigation/provider/LAB source selection: **67 passed, 1 skipped,
  3 subtests, 1.75 seconds**. Existing skip: sandbox lacks delegated systemd user
  unit. It was not added to turn a failure green.
- Narrow runtime retirement: **9 focused tests passed, 0.57 seconds** with fake
  backends; host boundary was needed for a fake temporary-port probe. No model or
  GPU workload. **96 tests collected** in those three modules.
- Readonly audit + timeout/sentinel regression: **2 passed, 4.81 seconds** on
  host; sandbox FastMCP thread bridge had stalled before the repair.
- Runtime-neutral helper error contract: **1 passed, 3 subtests, 0.08 seconds**.
- Explicit expanded collection: **2,135 collected, 1.96 seconds**, before later
  added tests. It is not the final post-change count and is not an assertion pass.
- Inert PA1 package check passes: 40 hashed files, 38 sources, 9 outcomes,
  11 native tasks, 40 acceptance cases, 12 eval cases, 6 Python AST checks,
  20 Markdown links. Native validation/product acceptance/plans applied: not run.
- `git diff --check` passed at handoff. No full suite or release build this pass.

## Live attempts and private evidence

Root: `/home/tumlinson/.local/state/project-control/qualification/pa1-source-implementation-20261007`.
Do not overwrite existing directories or replay ambiguous effects.

### Grounded answer

- `grounded-answer-result.json`: first attempt interrupted at 345.51 seconds.
- `grounded-answer-retry-result.json`: exact retry interrupted at 129.29 seconds;
  stack dumps locate model RPC followed by reconcile contention.
- Readonly census found older source stdio frontends holding/queuing on the
  reconcile flock. Three old stdio processes and HTTP were restarted; the
  inference owner remained intact until verified cleanup.
- After batched retention, `grounded-answer-retention-retry-result.json`:
  return code 0, **99.819 seconds**, correct setup/smoke/targeted-test/restart/
  no-wheel answer, with README/development citations and evidence findings.
- Result is **partial**, `sources=[]`, with a historical freshness warning.
  This is conservative replay behavior, not a proven accidental polling bug.
  One exact development.md command packet has direct-cat SHA proof; two other
  cited command packets lack source-read proof, and one is truncated. Existing
  runner already supports direct-cat and bounded-sed source proofs. Do not
  synthesize hashes or weaken freshness to claim acceptance.

### CPU LAB

- `cpu-lab`: session `lab_session_7a9c8f6eabf34d1db9b8eee7e961b13f` failed
  before effects in 28.329 seconds: `lab_planner_incomplete_proposal`,
  `missing_fields=artifacts`. Cancellation completed. Helper's overall cleanup
  flag was false; no successful journey was claimed.
- `cpu-lab-retry`: session `lab_session_bd4249ef23134c77bdfe3bf5d6c5460b`.
  Run returned in **69.595 seconds**, state **paused**,
  `reason=proposal_or_source_error`,
  `error=artifact path must be a safe relative path`, experiment_count=1.
- Qualification receipt: effect_count=1, effects_cleanup_verified=true,
  runtime_identity_stable=true, mechanical_trial_passed=false,
  answer_quality_accepted=false. **The actual effect/proposal receipts still need
  review; a successful discriminating counterexample is not established.**
- At the user's stop request, supported cancellation returned cancelled with
  cleanup_pending=false. Do not automatically resume/replay it.
- Sources selected under PC checkout root:
  `planning/project-assistance-v1/fixtures/repository/demo/pairs.py` and
  `planning/project-assistance-v1/fixtures/repository/tests/test_pairs.py`.
- Goal was a scratch odd-tail test, canonical source unchanged, CPU python3
  only, no network/GPU experiment, maximum two experiments/600 seconds.
- Retry code identities: PC `36b0649172b23169f8bde1764c356723d907efd46bb451e1c6a33b7010e93402`;
  receiver `064a4abcd6819bfed828bc8581b91d0c4cbdf86f4cee124da979b4f2bec49ceb`;
  Todo runtime `5f3e9524d66f951b9474cda46be507e7ec40d7e41b8502b09e59814a9b705383`.

## Final runtime and Todo handoff

- Supported `assistance stop` returned **stopped** after the retry cancellation.
- Inference service: inactive, PID 0. Automatic preparation off; permanent
  release veto true; notifications off; no active jobs or execution slots.
- Owned resource rows: 11 total, 6 released_verified, 5 superseded;
  release_pending=false.
- **Residual accounting inconsistency:** final operator power projection says
  physical_state=pending, release_verified_sessions=0 for request
  `dd2cc232e8616f2c4b8f455468c474e5`, despite settled resource rows and stopped
  inference. Do not call this fully coherent final accounting. Check repeated
  stop/new intent/no-current-target acknowledgement semantics before more live
  work. Do not hand-edit the database.
- HTTP source service was restarted with current source earlier. No final
  post-stop HTTP readiness qualification was run.
- Restarted desktop stdio transports closed this chat's original MCP connector.
  A fresh stdio connection through the exact configured `scripts/pc-dev run codex`
  successfully read the native claim. Reconnect clients; do not diagnose the
  closed old transport as current source import failure.
- Native task: `PC-PA1-LAB`; run `PC-PA1-RUN-1`; lane `PC-PA1-L-EXEC`.
  Root claim was `aff0e132-5376-44bb-babc-4e59cec71e16`, session
  `f0803bfd-d1e8-407a-b25c-ea6505f7753d`. Final disposition is appended below
  after the supported handoff call; re-read native authority in the new chat.
- QUALIFY remains blocked on LAB; whole PA1 remains unaccepted.
- Two unrelated stale dispatches were deliberately left unchanged:
  `48983c8c-dd82-45fe-b125-ae6f78d4ec3e` / `PC-PCE2-RUNTIME` and
  `4ea78cd3-8bdd-478b-8c17-97d40f3b8261` / `PC-AS1-INQUIRY-CACHE`.
  Automatic approval review rejected recovery outside this PA1 scope while the
  separate dirty work and active PA1 claim remained. Do not bypass that rejection.

## Remaining work, in order

1. Read current native task/context, this dirty diff and the exact CPU LAB receipts.
   Preserve changes; no broad search, reset, release rebuild or full suite first.
2. Repair the safe-relative-artifact path failure and interpret the already
   executed effect. Use an exact bounded fresh retry only when warranted; no
   automatic replay. Confirm counterexample + interpretation + actual cleanup.
3. Repair the final stop/power projection inconsistency with focused fixtures.
4. Finish PA1 native contract adaptation: source QUALIFY still has obsolete
   `SK-PA1-RETIRE` supplier/catalog handoff at machine plan line ~525 and
   cross-authority.json line ~24. Native run charter v1 also retains the old
   Oct6 no-inference restriction (user's current bounded permission superseded
   it for this pass). Source LAB charter has no such ban.
   There is no public `amend_task` operation. Selective replan replaces IDs and
   rejects claim history; do not misuse it. Public native
   `RunService.revise_charter` exists in `todo_orchestrator/workflow/runs.py`
   (~63–130), appending immutable versions, but no PC route was demonstrated.
   `plan.apply` preserves omitted task fields but changed existing run charters
   fail `workflow_run_exists`; do not blindly reapply the whole historical plan.
   No native charter/QUALIFY definition amendment was made in this pass.
5. Final bounded source review/smoke/collection; review and commit this dirty
   integration. Do not infer final count or qualification from earlier collection.
6. Stop before broad profiling/qualification unless newly authorized. Subsequent
   acceptance still needs fresh grounded answers, full CUDA yield/foreground/
   owner release/rewarm, active cancellation/crash recovery, all A01–A40 mapping,
   actual delivery and any relevant frozen candidate qualification.

Rollback references retained: `.project-control-test/source-service-backup-20261007T132049Z`
and installed historical R10 candidate under
`/home/tumlinson/.local/share/project-control/releases/unified-20261007-finalize-r10`.
Do not restore retired features just to reproduce R10.

## Confirmed native handoff

At 2026-10-07 17:02 UTC, supported `finish_task(action="handoff")` returned
status **idle**, operation_status **finished**, project_revision **1152**,
handoff_id `4b4d93ae-2a68-45a1-8922-5802fd168eae`. Snapshot and Markdown
projections both succeeded with no projection error. The execution handle is
terminal and has no allowed actions. This is claim release/handoff, **not task
completion**. The receiving chat should use `next_task` and current authority,
rather than reuse the released handle.
