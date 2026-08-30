# PCU-PROFILES/1

Profile selection is trusted startup configuration or a distinct configured entry point. It is never inferred from MCP `clientInfo`, user-agent, model identity, annotations, or a tool argument. Each profile has profile-specific registration and a server-side invocation allowlist.

The observer profile uses loopback Streamable HTTP through the current trusted tunnel arrangement. It registers exactly 15 tools: `project_overview`, `project_delta`, `project_frontier`, `inspect`, `evidence`, `plan_preview`, `agent_status`, `performance_status`, `architecture_context`, `coordination_view`, `source_context`, `history_trace`, `impact_preview`, `program_context`, and `terminal_capture`. Hidden workflow invocation is denied before Todo Orchestrator. `terminal_capture` stays observer-only and app-private, with no Todo, Git, repository, or workflow mutation authority.

The Codex profile uses stdio and registration name `project-control`. It registers the exact six workflow tools and accepted schemas—`next_task`, `inspect_task`, `coordinate_task`, `delegate_task`, `collect_delegation`, `finish_task`—plus the same fourteen rich reads, excluding `terminal_capture`, for 20 tools total. Instructions require cheap-first workflow use: `next_task`, bounded `inspect_task`, then `coordinate_task`; rich reads are secondary escalation tools.
