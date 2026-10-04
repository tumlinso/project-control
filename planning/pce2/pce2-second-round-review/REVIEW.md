# PCE2 second-round implementation review

**21 September 2026 · Decision: retain the architecture and implemented improvements; make a finite correctness/lifecycle correction before relying broadly on live adoption and delegated recovery. No new PCE2 redesign or new planning epic is warranted.**

## 1. Scope and evidence

Reviewed ranges:

```text
Project Control: 3c11bb9..40f80f3212ead5ed1b556c0a4a9f9a54d70ae333
Skills:         6cf647f..e6c1fc843ed882554eecea9eca559e3dbdd011cd
```

I started with `planning/pce2/REVIEW_RESPONSE.md`, compared its claims with the previous R1–R13 review, inspected the changed-file inventories and relevant immutable endpoint source, and traced the corresponding tests and adjacent services. I also read current PCE2 and donor coordination state. PC's working tree was dirty, so code findings are pinned to its commit, not its uncommitted files. Generated Skills snapshots and Todo Markdown were treated as projections, not independent authorities to edit.

References **E01–E43** and observations **O01–O08** resolve to repository, commit, path, location and method in [evidence.json](evidence.json). Below, `todo/` abbreviates Skills' `todo-orchestrator/todo_orchestrator/`; PC source paths are relative to `src/project_control/` unless otherwise specified.

This is a source/contract/qualification review, not a fresh execution of the remote test suites or every possible interleaving. I did not run the supplied shell diff commands on the user's host. Six limited copied-algorithm/SQL counterexamples were executed locally; their inputs, stubs and outputs are in [isolated_checks.py](isolated_checks.py) and [isolated_results.json](isolated_results.json). Real Git/SQLite/files/HMAC were used where indicated. Those checks do not constitute a full product or public-MCP reproduction. No production corruption, file loss or unauthorized live workload change was demonstrated.

## 2. Executive assessment

The second round is a substantial improvement. It responds to the actual first review rather than merely changing names or adding another administrative layer. Directory fingerprints, acceptance-aware caching, shared gate validation, transaction-time recovery revision checks, request-linked receipts, coordinator child disposition, executable schemas, readable context pages and explicit validation effects all have substantive implementations and relevant test cases.

The remaining failures concentrate at **composition boundaries**: a source fingerprint measures the wrong side of a rename; a consumer relationship is treated as workspace ownership; an adopted producer receives the wrong integration target; a public preflight prevents a repaired lower-level replay; a priority selector is used as an exact-lane eligibility check. These matter directly to engineering progress per reasoning token because they either undermine a privileged guarantee or force agents back into source inspection and repair workarounds.

The conclusion is not that Project Control is generally unusable. The ordinary improvements should remain deployed unless the owner has a separate operational reason to roll back. However, I would not sign off the supported preserved-work adoption and recovery story as complete while F1–F7 remain. They need local changes and focused public-path tests, not a replacement kernel, autonomous repair framework, model launcher, general index or universal evidence cache.

A crucial current-state correction: **NF1 is already cancelled and NF1A active in both donors.** The present donor problem is continuing those existing NF1A executions, not replaying the original supersession simply because a new supersession tool exists. [O07–O08]

## 3. Finding-by-finding disposition of the previous review

“Resolved” below means the identified source defect is addressed with relevant evidence; it is not a claim of exhaustive conformance of the entire subsystem.

