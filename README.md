# Project Control

Project Control provides registered-workspace inspection, source navigation,
project metadata, observer jobs, and demand-driven local assistance. Todo state
is owned by the independently installed Todo Orchestrator engine. Project
Control presents the compact agent workflow over that engine; use the active
runtime metadata and MCP manifest for deployed versions and exact schemas.

## Development

Use the exact Todo wheel pinned in `config/todo-orchestrator-runtime.json`:

```sh
scripts/pc-dev setup --todo-wheel /path/to/todo_orchestrator-0.2.0-py3-none-any.whl
scripts/pc-dev run serve observer --host 127.0.0.1 --port 8768
scripts/pc-dev run codex
scripts/pc-dev run mutator
scripts/pc-dev test -q
```

Legacy-contract tests are retained as reference while their request shapes are
retired; they are not part of the native source smoke selection.
The default source checks cover launcher sanitation, independent package
identity, host binding, readiness and native Todo reads. Select focused tests
for changed behavior. Tests do not qualify a live service or GPU workload.
Authored command checks require Linux and Bubblewrap (`bwrap`). They run in
an isolated filesystem/process/network namespace with access to the selected
workspace and trusted toolchain. Missing containment leaves the check unavailable.
The standalone Todo source has its own repository, test suite and wheel; it is
not included in this distribution. Setup verifies the pinned wheel hash and
installed Python-source fingerprint. Source and frozen-release deployments
retain separate identities.

## Todo workflow

Project Control keeps its compact, agent-facing workflow: call
`next_task(project, repository?, task_id?, run_id?)` to prepare or resume work,
then use its returned handle with `inspect_task`, `coordinate_task`, and
`finish_task`. Those tools supply bounded context, typed coordination, and
complete, hand off, block, or release work. The calls hide native authority
IDs, leases, credentials, and retry bookkeeping from ordinary agents. The live
MCP manifest is authoritative for exact argument schemas.

The observer retains its existing 11 read-only tools. The current compact
profile catalogs are observer 11, coder 12, and mutator 16 tools. Use its
established `overview`, `search`, `delta`, `frontier`, `history`, `evidence`,
and `impact` entry points for workspace context and migrated Todo status/history.
An observer does not need a task claim and cannot mutate workflow state.

Privileged profiles retain `plan`, `maintain_execution`, and `amend_project`
for reviewed plan changes, operational recovery, and project metadata. Native
Todo capabilities belong behind these established routes or the four workflow
calls; they should be implemented fully where available, not represented by
placeholder actions. The standalone Todo package still exposes its granular
public API and CLI to clients operating a separately managed authority.

The host owns authorization, workspace selection, identifiers, and provider
bindings. Imported completion assertions preserve their legacy provenance and
do not invent native verification. Unknown prior effects remain visible for
attention and are never replayed automatically. Project Control's existing
source, observer, and host-resource tools retain their own purposes.

After a deployment changes the MCP manifest, reconnect the client to load the
active tool list. Source documentation describes the intended compact contract;
use `tools/list` and the current readiness endpoint to verify what a running
service actually exposes.

## Service operation

Before changing executable source, stop active assistance while its original
identity can still release owned work. Keep a source and authority backup.
After installing the paired runtime and updating narrow Todo state grants,
restart the inference supervisor and HTTP service together. Restore an explicit
stop veto only after checking the selected runtime:

```sh
scripts/pc-dev run assistance stop
# Install and qualify the paired source/artifacts, then update state paths.
scripts/pc-dev python scripts/configure_source_state_paths.py --all-projects --apply
systemctl --user daemon-reload
systemctl --user restart project-control-inference.service project-control.service
scripts/pc-dev run assistance resume --release
```

`resume --release` does not load a model. Inference remains demand-driven.
`/healthz`, `/readyz` and `/version` distinguish core readiness, optional
inference/domain availability and the active package/tool schema.

## Preservation and cutover

`legacy/todo_orchestrator-reference/` retains the original engine as source
reference and is excluded from imports and builds. Original authorities and
worktrees are preserved. `scripts/migrate_todo_authorities.py` imports frozen
sanitized snapshots into fresh authorities; activation requires verified backup
and source bindings, a stopped-writer gate, and explicit apply. It supports a
pilot followed by the remaining authorities without replacing new pilot writes.
Host credentials, authority databases and migration receipts remain private.

After native writes exist, rollback must preserve them before restoring an old
runtime; never overwrite or downgrade an authority or replay unknown effects.
The standalone repository's `planning/cutover-v1/` and
`docs/operations/cutover-operator-notes.md` record the operator scope and actual
deployment evidence. Older documents under `docs/` retain historical design and
qualification context; use this README and the live manifest for current Todo
setup and tool names.
