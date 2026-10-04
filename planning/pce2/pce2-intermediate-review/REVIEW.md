# PCE2 intermediate implementation review

**Review date:** 20 September 2026. **Disposition:** retain the useful implementation; repair specific correctness and recovery defects before expanding privileged use. The original NF1A supersession/resumption problem remains outstanding. This is a review, not a new implementation epic or an authorization to resume NF1A.

## 1. Scope, evidence and limitations

Requested ranges:

```text
Project Control: f894427c8fd2d3a73f641f946074b1ecc22e33ef
              ..3c11bb9ebf01cf4ed40bbcace03594cf8135185e
Skills:         688dcc2e92ffc2af690c29734ef9d09c67d8e091
              ..6cf647f66bdd37668dfa219f7c7816192aae5c65
```

I used Project Control's changed-file/history reads, immutable endpoint source reads, selected baseline comparisons, current Todo inspection, coordination observations, test source, and the committed release/progress documents. I did not execute the supplied shell commands or rerun the project's regression suites on the remote host. The endpoint code and relevant call paths were reviewed independently rather than accepting the implementation summary as proof.

Below, **PC** means the Project Control endpoint above; **SK** means the Skills endpoint above. Paths and line ranges refer to those immutable commits unless explicitly stated otherwise. The prefix `todo-orchestrator/todo_orchestrator/` in SK is abbreviated as `todo/` in finding citations. Test paths remain explicit. Baseline comparisons establish whether important defects are introduced or inherited.

Project Control reported its requested HEAD with a dirty working tree. Source findings use the commit selector, not the uncommitted worktree. Skills reported the requested HEAD clean. Generated Skills snapshots and Todo Markdown were treated as projections, not as a second authority or files to repair manually.

The changed-file inventories covered 41 PC and 40 SK paths, including the adopted planning bundle and generated projections. This is a substantive source/contract review, not a claim that every generated line or every possible runtime interleaving has been exercised. No actual production corruption, lost work or unauthorized GPU activity was demonstrated.

Two copied-function counterexamples and an extracted recovery predicate were checked in a disposable local fixture. See `logic_reproductions.py` and `logic_results.json`. The fixture uses real SQLite/filesystem operations for the static evidence check; metadata/path helpers outside the exercised behavior are explicitly stubbed. These are **not full product tests**. Transaction, crash, process-liveness and managed-workspace findings below require the identified real-kernel regression tests.

## 2. Overall assessment

This is useful progress toward a coherent execution interface, not a wasted implementation. Verified runtime selection, explicit run focus, exact capability-to-authority resolution, append-only gate attachment and startup-bound delegated maintenance are worth keeping. The planning and release records correctly avoid claiming full PCE2 completion.

The main weakness is that several improvements establish **a better entry point without closing the operation's complete lifecycle**. The action table can be correct while the actor cannot dispose of its child. A signed mandate can be authentic while the wrapped recovery operation has wider effects than its advertised task scope. A recovery can commit successfully while the advertised next action remains blocked. A cache can be conservative about gate categories yet unsound for a supported input category.

The most economical continuation is not another broad architecture revision. It is to repair these specific boundaries, then finish the original, already-demonstrated NF1A supersession/resumption path. General indexing, generic model launching and a universal command-result cache can remain deferred.

## 3. Progress that should be retained

### Planning and pause state

Both native PCE2 plans exist in separate Todo authorities. At review time PC revision 797 and SK revision 883 were observable. `PC-PCE2-RUNTIME` and `SK-PCE2-OPERATE` were still raw `in_progress`, with no active claim and effective readiness for continuation. Their later outcomes remained queued/dependency-blocked; the aggregate epics were not falsely closed. That combination is a legitimate pause, not an inconsistency between progress and availability.

Handoffs record source identities and point to progress/release documents. The detailed conformance results are mostly narrative; the inspected handoffs do not substitute for a complete executed J01–J24 evidence map. Current first-class activity was absent in the inspected PC status. The local-service observation referred to the observer-analysis registry, not systemd deployment status.

### Verified runtime and read-only preview