| Prior finding | Disposition | What the final source establishes |
|---|---|---|
| R1: directory existence cache | Resolved | Static identity distinguishes existence and kind; a removed directory no longer shares the earlier identity. [E17–E20] |
| R2: cache skips acceptance | Resolved | Child/explicit acceptance paths do not use the ordinary reuse shortcut. [E18, E20] |
| R3: maintenance effects too broad | Partly resolved | Stopped-process proof and lease ownership are improved. Workspace selection still crosses a task-ownership boundary: F2. [E11–E12] |
| R4: general recovery DB race | Resolved for the identified race | The inspection revision is checked after `BEGIN IMMEDIATE`, before effects. Ordinary authoritative writes cannot pass through the old gap. [E12, E36] |
| R5: commit-before-receipt gap | Canonical layer repaired; public composition incomplete | Request-linked results commit with effects. Public expiry handling still hides them in the relevant failure case: F4. [E09, E12, E31] |
| R6: recovery is not resumability | Substantially repaired, still incomplete | Exact run/workspace binding, clean reactivation and current readiness exist. F2 and F5 prevent universal same-target continuation. [E08, E13, E24, E34] |
| R7: coordinator cannot dispose of child | Resolved for the supported lifecycle | Owned accept/reject and explicit unavailable-launch cleanup are allowed and tested; foreign child rejection remains denied. [E25–E26, E37] |
| R8: executable MCP mismatch | Resolved for the identified mismatch | Gate binding, source targets, context inspection and collection annotations are aligned. [E22, E27–E28] |
| R9: partial gate validation | Resolved for identified defects | Shared validation checks supported types and required fields, including CUDA configuration, before binding. [E18–E20] |
| R10: large-context continuation | Substantially resolved | A real-kernel/protocol large-charter paging journey and an outer-entry retained-handle test now exist. Arbitrary envelope edge cases are not all qualified. [E21–E22, E38–E40] |
| R11: brittle grants | Resolved within declared policy | Ordinary recovery can freshly prove unchanged effects across unrelated revisions. Supersession deliberately remains a fresh exact snapshot. [E09–E12] |
| R12: false blockers/error loops | Original examples repaired | Completed task prerequisites are removed; relevant recovery blockers and typed error details survive. This is not yet equivalence for every conditional dependency. [E28–E30] |
| R13: actual supersession | Substantial implementation, not complete end to end | Whole membership, source-scoped queues, foreign memberships, history and signed adoption exist. F1, F3 and F6 concern necessary correctness/completion, not optional generality. [E06–E08, E10, E33, E41] |

The machine-readable disposition is [previous_findings.json](previous_findings.json).

## 4. Required corrections

Priorities describe engineering consequence, not observed production damage. **P1** means correct before relying on that privileged/content-authority path. **P2** denotes a concrete authority, lifecycle or recovery failure that warrants a narrow repair. These are seven records, not a demand for seven separate agents, epics or release cycles.

### F1 — Adoption approves a content fingerprint that can miss edited rename destinations

**P1 · existing shared utility newly relied on by privileged adoption · real-Git isolated counterexample observed.**

`todo/runtime/source.py:18–34` parses a NUL-delimited porcelain rename by reading the first path and then replacing it with the second. In the observed Git output, `RM new.txt\0old.txt\0`, the first path is the destination that actually contains the edited bytes. The implementation consequently fingerprints `old.txt`, which no longer exists, rather than `new.txt`.

`capture_source_identity` combines HEAD, status bytes and those selected path hashes. Changing the contents of the renamed destination again can leave HEAD and status bytes unchanged. Both observations then contain the same old-path/null hash and produce the same fingerprint. Source normalization validates the value but does not hash the missing destination afterwards. [E04–E05]

This is now a privileged precondition: preparation signs `expected_content_fingerprint`, and retirement remeasures and compares it before accepting retained dirty work. The recheck therefore fails to detect this supported filesystem change. A changed renamed file can be adopted under the earlier content approval. [E06, E08]

The included fixture observes different destination hashes but identical source fingerprints. An ordinary modified-file control detects the change, so the result is specific rather than a generic assertion that hashing is broken. It does not run a full supersession transaction or demonstrate lost data.

**Smallest correction:** repair rename/copy parsing so the actual retained content is covered, keeping the source identity contract content-sensitive. Test preparation, a second edit of a renamed destination, then public application: it must refuse changed content without retiring the run. Do not add a new source index.

### F2 — Recovering an integration consumer can quarantine a producer it does not own

**P1 · remaining delegated effect-scope defect · source-confirmed with an isolated SQL/policy counterexample.**

The workspace query in `todo/workflow/recovery.py:415–441` still selects workspaces using either a matching claim task **or** `workspace.integration_task_id == requested_task`. That second relation identifies the workspace's integration consumer, not its current writer. Each resulting quarantine action is labelled with the requested `task_id`. The delegated effect filter checks that label, so it does not independently establish ownership. [E11]

