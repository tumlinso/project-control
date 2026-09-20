# Bounded PCE2 candidate

This candidate qualifies two specific public journeys. It does not complete
all six PCE2 outcomes or all 24 acceptance groups. It is activated as the
shared Project Control release; NF1A remains paused.

## Exact candidate

- Project Control source: `f4160ce4e274bbfb82c71d7db8330cf9db3002ac`.
- Skills source: `9cf3c019d98568fb5e355e972a488cf623f354f7`.
- Candidate: `/home/tumlinson/.local/share/project-control/candidates/pce2-bounded-f4160ce-session-root`.
- Candidate identity digest: `0a15f5e6b41af1d738ea1fcde0c021b99c63702daefd59f6630ff82f982b2025`.
- Runtime binding: that candidate's `release-manifest.json`, its SHA-256,
  frozen `runtime-skills`, and `bin/python`.

## Activation

The candidate is activated through the digest-checked atomic shared launcher
replacement at `/home/tumlinson/.local/state/project-control/activations/pce2-20260920T154146Z`.
Its activation-manifest digest is
`aec624ff1be0c64155d6716e811d99c46bcef7679b1b85dd2f7bcbae3d2f226e`;
this is activation evidence, distinct from the candidate identity digest above.

The preceding launcher was retained for rollback. The effective user service
unit was preserved, `systemctl --user restart project-control.service`
succeeded, and PID `105531` resolves to the candidate manifest. `/healthz`,
`/readyz`, and `/version` returned HTTP 200; readiness reported 10 workspaces.
A fresh stdio client through `/home/tumlinson/.local/bin/project-control codex`
discovered 22 tools, the `bind_required_gates` schema, no `terminal_capture`,
and the intended unconfigured-host response from `maintain_execution`.
Existing connected Codex MCP clients require reconnect; the active stdio
process cannot be hot-switched. Maintenance remains unavailable until trusted
host startup supplies an operator context.

The two earlier inactive build directories are superseded and are not the
qualified candidate above. The candidate replaced the shared launcher only
after the recorded checks; the prior launcher remains available for rollback.

## Executed public journeys

The candidate Python ran `tests.test_pce2_workflow_journey` and
`tests.test_maintenance_journey` together: **2 tests passed in 4.219 seconds**.
Qualification used private temporary XDG state/cache directories and retained
the candidate's manifest binding in the subprocesses.

1. Ordinary entry supplies its work packet without recommending a mandatory
   inspection. The caller adds required gates through `coordinate_task`,
   repeats the same binding without advancing the revision, validates, and
   completes through public MCP tools.
2. A trusted host prepares a scoped maintenance assignment. An unconfigured
   host and a differently bound operator are rejected. The intended operator
   calls public `maintain_execution`, then public `next_task` claims the same
   repaired task. No post-recovery lane edits or private completion helper are
   used. Assignment reports `launch_required`; no model launcher is invented.

The host issuer is intentionally outside the model-facing tool surface. The
test operator runs in a fresh process; this is not evidence that a paid model
or an installed external agent launcher was exercised. Host issuance and the
operator MCP servers are constructed within that fresh subprocess; transfer
between independently launched host and operator processes remains unqualified.

## Supporting changes and evidence

- Runtime diagnostics and bound read-only plan previews are committed in
  `88483dc`; the final changed preview paths passed 19 focused tests plus the
  7-test real preview module.
- Ordinary action projection and context continuation are committed in
  `497a878`; the reviewed protocol corrections passed 18 protocol tests,
  following the broader 50-test entry/context run.
- Live required-gate binding and conservative file-check evidence reuse are
  committed in `f423882`; 38 focused gate/audit/protocol tests passed.
- Maintenance and explicit startup principal binding are committed in
  `a1c2bb8` and `92594f6`; 27 focused maintenance/profile/tool tests passed.
- Capability resolution no longer scans unrelated locator hints. Read-only
  validation derives the authoritative session root before writable access.
  Commits `8067958` and `9cf3c01` passed 20 integration/isolated-worktree tests,
  including a stale sibling-worktree hint regression.
- Focused independent review identified authorization, replay, scope,
  fingerprint and provenance defects; those findings were corrected before
  the final candidate qualification. Tests were rerun when affected code or
  runtime identity changed, not as repeated unchanged validation.

## Remaining scope

Full PCE2 qualification remains open. In particular, there is no installed
callable maintenance-agent launcher, no general amend/retire/supersede flow,
and no proof covering every large-context, explicit-run-focus, delegation,
delivery or adverse lifecycle acceptance scenario. Ordinary command,
resource, and managed-workspace gates still rerun: only fingerprinted
`file_exists`/`pattern` evidence outside managed workspaces is reused.
Identical gate binding is a serial no-op, not a new transaction-level
contention primitive.

The existing exact-target recovery bridge was reused to prove a useful
maintenance path before attempting a larger kernel mandate system. This is an
intentional adaptation of PCE2, preserving its purpose rather than expanding
infrastructure for its own sake. It does not declare the Skills maintenance
outcome complete.

All-agent provider usage was not available as one complete meter. No measured
token-savings claim or verified aggregate-budget certificate is asserted.
