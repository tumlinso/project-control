# PCE2 intermediate review response

This continuation implements the review in `pce2-intermediate-review/` under
the owner's instruction to repair correctness and complete useful maintenance.
It is not a new planning program. NF1A and donor repositories remain paused;
the supersession and preserved-work continuation are qualified on disposable
fixtures before any live use.

The main thread owns reasoning, scope, effect boundaries, and final acceptance.
Subagents perform assigned inspection, implementation and focused checks and
return findings or unresolved choices. The owner's explicit division of work
is retained rather than replaced by the review's optional wording suggestion.

## Selected corrections

| Review | Authorized correction | Evidence/status |
|---|---|---|
| R1, R2, R9 | Correct static path fingerprints; preserve child-acceptance effects; reject malformed executable gates before binding. | Implemented in Skills `d09e0aa`; focused cache/acceptance/contracts regressions pass. |
| R3, R4, R5 | Prove delegated effect scope/liveness; recheck canonical recovery transactionally; store request-linked replay result with effects. | Implemented in Skills `0a530d3`, `c425e28`, `b0e3b72` and PC `bd159fd`, `1165be5`; real canonical-commit/private-receipt fault injection passes. |
| R6, R11 | Bind exact run/workspace continuation, distinguish recovery from current readiness, permit only freshly proven same effects across unrelated revisions. | Implemented in Skills `59ebb59`, `e213785` and PC `fb1943d`, `0ce3b97`, `bfb84da`; clean managed public claim and dirty-preservation journeys pass. |
| R7, R8 | Complete owned coordinator child disposition/cleanup and align executable MCP schemas/annotations. | Implemented in Skills `e213785` and PC `687d12a`; owned/foreign lifecycle and executable schema tests pass. |
| R10, R12 | Retrieve required large context in bounded readable pages; preserve committed handles and typed errors; report actual blockers. | Implemented in Skills `e213785` and PC `687d12a`; real large-charter paging, committed-handle overflow, sync delta and frontier/error tests pass. |
| R13 | Require complete unfinished source membership, protect foreign queues/consumers, preserve source/history and qualify explicit successor continuation. | Canonical checks and measured handoffs implemented in Skills `a14fa7b`, `db1d0af`, `69475b6`; PC `0ce3b97`, `eebc556`, `aa6131a`, `a74d113` add signed owner preparation, durable replay and assessed continuation; public CLI/operator MCP/ordinary MCP isolated-worktree journey passes. |
| J05, J18/J19 | Carry deliberate authorized delegation targets/context; avoid duplicate scheduling within one integration operation without inventing general command caching. | Implemented in Skills `e213785` and PC `687d12a`; managed integration command-count and validation-only queue-preservation tests pass. |

The original review bundle and original supplied bootstrap directory are kept
unchanged. Focused executed results, source commits and release identity will
be recorded here as each coherent change is accepted. Existing unaffected
evidence is reused; test counts are not added into a fictitious coverage total.

## Qualification boundaries

Focused suites cover the modified gate contracts, recovery ownership and replay,
child lifecycle, context paging, managed integration, and run retirement.
These overlap; their test counts are intentionally not added together.
Task-scoped recovery leaves unrelated live or unobservable leases untouched;
its own writable owners still require positive stopped-process evidence.
Project-wide recovery retains its broader ownership checks.

No live NF1A work, donor worktree adoption, or numerical acceptance was performed.
Filesystem/process observations cannot eliminate external changes after observation.
The public maintained result describes current readiness rather than promising
that another actor cannot change it before the subsequent claim.

## Supported maintenance expansion

`project-control admin prepare-maintenance` prepares stopped-execution recovery
with exact run/workspace continuation. `prepare-supersession` accepts a compact
owner intent naming the source/successor runs and optional explicit retained-work
handoffs; the host derives complete membership and measured content identity.
Both feed the existing opaque `maintain_execution` operator tool.

Successful canonical effects remain replayable after a private receipt-write
failure, including after grant expiry. Supersession remains a one-shot exact
authority snapshot: if authority changes before its first application, the owner
prepares a fresh assignment. Unlike regular recovery, it does not refresh the
signed retirement request across unrelated revisions.

Source evidence: Skills `e213785` consolidates shared context, lifecycle, managed
continuation and validation scheduling changes; PC `687d12a` includes matching
tools/frontier/schema changes and guidance. Managed public tests are in
`tests/test_maintenance_managed_journey.py`; retained-work public qualification
is in `tests/test_supersession_journey.py`; postcommit receipt tests are in
`tests/test_maintenance_receipt.py` and `tests/test_supersession_receipt.py`.

## Activated paired release

- Candidate: `/home/tumlinson/.local/share/project-control/candidates/pce2-review-20260920`.
- Packaged production source: PC `aa6131a4f6bd1e3596aab2e810afa6d59897f804`; Skills `e21378560146e8059b51fd4b2b63ce60db532778`. Later commits contain test portability, documentation and handoff evidence only.
- Candidate identity digest: `cab757e62c1b283ea9e321f2f2cafe0982af7c8ab598d8382f1feb582f8dcf82`.
- Release manifest SHA256: `636ecc8307ddc8ed122e1e36bf65c2fe570c91475c964cae6c6fd61cfd0fae4c`.
- Rollback: `/home/tumlinson/.local/state/project-control/activations/pce2-review-20260920T201604Z`.
- Shared launcher switched; user service restarted. Live process manifest/digest matched; `/healthz` and `/readyz` returned 200.

Installed qualification covered eight tests across the maintenance journey,
managed maintenance journey, maintenance receipt, supersession journey and
supersession receipt modules. Seven passed initially; the eighth had a test
fixture import-path error, fixed to use its package-qualified import and rerun
alone successfully. No production change or second candidate build was needed.
Public isolated-worktree supersession proved the active successor dispatch owns
the newly adopted workspace and retained dirty bytes survive unchanged.

The root performed release integration and the one-line installed-test import
repair directly because they were part of final cross-repository acceptance.
Both execution claims were handed off with durable evidence (PC revision 799,
Skills revision 885); broader PCE2 acceptance was not falsely marked complete.

Fresh shared-launcher Codex MCP initialization exposed 22 tools; verified optional
run focus, delegation source targets, mutating collection annotation, and durable
main-thread reasoning guidance. Existing client sessions need reconnecting to
load the newly installed runtime.