Consider a stopped integrator I and a live producer P in the same run. P owns clean active workspace W, whose integration target is I. I's task-scoped dispatch inspection need not inspect P's live dispatch, but the workspace query selects W. The resulting action says it belongs to I. With one such workspace, continuation derivation takes W's lane without verifying that it matches I's recovered lane; the preparation path checks the run, which is the same. [E08, E12]

A transaction-time revision check does not repair this: even a perfectly current snapshot can describe the wrong authorized target. The mutation applies the selected quarantine action. A later workspace-reactivation refusal for the live producer occurs after the incorrect workspace state transition.

The local fixture reproduces the exact query selection, accepted action label and same-run/different-lane continuation. It does not execute the complete RecoveryEngine mutation or simulate an actual live producer.

**Smallest correction:** derive and validate task/claim/dispatch/lane/workspace ownership as one binding. An integration-consumer relation must not grant authority over producer workspaces. Test stopped I plus live clean P, verifying P's workspace, claim, dispatch and leases are unchanged. Do not solve it by requiring every unrelated project process to stop.

### F3 — The adopted isolated producer is wired to itself instead of its integrator

**P2 · introduced preserved-work completion defect · source-confirmed with an isolated publisher-query counterexample.**

The successor workspace insert in `todo/retirement.py:350–382` sets `integration_task_id` to `handoff.successor_task_id`. For an `isolated_merge` implementer task NEW, that is the producer itself, not the successor run's integration task INT. [E07]

Producer completion at `todo/workflow/service.py:381–395` searches for an integrator/validator lane owning that integration task. In the ordinary distinct producer/integrator layout, no such lane owns NEW, and publication fails with `integrator_lane_missing`. Adding a correctly declared INT lane does not help because the workspace still points to NEW. [E13]

The new separate-process supersession journey proves dirty bytes survive and the successor claims the adopted workspace. It stops there. Its isolated-merge fixture has no final integration owner, so the defect is not tested. [E33]

**Smallest correction:** derive or explicitly bind the actual successor integration destination while validating the handoff. Extend the existing journey through adoption, a legitimate source commit, producer completion/publication and destination integration. This is finishing the advertised operation, not implementing a general early-delivery framework.

### F4 — Expired public preflight still rejects an already committed recovery

**P2 · canonical replay fixed, public call order still wrong · source-confirmed with an isolated authenticated-preflight check.**

The repaired `run_authorized_recovery` checks for a canonical result before fresh-execution expiry and reconstructs a missing private receipt. That is correct. However, public `maintain_execution` calls `inspect_maintenance_recovery_authorization` first. That preflight checks the private receipt and then expiry, without querying the canonical recovery result. [E08–E09]

Thus the exact problematic sequence remains: recovery commits; private receipt persistence fails; the caller retries after expiry; public preflight rejects before reaching the canonical lookup. The ledger result is not lost and a duplicate mutation is not implied, but the supported lost-response recovery path still demands an administrative workaround. The handoff's statement that this works “including after grant expiry” is too broad for the public recovery route. [E01]

`test_maintenance_receipt.py` tests the lower-level helper directly and does not advance beyond expiry before calling the public wrapper. The local copied preflight similarly demonstrates rejection before any canonical lookup, with an explicit stub for that already committed result. Supersession's analogous canonical-first preflight is a useful implementation precedent. [E10, E31]

**Smallest correction:** authenticate target and principal, then check the canonical committed result before applying expiry to unexecuted work. Add a public-MCP fault test spanning commit, failed private write, expiry and replay. Assert one original audit/result and no second effect; unexecuted expired grants must still fail. Align revocation/receipt checks with the same completed-state source rather than treating a missing private projection as proof of nonexecution.

### F5 — An exact ready lane is rejected because another lane ranks first

**P2 · actual supported continuation friction · isolated ranking counterexample observed.**

