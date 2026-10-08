# Project Control observer and agentic surface: diagnostic report

Investigation date: 2026-10-08 UTC. Diagnostic only; no remediation performed.

## Root-cause summary

The strongest established cause of the current investigation failure is **stranded broker execution capacity after earlier cleanup failures**, not inability to answer the two substantive questions. Two older, terminal investigations still hold both global execution slots under the live observer process. Subsequent accepted work can remain queued until its five-minute deadline. One of the reported substantive requests demonstrably never claimed an attempt; neither recorded observations, findings, an answer, or a model request before expiry. There is no completed answer that was lost or hidden by retrieval in those records.

Retaining uncertain execution slots is an intentional safety interlock. The defect is the resulting availability and recovery gap: the live service can appear healthy while both slots remain unusable, accepted inquiries expire, and the public surface supplies no explanation. The exact underlying cleanup error was not persisted. One request's attempt counter is inconsistent with its frame counters and remains unresolved.

Several other initial interpretations require correction. GPU utilization and VRAM telemetry already work under supported selector names. CE-IS1-TORCH is intentionally blocked by an explicit user hold. Observer packet continuation recovered its task successfully with `extended`; the unrecoverable loop was reproduced through the local Codex profile, where `extended` is forbidden. Evidence confidence is mechanically derived from support-record presence, and those records can be incidental mentions. Specific claim retrieval is literal rather than semantic entailment.

## Evidence boundary and method

Five bounded scout/research agents examined lifecycle, client traces, machine diagnostics, retrieval, and frontier/packets; a sixth performed a bounded report evidence audit. The root integrated their evidence and performed two observer-profile checks unavailable to the frontier researcher: task continuation and README evidence. These used `mcp__codex_apps__project_control_search` and `mcp__codex_apps__project_control_evidence`, the external app connector, rather than native `mcp__project_control__` tools. Selected structured results are retained in [the observer probe appendix](2026-10-08-observer-probes.json). Direct root execution was needed to resolve the profile discrepancy, not to replace delegated investigation. This is a new selective reproduction through the connector, not recovery of the earlier client's transcript.

The checkout was clean at the start, at HEAD `d8c45ebf7991e9338da5e6971ac0be7ccfa455ae`. Host inspection identified observer PID 34150 running from this checkout; supervisor state reports source mode and the active state root. This establishes source/process alignment, but is not a fresh cryptographic attestation of every module already loaded by that process. Source references below refer to that HEAD.

Cellerator read-only observations used semantic revision 7942, project UUID `0ccaac37-dbbf-448e-a5f8-def197a70aba`, and checkout HEAD `7013137ed32d58e5a29400d2153122f4beeeacfa`. Runtime facts are snapshots, not permanent claims.

Evidence used: existing broker SQLite records opened in URI `mode=ro`; canonical Todo SQLite in `mode=ro` with `query_only=ON`; filtered existing call-audit records; supervisor snapshots and bounded host process inspection; source, contracts, and existing tests; selective read-only MCP evidence/search/frontier/machine requests. No new investigate/skill call, model inference, GPU computation, rebuild, service restart, cache release, workflow mutation, or implementation edit was performed. Read-only MCP tools may write their normal private immutable packets; that is part of their existing contract, not a runtime configuration change. Existing tests were inspected, not executed; no claim of test-suite or deployment qualification is made.

Original external-client response bodies and the batch-timeout traceback were not recovered. The audit does recover individual call timing. Do not mistake this report's agents' session transcripts for the original client transcript. Historical memory supplied leads only; current job records independently confirmed the historical failure classes described below.

## Issue inventory, ordered by impact

