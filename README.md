# project-control

`project-control` is the sole model-facing product for observing and coordinating
registered engineering workspaces. It composes two separately enforced MCP
profiles over one implementation:

- **observer** is the existing loopback Streamable HTTP service for ChatGPT. It
  retains the exact 15-tool surface and is permanently project-read-only.
- **codex** is a stdio server registered as `project-control`. It exposes the
  canonical six Todo workflow tools plus the fourteen rich Project Control read
  tools. It does not expose `terminal_capture`.

Todo Orchestrator remains the sole transactional workflow kernel and SQLite
semantic authority. Project Control verifies and imports that canonical runtime
in-process; it neither calls another MCP server nor copies scheduling, claim,
capability, transaction, completion, or recovery logic. The old
`coding-workflow` name is a temporary forwarding compatibility alias, not a
second product, backend, or live registration.

The observer's one execution aperture, `terminal_capture`, runs only a
repository-contained executable in a fail-closed observation sandbox and
returns the rendered PTY screen. Its mutable state is confined to an app-private
PTY registry and grants no Todo, Git, repository, or workflow authority.

Project Control v2 is the compatibility authority: it preserves the eight v1
tools and makes six richer reads first-class. Project Control 0.3.1/tool schema
v3 freezes those fourteen input contracts and adds one tool. The discovered
surface is exactly fifteen tools:

- `project_overview`
- `project_delta`
- `project_frontier`
- `inspect`
- `evidence`
- `plan_preview`
- `agent_status`
- `performance_status`
- `architecture_context`
- `coordination_view`
- `source_context`
- `history_trace`
- `impact_preview`
- `program_context`
- `terminal_capture`

The observer service binds only to loopback and is intended to be connected to
ChatGPT through OpenAI Secure MCP Tunnel. The fourteen query tools never accept
arbitrary repository paths, run workers or benchmarks, claim tasks, edit
registered projects, or mutate Git/todo state. `terminal_capture` accepts no
shell or host path and gives the child a read-only repository, isolated
HOME/tmp, and no network through bubblewrap. Bubblewrap is required; the
capability fails closed when it is unavailable. `pyte` supplies the VT state
machine.

Local setup and connection instructions are in `docs/CHATGPT_SETUP.md`.
Codex setup, compatibility, and cheap-first usage are in `docs/CODEX_SETUP.md`;
repository guidance migration is in `docs/MIGRATION.md`.

## Codex usage policy

Normal Codex work starts with the bounded workflow protocol:

1. `next_task` acquires or resumes the current first-class lane task.
2. `inspect_task` retrieves bounded current-task context.
3. `coordinate_task` handles typed synchronization, gates, interfaces,
   rendezvous, and integration requests.

`delegate_task`, `collect_delegation`, and `finish_task` complete that canonical
six-tool protocol. The fourteen rich reads remain available as secondary
escalation tools when current-task context is insufficient or source,
architecture, history, impact, performance, or cross-project context is
genuinely needed.

## Local development

Python 3.11 or newer and `uv` are required for the locked workflow:

```bash
uv sync --frozen
uv run python -m unittest discover -s tests -v
uv run project-control config init
uv run project-control config migrate --dry-run
# Apply only when explicitly intended:
uv run project-control config migrate --apply
uv run project-control doctor --json
uv run project-control serve
```

Configuration lives at `$XDG_CONFIG_HOME/project-control/config.toml` or
`~/.config/project-control/config.toml`, with mode `0600`. Register repositories
only through `project-control workspace add`; MCP tools accept stable workspace
and repository IDs, never filesystem roots.

Schema v2 can add query-only program groups without changing workspace authority:

```toml
[programs.biological-stack]
display_name = "Biological computation stack"
workspaces = ["baseplane", "cellerator", "cellshard", "glasshelix"]
```

`project-control doctor --json` is the local-only provider diagnostic. It may
show selected executable and filesystem paths; ordinary MCP output replaces
private locations with stable IDs and bounded error classifications.
Terminal diagnostics separate installation, timeout, namespace, mount,
permission, and service-policy failures. The hardened systemd unit permits
`AF_NETLINK` only because bubblewrap needs `NETLINK_ROUTE` while constructing
the isolated network namespace; the sandbox still has no external network.

Bonded terminal sessions are app-private live runtime objects. Launch with
`kill_after_capture=false` to receive an opaque session ID and optional unique
active label, then recapture the same PTY by either identity. They survive MCP
request boundaries but intentionally do not survive a Project Control service
restart; shutdown terminates and reaps them. Default capture kills the owned
process group after rendering.

Workflow data is additive output on the existing tools. Operational state comes
only from `todo semantic workflow`; the official durable export enriches records
anchored by that read and is never independently interpreted as worker activity. It includes the
active run, first-class Codex lane tree and serial queues, authoritative
dispatches, typed blockers, rendezvous, managed workspaces, pending patches,
integration conflicts, context cursors, safe parallel groups, and recovery
attention. `agent_status` keeps first-class Codex/project agents separate from
subordinate local-worker child executions. Claims alone are not agents, and a
local child is never a lane, role, communicator, or rendezvous participant.

An older todo kernel without `semantic workflow` produces an explicit partial
result while legacy task reads remain available. Project-control does not infer
missing workflow semantics from raw tables or repair project state.

Registered repositories are worktree-aware. The configured checkout remains the
security anchor while verified same-Git-common worktrees receive stable public
IDs. Reads use pre/post identities and return partial or racy results if active
source changes; they never lock or modify a worktree. Derived lexical context is
disposable and stored only below `$XDG_CACHE_HOME/project-control/`.

Configuration schema v2 optionally defines query-only programs. Membership does
not imply dependency, ownership, or architectural authority, and cross-project
observations report per-project cursors and skew rather than claiming one global
transaction. Schema v1 remains readable and is never rewritten automatically.

The service provides `/healthz`, `/readyz`, `/version`, and the loopback MCP URL
`http://127.0.0.1:8767/mcp`. See `docs/SECURITY.md` for the enforced capability
boundary and `docs/TOOL_CONTRACTS.md` for the frozen v2 contracts and additive
v3 terminal contract.
