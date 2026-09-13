# Codex setup

Project Control's Codex profile is a stdio MCP server registered under the name
`project-control`. It is separate from the loopback observer endpoint and does
not use the ChatGPT tunnel.

## Candidate-first installation

Build an isolated candidate environment containing both local distributions:
`project-control` and the canonical `todo-orchestrator` from Skills. The installer also creates `runtime-skills/` and `release-manifest.json`.
For deployment, set `PROJECT_CONTROL_SKILLS_ROOT` to that frozen snapshot,
`PROJECT_CONTROL_RELEASE_MANIFEST` to the manifest's absolute path, and
`PROJECT_CONTROL_RELEASE_DIGEST` to its SHA-256. Keep all three settings and the
executable in one release launcher shared by HTTP and stdio. Editing the
maintained Skills checkout then cannot invalidate the deployed release.
Development mode without a release manifest still compares live source/package
fingerprints. The legacy
`CODING_WORKFLOW_SKILLS_ROOT` name is accepted only during the bounded
compatibility window and emits a deprecation warning.

Before registration, candidate validation must prove:

- runtime package and frozen source match the digest-pinned manifest;
- rebinding, skew, missing packages, and ambiguous packages fail closed;
- stdio discovery returns exactly 21 tools;
- the six workflow input schemas match the existing canonical protocol;
- workflow writes and rich reads observe the same Todo project UUID, revision,
  and authority fingerprint; and
- no implementation path creates an MCP client or launches an MCP subprocess.

Do not replace the current registration during candidate construction. Final
cutover is a digest-checked atomic swap that records the previous executable,
environment, and registration first. Only `project-control` remains registered
after success; `coding-workflow` is not a concurrent live server. On any failed
health, discovery, schema, or authority check, restore the prior registration
without deleting the candidate.

## Tool discovery and normal use

The Codex profile exposes these six workflow tools:

- `next_task`
- `inspect_task`
- `coordinate_task`
- `delegate_task`
- `collect_delegation`
- `finish_task`

It also exposes fourteen rich reads plus one registered measurement aperture:

- `project_overview`
- `project_delta`
- `project_frontier`
- `inspect`
- `evidence`
- `plan_preview`
- `agent_status`
- `performance_status`
- `performance_probe`
- `architecture_context`
- `coordination_view`
- `source_context`
- `history_trace`
- `impact_preview`
- `program_context`

`terminal_capture` is observer-only and is neither discovered nor invocable in
the Codex profile.

For ordinary work, call `next_task` first, use `inspect_task` for bounded
current-task context, and use `coordinate_task` for typed synchronization.
Delegate only a bounded subordinate child and collect only its returned opaque
handle. Use `finish_task` for every first-class disposition. Escalate to rich
reads only when current-task context is insufficient or the question genuinely
requires source, architecture, history, impact, performance, or cross-project
context.

Profile selection is a trusted startup choice. `clientInfo`, user-agent strings,
model identity claims, annotations, and tool arguments cannot select or broaden
the profile.