| ID | Finding | Classification | Confidence |
| --- | --- | --- | --- |
| L1 | Two failed-cleanup slots exhaust global broker execution capacity; later work expires | Confirmed availability/recovery defect around an intentional safety guard | High |
| L2 | `thinking` hides queued/nonproductive work; terminal failure collapses to `analysis_unavailable` | Intended state projection and negative cache; diagnostic capability gap | High |
| L3 | Physical cleanup failure reason and one attempt-count discrepancy cannot be reconstructed | Confirmed missing durable diagnostics; counter discrepancy unresolved | High / unresolved |
| L4 | Larger concurrent batch timed out in client orchestration | User-observed event; cause unresolved | Low for causality |
| E1 | Incidental source mentions produce `confidence: high` | Confirmed misleading confidence semantics; mentions are explicitly not proof | High |
| E2 | Explicit zero-regret proposition misses literal evidence lookup | Confirmed retrieval limitation, not evidence the proposition is false | High |
| P1 | Packet continuation loops when whole packet exceeds caller's permitted budget | Confirmed local-profile usability defect; observer example succeeds | High |
| S1 | Metadata resolution and source-text discovery differ; source-first ordering disadvantages docs | Intended split/order, bounded retrieval limitation; specific original misses unresolved | High for mechanism, medium for incident attribution |
| F1 | Blocked task has empty immediate blockers despite a user hold | Intended blocked state; reason projection omission | High |
| M1 | Guessed GPU selectors rejected although named GPU views work | Intended allowlist/client discovery mismatch; power and attribution missing | High |

## L1. Stranded execution slots explain current noncompletion

### Exact server observations

Active broker: `/home/tumlinson/.cache/project-control/as1-observer-analysis/as1/jobs-v2/jobs.sqlite3`. The similarly named `.local/state` broker was old and empty; using it would give a false diagnosis. Supervisor state named the `.cache` state root.

| Job | Created UTC | Terminal UTC | Persisted state | Productive evidence |
| --- | --- | --- | --- | --- |
| `job_9c95f5e934c441ca92e3014ec64ab8ba` | 11:04:27.771 | 11:05:04.442 | Completed, attempt 2; simpler purpose-of-Cellerator question | Answer, three findings, result `pkt_658f9fe5cd184bc1bbb1c42f8f35ac45` |
| `job_ccb645b85cac4e7ab4118a2488ab8b49` | Earlier substantive inquiry | 11:16:59.619 | Partial, `deadline_exhausted`; execution slot retained at attempt 2 | Earlier model-request audit events exist |
| `job_cbdec88c0ea240a9a37a13bb99c13c25` | Earlier substantive inquiry | 11:16:59.619 | Partial, `deadline_exhausted`; execution slot retained at attempt 3 | Earlier model-request audit events exist |
| `job_c2cc682affe648b0bad891f1936a95f5` | 12:17:14.158 | 12:22:14.258 | Failed, `deadline_exhausted`; job attempt 1, frame expired, turns used 0 | No observations, answer, findings, result, frame-attempt row, or matching model-request audit event |
| `job_311af2e241064cb796ffaefb3d12cf5b` | 12:17:50.042 | 12:22:50.065 | Failed, `attempt_or_deadline_exhausted`; attempt 0, frame still queued, turns used 0 | Same absence of productive evidence |

The two retained slots have `cleanup_failed=1`, owner PID 34150, owner-start identity 144964, and lease timestamps 11:20:00 / 11:18:58 UTC. PID 34150 was still alive around 12:25. Their jobs are terminal, but their slots remain counted. Broker execution slots are distinct from inference-supervisor model slots. The supervisor snapshot reports capacity 2, zero active admissions/leases, and two idle model slots; it does not erase broker safety reservations or prove physical release.

The current inquiries are identifiable without guessing from question similarity:

- PBMC3K planner-intelligence question: inquiry identity `c2f0d69b8b545d0daebb0ac7edbceff196fe3cb00543c44c68134c88c3a179a3` maps to `job_c2cc682affe648b0bad891f1936a95f5`.
- Same cell-by-gene dataset, similarity retrieval versus dynamical propagation question: identity `39ee75c2cb3eac534d19b65f036636a0b8e4831f1bf77f1579f2beebeb848273` maps to `job_311af2e241064cb796ffaefb3d12cf5b`.

Filtered `/home/tumlinson/.cache/project-control/call-audit/calls.jsonl` shows PBMC3K submission at 12:17:13.790, return at 12:17:14.184, then repeated identical calls at 12:17:26, :34, :39, :54; 12:18:03, :14, :41; 12:19:21; 12:20:57. The second question starts at 12:17:49.090 and repeats at 12:17:56, 12:18:03, :16, :41, 12:19:21, 12:20:56. Individual calls returned in approximately 0.3–1.5 seconds. No matching investigator model requests were recorded in that window. Earlier jobs did have model requests at 11:11:58, 11:12:07, 11:15:12, 11:15:51, and 11:16:38.

### Mechanism and classification

