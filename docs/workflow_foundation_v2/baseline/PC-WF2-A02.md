# PC-WF2-A02 isolated qualification and rollback baseline

Observed 2026-09-12 after A01.  This document registers the qualification
environment before any WF2 milestone depends on it.  It is a safety boundary,
not a release cutover.

## Test inventory and disposable roots

The existing Project Control repository registers `uv run python -m unittest
discover -s tests -v` in `README.md`; the currently observed repository has 39
top-level `tests/test*.py` modules.  WF2 additionally registers
`tests/wf2_acceptance/a/test_a02_environment_baseline.py`, which checks this
document's ownership, fixture separation, and rollback requirements.  The
test is deliberately runnable with the standard library so its registration
does not require a candidate installation.  Before a WF2 milestone relies on
this baseline, run its registered dedicated discovery command:

```
python -B -m unittest discover -s tests/wf2_acceptance/a -p 'test_a02*.py' -v
```

The historical repository-wide `discover -s tests` command remains a
regression check, not the registration mechanism for this nested WF2 suite.
The local registry at `tests/wf2_acceptance/a/README.md` is deliberately
owned by this task; it avoids changing historical test-package topology.

The fixture root is external to both authorities:

```
/home/tumlinson/wf2-preflight-evidence/execution-2026-09-12/fixtures/pc-wf2-a02/
  authority/  # disposable authority fixture only
  backup/     # disposable snapshot/restore exercise only
  candidate/  # candidate runtime files only
```

No fixture path may be a descendant of `/home/tumlinson/project-control` or
`/home/tumlinson/.agents/skills`; neither real authority database nor a
managed worktree is a test fixture.  The deployed launcher and frozen
verified runtime remain the control path while these roots are used.

## Control and rollback procedure

1. Before any candidate launch, record the launcher target, runtime
   interpreter/environment, authority UUID/revision, and a restorable backup
   of the affected disposable database in `backup/`.
2. Run candidates only against `candidate/` and `authority/`; do not point a
   candidate at a real authority.  Keep the deployed launcher untouched so
   the control runtime can continue serving its real authorities.
3. A candidate failure restores the recorded launcher target and environment,
   restores the disposable backup, verifies the prior runtime's health and
   authority identity, then restarts/reconnects the HTTP consumer and creates
   a fresh stdio connection.  Existing stdio processes are not presumed to
   switch executables on an HTTP restart.
4. Preserve candidate files, test logs, and failed restore evidence for
   review.  A real-authority change requires a later explicitly authorized
   deployment task with its own backup and reconnect evidence.

## Dispatch and integration boundary

The observed first-class API is `next_task`, `inspect_task`,
`coordinate_task`, `delegate_task`, `collect_delegation`, and `finish_task`.
Producers use their managed isolated worktrees; the exclusive integration
destination is provisioned through the owner-supported workspace lifecycle,
not by direct Todo/SQLite edits.  A02 does not provision or redirect another
lane's workspace, and it does not claim that an actual deployment rehearsal
has occurred.