PC `runtime_identity.py:29–79`, `app.py`'s bound Todo plan reader and `services/planning.py:156–245` keep preview on the verified in-process Todo runtime. A bound path does not silently switch to an unrelated script, and missing observed authority is rejected rather than initialized as an empty project. The diagnostics are useful without hot import rebinding. PC `tests/test_todo_authority.py` and `tests/test_runtime_identity.py` directly address these contracts.

A concurrent external writer during preview is still reported by the older broad `MutationDetected` comparison as though the preview itself changed authority. That is a diagnostic precision opportunity, not evidence the new preview mutates. Missing-authority and identity checks should remain conservative.

### Exact capability provenance

SK `todo/workflow/capabilities.py:439–473` resolves the capability through a read-only authority, obtains the authoritative session root and checks repository identity before the kernel opens that exact target writable. Removing the unrelated global locator scan is a meaningful improvement. The isolated-worktree and stale-foreign-hint tests in `test_workflow_isolated_claims.py` and `test_workflow_integration.py` are relevant evidence.

### Run focus and ordinary guidance

The optional `run_id` reaches both resume and new-task selection. Ambiguous task-only requests return choices without claiming, and focus is rechecked inside the mutation path. `test_workflow_lane_resume.py:55–121` covers duplicate active-run membership, no fallback to another run, unavailable focus and legacy unambiguous behavior. A ready entry no longer prescribes an inspection automatically.

PC permits deliberate rich read-only inspection without creating a task or claim. Correcting PC's collection annotation is useful, although the standalone Skills surface remains inconsistent (R8).

### Gate attachment and conservative reuse boundary

The new append-only binding avoids replaying an entire plan to add required checks. Same serial binding is a genuine no-op; conflicting replacement is rejected. Current tests verify task ownership, regular-file changes and retained evidence count. It is sensible not to claim arbitrary command, GPU/resource or managed-workspace evidence is reusable before defining its complete input contract. R1, R2 and R9 concern defects inside the newly selected bounded implementation, not a request for a general cache.

### Maintenance host and deployment discipline

The maintenance principal is a trusted startup binding, not a field the model supplies in the tool call. The grant carries repository/project/task information and has signing, expiry, revocation and ordinary completed-response replay. The observer remains nonmutating. The launch packet truthfully says `launch_required`; absence of an automatic model launcher is not a defect.

The separate issuer/operator/ordinary-client stdio journey is valuable. Its fixture is considerably narrower than a general stopped managed execution, as described in R6 and the qualification section.

## 4. Findings

Severity here is an engineering priority, not a claim of observed production damage. **P1** means fix before relying on the affected correctness/privileged path. **P2** means a material lifecycle/usability defect or missing qualification. An inherited P1 is still relevant when a new automated entry point exposes it, but it is not attributed as newly written code.

### R1 — A removed directory can retain a passed existence gate

**P1 · introduced by static evidence reuse · isolated copied-function counterexample observed.**

SK `todo/evidence.py:13–48` records a static gate path as a file hash when `is_file()` is true and as `"missing"` otherwise. SK `todo/gates.py:229–233` evaluates `file_exists` using `Path.exists()`, which also accepts directories. The new reuse path at `gates.py:255–280,354–358` treats an unchanged fingerprint as sufficient to return the prior pass.

An existing directory therefore passes with `static_path.sha256="missing"`. Removing the directory leaves that fingerprint unchanged. The prior evidence still returns `passed, valid=true` even though the evaluator would now fail. The audit uses the same fingerprint, so it does not fix the collision. The included fixture demonstrated this using the copied fingerprint and reuse functions. Regular-file deletion is a passing negative control and is already covered by the project test.

**Minimal correction:** make the fingerprint agree with the evaluator's actual supported input semantics, including existence and file kind; alternatively reject unsupported target kinds consistently before execution. Do not silently redefine previously supported directories only at the cache layer.

**Regression:** pass an existing directory, remove it, run/finish again and assert failure rather than cached success. Include regular-file deletion and file/directory transitions. Full command caching is not involved.

### R2 — The cache returns before explicit child-acceptance validation

