# Codex setup

Project Control's coder profile (`codex` compatibility identity) is a stdio MCP server registered under the name
`project-control`. It is separate from the loopback observer endpoint and does
not use the ChatGPT tunnel.

## Unified runtime entrypoints

Install the stable launcher from
[`runtime-unification/entrypoints/project-control`](runtime-unification/entrypoints/project-control)
at `~/.local/bin/project-control`; source
[`runtime-unification/entrypoints/shell-env.sh`](runtime-unification/entrypoints/shell-env.sh)
from the interactive shell profile. The launcher routes HTTP and stdio commands
through `~/.local/share/project-control/current/bin/project-control-release`
and publishes that release's interpreter as
`PROJECT_CONTROL_RUNTIME_PYTHON`. It removes ambient Python paths and legacy
runtime identity settings before forwarding. The release launcher owns the
manifest, digest, and frozen Skills binding; do not set a live Skills checkout
as a runtime override.

The same launcher serves both supported surfaces:

```sh
project-control serve observer --host 127.0.0.1 --port 8768
project-control serve codex
```

Codex MCP registration uses `~/.local/bin/project-control` with
`serve codex` arguments. There is no HTTP-to-stdio bridge. The installed user
service uses the same launcher for the loopback HTTP observer. Its durable
analysis state remains at `~/.cache/project-control/as1-observer-analysis`,
and `TODO_BACKGROUND_HOST_RUNTIME_DIR` remains
`/tmp/codex-todo-orchestrator-1000`. The existing observer GPU UUID allowlist
is passed as configuration only; no background or enabled inference service
starts as part of the unified observer runtime. The old frozen supervisor hash
is not carried into the new release.

The separate `project-control-inference` entrypoint resolves `current` once,
binds the selected release manifest and frozen Skills snapshot, then invokes
`local_worker.supervisor` through Project Control's verified runtime binding.
Its systemd unit is optional and remains disabled. Starting that unit only
starts the supervisor; a model server is created only after an explicit worker
request.

After an authorized service cutover, check `/healthz` and `/version` on
`http://127.0.0.1:8768`. `/readyz` also requires the optional central inference
supervisor, so it may return `503` while that demand-only capability is offline.
Do not treat that response as a failed HTTP observer startup.

Candidate construction still uses the isolated installer to build both local
distributions (`project-control` and the canonical `todo-orchestrator` from
Skills), the `runtime-skills/` snapshot, and `release-manifest.json`. Project
Control and Skills remain standalone source repositories paired by an explicit
release manifest. The stable `current` runtime is advanced only after candidate
validation and preservation of the previous release for rollback.

`project-control doctor --json` reports whether the configured runtime was
verified and, when it was not, the failing layer and a supported configuration,
installation, or restart action. It never emits ambient environment values or
launches a process. Its local-worker entry describes Project Control's
observation adapter only; it does not claim the availability of a Todo-managed
executor.

## Bounded maintenance hosts

AS1 `maintain_execution` belongs to the separately selected **mutator** profile,
not coder/codex. It exposes diagnose/prepare/execute through the existing native
principal-bound grant mechanism. Trusted startup binds the principal; callers
cannot supply a role, recipient, repository root or approval flag to broaden it.
See [control ports](as1-control.md) and [the current surface](as1-surface.md).
Owner CLI maintenance compatibility remains separate from model permissions;
[maintenance continuation](MAINTENANCE.md) preserves the historical operator path.

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
- coder/codex discovery returns exactly 12 tools, mutator exactly 16;
- the four workflow schemas, including the reviewed `bind_required_gates`
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

Coder/codex exposes four workflow tools: `next_task`, `inspect_task`,
`coordinate_task`, and `finish_task`, plus eight shared information tools:
`overview`, `delta`, `frontier`, `search`, `evidence`, `impact`, `history`, `machine`.
Compact is the default; local profiles can request standard but not extended.
Use native files, shell, `find`, `rg`, Git and skills. Observer-only `read` and
`skill` are not local MCP replacements. No profile injects overview at startup.

`search(query={"kind": "task", "target": "T1"}, project="demo")` performs a
direct canonical exact lookup; discovery queries retain their existing behavior.
There is no public Project Control `find`. Removed legacy names are not aliases
in ordinary discovery or dispatch. See [routing examples](as1-surface.md).

`delegate_task` and `collect_delegation` preserve implementation/history but are
absent from discovery and rejected at dispatch as `temporarily_inactive`.
Feature metadata specifies explicit operator reenable; no timer reactivates them.
Use configured Codex subagents for scoped coding/research assignments under the
owning root's active task claim.

For substantial work, call `next_task` first. When its context is ready,
proceed; use `inspect_task` only for missing needed current-task context, and
use `coordinate_task` for typed synchronization. Use
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