[`JobService.claim`](../../src/project_control/as1_jobs.py#L2143) only removes expired slots when cleanup has not failed and the owner identity is absent. The hard global execution cap is two ([line 2224](../../src/project_control/as1_jobs.py#L2224)). [`_execute` cleanup](../../src/project_control/as1_jobs.py#L3041) deliberately retains the slot on failed close/release. Tests explicitly protect this behavior: [`test_pc_as1_jobs.py`](../../tests/as1/test_pc_as1_jobs.py#L286) covers a live noncooperative operation; [`test_pc_as1_inquiry_cache.py`](../../tests/as1/test_pc_as1_inquiry_cache.py#L389) retains failed-cleanup capacity even after time advances. Trusted recovery tests require owner absence and physical release proof ([`test_execution_cleanup_recovery.py`](../../tests/assistance/test_execution_cleanup_recovery.py)).

**Established:** all broker capacity is retained by old failed-cleanup work; the second incident inquiry never executes and expires. Both current questions are nonproductive through expiry. **Not established:** why the physical/backend close failed, or precisely how the PBMC3K job acquired attempt 1 while its frame has no attempt rows. Therefore attribution of that job's entire scheduling history to the retained slots is less certain than for the queued attempt-0 job.

The safety guard is intended. The observed service-level availability failure is real: two uncertain releases can poison the entire shared inquiry pool while the owning service stays alive. Current supervisor idleness makes an ongoing physical operation less likely, but is insufficient release proof. Consequences extend beyond Cellerator: the cap is global, so unrelated questions/projects can be affected. No evidence shows these two current requests overwrote one another or caused the older cleanup failure.

**Reproduction:** inspect the existing jobs, inquiry index, slots, frames, and attempts using the read-only SQL in [the reproduction appendix](2026-10-08-lifecycle-reproduction.md). No disruptive cleanup-failure experiment is needed to demonstrate the current condition.

**Future direction:** preserve uncertain-release fencing, but design trusted recovery for terminal reservations whose long-lived owner remains alive; distinguish broker capacity from model capacity; expose degraded dispatch/admission health before accepting work that cannot progress. First retrieve close/release diagnostics and correlate backend session ownership before choosing that design. Do not blindly clear slots or increase capacity.

## L2–L3. Identity, lifecycle projection, persistence, and diagnostics

[`_inquire`](../../src/project_control/as1_jobs.py#L1715) uses exact inquiry identity and returns `thinking` for nonterminal indexed jobs. Submission persists the identity-to-job mapping ([line 1348](../../src/project_control/as1_jobs.py#L1348)). The [cache contract](../as1-inquiry-cache.md) includes literal question, trusted project/authority, mode and selected skill; whitespace matters. Detail, request ID and advisory hints do not create another inquiry. Exact retries do not restart work, extend expiry, or change scheduler order. Global limits are two executing, four waiting, 30-second foreground wait and 300-second lifetime.

Thus `thinking` means an inquiry is pending, not that a model is presently reasoning. The reported retries occurred before the persisted five-minute expiries. They are consistent with the same queued work. The audit lacks response bodies, so each individual `thinking` body is still user-reported; its semantics are independently supported by source and durable status.

At expiry there was no usable result. [`_cache_eligible`](../../src/project_control/as1_jobs.py#L1822) rejects those terminal records; exact terminal repeats map to `unavailable` with `analysis_unavailable` ([lines 1742](../../src/project_control/as1_jobs.py#L1742) and [1802](../../src/project_control/as1_jobs.py#L1802)). Empty terminal failures are intentionally negative cached, without automatic resubmission. No current stale completed answer or lost completion was found. A failed job can coexist with a still-queued frame, as the second incident record shows; public state follows the durable job, so frame state alone is insufficient for diagnosis.

Earlier retained records independently show that the same public failure can hide different causes:

- `job_6d6fb31c7fb94cc6ae98ae27b9dacd9c`: failed 2026-10-05, malformed JSON, `Extra data: line 2 column 1 (char 131)`.
- `job_d6d9e2fc8c634b23802d8008db2ef0a7`: empty partial, `Extra data: line 1 column 155 (char 154)`.
- `job_1b073443353448a8bb0a0c29ccafc579` and `job_f81b6fa5f4fe4aff8342db62ba6dfb07`: `finding_requires_observed_packet`.
- `job_09323aa9adee482aa3cee7df0087d522`: `context_budget`.

Those failures do not establish the cause of an unmatched external-client response or of the October 8 incidents. The simpler completed job predates the stranded-slot condition; it proves one successful execution, not reliable substantive concurrency.

**Classification/confidence:** exact deduplication and negative caching are intended, high confidence. The lack of actionable progress/failure projection is a confirmed observability gap. The cleanup error is represented durably only by a boolean; detailed `last_error` is ephemeral. The attempt discrepancy is unresolved, not a proven double-execution defect. **Scope:** all inquiry clients can mistake pending for executing and cannot distinguish queue starvation, parse failure, or empty evidence from `analysis_unavailable` alone.

**Future direction:** persist bounded cleanup/failure classes and lifecycle timestamps; provide safe operator diagnostics for queued, executing, parked, expired and uncertain-release states. Reconcile job/frame/attempt accounting and test this exact mismatch before changing lifecycle semantics. Private `inquiry_failure_diagnostics()` already exists; it is not a public observer tool. Any later negative-cache release must use the supported exact terminal-reason hook, not hand-edit SQLite.

## L4. Client batch timeout and concurrency

The larger batch's orchestration timeout is user-observed. No original client stack, timeout setting, batch request correlation, or response transcript was recovered. The existing audit shows the two individual submissions/retries returned quickly; it does not show a timeout for them. The [contract](../as1-inquiry-cache.md) and inquiry adapter move foreground waiting off the HTTP event loop; that is a design property, not fresh load qualification.

**Established interaction:** global shared execution and queue capacity permits cross-request contention; the two old reservations block later work. **Unproven:** event-loop starvation, a client concurrency cap, a tool-bridge deadline, SQLite contention, batch cancellation behavior, or responses exceeding a client payload limit. Do not equate the 300-second inquiry lifetime with a transport/orchestration timeout.

**Classification/confidence:** unresolved cause, low causal confidence. Scope could be client-only, server-load-related, or both. **Reproduction direction:** retain the original client orchestration trace, exact tools/arguments, start/end times and per-call outcomes, then correlate the existing server audit. If a later bounded replay is needed, start with non-inference read tools and record the client timeout budget; do not begin an expensive concurrent model campaign to fill this evidence gap.

## E1–E2. Evidence confidence and claim matching

### Incidental README support: reproduced on the observer

At 12:30:01 UTC, `evidence(project="cellerator", subject="README.md", kinds=["source"], detail="extended")` returned `status: ok`, `confidence: high`, packet `pkt_4320b9a5f523478097be0274e0f638bd`, complete coverage, no contradictions, and `source_mentions_are_proof: false`. Its 30 support entries were filename mentions rather than the requested README's substantive content. Examples:

- `experiments/moonshot-parallel-v1/diff/check_diff.py:19`: a filename filter.
- `experiments/moonshot-parallel-v1/integration/evidence/cuda-build-2.stdout.txt:15,34,606`: installation messages for runtime, distributed planner and model README files.
- Plan JSON paths and a documentation receipt hash also appeared.

This reproduces the suspicious behavior without default optional evidence providers or an inference call. It does not prove a particular claim false or imply the tool fabricated those excerpts.

[`evidence_for`](../../src/project_control/services/evidence.py#L95) converts source matches directly into support ([line 110](../../src/project_control/services/evidence.py#L110)). Confidence is exactly `high if support and not contradictions`, otherwise mixed or insufficient ([line 197](../../src/project_control/services/evidence.py#L197)). [`CtxppReadAdapter.inspect`](../../src/project_control/adapters/ctxpp.py#L19) searches for the whole target substring in serialized existing index rows, returns up to 30 records, and falls back to fixed-string Git grep if no matches or stale context ([line 84](../../src/project_control/adapters/ctxpp.py#L84)). Matches can be incidental fields; the matching field can disappear when the row is compacted. Nonempty index matches can prevent a documentation fallback.

**Classification:** confirmed misleading confidence semantics, high confidence. The output explicitly says mentions are not proof, so claim-level certainty is also a client misinterpretation; nevertheless the `claim`/`support`/`high` vocabulary invites that interpretation, and [TOOL_CONTRACTS](../TOOL_CONTRACTS.md#L286) requires relevant support. For a bare filename, incidental references are relevant as mentions, not evidence of the file's contents or authority. This is a semantic-contract problem, not proof that retrieval fabricated evidence.

**Future direction:** separate relevance/retrieval success, source authority/freshness, and proposition support. Prefer an exact path read for a filename subject. Do not infer high claim confidence from any nonempty hit list or treat absence of discovered contradictions as adjudicated truth.

### Specific zero-regret claim: reproduced literal miss

Native read-only `evidence(project="cellerator", subject="PBMC3K zero planner regret", kinds=["source"], detail="compact")` returned insufficient confidence, empty support, `no_matching_evidence` and `evidence_unavailable`, packet `pkt_fed2fb9a44e0462a91b91aad2ac254b3`. Broad subject `PBMC3K` returned high confidence with `LexicalStructDecl` index records from C++ fixtures, packet `pkt_2dddac41886949c7a2087d946bd04955`; these were not proposition excerpts.

The explicit authoritative explanatory passage exists at `/home/tumlinson/Cellerator/docs/CE_LIVE_EVIDENCE.md:16–23`: it reports 0% regret, qualifies the catalog to one legal FMP1 schedule per request, and disclaims a universal cuSPARSE comparison. `docs/CE_LIVE_PROGRAM.md:568–571` repeats that boundary. Local bounded `rg` found both. The exact phrase `PBMC3K zero planner regret` is not the wording of that passage.

**Root cause:** whole-subject literal matching plus source-index preference, not semantic claim verification. **Classification/confidence:** confirmed retrieval limitation, high confidence; not an incorrect judgment that the claim is false. The broad query's high label reinforces E1. Source authority is not enough when the passage is never selected. No independent index-corruption defect was established.

**Scope/consequence:** equivalent wording, cross-line propositions and prose documentation can be missed while broad incidental C++ records appear sufficient. **Future direction:** retrieve term-level candidate passages, expose their authority and exact text, and separately adjudicate support. Capture original exact request arguments before assigning the earlier client's miss to this precise mechanism.

## P1. Partial responses, continuation, and recovered coverage

Outer response limits are 2,048 / 8,192 / 65,536 bytes for compact / standard / observer-only extended ([`as1_contracts.py`](../../src/project_control/as1_contracts.py#L24)). [`InformationService.call`](../../src/project_control/as1_context.py#L228) stores the whole payload first, then replaces oversized ordinary response data with `needed_bytes` and an exact packet continuation. Packet retrieval resolves a whole immutable payload, without offset/page ([line 398](../../src/project_control/as1_context.py#L398)). Repeated oversized packet requests preserve the same target.

**Native Codex reproduction:** task lookup of CE-IS1-TORCH at standard detail needed 9,260 bytes, packet `pkt_c40e8d4c8a1b411eabcb8ab92a2df0e5`. Packet retrieval needed 19,219 bytes and returned the same continuation at standard and compact. Extended was rejected as observer-only. README source evidence similarly needed 9,079 bytes, then 19,038 through packet retrieval. The same indivisible object cannot fit the permitted cap; this is a confirmed local-profile usability defect, not lost packet storage.

**Observer counterevidence:** at 12:29:08, the same task lookup at standard detail produced packet `pkt_c56138f973f5410cb9976a6c5080622d`, needed 9,260 bytes and instructed `extended`. Following that instruction at 12:29:16 returned `status: ok`, complete outer/stored coverage, packet `pkt_a3d7a4fbeae74bcc99202897c27e29f6`, and the user-hold text. Thus the external observer example works. A packet exceeding even extended capacity would still face the same whole-object limitation; that is source-backed risk, not a separately executed observer failure here.

The recovered task packet contains 52 worktree cursor identities and duplicated task/resolution information. Its inner graph truncation says 605 items considered, 52 returned, `truncated: true`, even though outer coverage is complete. These are distinct layers: complete delivery of a stored bounded selection does not mean exhaustive project retrieval. Native `search("atoms")` needed 35,808 bytes and mostly exposed locators instead of useful explanatory snippets. Large metadata envelopes can crowd out even a single task explanation; packet retrieval adds wrapping overhead. This demonstrably degrades usability, but does not establish that it caused the client orchestration timeout.

**Genuine unavailable evidence is separate:** `local_worker_state_unavailable` means the configured owner-only snapshot is absent ([`adapters/local_worker.py`](../../src/project_control/adapters/local_worker.py#L17)); `cuda_evidence_unavailable` means optional CUDA records/database are unavailable ([`adapters/cuda.py`](../../src/project_control/adapters/cuda.py#L14)); `evidence_unavailable` means no selected support/stale evidence, not that no relevant facts exist anywhere. These omissions can coexist with `response_budget`. Provider-specific tests preserve unrelated healthy reads ([`test_query_services.py`](../../tests/test_query_services.py#L144)). None of these signals alone establishes an inference-server failure.

**Confidence:** high for both profile-specific outcomes and provider semantics; original external recovered-coverage sequence remains unavailable. **Future direction:** make continuation useful within each role, provide bounded projections/pages or explicit oversize terminals, reduce repeated identity metadata, and test public multi-call recovery through each profile. Clarify inner selection coverage separately from outer transport delivery.

## S1. Search and authoritative source discovery

Exact typed graph lookup intentionally skips fuzzy/lexical source retrieval ([AS1 context contract](../as1-context.md)); resolving a task, invariant or path does not read explanatory text. Source discovery is separate. [`source_context`](../../src/project_control/services/source_context.py#L150) uses existing ctxpp for symbols, lexical indexing for working-tree text/subsystem search, and literal Git fallback for other selectors. [`SourceLexicalIndex.search`](../../src/project_control/source_index.py#L122) uses token OR matching, not semantic embeddings or entailment.

[`source_path_priority`](../../src/project_control/source_index.py#L18) deliberately places ordinary source before tests, Markdown/configuration, and archive/planning material. Documentation therefore has a lower presentation priority than current source; authored authority is not a general override. The live `atoms` query did include `docs/language/cellerator-language-specification.md` after code/test paths, so documentation was not wholly absent. Bounded output prevented inspection of its full selected text through the native profile.

**Classification:** intentional graph/source split and current-source-first ordering; confirmed retrieval/budget limitation, not established systematic exclusion. **Confidence/competition:** high for source mechanics; medium for explaining the reported searches because original queries, selectors, scope and continuation results are missing. Empty source-text results could reflect exact typed lookup, phrase mismatch, selector choice, index preference or bounded ranking. No evidence proves embeddings malfunctioned; this path does not implement that semantic search model.

**Reproduction:** compare an exact typed path query for `docs/CE_LIVE_EVIDENCE.md`, a text query `atoms`, and the literal evidence probes above; inspect response sections and budgets rather than interpreting metadata resolution as text coverage. **Future direction:** expose the retrieval mode, combine graph metadata with authoritative explanatory paths where appropriate, and test query-sensitive prose/source ranking against concrete known passages before changing the index.

## F1. Frontier's empty blocker list conceals an intentional hold

At 12:25:45, native `frontier(project="cellerator", detail="standard")` returned complete coverage, `ready: []`, and CE-IS1-TORCH blocked with `immediate_blockers: []`. It also listed CE-IS1-INTEGRATE blocked by that task.

Canonical read-only Todo inspection confirmed CE-IS1-TORCH status blocked at task revision 7877, `attention_reason=NULL`, no task locks, and its sole prerequisite CE-IS1-MERGE-B done. Next action/notes explicitly contain a user hold dated 2026-10-03: defer packaging, frontends, provider delivery and final qualification; resume only on explicit instruction and owner review. The observer packet continuation independently recovered the same text. The task objective also says path reconciliation does not resume deferred frontend work.

[`frontier.py`](../../src/project_control/services/frontier.py#L92) reports immediate unmet dependency and recovery IDs, not free-text reasons for raw blocked status. [`reconcile.py`](../../src/project_control/reconcile.py#L101) preserves explicit blocked status independently of unmet edges. There is a valid constraint; empty immediate prerequisite IDs do not mean the task is runnable.

**Classification/confidence:** intended blocked/no-ready behavior, high confidence; diagnostic omission of the hold reason. No authority repair or task resume is justified. `scope={"task_id":"CE-IS1-TORCH"}` adds a coordination view rather than filtering the base frontier ([`as1_context.py`](../../src/project_control/as1_context.py#L150), [`CoordinationViewInput`](../../src/project_control/models.py#L372)); the larger response is consistent with that implementation, though tool wording that scope narrows the view is easy to misunderstand.

**Scope/consequence:** any explicitly blocked task with a free-text hold can appear unexplained; scope can increase payload size. **Future direction:** project a canonical hold/status reason with readiness, and clarify coordination scope versus frontier filtering. Preserve this hold.

## M1. Machine capabilities and internal observability

Live observer calls around 12:25–12:26 establish:

| Exact selector | Outcome / packet | Capability and limit |
| --- | --- | --- |
| `host_memory` | OK, `pkt_5727c7e9038b4b329ccebe2a473655a8` | `/proc/meminfo`, approximately 65.8 million kB total |
| `gpu_summary` | OK, `pkt_39ad89d2c9994dfd8ed1dfe4a4ae9ae0` | Four V100s; utilization 0% in this snapshot; total/free VRAM and driver |
| `gpu_processes` | OK, `pkt_945d697933cc461caa7cfa4aec96d10a` | Compute-process names and used VRAM; names may be redacted; no PID/GPU association |
| `gpu_topology` | OK, `pkt_280c391f8e6f4ebf86c2a59777989230` | NV6 pairs 0–2 and 1–3 |
| `processes` | OK, `pkt_bc0ece145cdc47f6a68ccf0b3bc558c6` | Bounded host names/RSS/state; deliberately no PID |
| `gpu`, `gpu_usage`, `nvidia_smi`, `runtime` | `unsupported_machine_view` | Argument dispatch rejects unsupported names |

`host_gpu` and `gpu_memory` were not separately replayed, but the same closed allowlist excludes them. [`MachineDiagnostic`](../../src/project_control/services/machine_inspection.py#L21) also accepts `filesystem_capacity`, `services`, `system`, `pcie_devices`, `storage_block`, `network_state`, `project_control_logs`, `versions`, `proc_sys`, and `filesystem`. The external connector takes a string, so the exact valid names are less discoverable than a native Literal schema.

[`host_gpu_summary`](../../src/project_control/adapters/host.py#L22) executes a fixed `nvidia-smi` column query; fixed GPU process routing is in [`machine_inspection.py`](../../src/project_control/services/machine_inspection.py#L310). Neither requests power nor stable process-to-GPU attribution. The read-only diagnostic sandbox allows fixed commands, not arbitrary `nvidia-smi` arguments. [`test_machine_inspection.py`](../../tests/test_machine_inspection.py#L37) documents query shape and deliberate PID omission.

Worker-pool diagnostics already exist internally: [`LocalWorkerReadAdapter.status`](../../src/project_control/adapters/local_worker.py#L11) reads an existing snapshot without starting a daemon; [`observer_status`](../../src/project_control/local_runtime/local_worker/supervisor.py#L737) contains capacity, slots, admissions and leases. [`agent_status`](../../src/project_control/services/agents.py#L70) has a local-services projection, but was not registered in the inspected public app/surface or runtime tool inventory. Broker job/queue fields are deliberately stripped from public inquiry results in `as1_surface.py`. Pool state, broker queue state and generic host process diagnostics are separate capabilities.

**Classification/confidence:** rejected aliases are intended/client selector mismatch, high confidence. GPU telemetry is not broadly unavailable. Power draw/limits, process PID/GPU association, and exposed broker/worker progress are missing capabilities, not established regressions. **Future direction:** enumerate valid views, add fixed bounded telemetry fields with explicit privacy rules, and expose broker degraded capacity and worker snapshot freshness through a safe named diagnostic view. No arbitrary command execution is required.

## Recommended next work, without remediation

1. Preserve the stranded-slot records and correlate the two older jobs with backend close/release events. Establish physical release and ownership before any recovery action. Capture bounded cleanup errors durably in a future change.
2. Explain the PBMC3K attempt-1/frame-zero discrepancy with transactional claim/transition evidence. Do not assume repeated inference or concurrent interference.
3. Design and test recovery/admission health for live-owner terminal reservations, retaining the safety interlock. This is the highest-priority implementation decision after diagnosis.
4. Obtain the original batch timeout trace and correlate per-call timing; do not use current slot starvation as proof of its transport cause.
5. Separate evidence retrieval confidence from proposition support; qualify known prose passages and incidental mentions with small fixtures.
6. Test packet continuation through both local and observer profiles, including payloads above each maximum cap; reduce metadata overhead and clarify recovered versus exhaustive coverage.
7. Surface canonical hold reasons and supported machine selectors. Add power/process attribution and worker/broker diagnostics only as explicitly scoped functionality.

The implementation and runtime configuration were preserved. Only this diagnostic report and its evidence appendices were added; no fixes, task transitions, recovery action, or remediation campaign were started.