**P1 · introduced control-flow regression · source-confirmed, not a full runtime exploit reproduction.**

SK `todo/gates.py:354–358` returns reused evidence before `:370–395` checks an explicit `accept_child`: existence, parent/task ownership, readiness, allowed gate, candidate freshness and the prohibition on a child accepting its own result. The same branch is entered before the later acceptance-specific effects.

A warmed static gate can therefore report a cached success without validating the named child or carrying out the intended acceptance operation. This is not a claim that arbitrary callers bypass the initial gate/claim authorization. It is a regression in the additional semantics of an explicit acceptance request.

**Minimal correction:** separate “validate this request and its requested effects” from “must the underlying static check execute again?” Reuse must not skip child-lineage checks or acceptance recording. An initial safe fix is to exclude acceptance/child-specific paths from the cache until their behavior is covered.

**Regression:** warmed gate with unknown, foreign and valid named children; child-token acceptance denial; verify the required parent-acceptance record, not just a `passed` response.

### R3 — An exact-task maintenance label wraps broader and less conservative recovery

**P1 · inherited recovery behavior newly reachable through delegated maintenance · source-confirmed.**

PC `admin.py:83–126` describes a bounded clean/stopped maintenance assignment. PC `workflow_core/recovery.py:108–112,130–161` issues the new grant from the existing recovery plan when there are no blockers. There is no additional effect filter proving that the plan is clean, stopped and within the advertised task-specific maintenance scope.

SK's unchanged `todo/workflow/recovery.py:58–62` returns unobservable status for a nonlocal/unavailable process. Its general dispatch predicate at `:298–306` refuses `live`, or `unavailable` only when the heartbeat is not expired. `_expired` means that the recorded timestamp is at or before now; an ordinary past heartbeat therefore permits retirement of an unobservable dispatch. Similar expired-unobservable behavior exists for claims, children and leases. The included extracted-predicate check demonstrates the branch for a five-second-old heartbeat; it is not a full recovery execution.

The general plan also treats dirty source as a preservation warning rather than a refusal (`:318–340`), and project-global lock/resource queries can produce release actions unrelated to the selected task (`:375–425`). Signing the full plan authenticates the selected effects; it does not make those effects conform to the narrower operator assignment.

**Minimal correction:** define and enforce the actual delegated effect boundary in the canonical path. For writable owners require stopped/fenced proof, or return a precise unknown-owner blocker. Restrict changes to authorized owners/resources, unless a deliberately broader mandate explicitly includes them. Keep legitimate exact read-shared coordinator fencing distinct: revoking an expired read-only seat can be safe while its host process remains alive for other work.

**Regression:** unobservable writable owner with a past heartbeat; expired-but-live owner; surviving child; dirty source; stale unrelated task resource/lock; safe exact read-shared coordinator recovery. Do not turn every case into an unrestricted owner override.

### R4 — General recovery's safety check is outside its write transaction

**P1 · inherited canonical race exposed by the new entry point · source-confirmed interleaving, not stress-tested.**

The special expired-coordinator plan includes an `authority_revision` and rechecks inside the transaction. The general plan returned by SK `todo/workflow/recovery.py:473–482` does not. `execute` performs fresh inspection before the mutation, and the mutation checks the revision only if that key exists (`:485–521`).

`todo/db.py:136–169` starts `BEGIN IMMEDIATE` after those reads. Another ordinary workflow writer can insert a claim, running gate or lease between the last inspection and transaction acquisition. The owner recovery file lock serializes owner-recovery callers, not all ordinary writers. The wrapper's revision comparison also occurs before the kernel mutation and does not close this window.

**Minimal correction:** validate the snapshot/facts that authorize the transition at the canonical transactional boundary, including absence of newly conflicting owners. A conservative consistent revision check is a reasonable first repair; finer same-effect freshness can follow only where it is needed for usability.

**Regression:** arrange an ordinary authoritative write after final inspection but before recovery's transaction. Recovery must fail stale or re-evaluate safely, with no partial releases. Preserve the special coordinator path's existing stronger checks.

### R5 — Recovery commits before the v2 replay receipt is persisted

