# Project Control

Project Control provides registered-workspace inspection, source navigation,
project metadata, observer jobs and demand-driven local assistance. Workflow
state is owned by the independently installed **Todo Orchestrator 0.2.0**.
Project Control 0.4.0 binds its public API to trusted profiles and real Git,
command and host-observation providers. The MCP surface schema is **11**.

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

## Native Todo workflow

Use the generated MCP `tools/list` response for the authoritative profile
surface and argument schemas. Registered workspace operations use a project
ID and optional repository alias; credentials and filesystem roots are supplied
by the host.

1. Read bounded state with `todo_read_authority` and inspect the relevant work.
2. Review `todo_preview_plan`, then commit that exact preview using
   `todo_commit_plan` from the mutator profile.
3. Open a run if needed, create an isolated workspace and dispatch an attempt
   with the corresponding `todo_*` tools.
4. Execute authorized work, verify declared checks and report its candidate.
   Outcome acceptance and integration remain explicit operations.
5. Inspect uncertainty before recovery; preserve unknown effects and source.

Coder/Codex profiles can execute the granted workflow; plan commitment,
acceptance and administrative recovery belong to the mutator. Observer profiles
cannot mutate Todo. Native child coordination uses host-issued identity and
private bearer delivery. It does not launch an engineering worker. Physical
resource permits require a separately configured trusted adapter; Project
Control retains its existing host-resource and GPU control tools.

Imported task IDs and statuses are available through
`todo_read_legacy_current_tasks`; immutable history and declarations are exposed
through `todo_search_legacy_history` and `todo_read_legacy_project_context`.
Imported completion assertions preserve legacy completion without inventing
native verification receipts. Unsupported contracts and uncertain old effects
remain visible for attention. Legacy Markdown projections are archival.

The previous `next_task`, `inspect_task`, `coordinate_task`, `finish_task` and
legacy Todo administration shapes have been replaced by native operations.
Existing client sessions must reconnect to receive the schema-11 tool manifest.
Project Control's non-Todo read tools and mutator-only `amend_project` remain;
the latter owns project identity, relations, generation/provider metadata,
orientation and skill-use declarations.

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
