# PCE2 second review correction pass

Scope: the seven concrete findings in `pce2-second-round-review/REVIEW.md`.
This is a bounded repair of existing operations, not a redesign or new epic.
Root owns scope, ownership rules, integration and final acceptance; assigned
workers implement and test exact corrections. No donor operations are included.

| Finding | Required result | Status |
|---|---|---|
| F1 | Editing a renamed destination invalidates prepared content approval. | Skills `dfd9da0`; real Git rename-content regression and public prepare/edit/application refusal pass. |
| F2 | Recovering an integrator leaves its live producer workspace and ownership untouched. | Skills `9e43df2`; actual RecoveryEngine leaves live producer workspace, claim, dispatch and resource unchanged. |
| F3 | An adopted producer publishes to the actual successor integrator and integrates successfully. | Skills `dfd9da0`, PC `64f4561`; adopted producer commits, publishes and integrates through CLI/MCP journey. |
| F4 | Public replay finds canonical committed recovery after receipt-write failure and expiry; unexecuted expired work stays denied. | PC `8c27d03`; public stdio MCP commit/private-write-failure/expired replay returns one audit, while unused expired grant refuses. |
| F5 | An eligible exact lane is not rejected by ranking of a different lane. | Skills `ab4550b`; real two-lane assessment and exact WorkflowProtocol claim regression passes. |
| F6 | Unresolved typed external consumers block retirement; successful historical evidence remains usable. | Skills `dfd9da0`; unresolved checkpoint/interface/barrier consumer guard; actual checkpoint regression passes. |
| F7 | Preparing supersession cannot initialize or restore a missing authority. | PC `64f4561`; missing database plus snapshot preparation refuses without recreating authority. |

Existing exact-snapshot supersession semantics remain intentional. Near-budget
plain-fragment expansion and conditional frontier precision are not included in
this correction pass. No new global graph, scheduler, permission system or
blanket validation loop is introduced.

Source baseline: Project Control `40f80f3`, Skills `e6c1fc8`.

## Evidence and boundaries

- Skills `test_runtime_facade.py`: 14 passed, including renamed destination content sensitivity.
- Skills `test_retirement.py`: 12 passed, including pending typed checkpoint protection.
- Skills `test_workflow_continuation.py`: exact lower-priority lane assessed ready and claimed through WorkflowProtocol.
- PC `tests.test_supersession_journey`: 5 passed, including public changed-rename refusal, missing-authority refusal, and full isolated producer publication/integration.
- Installed focused recovery qualification: 3 passed in `/tmp/pce2-recovery-candidate-tbtkSO`, exercising actual-engine live-producer preservation plus public MCP canonical replay after expiry and unused-expired denial.

Narrow independent source review found no remaining concrete defect in the
settled scope. It did not rerun tests. F6 checkpoint behavior has executed
regression evidence; interface/barrier branches were source-reviewed. The
source review correctly retained support for stale active dispatches whose
claims have already expired/released: claim lifecycle status alone does not
erase the original exact workspace ownership binding.

No source compatibility redesign or new public request field was necessary.
Isolated producer adoption derives a unique unfinished integrator/validator
task from the declared successor run and refuses zero/ambiguous destinations
before retirement. Exclusive handoffs retain their existing semantics.

## Activated release and handoff

- Candidate: `/home/tumlinson/.local/share/project-control/candidates/pce2-round2-20260921`.
- Production source: PC `64f4561388178f46f58e3c958c040800d1be6400`; Skills `dfd9da0a779fee9ce5bcf1b7cb70ac4425faa70b`.
- Candidate identity digest: `24510f975c1511a804bcd3d5424bf5048806362554dbf1d76a0e645e09584881`.
- Release manifest SHA256: `57f8a6dd022d311d49cd02442cd30f7d1e95ddf946f394e098710b7fb68ebfe1`.
- Rollback: `/home/tumlinson/.local/state/project-control/activations/pce2-round2-20260921T133136Z`.

Final installed `tests.test_supersession_journey` passed all five cases. Recovery
qualification was reused rather than repeated: final PC executable fingerprint
`0f0e4e13c87cc811ac17dd8ef693277073f5dad1f5f98544ff70e00525ccf446` and
Todo runtime fingerprint `15521b1576eab3f0f8aec70519cd2d06f52396db6d48b3a681d5bfb9e6a3464d`
exactly match the recovery-qualified candidate. A full frozen-file comparison
found the only bundle difference was `todo-orchestrator/tests/test_runtime_facade.py`
(the improved rename fixture), not executable production source.

The shared launcher selects this candidate; the service was restarted and its
process manifest/digest verified. Health/readiness returned 200. A fresh stdio
MCP initialized with 22 tools and unchanged main-thread reasoning guidance.
Existing clients need reconnecting to load the new runtime.

Root handled paired release integration and final acceptance. Claims handed
off at PC revision 801 and Skills revision 887. Subsequent commits record
documentation/projections only; no donor authority was inspected or mutated,
and broader PCE2 program acceptance remains separate from these seven fixes.