**P1 for reliable recovery · introduced replay guarantee gap · source-confirmed crash window.**

PC `workflow_core/recovery.py:266–277` calls `engine.execute`, which commits the Todo recovery/audit, and then writes `completed_receipt` to a separate private JSON record. A process exit or file-write failure in between leaves the database changed but the grant looking pending. Retrying then sees a changed authority revision/plan and fails stale instead of returning the original result.

The canonical recovery audit exists, but the current path does not link a stable grant/request identity to that audit inside the transaction for automatic recovery. The `_Engine` replay unit fixture in `tests/wf2a_core/test_workflow_core.py` does not model this: its execution increments a counter but does not change the plan or revision, and no post-commit failure is injected.

**Minimal correction:** bind the grant/request identifier to a canonical recovery result in the same transaction. The private receipt file can become a reconstructible projection. This does not require a distributed transaction or a new database.

**Regression:** commit recovery, fail before private receipt replacement, reopen the operator, replay the same grant, and obtain one authoritative result with exactly one effect. Also cover failed post-commit rendering/projection without replaying the original mutation.

### R6 — `maintained` still does not establish resumability or preserve run focus

**P2 · incomplete newly advertised postcondition · source-confirmed.**

PC `admin.py:210–254` returns `status="maintained"` and a `next_task(repo_root,task_id)` recommendation for both fresh execution and replay, without assessing whether the task is now actionable. It also omits `run_id`.

The canonical recovery can leave dirty tasks in attention and managed workspaces quarantined. SK `test_workflow_lane_resume.py:219–237` explicitly expects dirty source to be preserved and the subsequent `next_task` to return idle. Multiple active runs containing the task now correctly require a run choice, so the maintenance continuation is also no longer sufficient in that case.

The new PC maintenance journey seeds a much simpler state: an already `expired_clean` claim, a planned task, a queued lane, released own locks and no managed workspace (`tests/test_maintenance_journey.py:29–40`). Retiring that stale dispatch and claiming the task is valuable evidence, but not proof of generic stopped-worker or managed-workspace resumability.

**Minimal correction:** distinguish administrative recovery completion from resumable execution. Return an exact run-focused continuation when known, or the actual remaining adoption/workspace/owner decision. Finish the clean managed-workspace path rather than merely returning a root preparation label. Replay should identify its historical result without guaranteeing that current readiness is unchanged forever.

**Regression:** real stopped writable managed execution; clean quarantined workspace; dirty preserved workspace; duplicate task membership across runs; subsequent real public `next_task` on the same target.

### R7 — Coordinators can launch children they cannot accept or reject

**P2 · newly enabled but incomplete lifecycle · source-confirmed.**

SK `todo/workflow/roles.py:17–24` now permits coordinator `delegate_child`. Its role and default capability operations still omit `accept_child` and `reject_child` (`capabilities.py:377–406`). The successful result requires parent disposition, while the late-adapter-failure cleanup in `service.py:1104–1125` invokes `coordinate_task(...reject_child...)` through that same forbidden role path.

Thus both success and some failure paths can strand the child. Intersecting the role and capability tables makes advertisements narrower, but cannot establish that a complete delegated job is possible. The real child-lifecycle test uses an implementer; the policy-table test only checks intersections.

**Minimal correction:** provide an owned-child disposition/cleanup path for every actor permitted to create that child. This can be narrow and does not imply broad planning or repository authority.

**Regression:** coordinator delegates, collects, accepts its own child; rejects its own child; experiences failure after child authorization; cannot dispose of someone else's child. Assert no orphaned child/lease after a supported failure cleanup.

### R8 — The two executable MCP surfaces still disagree

**P2 · incomplete propagation of new behavior · source-confirmed.**

SK `todo/workflow/mcp/server.py:112–120` omits `bind_required_gates` from its `coordinate_task` Literal even though the common protocol advertises it and PC's wrapper accepts it. The standalone surface can therefore advertise a valid capability action and reject it in schema validation before reaching the kernel.

The same file still advertises `collect_delegation` as read-only/idempotent (`:145–151`), while PC's annotation is corrected. Its inspection Literal also lacks the `context_fragment` route supported by PC. These are executable-surface inconsistencies, not a request to change observer schemas.

