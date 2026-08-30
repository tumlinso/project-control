# PCU-DOWNSTREAM-MIGRATION/1

Migration is explicit, dry-run by default, idempotent, reversible, and limited to owned guidance and configuration. It recognizes old and new marker blocks, may replace the owned Coding Workflow block with Project Control guidance, may set `configuration.workflow_front_door` to `project-control`, and records its result.

It never changes project UUID, database location, task or event history, checkpoints, gates, interfaces, decisions, worktrees, commits, branches, unrelated source, or user-authored AGENTS content outside the owned block. It never resets state. Apply/remove produce ordinary forward changes.

Rehearsal uses `git clone --no-local` or a Git-bundle-derived independent clone, never a linked worktree. Real downstream migration—including Cellerator—is outside PCU-V1. Cellerator's checkout and Todo authority are observed only through the existing Project Control read surface and must remain byte-for-byte or semantically unchanged.
