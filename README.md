# project-control

`project-control` observes and coordinates registered engineering workspaces
through startup-bound MCP profiles over one implementation. The adaptive AS1
surface exposes eight shared tools: `overview`, `delta`, `frontier`, `search`,
`evidence`, `impact`, `history`, and `machine`.

- **observer**: 11 tools over loopback HTTP; adds `read`, `investigate`, `skill`.
- **coder** (`codex` compatibility identity): 12 tools over stdio; adds
  `next_task`, `inspect_task`, `coordinate_task`, `finish_task`.
- **mutator**: 16 tools over local stdio; adds `investigate`, the four workflow
  tools, `plan`, `amend_project`, `maintain_execution`.
- **investigator** and **skill assembler** are internal read-only modes with
  the shared eight plus `command` and `log` (10 tools each).

Compact is the default. Only observer supports extended detail. No profile
receives automatic overview or loads a model at startup. Local profiles retain
native files, shell, Git and skills. See [the adaptive surface guide](docs/as1-surface.md)
for routing, exact typed search, packets, durable jobs, examples, and release scope.

Todo Orchestrator remains the sole transactional workflow kernel and SQLite
semantic authority. Project Control verifies and imports that canonical runtime
in-process; it neither calls another MCP server nor copies scheduling, claim,
capability, transaction, completion, or recovery logic. The old
`coding-workflow` name is a temporary forwarding compatibility alias, not a
second product, backend, or live registration.

For a completed run whose `contract_split` branches were merged into `main`,
the owner can preview the missing workspace integration receipt with:

```bash
project-control admin record-contract-split-integration --repo /path/to/repo \
  --workspace WORKSPACE_ID --integration-task FINAL_INTEGRATION_TASK \
  --accepted-commit EXACT_MAIN_COMMIT --reason "Final accepted integration"
```

Apply the reviewed receipt with `--apply --confirm RECORD-CONTRACT-SPLIT-INTEGRATION`.
Todo requires terminal tasks and lanes, inactive ownership, clean material
source, producer ancestry in `main`, and current executable integration-gate
evidence. Material source must have been committed before those gates ran.
The command records integration only; the existing completed-run
`mark-run-workspaces-cleanup-eligible` operation remains a separate prerequisite
to removing worktrees. Neither operation merges or deletes Git work.

Observer `investigate` and `skill` use one durable read-only job broker.
Project Control owns access, persistence and verified source excerpts; the local
worker navigates installed `SKILL.md`, authored maps and prerequisites agentically.
`search` retains discovery and accepts exact typed canonical IDs through a direct
deterministic lookup. There is no public Project Control `find`; local filesystem
discovery uses native `find`, `rg`, and Git.

Local setup and connection instructions are in `docs/CHATGPT_SETUP.md`.
Codex setup, compatibility, and cheap-first usage are in `docs/CODEX_SETUP.md`;
repository guidance migration is in `docs/MIGRATION.md`.
For coarse outcome packages, guarded conditional choices, compact evidence, and
root/head-authorized low-ceremony lifecycle actions, see
`docs/ADAPTIVE_EPICS.md`.
The optional, claimless local-analysis stop-line and deterministic fallback are
documented in `docs/LOCAL_ANALYSIS.md`.

## Codex usage policy

Normal Codex work starts with the bounded workflow protocol:

1. `next_task` acquires or resumes the current first-class lane task.
2. `inspect_task` retrieves bounded current-task context.
3. `coordinate_task` handles typed synchronization, gates, interfaces,
   rendezvous, integration requests, and authorized non-authoritative
   `publish_context` findings.
4. `finish_task` records disposition and runs required gates.

`delegate_task` and `collect_delegation` retain implementation/history but are
absent from discovery and rejected at dispatch as `temporarily_inactive`.
Reenable requires an explicit operator decision; no timer reactivates them.
Use configured Codex subagents for bounded assignments under the root's claim.
The eight shared information tools supply additional context when needed.