**Minimal correction:** align supported executable surfaces or explicitly designate one as a narrower compatibility surface without advertising unsupported operations. Add a small schema/annotation parity test that exercises the actual call as well as checking names.

### R9 — Gate validation is shared only partially

**P2 · introduced live-binding acceptance gap · copied helper counterexamples observed.**

SK `todo/gates.py:30–76` checks that `id` and `type` exist, some argv/path rules, and references. It does not require a supported executable type or the mandatory `file_exists`/`pattern` fields. The local copied-function checks accepted a missing-path existence gate, missing-path/pattern pattern gate and unknown type.

CUDA validation still lives separately in `todo/plan.py:207–224`: allowed CUDA fields, positive GPU count, expected exit and resource conditions. Live binding calls the shared helper without those plan-only checks. The copied helper accepts a command with `cuda.gpus=0`, which that plan path rejects.

Malformed required gates are particularly expensive with append-only binding: the caller can durably attach a gate it cannot execute and cannot correct under the same ID using the new operation. This is not evidence of arbitrary shell sandbox escape; it is incomplete executable-spec validation.

**Minimal correction:** consolidate the applicable type-specific execution contract before either plan import or live binding. Preserve append-only policy; reject invalid requirements before they enter the ledger. Avoid adding a broad weakening/replacement capability as a workaround for inadequate validation.

**Regression:** all supported gate types through both plan and live binding, missing required fields, malformed regex/field types where applicable, unsupported types and invalid CUDA configuration. A rejected binding must not change authority.

### R10 — Oversized context has a preserved claim, but no demonstrated complete retrieval journey

**P2 · partial fix, with a source-supported large-charter failure mode; not reproduced against the full kernel.**

SK `todo/workflow/service.py:609–633` preserves the committed handle when 6 KiB context composition fails and returns a receipt containing run/lane/task plus `retrieve_via="inspect_task"`. It does not provide a concrete bounded fragment/page reference or complete next-call arguments. The task inspection does not itself return the run charter. `RunService.inspect` returns raw charter JSON and decoded content plus lanes (`todo/workflow/runs.py:138–150`), which can itself exceed the inspection budget for a legal large charter.

The outer protocol still adds identity/action-policy and performs its own size check after the kernel result; the catch handles the inner context error, not every post-commit outer-envelope failure. The `needs_context` test in `test_workflow_protocol_mcp.py` uses a fake port setting that status, rather than proving that a real oversized packet can be retrieved through public calls. Known-manifest context machinery exists, but `sync` still does not forward its accepted context-delta inputs (`service.py:728–734`).

**Minimal correction:** supply a real bounded retrieval path and budget the complete public response without losing committed operation identity. Keep required planner constraints available before implementation. A small fragment/page route is sufficient; a new index is not necessary.

**Regression:** a real near-limit charter and meaningful essential constraints, entry returning `needs_context`, following only its returned references through public calls, then obtaining the required content without another claim or repeated overflow. Separately inject outer-envelope overflow after a committed claim.

### R11 — The new maintenance mandate is still brittle under ordinary concurrent progress

**P2 · deliberate narrow v1 policy inherited by the new operator surface · source-confirmed usability limit.**

The v2 grant starts from the v1 issuer and retains the entire authority revision, full recovery-plan fingerprint and 300-second default expiry, capped at 900 seconds (`PC workflow_core/recovery.py:89–161,261–271`). An unrelated authority update can invalidate the grant before the delegate begins, even if its approved effects are unchanged. It is a one-shot exact recovery authorization, not yet a durable bounded “make execution E resumable” assignment spanning useful maintenance stages.

The conservative checks are preferable to an unsafe bypass, especially given R4. But their operational cost should not be hidden by calling the bridge complete delegated maintenance. The originator may still need to reconstruct and reissue the job repeatedly.

**Minimal correction:** first fix transactional checks. Then permit fresh recomputation of the same approved effect under the mandate, while requiring a new decision for expanded membership/impact or changed adopted source. Expose expiry and specific reissue/escalation information. Do not simply remove revision checks or build a generic permission language.