`assess_continuation` calls `deterministic_lane_assignment(run_id, role)` and requires its single result to equal the exact requested lane/task. Otherwise it reports `not_serial_lane_head`. But that function selects the highest-ranked eligible head across all same-role lanes. It does not answer whether the requested task is its own lane's head. [E13–E14]

Two independent ready implementer lanes illustrate it: A has higher priority than B; both are valid serial heads. An explicit `next_task(task_id=B, run_id=R)` is allowed to select B and dispatch checks B's own lane head. The preceding maintenance assessment can nevertheless declare B blocked and suppress its continuation.

The local SQL fixture confirms the differing predicates with readiness fixed true. No claim was executed; the actual dispatch code supplies the relevant comparison.

**Smallest correction:** assess the requested lane against its real dispatch predicate, or select it from all eligible lane candidates rather than compare it with the ranking winner. A two-lane public assessment/claim test is enough. Preserve the honest distinction between an observed ready target and a future reservation.

### F6 — Retirement still misses external consumers expressed through typed prerequisites

**P2 · incomplete existing protection extended by this revision · isolated pending-checkpoint counterexample observed.**

The new retirement guards protect direct task prerequisites and records in `interface_consumers`. They do not inspect every supported way to depend on an old task's output. For example, `task_dependencies` can point to a checkpoint rather than populate `prerequisite_task_id`. [E07, E16]

An external unfinished task EXTERNAL waiting for OLD's pending checkpoint OLD-READY is missed by both current guard queries. Retiring OLD leaves EXTERNAL waiting for a requirement whose producer was just superseded, without an approved new producer or dependency mapping. This is not an objection to retaining completed historical evidence; it is the unresolved outgoing edge that matters.

The service wrapper adds no additional consumer closure before the retirement transaction. The local SQLite check executes the two actual guard queries and contrasts their empty results with the pending checkpoint consumer. It is not a complete retirement execution. [E15]

**Smallest correction:** apply the existing typed dependency relationships when determining affected external consumers, covering pending checkpoint and alternate interface/barrier paths as appropriate. Refuse unresolved outgoing requirements without an explicit valid replacement, while preserving usable successful history. No new graph framework is required.

### F7 — Preparing supersession can recreate the authority it was supposed to inspect

**P2 · preparation/authority-boundary inconsistency · source-confirmed, not separately executed.**

`prepare_supersession_assignment` constructs `Service(repo, mutation_mode="self_debug")` without `read_only=True`. By contrast, the recovery preparation path does request read-only authority. [E08]

The canonical Service constructor initializes the database when writable, and can restore a missing database from an existing snapshot. This happens before supersession preparation validates source/successor eligibility. A preparation-only command can therefore initialize, migrate or restore authority rather than fail explicitly on unavailable state. [E15]

Private grant-file creation is an intended preparation effect and is not the concern. Reconstituting a missing Todo authority from a potentially stale projection is a different operation, inconsistent with the revision's fail-closed missing-authority boundary.

**Smallest correction:** inspect the authority read-only during preparation. Test existing project metadata/snapshot with an absent authority database: fail without creating/restoring the database or issuing a usable assignment. Actual authorized application remains a separate write operation.

## 5. Bounded residual limitations, not grounds for another general redesign

Two source-level limitations prevent treating the current interfaces as universally complete, but should not expand the correction into a new platform.

**Near-budget plain-fragment expansion.** Large fragments now page correctly. For a plain fragment ID whose inner expanded payload fits the requested budget but leaves no space for protocol identity/action policy, the kernel can choose `expand` rather than paging and the outer envelope can still overflow. The page path reserves overhead; the plain path at `todo/workflow/service.py:785–795` initially uses the full budget. A boundary-size regression and outer-envelope-aware fallback are appropriate when tightening this touched path. This does not invalidate the demonstrated 14 KB charter paging journey. [E21–E23, E38–E40]

**Conditional frontier dependencies.** The original completed-ADOPT false blocker is fixed. The projection still tests identifiers using broad done/reached/frozen states rather than each edge's required disposition or interface version. A frozen v1 interface is not necessarily a satisfied v2 dependency. Keep the frontier's precision claim bounded, and use canonical condition results where the corresponding route is needed. This is not a reason to introduce a second materialized workflow state machine. [E16, E29–E30]