## Bulk plan ingestion

Project Control accepts either a versioned `project-control-preledger` directory
or a compatibility directory containing `proposed_todos.json`. Compilation
selects one repository authority and produces deterministic native Todo plan
schema v2 JSON. Validation and application use Todo Orchestrator as the sole
transaction authority:

```bash
project-control plan compile \
  --package /path/to/preledger \
  --repository-label Cellerator \
  --output /tmp/cellerator.plan.json
project-control plan validate --project cellerator --file /tmp/cellerator.plan.json
project-control plan apply --project cellerator --file /tmp/cellerator.plan.json
```

The local apply command observes current state, constructs an inert
`ProposalEnvelope`, and calls the same stale-fail-closed mutation service used
by the mutator MCP profile. Cross-authority dependencies are reported and never
translated into local Todo dependencies.

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

For a deliberately small inspection façade, the local operator may register
exact live symlinks with repeated
`--live-link REPOSITORY_PATH=ABSOLUTE_TARGET` arguments. Each read verifies that
the repository entry is still a symlink to that exact configured target.
Unconfigured escaping symlinks remain denied, and the normal deny patterns,
text limits, and output redaction still apply.

Schema v2 can add query-only program groups without changing workspace authority:

```toml
[programs.biological-stack]
display_name = "Biological computation stack"
workspaces = ["baseplane", "cellerator", "cellshard", "glasshelix"]
```

`project-control doctor --json` is the local-only provider diagnostic. It may
show selected executable and filesystem paths; ordinary MCP output replaces
private locations with stable IDs and bounded error classifications.

Workflow data is additive output on the existing tools. Operational state comes
only from `todo semantic workflow`; the official durable export enriches records
anchored by that read and is never independently interpreted as worker activity. It includes the
active run, first-class Codex lane tree and serial queues, authoritative
dispatches, typed blockers, rendezvous, managed workspaces, pending patches,
integration conflicts, context cursors, safe parallel groups, and recovery
attention. `frontier` keeps first-class Codex/project agents separate from
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

Relation searches capture matching filenames before reading bounded text files,
so repeated matches in generated evidence cannot exhaust the command output
budget. Matches retain source line numbers and stop at the requested result
limit; denied, binary, non-UTF-8, and oversized files are excluded under the
source read policy.

Configuration schema v2 optionally defines query-only programs. Membership does
not imply dependency, ownership, or architectural authority, and cross-project
observations report per-project cursors and skew rather than claiming one global
transaction. Schema v1 remains readable and is never rewritten automatically.

The service provides `/healthz`, `/readyz`, `/version`, and the loopback MCP URL
`http://127.0.0.1:8767/mcp`. See `docs/SECURITY.md` for the enforced capability
boundary and `docs/TOOL_CONTRACTS.md` for the current surface and clearly labeled historical backend contracts.

## Runtime maintenance

Project Control and Skills remain standalone repositories, bound by an explicit
paired release manifest with distinct authority identities and exact commits.
Skills contains no Project Control source copy or submodule.

Deployment candidates freeze the required Skills runtime alongside the two
installed distributions. The release manifest and its configured SHA-256 bind
that snapshot; mutable development checkouts are not live release dependencies.
Installed package, frozen Python tools, and manifest mutation still fail closed.
See [candidate setup](docs/CODEX_SETUP.md) before promotion. Preserve the previous
candidate and launcher for rollback; switch the common launcher, restart HTTP,
and establish a fresh stdio connection before declaring the cutover complete.

Workflow completion records producer and authority commits separately. A declared
artifact must exist in the dispatch workspace. Plans reject interface ownership
assigned to roles unable to publish. Explicit task selection and the existing
one-active-lane-per-session guard prevent an accidental cross-lane resume.
GPU command gates can declare their CUDA controller contract so completion reruns
acquire the same resources as explicit validation.
