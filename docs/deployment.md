# Source deployment and release candidates

The normal local deployment runs Project Control from this checkout. A source
edit takes effect when the relevant process restarts; it does not require
building a wheel or refreshing a receiver manifest. Use a frozen candidate only
when deployment specifically requires one.

## Prepare and inspect the source

From the checkout root:

```sh
scripts/pc-dev setup
git rev-parse HEAD
git status --short
```

Setup is needed for a new checkout or dependency changes. Record the source
revision and working-tree state with the deployment notes so operators can tell
which edits were running. Preserve uncommitted work. The launcher selects code
from this checkout, while optional domain content is configured separately.
For `run` and ordinary `python` commands, `scripts/pc-dev` defaults
`PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR` to
`$HOME/.cache/project-control/as1-observer-analysis` only when the variable is
unset. Set that variable explicitly to use another analysis-state directory;
the launcher preserves the override. Pytest and test-helper modes clear this
variable so source tests remain isolated from operator analysis state.

## Restart the source services

The checked-in user service templates are in `scripts/services/`. Before
installing or editing units, retain the configured profile, principal binding,
workspace list, ports, durable state paths, and any local resource policy. This
repository's source HTTP unit serves on loopback port `8768`; the optional
inference supervisor is a separate, demand-driven service.

The source `codex`/`coder` stdio frontend must not start the shared inquiry
dispatcher: its sanitized environment has no systemd bus. Public inquiry
dispatch is owned by the observer and mutator profiles; coder/codex,
investigator, and skill-assembler profiles do not start it. The local
`assistance ask`/`chat` CLI manages its intended local dispatcher.

The source HTTP and inference units refer to the host runtime directory under
`/tmp` with an optional `ReadWritePaths` entry (`-/tmp/...`). `/tmp` is
ephemeral and may not contain that directory after reboot; systemd must still
allow the service namespace to start in that case. `ProtectSystem=full` leaves
`/tmp` writable, and the Todo runtime creates its directory with restrictive
permissions when it first needs it. Keep this path optional in both source
units so boot and demand-driven inference startup do not depend on stale `/tmp`
contents.

Before editing executable Project Control or Todo Python, stop active
assistance while the current source identity can still release its owned work.
Then edit and run focused source checks before restarting both services
together for live HTTP assistance. At startup, the inference supervisor attests
the Project Control executable package identity; an HTTP-only restart after
package code changed can leave the supervisor bound to older code. Batch source
edits and tests before the restart instead of restarting for every edit. Focused
source tests do not need either service. A newly launched stdio client using
`scripts/pc-dev run codex` loads the current checkout automatically; reconnect
it after changing tool registrations or schemas. Optional external
domain-documentation changes do not invalidate executable identity. For a
source-code change, run this sequence:

```sh
scripts/pc-dev run assistance stop
scripts/pc-dev test -q
# Edit executable source before the paired restart.
systemctl --user restart project-control-inference.service project-control.service
scripts/pc-dev run assistance resume --release
```

For configuration-only changes, restart the affected unit:

```sh
# For HTTP service configuration only:
systemctl --user restart project-control.service
# For inference supervisor configuration only:
systemctl --user restart project-control-inference.service
# After executable PC/Todo Python changes, refresh both together:
systemctl --user restart project-control-inference.service project-control.service
```

`resume --release` clears the explicit stop veto without loading a model.

### Allow source HTTP to read registered Todo state

The HTTP unit runs with `ProtectHome=read-only`. SQLite query-only access can
still need to create or update `-wal`/`-shm` bookkeeping files beside a
canonical Todo database. When HTTP reads fail with a Todo database-open error,
inspect the exact paths first and use the checkout utility to render the
additive user-service permission drop-in. Its default is a dry run over all
registered workspaces; it includes only existing canonical Todo state
directories and does not initialize missing ledgers:

```sh
scripts/pc-dev python scripts/configure_source_state_paths.py --all-projects
```

For one workspace, use its configured workspace ID instead:

```sh
scripts/pc-dev python scripts/configure_source_state_paths.py --project project-control
```

After reviewing the rendered paths, apply either an explicitly selected
workspace or the reviewed all-workspaces set:

```sh
scripts/pc-dev python scripts/configure_source_state_paths.py --all-projects --apply
# Or: scripts/pc-dev python scripts/configure_source_state_paths.py --project project-control --apply
systemctl --user daemon-reload
systemctl --user restart project-control-inference.service project-control.service
```