**Regression:** unrelated task progress before a valid delegated operation, an actual target change, operator-local permitted stages, expiry/revocation and materially broadened effects.

### R12 — The original false blockers and procedural error loops remain

**P2 · inherited, still directly relevant to PCE2 · source-confirmed.**

PC `services/frontier.py:19–23,67–72` still builds `immediate_blockers` from all dependency edges, not just unsatisfied ones. This preserves the original failure class in which a completed prerequisite is blamed while recovery attention is the actual obstacle. The file was not changed in the reviewed increment.

PC `workflow_tools.py:125–142` and SK `todo/workflow/mcp/server.py:52–72` still discard most typed error details and suggest `next_task` for unrelated failures. The newly added live-binding validation can therefore know exactly which spec is invalid while the public agent receives only a generic reason. Protocol inspection and coordination still default to another protocol call even where no such call is necessary (`protocol.py:319–353`).

**Minimal correction:** preserve a bounded specific cause and necessary decision, use actual unmet dependency/recovery predicates for blockers, and recommend a next call only when it is executable and useful. This is a smaller, more direct fix than additional instructions telling the model to work around inaccurate output.

**Regression:** completed ADOPT-like prerequisite with CORE-like recovery attention; malformed gate spec with useful bounded details; missing owner/runtime; ready inspection with no compulsory follow-up; agreement with an actual claim attempt at the same observation.

### R13 — Exact supersession, the original reason for the pause, is still not delivered

**P1 before using legacy retirement broadly; otherwise an acknowledged unfinished PCE2 outcome · inherited code unchanged.**

SK `todo/retirement.py:119–170` permits a subset of the source run's task IDs, checks quiescence only for that selected subset, and then cancels the whole source run. Its queued-task update is keyed by task ID without restricting the source run's lanes. A request can therefore omit an unfinished/live member while retiring the enclosing run, or affect another queued membership of a shared task.

The new `maintain_execution` only wraps recovery; it does not add safe run supersession, exact dirty-source adoption, or ordinary narrow amendments beyond appending gates. Early immutable dependency delivery and explicit validation-versus-integration effects also remain unfinished. `run_gates` still integrates source and schedules required gates in the integration loop and again afterwards (`service.py:855–978`); completion invokes them again. The static-only reuse does not remove repeated command/managed-workspace execution.

**Minimal correction:** finish the specific source-run→successor transition and preserved-work resumption needed by NF1A, with complete affected membership and shared/foreign-consumer accounting. Reuse canonical retirement and its request/audit machinery after fixing those boundaries. This need not be a universal amendment or distributed transaction framework.

**Regression:** source has two unfinished tasks and only one is selected; omitted live member; shared queued task in another run; successful historical outcomes retained; external consumers; stopped dirty source preserved; approved successor becomes the intended current execution. Then demonstrate the actual intended continuation on a disposable donor-shaped fixture.

## 5. What remains outstanding across the original acceptance groups

These classifications describe evidence and implementation at the pause, not a numerical completion percentage. A narrow implemented slice is not marked wholly absent merely because its larger outcome is open.

