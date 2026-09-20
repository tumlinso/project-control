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

`project-control doctor --json` reports whether the configured runtime was
verified and, when it was not, the failing layer and a supported configuration,
installation, or restart action. It never emits ambient environment values or
launches a process. Its local-worker entry describes Project Control's
observation adapter only; it does not claim the availability of a Todo-managed
executor.

## Bounded maintenance hosts

The Codex profile exposes `maintain_execution`, but an ordinary unconfigured
server cannot execute it. Trusted host startup must bind a distinct operator
principal:

```python
from project_control.app import create_mcp
from project_control.workflow_tools import trusted_maintenance_context

server = create_mcp(
    profile="codex",
    maintenance_host=trusted_maintenance_context("operator-a"),
)
```

An owner prepares a mandate and the exact stdio operator command with:

```bash
project-control admin prepare-maintenance --repo /path/to/repo --task TASK-ID --recipient operator-a
```

The JSON response contains the principal-bound opaque grant and a fixed
same-runtime `operator_launch` command. Start that command as the trusted host
and have its MCP client make the recorded public `maintain_execution` call.
The server never chooses work or invokes the grant by itself; after it returns
the receipt, a separate implementer uses the returned exact `next_task`
recommendation for the same task. The operator supplies the repository and
opaque grant reference, not a role or principal. Observer and mutator profiles
do not expose this operation. This tool boundary does not provide OS isolation
from arbitrary same-user Python.

For an active task, `coordinate_task(action="bind_required_gates", payload={
"gates": [...]})` adds required gates without replacing the plan. Existing gate
definitions and evidence are preserved; an identical serial binding is a
no-op. Gate paths must be within the task's declared readable or owned scope.
Completion performs required validation, so an extra `run_gates` call is only
needed when earlier validation is useful. File-check evidence can be reused
when its observed inputs are unchanged; command and managed-workspace gates
still require fresh execution.

Before registration, candidate validation must prove:

- runtime package and frozen source match the digest-pinned manifest;
- rebinding, skew, missing packages, and ambiguous packages fail closed;
- stdio discovery returns exactly 22 tools;
- the six workflow schemas, including the reviewed `bind_required_gates`
  coordination action, match the current canonical protocol;
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

It separately exposes `maintain_execution`, a startup-bound maintenance tool.
It remains unavailable until the Codex server receives a trusted maintenance
host context; it is not an ordinary workflow claim operation.

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

For substantial work, call `next_task` first. When its context is ready,
proceed; use `inspect_task` only for missing needed current-task context, and
use `coordinate_task` for typed synchronization. Delegate only a bounded
subordinate child and collect only its returned opaque handle. Use
`finish_task` for every first-class disposition: it runs required gates, so
use `run_gates` separately only when earlier validation is useful. Read-only
questions and research may use rich reads directly without a task or claim.
When more than one ready run needs attention, pass its `run_id` to `next_task`
to choose the run focus; omit it for the normal workflow choice.

If `next_task` returns `status="needs_context"`, its committed workflow handle
remains valid. Follow `context_receipt.next_call` exactly: it is an
`inspect_task(kind="context_fragment")` call with the handle, fragment target,
and budget already supplied. A small fragment keeps the ordinary expanded
`content` response. A larger one returns readable `page.content` canonical JSON
text with `page.next_target`; repeat the same call with that target until it is
`null`. The cursor is bound to the fragment revision and content hash. A
`coordinate_task(action="sync")` payload may include `cursor` and
`known_fragments` to receive the actual message cursor and changed fragment
references without re-sending the whole context capsule.

`coordinate_task(action="run_gates")` accepts an optional
`payload.effect`: use `"validate"` for validation or
`"integrate_and_validate"` for the lane-owned integration path followed by
validation. The action policy returned with the workflow handle remains the
authority for whether that action is available.

`delegate_task` accepts `mode="readonly"` or `mode="writable"` and optional
repository-relative `source_targets`. A writable child requires explicit,
existing, narrower source targets within the parent’s authorized scope.
Readonly delegation may use the parent’s readable scope when targets are
omitted. A parent that creates a child is responsible for its disposition:
call `coordinate_task(action="accept_child", payload={"child_execution_id":
...})` for an accepted result, or `reject_child` with its required `reason`.
Only the owning parent claim can make that decision.

The main thread owns reasoning, synthesis, strategy, architecture, scope
changes, consequential tradeoffs, and final acceptance. Subagents gather
evidence or execute tightly scoped assignments, then report findings and
blockers at meaningful checkpoints. The main thread resolves uncertainty and
tradeoffs, provides direction, and subagents wait before consequential changes.
Delegate archaeology or research to cheaper subagents when appropriate, and
request richer projections deliberately rather than routinely.

Profile selection is a trusted startup choice. `clientInfo`, user-agent strings,
model identity claims, annotations, and tool arguments cannot select or broaden
the profile.
