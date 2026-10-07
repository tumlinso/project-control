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

After a batch of executable Project Control or Todo Python changes, run the
focused source checks and then restart both services together before live
assistance use. At startup, the inference supervisor attests the Project
Control executable package identity; an HTTP-only restart after package code
changed can leave the supervisor bound to older code. Batch source edits and
tests before the restart instead of restarting for every edit. Focused source
tests do not need either service. Optional external domain-documentation
changes do not invalidate executable identity. For a change confined to one
unit's configuration, restart that unit:

```sh
# For HTTP service configuration only:
systemctl --user restart project-control.service
# For inference supervisor configuration only:
systemctl --user restart project-control-inference.service
# After executable PC/Todo Python changes, refresh both together:
systemctl --user restart project-control-inference.service project-control.service
```

The inference supervisor remains demand-driven; refreshing it does not request
model or GPU work. Do not treat the paired process restart as inference/GPU
qualification.

Then check process state and the HTTP endpoints:

```sh
systemctl --user status project-control.service
curl --fail http://127.0.0.1:8768/healthz
curl --fail http://127.0.0.1:8768/readyz
```

`/healthz` reports process liveness. `/readyz` reports core configuration and
the bundled workflow engine; inference and optional content are reported
separately and do not disable core reads. Reconnect the configured MCP client,
list its tools, and make a real read-only workflow request. For rescue mutation,
verify the advertised inputs and exercise authorization against temporary state
before relying on it against live state.

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
