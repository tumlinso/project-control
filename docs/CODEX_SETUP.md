> Project Control is the agent-facing workflow front door for the independently
> installed Todo engine. Use `next_task`, `inspect_task`, `coordinate_task`,
> and `finish_task` for routine workflow work. The connected host's live MCP
> manifest is authoritative for exact schemas and permissions.

# Codex setup

Codex connects to Project Control through its configured stdio MCP registration.
The standard registration uses the sealed release through
`~/.local/bin/project-control`, which resolves the release selected by
`~/.local/state/project-control/current`. The registered profile, principal
binding, workspace configuration, and durable state are part of the existing
local configuration; preserve them when changing the command.

## Use the sealed runtime

Keep Codex configured with the standard `project-control` command. After a
release-pointer change, reconnect the stdio MCP client so it starts from the
selected release and loads its tool manifest. Check exact schemas and
permissions from the connected host's `tools/list` response.

## Explicit source development

From the repository root:

```sh
scripts/pc-dev setup
scripts/pc-dev run --help
scripts/pc-dev run codex
```

The first command creates or synchronizes the source development environment.
For a source-only session, configure a separate Codex registration to invoke
the absolute checkout path to `scripts/pc-dev` with arguments `run`, `codex`.
Do not replace the standard sealed-release registration for routine use. Begin
workflow work with
`next_task(project, repository?, task_id?, run_id?)`; pass the returned handle
to `inspect_task`, `coordinate_task`, and `finish_task`. Use the permitted
actions instead of managing native leases, credentials, or operation IDs.
Plan and execution-maintenance tools remain privileged according to the
configured profile. The host's current `tools/list` response remains the
authority for exact schemas and permissions.

Restart the source Project Control service after source edits and reconnect the
source registration after a deployed tool-manifest change. See
[development](development.md) for tests and [deployment](deployment.md) for
source restart and sealed-release checks.

Read-only questions and investigations do not need a task claim. Project
Control's observer keeps its existing 11 read-only tools. The current compact
profile catalogs are observer 11, coder 12, and mutator 16 tools. Observer
reads include `overview`,
`search`, `delta`, `frontier`, `history`, `evidence`, and `impact` for project
and migrated workflow context. Observer access does not grant mutation rights.
A temporarily unavailable service does not prevent authorized direct checkout
work or source tests. Semantic workflow changes still pass through Todo's
transaction and authorization guards behind the compact interface.

## Delegation

Delegate bounded research, implementation, testing, or review to configured
Codex subagents using the named profiles `scout`, `researcher`, `implementer`,
`reviewer`, or `parallel-head`. Keep ownership and source scope explicit. Do not
use Project Control's local observer service as a coding worker. Ordinary
workers do not recursively delegate. The root controller retains cross-cutting
decisions, integration, and final acceptance. Preserve unrelated edits, state,
and unknown work.

## Candidate construction

When preparing a release candidate, pass the exact standalone Todo wheel along
with explicit source and destination paths:

```sh
python scripts/install.py \
  --project-control-root "$PWD" \
  --todo-wheel /absolute/path/to/todo_orchestrator-0.2.1-py3-none-any.whl \
  --destination /absolute/path/to/candidate
```

`--todo-wheel` is required. An optional domain-content tree can be supplied with
`--skills-root /absolute/path/to/content`. Candidate construction does not
change the current pointer, service, or Codex registration. See
[deployment](deployment.md) for the sealed runtime and source restart paths.