| Group | Assessment at this pause |
|---|---|
| J01 entry/focus | Explicit focus and no wrong-run fallback implemented; full required-context journey remains partial. |
| J02 executable actions | Role/capability intersection implemented; coordinator lifecycle and standalone schema gaps remain. |
| J03 consistent blockers | Original frontier/error inconsistency remains. |
| J04 bounded context/deltas | Committed claim preserved for one inner failure; complete retrieval/delta/outer-envelope contract not established. |
| J05 meaningful delegate scope/context | First-file selection and generic/empty packet inputs remain. |
| J06 truthful executor/launch | Host handoff and startup-bound operator are useful; automatic model launch is not required. Available executor claims remain intentionally limited. |
| J07 launch/wait/collection lifecycle | PC annotation fixed; collection/wait/idempotence and failure closure not fully qualified. |
| J08 bounded maintenance authority | Principal/target authentication, expiry and ordinary replay present; canonical effect/liveness/atomic receipt gaps remain. |
| J09 usable preparation/recovery | Narrow pre-prepared stale-dispatch journey proven in recorded tests; general managed preparation/resume not established. |
| J10 dirty work/adoption | Preservation exists; supported exact adoption and usable continuation remain missing. |
| J11 resources/liveness | Existing owner behavior reused; unobservable owner handling and exact resource effect boundary need repair/qualification. |
| J12 supersession membership | Original operation not delivered; legacy subset/whole-run cancellation hazard remains. |
| J13 invalidation/retirement semantics | No new complete semantic surface. |
| J14 transactional freshness | Special coordinator path stronger; general recovery's inspection/write gap remains. |
| J15 replay/post-commit failure | Ordinary stored-receipt replay works; commit-to-receipt crash window and projection/response failures not closed. |
| J16 small amendment | Appending required gates improved; broader scoped correction without plan replay not delivered. |
| J17 early dependency delivery | Not delivered by this increment. |
| J18 unsurprising validation/integration | Combined integration/gating behavior remains. |
| J19 evidence economy | Static reuse implemented with identified bugs; other categories deliberately fresh, duplicate scheduling still present. |
| J20 runtime identity | Strongest substantially delivered area; frozen paired routing and missing-authority refusal worth retaining. |
| J21 public journeys | Two useful narrow journey shapes; not complete public lifecycle/adverse-condition qualification. |
| J22 flexible planning/schema | Better instructions; payload parity, defaults and actual actionable semantics remain partial. |
| J23 qualification/usage/pause | Honest bounded release and paused donors; no aggregate implementation-usage measurement or full acceptance closure. |
| J24 reuse/separate authorities | Separate import and open outcomes observed; no blanket supersession of old work was performed by this review. |

At durable outcome level: **PC-RUNTIME and SK-OPERATE have substantial partial work; PC-SURFACE and SK-MAINTAIN have narrow slices; SK-COLLABORATE is largely outstanding; PC-QUALIFY has bounded candidate evidence rather than full outcome closure.** There is no justification for either declaring the epic complete or discarding the implemented foundation.

## 6. Delegation and flexibility: what the next increment should not repeat

The new instruction says the main thread owns reasoning, synthesis, strategy, architecture, scope changes, tradeoffs and final acceptance (PC `app.py:92–101`). The prioritization record attributes this division to the owner's preference. It is reasonable for the main thread to retain architectural intent and consequential approval; the review does not presume to override that preference.

However, “owns reasoning” should not force every local diagnostic inference or choice within an approved maintenance assignment back to the most expensive model. A scoped delegate must be able to reason about and complete its assignment. Clarify the boundary as **main thread owns project-level/consequential decisions; delegates exercise the local judgment already authorized to complete their assignments**. Escalate changes of intent or authority, not every mechanical branch. That can be a small wording/contract clarification rather than a new delegation hierarchy.

The first-file delegation defect is independently visible at SK `service.py:1040–1078`. Fixing it does not initially require a general scope index or arbitrary equal-scope concurrent writers. Optional exact authorized read/source targets plus inherited task constraints and a test that the intended file reaches the delegate would already address a demonstrated failure. Keep harder writer handoff conditional on an actual write-delegation need.

Similarly, deferring unsafe universal command caching is sensible; accepting repeated scheduling of the same gate by two adjacent phases of one integration operation is a separate question. First remove unnecessary duplicate scheduling under the existing evidence/operation contract, without inventing a broad cache across unrelated environments.

## 7. Qualification and deployed candidate

### What the recorded tests establish

The recorded focused results include runtime/preview, profiles, provenance, gate/audit/protocol and lane-resume checks. Counts from successive runs overlap; they must not be summed into a unique coverage total. The original ordinary journey uses a real Todo fixture and the PC FastMCP tool manager inside a subprocess, not a paid model. It verifies entry, append/no-op gate binding, validation and completion on a simple compatibility-run task.