The following can legitimately remain deferred: a Project-Control-owned model launcher, a general budget platform, arbitrary equal-scope concurrent writing, a generic amendment language, universal command/GPU evidence caching and a general dependency-delivery overhaul. Existing explicit prepared supersession can remain revision-sensitive; the user already accepted that boundary. Scoped delegates may perform approved work without reclassifying local code children as tool-capable hosts. The owner's main-thread reasoning and consequential-decision instructions are retained, not reopened as a review finding.

## 6. What is now genuinely solved and worth retaining

### Gates and validation

The static cache now distinguishes directory existence and skips child/explicit-acceptance paths. The shared validator catches the identified malformed executable definitions, required fields, patterns and CUDA contract inconsistencies before append-only live binding. Tests address the specific earlier false-pass and uncorrectable-bad-gate cases. [E17–E20]

Validation-only effect selection is explicit. The combined integration operation avoids scheduling the same required command again in its outer loop after it was already performed for the same integration state. The command-count fixture observes one execution in that operation; a later validation after a source change executes again. Validation-only leaves queued integration unchanged. [E23, E37]

Not caching arbitrary command/resource/managed-workspace results across later calls is a valid conservative boundary. Removing an unnecessary separate `run_gates` call before ordinary completion already saves workflow overhead; preserving actual fresh acceptance remains more important than a test-count target.

### Delegation and model behavior

The first-file guess is replaced by explicit source targets and declared readable scope. Read-only delegation can receive the full authorized read scope; writable delegation requires explicit bounded targets. Parent constraints, relevant references and acceptance facts are actually supplied. The coordinator now has narrowly owned acceptance/rejection, including tested cleanup after an explicitly unavailable launch. [E22–E28, E37]

These changes make cheaper bounded agents more useful without forcing the main model to assemble path permissions or repair stranded children. The tests use a fake local worker for those semantics; they do not establish an actual local model's quality or token savings. An unconfigured executor is still not a running agent, and the external host launch handoff is a legitimate supported route rather than missing automation.

### Context and guidance

A ready entry does not compel another inspection. Read-only research may use rich reads directly. Expanded context is readable canonical JSON in bounded pages, with a cursor tied to fragment ID/version/hash, and an explicit no-progress failure rather than an empty-page loop. Entry overflow preserves the handle and supplies callable retrieval arguments. Real-kernel and unit tests cover complementary parts of this path. [E03, E21–E23, E38–E40]

Known-manifest synchronization now returns actual changed references, and typed error details survive without dumping transcripts. The completed-prerequisite/recovery example is fixed. These are substantive reductions in avoidable model reasoning, even without measured provider token data. [E28–E30]

### Authority and maintenance

Positive stopped-process checks replace “old heartbeat therefore dead” for writable recovery. Task-scoped leases are not indiscriminately cleared, and general recovery validates its ledger snapshot inside the write transaction. Canonical request-linked receipts remove the original data-layer commit gap. Clean exact quarantined workspaces can be reactivated without resetting, committing or cleaning retained source. [E09–E12, E24, E34–E36]

The public host principal remains startup-bound; ordinary callers cannot self-authorize by naming a role. Observer mutation was not added. Independent Todo, source/worktree and host authorities are not falsely presented as one distributed transaction. F2 and F4 are necessary remaining fixes to that otherwise improved design.

## 7. Qualification and release identity

The committed handoff records eight installed tests across maintenance journey, managed maintenance, maintenance receipt, supersession journey and supersession receipt modules. Seven passed initially; the remaining test had an import-path fixture issue, was corrected and rerun alone successfully. These are attributed results, not executions performed by this review. Focused suites overlap, so their counts must not be added into a coverage total. [E01, E31–E35]

The public isolated-worktree supersession test crosses issuer CLI, operator stdio MCP and ordinary stdio MCP. That is valuable. Its success criterion is adopted-workspace claiming and retained bytes, not completion/integration. The maintenance receipt fault test calls the helper, not the expired public preflight. These exact gaps explain F3 and F4; they are not a dismissal of the tests that do run. [E31, E33]

