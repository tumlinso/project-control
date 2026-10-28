# Maintenance compatibility and rollback

This is an administrator reference, not ordinary agent routing. Existing v2
plans, snapshots, tasks, dependencies, checkpoints, barriers, interfaces,
decisions, scopes, locks, resources, gates, evidence, handoffs, and child
executions remain supported. Legacy projects without a front-door setting keep
direct CLI compatibility until explicitly migrated.

Old live-override, force-release, and terminal-checkpoint repair commands may
remain as deprecated wrappers for historical automation. They are not MCP
tools. New owner recovery is one TTY flow:

```bash
coding-workflow-admin recover --repo <repo> [--task <id>] --reason "<reason>"
```

The installer's owner-only rollback file records the prior stdio registration.
Failed installed smoke restores that command, arguments, and environment.
Repository migration is separately reversible with `scripts/migrate.py
--remove`; it does not rewrite plans, IDs, gates, constraints, snapshots, or
SQLite.
