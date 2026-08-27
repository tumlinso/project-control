# project-control

`project-control` is a standalone, strictly read-only MCP server that gives
ChatGPT a compact live view of registered engineering workspaces. It synthesizes
architecture, planning, coordination, evidence, source identity, and performance
state while leaving every mutation with Codex and the existing coding skills.

It is the observation plane for the unified workflow product:
`coding-workflow` is the model-facing protocol and `todo-orchestrator` is its
transactional kernel. Project-control consumes todo's additive normalized
workflow read and never becomes a second orchestration authority.

The v1 MCP surface is frozen to eight tools:

- `project_overview`
- `project_delta`
- `project_frontier`
- `inspect`
- `evidence`
- `plan_preview`
- `agent_status`
- `performance_status`

The service binds only to loopback and is intended to be connected to ChatGPT
through OpenAI Secure MCP Tunnel. It never accepts arbitrary repository paths,
runs workers or benchmarks, claims tasks, edits registered projects, or mutates
Git/todo state.

Local setup and connection instructions are in `docs/CHATGPT_SETUP.md`.

## Local development

Python 3.11 or newer and `uv` are required for the locked workflow:

```bash
uv sync --frozen
uv run python -m unittest discover -s tests -v
uv run project-control config init
uv run project-control doctor --json
uv run project-control serve
```

Configuration lives at `$XDG_CONFIG_HOME/project-control/config.toml` or
`~/.config/project-control/config.toml`, with mode `0600`. Register repositories
only through `project-control workspace add`; MCP tools accept stable workspace
and repository IDs, never filesystem roots.

Workflow data is additive output on the existing eight tools. It includes the
active run, first-class Codex lane tree and serial queues, authoritative
dispatches, typed blockers, rendezvous, managed workspaces, pending patches,
integration conflicts, context cursors, safe parallel groups, and recovery
attention. `agent_status` keeps first-class Codex/project agents separate from
subordinate local-worker child executions. Claims alone are not agents, and a
local child is never a lane, role, communicator, or rendezvous participant.

An older todo kernel without `semantic workflow` produces an explicit partial
result while legacy task reads remain available. Project-control does not infer
missing workflow semantics from raw tables or repair project state.

The service provides `/healthz`, `/readyz`, `/version`, and the loopback MCP URL
`http://127.0.0.1:8767/mcp`. See `docs/SECURITY.md` for the enforced capability
boundary and `docs/TOOL_CONTRACTS.md` for the frozen v1 schemas.
