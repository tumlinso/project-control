# Repository guidance migration

Project Control migration updates only model-facing guidance and the configured
workflow front door. It is explicit, dry-run by default, idempotent, and
reversible.

An eligible migration may:

- recognize either the owned Coding Workflow or Project Control marker block;
- replace only that owned marker block with current Project Control guidance;
- set `configuration.workflow_front_door` from `coding-workflow` to
  `project-control`; and
- record a bounded migration result.

It must not change a project UUID, Todo database location, task or event history,
checkpoints, gates, interfaces, decisions, claims, worktrees, commits, branches,
unrelated source, or user-authored `AGENTS.md` content outside the owned block.
It never resets or recreates project state. Apply and remove produce ordinary
forward changes; remove reverses only migration-owned fields.

## Safe rehearsal

Run dry-run, apply, idempotent reapply, and remove only in a genuinely
independent disposable clone. Use `git clone --no-local` or a Git-bundle-derived
clone and verify that its Git common directory and Todo state are independent.
A linked worktree is not a disposable clone because it shares Git common state
and may share Todo authority.

Before and after rehearsal, compare commit ancestry, project UUID, Todo revision
and authority fingerprint, task/event history, and the original checkout's HEAD
and status fingerprint. Only the owned guidance block and front-door field may
change. Do not commit or push rehearsal changes to a real downstream remote.

Real downstream migration is outside PCU-V1 and requires explicit user
authorization. Cellerator is a live read-only sentinel: its real checkout,
configuration, Git state, Todo authority, projections, sessions, claims,
dispatches, gates, resources, and events are never a migration or runtime test
target. Any Cellerator rehearsal must use a genuinely independent clone.

## Compatibility window and rollback

New repositories use `project-control`. Historical `coding-workflow`
owner-system values, sessions, claims, and marker blocks remain readable and
recoverable during the compatibility window. The old executable is a forwarding
alias only; it is not a second backend or concurrent live MCP registration.

Rollback restores the previously recorded registration and service executable
without deleting the candidate or standalone checkout. Revert repository
changes with new ordinary revert commits. Never reset, rebase, amend, squash,
rewrite history, replace a Todo database, or change a project UUID.
