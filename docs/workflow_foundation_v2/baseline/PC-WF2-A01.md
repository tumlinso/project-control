# PC-WF2-A01 live baseline

Observed on 2026-09-12T17:19Z.  This is an inventory, not a release
qualification or a migration authorization.  It deliberately retains negative
and incomplete observations so later work cannot mistake package timestamps
for live authority.

## Authorities and source identities

| authority | UUID | source head | semantic/workflow revision | semantic fingerprint |
| --- | --- | --- | --- | --- |
| Project Control | `76dc6bf0-1223-4dda-bf8b-c306fb7721a7` | `23c177371860018574f61f327df4af262de6af56` | 606 | `54a2f068a3065f8538b8e5e61b133d2f2823ca525f0aaaf0c1898ee552178e0c` |
| Skills | `460468fe-ee72-4a64-a565-87cfec640d0c` | `07fa81cdd82c15162234b035950d47c85d0ca675` | 802 | `400042d933dd73a5c42407999a65cda1b837bce3e115e7217a63d26d57fbbd62` |

The values above came from the frozen verified runtime
`/home/tumlinson/project-control/.venv-codex-live-links-70b863a/bin/python`
using the package's read-only native bridge, plus the Project Control read
surface (`project_overview` and `agent_status`).  Provider skew was zero for
the Project Control observation.  These are independent authorities; nothing
in this record merges or relocates either database.

## Runtime and API disposition

The frozen runtime identifies installed `project-control 0.3.1` and
`todo-orchestrator 0.3.0`.  It exposes the already-frozen `PC-CONFIG/1`,
`PC-DEPLOY/1`, `PC-SNAPSHOT/1`, and `PC-TOOLS/1` interfaces, while the four
`PC-WF2-IF-*` interfaces remain draft.  The observed Project Control WF2
gates, including `PC-WF2-A01-G`, are pending.

The ordinary `/home/tumlinson/project-control/.venv/bin/python` is *not* a
substitute: the same native observation returned
`runtime_identity_mismatch` for both authorities because its imported
`todo_orchestrator` does not match the configured Skills source.  This is a
confirmed negative finding.  No runtime identity, installed release, source
tree, or database was changed to obtain this inventory.

## Claims, work, and preservation disposition

At observation time the Project Control authority showed the active WF2
coordinator and this first-class claim (`PC-WF2-A01`, lane `PC-WF2-L-A`).
The Skills authority showed its independent coordinator claim and
`SK-WF2-A01` on `SK-WF2-L-A`.  Both source roots were clean; Project Control
reported 26 clean managed/historical worktrees and Skills reported its
pre-existing clean worktrees.  Those worktrees, their branches, and all
unlisted historical state are preserved as **unknown/unmodified**, not
implicitly disposable.

Historical WF/PCU records visible through the frozen authority are
**confirmed only as historical completed/frozen obligations**.  They are not
evidence that their old source heads, release artifacts, or deployment
identities remain current.  The WF2 tasks and checkpoints are pending except
for currently claimed coordinator/A01 work; the authoritative read model
also labels its broad project overview as partial, so no omitted item is
classified as complete by this baseline.

## Affected paths and follow-up boundary

This task owns only `docs/workflow_foundation_v2/baseline` and did not create
or modify tests.  The package planning paths were read only.  Runtime identity
reconciliation, isolated environments, and any release cutover are explicitly
deferred to later authorized tasks; this record grants none of them.
