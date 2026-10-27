# Markdown Projection and Legacy Format

In v2, `.todo-orchestrator/state.snapshot.json` is the durable structured recovery format and SQLite is live authority. Markdown is generated after semantic commits.

Managed projection blocks are delimited by:

```text
<!-- todo-orchestrator:v2-managed:start -->
...
<!-- todo-orchestrator:v2-managed:end -->
```

Existing text outside managed blocks is preserved. Generated files carry the project revision:

- `todos.md`: human task/workstream index
- `todo-status.md`: human lifecycle/execution/next-action projection
- `todos/<task-id>.md`: objective, current state, ownership, and dependencies

Do not edit managed blocks to claim, unblock, complete, reserve, or lock anything. The next projection replaces those edits.

Legacy repositories may still contain frontmatter and one-line root/status records. `todo migrate markdown --dry-run` parses root, status, and workstream files; reports disagreements; preserves complete original Markdown in migration payloads; and imports old owner/claim labels only as historical or orphaned records. Apply only after reviewing the dry run.