The maintenance test uses separate issuer CLI, trusted operator MCP stdio and ordinary MCP stdio processes. That establishes real process-boundary grant transfer and refusal by an unconfigured ordinary maintenance host. It does not establish delegation to a running cheaper language-model agent, aggregate token savings, dirty adoption, managed-workspace continuation or crash recovery.

The policy table tests use a fake port for important envelope cases. The grant replay unit test uses a fake engine. These are useful unit tests but not substitutes for the adverse public journeys in R1–R10. The latest recorded installed run-focus ordinary journey is one test, while maintenance journey evidence is carried from an earlier paired candidate. The run-focus/maintenance-return interaction merits a current paired test.

### HEAD versus activated release

`BOUNDED_CANDIDATE.md` records the active candidate as:

```text
PC source:     56da5f34426b9d607cb568a17a5f004a267a5ad6
Skills source: 6cf647f66bdd37668dfa219f7c7816192aae5c65
Candidate:     pce2-focus-56da5f3
Identity:      e9d6de3ea3cf142f81d0021a198e2ecfaf4239c7ab74f79fab6772701a7cad4d
Manifest SHA:  ed99177ad31cfa06ca4d29d533a6201301704786b46b04fcc6c26c58b04c7d67
```

A separate Project Control delta read confirmed that `56da5f3..3c11bb9` changes only `BOUNDED_CANDIDATE.md` and `REMAINING_SCOPE.md`. The earlier PC commit in the deployed candidate is therefore **not evidence of missing reviewed product code**.

The activation/service restart, HTTP health/readiness and rollback paths are supported by the committed deployment record and handoff. I did not independently interrogate systemd, read the installed manifest from the host filesystem or validate a live process image. Existing client sessions may still need reconnection, as the record notes.

No automatic rollback or further deployment was performed. The runtime/focus improvements need not be discarded merely because the new maintenance/cache paths need repair. Until repaired, avoid treating the broader delegated recovery surface as qualified beyond its demonstrated narrow scenario.

### Usage

The bundle's shared implementation ceiling is not an actual consumption record. No audited all-agent total was available in the inspected progress evidence, so the remaining allowance and measured net savings are unknown. Test durations are not model usage. Do not restart the allowance because the plan is being reviewed, or claim a particular implementation percentage from commit/test counts.

## 8. Recommended continuation without another redesign

**First, a bounded correctness pass on shipped behavior.** Correct static fingerprint semantics and child-acceptance ordering; enforce the delegated recovery effect/liveness boundary and transactional preconditions; make the authoritative receipt recoverable across the commit/file-write gap. Fix malformed gate acceptance and executable MCP parity while those files are under test. A paired set of focused regressions should establish these before broader privileged use.

**Second, close the public jobs that the current tools claim to support.** Complete coordinator child disposition/failure cleanup; distinguish recovery from actual resumability; return run-focused continuation; prove one real stopped managed execution and one preserved-dirty refusal/adoption path; carry precise errors and correct blockers. This is the low-cost point to ensure that an authorized maintenance delegate can finish without repeated root reconstruction.

**Third, address the original NF1A obstruction with one specific lifecycle outcome.** Fix exact source-run membership/foreign queue handling, then implement the intended source→successor transition and preserved-work continuation. Include narrow metadata corrections or dependency delivery only where the actual paused execution needs them. Do not substitute a new permission platform, scheduler, generic index or model backend.

These are sequencing priorities inside existing PCE2 outcomes, not a request for a new microtask graph. Preserve the current useful commits and tests. Reuse unchanged evidence while adding tests at the changed semantic boundaries; do not run an entire unchanged release suite after every local edit. Record a compact current acceptance/evidence map and an honest usage checkpoint before choosing the remaining scope.

Full J01–J24 completion is not required to release a truthful smaller increment. But scope decisions must distinguish **optional future generalization** from **known correctness defects** and **the original demonstrated blocker**. In particular, retiring/superseding NF1 and safely continuing retained NF1A work were the reason for this intervention, not speculative additions discovered after the fact.

**Bottom line:** the pause is productive. Keep the foundation, repair the narrow but important safety and lifecycle holes, and finish a real supersede/reconcile/resume journey before treating the environment problem as solved. NF1A remains paused throughout this review.
