# Observer maintenance patch: activated

The approved bounded patch is active in the source-mode service at
`/home/tumlinson/project-control`. Both HTTP and inference services restarted
together, assistance was resumed through the supported command, and live
health, readiness, tool guidance and a connected non-inference evidence read
passed. At this maintenance cutover, historical cleanup-failed reservations remained
and investigation capacity was exhausted. The subsequent guarded recovery and
working live requests are documented in [the functional activation record](2026-10-08-observer-functional-activation.md).

The complete [patch](2026-10-08-observer-maintenance.patch) and
[file/hash manifest](2026-10-08-observer-maintenance-manifest.json) preserve the
prepared result from `/tmp/pc-observer-quickfix-20261008`, based on commit
`d8c45ebf7991e9338da5e6971ac0be7ccfa455ae`. No commit, push, new epic, cache reset,
slot clearing or service-configuration change was performed.

## Changes

- Add nullable `execution_slots.cleanup_error_code` through an idempotent migration.
  Only allowlisted cleanup codes persist; existing rows retain null because old
  failure details cannot be reconstructed.
- Add operator `execution_capacity` and a separate `execution_cleanup_errors`
  section. Existing `active_execution_slots` records retain their exact shape,
  preserving physical-release verification and recovery contracts.
- Keep public `thinking` while distinguishing accepted/waiting and running states
  through sanitized messages. Identity, deadlines, admission and negative caching
  are unchanged.
- Preserve confidence values and add `confidence_basis=matching_evidence_presence`
  and `claim_support_status=not_evaluated`.
- Stop impossible packet-continuation loops with a profile/size limitation;
  retain stored packets, genuine evidence omissions and provenance. Preserve
  usable expansion routes and bound oversized impact previews.
- Expose bounded raw task next-action context for blocked frontier entries with
  no computed blockers; document supported machine selectors.
- State in observer server instructions and the actual registered `investigate`
  and `skill` descriptions that local reasoning is limited. Favor bounded
  evidence gathering, targeted inspection, installed-skill lookup and simple
  grounded summaries. Its controlled project/machine/skill context supplies
  evidence; difficult inference, architecture, strategy and consequential
  decisions remain with the caller. No internal model protocol or authority
  changes were made.

## Validation

All commands used `scripts/pc-dev` in the isolated checkout. Counts below describe
separate, overlapping runs and must not be summed as unique coverage.

| Selection | Result |
| --- | --- |
| `tests/test_query_services.py` | 19 passed |
| AS1 evidence execution-distinction wrapper test | 1 passed |
| Initial broker-close, demand-broker and inquiry-guidance selection | 24 passed |
| Cleanup recovery, owned release and final CLI selection | 62 passed, 5 subtests |
| Final inquiry wording selection | 2 passed |
| Broker and owned-release selection after slot-shape correction | 41 passed, 5 subtests |
| Context, trace and project-model suites after impact fallback correction | 65 passed |
| Composed observer guidance/pending-response test | 1 passed outside sandbox |
| Default source smoke | 23 passed |

The default smoke ran once on the integrated candidate before the final review
corrections. Those corrections were subsequently checked by the focused broker,
owned-release, context and trace selections above. Independent review identified
and then confirmed resolution of the slot-shape compatibility issue and the
oversized impact-preview edge. Final `git diff --check` passed.

The composed guidance test hung during asyncio executor shutdown inside the
managed sandbox. A bare `asyncio.run(asyncio.to_thread(lambda: 1))` reproduced
that environment behavior; the same probe and composed CPU test completed
outside the sandbox. Interrupted runs are not counted as passes. No assertion,
skip/xfail, harness, or production async behavior was changed to obtain a pass.

Preparation did not run live model-quality, GPU-computation, recovery-campaign
or frozen-release qualification. Activation subsequently checked live readiness,
registered instructions and a bounded non-inference MCP read, as recorded below.

## Activation and remaining limitations

Activation was initially deferred because retained broker slots did not establish
physical release. After the user confirmed other work was paused and authorized
full activation, the original source issued a supported release request and the
HTTP service stopped gracefully. The existing supervisor then returned two
authenticated process-and-memory cleanup receipts through its guarded native
physical-release API, with no active leases or admissions. Both services were
inactive before applying the patch. No release proof was fabricated and no
reservation or ledger record was manually edited.

Before application, all baseline hashes and the patch hash matched. Original
files and a read-only SQLite backup were preserved in
`/home/tumlinson/project-control/.cache/observer-maintenance-activation-20261008`.
The patch was applied, inference and HTTP restarted together, and the supported
`assistance resume --release` command removed the release veto. Automatic
assistance remains disabled. All 18 activated files match candidate hashes.

HTTP stop began at 13:28:15 UTC and the paired restart completed at 13:30:05 UTC
on 2026-10-08, approximately 110 seconds later. This measures stop-to-restart,
not a guaranteed end-to-end outage bound. Initial five-second direct probes timed
out; later direct health and readiness requests succeeded. Their cause was not
established. Health/readiness/version verification completed at 13:33:33 UTC.
The HTTP dispatcher reported running with no last error, core and optional
content reported available, and the supervisor reported available. New PIDs:
HTTP 328536; inference 328534. The source fingerprint is
`ea49f086ffb30f9482dd1a8cf2d623191188aa62d3a9480bfa8157c02c997fbb`.

Live MCP initialization and tool listing confirm the limited-reasoning guidance
in server instructions and both `investigate` and `skill` descriptions. A
connected client evidence call for `PBMC3K zero planner regret` returned packet
`pkt_c5ffe5eddd924137a424a1592098c94a`, with both additive clarification fields.
Its `partial`/`evidence_unavailable` response is a preserved evidence limitation,
not an activation failure. The captured response is
[activation evidence](2026-10-08-observer-activation-evidence.json).

Operator capacity reports total 2, occupied 2, available 0,
`degraded_by_failed_cleanup=true`. Both historical reservations and their owners
remain preserved. Their new cleanup code is null because historical details
cannot be reconstructed. The resource ledger still reports four current
sessions (two active, two release-pending); physical stop receipts did not
reconcile those historical records. No model is resident in the new demand-only
supervisor. Readiness therefore does not establish usable investigation capacity
or restored model execution. Stranded-slot recovery remains a separate decision.

Rollback originals, native release proof, request/restart timestamps, health and
registered guidance are retained in the backup directory. Rollback would restore
only the 18 owned files, remove the one newly added test, and restart the service
pair, retaining the nullable column and all historical records. No rollback was
needed.
