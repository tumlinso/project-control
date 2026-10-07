# Project Control assistance qualification

This is the current checkout-first guide for testing the assistance work. It
supersedes the executable recipes in the R10-era
[testing handoff](testing-handoff.md),
[diagnostics](testing-diagnostics.md), and
[live qualification plan](live-qualification-plan.md). Those files retain their
candidate-specific results and original acceptance rationale; they do not
assert current Todo status, interface shape, or run authorization.

Use [repository instructions](../../AGENTS.md), the current Todo task and
prerequisites when available, and the actual configured MCP interface. The
current profile's tool names and schemas come from a live `tools/list` response;
do not reconstruct them from historical counts. Preserve the existing ledgers,
project identities, claims, snapshots, worktrees, fixtures, and unknown edits.

## Fast source loop

Make a small change. For a defect, reproduce it with a focused test or minimal
fixture, add a regression assertion, run the impacted tests, and expand to
nearby shared boundaries in proportion to risk. Source tests do not require a
running Project Control service, task assignment, inference, or GPU.

```sh
scripts/pc-dev setup
scripts/pc-dev test -q
scripts/pc-dev test tests/assistance/test_broker_close_persistence.py \
  tests/assistance/test_execution_cleanup_recovery.py \
  tests/assistance/test_lab_snapshot.py
scripts/pc-dev test tests --collect-only -q
```

Run setup for a new checkout or dependency change. The first test command runs
the small smoke selection. The second exercises three focused assistance
regressions. The third checks broad imports and test discovery only; it runs no
assertions. `scripts/pc-dev test tests` is an expanded suite that may include
legacy host-dependent tests and is not guaranteed to be hermetic. Report the
selected behavior and actual result; a count is not acceptance evidence.

The source launcher imports Project Control and bundled Todo from this
checkout, removes inherited release/interpreter pins, and derives source
identity from current files. A code edit needs a new process; it does not need a
wheel build, frozen candidate, or receiver-manifest refresh. Optional domain
content is independent of executable identity.

## Inert qualification commands

The qualification helpers support explicit `source` and `release` identity.
Release is the default and retains strict digest-pinned manifest, package,
interpreter, and receiver checks. Source mode uses this checkout's
`scripts/pc-dev run` command, verifies imports from `src/`, captures Git/package/receiver
provenance, and clears inherited release pins. These commands are dry-run only:

```sh
scripts/pc-dev python scripts/qualify_assistance_installed.py \
  --runtime source --dry-run \
  --project <registered-project-id> \
  --question "<bounded question grounded in a registered source>" \
  --skill <registered-content-id> \
  --skill-question "<bounded question about that content>"

scripts/pc-dev python scripts/qualify_assistance_scoped_lab.py \
  --runtime source \
  --project <registered-project-id> \
  --source <registered-relative-source-path> \
  --goal "<bounded CPU experiment goal>"
```

The first command validates source identity and prints provenance without
starting a service or making an assistance request. The second prints the LAB
plan; dry-run is its default. Inspect the printed `runtime_provenance` and
confirm both package paths resolve under this checkout before proceeding.
Neither command is live model, source-access, or LAB-effect evidence.

To inspect the current helper interface, use `--help` through the same source
launcher. Do not omit `--runtime source` when the purpose is checkout
qualification: release mode deliberately remains strict and may import or
require a separate candidate.

## Live evidence boundaries

Live qualification is a separate, deliberately selected step. The helper's
`--execute-live` flag is required and does not itself authorize a real effect.
Use it only when the active task and governing operator authorization permit
the specific case, its fixtures and service configuration are verified, and a
new private artifact directory outside the checkout has been chosen. Preserve
the intent, command, provenance, raw receipts, failure, cancellation, and
cleanup evidence. Never reuse an artifact directory or replay an ambiguous
effect.

The installed-assistance helper records source/release identity before the
case and rebinds it at completion. If PC, Todo, or receiver identity changes
during the case, it returns `runtime_changed` and fails qualification. Do not
edit code during a live run; retain the failed receipt and investigate it as a
consistency failure.

| Evidence | Minimum evidence to report | Does not establish |
| --- | --- | --- |
| Source test / fixture | Exact selector, source identity, assertion result | MCP operation or model quality |
| Dry-run provenance | Runtime mode, PC/Todo paths and fingerprints, receiver identity, planned command | Service call, model request, or mutation |
| Live MCP read / grounded inquiry | Actual endpoint/profile, `tools/list`, request/result receipt, cited registered source, process/runtime identity | LAB effect cleanup, CUDA handoff, or broad model quality |
| CPU LAB | Preview/authorization/run/status receipts, bounded experiment and interpretation, effect and cleanup outcome | CUDA resource ownership or other lifecycle cases |
| CUDA LAB | Admission, explicit controller owner/release receipts, experiment result, rewarm and interpretation evidence | Unexercised recovery/cancellation cases or general model quality |
| Cancellation/recovery | Scope, process and effect identities; terminal state, effect fence and cleanup/reconciliation evidence | Other scenarios or a mere graceful restart's crash recovery |
| Frozen release | Candidate manifest/provenance and release-mode verification | Later source edits or live scenarios not actually exercised |

Before live assistance use after executable Project Control or Todo Python
changes, first finish focused source checks and then refresh both source service
processes together:

```sh
systemctl --user restart project-control-inference.service project-control.service
```

The inference supervisor startup-attests the PC executable package, so
restarting HTTP alone after package code changed can leave the supervisor bound
to its prior code. Do this once after batching source work, not before every
focused test or edit. Documentation-only changes to optional external domain
content do not invalidate executable identity. The paired restart does not
request model or GPU work; inference remains demand-driven.

Check `/healthz` for process liveness and `/readyz` for core configuration and
the bundled workflow engine. Inspect optional inference and domain-content
status separately. A source test or healthy HTTP process is not proof that a
model, GPU, or scoped experiment works. Use [deployment](../deployment.md) for
service checks and the rollback boundary.

## Acceptance and historical records

Reconcile the current epic's remaining obligations with the cleaned
architecture before selecting cases. Record each applicable requirement as
`passed`, `failed`, `blocked`, or `not_run`, with the exact command, source or
release identity, evidence reference, and limits. Preserve old A01–A40 results
and candidate receipts; they are historical evidence to review, not automatic
current statuses or a mandate to reproduce retired routes. Do not reintroduce
`wide`, retired adapter/provider paths, or the old external Todo supplier to
match those recipes.

Collect independent failures in one bounded pass when useful. Fix flaky or
hanging fixtures directly, and report an environment prerequisite as a scoped
skip/xfail only when that is the honest test contract. Do not turn a regression
into a green result by hiding it or weakening its assertion. Do not claim
comprehensive acceptance from a collection count, smoke, source-only fixture,
or the mere existence of a helper.