Recorded candidate:

```text
Name:      pce2-review-20260920
PC:        aa6131a4f6bd1e3596aab2e810afa6d59897f804
Skills:    e21378560146e8059b51fd4b2b63ce60db532778
Identity:  cab757e62c1b283ea9e321f2f2cafe0982af7c8ab598d8382f1feb582f8dcf82
Manifest:  636ecc8307ddc8ed122e1e36bf65c2fe570c91475c964cae6c6fd61cfd0fae4c
```

I independently checked the repository deltas from those packaged commits to the review endpoints. PC changed only two documentation/handoff files and two tests. Skills changed only progress and generated authority projections. **There is no production-code skew hidden by the different commit names.** Activation, process-manifest agreement, health/readiness HTTP 200, 22-tool initialization and retained rollback are documented in the handoff; I did not inspect the running process image/systemd or invoke the live operator. [E01, O03–O04]

I also did not rerun the whole compatibility/regression suite. The read-only observer remains usable during the review; that is narrower evidence than a release-wide safety certificate.

## 8. Acceptance scope and planning truth

The original J01–J24 groups are a useful coverage map, not 24 new implementation tasks or an instruction to fill every deferred feature. The following is an assessment of what this round adds; a group is not certified in full merely because one constituent test exists.

| Group | State relevant to this review |
|---|---|
| J01 entry/focus | Earlier explicit run focus retained; ready entry/context improved. F5 is a separate exact continuation composition defect. |
| J02 executable actions | Coordinator disposition and schema parity repaired for the reported cases. |
| J03 actual blockers | Original completed-prerequisite case repaired; conditional projection limitations remain bounded. |
| J04 bounded context | Real large-charter retrieval and retained-handle overflow evidence; near-budget plain expansion not fully covered. |
| J05 intended delegation | Explicit targets and inherited constraints delivered; arbitrary same-scope concurrent writes not required. |
| J06 honest executors | External ready-to-launch host remains supported; no claim of an automatic model executor. |
| J07 child lifecycle | Owned acceptance/rejection and explicit unavailable-launch cleanup tested; not exhaustive supervisor failure proof. |
| J08 delegated maintenance | Principal and bounded signed authority implemented; F2 is a real remaining scope violation. |
| J09 resumable recovery | Clean managed public journey exists; F5 and current donor predicates prevent blanket readiness claims. |
| J10 retained source | Preservation/adoption implemented, but F1 means exact content approval is not yet sound for renamed files. |
| J11 liveness/resources | Positive stopped proof and task lease scope improved; no new host scheduler or full host-resource qualification asserted. |
| J12 whole generation | Complete source membership and source-scoped queues fixed; F6 outgoing typed consumers remain. |
| J13 history/applicability | Successful history preserved; generic applicability invalidation is not claimed complete. |
| J14 freshness | Transaction revision guard and same-effect recovery refresh exist; exact supersession reprepare is intentional. |
| J15 replay | Canonical receipts delivered; F4 public recovery-expiry gap remains. |
| J16 amendments | Append-only gate binding is useful; a general amendment framework is neither complete nor required by this review. |
| J17 early delivery | General early dependency delivery remains outside the demonstrated slice; F3 is ordinary adopted-producer completion, not J17 expansion. |
| J18 explicit effects | Validation-only and combined integration are exposed and tested. |
| J19 validation reuse | Same-operation duplicate execution removed; later command/resource validation remains conservative. |
| J20 runtime | Existing verified routing retained; F7 preparation must not restore unavailable authority. |
| J21 public journeys | Real subprocess/MCP journeys improved; finish the specific failure paths rather than invent a benchmark platform. |
| J22 adaptive schemas | Mechanical direction and source targets improved; no new workflow language is needed. |
| J23 paired evidence/usage | Recorded paired release matches product sources; no full aggregate provider usage meter. |
| J24 prior work/donors | Prior work reused and outcomes left open; existing donor supersession must not be repeated. |

