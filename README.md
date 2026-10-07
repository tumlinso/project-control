# project-control

## Development quickstart

Prepare the repository-local Python environment once, then run this checkout:

```sh
scripts/pc-dev setup
scripts/pc-dev run serve observer --host 127.0.0.1 --port 8768
scripts/pc-dev run serve mutator
scripts/pc-dev test
scripts/pc-dev test tests/test_workflow_binding.py -q
systemctl --user restart project-control-inference.service project-control.service
```

The default smoke covers launcher sanitation, runtime identity, workflow
binding, readiness, and one focused mutator case using bundled Todo source. It
does not start the Project Control service or inference/model processes.
Foreground servers pick up source edits when rerun. This host's
`project-control-inference.service` and `project-control.service` use the
checkout. After a batch of executable Project Control or Todo Python changes
and focused source checks, restart both services together before live
assistance use: the supervisor verifies the Project Control package when it
starts. Focused source tests need no service restart, and external domain
documentation changes do not change executable identity. The inference
supervisor remains demand-driven. Installed-release deployments keep using
their selected release until switched. See [development notes](docs/development.md)
for setup, test selection, and evidence boundaries.

`project-control` reads registered engineering workspaces and coordinates
authorized workflow operations through startup-bound MCP profiles. The current
surface design is summarized in
[the adaptive surface guide](docs/as1-surface.md). After a server or
registration change, inspect the actual generated MCP `tools/list` response for
the authoritative tool names and schemas. Inference is demand-driven and does
not load a model at startup.

Todo Orchestrator remains the sole transactional workflow kernel and SQLite
semantic authority. Project Control verifies and imports that canonical runtime
in-process; it neither calls another MCP server nor copies scheduling, claim,
capability, transaction, completion, or recovery logic.

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
Codex source setup and usage are in `docs/CODEX_SETUP.md`;
repository guidance migration is in `docs/MIGRATION.md`.
For coarse outcome packages, guarded conditional choices, compact evidence, and
root/head-authorized low-ceremony lifecycle actions, see
`docs/ADAPTIVE_EPICS.md`.
The optional, claimless local-analysis stop-line and deterministic fallback are
documented in `docs/LOCAL_ANALYSIS.md`.

## Codex usage policy

For assigned or registered workflow execution, use the bounded workflow
protocol:

1. `next_task` acquires or resumes the current first-class lane task.
2. `inspect_task` retrieves bounded current-task context.
3. `coordinate_task` handles typed synchronization, gates, interfaces,
   rendezvous, integration requests, and authorized non-authoritative
   `publish_context` findings.
4. `finish_task` records disposition and runs required gates.

Delegated work uses configured Codex subagents under the root's active task
claim; delegation is not exposed through Project Control workflow tools.
Shared information tools supply additional context when needed.

Direct user-authorized Project Control source fixes, rescue work, and local
tests can run from the checkout without a live service or task assignment. Do
not acquire or invent a task just to run source tests. Semantic workflow
mutations still go through Todo's transaction and authorization guards. See
[repository instructions](AGENTS.md) for the full development and delegation
policy.

## Source checks

Use `scripts/pc-dev test` for the checked-out Project Control and bundled Todo
sources. `scripts/dev_tests.sh` and `scripts/pa1_source_tests.sh` remain thin
compatibility wrappers. See [development notes](docs/development.md).

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

Python 3.11 or newer and `uv` are required. Use `scripts/pc-dev setup` once,
then use `scripts/pc-dev run ...` and `scripts/pc-dev test ...` for source
commands. The environment is reused; tests do not trigger a package rebuild or
service restart. See [deployment](docs/deployment.md) for service restart,
readiness, and rollback boundaries.

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

The source service provides `/healthz`, `/readyz`, `/version`, and the loopback
MCP URL `http://127.0.0.1:8768/mcp`. See `docs/SECURITY.md` for the enforced capability
boundary and `docs/TOOL_CONTRACTS.md` for the current surface and clearly labeled historical backend contracts.

## Runtime maintenance

Project Control and bundled Todo executable code are verified independently
from optional CUDA/C++ and skill documentation. Frozen candidates retain strict
packaged-code verification. Optional domain content can be configured
separately; it is not required for core startup or workflow reads. See
[source deployment](docs/deployment.md) for restart and candidate boundaries.

Workflow completion records producer and authority commits separately. A declared
artifact must exist in the dispatch workspace. Plans reject interface ownership
assigned to roles unable to publish. Explicit task selection and the existing
one-active-lane-per-session guard prevent an accidental cross-lane resume.
GPU command gates can declare their CUDA controller contract so completion reruns
acquire the same resources as explicit validation.