The utility writes the managed
`~/.config/systemd/user/project-control.service.d/90-todo-state-paths.conf`
drop-in. `ReadWritePaths` is additive to the unit's existing protections and
grants writes only within the selected canonical Todo metadata directories;
it does not grant access to repository source files or make application
queries semantically writable. A replaced drop-in is preserved as a
timestamped `.bak-*` file. To roll back, stop using the added permissions,
restore the exact prior drop-in from that backup (or remove the managed file
if the utility created it), then run `systemctl --user daemon-reload` and
restart the paired services. Keep the backup until the rollback is verified.
Never remove or rewrite a Todo database, WAL, or SHM file to repair this
service permission issue.

The inference supervisor remains demand-driven; refreshing it does not request
model or GPU work. Do not treat the paired process restart as inference/GPU
qualification.

Then check process state and the HTTP endpoints:

```sh
systemctl --user status project-control.service
curl --fail http://127.0.0.1:8768/healthz
curl --fail http://127.0.0.1:8768/readyz
```

Record the checkout and imported runtime identity around a source restart:

```sh
git rev-parse HEAD
git status --short
scripts/pc-dev run doctor --json
systemctl --user show project-control.service -p MainPID -p ExecStart -p ActiveEnterTimestamp
systemctl --user show project-control-inference.service -p MainPID -p ExecStart -p ActiveEnterTimestamp
```

`doctor --json` reports the current checkout's verified bundled Todo package
path and fingerprint. Unit PID/start time and `ExecStart` identify the restarted
process launch; `/readyz` confirms the live HTTP process serves core readiness.
Source mode does not require a wheel build or release-manifest refresh.

`/healthz` reports process liveness. `/readyz` reports core configuration and
the bundled workflow engine; inference and optional content are reported
separately and do not disable core reads. Reconnect the configured MCP client,
list its tools, and make a real read-only workflow request. For rescue mutation,
verify the advertised inputs and exercise authorization against temporary state
before relying on it against live state.

For the finite PA1 server/assistance delivery, record the public information
surface through both transports:

```sh
scripts/pc-dev python scripts/verify_pa1_bootstrap.py \
  --project project-control --output /tmp/pa1-public-surface.json --timeout 60
```

Grounded project and skill answers require separate actual consultation and
source-identity checks. LAB is deferred and does not gate this deployment.
Use `scripts/pc-dev run assistance status`, an explicit `ask` or `chat`, and
`scripts/pc-dev run assistance stop` for demand-driven inference and owner-proven
release. A stop veto can be cleared with `assistance resume --release`, which
does not load a model. `already_stopped_no_owned_resources` combines an inactive
service census with settled durable ownership; it does not fabricate physical
release proof for an empty target set. Unresolved ownership stays pending.

If the source process fails, inspect that unit's status and journal, correct the
source/configuration problem, then restart the affected process. Do not alter
semantic project state as part of service recovery. Preserve the prior service
configuration and previously installed candidate for rollback. Rollback means
restoring the recorded configuration and selecting that known candidate; it
does not mean deleting or reinitializing ledgers, claims, queued work,
snapshots, or project identities. Reconnect stdio clients after changing their
registration; an already-connected client can retain old tool definitions.

## Test before and after a change

Use [the development guide](development.md) to choose a focused source test or
the default smoke. Run `scripts/pc-dev test tests --collect-only` when you need
to check broad import and collection health. That is not a test run. Run
`scripts/pc-dev test tests` only when expanded-suite execution is appropriate.
Neither collection nor a large pass count alone qualifies HTTP MCP behavior,
inference, GPU workloads, CUDA LAB, model quality, recovery, or a frozen
candidate. Match the verification to the change and report the exact selection,
outcome, and untested layers.

## Optional frozen release

Candidate construction is a separate release activity. It records PC checkout
provenance and strictly verifies bundled executable code. Optional domain
content can be supplied independently:

```sh
python scripts/install.py \
  --project-control-root "$PWD" \
  --destination /absolute/path/to/candidate \
  --skills-root /absolute/path/to/optional-content
```

Omit `--skills-root` when no external content snapshot is needed. The installer
builds a candidate; it does not switch a `current` pointer, edit service units,
restart processes, or migrate state. Validate the candidate and preserve the
previous one before performing a separately controlled activation. Source-mode
identity and frozen-release identity have different checks; do not copy release
pins into the source launcher environment.
