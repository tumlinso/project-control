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
- stdio discovery returns exactly 20 tools;
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

It also exposes the fourteen rich reads:

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

## Routing bounded research

Use the role-neutral routing and digest contract in
[`workflow_foundation_v2/agents/research-routing.md`](workflow_foundation_v2/agents/research-routing.md).
Choose context depth and difficulty independently; neither chooses a provider,
grants authority, or creates a workflow lane. For one independent deep question,
start with one bounded read-only researcher and use its compact digest rather
than repeating exploration in the controller. A spawned helper remains a
subordinate child, never a first-class lane.

## Researcher profile qualification

The Codex client observed during the D02 qualification was `codex-cli 0.153.4`.
The [current official subagent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents)
describes per-agent `model`, `model_reasoning_effort`, `sandbox_mode`, and MCP
configuration, and also warns that parent-turn sandbox and approval overrides
are reapplied to spawned children. Recheck both the installed version and the
effective child session before changing a profile; this observation is not a
permanent compatibility guarantee.

For research, use an independently enforced observer-only MCP configuration.
Allow only the needed read tools and exclude `terminal_capture` plus inherited
workflow mutation tools. Record requested model/effort separately from actual
child model/effort, tools, sandbox, and approval state. A role file or
"read-only" instruction does not enforce permissions. If the configured
inexpensive model or observer path is unavailable, return an explicit failed
dispatch; do not silently use a costlier model or the parent's broader tools.