At PC revision 799 and SK revision 885, the PCE2 runs remain active and their execution lanes are ready/queued without active dispatches. Their later outcome records and aggregates are not falsely completed. The current source implementation spans more than the queue labels alone convey; durable handoffs are therefore important, but there is no reason to mark everything done merely to make the ledger look tidy. [E01, E42, O05–O06]

No complete all-agent provider usage total was available. I cannot establish the remaining portion of the shared implementation allowance or claim measured net token savings. Earlier work still counts; a correction does not restart the budget. Use existing telemetry when available, and keep mechanical fixes/test execution delegated appropriately. Do not build usage accounting infrastructure as a prerequisite to these corrections.

## 9. Present NF1A readiness — separate from PCE2 product readiness

**Both donors already completed the old-run supersession step.** Cellerator reports `CE-NF1-RUN-V1` cancelled and `CE-NF1A-RUN-V1` active. GlassHelix reports the equivalent cancelled NF1 and active NF1A runs. Their ADOPT work is recorded complete. A new supersession tool is not a reason to repeat those transitions. [O07–O08]

Cellerator, revision 7326, has CORE queued in an attention-required isolated lane with quarantined workspace `fc55acb4-5c9d-4861-9cd1-33d23f0cb680`. CONTROL, MECHANISMS and SUPPORT retain stale dispatch/expired claim records. QUALIFY has an active destination workspace; there are no current patch/integration entries in the queried NF1A view. Its default top-level run still shows `compat-v2`, so intended NF1A continuation should remain explicitly focused. Broad export is size-limited, although the targeted run view succeeds.

GlassHelix authority is now readable at revision 166. CONTROL, OBSERVE and SYSTEM likewise have stale dispatch/expired claim records; OBSERVE and SYSTEM have isolated workspaces and ACCEPT has an exclusive destination. The earlier authority-unavailable observation should not be repeated as a current fact.

These are dated read-only observations, not recovery plans. Stale/unobservable dispatch metadata does **not** prove the underlying writer is stopped. A clean canonical checkout also does not establish that every retained managed worktree is clean. Actual liveness, worktree bytes, ownership and readiness must be measured by the supported authorized host path at continuation time. [O07–O08]

Therefore I cannot conclude “NF1A can simply resume now.” That conclusion is blocked by its actual remaining execution state as well as the identified PCE2 defects. After the necessary correction and targeted qualification, the relevant next operation is current NF1A recovery/continuation—not reinserting plans, clearing global state, bypassing liveness or rerunning completed adoption. No donor operation was performed here.

## 10. Final recommendation and finite exit condition

Keep the revision. The main implementation choices are aligned with the goal: capable agents receive useful semantic context; routine mechanics are expressed through small supported operations; privileged execution can be delegated without delegating architectural authority indiscriminately.

One bounded correction pass is justified by the present code, not by optional missing features. It can be organized into two coherent implementation areas within the existing PCE2 outcomes: **adoption/retirement correctness** (F1, F3, F6, F7) and **recovery/continuation composition** (F2, F4, F5). That is a coordination suggestion, not a mandatory agent topology or seven-ticket ceremony.

The exit evidence should be the focused regressions in those findings and affected existing tests. In particular: changed renamed bytes cannot pass old adoption approval; recovering an integrator cannot touch its live producer; an adopted producer completes and integrates; expired public replay finds the original commit; a lower-ranked eligible exact lane is not falsely blocked; unresolved typed external consumers are protected; preparation cannot restore a missing authority.

These are path-specific exit conditions, not a reason to block every ordinary workflow on all seven fixes. F1/F3/F6/F7 concern the newly supported supersession/adoption route; current NF1A continuation must not repeat that route unnecessarily. Its immediate product concerns are exact recovery ownership, public replay and accurate continuation, followed by fresh checks of its actual retained work.

There is no requirement for another general architecture design, model launcher, generalized cache or blanket PCE2 feature sweep. Nor is there justification for asserting every current deployment route is unsafe or automatically rolling back unrelated useful improvements. Correct the affected mechanisms, qualify the public paths, and then assess the current NF1A continuation under its actual authority.

**Bottom line: substantial progress, most earlier defects repaired, but not yet a clean sign-off for live adoption and general delegated recovery. The necessary remainder is specific and finite.**
